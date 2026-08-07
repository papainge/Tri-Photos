# Tri de photos par date

Application locale (Python + Tkinter) qui analyse un dossier de photos et vidéos,
propose une arborescence par date (Année, Année / Mois, ou Année / Mois / Jour, au
choix) basée sur la date de prise de vue (EXIF pour les photos, avec repli sur la date
de modification du fichier), puis copie ou déplace les fichiers dans un nouveau dossier
selon cette arborescence — avec la possibilité de séparer Photos et Vidéos à la racine
de la destination.

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

1. Cliquer sur **Choisir...** pour sélectionner le dossier contenant les photos et
   vidéos à trier.
2. Choisir le **niveau de tri** souhaité : *Année*, *Année / Mois* ou
   *Année / Mois / Jour*. Les niveaux étant imbriqués, choisir *Jour* implique
   automatiquement le mois et l'année, etc. Ce choix peut être changé à tout moment,
   même après l'analyse : l'aperçu se met à jour immédiatement.
3. Cocher **Séparer Photos et Vidéos à la racine de la destination** si vous voulez deux
   arborescences distinctes (`destination/Photos/...` et `destination/Vidéos/...`) au
   lieu d'un classement mélangé par date (comportement par défaut). Ce choix peut aussi
   être changé après l'analyse.
4. Cliquer sur **Analyser** : l'arborescence correspondant aux choix ci-dessus, avec le
   nombre de fichiers à chaque niveau, s'affiche.
5. Cliquer sur **Choisir...** (destination) pour désigner le dossier dans lequel créer
   l'arborescence.
6. Choisir l'**action** : *Copier* (les originaux sont conservés, par défaut) ou
   *Déplacer* (les originaux sont supprimés une fois transférés).
7. Cliquer sur **Créer l'arborescence et copier/déplacer les fichiers**. Une copie (ou
   le fichier déplacé) est placée dans le sous-dossier correspondant (par exemple
   `destination/2024/08-Août/15/`, ou `destination/Photos/2024/08-Août/15/` si Photos et
   Vidéos sont séparés). Les fichiers déjà présents dans le dossier de destination (même
   contenu) sont détectés comme doublons : ils ne sont pas dupliqués (et sont supprimés
   de la source en mode *Déplacer*) ; leur nombre est indiqué dans le message final.

## Tests

Les fonctions de tri, d'agrégation par niveau et de détection des doublons sont
couvertes par des tests unitaires (module `unittest`, sans dépendance
supplémentaire) :

```bash
venv\Scripts\python -m unittest discover -s tests -v   # Windows
venv/bin/python -m unittest discover -s tests -v        # Linux / macOS
```

## Formats supportés

- **Photos** : JPEG, PNG, GIF, BMP, TIFF, WEBP, HEIC/HEIF.
- **Vidéos** : MP4, MOV, AVI, MKV, M4V, 3GP, WMV, MPG/MPEG, WEBM. Pour les formats
  MP4/MOV/M4V/3GP (boîte `moov`/`mvhd`), la date de création embarquée dans le fichier
  est lue directement, sans dépendance externe. Les autres conteneurs (AVI, MKV, WMV,
  WEBM...) n'ont pas ce format de métadonnées et sont datés via la date de modification
  du fichier.

Pour la lecture des photos HEIC/HEIF (iPhone), installer en plus `pillow-heif` :

```bash
pip install pillow-heif
```

(`TriPhotos.exe` et le script `run.sh` n'incluent que Pillow par défaut ; sans
`pillow-heif`, les fichiers HEIC sont classés selon leur date de modification au lieu
de la date EXIF. Pour l'inclure dans l'exécutable Windows, l'ajouter à
`requirements.txt` puis relancer `build.bat`).
