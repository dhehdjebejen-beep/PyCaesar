"""
tablebase.py — Поддержка эндшпильных баз Syzygy (WDL + DTZ) через python-chess.

Автоматически подключает каталог `syzygy/` из корня проекта (или из `_MEIPASS`
в собранном exe). По умолчанию проект поставляется с 3–4-фигурными базами
(~4.4 МБ), а 5-фигурные (~938 МБ) можно докачать скриптом `tools/download_syzygy.py`
или подключить через UCI-опцию `SyzygyPath`.
"""

import os
import sys
import chess
import chess.syzygy

# Оценка из базы Syzygy: заведомо выше любой статической эвристики (~10 000),
# но ниже матовых оценок MATE_SCORE (99 000), чтобы реальный форсированный мат
# всегда предпочитался абстрактной победе по базе.
TB_WIN_SCORE = 20000


def default_syzygy_dir() -> str:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'syzygy')
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, 'syzygy')


def _infer_max_pieces(directory: str) -> int:
    """Определить максимальное число фигур по именам файлов .rtbw в каталоге."""
    max_p = 0
    try:
        for fname in os.listdir(directory):
            if not fname.endswith('.rtbw'):
                continue
            stem = fname[:-5]
            # Имена вида KQvK, KRvKP, KBNvK — число заглавных букв = число фигур.
            n = sum(1 for ch in stem if ch.isupper())
            if n > max_p:
                max_p = n
    except OSError:
        return 0
    return max_p


class SyzygyTablebase:
    """Обёртка над chess.syzygy.Tablebase с безопасной проверкой и DTZ-выбором в корне."""

    def __init__(self, path: str | None = None) -> None:
        self._tb: chess.syzygy.Tablebase | None = None
        self.max_pieces: int = 0
        self.path: str | None = None
        target = path if path is not None else default_syzygy_dir()
        if target and os.path.isdir(target):
            self.load(target)

    @property
    def is_available(self) -> bool:
        return self._tb is not None and self.max_pieces >= 3

    def load(self, path: str) -> bool:
        """Подключить каталог (или несколько путей через os.pathsep) с базами Syzygy."""
        self.close()
        if not path or path.strip() in ('', '<empty>'):
            return False

        dirs = [p.strip() for p in path.split(os.pathsep) if p.strip() and os.path.isdir(p.strip())]
        if not dirs:
            return False

        tb = chess.syzygy.Tablebase()
        max_p = 0
        loaded_any = False
        for d in dirs:
            mp = _infer_max_pieces(d)
            if mp >= 3:
                try:
                    tb.add_directory(d)
                    loaded_any = True
                    if mp > max_p:
                        max_p = mp
                except Exception:
                    pass

        if not loaded_any:
            tb.close()
            return False

        self._tb = tb
        self.max_pieces = max_p
        self.path = os.pathsep.join(dirs)
        return True

    def close(self) -> None:
        if self._tb is not None:
            try:
                self._tb.close()
            except Exception:
                pass
            self._tb = None
        self.max_pieces = 0
        self.path = None

    def can_probe(self, board: chess.Board) -> bool:
        return (
            self._tb is not None
            and board.castling_rights == 0
            and chess.popcount(board.occupied) <= self.max_pieces
        )

    def probe_wdl(self, board: chess.Board, ply: int = 0) -> int | None:
        """
        Быстрая проверка WDL внутри дерева поиска.
        Возвращает оценку с точки зрения стороны, чей ход, либо None.
        """
        if not self.can_probe(board):
            return None
        try:
            wdl = self._tb.get_wdl(board)
        except Exception:
            return None
        if wdl is None:
            return None
        if wdl >= 2:
            return TB_WIN_SCORE - ply
        if wdl <= -2:
            return -TB_WIN_SCORE + ply
        # 0 (ничья), +1 (cursed win), -1 (blessed loss) при учёте правила 50 ходов = 0.
        return 0

    def probe_root(self, board: chess.Board) -> tuple[chess.Move, int] | None:
        """
        Выбор оптимального хода в корне с учётом DTZ (правило 50 ходов).
        При отсутствии .rtbz-файлов корректно откатывается на WDL.
        """
        if not self.can_probe(board):
            return None

        best_move: chess.Move | None = None
        best_key: tuple | None = None
        best_score = 0

        for move in board.legal_moves:
            board.push(move)
            try:
                if board.is_checkmate():
                    # Немедленный мат на доске — лучше любого DTZ.
                    board.pop()
                    return move, 99998

                wdl = self._tb.get_wdl(board)
                if wdl is None:
                    board.pop()
                    return None

                # wdl — с точки зрения соперника после нашего хода, инвертируем.
                our_wdl = -wdl
                dtz = self._tb.get_dtz(board)
                our_dtz = -dtz if dtz is not None else 0

                # Приоритет выбора хода:
                # 1) Выше WDL (2 > 1 > 0 > -1 > -2).
                # 2) В выигранной позиции — минимальный положительный DTZ (быстрее к цели).
                #    Предпочитаем обнуляющие ходы (взятия/ходы пешкой) при равном DTZ.
                # 3) В проигранной позиции — максимальный по модулю отрицательный DTZ (дольше сопротивляться).
                if our_wdl > 0:
                    dtz_rank = -abs(our_dtz) if dtz is not None else 0
                elif our_wdl < 0:
                    dtz_rank = abs(our_dtz) if dtz is not None else 0
                else:
                    dtz_rank = 0

                key = (our_wdl, dtz_rank)
                if best_key is None or key > best_key:
                    best_key = key
                    best_move = move
                    if our_wdl >= 2:
                        best_score = TB_WIN_SCORE
                    elif our_wdl <= -2:
                        best_score = -TB_WIN_SCORE
                    else:
                        best_score = 0
            except Exception:
                board.pop()
                return None
            board.pop()

        if best_move is None:
            return None
        return best_move, best_score
