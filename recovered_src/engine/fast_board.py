"""
fast_board.py — Быстрая чистая Python-реализация шахматной доски без chess.Board в горячем цикле.

Особенности:
  • Упакованные целочисленные ходы (int): 0 аллокаций объектов на ход
  • Гибрид sq[64] + битборды bb[15] + предвычисленные таблицы скользящих атак
  • Инкрементальная оценка PeSTO (mg, eg, phase) обновляется за O(1) прямо в make_move
  • Инкрементальный 64-битный Zobrist-хэш (O(1) в make_move)
  • Конвертация chess.Board <-> FastBoard только на входе/выходе поиска
"""

import random
import chess

# Коды фигур: 0 = пусто; тип = code & 7 (1..6), цвет = code & 8 (0=белые, 8=чёрные)
EMPTY = 0
WP, WN, WB, WR, WQ, WK = 1, 2, 3, 4, 5, 6
BP, BN, BB, BR, BQ, BK = 9, 10, 11, 12, 13, 14

WHITE = 0
BLACK = 8

# Флаги хода (биты 12..15)
FLAG_QUIET = 0
FLAG_DOUBLE = 1
FLAG_CASTLE_K = 2
FLAG_CASTLE_Q = 3
FLAG_CAPTURE = 4
FLAG_EP = 5
FLAG_PROMO_N = 8
FLAG_PROMO_B = 9
FLAG_PROMO_R = 10
FLAG_PROMO_Q = 11
FLAG_PROMO_CAP_N = 12
FLAG_PROMO_CAP_B = 13
FLAG_PROMO_CAP_R = 14
FLAG_PROMO_CAP_Q = 15

# Права рокировки (4 бита)
CASTLE_WK = 1
CASTLE_WQ = 2
CASTLE_BK = 4
CASTLE_BQ = 8

# Маска снятия прав рокировки при уходе/взятии на клетке
_CASTLE_MASK = [15] * 64
_CASTLE_MASK[0] &= ~CASTLE_WQ   # a1
_CASTLE_MASK[4] &= ~(CASTLE_WK | CASTLE_WQ)  # e1
_CASTLE_MASK[7] &= ~CASTLE_WK   # h1
_CASTLE_MASK[56] &= ~CASTLE_BQ  # a8
_CASTLE_MASK[60] &= ~(CASTLE_BK | CASTLE_BQ)  # e8
_CASTLE_MASK[63] &= ~CASTLE_BK  # h8
CASTLE_MASK = tuple(_CASTLE_MASK)

# Предвычисленные таблицы атак (плоские кортежи для скорости)
KNIGHT_ATT = tuple(chess.BB_KNIGHT_ATTACKS)
KING_ATT = tuple(chess.BB_KING_ATTACKS)
PAWN_ATT_W = tuple(chess.BB_PAWN_ATTACKS[chess.WHITE])
PAWN_ATT_B = tuple(chess.BB_PAWN_ATTACKS[chess.BLACK])
DIAG_MASKS = tuple(chess.BB_DIAG_MASKS)
DIAG_ATTACKS = tuple(chess.BB_DIAG_ATTACKS)
FILE_MASKS = tuple(chess.BB_FILE_MASKS)
FILE_ATTACKS = tuple(chess.BB_FILE_ATTACKS)
RANK_MASKS = tuple(chess.BB_RANK_MASKS)
RANK_ATTACKS = tuple(chess.BB_RANK_ATTACKS)
BB_SQUARES = tuple(1 << s for s in range(64))
NOT_FILE_A = ~chess.BB_FILE_A & 0xFFFFFFFFFFFFFFFF
NOT_FILE_H = ~chess.BB_FILE_H & 0xFFFFFFFFFFFFFFFF
RANK_2 = chess.BB_RANK_2
RANK_7 = chess.BB_RANK_7
RANK_8 = chess.BB_RANK_8
RANK_1 = chess.BB_RANK_1

# PeSTO таблицы для инкрементальной оценки (по коду фигуры 0..14)
from engine.evaluation import (
    _PST_MG_W, _PST_EG_W, _PST_MG_B, _PST_EG_B, MAX_PHASE,
)

# _INC_MG[piece_code][sq] — вклад фигуры со знаком (+ для белых, - для чёрных)
_INC_MG = [[0] * 64 for _ in range(15)]
_INC_EG = [[0] * 64 for _ in range(15)]
_PHASE_W = [0] * 15
_PHASE_BY_TYPE = (0, 0, 1, 1, 2, 4, 0)
for _pt in range(1, 7):
    _PHASE_W[_pt] = _PHASE_BY_TYPE[_pt]
    _PHASE_W[_pt | 8] = _PHASE_BY_TYPE[_pt]
    for _s in range(64):
        _INC_MG[_pt][_s] = _PST_MG_W[_pt - 1][_s]
        _INC_EG[_pt][_s] = _PST_EG_W[_pt - 1][_s]
        _INC_MG[_pt | 8][_s] = -_PST_MG_B[_pt - 1][_s]
        _INC_EG[_pt | 8][_s] = -_PST_EG_B[_pt - 1][_s]

INC_MG = tuple(tuple(row) for row in _INC_MG)
INC_EG = tuple(tuple(row) for row in _INC_EG)
PHASE_W = tuple(_PHASE_W)

# Детерминированные 64-битные Zobrist-ключи
_rng = random.Random(0xC0FFEE123456789)
Z_PIECE = tuple(tuple(_rng.getrandbits(64) for _ in range(64)) for _ in range(15))
Z_CASTLE = tuple(_rng.getrandbits(64) for _ in range(16))
Z_EP = tuple(_rng.getrandbits(64) for _ in range(65))  # индекс 64 = нет ep
Z_SIDE = _rng.getrandbits(64)

PIECE_VAL = (0, 100, 320, 330, 500, 900, 20000)


def move_to_chess(m: int) -> chess.Move:
    f = m & 63
    t = (m >> 6) & 63
    fl = m >> 12
    if fl & 8:
        promo = (fl & 3) + 2  # 2=N, 3=B, 4=R, 5=Q
        return chess.Move(f, t, promotion=promo)
    return chess.Move(f, t)


def chess_to_move(board: "FastBoard", cm: chess.Move) -> int:
    f = cm.from_square
    t = cm.to_square
    pc = board.sq[f]
    pt = pc & 7
    cap = board.sq[t] != EMPTY
    if cm.promotion:
        fl = (FLAG_PROMO_CAP_N if cap else FLAG_PROMO_N) + (cm.promotion - 2)
    elif pt == 1 and t == board.ep:
        fl = FLAG_EP
    elif pt == 1 and abs(t - f) == 16:
        fl = FLAG_DOUBLE
    elif pt == 6 and abs(t - f) == 2:
        fl = FLAG_CASTLE_K if t > f else FLAG_CASTLE_Q
    elif cap:
        fl = FLAG_CAPTURE
    else:
        fl = FLAG_QUIET
    return f | (t << 6) | (fl << 12)


class FastBoard:
    __slots__ = (
        "sq", "bb", "occ_w", "occ_b", "occ",
        "turn", "castling", "ep", "halfmove",
        "wk_sq", "bk_sq", "mg", "eg", "phase", "zhash",
        "undo",
    )

    def __init__(self) -> None:
        self.sq = [EMPTY] * 64
        self.bb = [0] * 15
        self.occ_w = 0
        self.occ_b = 0
        self.occ = 0
        self.turn = WHITE
        self.castling = 0
        self.ep = -1
        self.halfmove = 0
        self.wk_sq = 4
        self.bk_sq = 60
        self.mg = 0
        self.eg = 0
        self.phase = 0
        self.zhash = 0
        self.undo: list = []

    @classmethod
    def from_chess(cls, cb: chess.Board) -> "FastBoard":
        fb = cls()
        sq = fb.sq
        bb = fb.bb
        mg = eg = phase = zhash = 0
        for s in range(64):
            p = cb.piece_at(s)
            if p is not None:
                code = p.piece_type if p.color == chess.WHITE else (p.piece_type | 8)
                sq[s] = code
                bit = 1 << s
                bb[code] |= bit
                if code < 8:
                    fb.occ_w |= bit
                else:
                    fb.occ_b |= bit
                if code == WK:
                    fb.wk_sq = s
                elif code == BK:
                    fb.bk_sq = s
                mg += INC_MG[code][s]
                eg += INC_EG[code][s]
                phase += PHASE_W[code]
                zhash ^= Z_PIECE[code][s]
        fb.occ = fb.occ_w | fb.occ_b
        fb.turn = WHITE if cb.turn == chess.WHITE else BLACK
        c = 0
        cr = cb.clean_castling_rights()
        if cr & chess.BB_H1:
            c |= CASTLE_WK
        if cr & chess.BB_A1:
            c |= CASTLE_WQ
        if cr & chess.BB_H8:
            c |= CASTLE_BK
        if cr & chess.BB_A8:
            c |= CASTLE_BQ
        fb.castling = c
        ep_sq = cb.ep_square
        fb.ep = ep_sq if (ep_sq is not None and cb.has_pseudo_legal_en_passant()) else -1
        fb.halfmove = cb.halfmove_clock
        fb.mg = mg
        fb.eg = eg
        fb.phase = phase
        zhash ^= Z_CASTLE[c]
        zhash ^= Z_EP[fb.ep if fb.ep >= 0 else 64]
        if fb.turn == BLACK:
            zhash ^= Z_SIDE
        fb.zhash = zhash
        return fb

    def is_attacked(self, s: int, by_side: int) -> bool:
        """Атакована ли клетка s стороной by_side (WHITE=0 или BLACK=8)."""
        bb = self.bb
        occ = self.occ
        if by_side == WHITE:
            if PAWN_ATT_B[s] & bb[WP]:
                return True
            if KNIGHT_ATT[s] & bb[WN]:
                return True
            if KING_ATT[s] & bb[WK]:
                return True
            bq = bb[WB] | bb[WQ]
            if bq and (DIAG_ATTACKS[s][occ & DIAG_MASKS[s]] & bq):
                return True
            rq = bb[WR] | bb[WQ]
            if rq and ((FILE_ATTACKS[s][occ & FILE_MASKS[s]] | RANK_ATTACKS[s][occ & RANK_MASKS[s]]) & rq):
                return True
            return False
        else:
            if PAWN_ATT_W[s] & bb[BP]:
                return True
            if KNIGHT_ATT[s] & bb[BN]:
                return True
            if KING_ATT[s] & bb[BK]:
                return True
            bq = bb[BB] | bb[BQ]
            if bq and (DIAG_ATTACKS[s][occ & DIAG_MASKS[s]] & bq):
                return True
            rq = bb[BR] | bb[BQ]
            if rq and ((FILE_ATTACKS[s][occ & FILE_MASKS[s]] | RANK_ATTACKS[s][occ & RANK_MASKS[s]]) & rq):
                return True
            return False

    def in_check(self) -> bool:
        if self.turn == WHITE:
            return self.is_attacked(self.wk_sq, BLACK)
        return self.is_attacked(self.bk_sq, WHITE)

    def make_move(self, move: int) -> bool:
        """
        Выполнить ход. Возвращает True, если ход легален (свой король не под шахом);
        если нелегален — автоматически откатывает и возвращает False.
        """
        f = move & 63
        t = (move >> 6) & 63
        fl = move >> 12

        sq = self.sq
        bb = self.bb
        pc = sq[f]
        cap = sq[t]

        old_c = self.castling
        old_ep = self.ep
        old_hm = self.halfmove
        mg = self.mg
        eg = self.eg
        phase = self.phase
        zhash = self.zhash

        self.undo.append((move, cap, old_c, old_ep, old_hm, mg, eg, phase, zhash))

        zhash ^= Z_CASTLE[old_c] ^ Z_EP[old_ep if old_ep >= 0 else 64] ^ Z_SIDE

        f_bit = 1 << f
        t_bit = 1 << t
        us = self.turn

        # Убираем ходящую фигуру с f
        sq[f] = EMPTY
        bb[pc] ^= f_bit
        mg -= INC_MG[pc][f]
        eg -= INC_EG[pc][f]
        zhash ^= Z_PIECE[pc][f]

        # Убираем взятую фигуру (обычное взятие или en passant)
        if fl == FLAG_EP:
            cap_sq = t - 8 if us == WHITE else t + 8
            cap_pc = BP if us == WHITE else WP
            cap_bit = 1 << cap_sq
            sq[cap_sq] = EMPTY
            bb[cap_pc] ^= cap_bit
            if us == WHITE:
                self.occ_b ^= cap_bit
            else:
                self.occ_w ^= cap_bit
            mg -= INC_MG[cap_pc][cap_sq]
            eg -= INC_EG[cap_pc][cap_sq]
            zhash ^= Z_PIECE[cap_pc][cap_sq]
        elif cap != EMPTY:
            bb[cap] ^= t_bit
            if us == WHITE:
                self.occ_b ^= t_bit
            else:
                self.occ_w ^= t_bit
            mg -= INC_MG[cap][t]
            eg -= INC_EG[cap][t]
            phase -= PHASE_W[cap]
            zhash ^= Z_PIECE[cap][t]

        # Ставим фигуру на t (с учётом превращения)
        if fl & 8:
            new_pc = ((fl & 3) + 2) | us
            phase += PHASE_W[new_pc]
        else:
            new_pc = pc

        sq[t] = new_pc
        bb[new_pc] |= t_bit
        if us == WHITE:
            self.occ_w ^= (f_bit | t_bit)
            if new_pc == WK:
                self.wk_sq = t
        else:
            self.occ_b ^= (f_bit | t_bit)
            if new_pc == BK:
                self.bk_sq = t

        mg += INC_MG[new_pc][t]
        eg += INC_EG[new_pc][t]
        zhash ^= Z_PIECE[new_pc][t]

        # Рокировка: двигаем ладью
        if fl == FLAG_CASTLE_K:
            if us == WHITE:
                rf, rt, rpc = 7, 5, WR
                self.occ_w ^= (1 << 7) | (1 << 5)
            else:
                rf, rt, rpc = 63, 61, BR
                self.occ_b ^= (1 << 63) | (1 << 61)
            sq[rf] = EMPTY
            sq[rt] = rpc
            bb[rpc] ^= (1 << rf) | (1 << rt)
            mg += INC_MG[rpc][rt] - INC_MG[rpc][rf]
            eg += INC_EG[rpc][rt] - INC_EG[rpc][rf]
            zhash ^= Z_PIECE[rpc][rf] ^ Z_PIECE[rpc][rt]
        elif fl == FLAG_CASTLE_Q:
            if us == WHITE:
                rf, rt, rpc = 0, 3, WR
                self.occ_w ^= (1 << 0) | (1 << 3)
            else:
                rf, rt, rpc = 56, 59, BR
                self.occ_b ^= (1 << 56) | (1 << 59)
            sq[rf] = EMPTY
            sq[rt] = rpc
            bb[rpc] ^= (1 << rf) | (1 << rt)
            mg += INC_MG[rpc][rt] - INC_MG[rpc][rf]
            eg += INC_EG[rpc][rt] - INC_EG[rpc][rf]
            zhash ^= Z_PIECE[rpc][rf] ^ Z_PIECE[rpc][rt]

        self.occ = self.occ_w | self.occ_b
        new_c = old_c & CASTLE_MASK[f] & CASTLE_MASK[t]
        self.castling = new_c
        new_ep = ((f + t) >> 1) if fl == FLAG_DOUBLE else -1
        self.ep = new_ep
        self.halfmove = 0 if (cap != EMPTY or (pc & 7) == 1) else (old_hm + 1)
        self.mg = mg
        self.eg = eg
        self.phase = phase
        zhash ^= Z_CASTLE[new_c] ^ Z_EP[new_ep if new_ep >= 0 else 64]
        self.zhash = zhash
        self.turn = us ^ 8

        # Проверка легальности: наш король не должен остаться под шахом
        if us == WHITE:
            if self.is_attacked(self.wk_sq, BLACK):
                self.unmake_move()
                return False
        else:
            if self.is_attacked(self.bk_sq, WHITE):
                self.unmake_move()
                return False
        return True

    def unmake_move(self) -> None:
        move, cap, old_c, old_ep, old_hm, old_mg, old_eg, old_phase, old_zhash = self.undo.pop()
        f = move & 63
        t = (move >> 6) & 63
        fl = move >> 12

        us = self.turn ^ 8
        self.turn = us
        sq = self.sq
        bb = self.bb
        f_bit = 1 << f
        t_bit = 1 << t

        moved_pc = sq[t]
        orig_pc = (WP | us) if (fl & 8) else moved_pc

        bb[moved_pc] ^= t_bit
        bb[orig_pc] |= f_bit
        sq[f] = orig_pc
        sq[t] = EMPTY

        if us == WHITE:
            self.occ_w ^= (f_bit | t_bit)
            if orig_pc == WK:
                self.wk_sq = f
        else:
            self.occ_b ^= (f_bit | t_bit)
            if orig_pc == BK:
                self.bk_sq = f

        if fl == FLAG_EP:
            cap_sq = t - 8 if us == WHITE else t + 8
            cap_pc = BP if us == WHITE else WP
            cap_bit = 1 << cap_sq
            sq[cap_sq] = cap_pc
            bb[cap_pc] |= cap_bit
            if us == WHITE:
                self.occ_b |= cap_bit
            else:
                self.occ_w |= cap_bit
        elif cap != EMPTY:
            sq[t] = cap
            bb[cap] |= t_bit
            if us == WHITE:
                self.occ_b |= t_bit
            else:
                self.occ_w |= t_bit

        if fl == FLAG_CASTLE_K:
            if us == WHITE:
                rf, rt, rpc = 7, 5, WR
                self.occ_w ^= (1 << 7) | (1 << 5)
            else:
                rf, rt, rpc = 63, 61, BR
                self.occ_b ^= (1 << 63) | (1 << 61)
            sq[rt] = EMPTY
            sq[rf] = rpc
            bb[rpc] ^= (1 << rf) | (1 << rt)
        elif fl == FLAG_CASTLE_Q:
            if us == WHITE:
                rf, rt, rpc = 0, 3, WR
                self.occ_w ^= (1 << 0) | (1 << 3)
            else:
                rf, rt, rpc = 56, 59, BR
                self.occ_b ^= (1 << 56) | (1 << 59)
            sq[rt] = EMPTY
            sq[rf] = rpc
            bb[rpc] ^= (1 << rf) | (1 << rt)

        self.occ = self.occ_w | self.occ_b
        self.castling = old_c
        self.ep = old_ep
        self.halfmove = old_hm
        self.mg = old_mg
        self.eg = old_eg
        self.phase = old_phase
        self.zhash = old_zhash

    def make_null_move(self) -> None:
        old_ep = self.ep
        old_zhash = self.zhash
        self.undo.append((0, 0, self.castling, old_ep, self.halfmove, self.mg, self.eg, self.phase, old_zhash))
        self.ep = -1
        self.zhash = old_zhash ^ Z_EP[old_ep if old_ep >= 0 else 64] ^ Z_EP[64] ^ Z_SIDE
        self.turn ^= 8

    def unmake_null_move(self) -> None:
        _, _, _, old_ep, _, _, _, _, old_zhash = self.undo.pop()
        self.turn ^= 8
        self.ep = old_ep
        self.zhash = old_zhash

    def generate_moves(self, captures_only: bool = False) -> list[int]:
        """Генерация псевдо-легальных упакованных ходов (включая превращения и рокировку)."""
        moves: list[int] = []
        append = moves.append
        bb = self.bb
        occ = self.occ
        empty = ~occ & 0xFFFFFFFFFFFFFFFF
        us = self.turn

        if us == WHITE:
            own = self.occ_w
            opp = self.occ_b
            pawns = bb[WP]

            # 1. Пешки (взятия + превращения)
            if pawns:
                # Взятия влево (+7) и вправо (+9)
                cap_l = ((pawns & NOT_FILE_A) << 7) & opp
                while cap_l:
                    lsb = cap_l & -cap_l
                    t = lsb.bit_length() - 1
                    f = t - 7
                    if t >= 56:
                        base = f | (t << 6)
                        append(base | (FLAG_PROMO_CAP_Q << 12))
                        append(base | (FLAG_PROMO_CAP_N << 12))
                        append(base | (FLAG_PROMO_CAP_R << 12))
                        append(base | (FLAG_PROMO_CAP_B << 12))
                    else:
                        append(f | (t << 6) | (FLAG_CAPTURE << 12))
                    cap_l ^= lsb

                cap_r = ((pawns & NOT_FILE_H) << 9) & opp
                while cap_r:
                    lsb = cap_r & -cap_r
                    t = lsb.bit_length() - 1
                    f = t - 9
                    if t >= 56:
                        base = f | (t << 6)
                        append(base | (FLAG_PROMO_CAP_Q << 12))
                        append(base | (FLAG_PROMO_CAP_N << 12))
                        append(base | (FLAG_PROMO_CAP_R << 12))
                        append(base | (FLAG_PROMO_CAP_B << 12))
                    else:
                        append(f | (t << 6) | (FLAG_CAPTURE << 12))
                    cap_r ^= lsb

                # Взятие на проходе
                ep = self.ep
                if ep >= 0:
                    ep_froms = PAWN_ATT_B[ep] & pawns
                    while ep_froms:
                        lsb = ep_froms & -ep_froms
                        f = lsb.bit_length() - 1
                        append(f | (ep << 6) | (FLAG_EP << 12))
                        ep_froms ^= lsb

                # Тихие ходы пешек (в captures_only добавляем тихие превращения в ферзя!)
                single = (pawns << 8) & empty
                promos = single & RANK_8
                while promos:
                    lsb = promos & -promos
                    t = lsb.bit_length() - 1
                    f = t - 8
                    base = f | (t << 6)
                    append(base | (FLAG_PROMO_Q << 12))
                    if not captures_only:
                        append(base | (FLAG_PROMO_N << 12))
                        append(base | (FLAG_PROMO_R << 12))
                        append(base | (FLAG_PROMO_B << 12))
                    promos ^= lsb

                if not captures_only:
                    quiet_p = single & ~RANK_8
                    double = ((quiet_p & chess.BB_RANK_3) << 8) & empty
                    while quiet_p:
                        lsb = quiet_p & -quiet_p
                        t = lsb.bit_length() - 1
                        append((t - 8) | (t << 6))
                        quiet_p ^= lsb
                    while double:
                        lsb = double & -double
                        t = lsb.bit_length() - 1
                        append((t - 16) | (t << 6) | (FLAG_DOUBLE << 12))
                        double ^= lsb

            target = opp if captures_only else (~own & 0xFFFFFFFFFFFFFFFF)

            # 2. Кони
            kn = bb[WN]
            while kn:
                lsb = kn & -kn
                f = lsb.bit_length() - 1
                att = KNIGHT_ATT[f] & target
                while att:
                    t_lsb = att & -att
                    t = t_lsb.bit_length() - 1
                    append(f | (t << 6) | ((FLAG_CAPTURE if (t_lsb & opp) else FLAG_QUIET) << 12))
                    att ^= t_lsb
                kn ^= lsb

            # 3. Слоны и ферзи (диагонали)
            bq = bb[WB] | bb[WQ]
            while bq:
                lsb = bq & -bq
                f = lsb.bit_length() - 1
                att = DIAG_ATTACKS[f][occ & DIAG_MASKS[f]] & target
                while att:
                    t_lsb = att & -att
                    t = t_lsb.bit_length() - 1
                    append(f | (t << 6) | ((FLAG_CAPTURE if (t_lsb & opp) else FLAG_QUIET) << 12))
                    att ^= t_lsb
                bq ^= lsb

            # 4. Ладьи и ферзи (прямые)
            rq = bb[WR] | bb[WQ]
            while rq:
                lsb = rq & -rq
                f = lsb.bit_length() - 1
                att = (FILE_ATTACKS[f][occ & FILE_MASKS[f]] | RANK_ATTACKS[f][occ & RANK_MASKS[f]]) & target
                while att:
                    t_lsb = att & -att
                    t = t_lsb.bit_length() - 1
                    append(f | (t << 6) | ((FLAG_CAPTURE if (t_lsb & opp) else FLAG_QUIET) << 12))
                    att ^= t_lsb
                rq ^= lsb

            # 5. Король + рокировка
            ks = self.wk_sq
            att = KING_ATT[ks] & target
            while att:
                t_lsb = att & -att
                t = t_lsb.bit_length() - 1
                append(ks | (t << 6) | ((FLAG_CAPTURE if (t_lsb & opp) else FLAG_QUIET) << 12))
                att ^= t_lsb

            if not captures_only and self.castling & (CASTLE_WK | CASTLE_WQ):
                if (self.castling & CASTLE_WK) and not (occ & 0x60):  # f1, g1
                    if not self.is_attacked(4, BLACK) and not self.is_attacked(5, BLACK) and not self.is_attacked(6, BLACK):
                        append(4 | (6 << 6) | (FLAG_CASTLE_K << 12))
                if (self.castling & CASTLE_WQ) and not (occ & 0x0E):  # b1, c1, d1
                    if not self.is_attacked(4, BLACK) and not self.is_attacked(3, BLACK) and not self.is_attacked(2, BLACK):
                        append(4 | (2 << 6) | (FLAG_CASTLE_Q << 12))

        else:
            own = self.occ_b
            opp = self.occ_w
            pawns = bb[BP]

            if pawns:
                cap_r = ((pawns & NOT_FILE_H) >> 7) & opp
                while cap_r:
                    lsb = cap_r & -cap_r
                    t = lsb.bit_length() - 1
                    f = t + 7
                    if t <= 7:
                        base = f | (t << 6)
                        append(base | (FLAG_PROMO_CAP_Q << 12))
                        append(base | (FLAG_PROMO_CAP_N << 12))
                        append(base | (FLAG_PROMO_CAP_R << 12))
                        append(base | (FLAG_PROMO_CAP_B << 12))
                    else:
                        append(f | (t << 6) | (FLAG_CAPTURE << 12))
                    cap_r ^= lsb

                cap_l = ((pawns & NOT_FILE_A) >> 9) & opp
                while cap_l:
                    lsb = cap_l & -cap_l
                    t = lsb.bit_length() - 1
                    f = t + 9
                    if t <= 7:
                        base = f | (t << 6)
                        append(base | (FLAG_PROMO_CAP_Q << 12))
                        append(base | (FLAG_PROMO_CAP_N << 12))
                        append(base | (FLAG_PROMO_CAP_R << 12))
                        append(base | (FLAG_PROMO_CAP_B << 12))
                    else:
                        append(f | (t << 6) | (FLAG_CAPTURE << 12))
                    cap_l ^= lsb

                ep = self.ep
                if ep >= 0:
                    ep_froms = PAWN_ATT_W[ep] & pawns
                    while ep_froms:
                        lsb = ep_froms & -ep_froms
                        f = lsb.bit_length() - 1
                        append(f | (ep << 6) | (FLAG_EP << 12))
                        ep_froms ^= lsb

                single = (pawns >> 8) & empty
                promos = single & RANK_1
                while promos:
                    lsb = promos & -promos
                    t = lsb.bit_length() - 1
                    f = t + 8
                    base = f | (t << 6)
                    append(base | (FLAG_PROMO_Q << 12))
                    if not captures_only:
                        append(base | (FLAG_PROMO_N << 12))
                        append(base | (FLAG_PROMO_R << 12))
                        append(base | (FLAG_PROMO_B << 12))
                    promos ^= lsb

                if not captures_only:
                    quiet_p = single & ~RANK_1
                    double = ((quiet_p & chess.BB_RANK_6) >> 8) & empty
                    while quiet_p:
                        lsb = quiet_p & -quiet_p
                        t = lsb.bit_length() - 1
                        append((t + 8) | (t << 6))
                        quiet_p ^= lsb
                    while double:
                        lsb = double & -double
                        t = lsb.bit_length() - 1
                        append((t + 16) | (t << 6) | (FLAG_DOUBLE << 12))
                        double ^= lsb

            target = opp if captures_only else (~own & 0xFFFFFFFFFFFFFFFF)

            kn = bb[BN]
            while kn:
                lsb = kn & -kn
                f = lsb.bit_length() - 1
                att = KNIGHT_ATT[f] & target
                while att:
                    t_lsb = att & -att
                    t = t_lsb.bit_length() - 1
                    append(f | (t << 6) | ((FLAG_CAPTURE if (t_lsb & opp) else FLAG_QUIET) << 12))
                    att ^= t_lsb
                kn ^= lsb

            bq = bb[BB] | bb[BQ]
            while bq:
                lsb = bq & -bq
                f = lsb.bit_length() - 1
                att = DIAG_ATTACKS[f][occ & DIAG_MASKS[f]] & target
                while att:
                    t_lsb = att & -att
                    t = t_lsb.bit_length() - 1
                    append(f | (t << 6) | ((FLAG_CAPTURE if (t_lsb & opp) else FLAG_QUIET) << 12))
                    att ^= t_lsb
                bq ^= lsb

            rq = bb[BR] | bb[BQ]
            while rq:
                lsb = rq & -rq
                f = lsb.bit_length() - 1
                att = (FILE_ATTACKS[f][occ & FILE_MASKS[f]] | RANK_ATTACKS[f][occ & RANK_MASKS[f]]) & target
                while att:
                    t_lsb = att & -att
                    t = t_lsb.bit_length() - 1
                    append(f | (t << 6) | ((FLAG_CAPTURE if (t_lsb & opp) else FLAG_QUIET) << 12))
                    att ^= t_lsb
                rq ^= lsb

            ks = self.bk_sq
            att = KING_ATT[ks] & target
            while att:
                t_lsb = att & -att
                t = t_lsb.bit_length() - 1
                append(ks | (t << 6) | ((FLAG_CAPTURE if (t_lsb & opp) else FLAG_QUIET) << 12))
                att ^= t_lsb

            if not captures_only and self.castling & (CASTLE_BK | CASTLE_BQ):
                if (self.castling & CASTLE_BK) and not (occ & 0x6000000000000000):  # f8, g8
                    if not self.is_attacked(60, WHITE) and not self.is_attacked(61, WHITE) and not self.is_attacked(62, WHITE):
                        append(60 | (62 << 6) | (FLAG_CASTLE_K << 12))
                if (self.castling & CASTLE_BQ) and not (occ & 0x0E00000000000000):  # b8, c8, d8
                    if not self.is_attacked(60, WHITE) and not self.is_attacked(59, WHITE) and not self.is_attacked(58, WHITE):
                        append(60 | (58 << 6) | (FLAG_CASTLE_Q << 12))

        return moves

    def has_non_pawn_material(self) -> bool:
        bb = self.bb
        if self.turn == WHITE:
            return bool(bb[WN] | bb[WB] | bb[WR] | bb[WQ])
        return bool(bb[BN] | bb[BB] | bb[BR] | bb[BQ])

    def is_insufficient_material(self) -> bool:
        bb = self.bb
        if bb[WP] or bb[BP] or bb[WR] or bb[BR] or bb[WQ] or bb[BQ]:
            return False
        minors = (bb[WN] | bb[WB] | bb[BN] | bb[BB]).bit_count()
        return minors <= 1
