"""
evaluation.py – «Человеческая» оценка позиции PyCaesar (PeSTO + позиционные принципы + стили)

Компоненты:
  • PeSTO (Texel-tuned таблицы и раздельные веса фигур для миттельшпиля и эндшпиля)
  • Целочисленная интерполяция фазы (0..24: N=1, B=1, R=2, Q=4)
  • Поддержка стилей игры (Personality: Balanced, Aggressive, Positional, Solid)
  • Форпосты коней и слонов (защищённые своей пешкой и недосягаемые для вражеских пешек)
  • Ладья на 7-й/2-й горизонтали (классика вторжения) + открытые/полуоткрытые линии
  • Дебютная дисциплина: бонус за развитие лёгких фигур и штраф за ранние вылазки ферзя
  • Пешечный хэш (сдвоенные, изолированные, отсталые, поддержанные, проходные по фазе + свободное поле впереди)
  • Безопасная мобильность (без своих фигур и полей под боем вражеских пешек) + угрозы пешками
  • Пара слонов (+30 MG / +50 EG), безопасность короля, бонус темпа и эндшпильный mop-up
"""

import chess

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000,
}

_PESTO_MG_VAL = (82, 337, 365, 477, 1025, 20000)
_PESTO_EG_VAL = (94, 281, 297, 512, 936, 20000)

MAX_PHASE = 24
TEMPO_BONUS = 10
LAZY_MARGIN = 500

ROOK_OPEN_FILE_BONUS = 28
ROOK_SEMI_OPEN_FILE_BONUS = 14
ROOK_ON_SEVENTH_MG = 22
ROOK_ON_SEVENTH_EG = 36

KNIGHT_OUTPOST_BONUS = 34
BISHOP_OUTPOST_BONUS = 20

KING_SHIELD_BONUS = 27
KING_OPEN_FILE_PENALTY = 45

PASSED_PAWN_MG = (0, 0, 5, 10, 20, 35, 60, 0)
PASSED_PAWN_EG = (0, 0, 15, 30, 55, 100, 175, 0)

# PeSTO Piece-Square Tables (порядок A8..H1)
PAWN_PST_MG = [
      0,   0,   0,   0,   0,   0,   0,   0,
     98, 134,  61,  95,  68, 126,  34, -11,
     -6,   7,  26,  31,  65,  56,  25, -20,
    -14,  13,   6,  21,  23,  12,  17, -23,
    -27,  -2,  -5,  12,  17,   6,  10, -25,
    -26,  -4,  -4, -10,   3,   3,  33, -12,
    -35,  -1, -20, -23, -15,  24,  38, -22,
      0,   0,   0,   0,   0,   0,   0,   0,
]

PAWN_PST_EG = [
      0,   0,   0,   0,   0,   0,   0,   0,
    178, 173, 158, 134, 147, 132, 165, 187,
     94, 100,  85,  67,  56,  53,  82,  84,
     32,  24,  13,   5,  -2,   4,  17,  17,
     13,   9,  -3,  -7,  -7,  -8,   3,  -1,
      4,   7,  -6,   1,   0,  -5,  -1,  -8,
     13,   8,   8,  10,  13,   0,   2,  -7,
      0,   0,   0,   0,   0,   0,   0,   0,
]

KNIGHT_PST_MG = [
   -167, -89, -34, -49,  61, -97, -15, -107,
    -73, -41,  72,  36,  23,  62,   7,  -17,
    -47,  60,  37,  65,  84, 129,  73,   44,
     -9,  17,  19,  53,  37,  69,  18,   22,
    -13,   4,  16,  13,  28,  19,  21,   -8,
    -23,  -9,  12,  10,  19,  17,  25,  -16,
    -29, -53, -12,  -3,  -1,  18, -14,  -19,
   -105, -21, -58, -33, -17, -28, -19,  -23,
]

KNIGHT_PST_EG = [
    -58, -38, -13, -28, -31, -27, -63, -99,
    -25,  -8, -25,  -2,  -9, -25, -24, -52,
    -24, -20,  10,   9,  -1,  -9, -19, -41,
    -17,   3,  22,  22,  22,  11,   8, -18,
    -18,  -6,  16,  25,  16,  17,   4, -18,
    -23,  -3,  -1,  15,  10,  -3, -20, -22,
    -42, -20, -10,  -5,  -2, -20, -23, -44,
    -29, -51, -23, -15, -22, -18, -50, -64,
]

BISHOP_PST_MG = [
    -29,   4, -82, -37, -25, -42,   7,  -8,
    -26,  16, -18, -13,  30,  59,  18, -47,
    -16,  37,  43,  40,  35,  50,  37,  -2,
     -4,   5,  19,  50,  37,  37,   7,  -2,
     -6,  13,  13,  26,  34,  12,  10,   4,
      0,  15,  15,  15,  14,  27,  18,  10,
      4,  15,  16,   0,   7,  21,  33,   1,
    -33,  -3, -14, -21, -13, -12, -39, -21,
]

BISHOP_PST_EG = [
    -14, -21, -11,  -8,  -7,  -9, -17, -24,
     -8,  -4,   7, -12,  -3, -13,  -4, -14,
      2,  -8,   0,  -1,  -2,   6,   0,   4,
     -3,   9,  12,   9,  14,  10,   3,   2,
     -6,   3,  13,  19,   7,  10,  -3,  -9,
    -12,  -3,   8,  10,  13,   3,  -7, -15,
    -14, -18,  -7,  -1,   4,  -9, -15, -27,
    -23,  -9, -23,  -5,  -9, -16,  -5, -17,
]

ROOK_PST_MG = [
     32,  42,  32,  51,  63,   9,  31,  43,
     27,  32,  58,  62,  80,  67,  26,  44,
     -5,  19,  26,  36,  17,  45,  61,  16,
    -24, -11,   7,  26,  24,  35,  -8, -20,
    -36, -26, -12,  -1,   9,  -7,   6, -23,
    -45, -25, -16, -17,   3,   0,  -5, -33,
    -44, -16, -20,  -9,  -1,  11,  -6, -71,
    -19, -13,   1,  17,  16,   7, -37, -26,
]

ROOK_PST_EG = [
     13,  10,  18,  15,  12,  12,   8,   5,
     11,  13,  13,  11,  -3,   3,   8,   3,
      7,   7,   7,   5,   4,  -3,  -5,  -3,
      4,   3,  13,   1,   2,   1,  -1,   2,
      3,   5,   8,   4,  -5,  -6,  -8, -11,
     -4,   0,  -5,  -1,  -7, -12,  -8, -16,
     -6,  -6,   0,   2,  -9,  -9, -11,  -3,
     -9,   2,   3,  -1,  -5, -13,   4, -20,
]

QUEEN_PST_MG = [
    -28,   0,  29,  12,  59,  44,  43,  45,
    -24, -39,  -5,   1, -16,  57,  28,  54,
    -13, -17,   7,   8,  29,  56,  47,  57,
    -27, -27, -16, -16,  -1,  17,  -2,   1,
     -9, -26,  -9, -10,  -2,  -4,   3,  -3,
    -14,   2, -11,  -2,  -5,   2,  14,   5,
    -35,  -8,  11,   2,   8,  15,  -3,   1,
     -1, -18,  -9,  10, -15, -25, -31, -50,
]

QUEEN_PST_EG = [
     -9,  22,  22,  27,  27,  19,  10,  20,
    -17,  20,  32,  41,  58,  25,  30,   0,
    -20,   6,   9,  49,  47,  35,  19,   9,
      3,  22,  24,  45,  57,  40,  57,  36,
    -18,  28,  19,  47,  31,  34,  39,  23,
    -16, -27,  15,   6,   9,  17,  10,   5,
    -22, -23, -30, -16, -16, -23, -36, -32,
    -33, -28, -22, -43,  -5, -32, -20, -41,
]

KING_PST_MG = [
    -65,  23,  16, -15, -56, -34,   2,  13,
     29,  -1, -20,  -7,  -8,  -4, -38, -29,
     -9,  24,   2, -16, -20,   6,  22, -22,
    -17, -20, -12, -27, -30, -25, -14, -36,
    -49,  -1, -27, -39, -46, -44, -33, -51,
    -14, -14, -22, -46, -44, -30, -15, -27,
      1,   7,  -8, -64, -43, -16,   9,   8,
    -15,  36,  12, -54,   8, -28,  24,  14,
]

KING_PST_EG = [
    -74, -35, -18, -18, -11,  15,   4, -17,
    -12,  17,  14,  17,  17,  38,  23,  11,
     10,  17,  23,  15,  20,  45,  44,  13,
     -8,  22,  24,  27,  26,  33,  26,   3,
    -18,  -4,  21,  24,  27,  23,   9, -11,
    -19,  -3,  11,  21,  23,  16,   7,  -9,
    -27, -11,   4,  13,  14,   4,  -5, -17,
    -53, -34, -21, -11, -28, -14, -24, -43,
]

_PST_MG_LIST = (PAWN_PST_MG, KNIGHT_PST_MG, BISHOP_PST_MG, ROOK_PST_MG, QUEEN_PST_MG, KING_PST_MG)
_PST_EG_LIST = (PAWN_PST_EG, KNIGHT_PST_EG, BISHOP_PST_EG, ROOK_PST_EG, QUEEN_PST_EG, KING_PST_EG)


def _mirror_square(sq: int) -> int:
    return sq ^ 56


_PST_MG_W = tuple(tuple(_PESTO_MG_VAL[i] + _PST_MG_LIST[i][_mirror_square(s)] for s in range(64)) for i in range(6))
_PST_EG_W = tuple(tuple(_PESTO_EG_VAL[i] + _PST_EG_LIST[i][_mirror_square(s)] for s in range(64)) for i in range(6))
_PST_MG_B = tuple(tuple(_PESTO_MG_VAL[i] + _PST_MG_LIST[i][s] for s in range(64)) for i in range(6))
_PST_EG_B = tuple(tuple(_PESTO_EG_VAL[i] + _PST_EG_LIST[i][s] for s in range(64)) for i in range(6))

_ADJACENT_FILES = tuple(
    (chess.BB_FILES[f - 1] if f > 0 else 0) | (chess.BB_FILES[f + 1] if f < 7 else 0)
    for f in range(8)
)

# Маски для проходных и для форпостов (соседние вертикали строго впереди клетки)
_PASSED_MASK_W = [0] * 64
_PASSED_MASK_B = [0] * 64
_OUTPOST_ENEMY_W = [0] * 64
_OUTPOST_ENEMY_B = [0] * 64
for _s in range(64):
    _f = chess.square_file(_s)
    _r = chess.square_rank(_s)
    _adj = _ADJACENT_FILES[_f]
    _span = chess.BB_FILES[_f] | _adj
    _aw = 0
    for _nr in range(_r + 1, 8):
        _aw |= chess.BB_RANKS[_nr]
    _ab = 0
    for _nr in range(0, _r):
        _ab |= chess.BB_RANKS[_nr]
    _PASSED_MASK_W[_s] = _span & _aw
    _PASSED_MASK_B[_s] = _span & _ab
    _OUTPOST_ENEMY_W[_s] = _adj & _aw
    _OUTPOST_ENEMY_B[_s] = _adj & _ab
_PASSED_MASK_W = tuple(_PASSED_MASK_W)
_PASSED_MASK_B = tuple(_PASSED_MASK_B)
_OUTPOST_ENEMY_W = tuple(_OUTPOST_ENEMY_W)
_OUTPOST_ENEMY_B = tuple(_OUTPOST_ENEMY_B)

# Зоны форпостов (4–6-я горизонтали для белых, 3–5-я для чёрных, без крайних вертикалей a/h)
_CENTER_FILES = chess.BB_FILE_B | chess.BB_FILE_C | chess.BB_FILE_D | chess.BB_FILE_E | chess.BB_FILE_F | chess.BB_FILE_G
_OUTPOST_ZONE_W = (chess.BB_RANK_4 | chess.BB_RANK_5 | chess.BB_RANK_6) & _CENTER_FILES
_OUTPOST_ZONE_B = (chess.BB_RANK_3 | chess.BB_RANK_4 | chess.BB_RANK_5) & _CENTER_FILES

# Стартовые поля лёгких фигур и ферзя (для дебютной дисциплины)
_W_START_MINORS = chess.BB_B1 | chess.BB_C1 | chess.BB_F1 | chess.BB_G1
_B_START_MINORS = chess.BB_B8 | chess.BB_C8 | chess.BB_F8 | chess.BB_G8
_W_QUEEN_HOME = chess.BB_D1
_B_QUEEN_HOME = chess.BB_D8

_SHIELD_MASKS = {chess.WHITE: [()] * 64, chess.BLACK: [()] * 64}
for _color in (chess.WHITE, chess.BLACK):
    _dir = 1 if _color == chess.WHITE else -1
    for _s in range(64):
        _kf = chess.square_file(_s)
        _kr = chess.square_rank(_s)
        _masks = []
        for _df in (-1, 0, 1):
            _f2 = _kf + _df
            if 0 <= _f2 <= 7:
                _m = 0
                for _dr in (1, 2):
                    _r2 = _kr + _dir * _dr
                    if 0 <= _r2 <= 7:
                        _m |= chess.BB_SQUARES[chess.square(_f2, _r2)]
                if _m:
                    _masks.append(_m)
        _SHIELD_MASKS[_color][_s] = tuple(_masks)
_SHIELD_W = tuple(_SHIELD_MASKS[chess.WHITE])
_SHIELD_B = tuple(_SHIELD_MASKS[chess.BLACK])

_KING_FILE_LIST = tuple(
    tuple(f2 for f2 in (chess.square_file(s) - 1, chess.square_file(s), chess.square_file(s) + 1) if 0 <= f2 <= 7)
    for s in range(64)
)

_CENTER_MANHATTAN = tuple(
    max(3 - chess.square_file(s), chess.square_file(s) - 4)
    + max(3 - chess.square_rank(s), chess.square_rank(s) - 4)
    for s in range(64)
)

_BB_FILES = tuple(chess.BB_FILES)
_NOT_FILE_A = ~chess.BB_FILE_A & 0xFFFFFFFFFFFFFFFF
_NOT_FILE_H = ~chess.BB_FILE_H & 0xFFFFFFFFFFFFFFFF
_PAWN_ATT_W = tuple(chess.BB_PAWN_ATTACKS[chess.WHITE])
_PAWN_ATT_B = tuple(chess.BB_PAWN_ATTACKS[chess.BLACK])
_BB_KING_ATTACKS = tuple(chess.BB_KING_ATTACKS)

_PAWN_HASH: dict[tuple[int, int], tuple[int, int, int, int, int, int]] = {}
_PAWN_HASH_MAX = 32768

del _s, _f, _r, _adj, _span, _aw, _ab, _nr, _color, _dir, _kf, _kr, _masks, _df, _f2, _m, _dr, _r2, _SHIELD_MASKS


def clear_pawn_hash() -> None:
    _PAWN_HASH.clear()


def _game_phase_int(board: chess.Board) -> int:
    phase = (
        board.knights.bit_count()
        + board.bishops.bit_count()
        + (board.rooks.bit_count() << 1)
        + (board.queens.bit_count() << 2)
    )
    return MAX_PHASE if phase > MAX_PHASE else phase


def _evaluate_material_and_pst(board: chess.Board) -> tuple[int, int]:
    mg_score = 0
    eg_score = 0
    scan = chess.scan_forward
    white = board.occupied_co[chess.WHITE]
    black = board.occupied_co[chess.BLACK]

    boards = (board.pawns, board.knights, board.bishops, board.rooks, board.queens, board.kings)
    for idx in range(6):
        piece_bb = boards[idx]
        if not piece_bb:
            continue
        w_bb = piece_bb & white
        if w_bb:
            mg_w = _PST_MG_W[idx]
            eg_w = _PST_EG_W[idx]
            for sq in scan(w_bb):
                mg_score += mg_w[sq]
                eg_score += eg_w[sq]
        b_bb = piece_bb & black
        if b_bb:
            mg_b = _PST_MG_B[idx]
            eg_b = _PST_EG_B[idx]
            for sq in scan(b_bb):
                mg_score -= mg_b[sq]
                eg_score -= eg_b[sq]
    return mg_score, eg_score


def _evaluate_pawn_structure_split(board: chess.Board) -> tuple[int, int, int, int, int, int]:
    """Возвращает (struct_mg, struct_eg, passed_mg, passed_eg, passed_w_bb, passed_b_bb)."""
    pawns = board.pawns
    if not pawns:
        return 0, 0, 0, 0, 0, 0

    white_pawns = pawns & board.occupied_co[chess.WHITE]
    black_pawns = pawns & board.occupied_co[chess.BLACK]
    key = (white_pawns, black_pawns)
    cached = _PAWN_HASH.get(key)
    if cached is not None:
        return cached

    s_mg = 0
    s_eg = 0
    p_mg = 0
    p_eg = 0
    passed_w = 0
    passed_b = 0
    scan = chess.scan_forward
    files = _BB_FILES

    white_counts = [
        (white_pawns & files[0]).bit_count(), (white_pawns & files[1]).bit_count(),
        (white_pawns & files[2]).bit_count(), (white_pawns & files[3]).bit_count(),
        (white_pawns & files[4]).bit_count(), (white_pawns & files[5]).bit_count(),
        (white_pawns & files[6]).bit_count(), (white_pawns & files[7]).bit_count(),
    ]
    black_counts = [
        (black_pawns & files[0]).bit_count(), (black_pawns & files[1]).bit_count(),
        (black_pawns & files[2]).bit_count(), (black_pawns & files[3]).bit_count(),
        (black_pawns & files[4]).bit_count(), (black_pawns & files[5]).bit_count(),
        (black_pawns & files[6]).bit_count(), (black_pawns & files[7]).bit_count(),
    ]

    for sq in scan(white_pawns):
        f = sq & 7
        r = sq >> 3
        if white_counts[f] > 1:
            s_mg -= 12
            s_eg -= 20
        has_neighbor = bool(white_pawns & _ADJACENT_FILES[f])
        if not has_neighbor:
            s_mg -= 15
            s_eg -= 18
        if not (_PASSED_MASK_W[sq] & black_pawns):
            p_mg += PASSED_PAWN_MG[r]
            p_eg += PASSED_PAWN_EG[r]
            passed_w |= (1 << sq)
        if _PAWN_ATT_B[sq] & white_pawns:
            s_mg += 10
            s_eg += 8
        elif not has_neighbor:
            s_mg -= 10
            s_eg -= 8

    for sq in scan(black_pawns):
        f = sq & 7
        r = 7 - (sq >> 3)
        if black_counts[f] > 1:
            s_mg += 12
            s_eg += 20
        has_neighbor = bool(black_pawns & _ADJACENT_FILES[f])
        if not has_neighbor:
            s_mg += 15
            s_eg += 18
        if not (_PASSED_MASK_B[sq] & white_pawns):
            p_mg -= PASSED_PAWN_MG[r]
            p_eg -= PASSED_PAWN_EG[r]
            passed_b |= (1 << sq)
        if _PAWN_ATT_W[sq] & black_pawns:
            s_mg -= 10
            s_eg -= 8
        elif not has_neighbor:
            s_mg += 10
            s_eg += 8

    res = (s_mg, s_eg, p_mg, p_eg, passed_w, passed_b)
    if len(_PAWN_HASH) >= _PAWN_HASH_MAX:
        _PAWN_HASH.clear()
    _PAWN_HASH[key] = res
    return res


def _evaluate_pawn_structure(board: chess.Board) -> int:
    s_mg, s_eg, p_mg, p_eg, _, _ = _evaluate_pawn_structure_split(board)
    return (s_mg + s_eg + p_mg + p_eg) // 2


def _evaluate_mobility(board: chess.Board) -> int:
    score = 0
    scan = chess.scan_forward
    attacks_mask = board.attacks_mask
    white = board.occupied_co[chess.WHITE]
    black = board.occupied_co[chess.BLACK]
    pawns = board.pawns
    wp = pawns & white
    bp = pawns & black
    w_pawn_att = ((wp & _NOT_FILE_A) << 7) | ((wp & _NOT_FILE_H) << 9)
    b_pawn_att = ((bp & _NOT_FILE_H) >> 7) | ((bp & _NOT_FILE_A) >> 9)
    safe_w = ~(white | b_pawn_att)
    safe_b = ~(black | w_pawn_att)

    for bb, bonus in (
        (board.knights, 4),
        (board.bishops, 4),
        (board.rooks, 2),
        (board.queens, 1),
    ):
        if not bb:
            continue
        w_tot = 0
        for sq in scan(bb & white):
            w_tot += (attacks_mask(sq) & safe_w).bit_count()
        b_tot = 0
        for sq in scan(bb & black):
            b_tot += (attacks_mask(sq) & safe_b).bit_count()
        score += bonus * (w_tot - b_tot)
    return score


def _evaluate_positional(
    board: chess.Board,
    phase: int,
    passed_w: int,
    passed_b: int,
) -> tuple[int, int]:
    mg = 0
    eg = 0
    scan = chess.scan_forward
    attacks_mask = board.attacks_mask
    white = board.occupied_co[chess.WHITE]
    black = board.occupied_co[chess.BLACK]
    occupied = white | black

    pawns = board.pawns
    white_pawns = pawns & white
    black_pawns = pawns & black

    w_pawn_att = ((white_pawns & _NOT_FILE_A) << 7) | ((white_pawns & _NOT_FILE_H) << 9)
    b_pawn_att = ((black_pawns & _NOT_FILE_H) >> 7) | ((black_pawns & _NOT_FILE_A) >> 9)
    safe_w = ~(white | b_pawn_att)
    safe_b = ~(black | w_pawn_att)

    knights = board.knights
    bishops = board.bishops
    rooks = board.rooks
    queens = board.queens

    # 1. Угрозы пешками по фигурам соперника
    if w_pawn_att:
        w_threats = (
            (w_pawn_att & (knights | bishops) & black).bit_count() * 25
            + (w_pawn_att & rooks & black).bit_count() * 40
            + (w_pawn_att & queens & black).bit_count() * 55
        )
        mg += w_threats
        eg += w_threats >> 1
    if b_pawn_att:
        b_threats = (
            (b_pawn_att & (knights | bishops) & white).bit_count() * 25
            + (b_pawn_att & rooks & white).bit_count() * 40
            + (b_pawn_att & queens & white).bit_count() * 55
        )
        mg -= b_threats
        eg -= b_threats >> 1

    # 2. Незаблокированные проходные пешки
    if passed_w:
        free_w = (passed_w << 8) & ~occupied
        if free_w:
            eg += free_w.bit_count() * 20
    if passed_b:
        free_b = (passed_b >> 8) & ~occupied
        if free_b:
            eg -= free_b.bit_count() * 20

    # 3. Форпосты коней и слонов (защищены своей пешкой, не прогоняются вражеской пешкой)
    outpost_score = 0
    w_kn_out = knights & white & _OUTPOST_ZONE_W & w_pawn_att
    if w_kn_out:
        for sq in scan(w_kn_out):
            if not (_OUTPOST_ENEMY_W[sq] & black_pawns):
                outpost_score += KNIGHT_OUTPOST_BONUS
    b_kn_out = knights & black & _OUTPOST_ZONE_B & b_pawn_att
    if b_kn_out:
        for sq in scan(b_kn_out):
            if not (_OUTPOST_ENEMY_B[sq] & white_pawns):
                outpost_score -= KNIGHT_OUTPOST_BONUS

    w_bi_out = bishops & white & _OUTPOST_ZONE_W & w_pawn_att
    if w_bi_out:
        for sq in scan(w_bi_out):
            if not (_OUTPOST_ENEMY_W[sq] & black_pawns):
                outpost_score += BISHOP_OUTPOST_BONUS
    b_bi_out = bishops & black & _OUTPOST_ZONE_B & b_pawn_att
    if b_bi_out:
        for sq in scan(b_bi_out):
            if not (_OUTPOST_ENEMY_B[sq] & white_pawns):
                outpost_score -= BISHOP_OUTPOST_BONUS

    if outpost_score:
        scaled_out = outpost_score
        mg += scaled_out
        eg += scaled_out >> 1

    # 4. Дебютная дисциплина (при phase >= 18): неразвитые лёгкие фигуры + ранний выход ферзя
    if phase >= 18:
        dev_score = 0
        w_un = ((knights | bishops) & white & _W_START_MINORS).bit_count()
        b_un = ((knights | bishops) & black & _B_START_MINORS).bit_count()
        dev_score += (b_un - w_un) * 8
        if w_un >= 2 and (queens & white) and not (queens & white & _W_QUEEN_HOME):
            dev_score -= 18
        if b_un >= 2 and (queens & black) and not (queens & black & _B_QUEEN_HOME):
            dev_score += 18
        if dev_score:
            mg += int(dev_score * 1.15)

    # 5. Пара слонов (сильнее в эндшпиле)
    if bishops:
        if (bishops & white).bit_count() >= 2:
            mg += 33
            eg += 55
        if (bishops & black).bit_count() >= 2:
            mg -= 33
            eg -= 55

    w_king = board.king(chess.WHITE)
    b_king = board.king(chess.BLACK)

    # 6. Ладьи: открытые/полуоткрытые линии + вторжение на 7-ю/2-ю горизонталь
    if rooks:
        r_mg = 0
        r_eg = 0
        files = _BB_FILES
        b_king_rank = (b_king >> 3) if b_king is not None else -1
        w_king_rank = (w_king >> 3) if w_king is not None else -1
        b_pawns_r7 = bool(black_pawns & chess.BB_RANK_7)
        w_pawns_r2 = bool(white_pawns & chess.BB_RANK_2)

        for sq in scan(rooks & white):
            fm = files[sq & 7]
            if not (white_pawns & fm):
                if not (black_pawns & fm):
                    r_mg += ROOK_OPEN_FILE_BONUS
                    r_eg += 14
                else:
                    r_mg += ROOK_SEMI_OPEN_FILE_BONUS
                    r_eg += 7
            if (sq >> 3) == 6 and (b_king_rank == 7 or b_pawns_r7):
                r_mg += ROOK_ON_SEVENTH_MG
                r_eg += ROOK_ON_SEVENTH_EG

        for sq in scan(rooks & black):
            fm = files[sq & 7]
            if not (black_pawns & fm):
                if not (white_pawns & fm):
                    r_mg -= ROOK_OPEN_FILE_BONUS
                    r_eg -= 14
                else:
                    r_mg -= ROOK_SEMI_OPEN_FILE_BONUS
                    r_eg -= 7
            if (sq >> 3) == 1 and (w_king_rank == 0 or w_pawns_r2):
                r_mg -= ROOK_ON_SEVENTH_MG
                r_eg -= ROOK_ON_SEVENTH_EG

        mg += r_mg
        eg += r_eg

    # 7. Безопасная мобильность + атаки на зону короля за один проход
    do_king_safety = phase >= 7
    w_kzone = _BB_KING_ATTACKS[w_king] if (do_king_safety and w_king is not None) else 0
    b_kzone = _BB_KING_ATTACKS[b_king] if (do_king_safety and b_king is not None) else 0
    w_attack_weight = 0
    b_attack_weight = 0

    mob_score = 0
    for bb, mob_bonus, ka_weight in (
        (knights, 4, 20),
        (bishops, 4, 20),
        (rooks, 2, 40),
        (queens, 1, 80),
    ):
        if not bb:
            continue
        w_mob = 0
        for sq in scan(bb & white):
            att = attacks_mask(sq)
            w_mob += (att & safe_w).bit_count()
            if b_kzone and (att & b_kzone):
                b_attack_weight += ka_weight
        b_mob = 0
        for sq in scan(bb & black):
            att = attacks_mask(sq)
            b_mob += (att & safe_b).bit_count()
            if w_kzone and (att & w_kzone):
                w_attack_weight += ka_weight
        mob_score += mob_bonus * (w_mob - b_mob)

    mob_score = int(mob_score * 0.95)
    mg += mob_score
    eg += mob_score

    if do_king_safety:
        ks_shield = 0
        ks_attack = 0
        files = _BB_FILES
        if w_king is not None:
            for mask in _SHIELD_W[w_king]:
                if mask & white_pawns:
                    ks_shield += KING_SHIELD_BONUS
            for f in _KING_FILE_LIST[w_king]:
                fm = files[f]
                if not (white_pawns & fm):
                    ks_shield -= KING_OPEN_FILE_PENALTY if not (black_pawns & fm) else (KING_OPEN_FILE_PENALTY >> 1)
            if w_attack_weight:
                ks_attack -= min(350, (w_attack_weight * w_attack_weight) // 110)

        if b_king is not None:
            for mask in _SHIELD_B[b_king]:
                if mask & black_pawns:
                    ks_shield -= KING_SHIELD_BONUS
            for f in _KING_FILE_LIST[b_king]:
                fm = files[f]
                if not (black_pawns & fm):
                    ks_shield += KING_OPEN_FILE_PENALTY if not (white_pawns & fm) else (KING_OPEN_FILE_PENALTY >> 1)
            if b_attack_weight:
                ks_attack += min(350, (b_attack_weight * b_attack_weight) // 110)

        mg += ks_shield + int(ks_attack * 0.85)

    return mg, eg


def evaluate(board: chess.Board, alpha: int | None = None, beta: int | None = None) -> int:
    """
    Фирменная статическая оценка PyCaesar (встроенный стиль — лучший по турниру против Stockfish 19):
    усиленная безопасность короля и пешечная структура, форпосты, ладья на 7-й и PeSTO.
    """
    if board.is_insufficient_material():
        return 0

    phase = _game_phase_int(board)
    mg_score, eg_score = _evaluate_material_and_pst(board)

    s_mg, s_eg, p_mg, p_eg, passed_w, passed_b = _evaluate_pawn_structure_split(board)
    mg_score += int(s_mg * 1.30) + int(p_mg * 1.10)
    eg_score += int(s_eg * 1.30) + int(p_eg * 1.10)

    pos_mg, pos_eg = _evaluate_positional(board, phase, passed_w, passed_b)
    mg_score += pos_mg
    eg_score += pos_eg

    if phase <= 8 and abs(eg_score) >= 350:
        w_king = board.king(chess.WHITE)
        b_king = board.king(chess.BLACK)
        if w_king is not None and b_king is not None:
            king_dist = abs((w_king & 7) - (b_king & 7)) + abs((w_king >> 3) - (b_king >> 3))
            if eg_score > 0:
                eg_score += _CENTER_MANHATTAN[b_king] * 10 + (14 - king_dist) * 4
            else:
                eg_score -= _CENTER_MANHATTAN[w_king] * 10 + (14 - king_dist) * 4

    score = int((mg_score * phase + eg_score * (MAX_PHASE - phase)) / MAX_PHASE)

    if board.turn == chess.WHITE:
        return score + TEMPO_BONUS
    return -score + TEMPO_BONUS
