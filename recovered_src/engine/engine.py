"""
engine.py – Главный класс шахматного движка PyCaesar

Обеспечивает единый интерфейс: принимает позицию, возвращает лучший ход.
Делегирует вычисления модулю search.py.

Поддержка:
  • Polyglot дебютная книга (мгновенный ход из book.bin)
  • Эндшпильные базы Syzygy (3–5 фигур, автопоиск в каталоге syzygy/)
  • Регулировка силы (уровни 1–10)
  • Тайм-менеджмент (доля оставшегося времени + инкремент)
"""

import chess
from engine.search import SearchEngine, MATE_SCORE, INFINITY, get_book_move
from engine.evaluation import evaluate, clear_pawn_hash
from engine import strength as strength_levels
from engine.tablebase import SyzygyTablebase


class ChessEngine:
    """
    Шахматный движок PyCaesar (классический поиск + битбордовая оценка + Syzygy).

    Использование:
        engine = ChessEngine()
        board = chess.Board()
        lines, score, depth = engine.get_best_move(board, time_limit=3.0)
        best_move = lines[0][0]
    """

    search_engine: SearchEngine

    def __init__(self, level: int = strength_levels.FULL_STRENGTH, syzygy_path: str | None = None) -> None:
        self.tablebase = SyzygyTablebase(path=syzygy_path)
        self.search_engine = SearchEngine(tablebase=self.tablebase)
        self.search_engine.set_strength(level)

    def stop(self) -> None:
        self.search_engine.stop()

    def close(self) -> None:
        self.tablebase.close()

    def set_strength(self, level: int) -> None:
        """Уровень силы 1..10; 10 — без ограничений."""
        self.search_engine.set_strength(level)

    def set_syzygy_path(self, path: str) -> bool:
        """Подключить каталог с эндшпильными базами Syzygy."""
        return self.tablebase.load(path)

    @property
    def strength(self):
        return self.search_engine.strength

    def get_best_move(
        self,
        board: chess.Board,
        time_limit: float = 3.0,
        use_book: bool = True,
        on_iteration=None,
        multipv: int = 1,
        max_depth: int = 64,
    ) -> tuple[list, int, int]:
        # На слабых уровнях (1–5, noise_cp > 0) не используем гроссмейстерскую
        # дебютную книгу, чтобы бот играл на свой заявленный рейтинг с 1-го хода.
        if use_book and self.strength.noise_cp == 0:
            book_move = get_book_move(board)
            if book_move is not None and book_move in board.legal_moves:
                return [(book_move, 0)], 0, 0

        return self.search_engine.search(
            board,
            time_limit=time_limit,
            use_book=False,
            on_iteration=on_iteration,
            multipv=multipv,
            max_depth=max_depth,
        )

    def get_best_move_with_clock(
        self,
        board: chess.Board,
        remaining_time: float = 300.0,
        increment: float = 0.0,
    ) -> tuple[list, int, int]:
        return self.search_engine.search_with_time_management(
            board, remaining_time=remaining_time, increment=increment
        )

    def get_book_move(self, board: chess.Board) -> chess.Move | None:
        return get_book_move(board)

    def evaluate_position(self, board: chess.Board) -> int:
        return evaluate(board)

    def clear_cache(self) -> None:
        self.search_engine.tt.clear()
        self.search_engine.clear_heuristics()
        clear_pawn_hash()

    @staticmethod
    def score_to_display(score: int) -> str:
        if abs(score) >= MATE_SCORE:
            mate_in = (99999 - abs(score) + 1) // 2
            if score > 0:
                return f"М{mate_in}"
            return f"-М{mate_in}"
        return f"{score / 100:+.2f}"

    @staticmethod
    def score_to_white_perspective(score: int, turn: bool) -> int:
        if turn == chess.WHITE:
            return score
        return -score
