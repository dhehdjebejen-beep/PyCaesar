"""
main_window.py – Главное окно шахматного приложения

Компоненты:
  • Шкала оценки (EvalBar) — слева (toggle: видима/скрыта)
  • Доска (BoardWidget) — центр
  • Список ходов PGN (QTextEdit) — справа
  • Toolbar: ⟵ Назад | ⟶ Вперёд | ↻ Перевернуть | 🔄 Новая | ← Меню
  • Подсказки в ЧвЧ + анализ партии
  • Загрузка позиции из FEN (Board Setup)
"""

import chess
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QStatusBar,
    QMessageBox, QPushButton, QTextEdit, QInputDialog, QDialog, QSlider,
    QDialogButtonBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtGui import QFont, QAction, QIcon
from gui.board_widget import BoardWidget
from gui.eval_bar import EvalBar
from gui.engine_worker import EngineWorker
from gui.start_dialog import MODE_PLAY_WHITE, MODE_PLAY_BLACK, MODE_HVH
from gui.analysis_dialog import AnalysisDialog
from engine.engine import ChessEngine
from gui.utils import get_app_icon

class StepSlider(QSlider):
    """
    Ползунок, который колесо мыши двигает ровно на одно деление.

    QAbstractSlider по умолчанию прокручивает на singleStep, умноженный на
    системное «строк за щелчок колеса» (обычно 3). Из-за этого уровень силы
    прыгал 10 → 7 → 4, минуя промежуточные значения.
    """

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            event.ignore()
            return
        step = self.singleStep() if delta > 0 else -self.singleStep()
        self.setValue(self.value() + step)
        event.accept()


class MainWindow(QMainWindow):
    """Главное окно шахматного приложения."""
    back_to_menu = pyqtSignal()
    # Значения по умолчанию; всё это меняется в «⚙ Настройки движка».
    DEFAULT_TIME = 3.0          # секунд на ход, диапазон 0.1..10.0
    DEFAULT_LEVEL = 10          # 10 — полная сила

    # Режимы подсказок в «человек против человека».
    ANALYSIS_OFF = 0
    ANALYSIS_BOTH = 1
    ANALYSIS_WHITE = 2
    ANALYSIS_BLACK = 3

    def __init__(self, game_mode: str, parent=None):
        super().__init__(parent)
        self.game_mode = game_mode
        self.board = chess.Board()
        # Начальная позиция ПАРТИИ: нужна для правильной нотации в списке ходов,
        # если партия начата не со стандартной расстановки.
        self.start_fen = chess.STARTING_FEN
        self.engine_time_limit = self.DEFAULT_TIME
        self.engine_level = self.DEFAULT_LEVEL
        # Режим подсказок живёт здесь, а не в виджете: сам выпадающий
        # список теперь показывается только внутри диалога настроек.
        self.analysis_mode = self.ANALYSIS_OFF
        self.engine = ChessEngine()
        self.hint_time_limit = self.DEFAULT_TIME
        self.move_history = []
        self.redo_stack = []
        self.human_color = None

        if game_mode == MODE_PLAY_WHITE:
            self.human_color = chess.WHITE
        elif game_mode == MODE_PLAY_BLACK:
            self.human_color = chess.BLACK

        self._init_ui()
        self._init_menu()
        self._init_engine_worker()

        if game_mode == MODE_PLAY_BLACK:
            self.board_widget.set_flipped(True)

        if game_mode in (MODE_PLAY_WHITE, MODE_PLAY_BLACK):
            self.eval_bar.setVisible(False)
            self._eval_visible = False
        else:
            # В ЧвЧ движок по умолчанию выключен, значит и шкалы быть не должно.
            self._eval_visible = self._engine_output_allowed()
            self.eval_bar.setVisible(self._eval_visible)

        self._update_eval_bar(0)
        self._update_pgn()
        self._apply_analysis_mode()

        if game_mode == MODE_PLAY_BLACK:
            QTimer.singleShot(300, self._start_engine_thinking)
        elif game_mode == MODE_HVH:
            QTimer.singleShot(300, self._start_hint_analysis)

    def _init_ui(self):
        self.setWindowTitle('♚ PyCaesar')
        self.setMinimumSize(850, 600)
        self.setWindowIcon(get_app_icon())

        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        self.eval_bar = EvalBar()
        main_layout.addWidget(self.eval_bar)

        center_layout = QVBoxLayout()

        self.board_widget = BoardWidget(self.board)
        self.board_widget.move_made.connect(self._on_human_move)
        center_layout.addWidget(self.board_widget, stretch=1)

        self.info_label = QLabel('')
        self.info_label.setFont(QFont('Segoe UI', 10))
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setStyleSheet('color: #bbb; padding: 4px;')
        self.info_label.setWordWrap(True)
        center_layout.addWidget(self.info_label)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(6)

        btn_style = """
            QPushButton {
                background-color: #3a3a3a;
                color: #ddd;
                border: 1px solid #555;
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 13px;
                font-weight: 600;
                font-family: 'Segoe UI';
                min-width: 36px;
            }
            QPushButton:hover { background-color: #4a4a4a; border-color: #777; }
            QPushButton:pressed { background-color: #2a2a2a; }
            QPushButton:disabled { background-color: #2a2a2a; color: #555; border-color: #333; }
        """

        btn_green_style = btn_style.replace('background-color: #3a3a3a', 'background-color: #D4AF37')\
                                   .replace('background-color: #4a4a4a', 'background-color: #C0A030')\
                                   .replace('background-color: #2a2a2a', 'background-color: #B09025') + 'QPushButton { color: #121212; font-weight: bold; }'

        btn_red_style = btn_style.replace('background-color: #3a3a3a', 'background-color: #CF6679')\
                                 .replace('background-color: #4a4a4a', 'background-color: #E0778A')\
                                 .replace('background-color: #2a2a2a', 'background-color: #B05566') + 'QPushButton { color: #121212; font-weight: bold; }'

        self.btn_undo = QPushButton('⟵')
        self.btn_undo.setStyleSheet(btn_style)
        self.btn_undo.setToolTip('Отменить ход (Ctrl+Z)')
        self.btn_undo.clicked.connect(self._undo_move)
        toolbar_layout.addWidget(self.btn_undo)

        self.btn_redo = QPushButton('⟶')
        self.btn_redo.setStyleSheet(btn_style)
        self.btn_redo.setToolTip('Повторить ход (Ctrl+Y)')
        self.btn_redo.clicked.connect(self._redo_move)
        toolbar_layout.addWidget(self.btn_redo)

        self.btn_eval_toggle = QPushButton('📊')
        self.btn_eval_toggle.setStyleSheet(btn_style)
        self.btn_eval_toggle.setToolTip('Показать/скрыть шкалу оценки')
        self.btn_eval_toggle.clicked.connect(self._toggle_eval_bar)
        toolbar_layout.addWidget(self.btn_eval_toggle)

        self.btn_settings = QPushButton('⚙')
        self.btn_settings.setStyleSheet(btn_style)
        self.btn_settings.setToolTip('Настройки движка: время на ход, уровень силы, подсказки')
        self.btn_settings.clicked.connect(self._show_settings)
        toolbar_layout.addWidget(self.btn_settings)

        self.btn_new = QPushButton('🔄 Новая')
        self.btn_new.setStyleSheet(btn_green_style)
        self.btn_new.setToolTip('Начать новую партию (Ctrl+N)')
        self.btn_new.clicked.connect(self._new_game)
        toolbar_layout.addWidget(self.btn_new)

        # Выпадающий список режима подсказок раньше жил здесь, в панели. После
        # того как в него добавился пункт «Движок выключен», он растянулся на
        # пол-экрана и сжал доску. Это настройка, а не действие — она переехала
        # в «⚙ Настройки движка», а панель осталась короткой.
        toolbar_layout.addStretch(1)

        self.btn_menu = QPushButton('← Меню')
        self.btn_menu.setStyleSheet(btn_red_style)
        self.btn_menu.setToolTip('Вернуться в меню выбора режима')
        self.btn_menu.clicked.connect(self._go_back_to_menu)
        toolbar_layout.addWidget(self.btn_menu)

        center_layout.addLayout(toolbar_layout)
        main_layout.addLayout(center_layout, stretch=1)

        right_panel = QVBoxLayout()
        right_panel.setSpacing(4)

        pgn_title = QLabel('📋 Ходы')
        pgn_title.setFont(QFont('Segoe UI', 11, QFont.Weight.Bold))
        pgn_title.setStyleSheet('color: #ccc; padding: 4px;')
        right_panel.addWidget(pgn_title)

        self.pgn_text = QTextEdit()
        self.pgn_text.setReadOnly(True)
        self.pgn_text.setFont(QFont('Consolas', 11))
        self.pgn_text.setMinimumWidth(180)
        self.pgn_text.setMaximumWidth(250)
        self.pgn_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 8px;
                selection-background-color: #3a6b1e;
            }
        """)
        right_panel.addWidget(self.pgn_text, stretch=1)

        main_layout.addLayout(right_panel)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._update_status('Готово')

        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QWidget { background-color: #121212; color: #e0e0e0; }
            QStatusBar {
                background-color: #1e1e1e;
                color: #aaa; font-size: 11px;
            }
            QMenuBar {
                background-color: #1e1e1e;
                color: #ddd; font-size: 12px;
            }
            QMenuBar::item:selected { background-color: #333; }
            QMenu {
                background-color: #1e1e1e;
                color: #ddd; border: 1px solid #444;
            }
            QMenu::item:selected { background-color: #D4AF37; color: #121212; }
        """)

        self._update_buttons()
        # Настройки движка нужны в любом режиме, а не только в ЧвЧ:
        # время на ход и уровень силы влияют и на игру против бота.
        self.btn_settings.setVisible(True)

    def _init_menu(self):
        menu_bar = self.menuBar()
        game_menu = menu_bar.addMenu('Игра')

        new_game_action = QAction('Новая игра', self)
        new_game_action.setShortcut('Ctrl+N')
        new_game_action.triggered.connect(self._new_game)
        game_menu.addAction(new_game_action)

        game_menu.addSeparator()

        undo_action = QAction('Отменить ход', self)
        undo_action.setShortcut('Ctrl+Z')
        undo_action.triggered.connect(self._undo_move)
        game_menu.addAction(undo_action)

        redo_action = QAction('Повторить ход', self)
        redo_action.setShortcut('Ctrl+Y')
        redo_action.triggered.connect(self._redo_move)
        game_menu.addAction(redo_action)

        game_menu.addSeparator()

        fen_action = QAction('Загрузить FEN...', self)
        fen_action.setShortcut('Ctrl+L')
        fen_action.triggered.connect(self._load_fen)
        game_menu.addAction(fen_action)

        game_menu.addSeparator()

        analysis_action = QAction('Анализ партии', self)
        analysis_action.setShortcut('Ctrl+A')
        analysis_action.triggered.connect(self._show_analysis)
        game_menu.addAction(analysis_action)

        game_menu.addSeparator()

        back_to_menu_action = QAction('Назад в меню', self)
        back_to_menu_action.setShortcut('Ctrl+M')
        back_to_menu_action.triggered.connect(self._go_back_to_menu)
        game_menu.addAction(back_to_menu_action)

        game_menu.addSeparator()

        quit_action = QAction('Выход', self)
        quit_action.setShortcut('Ctrl+Q')
        quit_action.triggered.connect(self.close)
        game_menu.addAction(quit_action)

        view_menu = menu_bar.addMenu('Вид')

        flip_action = QAction('Перевернуть доску', self)
        flip_action.setShortcut('Ctrl+F')
        flip_action.triggered.connect(self._flip_board)
        view_menu.addAction(flip_action)

        toggle_eval_action = QAction('Показать/скрыть оценку', self)
        toggle_eval_action.setShortcut('Ctrl+E')
        toggle_eval_action.triggered.connect(self._toggle_eval_bar)
        view_menu.addAction(toggle_eval_action)

    def _init_engine_worker(self):
        self.engine_worker = EngineWorker(self.engine, self)
        self.engine_worker.result_ready.connect(self._on_engine_result)
        self.engine_worker.progress.connect(self._on_engine_progress)
        self._engine_thinking = False
        self._engine_request_id = 0

        self._hint_engine = ChessEngine()
        self._apply_engine_settings()
        self.hint_worker = EngineWorker(self._hint_engine, self)
        self.hint_worker.result_ready.connect(self._on_hint_result)
        self.hint_worker.progress.connect(self._on_hint_progress)
        self._hint_thinking = False
        self._hint_request_id = 0
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self._launch_hint_analysis)

    def _update_buttons(self):
        can_undo = len(self.move_history) > 0 and not self._engine_thinking
        can_redo = len(self.redo_stack) > 0 and not self._engine_thinking
        self.btn_undo.setEnabled(can_undo)
        self.btn_redo.setEnabled(can_redo)

    def _undo_move(self):
        if not self.move_history or self._engine_thinking:
            return

        move = self.move_history.pop()
        self.redo_stack.append(move)
        self.board.pop()

        if self.game_mode != MODE_HVH and self.move_history:
            engine_move = self.move_history.pop()
            self.redo_stack.append(engine_move)
            self.board.pop()

        self.board_widget.set_board(self.board)
        self.board_widget.set_last_move(self.move_history[-1] if self.move_history else None)
        self.board_widget.set_best_move(None)
        self.board_widget.unlock_input()

        # Removed self.engine.clear_cache() to retain Transposition Table and improve calculation depth
        self._refresh_eval()
        self._update_pgn()
        self._update_buttons()
        self._update_status('Ход отменён')

        if self.game_mode == MODE_HVH:
            if not self.board.is_game_over():
                self._start_hint_analysis()

    def _redo_move(self):
        if not self.redo_stack or self._engine_thinking:
            return

        move = self.redo_stack.pop()
        self.move_history.append(move)
        self.board.push(move)

        if self.game_mode != MODE_HVH and self.redo_stack:
            engine_move = self.redo_stack.pop()
            self.move_history.append(engine_move)
            self.board.push(engine_move)

        self.board_widget.set_board(self.board)
        self.board_widget.set_last_move(move)
        self.board_widget.set_best_move(None)

        # Removed self.engine.clear_cache() to retain Transposition Table and improve calculation depth
        self._refresh_eval()
        self._update_pgn()
        self._update_buttons()
        self._update_status('Ход повторён')

        if self._check_game_over():
            return

        if self.game_mode == MODE_HVH:
            self._start_hint_analysis()

    def _flip_board(self):
        self.board_widget.set_flipped(not self.board_widget.flipped)

    def _toggle_eval_bar(self):
        if not self._engine_output_allowed():
            return
        self._eval_visible = not self._eval_visible
        self.eval_bar.setVisible(self._eval_visible)
        if self._eval_visible:
            self._refresh_eval()

    def _show_settings(self):
        """Настройки движка: время на ход, уровень силы и режим подсказок."""
        from engine import strength as strength_levels

        dialog = QDialog(self)
        dialog.setWindowTitle('⚙ Настройки движка')
        dialog.setMinimumWidth(430)
        dialog.setStyleSheet("""
            QDialog { background-color: #1e1e1e; }
            QLabel { color: #e0e0e0; font-family: 'Segoe UI'; }
            QSlider::groove:horizontal {
                height: 6px; background: #444; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #D4AF37; width: 16px; height: 16px;
                margin: -5px 0; border-radius: 8px;
            }
            QSlider::sub-page:horizontal { background: #D4AF37; border-radius: 3px; }
            QDialogButtonBox QPushButton {
                background-color: #D4AF37; color: #121212;
                border: none; border-radius: 6px;
                padding: 7px 24px; font-weight: bold;
            }
            QDialogButtonBox QPushButton:hover { background-color: #C0A030; }
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(6)

        def section(text):
            lbl = QLabel(text)
            lbl.setStyleSheet('font-size: 13px; font-weight: bold; color: #D4AF37; padding-top: 8px;')
            layout.addWidget(lbl)

        def caption(text):
            lbl = QLabel(text)
            lbl.setStyleSheet('font-size: 10px; color: #888;')
            lbl.setWordWrap(True)
            layout.addWidget(lbl)
            return lbl

        def make_slider(lo, hi, value, tick=1):
            s = StepSlider(Qt.Orientation.Horizontal)
            s.setMinimum(lo)
            s.setMaximum(hi)
            s.setValue(value)
            # singleStep 1 плюс переопределённый wheelEvent: одно деление
            # на щелчок колеса. pageStep нужен для клавиш PageUp/PageDown.
            s.setSingleStep(1)
            s.setPageStep(max(1, (hi - lo) // 10))
            s.setTickInterval(tick)
            s.setTickPosition(QSlider.TickPosition.TicksBelow)
            layout.addWidget(s)
            return s

        # --- время на ход -------------------------------------------------
        section('Время на ход')
        time_val = QLabel()
        time_val.setStyleSheet('font-size: 12px; color: #ccc;')
        layout.addWidget(time_val)
        # Шаг 0.1 с, а не 1 с: для пули и блица секунда на ход — это уже много,
        # там нужны 0.2-0.5 с. Слайдер хранит десятые доли секунды.
        time_slider = make_slider(1, 100, max(1, int(round(self.engine_time_limit * 10))), tick=10)
        caption('От 0.1 до 10.0 секунд. Движок расходует бюджет целиком: '
                'чем больше времени, тем глубже расчёт. Для пули ставьте 0.2-0.5 с, '
                'для блица 1-2 с, для игры без часов 3-5 с.')

        # --- уровень силы -------------------------------------------------
        section('Уровень силы')
        level_val = QLabel()
        level_val.setStyleSheet('font-size: 12px; color: #ccc;')
        layout.addWidget(level_val)
        level_slider = make_slider(1, strength_levels.MAX_LEVEL, self.engine_level)
        caption('Слабые уровни не «ошибаются случайно»: у них меньше глубина, '
                'короче добор разменов (поэтому они зевают фигуры, как человек) '
                'и добавлен разброс при выборе между близкими ходами. '
                'Найденный мат бот не теряет ни на одном уровне.')

        # --- подсказки в ЧвЧ (переехало сюда из панели) ----------------------
        hints_box = None
        if self.game_mode == MODE_HVH:
            section('Подсказки движка')
            hints_box = QComboBox()
            hints_box.addItems([
                "🚫 Движок выключен",
                "🔎 Анализ: обе стороны",
                "🔎 Анализ: только белые",
                "🔎 Анализ: только чёрные",
            ])
            hints_box.setCurrentIndex(self.analysis_mode)
            hints_box.setStyleSheet("""
                QComboBox {
                    background-color: #2a2a2a; color: #e0e0e0;
                    border: 1px solid #555; border-radius: 4px;
                    padding: 6px 10px;
                }
                QComboBox QAbstractItemView {
                    background-color: #2a2a2a; color: #e0e0e0;
                    selection-background-color: #D4AF37; selection-color: #121212;
                }
            """)
            layout.addWidget(hints_box)
            caption('Когда движок выключен, он не считает вообще: ни стрелок, '
                    'ни шкалы оценки, ни нагрузки на процессор. Для партии двоих '
                    'за одним экраном это и нужно.')

        def refresh(*_):
            t = time_slider.value() / 10.0
            time_val.setText(f'⏱ {t:.1f} сек')
            lv = level_slider.value()
            level_val.setText('💪 ' + strength_levels.describe(lv))

        for s in (time_slider, level_slider):
            s.valueChanged.connect(refresh)
        refresh()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addSpacing(8)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.engine_time_limit = time_slider.value() / 10.0
            self.hint_time_limit = time_slider.value() / 10.0
            self.engine_level = level_slider.value()
            self._apply_engine_settings()
            if hints_box is not None and hints_box.currentIndex() != self.analysis_mode:
                self._set_analysis_mode(hints_box.currentIndex())
            self._update_status(
                f'⚙ {self.engine_time_limit:.1f} с/ход  •  уровень {self.engine_level}'
            )

    def _san_of_last_move(self) -> str:
        """Последний ход в человеческой нотации («Nf3»), а не в UCI («g1f3»)."""
        if not self.move_history:
            return ''
        board = chess.Board(self.start_fen)
        for move in self.move_history[:-1]:
            board.push(move)
        return board.san(self.move_history[-1])

    def _apply_engine_settings(self):
        """Передать текущие настройки обоим экземплярам движка."""
        self.engine.set_strength(self.engine_level)
        # Движок подсказок в ЧвЧ всегда работает на полной силе и отдаёт ровно 2 линии
        # (лучший ход + альтернатива), чтобы при понижении уровня бота доску не заваливало 4-6 стрелками.

    def _go_back_to_menu(self):
        if self.move_history:
            reply = QMessageBox.question(
                self,
                'Назад в меню',
                'Текущая партия будет потеряна.\nВернуться в меню?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._stop_hint_analysis()
        self._engine_request_id += 1
        if self.engine_worker.isRunning():
            self.engine_worker.terminate()

        self._engine_thinking = False
        self.back_to_menu.emit()
        self.close()

    def _load_fen(self):
        fen, ok = QInputDialog.getText(self, 'Загрузить FEN', 'Введите FEN-строку позиции:', text=self.board.fen())
        if not ok or not fen.strip():
            return

        try:
            new_board = chess.Board(fen.strip())
        except ValueError:
            QMessageBox.warning(
                self,
                'Ошибка FEN',
                'Неверный формат FEN-строки.\n\nПример: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
            )
            return

        self._stop_hint_analysis()
        self._engine_request_id += 1
        if self.engine_worker.isRunning():
            self.engine_worker.terminate()

        self._engine_thinking = False

        self.board = new_board
        self.start_fen = new_board.fen()
        self.engine.clear_cache()
        self.move_history.clear()
        self.redo_stack.clear()

        self.board_widget.set_board(self.board)
        self.board_widget.set_last_move(None)
        self.board_widget.set_best_move(None)
        self.board_widget.unlock_input()

        self._refresh_eval()
        self._update_pgn()
        self._update_buttons()
        self._update_status('Позиция загружена из FEN')
        self.info_label.setText('📋 Позиция загружена из FEN')

        if self.game_mode == MODE_PLAY_WHITE and self.board.turn == chess.BLACK:
            self._start_engine_thinking()
            return
        elif self.game_mode == MODE_PLAY_BLACK and self.board.turn == chess.WHITE:
            self._start_engine_thinking()
            return
        elif self.game_mode == MODE_HVH:
            self._start_hint_analysis()
            return

    def _refresh_eval(self):
        # При выключенном движке не считаем оценку вообще. Иначе «выключен»
        # означало бы только «без стрелок»: шкала выдавала бы ту же самую
        # подсказку, просто числом.
        if not self._engine_output_allowed():
            return
        static_eval = self.engine.evaluate_position(self.board)
        white_score = ChessEngine.score_to_white_perspective(static_eval, self.board.turn)
        self._update_eval_bar(white_score)

    def _update_pgn(self):
        """
        Список ходов от НАЧАЛЬНОЙ ПОЗИЦИИ ПАРТИИ, а не от стандартной расстановки.

        Раньше здесь всегда стояло chess.Board(), поэтому после «Загрузить FEN»
        нотация считалась относительно другой позиции: для партии, начатой с
        FEN с ходом чёрных, выводилось «1. Bxc5» — взятие, которого нет,
        неверный номер хода и неверная сторона.
        """
        if not self.move_history:
            self.pgn_text.setPlainText('')
            return

        temp_board = chess.Board(self.start_fen)
        lines = []
        for i, move in enumerate(self.move_history):
            white_to_move = temp_board.turn == chess.WHITE
            move_num = temp_board.fullmove_number
            try:
                san = temp_board.san(move)
            except (ValueError, AssertionError):
                # История разошлась с позицией — показываем UCI, но не падаем.
                san = move.uci()
            if white_to_move:
                lines.append(f'{move_num}. {san}')
            elif not lines:
                # Партия начата с позиции, где ход чёрных: «4... Bc5».
                lines.append(f'{move_num}... {san}')
            else:
                lines[-1] += f'  {san}'
            temp_board.push(move)

        self.pgn_text.setPlainText('\n'.join(lines))
        scrollbar = self.pgn_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_human_move(self, move: chess.Move):
        if move not in self.board.legal_moves:
            return

        self.board_widget.lock_input()
        self.redo_stack.clear()
        self.move_history.append(move)
        self.board.push(move)

        self.board_widget.set_board(self.board)
        self.board_widget.set_last_move(move)
        self.board_widget.set_best_move(None)

        self._refresh_eval()
        self._update_pgn()
        self._update_buttons()

        if self._check_game_over():
            return

        if self.game_mode == MODE_HVH:
            self.board_widget.unlock_input()
            self._start_hint_analysis()
            return

        self._start_engine_thinking()

    def _start_engine_thinking(self):
        if self._engine_thinking:
            return

        self._engine_thinking = True
        self.board_widget.lock_input()
        self._update_buttons()
        self._update_status('Движок думает...')
        self.info_label.setText('🤔 Движок анализирует позицию...')

        self._engine_request_id += 1
        self.engine_worker.setup(self.board, self.engine_time_limit, self._engine_request_id)
        self.engine_worker.start()

    def _engine_output_allowed(self) -> bool:
        """
        Разрешён ли ЛЮБОЙ вывод движка на экран.

        В ЧвЧ с выключенным движком это False, и тогда молчит всё: стрелки,
        шкала оценки и строка состояния. Скрыть только стрелки недостаточно —
        шкала показывает ту же информацию, просто числом.
        """
        if self.game_mode != MODE_HVH:
            return True
        return self.analysis_mode != self.ANALYSIS_OFF

    def _stop_hint_analysis(self):
        """Мгновенно и без блокировки GUI отменить текущий расчёт подсказок."""
        self._hint_timer.stop()
        self._hint_request_id += 1
        self._hint_thinking = False
        self.hint_worker.cancel()

    def _apply_analysis_mode(self):
        """Привести интерфейс в соответствие с выбранным режимом подсказок."""
        allowed = self._engine_output_allowed()

        if not allowed:
            self._stop_hint_analysis()
            self.board_widget.set_best_move(None)
            self._eval_visible = False
            self.eval_bar.setVisible(False)
            self.info_label.setText('')
            self._update_status('Движок выключен — играют двое')

        # Шкалу нельзя включить вручную при выключенном движке: она показывала
        # бы его же оценку, то есть «выключен» перестал бы что-то значить.
        self.btn_eval_toggle.setEnabled(allowed)
        self.btn_eval_toggle.setToolTip(
            'Показать/скрыть шкалу оценки' if allowed
            else 'Недоступно: движок выключен'
        )

    def _set_analysis_mode(self, index: int):
        if self.game_mode != MODE_HVH:
            return
        self.analysis_mode = index

        self._stop_hint_analysis()
        self.board_widget.set_best_move(None)
        self.info_label.setText('')

        self._apply_analysis_mode()
        if not self._engine_output_allowed():
            return

        self._eval_visible = True
        self.eval_bar.setVisible(True)
        self._refresh_eval()
        self._start_hint_analysis()

    def _start_hint_analysis(self):
        # Всегда сначала отменяем предыдущий расчёт и сбрасываем флаг,
        # иначе быстрые ходы или смена стороны при ANALYSIS_WHITE/BLACK
        # могли оставить _hint_thinking == True навсегда.
        self._stop_hint_analysis()

        if self.game_mode == MODE_HVH:
            idx = self.analysis_mode
            if idx == self.ANALYSIS_OFF:
                return
            if idx == self.ANALYSIS_WHITE and self.board.turn == chess.BLACK:
                self.info_label.setText('')
                self._update_status('Ход чёрных (подсказки только для белых)')
                return
            if idx == self.ANALYSIS_BLACK and self.board.turn == chess.WHITE:
                self.info_label.setText('')
                self._update_status('Ход белых (подсказки только для чёрных)')
                return

        if self.board.is_game_over():
            return

        # Небольшой debounce (40 мс) гарантирует мгновенный отклик при серии
        # быстрых ходов подряд и даёт старому потоку <1 мс на чистый выход.
        self._hint_timer.start(40)

    def _launch_hint_analysis(self):
        if self.game_mode == MODE_HVH:
            idx = self.analysis_mode
            if idx == self.ANALYSIS_OFF:
                return
            if idx == self.ANALYSIS_WHITE and self.board.turn == chess.BLACK:
                return
            if idx == self.ANALYSIS_BLACK and self.board.turn == chess.WHITE:
                return

        if self.board.is_game_over():
            return

        self._hint_thinking = True
        side = 'белых' if self.board.turn == chess.WHITE else 'чёрных'
        self._update_status(f'Анализ позиции для {side}...')

        self._hint_request_id += 1
        self.hint_worker.setup(self.board, self.hint_time_limit, self._hint_request_id, use_book=False, multipv=2)
        self.hint_worker.start()

    def _on_engine_progress(self, multi_pv: list, score: int, depth: int, nodes: int, request_id: int):
        """
        Промежуточный результат итерации, пока движок ещё думает.
        Ход НЕ показываем: в режиме игры это подсказка о том, что сейчас последует.
        Оценку показываем только если шкала включена — в игровых режимах она
        по умолчанию скрыта, и это осознанное решение, ломать его не нужно.
        """
        if request_id != self._engine_request_id or not self._engine_thinking:
            return

        # Глубина и оценка — это мнение движка о позиции. Пока вы играете
        # ПРОТИВ него, показывать их значит играть с подсказкой: шкалу мы
        # прячем, а тот же самый смысл текстом оставлять нелогично.
        # Кнопка «📊» служит явным согласием их видеть.
        if self._eval_visible:
            self._update_eval_bar(score)
            line = (f'🤔 Движок думает…  глубина {depth}'
                    f'  •  оценка {ChessEngine.score_to_display(score)}')
            self._update_status(f'Движок думает… глубина {depth}')
        else:
            line = '🤔 Движок думает…'
            self._update_status('Движок думает…')
        self.info_label.setText(line)

    def _on_hint_progress(self, multi_pv: list, score: int, depth: int, nodes: int, request_id: int):
        """В режиме анализа наоборот: показываем всё, включая стрелки, вживую."""
        if request_id != self._hint_request_id or not self._hint_thinking:
            return
        if not multi_pv:
            return

        best_move = multi_pv[0][0]
        if best_move not in self.board.legal_moves:
            return

        self.board_widget.set_best_move(multi_pv[:2])
        self._update_eval_bar(score)

        display = ChessEngine.score_to_display(score)
        side = 'белых' if self.board.turn == chess.WHITE else 'чёрных'
        self.info_label.setText(
            f'🔎 Анализ для {side}: {best_move.uci()}  •  оценка {display}  •  глубина {depth}'
        )
        self._update_status(f'Анализ… глубина {depth} | {display}')

    def _on_engine_result(self, move: list | chess.Move | None, score: int, depth: int, request_id: int):
        if request_id != self._engine_request_id:
            return

        self._engine_thinking = False

        if move is None:
            if not self.engine_worker.isRunning():
                self._update_status('Движок остановлен')
                self.board_widget.unlock_input()
                self._update_buttons()
            return

        best_move = move[0][0] if isinstance(move, list) else move

        if best_move not in self.board.legal_moves:
            # Раньше здесь был просто return: доска оставалась заблокированной
            # после lock_input(), а _engine_thinking уже сброшен — ходить не мог
            # никто, и окно приходилось закрывать.
            print(f'Warning: Engine returned illegal move {best_move.uci()} for current board')
            self.board_widget.unlock_input()
            self._update_buttons()
            self._update_status('Движок вернул невозможный ход — повторяю расчёт')
            QTimer.singleShot(50, self._start_engine_thinking)
            return

        self.redo_stack.clear()
        self.move_history.append(best_move)
        self.board.push(best_move)

        self.board_widget.set_board(self.board)
        self.board_widget.set_last_move(best_move)
        self.board_widget.unlock_input()

        self._update_eval_bar(score)
        self._update_pgn()
        self._update_buttons()

        if depth == 0:
            # Из книги оценки нет: 0 здесь — это заглушка, а не «равная позиция».
            # Показывать её на шкале нельзя, иначе весь дебют шкала врёт «0.00».
            self._update_status(f'Движок: {best_move.uci()} | 📖 Из дебютной книги')
            self.info_label.setText(f'📖 Ход из дебютной книги: {best_move.uci()}')
            self._refresh_eval()
        else:
            if self._eval_visible:
                display = ChessEngine.score_to_display(score)
                self._update_status(f'Движок: {best_move.uci()} | Оценка: {display} | Глубина: {depth}')
                self.info_label.setText(
                    f'Ход движка: {best_move.uci()}  •  Глубина: {depth}  •  Оценка: {display}')
            else:
                try:
                    san = self._san_of_last_move()
                except Exception:
                    san = best_move.uci()
                self._update_status(f'Движок сыграл {san}')
                self.info_label.setText(f'Ход движка: {san}')

        self._check_game_over()

    def _on_hint_result(self, move: list | chess.Move | None, score: int, depth: int, request_id: int):
        if request_id != self._hint_request_id:
            return

        self._hint_thinking = False

        if move is None:
            return

        best_move = move[0][0] if isinstance(move, list) else move

        if best_move not in self.board.legal_moves:
            return

        self.board_widget.set_best_move(move[:2] if isinstance(move, list) else move)
        self._update_eval_bar(score)

        display = ChessEngine.score_to_display(score)
        side = 'белых' if self.board.turn == chess.WHITE else 'чёрных'
        self.info_label.setText(f'💡 Лучший ход для {side}: {best_move.uci()}  •  Оценка: {display}  •  Глубина: {depth}')
        self._update_status(f'Подсказка: {best_move.uci()} | Оценка: {display} | Глубина: {depth}')

    def _update_eval_bar(self, score_white_cp: int):
        display = ChessEngine.score_to_display(score_white_cp)
        self.eval_bar.set_score(score_white_cp, display)

    def _update_status(self, text: str):
        self.status_bar.showMessage(text)

    def _show_analysis(self):
        if not self.move_history:
            QMessageBox.information(self, 'Анализ', 'Нет ходов для анализа. Сначала сыграйте партию.')
            return

        dialog = AnalysisDialog(self.move_history, self, start_fen=self.start_fen)
        dialog.exec()

    def closeEvent(self, event):
        self._hint_timer.stop()
        if self.engine_worker.isRunning():
            self.engine_worker.terminate()
        if self.hint_worker.isRunning():
            self.hint_worker.terminate()
        self.engine.close()
        self._hint_engine.close()
        super().closeEvent(event)

    def _check_game_over(self) -> bool:
        if not self.board.is_game_over():
            return False

        self.board_widget.lock_input()
        self.board_widget.set_best_move(None)

        result = self.board.result()
        if self.board.is_checkmate():
            winner = 'Белые' if self.board.turn == chess.BLACK else 'Чёрные'
            message = f'Мат! Победа: {winner}\n\nРезультат: {result}'
        elif self.board.is_stalemate():
            message = f'Пат! Ничья.\n\nРезультат: {result}'
        elif self.board.is_insufficient_material():
            message = f'Недостаточно материала. Ничья.\n\nРезультат: {result}'
        elif self.board.can_claim_fifty_moves():
            message = f'Правило 50 ходов. Ничья.\n\nРезультат: {result}'
        elif self.board.is_repetition():
            message = f'Троекратное повторение. Ничья.\n\nРезультат: {result}'
        else:
            message = f'Партия окончена.\n\nРезультат: {result}'

        self.info_label.setText(f'🏁 Партия окончена: {result}')

        reply = QMessageBox.question(
            self,
            'Партия окончена',
            message + '\n\nХотите увидеть анализ партии?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._show_analysis()

        return True

    def _new_game(self):
        self._stop_hint_analysis()
        self._engine_request_id += 1
        if self.engine_worker.isRunning():
            self.engine_worker.terminate()

        self._engine_thinking = False

        self.board = chess.Board()
        self.start_fen = chess.STARTING_FEN
        self.engine.clear_cache()
        self.move_history.clear()
        self.redo_stack.clear()

        self.board_widget.set_board(self.board)
        self.board_widget.set_last_move(None)
        self.board_widget.set_best_move(None)
        self.board_widget.unlock_input()

        self._update_eval_bar(0)
        self._update_pgn()
        self._update_buttons()
        self._update_status('Новая партия')
        self.info_label.setText('')

        if self.game_mode == MODE_PLAY_BLACK:
            QTimer.singleShot(300, self._start_engine_thinking)
        elif self.game_mode == MODE_HVH:
            QTimer.singleShot(300, self._start_hint_analysis)
