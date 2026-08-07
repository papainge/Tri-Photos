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
    if errorlevel 1 (
        echo Echec de la creation de l'environnement virtuel.
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo Echec de l'activation de l'environnement virtuel.
    pause
    exit /b 1
)

pip install -q -r requirements.txt
if errorlevel 1 (
    echo Echec de l'installation des dependances via requirements.txt.
    pause
    exit /b 1
)

pip install -q pyinstaller==6.21.0
if errorlevel 1 (
    echo Echec de l'installation de PyInstaller.
    pause
    exit /b 1
)

python packaging\_patch_pyinstaller_tkinter.py
if errorlevel 1 (
    echo Echec du correctif Tcl/Tk de PyInstaller.
    pause
    exit /b 1
)

pyinstaller --noconfirm --onefile --windowed --name TriPhotos src\media_sorter.py
if errorlevel 1 (
    echo Echec de la generation de l'executable.
    pause
    exit /b 1
)

echo.
echo TriPhotos.exe genere dans dist\TriPhotos.exe
pause
