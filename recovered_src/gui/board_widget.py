"""
board_widget.py – Виджет шахматной доски

Функциональность:
  • Отрисовка 8×8 доски с координатными метками
  • Отрисовка фигур (Unicode-символы шахматных фигур)
  • Подсветка последнего хода, выбранной клетки, легальных ходов
  • Клик-для-хода: выбрать фигуру → клик на целевую клетку
  • Перетаскивание: захват → перемещение → отпускание
  • Диалог превращения пешки
  • Переворот доски (при игре за чёрных)
"""

import math
import chess
from PyQt6.QtWidgets import QWidget, QMenu
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint, QSize, QPointF
from PyQt6.QtGui import QPainter, QColor, QFont, QBrush, QPen, QMouseEvent, QPaintEvent, QPolygonF

LIGHT_SQUARE = QColor(224, 192, 151)
DARK_SQUARE = QColor(93, 64, 55)
HIGHLIGHT_LAST = QColor(212, 175, 55, 180)
HIGHLIGHT_SELECTED = QColor(70, 130, 180, 160)
HIGHLIGHT_LEGAL = QColor(0, 0, 0, 50)
HIGHLIGHT_CHECK = QColor(207, 102, 121, 200)
HIGHLIGHT_BEST = QColor(76, 175, 80, 180)

PIECE_UNICODE = {
    (chess.KING, chess.WHITE): '♔',
    (chess.QUEEN, chess.WHITE): '♕',
    (chess.ROOK, chess.WHITE): '♖',
    (chess.BISHOP, chess.WHITE): '♗',
    (chess.KNIGHT, chess.WHITE): '♘',
    (chess.PAWN, chess.WHITE): '♙',
    (chess.KING, chess.BLACK): '♚',
    (chess.QUEEN, chess.BLACK): '♛',
    (chess.ROOK, chess.BLACK): '♜',
    (chess.BISHOP, chess.BLACK): '♝',
    (chess.KNIGHT, chess.BLACK): '♞',
    (chess.PAWN, chess.BLACK): '♟'
}

class BoardWidget(QWidget):
    """
    Виджет шахматной доски с поддержкой click-to-move и drag-and-drop.

    Сигналы:
      move_made(chess.Move) — испускается, когда пользователь совершил ход
    """

    move_made = pyqtSignal(object)

    board: chess.Board
    flipped: bool
    _selected_square: int | None
    _legal_targets: set
    _last_move: chess.Move | None
    _best_move_arrow: chess.Move | None
    _dragging: bool
    _drag_piece: chess.Piece | None
    _drag_from: int | None
    _drag_pos: QPoint | None
    _input_locked: bool

    def __init__(self, board: chess.Board, parent=None) -> None:
        super().__init__(parent)
        self.board = board
        self.flipped = False
        self._selected_square = None
        self._legal_targets = set()
        self._last_move = None
        self._best_move_arrow = None
        self._dragging = False
        self._drag_piece = None
        self._drag_from = None
        self._drag_pos = None
        self._input_locked = False
        self.setMinimumSize(480, 480)
        self.setSizePolicy(self.sizePolicy().Policy.Expanding, self.sizePolicy().Policy.Expanding)

    def set_board(self, board: chess.Board) -> None:
        self.board = board
        self.update()

    def set_flipped(self, flipped: bool) -> None:
        self.flipped = flipped
        self.update()

    def set_last_move(self, move: chess.Move | None) -> None:
        self._last_move = move
        self.update()

    def set_best_move(self, move) -> None:
        # move can be a single chess.Move or a list of tuples: [(move1, score1), (move2, score2), ...]
        self._best_move_arrow = move
        self.update()

    def lock_input(self) -> None:
        self._input_locked = True
        self._dragging = False
        self._drag_piece = None
        self._drag_from = None
        self._drag_pos = None
        self._clear_selection()

    def unlock_input(self) -> None:
        self._input_locked = False

    def _square_size(self) -> int:
        return min(self.width(), self.height()) // 8

    def _square_rect(self, sq: int) -> QRect:
        ss = self._square_size()
        file = chess.square_file(sq)
        rank = chess.square_rank(sq)
        if self.flipped:
            col = 7 - file
            row = rank
        else:
            col = file
            row = 7 - rank
        return QRect(col * ss, row * ss, ss, ss)

    def _pos_to_square(self, pos: QPoint) -> int | None:
        ss = self._square_size()
        if ss == 0:
            return None
        col = pos.x() // ss
        row = pos.y() // ss
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        if self.flipped:
            file = 7 - col
            rank = row
        else:
            file = col
            rank = 7 - row
        return chess.square(file, rank)

    def _clear_selection(self) -> None:
        self._selected_square = None
        self._legal_targets.clear()
        self.update()

    def _get_legal_targets(self, from_sq: int) -> set:
        targets = set()
        for move in self.board.legal_moves:
            if move.from_square == from_sq:
                targets.add(move.to_square)
        return targets

    def _try_make_move(self, from_sq: int, to_sq: int) -> None:
        piece = self.board.piece_at(from_sq)
        if piece and piece.piece_type == chess.PAWN:
            to_rank = chess.square_rank(to_sq)
            if (piece.color == chess.WHITE and to_rank == 7) or (piece.color == chess.BLACK and to_rank == 0):
                promo = self._ask_promotion()
                move = chess.Move(from_sq, to_sq, promotion=promo)
                if move in self.board.legal_moves:
                    self._last_move = move
                    self.move_made.emit(move)
                self._clear_selection()
                return

        move = chess.Move(from_sq, to_sq)
        if move in self.board.legal_moves:
            self._last_move = move
            self.move_made.emit(move)
        self._clear_selection()

    def _ask_promotion(self) -> int:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #f5f0e8;
                border: 1px solid #999;
                padding: 4px;
                font-size: 24px;
            }
            QMenu::item {
                padding: 8px 24px;
            }
            QMenu::item:selected {
                background-color: #d4c9a8;
            }
        """)
        turn = self.board.turn
        actions = {
            menu.addAction(PIECE_UNICODE[(chess.QUEEN, turn)] + " Ферзь"): chess.QUEEN,
            menu.addAction(PIECE_UNICODE[(chess.ROOK, turn)] + " Ладья"): chess.ROOK,
            menu.addAction(PIECE_UNICODE[(chess.BISHOP, turn)] + " Слон"): chess.BISHOP,
            menu.addAction(PIECE_UNICODE[(chess.KNIGHT, turn)] + " Конь"): chess.KNIGHT
        }
        chosen = menu.exec(self.mapToGlobal(self._drag_pos) if self._drag_pos else QPoint(200, 200))
        return actions.get(chosen, chess.QUEEN)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._input_locked or event.button() != Qt.MouseButton.LeftButton:
            return
        sq = self._pos_to_square(event.pos())
        if sq is None:
            self._clear_selection()
            return
            
        piece = self.board.piece_at(sq)
        if self._selected_square is not None and sq in self._legal_targets:
            self._try_make_move(self._selected_square, sq)
            return
            
        if piece and piece.color == self.board.turn:
            self._selected_square = sq
            self._legal_targets = self._get_legal_targets(sq)
            self._dragging = True
            self._drag_piece = piece
            self._drag_from = sq
            self._drag_pos = event.pos()
            self.update()
        else:
            self._clear_selection()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._drag_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self._dragging or event.button() != Qt.MouseButton.LeftButton:
            return
        to_sq = self._pos_to_square(event.pos())
        if to_sq is not None and self._drag_from is not None:
            if to_sq == self._drag_from:
                self._dragging = False
                self._drag_piece = None
                self._drag_pos = None
                self.update()
                return
            elif to_sq in self._legal_targets:
                self._dragging = False
                self._drag_piece = None
                self._drag_pos = None
                self._try_make_move(self._drag_from, to_sq)
                return
                
        self._dragging = False
        self._drag_piece = None
        self._drag_from = None
        self._drag_pos = None
        self._clear_selection()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ss = self._square_size()
        
        # 1. Draw Squares
        for sq in range(64):
            rect = self._square_rect(sq)
            file = chess.square_file(sq)
            rank = chess.square_rank(sq)
            is_light = (file + rank) % 2 == 1
            color = LIGHT_SQUARE if is_light else DARK_SQUARE
            painter.fillRect(rect, color)
            
        # 2. Last move highlight
        if self._last_move:
            for sq in (self._last_move.from_square, self._last_move.to_square):
                painter.fillRect(self._square_rect(sq), HIGHLIGHT_LAST)
                
        # 3. Check highlight
        if self.board.is_check():
            king_sq = self.board.king(self.board.turn)
            if king_sq is not None:
                painter.fillRect(self._square_rect(king_sq), HIGHLIGHT_CHECK)
                
        # 4. Selected square highlight
        if self._selected_square is not None:
            painter.fillRect(self._square_rect(self._selected_square), HIGHLIGHT_SELECTED)
            
        # 5. Legal moves highlight
        for sq in self._legal_targets:
            rect = self._square_rect(sq)
            center = rect.center()
            has_piece = self.board.piece_at(sq) is not None
            if has_piece:
                painter.setPen(QPen(QColor(0, 0, 0, 60), 3))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                radius = ss // 2 - 4
                painter.drawEllipse(center, radius, radius)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(HIGHLIGHT_LEGAL))
                radius = ss // 6
                painter.drawEllipse(center, radius, radius)
                
        # 6. Pieces Font
        piece_font = QFont("Segoe UI Symbol", int(ss * 0.65))
        piece_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        painter.setFont(piece_font)
        
        # 7. Draw Pieces (excluding the dragged one)
        for sq in range(64):
            if self._dragging and sq == self._drag_from:
                continue
            piece = self.board.piece_at(sq)
            if not piece:
                continue
            rect = self._square_rect(sq)
            symbol = PIECE_UNICODE.get((piece.piece_type, piece.color), '?')
            
            # Draw shadow
            shadow_offset = max(1, ss // 40)
            painter.setPen(QColor(0, 0, 0, 50))
            shadow_rect = QRect(rect.x() + shadow_offset, rect.y() + shadow_offset, rect.width(), rect.height())
            painter.drawText(shadow_rect, Qt.AlignmentFlag.AlignCenter, symbol)
            
            # Draw piece itself
            if piece.color == chess.WHITE:
                painter.setPen(QColor(255, 255, 255))
            else:
                painter.setPen(QColor(30, 30, 30))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, symbol)
            
        # 8. Draw Dragged Piece
        if self._dragging and self._drag_piece and self._drag_pos:
            symbol = PIECE_UNICODE.get((self._drag_piece.piece_type, self._drag_piece.color), '?')
            drag_rect = QRect(self._drag_pos.x() - ss // 2, self._drag_pos.y() - ss // 2, ss, ss)
            
            painter.setPen(QColor(0, 0, 0, 80))
            painter.drawText(QRect(drag_rect.x() + 2, drag_rect.y() + 2, ss, ss), Qt.AlignmentFlag.AlignCenter, symbol)
            
            if self._drag_piece.color == chess.WHITE:
                painter.setPen(QColor(255, 255, 255))
            else:
                painter.setPen(QColor(30, 30, 30))
            painter.drawText(drag_rect, Qt.AlignmentFlag.AlignCenter, symbol)
            
        # 9. Best move arrow
        if self._best_move_arrow:
            if isinstance(self._best_move_arrow, list):
                for i, (m, score) in enumerate(self._best_move_arrow):
                    self._draw_arrow(painter, m, ss, is_secondary=(i > 0), score=score)
            else:
                self._draw_arrow(painter, self._best_move_arrow, ss)
            
        # 10. Coordinates Label
        coord_font = QFont("Segoe UI", max(8, ss // 7), QFont.Weight.Bold)
        painter.setFont(coord_font)
        
        for i in range(8):
            # Files (a-h)
            file_label = chr(ord('a') + (7 - i if self.flipped else i))
            x = i * ss + ss - max(8, ss // 6)
            y = 8 * ss - 3
            is_light_sq = (i + 0) % 2 == (1 if self.flipped else 0)
            painter.setPen(DARK_SQUARE if is_light_sq else LIGHT_SQUARE)
            painter.drawText(x, y, file_label)
            
            # Ranks (1-8)
            rank_label = str(i + 1 if self.flipped else 8 - i)
            rx = 3
            ry = i * ss + max(10, ss // 5)
            is_light_sq2 = (0 + (i if self.flipped else 7 - i)) % 2 == 1
            painter.setPen(DARK_SQUARE if is_light_sq2 else LIGHT_SQUARE)
            painter.drawText(rx, ry, rank_label)
            
        painter.end()

    def _draw_arrow(self, painter: QPainter, move: chess.Move, ss: int, is_secondary: bool = False, score: int | None = None) -> None:
        import math
        from PyQt6.QtGui import QPolygonF
        from PyQt6.QtCore import QPointF
        
        from_rect = self._square_rect(move.from_square)
        to_rect = self._square_rect(move.to_square)
        
        x1 = from_rect.center().x()
        y1 = from_rect.center().y()
        x2 = to_rect.center().x()
        y2 = to_rect.center().y()
        
        line_width = max(4, ss // 8)
        arrow_size = max(10, ss // 4)
        
        if is_secondary:
            line_width = max(2, ss // 16)
            arrow_size = max(6, ss // 6)
            color = QColor(100, 150, 200, 160)
        else:
            color = HIGHLIGHT_BEST

        painter.setPen(QPen(color, line_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(QBrush(color))
        painter.drawLine(x1, y1, x2, y2)
        
        angle = math.atan2(y2 - y1, x2 - x1)
        p1_x = x2 - arrow_size * math.cos(angle - math.pi / 6)
        p1_y = y2 - arrow_size * math.sin(angle - math.pi / 6)
        p2_x = x2 - arrow_size * math.cos(angle + math.pi / 6)
        p2_y = y2 - arrow_size * math.sin(angle + math.pi / 6)
        
        triangle = QPolygonF([
            QPointF(x2, y2),
            QPointF(p1_x, p1_y),
            QPointF(p2_x, p2_y)
        ])
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(triangle)

        if score is not None:
            if abs(score) >= 99000:
                mate_in = (99999 - abs(score) + 1) // 2
                display_score = f"M{mate_in}" if score > 0 else f"-M{mate_in}"
            else:
                display_score = f"{score / 100:+.2f}"

            font = QFont("Segoe UI", max(8, ss // 6), QFont.Weight.Bold)
            painter.setFont(font)

            mid_x = (x1 + x2) / 2
            mid_y = (y1 + y2) / 2

            text_rect = QRect(int(mid_x - 30), int(mid_y - 15), 60, 30)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRoundedRect(text_rect, 4, 4)

            painter.setPen(QColor(255, 255, 255))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, display_score)

    def sizeHint(self) -> QSize:
        return QSize(560, 560)
