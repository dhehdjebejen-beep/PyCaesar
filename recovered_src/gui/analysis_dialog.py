"""
analysis_dialog.py – Окно анализа партии после игры

Показывает полный разбор партии:
  • Список ходов с оценкой движка
  • Классификация ходов: ✅ отлично, ⚠ неточность, ❌ ошибка, 💀 зевок
  • Общая статистика по качеству игры обеих сторон
  • Параллельный анализ (multiprocessing, max 2 процесса)
  • Неблокирующий GUI (QThread + прогресс)
"""

import traceback
import chess
from concurrent.futures import ProcessPoolExecutor, as_completed
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, 
    QTableWidgetItem, QHeaderView, QProgressBar, QGroupBox, 
    QPushButton, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from engine.engine import ChessEngine
from engine.evaluation import evaluate

BRILLIANT_THRESHOLD = 0
GOOD_THRESHOLD = 30
INACCURACY_THRESHOLD = 80
MISTAKE_THRESHOLD = 200

MOVE_CLASSES = {
    'brilliant': ('✅ Отлично', QColor(76, 175, 80)),
    'good': ('👍 Хорошо', QColor(139, 195, 74)),
    'inaccuracy': ('⚠ Неточность', QColor(255, 193, 7)),
    'mistake': ('❌ Ошибка', QColor(255, 87, 34)),
    'blunder': ('💀 Зевок', QColor(244, 67, 54))
}

def classify_move(cp_loss: int) -> str:
    if cp_loss <= BRILLIANT_THRESHOLD:
        return 'brilliant'
    if cp_loss <= GOOD_THRESHOLD:
        return 'good'
    if cp_loss <= INACCURACY_THRESHOLD:
        return 'inaccuracy'
    if cp_loss <= MISTAKE_THRESHOLD:
        return 'mistake'
    return 'blunder'

def _clamp_for_loss(score: int) -> int:
    """
    Матовые счета (±99xxx) нельзя вычитать друг из друга: разница в 99998 сп
    превращает шкалу потерь в бессмыслицу. Приводим их к конечной величине.
    """
    return max(-2000, min(2000, score))


def _analyze_position(fen: str, move_uci: str, move_index: int, time_limit: float = 1.0) -> dict:
    engine = ChessEngine()
    board = chess.Board(fen)
    move = chess.Move.from_uci(move_uci)
    side = board.turn
    try:
        move_san = board.san(move)
    except Exception:
        move_san = move_uci

    # get_best_move возвращает СПИСОК пар (ход, оценка). Раньше здесь первый
    # элемент кортежа принимали за chess.Move: сравнение best_move == move
    # всегда было ложным, а best_move.uci() кидал AttributeError, который
    # AnalysisWorker молча проглатывал и подставлял заглушку. В итоге таблица
    # рисовала "?" и "Хорошо" для каждого хода партии.
    lines, best_score, depth = engine.get_best_move(board, time_limit=time_limit, use_book=False)
    best_move = lines[0][0] if lines else None

    if best_move is not None and best_move == move:
        cp_loss = 0
        real_move_score = best_score
    else:
        board.push(move)
        _, opp_best_score, _ = engine.get_best_move(board, time_limit=time_limit, use_book=False)
        real_move_score = -opp_best_score
        cp_loss = max(0, _clamp_for_loss(best_score) - _clamp_for_loss(real_move_score))

    move_class = classify_move(cp_loss)
    move_number = move_index // 2 + 1
    side_label = '.' if move_index % 2 == 0 else '...'
    eval_white = real_move_score if side == chess.WHITE else -real_move_score
    
    return {
        'index': move_index,
        'move_num': f"{move_number}{side_label}",
        'move_san': move_san,
        'eval_white': eval_white,
        'best_move': best_move.uci() if best_move is not None else '—',
        'best_score': best_score,
        'cp_loss': cp_loss,
        'move_class': move_class,
        'side': side
    }

class AnalysisWorker(QThread):
    """
    Фоновый поток для анализа партии.

    Использует ProcessPoolExecutor(max_workers=2) для параллельного анализа,
    но ограничивает использование CPU (не более 2 процессов).
    """

    progress_updated = pyqtSignal(int, int)
    analysis_done = pyqtSignal(list)

    _moves: list

    def __init__(self, moves: list, parent=None) -> None:
        super().__init__(parent)
        self._moves = moves

    def run(self) -> None:
        total = len(self._moves)
        results = [None] * total
        
        board = chess.Board()
        tasks = []
        for i, move in enumerate(self._moves):
            fen = board.fen()
            tasks.append((fen, move.uci(), i))
            board.push(move)
            
        completed = 0
        with ProcessPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(_analyze_position, fen, uci, idx, 1.0): idx
                for fen, uci, idx in tasks
            }
            
            for future in as_completed(futures):
                try:
                    result = future.result()
                    idx = result['index']
                    results[idx] = result
                except Exception:
                    # Этот обработчик уже однажды скрыл полностью сломанный
                    # анализ: AttributeError глотался, таблица заполнялась
                    # заглушками и выглядела рабочей. Пусть теперь хотя бы
                    # печатает, что именно упало.
                    traceback.print_exc()
                    idx = futures[future]
                    move_number = idx // 2 + 1
                    side_label = '.' if idx % 2 == 0 else '...'
                    side = chess.WHITE if idx % 2 == 0 else chess.BLACK
                    results[idx] = {
                        'index': idx,
                        'move_num': f"{move_number}{side_label}",
                        'move_san': '?',
                        'eval_white': 0,
                        'best_move': '—',
                        'best_score': 0,
                        'cp_loss': 0,
                        'move_class': 'good',
                        'side': side
                    }
                completed += 1
                self.progress_updated.emit(completed, total)
                
        self.analysis_done.emit(results)

class AnalysisDialog(QDialog):
    """Диалог анализа завершённой партии (неблокирующий)."""

    _moves: list
    _analysis_data: list
    _worker: AnalysisWorker | None
    progress_label: QLabel
    progress_bar: QProgressBar
    white_stats_box: QGroupBox
    black_stats_box: QGroupBox
    table: QTableWidget

    def __init__(self, move_history: list, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("📊 Анализ партии")
        self.setMinimumSize(700, 550)
        self._moves = move_history
        self._analysis_data = []
        self._worker = None
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("📊 Анализ партии")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #e0e0e0;")
        layout.addWidget(title)

        self.progress_label = QLabel("Анализ ходов... (2 параллельных процесса)")
        self.progress_label.setStyleSheet("color: #aaa; font-size: 12px;")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(len(self._moves))
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555; border-radius: 4px;
                background-color: #333; height: 8px;
                text-align: center; color: #aaa;
            }
            QProgressBar::chunk {
                background-color: #4CAF50; border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress_bar)

        stats_layout = QHBoxLayout()
        self.white_stats_box = self._create_stats_group("♔ Белые")
        stats_layout.addWidget(self.white_stats_box)
        self.black_stats_box = self._create_stats_group("♚ Чёрные")
        stats_layout.addWidget(self.black_stats_box)
        layout.addLayout(stats_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(['№', 'Ход', 'Оценка', 'Лучший', 'Потеря', 'Класс'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #2a2a2a; color: #ddd;
                border: 1px solid #444; gridline-color: #3a3a3a;
                font-size: 12px;
            }
            QTableWidget::item:selected { background-color: #3a6b1e; }
            QHeaderView::section {\n                background-color: #1e1e1e; color: #ccc;
                padding: 5px; border: 1px solid #333;
                font-size: 11px; font-weight: bold;
            }
        """)
        # stretch передавался и позиционно, и по ключу -> TypeError прямо в
        # __init__, то есть внутри слота Qt, то есть qFatal и падение всего
        # приложения по кнопке "Анализ партии" и по "Да" в конце партии.
        layout.addWidget(self.table, 1)

        btn_close = QPushButton("Закрыть")
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #D4AF37; color: #121212; border: none;
                border-radius: 6px; padding: 10px 32px;
                font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background-color: #C0A030; }
            QPushButton:pressed { background-color: #B09025; }
        """)
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignCenter)

        self.setStyleSheet("""
            QDialog { background-color: #121212; }
            QLabel { color: #e0e0e0; }
            QGroupBox {
                color: #D4AF37; border: 1px solid #444;
                border-radius: 6px; margin-top: 10px; padding-top: 16px;
                font-weight: bold; background-color: #1e1e1e;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center; padding: 0 8px;
                color: #D4AF37;
            }
            QTableWidget {
                background-color: #1e1e1e; color: #e0e0e0;
                border: 1px solid #444; gridline-color: #333;
                font-size: 12px;
            }
            QTableWidget::item:selected { background-color: #3a6b1e; color: white; }
            QHeaderView::section {
                background-color: #2a2a2a; color: #D4AF37;
                padding: 5px; border: 1px solid #444;
                font-size: 11px; font-weight: bold;
            }
        """)

    def _create_stats_group(self, title: str) -> QGroupBox:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        
        for key in ('brilliant', 'good', 'inaccuracy', 'mistake', 'blunder'):
            label_text, color = MOVE_CLASSES[key]
            lbl = QLabel(f"{label_text}: —")
            lbl.setObjectName(f"{title}_{key}")
            lbl.setStyleSheet(f"color: {color.name()}; font-size: 12px; padding: 2px;")
            layout.addWidget(lbl)
            
        avg_lbl = QLabel("Ср. потеря: —")
        avg_lbl.setObjectName(f"{title}_avg")
        avg_lbl.setStyleSheet("color: #aaa; font-size: 12px; font-weight: bold; padding: 2px;")
        layout.addWidget(avg_lbl)
        
        return box

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._worker is None:
            self._start_analysis()

    def _start_analysis(self) -> None:
        self._worker = AnalysisWorker(self._moves, self)
        self._worker.progress_updated.connect(self._on_progress)
        self._worker.analysis_done.connect(self._on_analysis_done)
        self._worker.start()

    def _on_progress(self, current: int, total: int) -> None:
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Анализ ходов: {current}/{total}...")

    def _on_analysis_done(self, results: list) -> None:
        self._analysis_data = results
        self._fill_table()
        self._fill_stats()
        self.progress_label.setText(f"Анализ завершён: {len(self._moves)} ходов")

    def _fill_table(self) -> None:
        self.table.setRowCount(len(self._analysis_data))
        for i, data in enumerate(self._analysis_data):
            cls_label, cls_color = MOVE_CLASSES[data['move_class']]
            
            # score_to_display печатает маты как «М3», а не как «+999.98».
            eval_text = ChessEngine.score_to_display(data['eval_white'])
            items = [
                data['move_num'],
                data['move_san'],
                eval_text,
                data['best_move'],
                str(data['cp_loss']),
                cls_label
            ]
            
            for col, text in enumerate(items):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                if col == 5:
                    item.setForeground(cls_color)
                elif col == 4 and data['cp_loss'] > GOOD_THRESHOLD:
                    item.setForeground(cls_color)
                    
                self.table.setItem(i, col, item)

    def _fill_stats(self) -> None:
        for side, box_title, box in (
            (chess.WHITE, "♔ Белые", self.white_stats_box),
            (chess.BLACK, "♚ Чёрные", self.black_stats_box)
        ):
            side_data = [d for d in self._analysis_data if d['side'] == side]
            counts = {k: 0 for k in MOVE_CLASSES}
            total_loss = 0
            
            for d in side_data:
                counts[d['move_class']] += 1
                total_loss += d['cp_loss']
                
            avg_loss = total_loss / max(1, len(side_data))
            
            for key in MOVE_CLASSES:
                label_text, _ = MOVE_CLASSES[key]
                lbl = box.findChild(QLabel, f"{box_title}_{key}")
                if lbl:
                    lbl.setText(f"{label_text}: {counts[key]}")
                    
            avg_lbl = box.findChild(QLabel, f"{box_title}_avg")
            if avg_lbl:
                avg_lbl.setText(f"Ср. потеря: {avg_loss:.1f} cp")

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(3000)
        super().closeEvent(event)
