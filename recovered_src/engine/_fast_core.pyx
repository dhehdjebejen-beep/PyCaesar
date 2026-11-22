# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, initializedcheck=False
"""
_fast_core.pyx — Скомпилированное Cython-ядро PyCaesar (Python с C-типами).

Даёт 50–150× ускорение по сравнению с чистым CPython:
  • 64-битные битборды uint64_t + аппаратные POPCNT/TZCNT
  • Плоская C-таблица транспозиций (16 байт на запись, без Python-объектов)
  • Инкрементальная оценка PeSTO + фирменный стиль PyCaesar + C-пешечный хэш
  • Полный поиск: PVS, NMP, RFP, Futility (с защитой шахов), LMP, IIR, LMR, SEE, Killers, Counter-Move, History
"""

from libc.stdint cimport uint64_t, uint32_t, uint16_t, uint8_t, int32_t, int16_t, int8_t
from libc.stdlib cimport malloc, free
from libc.string cimport memset, memcpy
import time
import math
import chess

cdef extern from *:
    """
    #if defined(_MSC_VER) && !defined(__clang__)
    #include <intrin.h>
    static inline int c_ctz64(unsigned long long x) {
        unsigned long idx = 0;
        _BitScanForward64(&idx, x);
        return (int)idx;
    }
    static inline int c_clz64(unsigned long long x) {
        unsigned long idx = 0;
        _BitScanReverse64(&idx, x);
        return 63 - (int)idx;
    }
    static inline int c_popcnt64(unsigned long long x) {
        return (int)__popcnt64(x);
    }
    #else
    static inline int c_ctz64(unsigned long long x) { return __builtin_ctzll(x); }
    static inline int c_clz64(unsigned long long x) { return __builtin_clzll(x); }
    static inline int c_popcnt64(unsigned long long x) { return __builtin_popcountll(x); }
    #endif

    #ifdef _WIN32
    #ifndef WIN32_LEAN_AND_MEAN
    #define WIN32_LEAN_AND_MEAN
    #endif
    #include <windows.h>
    static inline double c_now_sec(void) {
        static double inv_freq = 0.0;
        LARGE_INTEGER counter;
        if (inv_freq == 0.0) {
            LARGE_INTEGER freq;
            QueryPerformanceFrequency(&freq);
            inv_freq = 1.0 / (double)freq.QuadPart;
        }
        QueryPerformanceCounter(&counter);
        return (double)counter.QuadPart * inv_freq;
    }
    #else
    #include <time.h>
    static inline double c_now_sec(void) {
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
    }
    #endif
    """
    int c_ctz64(uint64_t x) noexcept nogil
    int c_clz64(uint64_t x) noexcept nogil
    int c_popcnt64(uint64_t x) noexcept nogil
    double c_now_sec() noexcept nogil

# Константы
DEF EMPTY = 0
DEF WP = 1
DEF WN = 2
DEF WB = 3
DEF WR = 4
DEF WQ = 5
DEF WK = 6
DEF BP = 9
DEF BN = 10
DEF BB = 11
DEF BR = 12
DEF BQ = 13
DEF BK = 14

DEF WHITE = 0
DEF BLACK = 8

DEF FLAG_QUIET = 0
DEF FLAG_DOUBLE = 1
DEF FLAG_CASTLE_K = 2
DEF FLAG_CASTLE_Q = 3
DEF FLAG_CAPTURE = 4
DEF FLAG_EP = 5
DEF FLAG_PROMO_N = 8
DEF FLAG_PROMO_B = 9
DEF FLAG_PROMO_R = 10
DEF FLAG_PROMO_Q = 11
DEF FLAG_PROMO_CAP_N = 12
DEF FLAG_PROMO_CAP_B = 13
DEF FLAG_PROMO_CAP_R = 14
DEF FLAG_PROMO_CAP_Q = 15

DEF CASTLE_WK = 1
DEF CASTLE_WQ = 2
DEF CASTLE_BK = 4
DEF CASTLE_BQ = 8

DEF INFINITY = 99999
DEF MATE_SCORE = 99000
DEF TB_WIN = 20000
DEF TT_NONE = 0
DEF TT_EXACT = 1
DEF TT_ALPHA = 2
DEF TT_BETA = 3

DEF MAX_PLY = 128
DEF MAX_MOVES = 256
DEF MAX_PHASE = 24
DEF PAWN_HASH_BITS = 16
DEF PAWN_HASH_SIZE = 1 << PAWN_HASH_BITS
DEF PAWN_HASH_MASK = PAWN_HASH_SIZE - 1

DEF CONTEMPT = 12
DEF TEMPO_BONUS = 10

cdef uint64_t NOT_FILE_A = 0xFEFEFEFEFEFEFEFEULL
cdef uint64_t NOT_FILE_H = 0x7F7F7F7F7F7F7F7FULL
cdef uint64_t RANK_1 = 0x00000000000000FFULL
cdef uint64_t RANK_2 = 0x000000000000FF00ULL
cdef uint64_t RANK_3 = 0x0000000000FF0000ULL
cdef uint64_t RANK_4 = 0x00000000FF000000ULL
cdef uint64_t RANK_5 = 0x000000FF00000000ULL
cdef uint64_t RANK_6 = 0x0000FF0000000000ULL
cdef uint64_t RANK_7 = 0x00FF000000000000ULL
cdef uint64_t RANK_8 = 0xFF00000000000000ULL
cdef uint64_t PROMO_RANKS = RANK_1 | RANK_8
cdef uint64_t CENTER_FILES = 0x7E7E7E7E7E7E7E7EULL
cdef uint64_t OUTPOST_ZONE_W = (RANK_4 | RANK_5 | RANK_6) & CENTER_FILES
cdef uint64_t OUTPOST_ZONE_B = (RANK_3 | RANK_4 | RANK_5) & CENTER_FILES
cdef uint64_t W_START_MINORS = (1ULL << 1) | (1ULL << 2) | (1ULL << 5) | (1ULL << 6)
cdef uint64_t B_START_MINORS = (1ULL << 57) | (1ULL << 58) | (1ULL << 61) | (1ULL << 62)

# Предвычисленные C-таблицы
cdef uint64_t KNIGHT_ATT[64]
cdef uint64_t KING_ATT[64]
cdef uint64_t PAWN_ATT_W[64]
cdef uint64_t PAWN_ATT_B[64]
cdef uint64_t RAY_ATT[8][64]  # 0=N, 1=S, 2=E, 3=W, 4=NE, 5=NW, 6=SE, 7=SW
cdef uint64_t BB_FILES[8]
cdef uint64_t ADJ_FILES[8]
cdef uint64_t PASSED_W[64]
cdef uint64_t PASSED_B[64]
cdef uint64_t OUTPOST_ENEMY_W[64]
cdef uint64_t OUTPOST_ENEMY_B[64]
cdef uint64_t SHIELD_W[64][3]
cdef uint64_t SHIELD_B[64][3]
cdef int SHIELD_CNT[64]
cdef int KING_FILES[64][3]
cdef int KING_FILE_CNT[64]
cdef int CENTER_MANHATTAN[64]
cdef int CASTLE_MASK[64]
cdef int INC_MG[15][64]
cdef int INC_EG[15][64]
cdef int PHASE_W[15]
cdef int PIECE_VAL[7]
cdef int PASSED_MG[8]
cdef int PASSED_EG[8]
cdef int LMR_TABLE[64][64]
cdef uint64_t Z_PIECE[15][64]
cdef uint64_t Z_CASTLE[16]
cdef uint64_t Z_EP[65]
cdef uint64_t Z_SIDE

cdef struct UndoEntry:
    uint16_t move
    uint8_t cap
    uint8_t castling
    int8_t ep
    uint16_t halfmove
    int mg
    int eg
    int phase
    uint64_t zhash

cdef struct BoardState:
    int sq[64]
    uint64_t bb[15]
    uint64_t occ_w
    uint64_t occ_b
    uint64_t occ
    int turn
    int castling
    int ep
    int halfmove
    int wk_sq
    int bk_sq
    int mg
    int eg
    int phase
    uint64_t zhash
    UndoEntry undo[512]
    int sp

cdef struct TTEntry:
    uint64_t key
    int32_t score
    uint16_t move
    int8_t depth
    uint8_t flag

cdef struct PawnEntry:
    uint64_t wp
    uint64_t bp
    uint64_t passed_w
    uint64_t passed_b
    int16_t mg
    int16_t eg


cdef void _init_tables():
    cdef int s, f, r, df, dr, f2, r2, d, i, pt, cnt
    cdef uint64_t mask, span, aw, ab

    PIECE_VAL[0] = 0
    PIECE_VAL[1] = 100
    PIECE_VAL[2] = 320
    PIECE_VAL[3] = 330
    PIECE_VAL[4] = 500
    PIECE_VAL[5] = 900
    PIECE_VAL[6] = 20000

    cdef int pmg[8]
    cdef int peg[8]
    pmg[0] = 0; pmg[1] = 0; pmg[2] = 5; pmg[3] = 10; pmg[4] = 20; pmg[5] = 35; pmg[6] = 60; pmg[7] = 0
    peg[0] = 0; peg[1] = 0; peg[2] = 15; peg[3] = 30; peg[4] = 55; peg[5] = 100; peg[6] = 175; peg[7] = 0
    for i in range(8):
        PASSED_MG[i] = pmg[i]
        PASSED_EG[i] = peg[i]

    for f in range(8):
        BB_FILES[f] = 0x0101010101010101ULL << f
    for f in range(8):
        ADJ_FILES[f] = (BB_FILES[f - 1] if f > 0 else 0ULL) | (BB_FILES[f + 1] if f < 7 else 0ULL)

    cdef int dirs_f[8]
    cdef int dirs_r[8]
    dirs_f[0] = 0;  dirs_r[0] = 1   # N
    dirs_f[1] = 0;  dirs_r[1] = -1  # S
    dirs_f[2] = 1;  dirs_r[2] = 0   # E
    dirs_f[3] = -1; dirs_r[3] = 0   # W
    dirs_f[4] = 1;  dirs_r[4] = 1   # NE
    dirs_f[5] = -1; dirs_r[5] = 1   # NW
    dirs_f[6] = 1;  dirs_r[6] = -1  # SE
    dirs_f[7] = -1; dirs_r[7] = -1  # SW

    for s in range(64):
        KNIGHT_ATT[s] = chess.BB_KNIGHT_ATTACKS[s]
        KING_ATT[s] = chess.BB_KING_ATTACKS[s]
        PAWN_ATT_W[s] = chess.BB_PAWN_ATTACKS[chess.WHITE][s]
        PAWN_ATT_B[s] = chess.BB_PAWN_ATTACKS[chess.BLACK][s]
        CASTLE_MASK[s] = 15

        f = s & 7
        r = s >> 3
        for d in range(8):
            mask = 0ULL
            f2 = f + dirs_f[d]
            r2 = r + dirs_r[d]
            while 0 <= f2 <= 7 and 0 <= r2 <= 7:
                mask |= (1ULL << (r2 * 8 + f2))
                f2 += dirs_f[d]
                r2 += dirs_r[d]
            RAY_ATT[d][s] = mask

        span = BB_FILES[f] | ADJ_FILES[f]
        aw = 0ULL
        for r2 in range(r + 1, 8):
            aw |= (0xFFULL << (r2 * 8))
        ab = 0ULL
        for r2 in range(0, r):
            ab |= (0xFFULL << (r2 * 8))
        PASSED_W[s] = span & aw
        PASSED_B[s] = span & ab
        OUTPOST_ENEMY_W[s] = ADJ_FILES[f] & aw
        OUTPOST_ENEMY_B[s] = ADJ_FILES[f] & ab

        cnt = 0
        for df in (-1, 0, 1):
            f2 = f + df
            if 0 <= f2 <= 7:
                KING_FILES[s][cnt] = f2
                mask = 0ULL
                for dr in (1, 2):
                    r2 = r + dr
                    if 0 <= r2 <= 7:
                        mask |= (1ULL << (r2 * 8 + f2))
                SHIELD_W[s][cnt] = mask
                mask = 0ULL
                for dr in (1, 2):
                    r2 = r - dr
                    if 0 <= r2 <= 7:
                        mask |= (1ULL << (r2 * 8 + f2))
                SHIELD_B[s][cnt] = mask
                cnt += 1
        SHIELD_CNT[s] = cnt
        KING_FILE_CNT[s] = cnt

        df = (3 - f) if (3 - f) > (f - 4) else (f - 4)
        dr = (3 - r) if (3 - r) > (r - 4) else (r - 4)
        CENTER_MANHATTAN[s] = df + dr

    CASTLE_MASK[0] &= ~CASTLE_WQ
    CASTLE_MASK[4] &= ~(CASTLE_WK | CASTLE_WQ)
    CASTLE_MASK[7] &= ~CASTLE_WK
    CASTLE_MASK[56] &= ~CASTLE_BQ
    CASTLE_MASK[60] &= ~(CASTLE_BK | CASTLE_BQ)
    CASTLE_MASK[63] &= ~CASTLE_BK

    from engine.fast_board import INC_MG as PY_MG, INC_EG as PY_EG, PHASE_W as PY_PW, Z_PIECE as PY_ZP, Z_CASTLE as PY_ZC, Z_EP as PY_ZE, Z_SIDE as PY_ZS
    for pt in range(15):
        PHASE_W[pt] = PY_PW[pt]
        for s in range(64):
            INC_MG[pt][s] = PY_MG[pt][s]
            INC_EG[pt][s] = PY_EG[pt][s]
            Z_PIECE[pt][s] = PY_ZP[pt][s]
    for i in range(16):
        Z_CASTLE[i] = PY_ZC[i]
    for i in range(65):
        Z_EP[i] = PY_ZE[i]
    global Z_SIDE
    Z_SIDE = PY_ZS

    for d in range(1, 64):
        for i in range(1, 64):
            LMR_TABLE[d][i] = int(0.75 + math.log(d) * math.log(i) / 2.25)


_init_tables()


# ---------------------------------------------------------------------------
# Быстрые C-атаки скользящих фигур (Classical Ray + CTZ/CLZ, без dict!)
# ---------------------------------------------------------------------------

cdef inline uint64_t bishop_attacks(int s, uint64_t occ) noexcept nogil:
    cdef uint64_t att = 0ULL
    cdef uint64_t b
    # NE (+9) -> LSB
    b = RAY_ATT[4][s] & occ
    att |= RAY_ATT[4][s] ^ (RAY_ATT[4][c_ctz64(b)] if b else 0ULL)
    # NW (+7) -> LSB
    b = RAY_ATT[5][s] & occ
    att |= RAY_ATT[5][s] ^ (RAY_ATT[5][c_ctz64(b)] if b else 0ULL)
    # SE (-7) -> MSB
    b = RAY_ATT[6][s] & occ
    att |= RAY_ATT[6][s] ^ (RAY_ATT[6][63 - c_clz64(b)] if b else 0ULL)
    # SW (-9) -> MSB
    b = RAY_ATT[7][s] & occ
    att |= RAY_ATT[7][s] ^ (RAY_ATT[7][63 - c_clz64(b)] if b else 0ULL)
    return att


cdef inline uint64_t rook_attacks(int s, uint64_t occ) noexcept nogil:
    cdef uint64_t att = 0ULL
    cdef uint64_t b
    # N (+8) -> LSB
    b = RAY_ATT[0][s] & occ
    att |= RAY_ATT[0][s] ^ (RAY_ATT[0][c_ctz64(b)] if b else 0ULL)
    # E (+1) -> LSB
    b = RAY_ATT[2][s] & occ
    att |= RAY_ATT[2][s] ^ (RAY_ATT[2][c_ctz64(b)] if b else 0ULL)
    # S (-8) -> MSB
    b = RAY_ATT[1][s] & occ
    att |= RAY_ATT[1][s] ^ (RAY_ATT[1][63 - c_clz64(b)] if b else 0ULL)
    # W (-1) -> MSB
    b = RAY_ATT[3][s] & occ
    att |= RAY_ATT[3][s] ^ (RAY_ATT[3][63 - c_clz64(b)] if b else 0ULL)
    return att


cdef inline int is_attacked(BoardState* st, int s, int by_side) noexcept nogil:
    cdef uint64_t occ = st.occ
    cdef uint64_t bq, rq
    if by_side == WHITE:
        if PAWN_ATT_B[s] & st.bb[WP]:
            return 1
        if KNIGHT_ATT[s] & st.bb[WN]:
            return 1
        if KING_ATT[s] & st.bb[WK]:
            return 1
        bq = st.bb[WB] | st.bb[WQ]
        if bq and (bishop_attacks(s, occ) & bq):
            return 1
        rq = st.bb[WR] | st.bb[WQ]
        if rq and (rook_attacks(s, occ) & rq):
            return 1
        return 0
    else:
        if PAWN_ATT_W[s] & st.bb[BP]:
            return 1
        if KNIGHT_ATT[s] & st.bb[BN]:
            return 1
        if KING_ATT[s] & st.bb[BK]:
            return 1
        bq = st.bb[BB] | st.bb[BQ]
        if bq and (bishop_attacks(s, occ) & bq):
            return 1
        rq = st.bb[BR] | st.bb[BQ]
        if rq and (rook_attacks(s, occ) & rq):
            return 1
        return 0


cdef inline int in_check(BoardState* st) noexcept nogil:
    if st.turn == WHITE:
        return is_attacked(st, st.wk_sq, BLACK)
    return is_attacked(st, st.bk_sq, WHITE)


cdef inline int make_move(BoardState* st, uint16_t move) noexcept nogil:
    cdef int f = move & 63
    cdef int t = (move >> 6) & 63
    cdef int fl = move >> 12
    cdef int pc = st.sq[f]
    cdef int cap = st.sq[t]
    cdef int us = st.turn
    cdef int old_c = st.castling
    cdef int old_ep = st.ep
    cdef int old_hm = st.halfmove
    cdef int mg = st.mg
    cdef int eg = st.eg
    cdef int phase = st.phase
    cdef uint64_t zhash = st.zhash
    cdef uint64_t f_bit = 1ULL << f
    cdef uint64_t t_bit = 1ULL << t
    cdef uint64_t cap_bit
    cdef int cap_sq, cap_pc, new_pc, rf, rt, rpc, new_c, new_ep

    cdef UndoEntry* u = &st.undo[st.sp]
    u.move = move
    u.cap = cap
    u.castling = old_c
    u.ep = old_ep
    u.halfmove = old_hm
    u.mg = mg
    u.eg = eg
    u.phase = phase
    u.zhash = zhash
    st.sp += 1

    zhash ^= Z_CASTLE[old_c] ^ Z_EP[old_ep if old_ep >= 0 else 64] ^ Z_SIDE

    st.sq[f] = EMPTY
    st.bb[pc] ^= f_bit
    mg -= INC_MG[pc][f]
    eg -= INC_EG[pc][f]
    zhash ^= Z_PIECE[pc][f]

    if fl == FLAG_EP:
        cap_sq = (t - 8) if us == WHITE else (t + 8)
        cap_pc = BP if us == WHITE else WP
        cap_bit = 1ULL << cap_sq
        st.sq[cap_sq] = EMPTY
        st.bb[cap_pc] ^= cap_bit
        if us == WHITE:
            st.occ_b ^= cap_bit
        else:
            st.occ_w ^= cap_bit
        mg -= INC_MG[cap_pc][cap_sq]
        eg -= INC_EG[cap_pc][cap_sq]
        zhash ^= Z_PIECE[cap_pc][cap_sq]
    elif cap != EMPTY:
        st.bb[cap] ^= t_bit
        if us == WHITE:
            st.occ_b ^= t_bit
        else:
            st.occ_w ^= t_bit
        mg -= INC_MG[cap][t]
        eg -= INC_EG[cap][t]
        phase -= PHASE_W[cap]
        zhash ^= Z_PIECE[cap][t]

    if fl & 8:
        new_pc = ((fl & 3) + 2) | us
        phase += PHASE_W[new_pc]
    else:
        new_pc = pc

    st.sq[t] = new_pc
    st.bb[new_pc] |= t_bit
    if us == WHITE:
        st.occ_w ^= (f_bit | t_bit)
        if new_pc == WK:
            st.wk_sq = t
    else:
        st.occ_b ^= (f_bit | t_bit)
        if new_pc == BK:
            st.bk_sq = t

    mg += INC_MG[new_pc][t]
    eg += INC_EG[new_pc][t]
    zhash ^= Z_PIECE[new_pc][t]

    if fl == FLAG_CASTLE_K:
        if us == WHITE:
            rf = 7; rt = 5; rpc = WR
            st.occ_w ^= (1ULL << 7) | (1ULL << 5)
        else:
            rf = 63; rt = 61; rpc = BR
            st.occ_b ^= (1ULL << 63) | (1ULL << 61)
        st.sq[rf] = EMPTY
        st.sq[rt] = rpc
        st.bb[rpc] ^= (1ULL << rf) | (1ULL << rt)
        mg += INC_MG[rpc][rt] - INC_MG[rpc][rf]
        eg += INC_EG[rpc][rt] - INC_EG[rpc][rf]
        zhash ^= Z_PIECE[rpc][rf] ^ Z_PIECE[rpc][rt]
    elif fl == FLAG_CASTLE_Q:
        if us == WHITE:
            rf = 0; rt = 3; rpc = WR
            st.occ_w ^= (1ULL << 0) | (1ULL << 3)
        else:
            rf = 56; rt = 59; rpc = BR
            st.occ_b ^= (1ULL << 56) | (1ULL << 59)
        st.sq[rf] = EMPTY
        st.sq[rt] = rpc
        st.bb[rpc] ^= (1ULL << rf) | (1ULL << rt)
        mg += INC_MG[rpc][rt] - INC_MG[rpc][rf]
        eg += INC_EG[rpc][rt] - INC_EG[rpc][rf]
        zhash ^= Z_PIECE[rpc][rf] ^ Z_PIECE[rpc][rt]

    st.occ = st.occ_w | st.occ_b
    new_c = old_c & CASTLE_MASK[f] & CASTLE_MASK[t]
    st.castling = new_c
    new_ep = ((f + t) >> 1) if fl == FLAG_DOUBLE else -1
    st.ep = new_ep
    st.halfmove = 0 if (cap != EMPTY or (pc & 7) == 1) else (old_hm + 1)
    st.mg = mg
    st.eg = eg
    st.phase = phase
    zhash ^= Z_CASTLE[new_c] ^ Z_EP[new_ep if new_ep >= 0 else 64]
    st.zhash = zhash
    st.turn = us ^ 8

    if us == WHITE:
        if is_attacked(st, st.wk_sq, BLACK):
            unmake_move(st)
            return 0
    else:
        if is_attacked(st, st.bk_sq, WHITE):
            unmake_move(st)
            return 0
    return 1


cdef inline void unmake_move(BoardState* st) noexcept nogil:
    st.sp -= 1
    cdef UndoEntry* u = &st.undo[st.sp]
    cdef uint16_t move = u.move
    cdef int cap = u.cap
    cdef int f = move & 63
    cdef int t = (move >> 6) & 63
    cdef int fl = move >> 12
    cdef int us = st.turn ^ 8
    cdef uint64_t f_bit = 1ULL << f
    cdef uint64_t t_bit = 1ULL << t
    cdef uint64_t cap_bit
    cdef int moved_pc = st.sq[t]
    cdef int orig_pc = (WP | us) if (fl & 8) else moved_pc
    cdef int cap_sq, cap_pc, rf, rt, rpc

    st.turn = us
    st.bb[moved_pc] ^= t_bit
    st.bb[orig_pc] |= f_bit
    st.sq[f] = orig_pc
    st.sq[t] = EMPTY

    if us == WHITE:
        st.occ_w ^= (f_bit | t_bit)
        if orig_pc == WK:
            st.wk_sq = f
    else:
        st.occ_b ^= (f_bit | t_bit)
        if orig_pc == BK:
            st.bk_sq = f

    if fl == FLAG_EP:
        cap_sq = (t - 8) if us == WHITE else (t + 8)
        cap_pc = BP if us == WHITE else WP
        cap_bit = 1ULL << cap_sq
        st.sq[cap_sq] = cap_pc
        st.bb[cap_pc] |= cap_bit
        if us == WHITE:
            st.occ_b |= cap_bit
        else:
            st.occ_w |= cap_bit
    elif cap != EMPTY:
        st.sq[t] = cap
        st.bb[cap] |= t_bit
        if us == WHITE:
            st.occ_b |= t_bit
        else:
            st.occ_w |= t_bit

    if fl == FLAG_CASTLE_K:
        if us == WHITE:
            rf = 7; rt = 5; rpc = WR
            st.occ_w ^= (1ULL << 7) | (1ULL << 5)
        else:
            rf = 63; rt = 61; rpc = BR
            st.occ_b ^= (1ULL << 63) | (1ULL << 61)
        st.sq[rt] = EMPTY
        st.sq[rf] = rpc
        st.bb[rpc] ^= (1ULL << rf) | (1ULL << rt)
    elif fl == FLAG_CASTLE_Q:
        if us == WHITE:
            rf = 0; rt = 3; rpc = WR
            st.occ_w ^= (1ULL << 0) | (1ULL << 3)
        else:
            rf = 56; rt = 59; rpc = BR
            st.occ_b ^= (1ULL << 56) | (1ULL << 59)
        st.sq[rt] = EMPTY
        st.sq[rf] = rpc
        st.bb[rpc] ^= (1ULL << rf) | (1ULL << rt)

    st.occ = st.occ_w | st.occ_b
    st.castling = u.castling
    st.ep = u.ep
    st.halfmove = u.halfmove
    st.mg = u.mg
    st.eg = u.eg
    st.phase = u.phase
    st.zhash = u.zhash


cdef inline void make_null(BoardState* st) noexcept nogil:
    cdef UndoEntry* u = &st.undo[st.sp]
    u.ep = st.ep
    u.zhash = st.zhash
    st.sp += 1
    st.zhash ^= Z_EP[st.ep if st.ep >= 0 else 64] ^ Z_EP[64] ^ Z_SIDE
    st.ep = -1
    st.turn ^= 8


cdef inline void unmake_null(BoardState* st) noexcept nogil:
    st.sp -= 1
    cdef UndoEntry* u = &st.undo[st.sp]
    st.turn ^= 8
    st.ep = u.ep
    st.zhash = u.zhash


cdef int gen_moves(BoardState* st, uint16_t* out, int caps_only) noexcept nogil:
    cdef int n = 0
    cdef uint64_t occ = st.occ
    cdef uint64_t empty = ~occ
    cdef uint64_t own, opp, target, pawns, cap_l, cap_r, ep_f, single, promos, qp, dbl, pcs, att
    cdef int f, t, base, ks, ep

    if st.turn == WHITE:
        own = st.occ_w
        opp = st.occ_b
        pawns = st.bb[WP]
        if pawns:
            cap_l = ((pawns & NOT_FILE_A) << 7) & opp
            while cap_l:
                t = c_ctz64(cap_l); cap_l &= cap_l - 1
                f = t - 7
                if t >= 56:
                    base = f | (t << 6)
                    out[n] = base | (FLAG_PROMO_CAP_Q << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_N << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_R << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_B << 12); n += 1
                else:
                    out[n] = f | (t << 6) | (FLAG_CAPTURE << 12); n += 1

            cap_r = ((pawns & NOT_FILE_H) << 9) & opp
            while cap_r:
                t = c_ctz64(cap_r); cap_r &= cap_r - 1
                f = t - 9
                if t >= 56:
                    base = f | (t << 6)
                    out[n] = base | (FLAG_PROMO_CAP_Q << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_N << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_R << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_B << 12); n += 1
                else:
                    out[n] = f | (t << 6) | (FLAG_CAPTURE << 12); n += 1

            ep = st.ep
            if ep >= 0:
                ep_f = PAWN_ATT_B[ep] & pawns
                while ep_f:
                    f = c_ctz64(ep_f); ep_f &= ep_f - 1
                    out[n] = f | (ep << 6) | (FLAG_EP << 12); n += 1

            single = (pawns << 8) & empty
            promos = single & RANK_8
            while promos:
                t = c_ctz64(promos); promos &= promos - 1
                base = (t - 8) | (t << 6)
                out[n] = base | (FLAG_PROMO_Q << 12); n += 1
                if not caps_only:
                    out[n] = base | (FLAG_PROMO_N << 12); n += 1
                    out[n] = base | (FLAG_PROMO_R << 12); n += 1
                    out[n] = base | (FLAG_PROMO_B << 12); n += 1

            if not caps_only:
                qp = single & ~RANK_8
                dbl = ((qp & RANK_3) << 8) & empty
                while qp:
                    t = c_ctz64(qp); qp &= qp - 1
                    out[n] = (t - 8) | (t << 6); n += 1
                while dbl:
                    t = c_ctz64(dbl); dbl &= dbl - 1
                    out[n] = (t - 16) | (t << 6) | (FLAG_DOUBLE << 12); n += 1

        target = opp if caps_only else ~own

        pcs = st.bb[WN]
        while pcs:
            f = c_ctz64(pcs); pcs &= pcs - 1
            att = KNIGHT_ATT[f] & target
            while att:
                t = c_ctz64(att); att &= att - 1
                out[n] = f | (t << 6) | ((FLAG_CAPTURE if ((1ULL << t) & opp) else FLAG_QUIET) << 12); n += 1

        pcs = st.bb[WB] | st.bb[WQ]
        while pcs:
            f = c_ctz64(pcs); pcs &= pcs - 1
            att = bishop_attacks(f, occ) & target
            while att:
                t = c_ctz64(att); att &= att - 1
                out[n] = f | (t << 6) | ((FLAG_CAPTURE if ((1ULL << t) & opp) else FLAG_QUIET) << 12); n += 1

        pcs = st.bb[WR] | st.bb[WQ]
        while pcs:
            f = c_ctz64(pcs); pcs &= pcs - 1
            att = rook_attacks(f, occ) & target
            while att:
                t = c_ctz64(att); att &= att - 1
                out[n] = f | (t << 6) | ((FLAG_CAPTURE if ((1ULL << t) & opp) else FLAG_QUIET) << 12); n += 1

        ks = st.wk_sq
        att = KING_ATT[ks] & target
        while att:
            t = c_ctz64(att); att &= att - 1
            out[n] = ks | (t << 6) | ((FLAG_CAPTURE if ((1ULL << t) & opp) else FLAG_QUIET) << 12); n += 1

        if not caps_only and (st.castling & (CASTLE_WK | CASTLE_WQ)) and st.wk_sq == 4:
            if (st.castling & CASTLE_WK) and st.sq[7] == WR and not (occ & 0x60ULL):
                if not is_attacked(st, 4, BLACK) and not is_attacked(st, 5, BLACK) and not is_attacked(st, 6, BLACK):
                    out[n] = 4 | (6 << 6) | (FLAG_CASTLE_K << 12); n += 1
            if (st.castling & CASTLE_WQ) and st.sq[0] == WR and not (occ & 0x0EULL):
                if not is_attacked(st, 4, BLACK) and not is_attacked(st, 3, BLACK) and not is_attacked(st, 2, BLACK):
                    out[n] = 4 | (2 << 6) | (FLAG_CASTLE_Q << 12); n += 1
    else:
        own = st.occ_b
        opp = st.occ_w
        pawns = st.bb[BP]
        if pawns:
            cap_r = ((pawns & NOT_FILE_H) >> 7) & opp
            while cap_r:
                t = c_ctz64(cap_r); cap_r &= cap_r - 1
                f = t + 7
                if t <= 7:
                    base = f | (t << 6)
                    out[n] = base | (FLAG_PROMO_CAP_Q << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_N << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_R << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_B << 12); n += 1
                else:
                    out[n] = f | (t << 6) | (FLAG_CAPTURE << 12); n += 1

            cap_l = ((pawns & NOT_FILE_A) >> 9) & opp
            while cap_l:
                t = c_ctz64(cap_l); cap_l &= cap_l - 1
                f = t + 9
                if t <= 7:
                    base = f | (t << 6)
                    out[n] = base | (FLAG_PROMO_CAP_Q << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_N << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_R << 12); n += 1
                    out[n] = base | (FLAG_PROMO_CAP_B << 12); n += 1
                else:
                    out[n] = f | (t << 6) | (FLAG_CAPTURE << 12); n += 1

            ep = st.ep
            if ep >= 0:
                ep_f = PAWN_ATT_W[ep] & pawns
                while ep_f:
                    f = c_ctz64(ep_f); ep_f &= ep_f - 1
                    out[n] = f | (ep << 6) | (FLAG_EP << 12); n += 1

            single = (pawns >> 8) & empty
            promos = single & RANK_1
            while promos:
                t = c_ctz64(promos); promos &= promos - 1
                base = (t + 8) | (t << 6)
                out[n] = base | (FLAG_PROMO_Q << 12); n += 1
                if not caps_only:
                    out[n] = base | (FLAG_PROMO_N << 12); n += 1
                    out[n] = base | (FLAG_PROMO_R << 12); n += 1
                    out[n] = base | (FLAG_PROMO_B << 12); n += 1

            if not caps_only:
                qp = single & ~RANK_1
                dbl = ((qp & RANK_6) >> 8) & empty
                while qp:
                    t = c_ctz64(qp); qp &= qp - 1
                    out[n] = (t + 8) | (t << 6); n += 1
                while dbl:
                    t = c_ctz64(dbl); dbl &= dbl - 1
                    out[n] = (t + 16) | (t << 6) | (FLAG_DOUBLE << 12); n += 1

        target = opp if caps_only else ~own

        pcs = st.bb[BN]
        while pcs:
            f = c_ctz64(pcs); pcs &= pcs - 1
            att = KNIGHT_ATT[f] & target
            while att:
                t = c_ctz64(att); att &= att - 1
                out[n] = f | (t << 6) | ((FLAG_CAPTURE if ((1ULL << t) & opp) else FLAG_QUIET) << 12); n += 1

        pcs = st.bb[BB] | st.bb[BQ]
        while pcs:
            f = c_ctz64(pcs); pcs &= pcs - 1
            att = bishop_attacks(f, occ) & target
            while att:
                t = c_ctz64(att); att &= att - 1
                out[n] = f | (t << 6) | ((FLAG_CAPTURE if ((1ULL << t) & opp) else FLAG_QUIET) << 12); n += 1

        pcs = st.bb[BR] | st.bb[BQ]
        while pcs:
            f = c_ctz64(pcs); pcs &= pcs - 1
            att = rook_attacks(f, occ) & target
            while att:
                t = c_ctz64(att); att &= att - 1
                out[n] = f | (t << 6) | ((FLAG_CAPTURE if ((1ULL << t) & opp) else FLAG_QUIET) << 12); n += 1

        ks = st.bk_sq
        att = KING_ATT[ks] & target
        while att:
            t = c_ctz64(att); att &= att - 1
            out[n] = ks | (t << 6) | ((FLAG_CAPTURE if ((1ULL << t) & opp) else FLAG_QUIET) << 12); n += 1

        if not caps_only and (st.castling & (CASTLE_BK | CASTLE_BQ)) and st.bk_sq == 60:
            if (st.castling & CASTLE_BK) and st.sq[63] == BR and not (occ & 0x6000000000000000ULL):
                if not is_attacked(st, 60, WHITE) and not is_attacked(st, 61, WHITE) and not is_attacked(st, 62, WHITE):
                    out[n] = 60 | (62 << 6) | (FLAG_CASTLE_K << 12); n += 1
            if (st.castling & CASTLE_BQ) and st.sq[56] == BR and not (occ & 0x0E00000000000000ULL):
                if not is_attacked(st, 60, WHITE) and not is_attacked(st, 59, WHITE) and not is_attacked(st, 58, WHITE):
                    out[n] = 60 | (58 << 6) | (FLAG_CASTLE_Q << 12); n += 1

    return n


# ---------------------------------------------------------------------------
# Быстрый C-SEE (полный swap-off с рентгеном)
# ---------------------------------------------------------------------------

cdef inline int c_lva(BoardState* st, int side, int sq, uint64_t occ, int* out_pt) noexcept nogil:
    cdef uint64_t att, diag, orth
    if side == WHITE:
        att = PAWN_ATT_B[sq] & st.bb[WP] & occ
        if att:
            out_pt[0] = 1; return c_ctz64(att)
        att = KNIGHT_ATT[sq] & st.bb[WN] & occ
        if att:
            out_pt[0] = 2; return c_ctz64(att)
        diag = bishop_attacks(sq, occ)
        att = diag & st.bb[WB] & occ
        if att:
            out_pt[0] = 3; return c_ctz64(att)
        orth = rook_attacks(sq, occ)
        att = orth & st.bb[WR] & occ
        if att:
            out_pt[0] = 4; return c_ctz64(att)
        att = (diag | orth) & st.bb[WQ] & occ
        if att:
            out_pt[0] = 5; return c_ctz64(att)
        att = KING_ATT[sq] & st.bb[WK] & occ
        if att:
            out_pt[0] = 6; return c_ctz64(att)
        return -1
    else:
        att = PAWN_ATT_W[sq] & st.bb[BP] & occ
        if att:
            out_pt[0] = 1; return c_ctz64(att)
        att = KNIGHT_ATT[sq] & st.bb[BN] & occ
        if att:
            out_pt[0] = 2; return c_ctz64(att)
        diag = bishop_attacks(sq, occ)
        att = diag & st.bb[BB] & occ
        if att:
            out_pt[0] = 3; return c_ctz64(att)
        orth = rook_attacks(sq, occ)
        att = orth & st.bb[BR] & occ
        if att:
            out_pt[0] = 4; return c_ctz64(att)
        att = (diag | orth) & st.bb[BQ] & occ
        if att:
            out_pt[0] = 5; return c_ctz64(att)
        att = KING_ATT[sq] & st.bb[BK] & occ
        if att:
            out_pt[0] = 6; return c_ctz64(att)
        return -1


cdef int c_see(BoardState* st, uint16_t move) noexcept nogil:
    cdef int f = move & 63
    cdef int t = (move >> 6) & 63
    cdef int fl = move >> 12
    cdef int a_pc = st.sq[f]
    if a_pc == EMPTY:
        return 0
    cdef uint64_t occ = st.occ & ~(1ULL << f)
    cdef int gain, on_sq, cap_sq, v_pc, side, depth, sq, pt, cap_v, dummy_pt, i
    cdef int gains[32]

    if fl == FLAG_EP:
        cap_sq = (t - 8) if st.turn == WHITE else (t + 8)
        occ &= ~(1ULL << cap_sq)
        gain = 100
    else:
        v_pc = st.sq[t]
        gain = PIECE_VAL[v_pc & 7] if v_pc != EMPTY else 0

    occ |= (1ULL << t)
    if fl & 8:
        on_sq = PIECE_VAL[(fl & 3) + 2]
        gain += on_sq - 100
    else:
        on_sq = PIECE_VAL[a_pc & 7]

    gains[0] = gain
    depth = 1
    side = st.turn ^ 8
    cdef int t_promo = 1 if ((1ULL << t) & PROMO_RANKS) else 0

    while depth < 32:
        sq = c_lva(st, side, t, occ, &pt)
        if sq < 0:
            break
        cap_v = on_sq
        on_sq = PIECE_VAL[pt]
        if pt == 1 and t_promo:
            cap_v += 800
            on_sq = 900
        gains[depth] = cap_v - gains[depth - 1]
        occ &= ~(1ULL << sq)
        side ^= 8
        if pt == 6 and c_lva(st, side, t, occ, &dummy_pt) >= 0:
            break
        depth += 1

    for i in range(depth - 1, 0, -1):
        if -gains[i - 1] < gains[i]:
            gains[i - 1] = -gains[i]
    return gains[0]


# ---------------------------------------------------------------------------
# Быстрая C-оценка (PeSTO + фирменный крепостной стиль PyCaesar + C-пешечный хэш)
# ---------------------------------------------------------------------------

cdef int c_evaluate(BoardState* st, PawnEntry* ptable) noexcept nogil:
    cdef uint64_t wp = st.bb[WP], bp = st.bb[BP]
    cdef uint64_t wn = st.bb[WN], wb = st.bb[WB], wr = st.bb[WR], wq = st.bb[WQ]
    cdef uint64_t bn = st.bb[BN], bb_b = st.bb[BB], br = st.bb[BR], bq = st.bb[BQ]
    cdef uint64_t occ = st.occ

    if not (wp | bp | wr | br | wq | bq):
        if c_popcnt64(wn | wb | bn | bb_b) <= 1:
            return 0

    cdef int phase = st.phase
    if phase > MAX_PHASE:
        phase = MAX_PHASE
    cdef int mg = st.mg
    cdef int eg = st.eg

    # 1. Пешечный хэш
    cdef uint64_t passed_w = 0ULL, passed_b = 0ULL
    cdef uint64_t h, x
    cdef PawnEntry* pe
    cdef int wc[8]
    cdef int bc[8]
    cdef int s_mg, s_eg, p_mg, p_eg, sq, f, r, has_n, i

    if wp | bp:
        h = (wp * 0x9E3779B97F4A7C15ULL) ^ (bp * 0xBF58476D1CE4E5B9ULL)
        pe = &ptable[(h >> 48) & PAWN_HASH_MASK]
        if pe.wp == wp and pe.bp == bp:
            mg += pe.mg
            eg += pe.eg
            passed_w = pe.passed_w
            passed_b = pe.passed_b
        else:
            s_mg = 0; s_eg = 0; p_mg = 0; p_eg = 0
            for i in range(8):
                wc[i] = c_popcnt64(wp & BB_FILES[i])
                bc[i] = c_popcnt64(bp & BB_FILES[i])
            x = wp
            while x:
                sq = c_ctz64(x); x &= x - 1
                f = sq & 7
                if wc[f] > 1:
                    s_mg -= 12; s_eg -= 20
                has_n = 1 if (wp & ADJ_FILES[f]) else 0
                if not has_n:
                    s_mg -= 15; s_eg -= 18
                if not (PASSED_W[sq] & bp):
                    r = sq >> 3
                    p_mg += PASSED_MG[r]; p_eg += PASSED_EG[r]
                    passed_w |= (1ULL << sq)
                if PAWN_ATT_B[sq] & wp:
                    s_mg += 10; s_eg += 8
                elif not has_n:
                    s_mg -= 10; s_eg -= 8
            x = bp
            while x:
                sq = c_ctz64(x); x &= x - 1
                f = sq & 7
                if bc[f] > 1:
                    s_mg += 12; s_eg += 20
                has_n = 1 if (bp & ADJ_FILES[f]) else 0
                if not has_n:
                    s_mg += 15; s_eg += 18
                if not (PASSED_B[sq] & wp):
                    r = 7 - (sq >> 3)
                    p_mg -= PASSED_MG[r]; p_eg -= PASSED_EG[r]
                    passed_b |= (1ULL << sq)
                if PAWN_ATT_W[sq] & bp:
                    s_mg -= 10; s_eg -= 8
                elif not has_n:
                    s_mg += 10; s_eg += 8
            pe.wp = wp
            pe.bp = bp
            pe.passed_w = passed_w
            pe.passed_b = passed_b
            pe.mg = <int16_t>((s_mg * 13) // 10 + (p_mg * 11) // 10)
            pe.eg = <int16_t>((s_eg * 13) // 10 + (p_eg * 11) // 10)
            mg += pe.mg
            eg += pe.eg

    # 2. Незаблокированные проходные
    if passed_w:
        eg += c_popcnt64((passed_w << 8) & ~occ) * 20
    if passed_b:
        eg -= c_popcnt64((passed_b >> 8) & ~occ) * 20

    # 3. Угрозы пешками
    cdef uint64_t w_pa = ((wp & NOT_FILE_A) << 7) | ((wp & NOT_FILE_H) << 9)
    cdef uint64_t b_pa = ((bp & NOT_FILE_H) >> 7) | ((bp & NOT_FILE_A) >> 9)
    cdef int thr
    if w_pa:
        thr = c_popcnt64(w_pa & (bn | bb_b)) * 25 + c_popcnt64(w_pa & br) * 40 + c_popcnt64(w_pa & bq) * 55
        mg += thr; eg += thr >> 1
    if b_pa:
        thr = c_popcnt64(b_pa & (wn | wb)) * 25 + c_popcnt64(b_pa & wr) * 40 + c_popcnt64(b_pa & wq) * 55
        mg -= thr; eg -= thr >> 1

    # 4. Форпосты
    cdef int out = 0
    x = wn & OUTPOST_ZONE_W & w_pa
    while x:
        sq = c_ctz64(x); x &= x - 1
        if not (OUTPOST_ENEMY_W[sq] & bp): out += 34
    x = bn & OUTPOST_ZONE_B & b_pa
    while x:
        sq = c_ctz64(x); x &= x - 1
        if not (OUTPOST_ENEMY_B[sq] & wp): out -= 34
    x = wb & OUTPOST_ZONE_W & w_pa
    while x:
        sq = c_ctz64(x); x &= x - 1
        if not (OUTPOST_ENEMY_W[sq] & bp): out += 20
    x = bb_b & OUTPOST_ZONE_B & b_pa
    while x:
        sq = c_ctz64(x); x &= x - 1
        if not (OUTPOST_ENEMY_B[sq] & wp): out -= 20
    if out:
        mg += out; eg += out >> 1

    # 5. Пара слонов
    if wb and (wb & (wb - 1)):
        mg += 33; eg += 55
    if bb_b and (bb_b & (bb_b - 1)):
        mg -= 33; eg -= 55

    # 6. Дебютная дисциплина
    cdef int w_un, b_un, dev
    if phase >= 18:
        w_un = c_popcnt64((wn | wb) & W_START_MINORS)
        b_un = c_popcnt64((bn | bb_b) & B_START_MINORS)
        dev = (b_un - w_un) * 9
        if w_un >= 2 and wq and not (wq & (1ULL << 3)):
            dev -= 20
        if b_un >= 2 and bq and not (bq & (1ULL << 59)):
            dev += 20
        mg += dev

    # 7. Безопасная мобильность + ладьи + атаки на зону короля
    cdef int wk = st.wk_sq, bk = st.bk_sq
    cdef uint64_t safe_w = ~(st.occ_w | b_pa)
    cdef uint64_t safe_b = ~(st.occ_b | w_pa)
    cdef int do_ks = 1 if phase >= 7 else 0
    cdef uint64_t w_kz = KING_ATT[wk] if do_ks else 0ULL
    cdef uint64_t b_kz = KING_ATT[bk] if do_ks else 0ULL
    cdef int w_aw = 0, b_aw = 0, mob = 0
    cdef uint64_t att, fm
    cdef int bk_r7 = 1 if ((bk >> 3) == 7 or (bp & RANK_7)) else 0
    cdef int wk_r0 = 1 if ((wk >> 3) == 0 or (wp & RANK_2)) else 0

    x = wn
    while x:
        sq = c_ctz64(x); x &= x - 1
        att = KNIGHT_ATT[sq]
        mob += c_popcnt64(att & safe_w) * 4
        if b_kz and (att & b_kz): b_aw += 20
    x = bn
    while x:
        sq = c_ctz64(x); x &= x - 1
        att = KNIGHT_ATT[sq]
        mob -= c_popcnt64(att & safe_b) * 4
        if w_kz and (att & w_kz): w_aw += 20

    x = wb
    while x:
        sq = c_ctz64(x); x &= x - 1
        att = bishop_attacks(sq, occ)
        mob += c_popcnt64(att & safe_w) * 4
        if b_kz and (att & b_kz): b_aw += 20
    x = bb_b
    while x:
        sq = c_ctz64(x); x &= x - 1
        att = bishop_attacks(sq, occ)
        mob -= c_popcnt64(att & safe_b) * 4
        if w_kz and (att & w_kz): w_aw += 20

    x = wr
    while x:
        sq = c_ctz64(x); x &= x - 1
        att = rook_attacks(sq, occ)
        mob += c_popcnt64(att & safe_w) * 2
        if b_kz and (att & b_kz): b_aw += 40
        fm = BB_FILES[sq & 7]
        if not (wp & fm):
            if not (bp & fm): mg += 28; eg += 14
            else: mg += 14; eg += 7
        if (sq >> 3) == 6 and bk_r7:
            mg += 22; eg += 36
    x = br
    while x:
        sq = c_ctz64(x); x &= x - 1
        att = rook_attacks(sq, occ)
        mob -= c_popcnt64(att & safe_b) * 2
        if w_kz and (att & w_kz): w_aw += 40
        fm = BB_FILES[sq & 7]
        if not (bp & fm):
            if not (wp & fm): mg -= 28; eg -= 14
            else: mg -= 14; eg -= 7
        if (sq >> 3) == 1 and wk_r0:
            mg -= 22; eg -= 36

    x = wq
    while x:
        sq = c_ctz64(x); x &= x - 1
        att = bishop_attacks(sq, occ) | rook_attacks(sq, occ)
        mob += c_popcnt64(att & safe_w)
        if b_kz and (att & b_kz): b_aw += 80
    x = bq
    while x:
        sq = c_ctz64(x); x &= x - 1
        att = bishop_attacks(sq, occ) | rook_attacks(sq, occ)
        mob -= c_popcnt64(att & safe_b)
        if w_kz and (att & w_kz): w_aw += 80

    mob = (mob * 95) // 100
    mg += mob; eg += mob

    # 8. Безопасность короля (фирменная крепость)
    cdef int ks, att_pen
    if do_ks:
        ks = 0
        for i in range(SHIELD_CNT[wk]):
            if SHIELD_W[wk][i] & wp: ks += 27
        for i in range(KING_FILE_CNT[wk]):
            fm = BB_FILES[KING_FILES[wk][i]]
            if not (wp & fm):
                ks -= 45 if not (bp & fm) else 22
        if w_aw:
            att_pen = (w_aw * w_aw) // 110
            if att_pen > 350: att_pen = 350
            ks -= (att_pen * 85) // 100

        for i in range(SHIELD_CNT[bk]):
            if SHIELD_B[bk][i] & bp: ks -= 27
        for i in range(KING_FILE_CNT[bk]):
            fm = BB_FILES[KING_FILES[bk][i]]
            if not (bp & fm):
                ks += 45 if not (wp & fm) else 22
        if b_aw:
            att_pen = (b_aw * b_aw) // 110
            if att_pen > 350: att_pen = 350
            ks += (att_pen * 85) // 100
        mg += ks

    # 9. Эндшпильный mop-up
    cdef int kd, df, dr
    if phase <= 8 and (eg >= 350 or eg <= -350):
        df = (wk & 7) - (bk & 7)
        if df < 0: df = -df
        dr = (wk >> 3) - (bk >> 3)
        if dr < 0: dr = -dr
        kd = df + dr
        if eg > 0:
            eg += CENTER_MANHATTAN[bk] * 10 + (14 - kd) * 4
        else:
            eg -= CENTER_MANHATTAN[wk] * 10 + (14 - kd) * 4

    cdef int score = (mg * phase + eg * (MAX_PHASE - phase)) // MAX_PHASE
    if st.turn == WHITE:
        return score + TEMPO_BONUS
    return -score + TEMPO_BONUS


# ---------------------------------------------------------------------------
# Быстрый C-класс поиска
# ---------------------------------------------------------------------------

cdef inline int score_to_tt(int score, int ply) noexcept nogil:
    if score > MATE_SCORE - 1000:
        score += ply
        if score > INFINITY: score = INFINITY
    elif score < -MATE_SCORE + 1000:
        score -= ply
        if score < -INFINITY: score = -INFINITY
    return score


cdef inline int score_from_tt(int score, int ply) noexcept nogil:
    if score > MATE_SCORE - 1000:
        score -= ply
        if score < MATE_SCORE - 999: score = MATE_SCORE - 999
    elif score < -MATE_SCORE + 1000:
        score += ply
        if score > -MATE_SCORE + 999: score = -MATE_SCORE + 999
    return score


cdef class CythonSearcher:
    cdef TTEntry* tt
    cdef uint64_t tt_mask
    cdef uint64_t tt_size
    cdef PawnEntry* ptable
    cdef uint64_t rep_history[2048]
    cdef int rep_len
    cdef uint16_t killers[MAX_PLY][2]
    cdef uint16_t counter_moves[64][64]
    cdef int history[2][64][64]
    cdef int cap_history[7][64][7]
    cdef uint16_t excluded_root[64]
    cdef int excluded_cnt
    cdef public uint64_t nodes
    cdef double start_time
    cdef double time_limit
    cdef public bint stopped
    cdef int root_turn
    cdef uint16_t best_move_root
    cdef int qs_max_depth

    def __cinit__(self, int hash_mb=64):
        self.tt = NULL
        self.ptable = <PawnEntry*>malloc(PAWN_HASH_SIZE * sizeof(PawnEntry))
        memset(self.ptable, 0, PAWN_HASH_SIZE * sizeof(PawnEntry))
        self.set_hash_mb(hash_mb)
        self.clear_heuristics()
        self.qs_max_depth = 8
        self.stopped = False

    def __dealloc__(self):
        if self.tt != NULL:
            free(self.tt)
            self.tt = NULL
        if self.ptable != NULL:
            free(self.ptable)
            self.ptable = NULL

    def set_hash_mb(self, int mb):
        if mb < 4: mb = 4
        if mb > 1024: mb = 1024
        cdef uint64_t bytes_n = (<uint64_t>mb) * 1024ULL * 1024ULL
        cdef uint64_t entries = bytes_n // sizeof(TTEntry)
        cdef uint64_t p2 = 1ULL
        while (p2 << 1) <= entries:
            p2 <<= 1
        if self.tt != NULL:
            free(self.tt)
        self.tt_size = p2
        self.tt_mask = p2 - 1ULL
        self.tt = <TTEntry*>malloc(p2 * sizeof(TTEntry))
        memset(self.tt, 0, p2 * sizeof(TTEntry))

    def clear_tt(self):
        if self.tt != NULL:
            memset(self.tt, 0, self.tt_size * sizeof(TTEntry))
        if self.ptable != NULL:
            memset(self.ptable, 0, PAWN_HASH_SIZE * sizeof(PawnEntry))

    def clear_heuristics(self):
        memset(self.killers, 0, sizeof(self.killers))
        memset(self.counter_moves, 0, sizeof(self.counter_moves))
        memset(self.history, 0, sizeof(self.history))
        memset(self.cap_history, 0, sizeof(self.cap_history))

    def stop(self):
        self.stopped = True

    cdef inline bint _check_time(self) noexcept nogil:
        if self.stopped:
            return True
        if c_now_sec() - self.start_time >= self.time_limit:
            self.stopped = True
            return True
        return False

    cdef inline int _is_repetition(self, uint64_t key, int halfmove) noexcept nogil:
        cdef int i = self.rep_len - 2
        cdef int stop_i = self.rep_len - halfmove
        if stop_i < 0: stop_i = 0
        while i >= stop_i:
            if self.rep_history[i] == key:
                return 1
            i -= 2
        return 0

    cdef inline void _scale_history(self) noexcept nogil:
        cdef int c, f, t
        for c in range(2):
            for f in range(64):
                for t in range(64):
                    self.history[c][f][t] >>= 1

    cdef inline void _scale_cap_history(self) noexcept nogil:
        cdef int a, sq, v
        for a in range(7):
            for sq in range(64):
                for v in range(7):
                    self.cap_history[a][sq][v] >>= 1

    cdef inline void _score_moves(self, BoardState* st, uint16_t* moves, int* scores, int n, int ply, uint16_t tt_move, uint16_t prev_move) noexcept nogil:
        cdef int i, fl, f, t, a_pt, v_pt, v_val, a_val, mvv, ch, see_v
        cdef int ply_c = ply if ply < MAX_PLY else (MAX_PLY - 1)
        cdef uint16_t k0 = self.killers[ply_c][0]
        cdef uint16_t k1 = self.killers[ply_c][1]
        cdef uint16_t cm = self.counter_moves[prev_move & 63][(prev_move >> 6) & 63] if prev_move else 0
        cdef int c_idx = 0 if st.turn == WHITE else 1
        cdef uint16_t m

        for i in range(n):
            m = moves[i]
            if m == tt_move:
                scores[i] = 10000000
                continue
            fl = m >> 12
            if fl == FLAG_PROMO_Q or fl == FLAG_PROMO_CAP_Q:
                scores[i] = 9000000
            elif fl & 4:
                f = m & 63
                t = (m >> 6) & 63
                a_pt = st.sq[f] & 7
                v_pt = 1 if fl == FLAG_EP else (st.sq[t] & 7)
                v_val = PIECE_VAL[v_pt]
                a_val = PIECE_VAL[a_pt]
                mvv = v_val * 10 - a_val
                ch = self.cap_history[a_pt][t][v_pt]
                if v_val >= a_val:
                    scores[i] = 1000000 + mvv + (ch >> 3)
                else:
                    see_v = c_see(st, m)
                    if see_v >= 0:
                        scores[i] = 1000000 + mvv + (ch >> 3)
                    else:
                        scores[i] = -500000 + see_v + (ch >> 4)
            elif m == k0:
                scores[i] = 900000
            elif m == k1:
                scores[i] = 800000
            elif m == cm:
                scores[i] = 750000
            else:
                scores[i] = self.history[c_idx][m & 63][(m >> 6) & 63]

    cdef inline uint16_t _pick_best(self, uint16_t* moves, int* scores, int n, int start) noexcept nogil:
        cdef int best_i = start
        cdef int best_s = scores[start]
        cdef int i
        for i in range(start + 1, n):
            if scores[i] > best_s:
                best_s = scores[i]
                best_i = i
        cdef uint16_t tm = moves[start]
        moves[start] = moves[best_i]
        moves[best_i] = tm
        cdef int ts = scores[start]
        scores[start] = scores[best_i]
        scores[best_i] = ts
        return moves[start]

    cdef int _qsearch(self, BoardState* st, int alpha, int beta, int qs_depth, int ply) noexcept nogil:
        self.nodes += 1
        if (self.nodes & 2047) == 0:
            if self._check_time():
                return 0

        cdef uint16_t moves[MAX_MOVES]
        cdef int scores[MAX_MOVES]
        cdef int n, i, best_score, score, legal_any, stand_pat, fl, a_pt, v_pt, v_val, a_val, see_v
        cdef uint16_t m

        if in_check(st):
            if qs_depth < self.qs_max_depth and ply < MAX_PLY - 1:
                n = gen_moves(st, moves, 0)
                self._score_moves(st, moves, scores, n, ply, 0, 0)
                best_score = -INFINITY
                legal_any = 0
                for i in range(n):
                    m = self._pick_best(moves, scores, n, i)
                    if not make_move(st, m):
                        continue
                    legal_any = 1
                    score = -self._qsearch(st, -beta, -alpha, qs_depth + 1, ply + 1)
                    unmake_move(st)
                    if self.stopped: return 0
                    if score > best_score: best_score = score
                    if score > alpha: alpha = score
                    if alpha >= beta: break
                if not legal_any:
                    return -INFINITY + ply
                return best_score
            n = gen_moves(st, moves, 0)
            for i in range(n):
                if make_move(st, moves[i]):
                    unmake_move(st)
                    return c_evaluate(st, self.ptable)
            return -INFINITY + ply

        stand_pat = c_evaluate(st, self.ptable)
        if stand_pat >= beta:
            return beta
        if qs_depth > 0 and stand_pat + 1100 < alpha:
            return alpha
        if stand_pat > alpha:
            alpha = stand_pat
        if qs_depth >= self.qs_max_depth or ply >= MAX_PLY - 1:
            return stand_pat

        n = gen_moves(st, moves, 1)
        if n == 0:
            return alpha

        cdef int kept = 0
        for i in range(n):
            m = moves[i]
            fl = m >> 12
            if fl == FLAG_PROMO_Q or fl == FLAG_PROMO_CAP_Q:
                moves[kept] = m; scores[kept] = 9000000; kept += 1
                continue
            a_pt = st.sq[m & 63] & 7
            v_pt = 1 if fl == FLAG_EP else (st.sq[(m >> 6) & 63] & 7)
            v_val = PIECE_VAL[v_pt]
            a_val = PIECE_VAL[a_pt]
            if v_val >= a_val:
                moves[kept] = m; scores[kept] = 100000 + v_val * 10 - a_val; kept += 1
            else:
                see_v = c_see(st, m)
                if qs_depth > 0 and see_v < 0:
                    continue
                moves[kept] = m; scores[kept] = see_v * 100 + v_val * 10 - a_val; kept += 1

        for i in range(kept):
            m = self._pick_best(moves, scores, kept, i)
            if not make_move(st, m):
                continue
            score = -self._qsearch(st, -beta, -alpha, qs_depth + 1, ply + 1)
            unmake_move(st)
            if self.stopped: return 0
            if score >= beta: return beta
            if score > alpha: alpha = score
        return alpha

    cdef int _negamax(self, BoardState* st, int depth, int alpha, int beta, int ply, uint16_t prev_move) noexcept nogil:
        self.nodes += 1
        if (self.nodes & 2047) == 0:
            if self._check_time():
                return 0

        cdef uint64_t zhash = st.zhash
        if ply > 0:
            if st.halfmove >= 100 or self._is_repetition(zhash, st.halfmove):
                return -CONTEMPT if st.turn == self.root_turn else CONTEMPT
            if not (st.bb[WP] | st.bb[BP] | st.bb[WR] | st.bb[BR] | st.bb[WQ] | st.bb[BQ]):
                if c_popcnt64(st.bb[WN] | st.bb[WB] | st.bb[BN] | st.bb[BB]) <= 1:
                    return 0

        if ply >= MAX_PLY - 1:
            return c_evaluate(st, self.ptable)

        cdef int is_in_check = in_check(st)
        cdef int is_pv = 1 if (beta - alpha) > 1 else 0

        # TT probe
        cdef TTEntry* tte = &self.tt[zhash & self.tt_mask]
        cdef uint16_t tt_move = 0
        cdef int tt_score
        if tte.key == zhash and tte.flag != TT_NONE:
            tt_move = tte.move
            if tte.depth >= depth and ply > 0:
                tt_score = score_from_tt(tte.score, ply)
                if tte.flag == TT_EXACT:
                    return tt_score
                elif tte.flag == TT_ALPHA and tt_score <= alpha:
                    return tt_score
                elif tte.flag == TT_BETA and tt_score >= beta:
                    return tt_score

        if depth <= 0:
            return self._qsearch(st, alpha, beta, 0, ply)

        # IIR
        if depth >= 4 and tt_move == 0 and ply > 0 and not is_in_check:
            depth -= 1

        cdef int has_static = 0
        cdef int static_eval = 0

        # Reverse Futility Pruning (depth <= 3, безопасный запас 130*d)
        if not is_in_check and not is_pv and ply > 0 and depth <= 3 and beta < TB_WIN - 1000 and beta > -TB_WIN + 1000:
            static_eval = c_evaluate(st, self.ptable)
            has_static = 1
            if static_eval - 130 * depth >= beta:
                return static_eval
            if depth == 1 and alpha < TB_WIN - 1000 and alpha > -TB_WIN + 1000 and static_eval + 300 <= alpha:
                return self._qsearch(st, alpha, beta, 0, ply)

        cdef int pushed_rep = 0
        if self.rep_len < 2048:
            self.rep_history[self.rep_len] = zhash
            self.rep_len += 1
            pushed_rep = 1

        # Null Move Pruning
        cdef int R, null_score
        cdef uint64_t non_pawn = (st.bb[WN] | st.bb[WB] | st.bb[WR] | st.bb[WQ]) if st.turn == WHITE else (st.bb[BN] | st.bb[BB] | st.bb[BR] | st.bb[BQ])
        if not is_in_check and not is_pv and ply > 0 and depth >= 3 and non_pawn and st.ep < 0:
            if not has_static:
                static_eval = c_evaluate(st, self.ptable)
                has_static = 1
            if static_eval >= beta:
                R = 3 if depth >= 6 else 2
                make_null(st)
                null_score = -self._negamax(st, depth - 1 - R, -beta, -beta + 1, ply + 1, 0)
                unmake_null(st)
                if self.stopped:
                    self.rep_len -= pushed_rep
                    return 0
                if null_score >= beta:
                    self.rep_len -= pushed_rep
                    return beta

        # Futility & LMP
        cdef int fut_margins[4]
        fut_margins[0] = 0; fut_margins[1] = 120; fut_margins[2] = 240; fut_margins[3] = 360
        cdef int lmp_counts[4]
        lmp_counts[0] = 0; lmp_counts[1] = 8; lmp_counts[2] = 14; lmp_counts[3] = 22

        cdef int futility = 0
        if not is_in_check and not is_pv and depth <= 3 and ply > 0 and alpha < TB_WIN - 1000 and alpha > -TB_WIN + 1000 and beta < TB_WIN - 1000 and beta > -TB_WIN + 1000:
            if not has_static:
                static_eval = c_evaluate(st, self.ptable)
                has_static = 1
            if static_eval + fut_margins[depth] <= alpha:
                futility = 1

        cdef int lmp_limit = lmp_counts[depth] if (not is_in_check and not is_pv and depth <= 3 and ply > 0 and alpha < TB_WIN - 1000 and alpha > -TB_WIN + 1000) else 999

        cdef uint16_t moves[MAX_MOVES]
        cdef int scores[MAX_MOVES]
        cdef int n = gen_moves(st, moves, 0)
        self._score_moves(st, moves, scores, n, ply, tt_move, prev_move)

        cdef int ply_c = ply if ply < MAX_PLY else (MAX_PLY - 1)
        cdef uint16_t k0 = self.killers[ply_c][0]
        cdef uint16_t k1 = self.killers[ply_c][1]
        cdef uint16_t cm = self.counter_moves[prev_move & 63][(prev_move >> 6) & 63] if prev_move else 0

        cdef int orig_alpha = alpha
        cdef int best_score = -(INFINITY + 1)
        cdef uint16_t best_move = 0
        cdef int legal_cnt = 0
        cdef int quiet_cnt = 0
        cdef int pruned = 0
        cdef int lmr_start = 4 if is_pv else 3
        cdef int i, j, fl, is_cap, is_promo, is_quiet, cap_a, cap_v, gives_chk, ext, red, score, c_idx, upd, is_excl
        cdef uint16_t m

        for i in range(n):
            m = self._pick_best(moves, scores, n, i)
            if ply == 0 and self.excluded_cnt > 0:
                is_excl = 0
                for j in range(self.excluded_cnt):
                    if self.excluded_root[j] == m:
                        is_excl = 1; break
                if is_excl:
                    continue

            fl = m >> 12
            is_cap = 1 if (fl & 4) else 0
            is_promo = 1 if (fl & 8) else 0
            is_quiet = 1 if (not is_cap and not is_promo) else 0

            if is_quiet and legal_cnt > 0 and m != k0 and m != k1 and m != cm:
                if quiet_cnt >= lmp_limit:
                    pruned = 1
                    continue

            if is_cap:
                cap_a = st.sq[m & 63] & 7
                cap_v = 1 if fl == FLAG_EP else (st.sq[(m >> 6) & 63] & 7)
            else:
                cap_a = 0; cap_v = 0

            if not make_move(st, m):
                continue

            gives_chk = in_check(st)
            if futility and is_quiet and legal_cnt > 0 and not gives_chk and m != k0 and m != k1 and m != cm:
                unmake_move(st)
                pruned = 1
                continue

            j = legal_cnt
            legal_cnt += 1
            if is_quiet:
                quiet_cnt += 1

            ext = 1 if gives_chk else 0
            red = 0
            if j >= lmr_start and depth >= 3 and ply > 0 and not is_in_check and not gives_chk and is_quiet and ext == 0:
                red = LMR_TABLE[depth if depth < 64 else 63][(j + 1) if (j + 1) < 64 else 63]
                if not is_pv: red += 1
                if m == k0 or m == k1 or m == cm: red -= 1
                if red < 1: red = 1
                elif red > depth - 2: red = depth - 2

            if red > 0:
                score = -self._negamax(st, depth - 1 + ext - red, -alpha - 1, -alpha, ply + 1, m)
                if score > alpha:
                    score = -self._negamax(st, depth - 1 + ext, -beta, -alpha, ply + 1, m)
            elif j == 0:
                score = -self._negamax(st, depth - 1 + ext, -beta, -alpha, ply + 1, m)
            else:
                score = -self._negamax(st, depth - 1 + ext, -alpha - 1, -alpha, ply + 1, m)
                if score > alpha:
                    score = -self._negamax(st, depth - 1 + ext, -beta, -alpha, ply + 1, m)

            unmake_move(st)
            if self.stopped:
                self.rep_len -= pushed_rep
                return 0

            if score > best_score:
                best_score = score
                best_move = m
                if ply == 0:
                    self.best_move_root = m

            if score > alpha:
                alpha = score
                if is_quiet:
                    c_idx = 0 if st.turn == WHITE else 1
                    upd = self.history[c_idx][m & 63][(m >> 6) & 63] + depth * depth
                    self.history[c_idx][m & 63][(m >> 6) & 63] = upd
                    if upd > 50000:
                        self._scale_history()

            if alpha >= beta:
                if is_quiet:
                    if m != self.killers[ply_c][0]:
                        self.killers[ply_c][1] = self.killers[ply_c][0]
                        self.killers[ply_c][0] = m
                    if prev_move:
                        self.counter_moves[prev_move & 63][(prev_move >> 6) & 63] = m
                elif is_cap:
                    upd = self.cap_history[cap_a][(m >> 6) & 63][cap_v] + depth * depth
                    self.cap_history[cap_a][(m >> 6) & 63][cap_v] = upd
                    if upd > 50000:
                        self._scale_cap_history()
                break

        self.rep_len -= pushed_rep

        if legal_cnt == 0:
            if pruned:
                return orig_alpha
            if is_in_check:
                return -INFINITY + ply
            return -CONTEMPT if st.turn == self.root_turn else CONTEMPT

        cdef uint8_t flag
        if best_score <= orig_alpha:
            flag = TT_ALPHA
        elif best_score >= beta:
            flag = TT_BETA
        elif pruned:
            flag = TT_BETA
        else:
            flag = TT_EXACT

        # Запись в TT (не перезаписываем корень при MultiPV-исключении ходов!)
        if not (ply == 0 and self.excluded_cnt > 0):
            if tte.key != zhash or depth >= tte.depth or flag == TT_EXACT:
                tte.key = zhash
                tte.score = <int32_t>score_to_tt(best_score, ply)
                tte.move = best_move
                tte.depth = <int8_t>depth
                tte.flag = flag

        return best_score

    cdef int _search_root(self, BoardState* st, int depth, int prev_score, int use_asp) noexcept nogil:
        if not use_asp:
            return self._negamax(st, depth, -INFINITY, INFINITY, 0, 0)
        cdef int delta = 30
        cdef int alpha = prev_score - delta
        cdef int beta = prev_score + delta
        cdef int attempt, score
        for attempt in range(2):
            score = self._negamax(st, depth, alpha, beta, 0, 0)
            if self.stopped:
                return score
            if score <= alpha:
                alpha = alpha - delta * 4
                if alpha < -INFINITY: alpha = -INFINITY
            elif score >= beta:
                beta = beta + delta * 4
                if beta > INFINITY: beta = INFINITY
            else:
                return score
            delta *= 4
        return self._negamax(st, depth, -INFINITY, INFINITY, 0, 0)

    cdef void _load_board(self, BoardState* st, object cb):
        memset(st, 0, sizeof(BoardState))
        cdef int s, code, c = 0
        cdef uint64_t bit
        for s in range(64):
            p = cb.piece_at(s)
            if p is not None:
                code = p.piece_type if p.color == chess.WHITE else (p.piece_type | 8)
                st.sq[s] = code
                bit = 1ULL << s
                st.bb[code] |= bit
                if code < 8:
                    st.occ_w |= bit
                else:
                    st.occ_b |= bit
                if code == WK: st.wk_sq = s
                elif code == BK: st.bk_sq = s
                st.mg += INC_MG[code][s]
                st.eg += INC_EG[code][s]
                st.phase += PHASE_W[code]
                st.zhash ^= Z_PIECE[code][s]
        st.occ = st.occ_w | st.occ_b
        st.turn = WHITE if cb.turn == chess.WHITE else BLACK
        cr = cb.clean_castling_rights()
        if cr & chess.BB_H1: c |= CASTLE_WK
        if cr & chess.BB_A1: c |= CASTLE_WQ
        if cr & chess.BB_H8: c |= CASTLE_BK
        if cr & chess.BB_A8: c |= CASTLE_BQ
        st.castling = c
        ep_sq = cb.ep_square
        if ep_sq is not None and cb.has_pseudo_legal_en_passant():
            st.ep = ep_sq
        else:
            st.ep = -1
        st.halfmove = cb.halfmove_clock
        st.zhash ^= Z_CASTLE[c] ^ Z_EP[st.ep if st.ep >= 0 else 64]
        if st.turn == BLACK:
            st.zhash ^= Z_SIDE

    def perft(self, object cb, int depth):
        cdef BoardState st
        self._load_board(&st, cb)
        return self._perft(&st, depth)

    cdef uint64_t _perft(self, BoardState* st, int depth) noexcept nogil:
        if depth == 0:
            return 1ULL
        cdef uint16_t moves[MAX_MOVES]
        cdef int n = gen_moves(st, moves, 0)
        cdef uint64_t nodes = 0ULL
        cdef int i
        for i in range(n):
            if make_move(st, moves[i]):
                nodes += self._perft(st, depth - 1)
                unmake_move(st)
        return nodes

    def search_position(
        self,
        object cb,
        double time_limit,
        int max_depth,
        int qs_max,
        int multipv,
        object on_iteration=None,
    ):
        cdef BoardState st
        self._load_board(&st, cb)

        # История повторений (храним максимум последние 1024 полухода, чтобы
        # всегда оставался запас >= 1024 ячеек под дерево поиска MAX_PLY=128)
        self.rep_len = 0
        if cb.move_stack:
            tmp = cb.root()
            hist_hashes = []
            self._load_board(&st, tmp)
            hist_hashes.append(st.zhash)
            for cm in cb.move_stack[:len(cb.move_stack) - 1]:
                tmp.push(cm)
                self._load_board(&st, tmp)
                hist_hashes.append(st.zhash)
            if len(hist_hashes) > 1024:
                hist_hashes = hist_hashes[len(hist_hashes) - 1024:]
            for h_val in hist_hashes:
                self.rep_history[self.rep_len] = h_val
                self.rep_len += 1
            self._load_board(&st, cb)

        self.start_time = c_now_sec()
        self.time_limit = time_limit
        self.stopped = False
        self.nodes = 0
        self.root_turn = st.turn
        self.qs_max_depth = qs_max
        memset(self.killers, 0, sizeof(self.killers))
        self._scale_history()
        self._scale_cap_history()

        cdef int depth, k, j_ex, score, best_score = 0, depth_reached = 0, prev_score = 0, stable = 0, use_asp
        cdef uint16_t best_move = 0, prev_best = 0, cand
        cdef list best_lines = []
        cdef list lines
        cdef TTEntry* tte
        cdef double elapsed, remaining

        for depth in range(1, max_depth + 1):
            lines = []
            self.excluded_cnt = 0

            for k in range(multipv):
                self.best_move_root = 0
                use_asp = 1 if (depth > 4 and k == 0 and multipv == 1) else 0
                with nogil:
                    score = self._search_root(&st, depth, prev_score, use_asp)
                if self.stopped:
                    break
                cand = self.best_move_root
                if cand == 0:
                    tte = &self.tt[st.zhash & self.tt_mask]
                    if tte.key == st.zhash and tte.move != 0:
                        cand = tte.move
                if cand != 0 and self.excluded_cnt > 0:
                    for j_ex in range(self.excluded_cnt):
                        if self.excluded_root[j_ex] == cand:
                            cand = 0
                            break
                if cand == 0:
                    break
                lines.append((int(cand), int(score)))
                if self.excluded_cnt < 64:
                    self.excluded_root[self.excluded_cnt] = cand
                    self.excluded_cnt += 1

            if self.stopped or not lines:
                break

            if multipv > 1 and len(lines) > 1:
                lines.sort(key=lambda item: item[1], reverse=True)

            prev_best = best_move
            best_lines = lines
            best_move = lines[0][0]
            best_score = lines[0][1]
            depth_reached = depth
            prev_score = best_score
            stable = (stable + 1) if best_move == prev_best else 0

            if on_iteration is not None:
                try:
                    on_iteration(depth, best_score, best_lines, int(self.nodes), c_now_sec() - self.start_time)
                except Exception:
                    pass

            if abs(best_score) >= MATE_SCORE and (multipv == 1 or depth >= 8):
                break

            elapsed = c_now_sec() - self.start_time
            remaining = time_limit - elapsed
            if remaining < time_limit * 0.02:
                break
            if stable >= 4 and elapsed > time_limit * 0.8:
                break

        return best_lines, best_score, depth_reached
