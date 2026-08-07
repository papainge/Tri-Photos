@echo off
cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
    echo Python n'est pas installe ou pas dans le PATH.
    echo Installez-le depuis https://www.python.org/downloads/ puis relancez ce script.
    pause
    exit /b 1
)

if not exist venv (
    echo Creation de l'environnement virtuel...
    python -m venv venv
)

call venv\Scripts\activate.bat
pip install -q -r requirements.txt
pip install -q pyinstaller==6.21.0

python packaging\_patch_pyinstaller_tkinter.py

pyinstaller --noconfirm --onefile --windowed --name TriPhotos src\media_sorter.py

echo.
echo TriPhotos.exe genere dans dist\TriPhotos.exe
pause
