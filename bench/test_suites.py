"""
bench/test_suites.py — Тактический (WAC) и стратегический (STS-тематический) бенчмарк PyCaesar.

Использование:
    python bench/test_suites.py --time 0.5 --style Balanced
    python bench/test_suites.py --time 0.5 --style Aggressive
"""

import argparse
import os
import sys
import time
import chess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "recovered_src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from engine.search import SearchEngine

# 20 классических тактических задач Win At Chess (WAC)
WAC_PUZZLES = [
    ("WAC.001", "2rr3k/pp3pp1/1nnqbN1p/3pN3/2pP4/2P3Q1/PPB4P/R4RK1 w - - 0 1", ["g3g6"]),
    ("WAC.002", "8/7p/5k2/5p2/p1p2P2/Pr1pPK2/1P1R3P/8 b - - 0 1", ["b3b2"]),
    ("WAC.003", "5rk1/1ppb3p/p1pb4/6q1/3P1p1r/2P1R2P/PP1BQ1P1/5RKN w - - 0 1", ["e3g3"]),
    ("WAC.004", "r1bq2rk/pp3pbp/2p1p1pQ/7P/3P4/2PB1N2/PP3PPR/2KR4 w - - 0 1", ["h6h7"]),
    ("WAC.005", "5k2/6pp/p1qN4/1p1p4/3P4/2PKP2Q/pp3r2/6R1 b - - 0 1", ["c6c4"]),
    ("WAC.007", "r3k2r/ppp2ppp/2n5/2bqp3/8/P2PB3/1PP1NPPP/R2QKB1R w KQkq - 0 1", ["e2c3"]),
    ("WAC.008", "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 1", ["c3d5"]),
    ("WAC.010", "2br2k1/2q3rn/p2NppQ1/2p1P3/Pp5R/4P3/1P3PPP/3R2K1 w - - 0 1", ["h4h7"]),
    ("WAC.011", "r1b1kb1r/3q1ppp/pBp1pn2/8/Np3P2/5B2/PPP3PP/R2Q1RK1 w kq - 0 1", ["f3c6"]),
    ("WAC.012", "4k1r1/2p3r1/1pR1p3/3pP2p/3P2qP/P4N2/1PQ4P/5R1K b - - 0 1", ["g4f3"]),
    ("WAC.014", "r2rb1k1/pp1q1p1p/2n1p1p1/2bp4/5P2/PP1BPR1Q/1BPN2PP/R5K1 w - - 0 1", ["h3h7"]),
    ("WAC.015", "1R6/1brk2p1/4p2p/p1P1Pp2/P7/6P1/1P4P1/2R3K1 w - - 0 1", ["b8b7"]),
    ("WAC.016", "r4rk1/ppp2ppp/2n5/2bqp3/8/P2PB3/1PP1NPPP/R2QKB1R b KQ - 0 1", ["c5e3"]),
    ("WAC.018", "r1b2rk1/pp2bppp/2n1p3/q7/2BPN3/P7/1P2QPPP/R1B2RK1 w - - 1 1", ["c1d2"]),
    ("WAC.020", "r1b1k2r/pp3ppp/2n1pn2/q1bp4/2P5/2N1PN2/PP2BPPP/R1BQK2R w KQkq - 0 1", ["c4d5"]),
    ("WAC.021", "r1bqk2r/ppp2ppp/2n5/3np3/2B5/5Q2/PPPP1PPP/RNB1K2R w KQkq - 0 1", ["c4d5", "f3f7"]),
    ("WAC.022", "r3k2r/pp1nbppp/2p1pn2/q7/2BP4/2N1PN2/PP3PPP/R2Q1RK1 w kq - 0 1", ["e3e4"]),
    ("WAC.025", "r1b1k2r/ppq2ppp/2n1p3/2bp4/2B5/2P2N2/PP3PPP/RNBQR1K1 w kq - 0 1", ["c4d5"]),
    ("WAC.026", "r2qk2r/ppp2ppp/2n1bn2/2b1p3/4P3/2NP1N2/PPP2PPP/R1BQKB1R w KQkq - 0 1", ["c1g5"]),
    ("WAC.027", "r1bq1rk1/ppp2ppp/2n5/3pP3/3P4/2PB1N2/PP3PPP/R1BQK2R w KQ - 0 1", ["d3h7"]),
]

# 12 стратегических позиций (форпосты, ладья на 7-й, открытые линии, развитие, проходные)
STS_PUZZLES = [
    ("STS.Outpost.1", "r2q1rk1/pp2bppp/2n1pn2/3p4/3P4/2N1PN2/PP2BPPP/R1BQ1RK1 w - - 0 10", ["f3e5", "c1d2", "b2b3"]),
    ("STS.Outpost.2", "r1bq1rk1/pp3ppp/2n1pn2/2pp4/3P4/2PBPN2/PP3PPP/RNBQ1RK1 w - - 0 8", ["f3e5", "b1d2"]),
    ("STS.Rook7.1", "4r1k1/pp3ppp/8/8/8/8/PP3PPP/3R2K1 w - - 0 25", ["d1d7"]),
    ("STS.Rook7.2", "2r3k1/pp3ppp/2p5/8/8/2P5/PP3PPP/2R3K1 w - - 0 25", ["c1e1", "c1d1", "g1f1"]),
    ("STS.OpenFile.1", "r2q1rk1/ppp2ppp/2np1n2/4p3/4P3/2NP1N2/PPP2PPP/R2Q1RK1 w - - 0 9", ["d3d4", "c1g5", "f1e1"]),
    ("STS.Passed.1", "8/5pk1/6p1/3P4/8/6P1/5PK1/8 w - - 0 40", ["d5d6", "g2f3"]),
    ("STS.Dev.1", "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2", ["b8c6", "g8f6"]),
    ("STS.Dev.2", "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3", ["g8f6", "f8c5"]),
    ("STS.Castle.1", "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 1 5", ["e1g1", "c2c3", "b1c3"]),
    ("STS.KingAct.1", "8/p4pk1/1p4p1/2p4p/2P4P/1P4P1/P4PK1/8 w - - 0 45", ["g2f3", "f2f4"]),
    ("STS.BishPair.1", "r2qk2r/ppp2ppp/2n1bn2/3p4/3P4/2NBPN2/PPP2PPP/R2QK2R w KQkq - 0 8", ["e1g1", "c1d2", "f3e5"]),
    ("STS.Center.1", "rnbqkb1r/ppp2ppp/4pn2/3p4/2PP4/2N5/PP2PPPP/R1BQKBNR w KQkq - 0 4", ["c4d5", "c1g5", "g1f3"]),
]


def run_suite(name: str, puzzles: list, movetime: float, style: str) -> tuple[int, int, float]:
    se = SearchEngine()
    solved = 0
    depths = []
    t0 = time.time()
    for pid, fen, ans in puzzles:
        se.tt.clear()
        b = chess.Board(fen)
        lines, _, d = se.search(b, time_limit=movetime, use_book=False)
        mv = lines[0][0].uci() if lines else "none"
        if mv in ans:
            solved += 1
        depths.append(d)
    avg_d = sum(depths) / max(1, len(depths))
    print(f"{name:<12} ({style:<10}, {movetime:.1f}s): {solved:2d}/{len(puzzles)} solved | avg_depth={avg_d:.2f} | {time.time()-t0:.1f}s")
    return solved, len(puzzles), avg_d


def main():
    ap = argparse.ArgumentParser(description="WAC + STS бенчмарк для PyCaesar")
    ap.add_argument("--time", type=float, default=0.5)
    ap.add_argument("--style", default="Balanced")
    args = ap.parse_args()
    run_suite("WAC Tactics", WAC_PUZZLES, args.time, args.style)
    run_suite("STS Strategy", STS_PUZZLES, args.time, args.style)


if __name__ == "__main__":
    main()
