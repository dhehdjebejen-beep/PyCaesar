import sys
import os
import unittest
sys.path.append(os.path.abspath('recovered_src'))

import chess
from engine.search import SearchEngine

class TestSearchEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SearchEngine()

    def test_mate_in_one(self):
        board = chess.Board("4k3/R7/8/8/8/8/8/4K2R w K - 0 1")
        best_move, score, depth = self.engine.search(board, max_depth=3, time_limit=5.0, use_book=False)
        best_move = best_move[0][0] if isinstance(best_move, list) else best_move
        self.assertEqual(best_move.uci(), "h1h8")
        self.assertGreaterEqual(score, 99000)

    def test_mate_in_two(self):
        board = chess.Board("r1bq2r1/b4pk1/p1pp1p2/1p2pP2/1P2P1PB/3P4/1PPQ2P1/R3K2R w - - 0 1")
        best_move, score, depth = self.engine.search(board, max_depth=5, time_limit=5.0, use_book=False)
        best_move = best_move[0][0] if isinstance(best_move, list) else best_move
        self.assertEqual(best_move.uci(), "d2h6")
        self.assertGreaterEqual(score, 99000)

    def test_timeout_behavior(self):
        board = chess.Board()
        best_move, score, depth = self.engine.search(board, max_depth=10, time_limit=0.01, use_book=False)
        self.assertIsNotNone(best_move)

if __name__ == '__main__':
    unittest.main()
