"""
eval_bar.py – Вертикальная шкала оценки позиции

Визуализирует оценку движка в виде двухцветной вертикальной полосы:
  • Белая часть снизу (преимущество белых → больше заполнение)
  • Чёрная часть сверху
  • Числовая метка: «+1.50», «−0.30», «М3» и т.д.

Перевод cp → win% через сигмоиду: win% = 1 / (1 + 10^(-cp/400))
"""

import math
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QFont, QLinearGradient

class EvalBar(QWidget):
    """Вертикальная шкала оценки позиции."""

    _score_cp: int
    _display_text: str
    _win_pct: float

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(36)
        self.setMinimumHeight(200)
        self._score_cp = 0
        self._display_text = "0.00"
        self._win_pct = 0.5

    def set_score(self, score_cp: int, display_text: str) -> None:
        self._score_cp = score_cp
        self._display_text = display_text
        clamped = max(-1500, min(1500, score_cp))
        self._win_pct = 1.0 / (1.0 + math.pow(10, -clamped / 400.0))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w = self.width()
        h = self.height()
        margin = 2
        
        painter.fillRect(0, 0, w, h, QColor(30, 30, 30))
        
        bar_x = margin
        bar_y = margin
        bar_w = w - 2 * margin
        bar_h = h - 2 * margin
        
        black_height = int(bar_h * (1.0 - self._win_pct))
        
        black_gradient = QLinearGradient(bar_x, bar_y, bar_x, bar_y + black_height)
        black_gradient.setColorAt(0.0, QColor(40, 40, 40))
        black_gradient.setColorAt(1.0, QColor(60, 60, 60))
        
        painter.fillRect(bar_x, bar_y, bar_w, black_height, black_gradient)
        
        white_y = bar_y + black_height
        white_height = bar_h - black_height
        
        white_gradient = QLinearGradient(bar_x, white_y, bar_x, white_y + white_height)
        white_gradient.setColorAt(0.0, QColor(230, 230, 230))
        white_gradient.setColorAt(1.0, QColor(245, 245, 245))
        
        painter.fillRect(bar_x, white_y, bar_w, white_height, white_gradient)
        
        painter.setPen(QColor(100, 100, 100))
        painter.drawLine(bar_x, white_y, bar_x + bar_w, white_y)
        
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)
        
        text = self._display_text
        text_y = h // 2
        
        if self._win_pct > 0.55:
            painter.setPen(QColor(40, 40, 40))
        else:
            painter.setPen(QColor(220, 220, 220))
            
        text_rect = painter.fontMetrics().boundingRect(text)
        text_x = (w - text_rect.width()) // 2
        text_y_pos = text_y + text_rect.height() // 4
        
        painter.drawText(text_x, text_y_pos, text)
        painter.end()
