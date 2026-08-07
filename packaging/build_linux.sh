#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

if ! command -v python3 &> /dev/null; then
    echo "Python 3 n'est pas installe. Installez-le puis relancez ce script."
    exit 1
fi

if ! python3 -c "import tkinter" &> /dev/null; then
    echo "Le module tkinter n'est pas installe pour ce python3."
    echo "Debian / Ubuntu : sudo apt install python3-tk"
    echo "Fedora          : sudo dnf install python3-tkinter"
    echo "Arch            : sudo pacman -S tk"
    exit 1
fi

if [ ! -d venv ]; then
    echo "Creation de l'environnement virtuel..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt
pip install -q pyinstaller

pyinstaller --noconfirm --onefile --name TriPhotos src/media_sorter.py

echo
echo "Executable genere : dist/TriPhotos"
