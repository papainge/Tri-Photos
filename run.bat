@echo off
cd /d "%~dp0"

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

start "" venv\Scripts\pythonw.exe src\media_sorter.py
exit
