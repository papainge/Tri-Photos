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
- **Linux (x86_64)** : aucun non plus, si vous utilisez `dist/TriPhotos` et que votre
  distribution est assez récente (voir ci-dessous).
- **macOS**, ou pour reconstruire un exécutable : Python 3.9 ou plus récent :
  https://www.python.org/downloads/
  (cocher "Add python.exe to PATH" lors de l'installation sous Windows)

## Lancer l'application

- **Windows** : double-cliquer sur `dist\TriPhotos.exe`. Aucun terminal ne s'affiche,
  Python n'a pas besoin d'être installé.
- **Linux (x86_64)** : `./dist/TriPhotos` (le rendre exécutable si besoin :
  `chmod +x dist/TriPhotos`). Binaire autonome, Python n'a pas besoin d'être installé.
  Construit sur une distribution récente (glibc récente) : s'il ne se lance pas sur une
  distribution plus ancienne (erreur du type `GLIBC_2.xx not found`), utiliser `./run.sh`
  à la place, ou reconstruire l'exécutable localement avec `build_linux.sh`.
- **macOS** ou en cas de souci avec le binaire Linux fourni : `./run.sh` (crée
  automatiquement un environnement virtuel `venv` et installe les dépendances au premier
  lancement).

## Reconstruire TriPhotos.exe (Windows)

Après une modification de `photo_sorter.py`, régénérer l'exécutable avec :

```
build.bat
```

Ce script crée/mets à jour l'environnement virtuel, installe les dépendances ainsi que
PyInstaller, puis génère `dist\TriPhotos.exe`.

## Reconstruire l'exécutable Linux

`dist/TriPhotos` est déjà fourni. Pour le régénérer (après une modification de
`photo_sorter.py`, ou pour une distribution plus ancienne que celle utilisée pour le
binaire fourni), il faut le reconstruire sur une machine Linux : pas de compilation
croisée possible depuis Windows. Le module `tkinter` doit être installé au préalable
(souvent absent par défaut) :

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
   vidéos à trier. La case **Inclure les sous-dossiers** (cochée par défaut) détermine
   si l'analyse descend dans les sous-dossiers ou se limite à la racine du dossier
   choisi ; ce choix est pris en compte au moment de cliquer sur **Analyser**.
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

`tests/test_load.py` ajoute des tests de charge : plusieurs milliers de fichiers
répartis sur des dizaines de dossiers/dates (`scan_media`, agrégation, détection de
doublons), et des fichiers vidéo de centaines de Mo (générés en fichiers creux/sparse
pour rester rapides à créer) pour vérifier que chaque parseur de métadonnées vidéo saute
bien par-dessus les données audio/vidéo sans jamais les lire — le temps d'exécution est
borné et ne doit pas dépendre de la taille du fichier. L'ensemble de la suite (tests
unitaires + charge) s'exécute en quelques secondes.

## Formats supportés

- **Photos** : JPEG, PNG, GIF, BMP, TIFF, WEBP, HEIC/HEIF. La date EXIF (DateTimeOriginal,
  puis DateTimeDigitized, puis DateTime) est lue quand le format et le fichier la
  portent (JPEG, TIFF, HEIC/HEIF, PNG, WEBP) ; GIF et BMP n'ont pas de mécanisme EXIF et
  sont toujours datés via la date de modification.
- **Vidéos** : MP4, MOV, M4V, 3GP, AVI, WMV, MKV, WEBM, MPG/MPEG. La date de création
  embarquée est lue directement, sans dépendance externe, pour chacun de ces conteneurs
  (sauf MPG/MPEG, qui n'a pas d'équivalent standardisé) :
  - **MP4/MOV/M4V/3GP** : boîte `moov`/`mvhd` (ISO-BMFF/QuickTime).
  - **AVI** : chunk `IDIT` du `LIST INFO` (absent de nombreux AVI modernes).
  - **WMV** : `File Properties Object` (ASF).
  - **MKV/WEBM** : élément `DateUTC` (`Segment`/`Info`, EBML/Matroska).

  Quand la métadonnée correspondante est absente du fichier (chunk/objet non renseigné
  par le logiciel d'origine), la date de modification du fichier est utilisée à la
  place, comme pour les photos sans EXIF.

Pour la lecture des photos HEIC/HEIF (iPhone), installer en plus `pillow-heif` :

```bash
pip install pillow-heif
```

(`TriPhotos.exe` et le script `run.sh` n'incluent que Pillow par défaut ; sans
`pillow-heif`, les fichiers HEIC sont classés selon leur date de modification au lieu
de la date EXIF. Pour l'inclure dans l'exécutable Windows, l'ajouter à
`requirements.txt` puis relancer `build.bat`).
