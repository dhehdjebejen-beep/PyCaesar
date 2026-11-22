"""
search.py – Сверхбыстрый чистый Python-поиск PyCaesar на собственном ядре FastBoard

Оптимизации:
  • Собственная доска FastBoard (без chess.Board и chess.Move внутри дерева поиска)
  • Упакованные целочисленные ходы (int) — 0 аллокаций объектов на ход
  • Инкрементальная оценка PeSTO + фирменный стиль PyCaesar (~2–3 мкс вместо 20 мкс)
  • Инкрементальный 64-битный Zobrist-хэш и O(1)-детектор повторений
  • Fail-soft Negamax + PVS + Null-Move + консервативный RFP (d=1) + Futility + LMP + IIR + умный LMR
  • Quiescence Search на captures-only генерации FastBoard + Delta Pruning + SEE
  • Сортировка: TT → ферзь → SEE/MVV-LVA + Capture History → Killers → Counter-Move → History
  • Polyglot дебютная книга (book.bin) + эндшпильные базы Syzygy (WDL + DTZ)
"""

import os
import sys
import time
import math
import random
import itertools
import chess
import chess.polyglot
from engine.evaluation import evaluate, PIECE_VALUES
from engine import strength as strength_levels
from engine.tablebase import SyzygyTablebase, TB_WIN_SCORE
from engine.fast_board import (
    FastBoard, WHITE, BLACK, EMPTY,
    WP, WN, WB, WR, WQ, WK, BP, BN, BB, BR, BQ, BK,
    FLAG_EP, FLAG_PROMO_Q, FLAG_PROMO_CAP_Q,
    KNIGHT_ATT, KING_ATT, PAWN_ATT_W, PAWN_ATT_B,
    DIAG_MASKS, DIAG_ATTACKS, FILE_MASKS, FILE_ATTACKS, RANK_MASKS, RANK_ATTACKS,
    PIECE_VAL, move_to_chess, chess_to_move,
)
from engine.fast_eval import fast_evaluate, clear_fast_pawn_hash
try:
    from engine._fast_core import CythonSearcher as _CythonSearcher
except Exception:
    _CythonSearcher = None

INFINITY = 99999
MATE_SCORE = 99000

TT_EXACT = 0
TT_ALPHA = 1
TT_BETA = 2

CONTEMPT = 12

NULL_MOVE_R_BASE = 2
NULL_MOVE_R_HIGH = 3

FUTILITY_MARGINS = (0, 120, 240, 360)
LMP_COUNTS = (0, 8, 14, 22)
DELTA_MARGIN = 200
ASPIRATION_DELTA = 30

# При 50–100k nps опрос раз в 1024 узла = ~10–20 мс (точно даже для пули)
CLOCK_POLL_MASK = 1023
QS_MAX_DEPTH = 8
HISTORY_MAX = 50000
CAPTURE_HISTORY_MAX = 50000

_PROMO_RANKS = chess.BB_RANK_1 | chess.BB_RANK_8

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    _BOOK_PATH = os.path.join(sys._MEIPASS, 'book.bin')
else:
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _BOOK_PATH = os.path.join(_PROJECT_ROOT, 'book.bin')


def get_book_move(board: chess.Board) -> chess.Move | None:
    if not os.path.isfile(_BOOK_PATH):
        return None
    try:
        with chess.polyglot.open_reader(_BOOK_PATH) as reader:
            entries = list(reader.find_all(board))
            if not entries:
                return None
            total_weight = sum(e.weight for e in entries)
            if total_weight == 0:
                return random.choice(entries).move
            r = random.randint(0, total_weight - 1)
            cumulative = 0
            for entry in entries:
                cumulative += entry.weight
                if r < cumulative:
                    return entry.move
            return entries[0].move
    except Exception:
        return None


def _fast_lva(fb: FastBoard, side: int, sq: int, occ: int) -> tuple[int, int]:
    """Самый дешёвый атакующий клетки sq для стороны side при занятости occ -> (from_sq, piece_type)."""
    bb = fb.bb
    if side == WHITE:
        att = PAWN_ATT_B[sq] & bb[WP] & occ
        if att:
            return (att & -att).bit_length() - 1, 1
        att = KNIGHT_ATT[sq] & bb[WN] & occ
        if att:
            return (att & -att).bit_length() - 1, 2
        diag = DIAG_ATTACKS[sq][occ & DIAG_MASKS[sq]]
        att = diag & bb[WB] & occ
        if att:
            return (att & -att).bit_length() - 1, 3
        orth = FILE_ATTACKS[sq][occ & FILE_MASKS[sq]] | RANK_ATTACKS[sq][occ & RANK_MASKS[sq]]
        att = orth & bb[WR] & occ
        if att:
            return (att & -att).bit_length() - 1, 4
        att = (diag | orth) & bb[WQ] & occ
        if att:
            return (att & -att).bit_length() - 1, 5
        att = KING_ATT[sq] & bb[WK] & occ
        if att:
            return (att & -att).bit_length() - 1, 6
        return -1, 0
    else:
        att = PAWN_ATT_W[sq] & bb[BP] & occ
        if att:
            return (att & -att).bit_length() - 1, 1
        att = KNIGHT_ATT[sq] & bb[BN] & occ
        if att:
            return (att & -att).bit_length() - 1, 2
        diag = DIAG_ATTACKS[sq][occ & DIAG_MASKS[sq]]
        att = diag & bb[BB] & occ
        if att:
            return (att & -att).bit_length() - 1, 3
        orth = FILE_ATTACKS[sq][occ & FILE_MASKS[sq]] | RANK_ATTACKS[sq][occ & RANK_MASKS[sq]]
        att = orth & bb[BR] & occ
        if att:
            return (att & -att).bit_length() - 1, 4
        att = (diag | orth) & bb[BQ] & occ
        if att:
            return (att & -att).bit_length() - 1, 5
        att = KING_ATT[sq] & bb[BK] & occ
        if att:
            return (att & -att).bit_length() - 1, 6
        return -1, 0


def fast_see(fb: FastBoard, move: int) -> int:
    """Полный swap-off SEE прямо на FastBoard (рентген, превращения, en passant)."""
    f = move & 63
    t = (move >> 6) & 63
    fl = move >> 12
    a_pc = fb.sq[f]
    if a_pc == EMPTY:
        return 0

    occ = fb.occ & ~(1 << f)
    if fl == FLAG_EP:
        cap_sq = t - 8 if fb.turn == WHITE else t + 8
        occ &= ~(1 << cap_sq)
        gain = 100
    else:
        v_pc = fb.sq[t]
        gain = PIECE_VAL[v_pc & 7] if v_pc != EMPTY else 0

    occ |= (1 << t)
    if fl & 8:
        on_sq = PIECE_VAL[(fl & 3) + 2]
        gain += on_sq - 100
    else:
        on_sq = PIECE_VAL[a_pc & 7]

    gains = [gain]
    side = fb.turn ^ 8
    t_promo = bool((1 << t) & _PROMO_RANKS)

    while True:
        sq, pt = _fast_lva(fb, side, t, occ)
        if sq < 0:
            break
        cap_v = on_sq
        on_sq = PIECE_VAL[pt]
        if pt == 1 and t_promo:
            cap_v += 800
            on_sq = 900
        gains.append(cap_v - gains[-1])
        occ &= ~(1 << sq)
        side ^= 8
        if pt == 6 and _fast_lva(fb, side, t, occ)[0] >= 0:
            gains.pop()
            break

    for i in range(len(gains) - 1, 0, -1):
        gains[i - 1] = -max(-gains[i - 1], gains[i])
    return gains[0]


def _see(board: chess.Board, move: chess.Move) -> int:
    """Обёртка для обратной совместимости с тестами."""
    fb = FastBoard.from_chess(board)
    return fast_see(fb, chess_to_move(fb, move))


class TranspositionTable:
    """Двухбакетная таблица транспозиций (целочисленные ключи и упакованные ходы)."""
    DEFAULT_MAX_SIZE = 300000
    BYTES_PER_ENTRY = 140

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE) -> None:
        self.table0: dict = {}
        self.table1: dict = {}
        self.max_size = max(1000, max_size)

    def set_hash_mb(self, megabytes: int) -> None:
        total_entries = max(2000, int(megabytes * 1024 * 1024 / self.BYTES_PER_ENTRY))
        self.max_size = max(1000, total_entries // 2)
        self.clear()

    def _evict(self, table: dict) -> None:
        drop = self.max_size // 8
        for key in list(itertools.islice(table, drop)):
            del table[key]

    def store(self, key: int, depth: int, score: int, flag: int, best_move: int, ply: int = 0) -> None:
        if score > MATE_SCORE - 1000:
            score += ply
            if score > INFINITY:
                score = INFINITY
        elif score < -MATE_SCORE + 1000:
            score -= ply
            if score < -INFINITY:
                score = -INFINITY

        existing1 = self.table1.get(key)
        if existing1 is None or depth >= existing1[0]:
            if len(self.table1) >= self.max_size:
                self._evict(self.table1)
            self.table1[key] = (depth, score, flag, best_move)
        else:
            if len(self.table0) >= self.max_size:
                self._evict(self.table0)
            self.table0[key] = (depth, score, flag, best_move)

    def probe(self, key: int, ply: int = 0) -> tuple[int, int, int, int] | None:
        e0 = self.table0.get(key)
        e1 = self.table1.get(key)
        if e0 is None and e1 is None:
            return None

        entry = e1 if e0 is None else e0 if e1 is None else (e0 if e0[0] >= e1[0] else e1)
        depth, score, flag, best_move = entry

        if score > MATE_SCORE - 1000:
            score -= ply
            if score < MATE_SCORE - 999:
                score = MATE_SCORE - 999
        elif score < -MATE_SCORE + 1000:
            score += ply
            if score > -MATE_SCORE + 999:
                score = -MATE_SCORE + 999

        return (depth, score, flag, best_move)

    def clear(self) -> None:
        self.table0.clear()
        self.table1.clear()


class SearchEngine:
    """Сверхбыстрый движок поиска PyCaesar на FastBoard."""

    def __init__(self, tablebase: SyzygyTablebase | None = None) -> None:
        self.tt = TranspositionTable()
        self.nodes_searched = 0
        self.start_time = 0.0
        self.time_limit = 5.0
        self.stopped = False
        self.killer_moves = [[0, 0] for _ in range(128)]
        self.history = [[[0] * 64 for _ in range(64)] for _ in range(2)]
        self.counter_moves = [[0] * 64 for _ in range(64)]
        self.capture_history = [[[0] * 7 for _ in range(64)] for _ in range(7)]
        self.best_move_root: int = 0
        self.root_moves_scores: dict = {}
        self.root_turn: int = WHITE
        self.excluded_root_moves: set[int] = set()
        self.strength = strength_levels.get(strength_levels.FULL_STRENGTH)
        self.qs_max_depth = QS_MAX_DEPTH
        self._rng = random.Random()
        self.tablebase = tablebase if tablebase is not None else SyzygyTablebase()
        self._rep_counts: dict[int, int] = {}
        self._cy = _CythonSearcher(64) if _CythonSearcher is not None else None

    def set_strength(self, level: int) -> None:
        self.strength = strength_levels.get(level)
        self.qs_max_depth = self.strength.qs_max if not self.strength.is_full else QS_MAX_DEPTH

    def stop(self) -> None:
        self.stopped = True
        if self._cy is not None:
            self._cy.stop()

    def clear_heuristics(self) -> None:
        self.killer_moves = [[0, 0] for _ in range(128)]
        self.history = [[[0] * 64 for _ in range(64)] for _ in range(2)]
        self.counter_moves = [[0] * 64 for _ in range(64)]
        self.capture_history = [[[0] * 7 for _ in range(64)] for _ in range(7)]
        clear_fast_pawn_hash()
        if self._cy is not None:
            self._cy.clear_heuristics()
            self._cy.clear_tt()

    def _check_time(self) -> bool:
        if self.stopped:
            return True
        if time.time() - self.start_time >= self.time_limit:
            self.stopped = True
            return True
        return False

    def _scale_history(self) -> None:
        for c in range(2):
            table = self.history[c]
            for f in range(64):
                row = table[f]
                for t in range(64):
                    row[t] >>= 1

    def _scale_capture_history(self) -> None:
        for a in range(7):
            plane = self.capture_history[a]
            for sq in range(64):
                row = plane[sq]
                for v in range(7):
                    row[v] >>= 1

    def _update_capture_history(self, attacker: int, to_sq: int, victim: int, depth: int) -> None:
        updated = self.capture_history[attacker][to_sq][victim] + depth * depth
        self.capture_history[attacker][to_sq][victim] = updated
        if updated > CAPTURE_HISTORY_MAX:
            self._scale_capture_history()

    def _order_moves(
        self,
        fb: FastBoard,
        moves: list[int],
        ply: int,
        tt_move: int,
        prev_move: int,
    ) -> list[int]:
        ply_c = ply if ply < 128 else 127
        k0 = self.killer_moves[ply_c][0]
        k1 = self.killer_moves[ply_c][1]
        cm = self.counter_moves[prev_move & 63][(prev_move >> 6) & 63] if prev_move else 0
        c_idx = 0 if fb.turn == WHITE else 1
        hist = self.history[c_idx]
        cap_hist = self.capture_history
        sq_arr = fb.sq
        pval = PIECE_VAL

        scored = []
        append = scored.append
        for m in moves:
            if m == tt_move:
                s = 10000000
            else:
                fl = m >> 12
                if fl == FLAG_PROMO_Q or fl == FLAG_PROMO_CAP_Q:
                    s = 9000000
                elif fl & 4:  # capture
                    f = m & 63
                    t = (m >> 6) & 63
                    a_pt = sq_arr[f] & 7
                    v_pt = 1 if fl == FLAG_EP else (sq_arr[t] & 7)
                    v_val = pval[v_pt]
                    a_val = pval[a_pt]
                    mvv_lva = v_val * 10 - a_val
                    ch = cap_hist[a_pt][t][v_pt]
                    if v_val >= a_val:
                        s = 1000000 + mvv_lva + (ch >> 3)
                    else:
                        see_v = fast_see(fb, m)
                        if see_v >= 0:
                            s = 1000000 + mvv_lva + (ch >> 3)
                        else:
                            s = -500000 + see_v + (ch >> 4)
                elif m == k0:
                    s = 900000
                elif m == k1:
                    s = 800000
                elif m == cm:
                    s = 750000
                else:
                    s = hist[m & 63][(m >> 6) & 63]
            append((s, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored]

    def _store_killer(self, move: int, ply: int) -> None:
        if ply > 127:
            ply = 127
        if move != self.killer_moves[ply][0]:
            self.killer_moves[ply][1] = self.killer_moves[ply][0]
            self.killer_moves[ply][0] = move

    def _biggest_capture_gain(self, fb: FastBoard) -> int:
        bb = fb.bb
        if fb.turn == WHITE:
            if bb[BQ]:
                best = 900
            elif bb[BR]:
                best = 500
            elif bb[BB]:
                best = 330
            elif bb[BN]:
                best = 320
            elif bb[BP]:
                best = 100
            else:
                best = 0
            if bb[WP] & chess.BB_RANK_7:
                best += 800
            return best
        else:
            if bb[WQ]:
                best = 900
            elif bb[WR]:
                best = 500
            elif bb[WB]:
                best = 330
            elif bb[WN]:
                best = 320
            elif bb[WP]:
                best = 100
            else:
                best = 0
            if bb[BP] & chess.BB_RANK_2:
                best += 800
            return best

    def _quiescence(self, fb: FastBoard, alpha: int, beta: int, qs_depth: int, ply: int) -> int:
        self.nodes_searched += 1
        if (self.nodes_searched & CLOCK_POLL_MASK) == 0:
            if self._check_time():
                return 0

        if fb.in_check():
            if qs_depth < self.qs_max_depth:
                evasions = self._order_moves(fb, fb.generate_moves(False), ply, 0, 0)
                best_score = -INFINITY
                legal_any = False
                for m in evasions:
                    if not fb.make_move(m):
                        continue
                    legal_any = True
                    score = -self._quiescence(fb, -beta, -alpha, qs_depth + 1, ply + 1)
                    fb.unmake_move()
                    if self.stopped:
                        return 0
                    if score > best_score:
                        best_score = score
                    if score > alpha:
                        alpha = score
                    if alpha >= beta:
                        break
                if not legal_any:
                    return -INFINITY + ply
                return best_score
            # Достигнут предел глубины под шахом: проверяем мат
            for m in fb.generate_moves(False):
                if fb.make_move(m):
                    fb.unmake_move()
                    return fast_evaluate(fb)
            return -INFINITY + ply

        stand_pat = fast_evaluate(fb)
        if stand_pat >= beta:
            return beta
        if qs_depth > 0 and stand_pat + self._biggest_capture_gain(fb) + DELTA_MARGIN < alpha:
            return alpha
        if stand_pat > alpha:
            alpha = stand_pat
        if qs_depth >= self.qs_max_depth:
            return stand_pat

        caps = fb.generate_moves(True)
        if not caps:
            return alpha

        sq_arr = fb.sq
        pval = PIECE_VAL
        scored_caps = []
        for m in caps:
            fl = m >> 12
            if fl == FLAG_PROMO_Q or fl == FLAG_PROMO_CAP_Q:
                scored_caps.append((9000000, m))
                continue
            a_pt = sq_arr[m & 63] & 7
            v_pt = 1 if fl == FLAG_EP else (sq_arr[(m >> 6) & 63] & 7)
            v_val = pval[v_pt]
            a_val = pval[a_pt]
            if v_val >= a_val:
                scored_caps.append((v_val * 10 - a_val + 100000, m))
            else:
                see_v = fast_see(fb, m)
                if qs_depth > 0 and see_v < 0:
                    continue
                scored_caps.append((see_v * 100 + v_val * 10 - a_val, m))

        if not scored_caps:
            return alpha

        scored_caps.sort(key=lambda x: x[0], reverse=True)
        for _, m in scored_caps:
            if not fb.make_move(m):
                continue
            score = -self._quiescence(fb, -beta, -alpha, qs_depth + 1, ply + 1)
            fb.unmake_move()
            if self.stopped:
                return 0
            if score >= beta:
                return beta
            if score > alpha:
                alpha = score
        return alpha

    def _negamax(self, fb: FastBoard, depth: int, alpha: int, beta: int, ply: int, prev_move: int = 0) -> int:
        self.nodes_searched += 1
        if (self.nodes_searched & CLOCK_POLL_MASK) == 0:
            if self._check_time():
                return 0

        zhash = fb.zhash
        if ply > 0:
            if fb.halfmove >= 100 or self._rep_counts.get(zhash, 0) >= 1 or fb.is_insufficient_material():
                return -CONTEMPT if fb.turn == self.root_turn else CONTEMPT

        is_in_check = fb.in_check()
        is_pv = (beta - alpha) > 1

        tt_move = 0
        tt_entry = self.tt.probe(zhash, ply)
        if tt_entry is not None:
            tt_depth, tt_score, tt_flag, tt_move = tt_entry
            if tt_depth >= depth and ply > 0:
                if tt_flag == TT_EXACT:
                    return tt_score
                elif tt_flag == TT_ALPHA and tt_score <= alpha:
                    return tt_score
                elif tt_flag == TT_BETA and tt_score >= beta:
                    return tt_score

        if depth <= 0:
            return self._quiescence(fb, alpha, beta, 0, ply)

        # Internal Iterative Reduction (IIR)
        if depth >= 4 and tt_move == 0 and ply > 0 and not is_in_check:
            depth -= 1

        static_eval: int | None = None

        # Консервативный Reverse Futility только на depth == 1 (запас 250 cp)
        if not is_in_check and not is_pv and ply > 0 and depth == 1 and abs(beta) < TB_WIN_SCORE - 1000:
            static_eval = fast_evaluate(fb)
            if static_eval - 250 >= beta:
                return static_eval

        prev_rep = self._rep_counts.get(zhash, 0)
        self._rep_counts[zhash] = prev_rep + 1

        try:
            # Null Move Pruning (с защитой от цугцванга)
            if (
                not is_in_check
                and not is_pv
                and ply > 0
                and depth >= NULL_MOVE_R_BASE + 1
                and fb.has_non_pawn_material()
                and fb.ep < 0
            ):
                if static_eval is None:
                    static_eval = fast_evaluate(fb)
                if static_eval >= beta:
                    R = NULL_MOVE_R_HIGH if depth >= 6 else NULL_MOVE_R_BASE
                    fb.make_null_move()
                    null_score = -self._negamax(fb, depth - 1 - R, -beta, -beta + 1, ply + 1, 0)
                    fb.unmake_null_move()
                    if self.stopped:
                        return 0
                    if null_score >= beta:
                        return beta

            # Futility Pruning
            futility_prune = False
            if (
                not is_in_check
                and not is_pv
                and depth <= 3
                and ply > 0
                and abs(alpha) < TB_WIN_SCORE - 1000
                and abs(beta) < TB_WIN_SCORE - 1000
            ):
                if static_eval is None:
                    static_eval = fast_evaluate(fb)
                if static_eval + FUTILITY_MARGINS[depth] <= alpha:
                    futility_prune = True

            lmp_active = (
                not is_in_check
                and not is_pv
                and depth <= 3
                and ply > 0
                and abs(alpha) < TB_WIN_SCORE - 1000
            )
            lmp_limit = LMP_COUNTS[depth] if lmp_active else 999

            raw_moves = fb.generate_moves(False)
            if ply == 0 and self.excluded_root_moves:
                raw_moves = [m for m in raw_moves if m not in self.excluded_root_moves]

            moves = self._order_moves(fb, raw_moves, ply, tt_move, prev_move)

            ply_c = ply if ply < 128 else 127
            k0 = self.killer_moves[ply_c][0]
            k1 = self.killer_moves[ply_c][1]
            cm = self.counter_moves[prev_move & 63][(prev_move >> 6) & 63] if prev_move else 0

            original_alpha = alpha
            best_score = -INFINITY
            best_move = 0
            legal_count = 0
            quiet_searched = 0
            pruned_moves = False
            lmr_start = 4 if is_pv else 3
            sq_arr = fb.sq

            for m in moves:
                fl = m >> 12
                is_capture = bool(fl & 4)
                is_promo = bool(fl & 8)
                is_quiet = not is_capture and not is_promo

                if is_quiet and legal_count > 0 and m != k0 and m != k1 and m != cm:
                    if lmp_active and quiet_searched >= lmp_limit:
                        pruned_moves = True
                        continue

                if is_capture:
                    cap_a = sq_arr[m & 63] & 7
                    cap_v = 1 if fl == FLAG_EP else (sq_arr[(m >> 6) & 63] & 7)
                else:
                    cap_a = cap_v = 0

                if not fb.make_move(m):
                    continue

                gives_check = fb.in_check()

                # Futility никогда не отсекает ходы, дающие шах (проверено точно после make_move)
                if (
                    futility_prune
                    and is_quiet
                    and legal_count > 0
                    and not gives_check
                    and m != k0 and m != k1 and m != cm
                ):
                    fb.unmake_move()
                    pruned_moves = True
                    continue

                i = legal_count
                legal_count += 1
                if is_quiet:
                    quiet_searched += 1

                ext = 1 if gives_check else 0
                red = 0
                if (
                    i >= lmr_start
                    and depth >= 3
                    and ply > 0
                    and not is_in_check
                    and not gives_check
                    and is_quiet
                    and ext == 0
                ):
                    red = int(0.75 + math.log(depth) * math.log(i + 1) / 2.25)
                    if not is_pv:
                        red += 1
                    if m == k0 or m == k1 or m == cm:
                        red -= 1
                    if red < 1:
                        red = 1
                    elif red > depth - 2:
                        red = depth - 2

                full_window = False
                if red > 0:
                    score = -self._negamax(fb, depth - 1 + ext - red, -alpha - 1, -alpha, ply + 1, m)
                    if score > alpha:
                        score = -self._negamax(fb, depth - 1 + ext, -beta, -alpha, ply + 1, m)
                        full_window = True
                elif i == 0:
                    score = -self._negamax(fb, depth - 1 + ext, -beta, -alpha, ply + 1, m)
                    full_window = True
                else:
                    score = -self._negamax(fb, depth - 1 + ext, -alpha - 1, -alpha, ply + 1, m)
                    if score > alpha:
                        score = -self._negamax(fb, depth - 1 + ext, -beta, -alpha, ply + 1, m)
                        full_window = True

                fb.unmake_move()
                if self.stopped:
                    return 0

                if score > best_score:
                    best_score = score
                    best_move = m
                    if ply == 0:
                        self.best_move_root = m

                if ply == 0 and full_window:
                    self.root_moves_scores[m] = score

                if score > alpha:
                    alpha = score
                    if is_quiet:
                        c_idx = 0 if fb.turn == WHITE else 1
                        f_sq = m & 63
                        t_sq = (m >> 6) & 63
                        upd = self.history[c_idx][f_sq][t_sq] + depth * depth
                        self.history[c_idx][f_sq][t_sq] = upd
                        if upd > HISTORY_MAX:
                            self._scale_history()

                if alpha >= beta:
                    if is_quiet:
                        self._store_killer(m, ply)
                        if prev_move:
                            self.counter_moves[prev_move & 63][(prev_move >> 6) & 63] = m
                    elif is_capture:
                        self._update_capture_history(cap_a, (m >> 6) & 63, cap_v, depth)
                    break

            if legal_count == 0:
                if pruned_moves:
                    return original_alpha
                if is_in_check:
                    return -INFINITY + ply
                return -CONTEMPT if fb.turn == self.root_turn else CONTEMPT

            if best_score <= original_alpha:
                flag = TT_ALPHA
            elif best_score >= beta:
                flag = TT_BETA
            elif pruned_moves:
                flag = TT_BETA
            else:
                flag = TT_EXACT

            if not (ply == 0 and self.excluded_root_moves):
                self.tt.store(zhash, depth, best_score, flag, best_move, ply)
            return best_score
        finally:
            if prev_rep == 0:
                del self._rep_counts[zhash]
            else:
                self._rep_counts[zhash] = prev_rep

    def _search_root(self, fb: FastBoard, depth: int, prev_score: int, use_aspiration: bool) -> int:
        if not use_aspiration:
            return self._negamax(fb, depth, -INFINITY, INFINITY, 0, 0)

        delta = ASPIRATION_DELTA
        alpha = prev_score - delta
        beta = prev_score + delta
        for _ in range(2):
            score = self._negamax(fb, depth, alpha, beta, 0, 0)
            if self.stopped:
                return score
            if score <= alpha:
                alpha = max(-INFINITY, alpha - delta * 4)
            elif score >= beta:
                beta = min(INFINITY, beta + delta * 4)
            else:
                return score
            delta *= 4
        return self._negamax(fb, depth, -INFINITY, INFINITY, 0, 0)

    def _seed_repetition_counts(self, board: chess.Board) -> None:
        counts: dict[int, int] = {}
        if board.move_stack:
            tmp_cb = board.root()
            fb = FastBoard.from_chess(tmp_cb)
            counts[fb.zhash] = 1
            for cm in board.move_stack[:-1]:
                m = chess_to_move(fb, cm)
                fb.make_move(m)
                counts[fb.zhash] = counts.get(fb.zhash, 0) + 1
        self._rep_counts = counts

    def search(
        self,
        board: chess.Board,
        time_limit: float = 5.0,
        max_depth: int = 64,
        use_book: bool = True,
        on_iteration=None,
        multipv: int = 1,
    ) -> tuple[list, int, int]:
        if use_book:
            book_move = get_book_move(board)
            if book_move is not None and book_move in board.legal_moves:
                return [(book_move, 0)], 0, 0

        limits = self.strength
        if not limits.is_full:
            max_depth = min(max_depth, limits.max_depth)
            time_limit = max(0.05, time_limit * limits.time_scale)
            multipv = max(multipv, limits.top_k)

        self.start_time = time.time()
        self.time_limit = time_limit
        self.stopped = False
        self.nodes_searched = 0
        self.best_move_root = 0
        self.root_moves_scores = {}
        self.root_turn = WHITE if board.turn == chess.WHITE else BLACK
        self.excluded_root_moves = set()

        self.killer_moves = [[0, 0] for _ in range(128)]
        self._scale_history()
        self._scale_capture_history()
        self._seed_repetition_counts(board)

        legal_moves = list(board.legal_moves)
        if not legal_moves:
            return [], 0, 0

        fb = FastBoard.from_chess(board)

        if len(legal_moves) == 1:
            only = legal_moves[0]
            sc = fast_evaluate(fb)
            return [(only, sc)], sc, 1

        if (
            limits.noise_cp == 0
            and multipv == 1
            and self.tablebase is not None
            and self.tablebase.can_probe(board)
        ):
            tb_root = self.tablebase.probe_root(board)
            if tb_root is not None:
                tb_move, tb_score = tb_root
                if on_iteration is not None:
                    try:
                        on_iteration({
                            'depth': 1,
                            'score': tb_score,
                            'best_move': tb_move,
                            'multi_pv': [(tb_move, tb_score)],
                            'nodes': 1,
                            'time': max(time.time() - self.start_time, 1e-4),
                        })
                    except Exception:
                        pass
                return [(tb_move, tb_score)], tb_score, 1

        multipv = max(1, min(multipv, len(legal_moves)))

        # Быстрая ветка на скомпилированном Cython-ядре (~2–4 млн узлов/с)
        if self._cy is not None:
            def _cy_cb(depth, score, raw_lines, nodes, elapsed):
                self.nodes_searched = nodes
                if on_iteration is not None:
                    cm_lines = [(move_to_chess(m), sc) for m, sc in raw_lines]
                    on_iteration({
                        'depth': depth,
                        'score': score,
                        'best_move': cm_lines[0][0],
                        'multi_pv': cm_lines,
                        'nodes': nodes,
                        'time': elapsed,
                    })

            best_lines_int, best_score, depth_reached = self._cy.search_position(
                board,
                float(time_limit),
                int(max_depth),
                int(self.qs_max_depth),
                int(multipv),
                _cy_cb if on_iteration is not None else None,
            )
            self.nodes_searched = int(self._cy.nodes)
            best_lines = [(move_to_chess(m), sc) for m, sc in best_lines_int]
            if not best_lines and legal_moves:
                best_sc = -INFINITY
                best_cm = legal_moves[0]
                for cm in legal_moves:
                    m = chess_to_move(fb, cm)
                    if fb.make_move(m):
                        sc = -fast_evaluate(fb)
                        fb.unmake_move()
                        if sc > best_sc:
                            best_sc = sc
                            best_cm = cm
                best_score = best_sc
                depth_reached = 1
                best_lines = [(best_cm, best_score)]
            if not limits.is_full and len(best_lines) > 1 and limits.noise_cp > 0:
                best_lines = self._pick_with_noise(best_lines, limits)
                best_score = best_lines[0][1]
            return best_lines, best_score, depth_reached

        best_move_int = 0
        best_score = 0
        depth_reached = 0
        best_lines_int: list[tuple[int, int]] = []
        prev_score = 0
        stable_iterations = 0

        for depth in range(1, max_depth + 1):
            lines: list[tuple[int, int]] = []
            self.excluded_root_moves = set()

            for _ in range(multipv):
                self.root_moves_scores = {}
                self.best_move_root = 0
                use_asp = depth > 4 and not lines and multipv == 1
                score = self._search_root(fb, depth, prev_score, use_asp)
                if self.stopped:
                    break

                cand = self.best_move_root
                if cand == 0:
                    entry = self.tt.probe(fb.zhash)
                    if entry is not None and entry[3] != 0:
                        cand = entry[3]
                if cand == 0 or cand in self.excluded_root_moves:
                    break

                lines.append((cand, score))
                self.excluded_root_moves.add(cand)

            if self.stopped or not lines:
                break

            if multipv > 1 and len(lines) > 1:
                lines.sort(key=lambda item: item[1], reverse=True)

            prev_best = best_move_int
            best_lines_int = lines
            best_move_int, best_score = lines[0]
            depth_reached = depth
            prev_score = best_score
            stable_iterations = stable_iterations + 1 if best_move_int == prev_best else 0

            if on_iteration is not None:
                try:
                    cm_lines = [(move_to_chess(m), sc) for m, sc in best_lines_int]
                    on_iteration({
                        'depth': depth,
                        'score': best_score,
                        'best_move': cm_lines[0][0],
                        'multi_pv': cm_lines,
                        'nodes': self.nodes_searched,
                        'time': time.time() - self.start_time,
                    })
                except Exception:
                    pass

            if abs(best_score) >= MATE_SCORE:
                break

            elapsed = time.time() - self.start_time
            remaining = time_limit - elapsed
            if remaining < time_limit * 0.02:
                break
            if stable_iterations >= 4 and elapsed > time_limit * 0.8:
                break

        best_lines = [(move_to_chess(m), sc) for m, sc in best_lines_int]

        if not best_lines and legal_moves:
            best_sc = -INFINITY
            best_cm = legal_moves[0]
            for cm in legal_moves:
                m = chess_to_move(fb, cm)
                if fb.make_move(m):
                    sc = -fast_evaluate(fb)
                    fb.unmake_move()
                    if sc > best_sc:
                        best_sc = sc
                        best_cm = cm
            best_score = best_sc
            depth_reached = 1
            best_lines = [(best_cm, best_score)]

        if not limits.is_full and len(best_lines) > 1 and limits.noise_cp > 0:
            best_lines = self._pick_with_noise(best_lines, limits)
            best_score = best_lines[0][1]

        return best_lines, best_score, depth_reached

    def _pick_with_noise(self, lines: list, limits) -> list:
        candidates = lines[:limits.top_k]
        forced = [pair for pair in candidates if abs(pair[1]) >= MATE_SCORE]
        if forced:
            return lines

        rng = self._rng
        noise = limits.noise_cp
        jittered = sorted(
            ((score + rng.randint(-noise, noise), move, score) for move, score in candidates),
            key=lambda t: t[0], reverse=True,
        )
        chosen = [(move, score) for _, move, score in jittered]
        rest = lines[limits.top_k:]
        return chosen + rest

    def search_with_time_management(
        self,
        board: chess.Board,
        remaining_time: float = 300.0,
        increment: float = 0.0,
        max_depth: int = 64,
        moves_to_go: int = 30,
    ) -> tuple[list, int, int]:
        moves_to_go = max(1, moves_to_go)
        budget = remaining_time / moves_to_go + increment * 0.8
        reserve = min(0.3, remaining_time * 0.05)
        budget = min(budget, max(0.05, remaining_time - reserve) * 0.3)
        budget = max(0.05, budget)
        return self.search(board, time_limit=budget, max_depth=max_depth)
