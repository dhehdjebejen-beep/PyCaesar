"""
engine_worker.py – Рабочий поток для шахматного движка

Использует threading.Thread + QObject-сигналы и потокобезопасную блокировку,
чтобы быстрые ходы в GUI никогда не вызывали гонок между расчётами и не
вешали интерфейс.
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
        worker.setup(board, time_limit, request_id)
        worker.start()
    """

    result_ready = pyqtSignal(object, int, int, int)
    # Испускается после каждой завершённой итерации углубления, из рабочего потока.
    # (multi_pv, score_белых, depth, nodes, request_id)
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
        self._latest_request_id = 0
        self._use_book = True
        self._multipv = 1
        self._thread = None
        self._search_lock = threading.Lock()

    def setup(
        self,
        board: chess.Board,
        time_limit: float = 5.0,
        request_id: int = 0,
        use_book: bool = True,
        multipv: int = 1,
    ) -> None:
        self._board = board.copy()
        self._time_limit = time_limit
        self._request_id = request_id
        self._latest_request_id = request_id
        self._use_book = use_book
        self._multipv = multipv

    def start(self) -> None:
        if self._board is None:
            return
        # Передаём снапшот параметров по значению, чтобы последующий setup()
        # из GUI-потока никогда не подменил данные запускаемого потока.
        args = (
            self._board.copy(),
            float(self._time_limit),
            int(self._request_id),
            bool(self._use_book),
            int(self._multipv),
        )
        self._thread = threading.Thread(target=self._run, args=args, daemon=True)
        self._thread.start()

    def isRunning(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        """Неблокирующая отмена текущего расчёта (мгновенно возвращает управление GUI)."""
        self._latest_request_id = -1
        self._engine.stop()

    def terminate(self) -> None:
        """Остановить текущий расчёт и дождаться выхода потока."""
        self.cancel()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.5)
        self._thread = None

    def wait(self) -> None:
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.5)

    def _run(
        self,
        board: chess.Board,
        time_limit: float,
        request_id: int,
        use_book: bool,
        multipv: int,
    ) -> None:
        turn = board.turn

        def _on_iteration(info: dict) -> None:
            if request_id != self._latest_request_id:
                return
            multi_pv = [
                (m, ChessEngine.score_to_white_perspective(s, turn))
                for m, s in info['multi_pv']
            ]
            white_score = ChessEngine.score_to_white_perspective(info['score'], turn)
            self.progress.emit(multi_pv, white_score, info['depth'], info['nodes'], request_id)

        with self._search_lock:
            # Если пока мы ждали завершения предыдущего расчёта пришёл новый запрос
            # или отмена — выходим сразу, не запуская устаревший поиск.
            if request_id != self._latest_request_id:
                return

            try:
                lines, score, depth = self._engine.get_best_move(
                    board,
                    time_limit=time_limit,
                    use_book=use_book,
                    on_iteration=_on_iteration,
                    multipv=multipv,
                )
                if request_id != self._latest_request_id:
                    return
                multi_pv = [
                    (m, ChessEngine.score_to_white_perspective(s, turn))
                    for m, s in lines
                ]
                if not multi_pv:
                    self.result_ready.emit(None, 0, 0, request_id)
                    return
                self.result_ready.emit(multi_pv, multi_pv[0][1], depth, request_id)
            except Exception:
                traceback.print_exc()
                if request_id == self._latest_request_id:
                    self.result_ready.emit(None, 0, 0, request_id)
