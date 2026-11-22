"""
Комплексный набор тестов для шахматного движка PyCaesar:
оценка, PST, пешечный хэш, мобильность (M3), ленивая оценка, SEE + en passant,
поиск мата, защита от пата/повторений в корне, MultiPV, уровни силы,
эндшпильные базы Syzygy (3–4 фигуры) и протокол UCI.
"""

import os
import sys
import time
import unittest

import chess
import chess.engine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "recovered_src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from engine.evaluation import (
    evaluate,
    clear_pawn_hash,
    LAZY_MARGIN,
    _evaluate_mobility,
    _evaluate_pawn_structure,
)
from engine.search import SearchEngine, MATE_SCORE, _see
from engine.engine import ChessEngine
from engine import strength as strength_levels
from engine.tablebase import SyzygyTablebase, TB_WIN_SCORE


class TestEvaluation(unittest.TestCase):
    def test_antisymmetry_mirror(self):
        fens = [
            "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQ1RK1 w kq - 5 4",
            "r2q1rk1/pp2bppp/2npbn2/4p3/4P3/1NN1BP2/PPPQ2PP/R3KB1R w KQ - 3 10",
            "8/5pk1/6p1/3R4/8/6P1/5PK1/3r4 w - - 0 40",
            "4k3/3P4/8/8/8/8/8/4K3 w - - 0 1",
        ]
        for fen in fens:
            b = chess.Board(fen)
            m = b.mirror()
            self.assertEqual(evaluate(b), evaluate(m), f"Asymmetry on {fen}")

    def test_pst_orientation_pawn_and_king(self):
        b_d2 = chess.Board("4k3/8/8/8/8/8/3P4/4K3 w - - 0 1")
        b_d7 = chess.Board("4k3/3P4/8/8/8/8/8/4K3 w - - 0 1")
        self.assertGreater(evaluate(b_d7), evaluate(b_d2) + 100)

        b_e1 = chess.Board("r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1")
        b_g1 = chess.Board("r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R4RK1 w kq - 1 1")
        self.assertGreater(evaluate(b_g1), evaluate(b_e1))

    def test_pawn_hash_exact(self):
        clear_pawn_hash()
        b = chess.Board("r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQ1RK1 w kq - 5 4")
        first = _evaluate_pawn_structure(b)
        second = _evaluate_pawn_structure(b)
        self.assertEqual(first, second)

    def test_mobility_excludes_own_pieces(self):
        # Слон на c1 в начальной позиции полностью заперт своими пешками — мобильность = 0.
        b_blocked = chess.Board("4k3/8/8/8/8/8/1PPP4/2B1K3 w - - 0 1")
        b_open = chess.Board("4k3/8/8/8/8/8/8/2B1K3 w - - 0 1")
        self.assertEqual(_evaluate_mobility(b_blocked), 0)
        self.assertGreater(_evaluate_mobility(b_open), 0)

    def test_lazy_eval_bounds(self):
        # Позиция с лишним ферзём (+900+): при beta=0 ленивая оценка должна быстро вернуть >= beta.
        b = chess.Board("4k3/8/8/8/8/8/4Q3/4K3 w - - 0 1")
        full = evaluate(b)
        lazy_hi = evaluate(b, alpha=-50, beta=0)
        self.assertGreaterEqual(lazy_hi, 0)
        self.assertGreater(full, 800)

        # В равной позиции внутри окна должна совпадать с полной оценкой.
        b_eq = chess.Board()
        self.assertEqual(evaluate(b_eq, alpha=-500, beta=500), evaluate(b_eq))


class TestSEE(unittest.TestCase):
    def test_see_losing_and_winning_captures(self):
        # Ладья бьёт слона, защищённого ферзём: 330 - 500 = -170
        b = chess.Board("3qk3/8/8/3b4/8/8/8/3RK3 w - - 0 1")
        self.assertEqual(_see(b, chess.Move.from_uci("d1d5")), -170)

        # Ферзь бьёт ладью, защищённую ладьёй: 500 - 900 = -400
        b2 = chess.Board("3rk3/8/8/3r4/8/8/8/3QK3 w - - 0 1")
        self.assertEqual(_see(b2, chess.Move.from_uci("d1d5")), -400)

        # Взятие на проходе: выигрывает пешку (+100)
        b3 = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
        self.assertEqual(_see(b3, chess.Move.from_uci("e5d6")), 100)


class TestSearch(unittest.TestCase):
    def setUp(self):
        # Отключаем Syzygy для чистых тестов алгоритма поиска (Syzygy тестируется отдельно).
        self.tb_off = SyzygyTablebase(path="")
        self.engine = SearchEngine(tablebase=self.tb_off)

    def test_mate_in_one(self):
        board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")
        lines, score, _ = self.engine.search(board, time_limit=1.0, max_depth=3, use_book=False)
        self.assertTrue(lines)
        self.assertEqual(lines[0][0], chess.Move.from_uci("d1d8"))
        self.assertGreaterEqual(score, MATE_SCORE)

    def test_mate_in_two(self):
        board = chess.Board("r1bqkb1r/ppppp1pp/2n2n2/5p2/4P3/2N5/PPPPQPPP/R1B1KBNR w KQkq - 0 1")
        # Классический мат в 2: Qh5+ g6 (или Kd8) -> ...; возьмём чистый мат в 2:
        board = chess.Board("r2qkb1r/pp2nppp/3p4/2pNN1B1/2BnP3/3P4/PPP2PPP/R2bK2R w KQkq - 1 1")
        lines, score, _ = self.engine.search(board, time_limit=2.0, max_depth=5, use_book=False)
        self.assertTrue(lines)
        self.assertEqual(lines[0][0], chess.Move.from_uci("d5f6"))
        self.assertGreaterEqual(score, MATE_SCORE)

    def test_check_extension_no_explosion(self):
        board = chess.Board("8/8/8/8/8/4k3/4Q3/4K3 w - - 0 1")
        lines, score, depth = self.engine.search(board, time_limit=0.8, use_book=False)
        self.assertTrue(lines)
        self.assertGreaterEqual(depth, 4)
        self.assertGreater(score, 500)

    def test_repetition_at_root_still_searches(self):
        board = chess.Board()
        for uci in ("g1f3", "g8f6", "f3g1", "f6g8"):
            board.push_uci(uci)
        lines, _, depth = self.engine.search(board, time_limit=0.5, use_book=False)
        self.assertTrue(lines)
        self.assertGreaterEqual(depth, 3)
        self.assertGreater(self.engine.nodes_searched, 100)

    def test_multipv_distinct_ordered(self):
        board = chess.Board("r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQ1RK1 b kq - 5 4")
        lines, _, depth = self.engine.search(board, time_limit=0.8, max_depth=4, use_book=False, multipv=3)
        self.assertEqual(len(lines), 3)
        moves = [m for m, _ in lines]
        self.assertEqual(len(set(moves)), 3)
        self.assertGreaterEqual(lines[0][1], lines[1][1])
        self.assertGreaterEqual(depth, 2)

    def test_timeout_respected(self):
        board = chess.Board()
        t0 = time.time()
        lines, _, depth = self.engine.search(board, time_limit=0.3, use_book=False)
        elapsed = time.time() - t0
        self.assertTrue(lines)
        self.assertGreaterEqual(depth, 1)
        self.assertLess(elapsed, 0.75)


class TestStrengthAndSyzygy(unittest.TestCase):
    def test_strength_levels(self):
        for lv in range(1, strength_levels.MAX_LEVEL + 1):
            s = strength_levels.get(lv)
            self.assertEqual(s.level, lv)
            self.assertTrue(strength_levels.describe(lv))
        self.assertEqual(strength_levels.level_for_elo(2600), 10)
        self.assertEqual(strength_levels.level_for_elo(400), 1)
        self.assertEqual(strength_levels.level_for_elo(761), 2)

    def test_syzygy_3_and_4_piece_probing(self):
        tb = SyzygyTablebase()
        self.assertTrue(tb.is_available, "Встроенный каталог syzygy/ должен быть доступен")
        self.assertGreaterEqual(tb.max_pieces, 4)

        # 3 фигуры: KQvK — гарантированная победа белых
        b3 = chess.Board("8/8/8/4k3/8/4K3/4Q3/8 w - - 0 1")
        res3 = tb.probe_root(b3)
        self.assertIsNotNone(res3)
        self.assertEqual(res3[1], TB_WIN_SCORE)
        self.assertIn(res3[0], b3.legal_moves)

        # 4 фигуры: KRPvK — гарантированная победа белых
        b4 = chess.Board("8/8/8/4k3/8/4K3/4P3/7R w - - 0 60")
        res4 = tb.probe_root(b4)
        self.assertIsNotNone(res4)
        self.assertEqual(res4[1], TB_WIN_SCORE)
        self.assertIn(res4[0], b4.legal_moves)
        tb.close()


class TestUCI(unittest.TestCase):
    def test_uci_protocol_end_to_end(self):
        uci_path = os.path.join(SRC, "uci.py")
        eng = chess.engine.SimpleEngine.popen_uci([sys.executable, uci_path])
        try:
            self.assertEqual(eng.id.get("name"), "PyCaesar")
            self.assertIn("SyzygyPath", eng.options)
            self.assertIn("Skill Level", eng.options)
            self.assertNotIn("Threads", eng.options)

            eng.configure({"Skill Level": 1, "OwnBook": False})
            board = chess.Board()
            res = eng.play(board, chess.engine.Limit(time=0.2), info=chess.engine.INFO_ALL)
            self.assertLessEqual(res.info.get("depth", 1), 1)
            eng.configure({"Skill Level": 10})
            res = eng.play(board, chess.engine.Limit(depth=3))
            self.assertIsNotNone(res.move)
            self.assertIn(res.move, board.legal_moves)
        finally:
            eng.quit()



class TestHumanStyle(unittest.TestCase):
    def test_fast_board_perft_exact(self):
        from engine.fast_board import FastBoard
        def perft(fb, d):
            if d == 0: return 1
            n = 0
            for m in fb.generate_moves():
                if fb.make_move(m):
                    n += perft(fb, d - 1)
                    fb.unmake_move()
            return n
        fb = FastBoard.from_chess(chess.Board())
        self.assertEqual(perft(fb, 3), 8902)
        fb2 = FastBoard.from_chess(chess.Board("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1"))
        self.assertEqual(perft(fb2, 3), 2812)

    def test_knight_outpost_and_rook_seventh(self):
        b_out = chess.Board("4k3/pp4pp/8/4N3/3P4/8/PP4PP/4K3 w - - 0 20")
        b_no = chess.Board("4k3/pp4pp/8/4N3/8/8/PP1P2PP/4K3 w - - 0 20")
        self.assertGreater(evaluate(b_out), evaluate(b_no))

        b_r7 = chess.Board("4k3/pp1R2pp/8/8/8/8/PP4PP/4K3 w - - 0 25")
        b_r3 = chess.Board("4k3/pp4pp/8/8/8/3R4/PP4PP/4K3 w - - 0 25")
        self.assertGreater(evaluate(b_r7), evaluate(b_r3))

    def test_tt_mate_no_int16_overflow(self):
        """Регрессия: матовые оценки ±99xxx в TT не должны переполнять int16_t в ±31078."""
        se = SearchEngine()
        board = chess.Board("rnb1kbnr/pp1ppppp/2p5/8/2P1P3/3P1K2/5qPP/5BNR w - - 0 1")
        lines, score, depth = se.search(board, time_limit=1.0, use_book=False, multipv=2, max_depth=10)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0][0].uci(), "f3f2")
        self.assertLess(lines[0][1], 0)
        self.assertLessEqual(lines[1][1], -MATE_SCORE)


if __name__ == "__main__":
    unittest.main()
