"""
calibrate.py — измерение силы PyCaesar в Elo против Stockfish

Зачем: матч «сам против себя» показывает только относительную силу
(«стало лучше на N»). Абсолютную цифру даёт только откалиброванный соперник.

Как: PyCaesar заданного уровня играет матч против Stockfish с
UCI_LimitStrength и фиксированным UCI_Elo. Доля очков переводится в разницу
Elo по обычной логистической формуле, и к ней прибавляется Elo соперника.

Оговорки, без которых цифрой нельзя пользоваться:
  * шкала UCI_Elo у Stockfish — его собственная, она не равна рейтингу
    на lichess или FIDE. Это ориентир порядка величины;
  * ниже 1320 Stockfish ограничивать не умеет, поэтому слабые уровни
    PyCaesar так не измерить — для них нужен более слабый соперник;
  * при 20 партиях доверительный интервал порядка ±150 Elo. Он печатается,
    и игнорировать его нельзя.

Запуск:
    python bench/calibrate.py --level 10 --elo 1320 1500 1700 --games 8
"""

import argparse
import glob
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "recovered_src"))

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


def find_stockfish() -> str | None:
    hits = glob.glob(os.path.join(ROOT, "tools", "**", "stockfish*.exe"), recursive=True)
    hits += glob.glob(os.path.join(ROOT, "tools", "**", "stockfish"), recursive=True)
    return os.path.abspath(hits[0]) if hits else None


def elo_diff(score_rate: float) -> float | None:
    if score_rate <= 0.0 or score_rate >= 1.0:
        return None
    return -400.0 * math.log10(1.0 / score_rate - 1.0)


def elo_margin(score_rate: float, n: int) -> float:
    """Грубый 95% интервал разницы Elo через ошибку доли."""
    if n <= 0:
        return float("inf")
    p = min(max(score_rate, 1e-6), 1 - 1e-6)
    se = math.sqrt(p * (1 - p) / n)
    lo, hi = max(1e-6, p - 1.96 * se), min(1 - 1e-6, p + 1.96 * se)
    d_lo, d_hi = elo_diff(lo), elo_diff(hi)
    if d_lo is None or d_hi is None:
        return float("inf")
    return (d_hi - d_lo) / 2.0


def play_game(ours, theirs, fen: str, our_white: bool, our_time: float,
              their_time: float, max_ply: int) -> str:
    board = chess.Board(fen)
    our_limit = chess.engine.Limit(time=our_time)
    their_limit = chess.engine.Limit(time=their_time)
    while not board.is_game_over(claim_draw=True) and len(board.move_stack) < max_ply:
        is_ours = (board.turn == chess.WHITE) == our_white
        engine = ours if is_ours else theirs
        limit = our_limit if is_ours else their_limit
        try:
            result = engine.play(board, limit)
        except chess.engine.EngineError:
            return "0-1" if board.turn == chess.WHITE else "1-0"
        if result.move is None or result.move not in board.legal_moves:
            return "0-1" if board.turn == chess.WHITE else "1-0"
        board.push(result.move)
    if board.is_game_over(claim_draw=True):
        return board.result(claim_draw=True)
    # По лимиту ходов судим по материалу.
    values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    m = sum(v * (len(board.pieces(pt, chess.WHITE)) - len(board.pieces(pt, chess.BLACK)))
            for pt, v in values.items())
    if m >= 5:
        return "1-0"
    if m <= -5:
        return "0-1"
    return "1/2-1/2"


def run(level: int, elo_points: list, games: int, our_time: float,
        their_time: float, max_ply: int) -> None:
    sf_path = find_stockfish()
    if sf_path is None:
        print("Stockfish не найден в tools/. Скачайте официальный релиз "
              "с github.com/official-stockfish/Stockfish/releases и распакуйте в tools/.")
        return

    asymmetric = abs(our_time - their_time) > 1e-9
    uci_path = os.path.join(ROOT, "recovered_src", "uci.py")
    print(f"PyCaesar уровень {level}  против  Stockfish (UCI_LimitStrength)")
    print(f"PyCaesar {our_time} с/ход, Stockfish {their_time} с/ход, "
          f"{games} партий на точку, лимит {max_ply} полуходов")
    if asymmetric:
        print()
        print("ВНИМАНИЕ: время у сторон РАЗНОЕ. Полученное число — это НЕ рейтинг.")
        print("Elo определён для равных условий; при форе по времени цифра отвечает")
        print(f"на другой вопрос: «PyCaesar с {our_time} с равен Stockfish какого уровня")
        print(f"с {their_time} с». Это гандикап по времени, а не сила движка.")
    print()

    summary = []
    for target_elo in elo_points:
        ours = chess.engine.SimpleEngine.popen_uci([sys.executable, uci_path])
        theirs = chess.engine.SimpleEngine.popen_uci(sf_path)
        try:
            ours.configure({"Skill Level": level, "OwnBook": False})
            theirs.configure({"UCI_LimitStrength": True, "UCI_Elo": target_elo, "Threads": 1, "Hash": 64})

            score = 0.0
            wins = draws = losses = 0
            t0 = time.time()
            for i in range(games):
                fen = OPENINGS[i % len(OPENINGS)]
                our_white = (i % 2 == 0)
                res = play_game(ours, theirs, fen, our_white, our_time, their_time, max_ply)
                if res == "1/2-1/2":
                    score += 0.5
                    draws += 1
                elif (res == "1-0") == our_white:
                    score += 1.0
                    wins += 1
                else:
                    losses += 1
                print(f"  Elo {target_elo}: партия {i+1}/{games} {res:7s} "
                      f"-> {score:.1f}/{i+1}  ({time.time()-t0:.0f}с)")
        finally:
            ours.quit()
            theirs.quit()

        rate = score / games
        diff = elo_diff(rate)
        margin = elo_margin(rate, games)
        if diff is None:
            verdict = f"счёт {score:.1f}/{games} — односторонний, точную разницу не оценить"
            estimate = None
        else:
            estimate = target_elo + diff
            verdict = (f"счёт {score:.1f}/{games} ({rate*100:.0f}%) -> "
                       f"разница {diff:+.0f} Elo -> PyCaesar ≈ {estimate:.0f} "
                       f"(±{margin:.0f})")
        summary.append((target_elo, score, games, wins, draws, losses, estimate, margin))
        print(f"  ИТОГ Elo {target_elo}: {verdict}\n")

    print("=" * 72)
    print(f"СВОДКА для уровня {level}")
    print("=" * 72)
    print(f"{'соперник':>9} {'счёт':>10} {'+':>4}{'=':>4}{'-':>4}  {'оценка PyCaesar':>20}")
    for target_elo, score, n, w, d, l, est, margin in summary:
        est_s = f"{est:.0f} ±{margin:.0f}" if est is not None else "—"
        print(f"{target_elo:>9} {score:>5.1f}/{n:<4} {w:>4}{d:>4}{l:>4}  {est_s:>20}")
    usable = [(e, m) for *_, e, m in summary if e is not None and m != float("inf")]
    if usable:
        weights = [1.0 / (m * m) for _, m in usable]
        combined = sum(e * w for (e, _), w in zip(usable, weights)) / sum(weights)
        print(f"\nВзвешенная оценка: ~{combined:.0f} Elo по шкале Stockfish UCI_Elo.")
    print("\nШкала UCI_Elo принадлежит Stockfish и не равна рейтингу lichess или FIDE.")


def main() -> None:
    p = argparse.ArgumentParser(description="Калибровка силы PyCaesar против Stockfish")
    p.add_argument("--level", type=int, default=10, help="уровень PyCaesar 1..10")
    p.add_argument("--elo", type=int, nargs="+", default=[1320, 1500, 1700],
                   help="точки UCI_Elo Stockfish (минимум 1320)")
    p.add_argument("--games", type=int, default=8, help="партий на точку")
    p.add_argument("--movetime", type=float, default=1.0, help="секунд на ход обоим")
    p.add_argument("--our-time", type=float, default=None,
                   help="секунд на ход PyCaesar (по умолчанию = --movetime)")
    p.add_argument("--sf-time", type=float, default=None,
                   help="секунд на ход Stockfish (по умолчанию = --movetime)")
    p.add_argument("--max-ply", type=int, default=200)
    a = p.parse_args()
    random.seed(20260809)
    our_t = a.our_time if a.our_time is not None else a.movetime
    their_t = a.sf_time if a.sf_time is not None else a.movetime
    run(a.level, a.elo, a.games, our_t, their_t, a.max_ply)


if __name__ == "__main__":
    main()
