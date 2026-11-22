"""
start_dialog.py – Диалог выбора режима игры

Модальное окно при запуске приложения с тремя вариантами:
  • Играть за белых (Human vs Engine, пользователь — белые)
  • Играть за чёрных (Human vs Engine, пользователь — чёрные)
  • Человек vs Человек (без участия движка)
"""

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from gui.utils import get_app_icon

MODE_PLAY_WHITE = "play_white"
MODE_PLAY_BLACK = "play_black"
MODE_HVH = "hvh"

BOT_VERSION = "2.0.0 (Final)"
UI_VERSION = "2.0.0"

class StartDialog(QDialog):
    """Диалог выбора режима игры."""

    selected_mode: str | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PyCaesar — Выбор режима")
        self.setMinimumSize(400, 320)
        self.resize(440, 380)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowIcon(get_app_icon())
        self.selected_mode = None
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(32, 24, 32, 20)

        title = QLabel("♚ PyCaesar")
        title.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #1a1a1a; margin-bottom: 2px;")
        layout.addWidget(title)

        subtitle = QLabel("Выберите режим игры")
        subtitle.setFont(QFont("Segoe UI", 11))
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #555;")
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        btn_style = """
            QPushButton {
                background-color: %BG%;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 14px 20px;
                font-size: 14px;
                font-weight: 600;
                font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background-color: %HOVER%;
            }
            QPushButton:pressed {
                background-color: %PRESS%;
            }
        """

        def _make_btn(text, bg, hover, press, mode):
            btn = QPushButton(text)
            btn.setStyleSheet(
                btn_style.replace("%BG%", bg).replace("%HOVER%", hover).replace("%PRESS%", press)
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda: self._select(mode))
            return btn

        layout.addWidget(_make_btn("♔  Играть за белых", "#2d5016", "#3a6b1e", "#224010", MODE_PLAY_WHITE))
        layout.addWidget(_make_btn("♚  Играть за чёрных", "#1a1a2e", "#2a2a4e", "#111122", MODE_PLAY_BLACK))
        layout.addWidget(_make_btn("👥  Человек vs Человек", "#5c4033", "#7a5a4a", "#3e2a1e", MODE_HVH))

        layout.addStretch()

        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        btn_info = QPushButton("ℹ")
        btn_info.setFixedSize(28, 28)
        btn_info.setStyleSheet("""
            QPushButton {
                background-color: #d5cfc4;
                color: #555;
                border: none;
                border-radius: 14px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #c5bfb4; }
        """)
        btn_info.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_info.clicked.connect(self._show_info)
        bottom_layout.addWidget(btn_info)
        layout.addLayout(bottom_layout)

        self.setStyleSheet("""
            QDialog {
                background-color: #f5f0e8;
            }
        """)

    def _show_info(self) -> None:
        info = QDialog(self)
        info.setWindowTitle("О программе")
        info.setMinimumWidth(340)
        info.setStyleSheet("""
            QDialog { background-color: #f5f0e8; }
            QLabel { color: #1a1a1a; }
        """)
        info.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowCloseButtonHint
        )

        lay = QVBoxLayout(info)
        lay.setSpacing(10)
        lay.setContentsMargins(28, 20, 28, 18)

        name_lbl = QLabel("♚ PyCaesar")
        name_lbl.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_lbl.setStyleSheet("color: #2d5016;")
        lay.addWidget(name_lbl)

        desc_lbl = QLabel("Шахматный движок на Python + 64-битном Cython")
        desc_lbl.setFont(QFont("Segoe UI", 10))
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl.setStyleSheet("color: #555;")
        lay.addWidget(desc_lbl)

        lay.addSpacing(6)

        try:
            from engine.search import _CythonSearcher
            core_label = "Cython 64-bit bitboards (_fast_core)" if _CythonSearcher is not None else "FastBoard (Python fallback)"
        except Exception:
            core_label = "FastBoard"

        try:
            from engine.tablebase import SyzygyTablebase
            tb = SyzygyTablebase()
            tb_label = f"Syzygy (до {tb.max_pieces} фигур)" if tb.is_available else "не подключены"
            tb.close()
        except Exception:
            tb_label = "не подключены"

        details_text = (
            f"Версия движка:  {BOT_VERSION}\n"
            f"Версия UI:  {UI_VERSION}\n"
            f"\n"
            f"Ядро:  {core_label}\n"
            f"Поиск:  PVS + Negamax + PeSTO + LMR/NMP/SEE\n"
            f"Дебюты:  Polyglot (book.bin)\n"
            f"Эндшпили:  {tb_label}\n"
            f"Сила игры:  10 уровней (400–2637 Elo, Stockfish 19)\n"
            f"Протоколы:  PyQt6 GUI + UCI"
        )
        details_lbl = QLabel(details_text)
        details_lbl.setFont(QFont("Segoe UI", 10))
        details_lbl.setStyleSheet("""
            color: #333;
            background-color: #eae5dc;
            border: 1px solid #d0c9be;
            border-radius: 6px;
            padding: 10px 14px;
        """)
        lay.addWidget(details_lbl)

        lay.addSpacing(4)

        ok_btn = QPushButton("Понятно")
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d5016;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 32px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI';
            }
            QPushButton:hover { background-color: #3a6b1e; }
        """)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(info.accept)
        lay.addWidget(ok_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        info.exec()

    def _select(self, mode: str) -> None:
        self.selected_mode = mode
        self.accept()

