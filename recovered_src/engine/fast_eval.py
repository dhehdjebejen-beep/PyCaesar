"""
fast_eval.py — Сверхбыстрая инкрементальная оценка на FastBoard (~2–3 мкс вместо 20 мкс).

Использует уже поддерживаемые инкрементально fb.mg, fb.eg, fb.phase (PeSTO),
плюс фирменный стиль PyCaesar (усиленный пешечный щит, структура, форпосты,
ладья на 7-й, пара слонов, угрозы пешками, свободные проходные, mop-up).
"""

import chess
from engine.fast_board import (
    FastBoard, WHITE, WP, WN, WB, WR, WQ, BP, BN, BB, BR, BQ,
    NOT_FILE_A, NOT_FILE_H,
    KNIGHT_ATT, KING_ATT, DIAG_MASKS, DIAG_ATTACKS, FILE_MASKS, FILE_ATTACKS, RANK_MASKS, RANK_ATTACKS,
)
from engine.evaluation import (
    MAX_PHASE,
    _BB_FILES, _ADJACENT_FILES, _PASSED_MASK_W, _PASSED_MASK_B,
    _OUTPOST_ENEMY_W, _OUTPOST_ENEMY_B, _OUTPOST_ZONE_W, _OUTPOST_ZONE_B,
    _SHIELD_W, _SHIELD_B, _KING_FILE_LIST, _CENTER_MANHATTAN,
    _PAWN_ATT_W, _PAWN_ATT_B, _W_START_MINORS, _B_START_MINORS,
    _W_QUEEN_HOME, _B_QUEEN_HOME,
    PASSED_PAWN_MG, PASSED_PAWN_EG,
    ROOK_OPEN_FILE_BONUS, ROOK_SEMI_OPEN_FILE_BONUS,
    ROOK_ON_SEVENTH_MG, ROOK_ON_SEVENTH_EG,
    KNIGHT_OUTPOST_BONUS, BISHOP_OUTPOST_BONUS,
    KING_SHIELD_BONUS, KING_OPEN_FILE_PENALTY, TEMPO_BONUS,
)

# Точный пешечный хэш для FastBoard: (wp, bp) -> (mg_delta, eg_delta, passed_w, passed_b)
_FAST_PAWN_HASH: dict[tuple[int, int], tuple[int, int, int, int]] = {}
_FAST_PAWN_HASH_MAX = 65536


def clear_fast_pawn_hash() -> None:
    _FAST_PAWN_HASH.clear()


def _pawn_eval(wp: int, bp: int) -> tuple[int, int, int, int]:
    key = (wp, bp)
    cached = _FAST_PAWN_HASH.get(key)
    if cached is not None:
        return cached

    s_mg = s_eg = p_mg = p_eg = 0
    passed_w = passed_b = 0
    files = _BB_FILES

    wc = (
        (wp & files[0]).bit_count(), (wp & files[1]).bit_count(),
        (wp & files[2]).bit_count(), (wp & files[3]).bit_count(),
        (wp & files[4]).bit_count(), (wp & files[5]).bit_count(),
        (wp & files[6]).bit_count(), (wp & files[7]).bit_count(),
    )
    bc = (
        (bp & files[0]).bit_count(), (bp & files[1]).bit_count(),
        (bp & files[2]).bit_count(), (bp & files[3]).bit_count(),
        (bp & files[4]).bit_count(), (bp & files[5]).bit_count(),
        (bp & files[6]).bit_count(), (bp & files[7]).bit_count(),
    )

    x = wp
    while x:
        lsb = x & -x
        sq = lsb.bit_length() - 1
        f = sq & 7
        if wc[f] > 1:
            s_mg -= 12
            s_eg -= 20
        has_n = bool(wp & _ADJACENT_FILES[f])
        if not has_n:
            s_mg -= 15
            s_eg -= 18
        if not (_PASSED_MASK_W[sq] & bp):
            r = sq >> 3
            p_mg += PASSED_PAWN_MG[r]
            p_eg += PASSED_PAWN_EG[r]
            passed_w |= lsb
        if _PAWN_ATT_B[sq] & wp:
            s_mg += 10
            s_eg += 8
        elif not has_n:
            s_mg -= 10
            s_eg -= 8
        x ^= lsb

    x = bp
    while x:
        lsb = x & -x
        sq = lsb.bit_length() - 1
        f = sq & 7
        if bc[f] > 1:
            s_mg += 12
            s_eg += 20
        has_n = bool(bp & _ADJACENT_FILES[f])
        if not has_n:
            s_mg += 15
            s_eg += 18
        if not (_PASSED_MASK_B[sq] & wp):
            r = 7 - (sq >> 3)
            p_mg -= PASSED_PAWN_MG[r]
            p_eg -= PASSED_PAWN_EG[r]
            passed_b |= lsb
        if _PAWN_ATT_W[sq] & bp:
            s_mg -= 10
            s_eg -= 8
        elif not has_n:
            s_mg += 10
            s_eg += 8
        x ^= lsb

    # Фирменные веса PyCaesar (структура 1.30×, проходные 1.10×)
    mg = int(s_mg * 1.30) + int(p_mg * 1.10)
    eg = int(s_eg * 1.30) + int(p_eg * 1.10)
    res = (mg, eg, passed_w, passed_b)
    if len(_FAST_PAWN_HASH) >= _FAST_PAWN_HASH_MAX:
        _FAST_PAWN_HASH.clear()
    _FAST_PAWN_HASH[key] = res
    return res


def fast_evaluate(fb: FastBoard) -> int:
    """
    Быстрая точная оценка позиции на FastBoard (~2–3 мкс).
    Возвращает оценку в сантипешках с точки зрения стороны, чей ход.
    """
    bb = fb.bb
    wp = bb[WP]
    bp = bb[BP]
    wn = bb[WN]
    wb = bb[WB]
    wr = bb[WR]
    wq = bb[WQ]
    bn = bb[BN]
    bb_b = bb[BB]
    br = bb[BR]
    bq = bb[BQ]

    # Недостаток материала (быстрая проверка по битбордам)
    if not (wp | bp | wr | br | wq | bq):
        if (wn | wb | bn | bb_b).bit_count() <= 1:
            return 0

    phase = fb.phase
    if phase > MAX_PHASE:
        phase = MAX_PHASE

    mg = fb.mg
    eg = fb.eg

    # 1. Пешечный хэш (O(1) при попадании)
    if wp | bp:
        p_mg, p_eg, passed_w, passed_b = _pawn_eval(wp, bp)
        mg += p_mg
        eg += p_eg
    else:
        passed_w = passed_b = 0

    occ = fb.occ

    # 2. Незаблокированные проходные пешки
    if passed_w:
        free_w = (passed_w << 8) & ~occ
        if free_w:
            eg += free_w.bit_count() * 20
    if passed_b:
        free_b = (passed_b >> 8) & ~occ
        if free_b:
            eg -= free_b.bit_count() * 20

    # 3. Угрозы пешками (2 битовых сдвига)
    w_pa = ((wp & NOT_FILE_A) << 7) | ((wp & NOT_FILE_H) << 9)
    b_pa = ((bp & NOT_FILE_H) >> 7) | ((bp & NOT_FILE_A) >> 9)
    if w_pa:
        wt = (
            (w_pa & (bn | bb_b)).bit_count() * 25
            + (w_pa & br).bit_count() * 40
            + (w_pa & bq).bit_count() * 55
        )
        mg += wt
        eg += wt >> 1
    if b_pa:
        bt = (
            (b_pa & (wn | wb)).bit_count() * 25
            + (b_pa & wr).bit_count() * 40
            + (b_pa & wq).bit_count() * 55
        )
        mg -= bt
        eg -= bt >> 1

    # 4. Форпосты коней и слонов
    out = 0
    x = wn & _OUTPOST_ZONE_W & w_pa
    while x:
        lsb = x & -x
        if not (_OUTPOST_ENEMY_W[lsb.bit_length() - 1] & bp):
            out += KNIGHT_OUTPOST_BONUS
        x ^= lsb
    x = bn & _OUTPOST_ZONE_B & b_pa
    while x:
        lsb = x & -x
        if not (_OUTPOST_ENEMY_B[lsb.bit_length() - 1] & wp):
            out -= KNIGHT_OUTPOST_BONUS
        x ^= lsb
    x = wb & _OUTPOST_ZONE_W & w_pa
    while x:
        lsb = x & -x
        if not (_OUTPOST_ENEMY_W[lsb.bit_length() - 1] & bp):
            out += BISHOP_OUTPOST_BONUS
        x ^= lsb
    x = bb_b & _OUTPOST_ZONE_B & b_pa
    while x:
        lsb = x & -x
        if not (_OUTPOST_ENEMY_B[lsb.bit_length() - 1] & wp):
            out -= BISHOP_OUTPOST_BONUS
        x ^= lsb
    if out:
        mg += out
        eg += out >> 1

    # 5. Пара слонов (сильнее в эндшпиле)
    if wb and wb & (wb - 1):
        mg += 33
        eg += 55
    if bb_b and bb_b & (bb_b - 1):
        mg -= 33
        eg -= 55

    # 6. Дебютная дисциплина (phase >= 18)
    if phase >= 18:
        w_un = ((wn | wb) & _W_START_MINORS).bit_count()
        b_un = ((bn | bb_b) & _B_START_MINORS).bit_count()
        dev = (b_un - w_un) * 9
        if w_un >= 2 and wq and not (wq & _W_QUEEN_HOME):
            dev -= 20
        if b_un >= 2 and bq and not (bq & _B_QUEEN_HOME):
            dev += 20
        mg += dev

    wk = fb.wk_sq
    bk = fb.bk_sq

    # 7. Ладьи: открытые/полуоткрытые линии + 7-я/2-я горизонталь
    if wr | br:
        files = _BB_FILES
        x = wr
        if x:
            bk_r7 = (bk >> 3) == 7 or bool(bp & chess.BB_RANK_7)
            while x:
                lsb = x & -x
                sq = lsb.bit_length() - 1
                fm = files[sq & 7]
                if not (wp & fm):
                    if not (bp & fm):
                        mg += ROOK_OPEN_FILE_BONUS
                        eg += 14
                    else:
                        mg += ROOK_SEMI_OPEN_FILE_BONUS
                        eg += 7
                if (sq >> 3) == 6 and bk_r7:
                    mg += ROOK_ON_SEVENTH_MG
                    eg += ROOK_ON_SEVENTH_EG
                x ^= lsb
        x = br
        if x:
            wk_r0 = (wk >> 3) == 0 or bool(wp & chess.BB_RANK_2)
            while x:
                lsb = x & -x
                sq = lsb.bit_length() - 1
                fm = files[sq & 7]
                if not (bp & fm):
                    if not (wp & fm):
                        mg -= ROOK_OPEN_FILE_BONUS
                        eg -= 14
                    else:
                        mg -= ROOK_SEMI_OPEN_FILE_BONUS
                        eg -= 7
                if (sq >> 3) == 1 and wk_r0:
                    mg -= ROOK_ON_SEVENTH_MG
                    eg -= ROOK_ON_SEVENTH_EG
                x ^= lsb

    # 8. Безопасная мобильность + атаки на зону короля на таблицах FastBoard
    safe_w = ~(fb.occ_w | b_pa)
    safe_b = ~(fb.occ_b | w_pa)
    do_ks = phase >= 7
    w_kz = KING_ATT[wk] if do_ks else 0
    b_kz = KING_ATT[bk] if do_ks else 0
    w_att_w = 0
    b_att_w = 0
    mob = 0

    # Кони
    x = wn
    while x:
        lsb = x & -x
        att = KNIGHT_ATT[lsb.bit_length() - 1]
        mob += (att & safe_w).bit_count() * 4
        if b_kz and (att & b_kz):
            b_att_w += 20
        x ^= lsb
    x = bn
    while x:
        lsb = x & -x
        att = KNIGHT_ATT[lsb.bit_length() - 1]
        mob -= (att & safe_b).bit_count() * 4
        if w_kz and (att & w_kz):
            w_att_w += 20
        x ^= lsb

    # Слоны
    x = wb
    while x:
        lsb = x & -x
        s = lsb.bit_length() - 1
        att = DIAG_ATTACKS[s][occ & DIAG_MASKS[s]]
        mob += (att & safe_w).bit_count() * 4
        if b_kz and (att & b_kz):
            b_att_w += 20
        x ^= lsb
    x = bb_b
    while x:
        lsb = x & -x
        s = lsb.bit_length() - 1
        att = DIAG_ATTACKS[s][occ & DIAG_MASKS[s]]
        mob -= (att & safe_b).bit_count() * 4
        if w_kz and (att & w_kz):
            w_att_w += 20
        x ^= lsb

    # Ладьи
    x = wr
    while x:
        lsb = x & -x
        s = lsb.bit_length() - 1
        att = FILE_ATTACKS[s][occ & FILE_MASKS[s]] | RANK_ATTACKS[s][occ & RANK_MASKS[s]]
        mob += (att & safe_w).bit_count() * 2
        if b_kz and (att & b_kz):
            b_att_w += 40
        x ^= lsb
    x = br
    while x:
        lsb = x & -x
        s = lsb.bit_length() - 1
        att = FILE_ATTACKS[s][occ & FILE_MASKS[s]] | RANK_ATTACKS[s][occ & RANK_MASKS[s]]
        mob -= (att & safe_b).bit_count() * 2
        if w_kz and (att & w_kz):
            w_att_w += 40
        x ^= lsb

    # Ферзи
    x = wq
    while x:
        lsb = x & -x
        s = lsb.bit_length() - 1
        att = DIAG_ATTACKS[s][occ & DIAG_MASKS[s]] | FILE_ATTACKS[s][occ & FILE_MASKS[s]] | RANK_ATTACKS[s][occ & RANK_MASKS[s]]
        mob += (att & safe_w).bit_count()
        if b_kz and (att & b_kz):
            b_att_w += 80
        x ^= lsb
    x = bq
    while x:
        lsb = x & -x
        s = lsb.bit_length() - 1
        att = DIAG_ATTACKS[s][occ & DIAG_MASKS[s]] | FILE_ATTACKS[s][occ & FILE_MASKS[s]] | RANK_ATTACKS[s][occ & RANK_MASKS[s]]
        mob -= (att & safe_b).bit_count()
        if w_kz and (att & w_kz):
            w_att_w += 80
        x ^= lsb

    mob = int(mob * 0.95)
    mg += mob
    eg += mob

    # 9. Безопасность короля (пешечный щит + открытые линии + атаки на зону короля)
    if do_ks:
        files = _BB_FILES
        ks = 0
        for mask in _SHIELD_W[wk]:
            if mask & wp:
                ks += KING_SHIELD_BONUS
        for f in _KING_FILE_LIST[wk]:
            fm = files[f]
            if not (wp & fm):
                ks -= KING_OPEN_FILE_PENALTY if not (bp & fm) else (KING_OPEN_FILE_PENALTY >> 1)
        if w_att_w:
            ks -= int(min(350, (w_att_w * w_att_w) // 110) * 0.85)

        for mask in _SHIELD_B[bk]:
            if mask & bp:
                ks -= KING_SHIELD_BONUS
        for f in _KING_FILE_LIST[bk]:
            fm = files[f]
            if not (bp & fm):
                ks += KING_OPEN_FILE_PENALTY if not (wp & fm) else (KING_OPEN_FILE_PENALTY >> 1)
        if b_att_w:
            ks += int(min(350, (b_att_w * b_att_w) // 110) * 0.85)
        mg += ks

    # 9. Эндшпильный mop-up
    if phase <= 8 and abs(eg) >= 350:
        kd = abs((wk & 7) - (bk & 7)) + abs((wk >> 3) - (bk >> 3))
        if eg > 0:
            eg += _CENTER_MANHATTAN[bk] * 10 + (14 - kd) * 4
        else:
            eg -= _CENTER_MANHATTAN[wk] * 10 + (14 - kd) * 4

    score = int((mg * phase + eg * (MAX_PHASE - phase)) / MAX_PHASE)
    if fb.turn == WHITE:
        return score + TEMPO_BONUS
    return -score + TEMPO_BONUS
