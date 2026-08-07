# Tri de photos et vidéos par date
Application locale (Python + Tkinter) qui analyse un dossier de photos et vidéos,
propose une arborescence par date (Année, Année / Mois, ou Année / Mois / Jour, au
choix) basée sur la date de prise de vue ou de création (métadonnées EXIF pour les
photos, métadonnées embarquées pour la plupart des formats vidéo, avec repli sur la
date de modification du fichier si absente), puis copie ou déplace les fichiers dans un
nouveau dossier selon cette arborescence — avec la possibilité de séparer Photos et
Vidéos à la racine de la destination.

Fonctionne sous Windows, Linux et macOS.

## Structure du projet

```
src/media_sorter.py     Point d'entrée : interface Tkinter, tri/agrégation, copie/déplacement
src/photo_metadata.py   Lecture de la date EXIF des photos
src/video_metadata.py   Lecture de la date de création des vidéos (MP4, AVI, WMV, MKV/WEBM)
tests/                  Tests unitaires et de charge (un fichier par module de src/)
packaging/              Scripts de génération des exécutables (build.bat, build_linux.sh)
dist/                   Sortie locale de build (généré, non versionné — voir Releases)
run.bat / run.sh        Lancement depuis les sources (sans passer par un exécutable)
```

## Prérequis

- **Windows** : aucun, si vous téléchargez `TriPhotos.exe` depuis la
  [page Releases](https://github.com/papainge/Tri-Photos/releases/latest) (voir
  ci-dessous).
- **Linux (x86_64)** : aucun non plus, si vous téléchargez `TriPhotos` depuis la même
  page et que votre distribution est assez récente (voir ci-dessous).
- **macOS**, ou pour reconstruire un exécutable : Python 3.9 ou plus récent :
  https://www.python.org/downloads/
  (cocher "Add python.exe to PATH" lors de l'installation sous Windows)

## Lancer l'application

Les exécutables Windows et Linux ne sont pas versionnés dans le dépôt (voir
[Pourquoi pas de binaires dans le dépôt ?](#pourquoi-pas-de-binaires-dans-le-dépôt))
mais publiés sur la [page Releases](https://github.com/papainge/Tri-Photos/releases/latest).

- **Windows** : télécharger `TriPhotos.exe` puis double-cliquer dessus. Aucun terminal
  ne s'affiche, Python n'a pas besoin d'être installé.
- **Linux (x86_64)** : télécharger `TriPhotos`, le rendre exécutable
  (`chmod +x TriPhotos`) puis le lancer (`./TriPhotos`). Binaire autonome, Python n'a pas
  besoin d'être installé. Construit sur une distribution récente (glibc récente) : s'il
  ne se lance pas sur une distribution plus ancienne (erreur du type
  `GLIBC_2.xx not found`), utiliser `./run.sh` à la place, ou reconstruire l'exécutable
  localement avec `packaging/build_linux.sh`.
- **macOS** ou en cas de souci avec le binaire Linux fourni : cloner le dépôt et lancer
  `./run.sh` (crée automatiquement un environnement virtuel `venv` et installe les
  dépendances au premier lancement).

## Reconstruire TriPhotos.exe (Windows)

Après une modification de `src\media_sorter.py`, régénérer l'exécutable avec :

```
packaging\build.bat
```

Ce script crée/mets à jour l'environnement virtuel, installe les dépendances ainsi que
PyInstaller, puis génère `dist\TriPhotos.exe` (dossier local, non versionné).

## Reconstruire l'exécutable Linux

Il faut le construire sur une machine Linux : pas de compilation croisée possible depuis
Windows. Le module `tkinter` doit être installé au préalable (souvent absent par
défaut) :

```bash
sudo apt install python3-tk      # Debian / Ubuntu
sudo dnf install python3-tkinter # Fedora
sudo pacman -S tk                # Arch
```

Puis lancer :

```bash
./packaging/build_linux.sh
```

Ce script crée/mets à jour l'environnement virtuel, installe les dépendances ainsi que
PyInstaller, puis génère `dist/TriPhotos` (binaire ELF, sans extension, dossier local
non versionné).

## Pourquoi pas de binaires dans le dépôt ?

Les exécutables (~20 Mo chacun) changent entièrement à chaque reconstruction : les
committer directement gonfle l'historique git de façon irréversible (dizaines de Mo à
chaque version, pour toujours). Ils sont donc uniquement attachés aux
[Releases](https://github.com/papainge/Tri-Photos/releases) (une par version publiée),
et `dist/` reste un simple dossier de build local, ignoré par git.

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
   nombre de fichiers à chaque niveau, s'affiche. Le total (avec la répartition
   photos/vidéos et le temps qu'a pris l'analyse) reste visible au-dessus de
   l'arborescence pendant toute l'opération. La lecture des dates est parallélisée sur
   plusieurs fichiers à la fois pour accélérer l'analyse des dossiers volumineux. Si elle
   prend malgré tout trop de temps, le bouton **Annuler** (à côté d'Analyser) l'interrompt
   à la volée.
5. Cliquer sur **Choisir...** (destination) pour désigner le dossier dans lequel créer
   l'arborescence. Ce dossier ne peut pas être le dossier source, ni un de ses
   sous-dossiers (refusé au moment de lancer la copie, pour éviter de copier les
   fichiers dans le dossier en cours d'analyse).
6. Choisir l'**action** : *Copier* (les originaux sont conservés, par défaut) ou
   *Déplacer* (les originaux sont supprimés une fois transférés).
7. Cliquer sur **Créer l'arborescence et copier/déplacer les fichiers**. Une copie (ou
   le fichier déplacé) est placée dans le sous-dossier correspondant (par exemple
   `destination/2024/08-Août/15/`, ou `destination/Photos/2024/08-Août/15/` si Photos et
   Vidéos sont séparés). Les fichiers déjà présents dans le dossier de destination (même
   contenu) sont détectés comme doublons : ils ne sont pas dupliqués (et sont supprimés
   de la source en mode *Déplacer*) ; leur nombre est indiqué dans le message final. Comme
   pour l'analyse, un bouton **Annuler** permet d'interrompre l'opération à la volée (le
   fichier en cours de transfert va jusqu'à son terme, seul le suivant ne démarre pas).

## Tests

Chaque module de `src/` a son fichier de tests dédié (module `unittest`, sans
dépendance supplémentaire) :

- `tests/test_photo_metadata.py` — lecture EXIF (photo_metadata.py)
- `tests/test_video_metadata.py` — parseurs MP4/AVI/WMV/MKV, et leurs tests de charge
  (fichiers vidéo de centaines de Mo, générés en fichiers creux/sparse pour rester
  rapides à créer, qui vérifient que chaque parseur saute bien par-dessus les données
  audio/vidéo sans jamais les lire — temps d'exécution borné, indépendant de la taille
  du fichier)
- `tests/test_media_sorter.py` — tri, agrégation par niveau, séparation Photos/Vidéos,
  copie/déplacement (`copy_files`), détection de doublons, et délégation de
  `get_media_date` vers les deux modules ci-dessus
- `tests/test_media_sorter_app.py` — l'interface Tkinter elle-même (`MediaSorterApp`) :
  enchaînement des états des boutons pendant l'analyse et la copie, annulation,
  validations, messages affichés. Un vrai `mainloop()` est nécessaire pour que les
  callbacks déclenchés depuis les threads d'arrière-plan s'exécutent ; les vérifications
  d'état "en cours d'opération" bloquent le mock concerné via un `threading.Event`
  plutôt que de deviner un délai
- `tests/test_load.py` — tests de charge génériques : plusieurs milliers de fichiers
  répartis sur des dizaines de dossiers/dates (`scan_media`, agrégation, détection de
  doublons face à un dossier de destination déjà bien rempli)

```bash
venv\Scripts\python -m unittest discover -s tests -v   # Windows
venv/bin/python -m unittest discover -s tests -v        # Linux / macOS
```

L'ensemble de la suite (tests unitaires + charge) s'exécute en quelques secondes.

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
`requirements.txt` puis relancer `packaging\build.bat`).
