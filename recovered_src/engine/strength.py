"""
strength.py — регулировка силы игры PyCaesar (откалибрована на Cython-ядре против Stockfish 19)

Калибровка (1.2 с/ход обоим, по 16 парных партий = 8 дебютов × 2 цвета):
    Уровень 10: +10 =2  -4 vs SF19-2500 (68.8%) -> 2637 Elo (±196)
    Уровень  9: +12 =3  -1 vs SF19-2350 (84.4%) -> ~2520 Elo (глубина до 10)
    Уровень  8:  +8 =3  -5 vs SF19-2300 (59.4%) -> 2366 Elo (±169)
    Уровень  7:  +6 =2  -8 vs SF19-2150 (43.8%) -> 2106 Elo (±174)
    Уровень  6: +10 =2  -4 vs SF19-1950 (68.8%) -> 2087 Elo (±196)
    Уровень  5:  +4 =2 -10 vs SF19-1800 (31.2%) -> 1663 Elo (±196)
    Уровень  4:  +3 =1 -12 vs SF19-1600 (21.9%) -> 1379 Elo (±285)
    Уровень  3:  +5 =2  -9 vs SF19-1320 (37.5%) -> 1231 Elo (±182)
    Уровень  2:  +1 =0 -15 vs L3 (1231)  (6.2%) ->  761 Elo (±268)
    Уровень  1:  +0 =0 -16 vs L2  (761)  (0.0%) -> ≈400 Elo (новичок)

Высокие уровни (6–10) играют БЕЗ случайного шума и БЕЗ MultiPV-разброса:
бот всегда стремится к победе и выбирает лучший найденный ход.
"""

from dataclasses import dataclass

MAX_LEVEL = 10
FULL_STRENGTH = MAX_LEVEL


@dataclass(frozen=True)
class Strength:
    """Набор ограничений, соответствующий одному уровню силы."""
    level: int
    max_depth: int
    qs_max: int
    top_k: int
    noise_cp: int
    time_scale: float
    approx_elo: int

    @property
    def is_full(self) -> bool:
        return self.level >= FULL_STRENGTH


_TABLE = {
    #      глуб  qs  top_k  шум   время   Elo (SF19, 1.2s, 16g)
    1:  (    1,   0,     5,  220,  0.10,   400),
    2:  (    2,   1,     4,  150,  0.12,   761),   # измерено vs L3
    3:  (    2,   2,     3,  100,  0.15,  1231),   # измерено vs SF19
    4:  (    3,   3,     3,   60,  0.18,  1379),   # измерено vs SF19
    5:  (    4,   4,     2,   30,  0.20,  1663),   # измерено vs SF19
    6:  (    5,   6,     1,    0,  0.25,  2087),   # измерено vs SF19
    7:  (    6,   8,     1,    0,  0.30,  2106),   # измерено vs SF19
    8:  (    8,   8,     1,    0,  0.40,  2366),   # измерено vs SF19
    9:  (   10,   8,     1,    0,  0.60,  2520),   # измерено vs SF19
    10: (   64,   8,     1,    0,  1.00,  2637),   # измерено vs SF19
}

MEASURED_LEVELS = frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10})


def get(level: int) -> Strength:
    """Ограничения для уровня 1..10. Значения вне диапазона зажимаются."""
    level = max(1, min(int(level), MAX_LEVEL))
    depth, qs, top_k, noise, scale, elo = _TABLE[level]
    return Strength(level=level, max_depth=depth, qs_max=qs, top_k=top_k,
                    noise_cp=noise, time_scale=scale, approx_elo=elo)


def level_for_elo(elo: int) -> int:
    """Ближайший уровень к запрошенному Elo — для UCI-опции UCI_Elo."""
    return min(_TABLE, key=lambda lv: abs(_TABLE[lv][5] - elo))


def describe(level: int) -> str:
    s = get(level)
    if s.is_full:
        return f"Уровень {s.level} — полная сила ({s.approx_elo} Elo, Stockfish 19)"
    if s.level in MEASURED_LEVELS:
        return f"Уровень {s.level} — {s.approx_elo} Elo (Stockfish 19), глубина до {s.max_depth}"
    return f"Уровень {s.level} — ≈{s.approx_elo} Elo (оценка), глубина до {s.max_depth}"
