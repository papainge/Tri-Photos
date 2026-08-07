#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "Python 3 n'est pas installe. Installez-le puis relancez ce script."
    exit 1
fi

if [ ! -d venv ]; then
    echo "Creation de l'environnement virtuel..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt
python src/media_sorter.py
