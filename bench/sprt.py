"""
sprt.py — сравнение двух версий движка последовательным критерием (SPRT)

Зачем именно SPRT, а не «сыграем N партий». Матч фиксированной длины на
14 партиях, которым пользовались во время аудита, имеет доверительный
интервал в сотни Elo: результат 13:1 говорит «стало заметно лучше», а
результат 4.5:5.5 не говорит вообще ничего. SPRT вместо этого играет ровно
столько, сколько нужно для решения с заданными вероятностями ошибок, и
останавливается сам.

Гипотезы:
    H0: разница = elo0  (правка ничего не даёт)
    H1: разница = elo1  (правка даёт как минимум столько)
Границы логарифма отношения правдоподобия:
    upper = log((1-beta)/alpha)   -> принять H1
    lower = log(beta/(1-alpha))   -> принять H0

Модель — пятиисходная (WDL с учётом доли ничьих), как в fastchess и
OpenBench: обычная двухисходная переоценивает значимость при большом
проценте ничьих.

Сравниваются две ревизии git по их UCI-входу, поэтому «до» и «после» — это
настоящие сборки, а не два объекта в одном процессе.

Запуск:
    python bench/sprt.py --base main --test HEAD --movetime 0.5
    python bench/sprt.py --base main --test HEAD --elo0 0 --elo1 15
"""

import argparse
import math
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import chess
import chess.engine

# Прогоны идут десятки минут и обычно перенаправляются в файл. Без построчной
# буферизации до самого конца в файле пусто и следить за ходом невозможно.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

OPENINGS = [
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2",
    "rnbqkb1r/pppppppp/5n2/8/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2",
    "rnbqkbnr/pp1ppppp/8/2p5/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2",
    "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
]


def export_revision(rev: str) -> str:
    """Выгрузить recovered_src нужной ревизии во временный каталог."""
    dest = tempfile.mkdtemp(prefix=f"pycaesar_{rev.replace('/', '_')}_")
    archive = os.path.join(dest, "src.tar")
    with open(archive, "wb") as fh:
        subprocess.run(["git", "archive", rev, "recovered_src", "book.bin"],
                       cwd=ROOT, stdout=fh, check=True)
    subprocess.run(["tar", "-xf", archive, "-C", dest], check=True)
    os.remove(archive)
    uci = os.path.join(dest, "recovered_src", "uci.py")
    if not os.path.isfile(uci):
        raise SystemExit(f"в ревизии {rev} нет recovered_src/uci.py — "
                         f"UCI появился позже, сравнивать так не получится")
    return uci


def elo_to_score(elo: float) -> float:
    return 1.0 / (1.0 + 10 ** (-elo / 400.0))


def llr(w: int, d: int, l: int, elo0: float, elo1: float) -> float:
    """Логарифм отношения правдоподобия, пятиисходная модель."""
    n = w + d + l
    if n == 0 or w == 0 or l == 0:
        return 0.0
    p_w, p_d, p_l = w / n, d / n, l / n
    mu = p_w + 0.5 * p_d
    var = p_w * (1 - mu) ** 2 + p_d * (0.5 - mu) ** 2 + p_l * (0 - mu) ** 2
    if var <= 0:
        return 0.0
    var_s = var / n
    mu0, mu1 = elo_to_score(elo0), elo_to_score(elo1)
    return (mu1 - mu0) * (2 * mu - mu0 - mu1) / (2 * var_s)


def play_game(a, b, fen: str, a_white: bool, movetime: float, max_ply: int) -> str:
    board = chess.Board(fen)
    limit = chess.engine.Limit(time=movetime)
    while not board.is_game_over(claim_draw=True) and len(board.move_stack) < max_ply:
        eng = a if (board.turn == chess.WHITE) == a_white else b
        try:
            res = eng.play(board, limit)
        except chess.engine.EngineError:
            return "0-1" if board.turn == chess.WHITE else "1-0"
        if res.move is None or res.move not in board.legal_moves:
            return "0-1" if board.turn == chess.WHITE else "1-0"
        board.push(res.move)
    if board.is_game_over(claim_draw=True):
        return board.result(claim_draw=True)
    values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    m = sum(v * (len(board.pieces(pt, chess.WHITE)) - len(board.pieces(pt, chess.BLACK)))
            for pt, v in values.items())
    return "1-0" if m >= 5 else "0-1" if m <= -5 else "1/2-1/2"


def main() -> None:
    p = argparse.ArgumentParser(description="SPRT между двумя ревизиями PyCaesar")
    p.add_argument("--base", default="main", help="ревизия «до»")
    p.add_argument("--test", default="HEAD", help="ревизия «после»")
    p.add_argument("--elo0", type=float, default=0.0)
    p.add_argument("--elo1", type=float, default=15.0)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--beta", type=float, default=0.05)
    p.add_argument("--movetime", type=float, default=0.5)
    p.add_argument("--max-ply", type=int, default=160)
    p.add_argument("--max-games", type=int, default=2000)
    a = p.parse_args()

    upper = math.log((1 - a.beta) / a.alpha)
    lower = math.log(a.beta / (1 - a.alpha))

    print(f"SPRT  H0: {a.elo0:+.0f} Elo   H1: {a.elo1:+.0f} Elo   "
          f"alpha={a.alpha} beta={a.beta}")
    print(f"границы LLR: {lower:.2f} .. {upper:.2f}")
    print(f"test={a.test} против base={a.base}, {a.movetime} с/ход\n")

    base_uci = export_revision(a.base)
    test_uci = export_revision(a.test)

    eng_test = chess.engine.SimpleEngine.popen_uci([sys.executable, test_uci])
    eng_base = chess.engine.SimpleEngine.popen_uci([sys.executable, base_uci])
    for e in (eng_test, eng_base):
        try:
            e.configure({"OwnBook": False})
        except Exception:
            pass

    w = d = l = 0
    t0 = time.time()
    try:
        for i in range(a.max_games):
            fen = OPENINGS[(i // 2) % len(OPENINGS)]
            test_white = (i % 2 == 0)
            res = play_game(eng_test, eng_base, fen, test_white, a.movetime, a.max_ply)
            if res == "1/2-1/2":
                d += 1
            elif (res == "1-0") == test_white:
                w += 1
            else:
                l += 1

            value = llr(w, d, l, a.elo0, a.elo1)
            n = w + d + l
            rate = (w + 0.5 * d) / n
            print(f"партия {n:4d}  +{w} ={d} -{l}  счёт {rate*100:5.1f}%  "
                  f"LLR {value:+.2f}  ({time.time()-t0:.0f}с)")

            if value >= upper:
                print(f"\nH1 ПРИНЯТА: правка даёт как минимум {a.elo1:+.0f} Elo ({n} партий)")
                return
            if value <= lower:
                print(f"\nH0 ПРИНЯТА: улучшения выше {a.elo0:+.0f} Elo не подтверждено ({n} партий)")
                return
        print(f"\nЛимит {a.max_games} партий исчерпан, решение не принято. LLR {llr(w, d, l, a.elo0, a.elo1):+.2f}")
    finally:
        eng_test.quit()
        eng_base.quit()


if __name__ == "__main__":
    main()
