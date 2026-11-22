"""
engine_worker.py – Рабочий поток для шахматного движка

Использует threading.Thread + QObject-сигналы вместо QThread,
чтобы избежать потенциальных segfault при пересечении границ C++/Python.
"""

import threading
import traceback
import chess
from PyQt6.QtCore import QObject, pyqtSignal
from engine.engine import ChessEngine

class EngineWorker(QObject):
    """
    Обёртка для запуска движка в фоновом потоке (threading.Thread).

    Использование:
        worker = EngineWorker(engine)
        worker.result_ready.connect(handler)
        worker.setup(board, time_limit)
        worker.start()
    """

    result_ready = pyqtSignal(object, int, int, int)
    # Испускается после каждой завершённой итерации углубления, из рабочего потока.
    # (multi_pv, score_белых, depth, nodes, request_id)
    # EngineWorker живёт в главном потоке, поэтому Qt доставляет сигнал через
    # очередь событий — обработчик выполняется в GUI-потоке, это безопасно.
    progress = pyqtSignal(object, int, int, int, int)

    _engine: ChessEngine
    _board: chess.Board | None
    _time_limit: float
    _request_id: int
    _thread: threading.Thread | None

    def __init__(self, engine: ChessEngine, parent=None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._board = None
        self._time_limit = 5.0
        self._request_id = 0
        self._use_book = True
        self._multipv = 1
        self._thread = None

    def setup(self, board: chess.Board, time_limit: float = 5.0, request_id: int = 0,
              use_book: bool = True, multipv: int = 1) -> None:
        self._board = board.copy()
        self._time_limit = time_limit
        self._request_id = request_id
        self._use_book = use_book
        self._multipv = multipv

    def start(self) -> None:
        if self._board is None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def isRunning(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def terminate(self) -> None:
        if self._thread and self._thread.is_alive():
            self._engine.stop()
            self._thread.join(timeout=5.0)
        self._thread = None

    def wait(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        # Snapshot the request under which this thread started: setup() may be
        # called again from the GUI thread while this search is still running,
        # and every emit below has to carry the id it was actually started with.
        board = self._board
        request_id = self._request_id
        use_book = self._use_book
        multipv = self._multipv
        if board is None:
            return

        turn = board.turn

        def _on_iteration(info: dict) -> None:
            multi_pv = [(m, ChessEngine.score_to_white_perspective(s, turn))
                        for m, s in info['multi_pv']]
            white_score = ChessEngine.score_to_white_perspective(info['score'], turn)
            self.progress.emit(multi_pv, white_score, info['depth'], info['nodes'], request_id)

        try:
            # search() теперь всегда отдаёт список пар (ход, оценка) —
            # ветка isinstance здесь больше не нужна.
            lines, score, depth = self._engine.get_best_move(
                board, time_limit=self._time_limit, use_book=use_book,
                on_iteration=_on_iteration, multipv=multipv)
            multi_pv = [(m, ChessEngine.score_to_white_perspective(s, turn)) for m, s in lines]
            if not multi_pv:
                self.result_ready.emit(None, 0, 0, request_id)
                return
            self.result_ready.emit(multi_pv, multi_pv[0][1], depth, request_id)
        except Exception:
            # Полная трассировка: молчаливое "Engine error: ..." в консоль
            # невидимого GUI-приложения диагностировать невозможно.
            traceback.print_exc()
            self.result_ready.emit(None, 0, 0, request_id)
