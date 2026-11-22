@echo off
chcp 65001 >nul
echo === PyCaesar Windows Build (Cython + PyInstaller) ===
echo.

echo [1/4] Установка зависимостей...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :err

echo.
echo [2/4] Сборка Cython-ядра (_fast_core.pyd)...
python setup_cython.py
if errorlevel 1 (
    echo [!] Не удалось собрать Cython-ядро (возможно, не установлены Microsoft C++ Build Tools).
    echo [!] Продолжаем сборку на чистом Python-ядре FastBoard...
)

echo.
echo [3/4] Сборка PyCaesar.exe (GUI)...
pyinstaller --clean --noconfirm PyCaesar.spec
if errorlevel 1 goto :err

echo.
echo [4/4] Сборка PyCaesarUCI.exe (UCI)...
pyinstaller --clean --noconfirm PyCaesarUCI.spec
if errorlevel 1 goto :err

echo.
echo === ГОТОВО! Бинарники лежат в папке dist\ ===
dir dist\*.exe
pause
exit /b 0

:err
echo.
echo [ОШИБКА] Сборка прервана. Проверьте вывод выше.
pause
exit /b 1
