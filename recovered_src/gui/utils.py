import sys
import os
from PyQt6.QtGui import QIcon

def get_resource_path(relative_path: str) -> str:
    """
    Путь к файлу данных (icon.svg, book.bin) и в собранном exe, и из исходников.

    Из исходников __file__ = <корень>/recovered_src/gui/utils.py, а данные
    лежат в <корне> — нужно три dirname. Раньше было два, поэтому иконка
    окна из исходников не находилась и окно запускалось пустым.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, relative_path)

def get_app_icon() -> QIcon:
    return QIcon(get_resource_path('icon.svg'))
