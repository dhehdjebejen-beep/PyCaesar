"""
Required Notice: Copyright (c) 2026 dhehdjebejen-beep (https://github.com/dhehdjebejen-beep)
License: PolyForm Noncommercial License 1.0.0 (https://polyformproject.org/licenses/noncommercial/1.0.0)

main.py – Точка входа в шахматное приложение

Запуск:
    python main.py

Зависимости:
    pip install python-chess PyQt6

Поддержка multiprocessing при сборке PyInstaller:
    freeze_support() вызывается для корректной работы ProcessPoolExecutor.
"""

import sys
import os
import multiprocessing

# Добавляем путь к DLL библиотекам Qt6 для корректного импорта PyQt6 в переносном окружении
if not getattr(sys, 'frozen', False):
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    qt_bin = os.path.join(root_dir, 'python314_portable', 'PyQt6', 'Qt6', 'bin')
    if os.path.isdir(qt_bin):
        os.add_dll_directory(qt_bin)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt
from gui.start_dialog import StartDialog
from gui.main_window import MainWindow

def main():
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setFont(QFont('Segoe UI', 10))

    while True:
        dialog = StartDialog()
        if dialog.exec() != StartDialog.DialogCode.Accepted:
            break

        mode = dialog.selected_mode
        if mode is None:
            break

        window = MainWindow(game_mode=mode)
        back_requested = [False]

        def on_back_to_menu():
            back_requested[0] = True

        window.back_to_menu.connect(on_back_to_menu)
        window.show()

        app.exec()

        if not back_requested[0]:
            break

    sys.exit(0)

if __name__ == '__main__':
    main()
