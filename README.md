# Tri de photos par date

Application locale (Python + Tkinter) qui analyse un dossier de photos, propose une
arborescence par date (Année, Année / Mois, ou Année / Mois / Jour, au choix) basée sur
la date de prise de vue (EXIF, avec repli sur la date de modification du fichier), puis
copie les photos dans un nouveau dossier selon cette arborescence.

Fonctionne sous Windows, Linux et macOS.

## Prérequis

- **Windows** : aucun, si vous utilisez `dist\TriPhotos.exe` (voir ci-dessous).
- **Linux / macOS**, ou pour reconstruire l'exécutable Windows : Python 3.9 ou plus
  récent : https://www.python.org/downloads/
  (cocher "Add python.exe to PATH" lors de l'installation sous Windows)

## Lancer l'application

- **Windows** : double-cliquer sur `dist\TriPhotos.exe`. Aucun terminal ne s'affiche,
  Python n'a pas besoin d'être installé.
- **Linux / macOS** : `./run.sh` (crée automatiquement un environnement virtuel `venv`
  et installe les dépendances au premier lancement).

## Reconstruire TriPhotos.exe (Windows)

Après une modification de `photo_sorter.py`, régénérer l'exécutable avec :

```
build.bat
```

Ce script crée/mets à jour l'environnement virtuel, installe les dépendances ainsi que
PyInstaller, puis génère `dist\TriPhotos.exe`.

## Générer un exécutable Linux

Un exécutable autonome pour Linux se construit uniquement sur une machine Linux (pas
de compilation croisée depuis Windows). Le module `tkinter` doit être installé au
préalable (souvent absent par défaut) :

```bash
sudo apt install python3-tk      # Debian / Ubuntu
sudo dnf install python3-tkinter # Fedora
sudo pacman -S tk                # Arch
```

Puis lancer :

```bash
./build_linux.sh
```

Ce script crée/mets à jour l'environnement virtuel, installe les dépendances ainsi que
PyInstaller, puis génère `dist/TriPhotos` (binaire ELF, sans extension).

## Utilisation

1. Cliquer sur **Choisir...** pour sélectionner le dossier contenant les photos à trier.
2. Choisir le **niveau de tri** souhaité : *Année*, *Année / Mois* ou
   *Année / Mois / Jour*. Les niveaux étant imbriqués, choisir *Jour* implique
   automatiquement le mois et l'année, etc. Ce choix peut être changé à tout moment,
   même après l'analyse : l'aperçu se met à jour immédiatement.
3. Cliquer sur **Analyser** : l'arborescence correspondant au niveau choisi, avec le
   nombre de photos à chaque niveau, s'affiche.
4. Cliquer sur **Choisir...** (destination) pour désigner le dossier dans lequel créer
   l'arborescence.
5. Choisir l'**action** : *Copier* (les originaux sont conservés, par défaut) ou
   *Déplacer* (les originaux sont supprimés une fois transférés).
6. Cliquer sur **Créer l'arborescence et copier/déplacer les photos**. Une copie (ou le
   fichier déplacé) est placée dans le sous-dossier correspondant (par exemple
   `destination/2024/08-Août/15/` au niveau *Jour*). Les photos déjà présentes dans le
   dossier de destination (même contenu) sont détectées comme doublons : elles ne sont
   pas dupliquées (et sont supprimées de la source en mode *Déplacer*) ; leur nombre est
   indiqué dans le message final.

## Tests

Les fonctions de tri, d'agrégation par niveau et de détection des doublons sont
couvertes par des tests unitaires (module `unittest`, sans dépendance
supplémentaire) :

```bash
venv\Scripts\python -m unittest discover -s tests -v   # Windows
venv/bin/python -m unittest discover -s tests -v        # Linux / macOS
```

## Formats supportés

JPEG, PNG, GIF, BMP, TIFF, WEBP, HEIC/HEIF.

Pour la lecture des photos HEIC/HEIF (iPhone), installer en plus `pillow-heif` :

```bash
pip install pillow-heif
```

(`TriPhotos.exe` et le script `run.sh` n'incluent que Pillow par défaut ; sans
`pillow-heif`, les fichiers HEIC sont classés selon leur date de modification au lieu
de la date EXIF. Pour l'inclure dans l'exécutable Windows, l'ajouter à
`requirements.txt` puis relancer `build.bat`).
