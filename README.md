# Tri de photos et vidéos par date

[![Tests](https://github.com/papainge/Tri-Photos/actions/workflows/tests.yml/badge.svg)](https://github.com/papainge/Tri-Photos/actions/workflows/tests.yml)

Application locale (Python + Tkinter) qui analyse un dossier de photos et vidéos,
propose une arborescence par date (Année, Année / Mois, ou Année / Mois / Jour, au
choix) basée sur la date de prise de vue ou de création (métadonnées EXIF pour les
photos, métadonnées embarquées pour la plupart des formats vidéo — les fichiers sans
métadonnée exploitable sont classés à part, dans un dossier "No Info"), puis copie ou
déplace les fichiers dans un nouveau dossier selon cette arborescence — avec la
possibilité de séparer Photos et Vidéos à la racine de la destination.

Fonctionne sous Windows, Linux et macOS.

## Structure du projet

```
src/media_sorter.py     Point d'entrée ; logique de scan/tri/copie/hash (sans dépendance Tkinter)
src/app_ui.py           Interface Tkinter (MediaSorterApp), délègue tout à media_sorter.py
src/photo_metadata.py   Lecture de la date EXIF des photos
src/video_metadata.py   Lecture de la date de création des vidéos (MP4, AVI, WMV, MKV/WEBM)
src/media_date_utils.py Filtre de plausibilité partagé (écarte les dates aberrantes)
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
- **macOS** : aucun exécutable n'est fourni pour cette plateforme (non prioritaire) ;
  cloner le dépôt et lancer `./run.sh` (voir ci-dessous), ce qui nécessite Python 3.9
  ou plus récent : https://www.python.org/downloads/
- Pour reconstruire vous-même l'exécutable Windows ou Linux : Python 3.9 ou plus
  récent également (cocher "Add python.exe to PATH" lors de l'installation sous
  Windows).

## Lancer l'application

Les exécutables Windows et Linux ne sont pas versionnés dans le dépôt (voir
[Pourquoi pas de binaires dans le dépôt ?](#pourquoi-pas-de-binaires-dans-le-dépôt))
mais publiés sur la [page Releases](https://github.com/papainge/Tri-Photos/releases/latest).

- **Windows** : télécharger `TriPhotos.exe` puis double-cliquer dessus. Aucun terminal
  ne s'affiche, Python n'a pas besoin d'être installé.
- **Linux (x86_64)** : télécharger `TriPhotos`, le rendre exécutable
  (`chmod +x TriPhotos`) puis le lancer (`./TriPhotos`). Binaire autonome, Python n'a pas
  besoin d'être installé. Construit sur Ubuntu 22.04 LTS (glibc 2.35) pour rester
  compatible avec la plupart des distributions encore maintenues ; s'il ne se lance
  malgré tout pas sur une distribution plus ancienne (erreur du type
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

**Important : construire sur une distribution pas trop récente.** Un exécutable
PyInstaller `--onefile` embarque les bibliothèques Tcl/Tk du système de build (pas
celles de la machine qui l'exécute), liées à une version minimale de glibc qui ne fait
que croître avec l'âge de la distribution — un exécutable construit sur une
distribution trop récente refuse de démarrer sur une machine plus ancienne
(`ImportError: ... GLIBC_2.xx not found`, y compris si `python3-tk` y est installé,
puisque ce n'est pas le Tcl/Tk du système qui est utilisé). Une Ubuntu 22.04 LTS
(glibc 2.35) offre un bon compromis : compatible avec la quasi-totalité des
distributions encore maintenues aujourd'hui. Éviter de construire sur une distribution
de développement ou une version très récente (ex. glibc 2.4x).

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
   vidéos à trier. Un nombre total de fichiers photo/vidéo trouvés dans ce dossier
   s'affiche alors immédiatement (comptage rapide, sans lecture des dates) — un ordre
   de grandeur avant de lancer l'analyse complète, potentiellement plus longue. La case
   **Inclure les sous-dossiers** (cochée par défaut) détermine si l'analyse descend
   dans les sous-dossiers ou se limite à la racine du dossier choisi ; la décocher met
   aussi à jour ce comptage rapide immédiatement. Contrairement au niveau de tri et à
   la séparation Photos/Vidéos (voir ci-dessous), ce réglage ne peut pas se contenter
   de réorganiser l'arborescence déjà analysée : la changer après une analyse affiche
   un rappel invitant à recliquer sur **Analyser** pour l'appliquer.
2. Cocher **À défaut, essayer de détecter une date dans le nom du fichier** si vous
   voulez qu'un fichier sans métadonnée exploitable (qui finirait sinon dans `No Info`)
   soit quand même daté à partir de motifs de nom répandus (`IMG_20230715_143022.jpg`,
   `IMG-20230715-WA0001.jpg` sans heure, `2023-07-15 14.30.22.jpg`...). Désactivé par
   défaut : contrairement aux métadonnées, un nom de fichier peut avoir été modifié ou
   ne rien vouloir dire, donc mieux vaut l'activer sciemment. Comme la case précédente,
   ce réglage change quels fichiers obtiennent une date pendant l'analyse elle-même : le
   changer après coup affiche aussi un rappel invitant à relancer **Analyser**.
3. Choisir le **niveau de tri** souhaité : *Année*, *Année / Mois* ou
   *Année / Mois / Jour*. Les niveaux étant imbriqués, choisir *Jour* implique
   automatiquement le mois et l'année, etc. Ce choix peut être changé à tout moment,
   même après l'analyse : l'aperçu se met à jour immédiatement.
4. Cocher **Séparer Photos et Vidéos à la racine de la destination** si vous voulez deux
   arborescences distinctes (`destination/Photos/...` et `destination/Vidéos/...`) au
   lieu d'un classement mélangé par date (comportement par défaut). Ce choix peut aussi
   être changé après l'analyse.
5. Cocher **Renommer les fichiers selon la date (AAAA-MM-JJ_HHMMSS)** si vous voulez
   remplacer le nom d'origine (par ex. `IMG_0001.jpg`, souvent réutilisé à l'identique
   par plusieurs appareils) par un nom basé sur la date de prise de vue/création (par
   ex. `2024-08-15_143022.jpg`). Un fichier sans date exploitable (classé dans
   `No Info`) garde son nom d'origine, faute de date à proposer. Ce réglage n'a d'effet
   qu'au moment de la copie/du déplacement, pas sur l'arborescence de l'aperçu.
6. Cliquer sur **Analyser** : l'arborescence correspondant aux choix ci-dessus, avec le
   nombre de fichiers à chaque niveau, s'affiche. Le total (avec la répartition
   photos/vidéos et le temps qu'a pris l'analyse) reste visible au-dessus de
   l'arborescence pendant toute l'opération. La lecture des dates est parallélisée sur
   plusieurs fichiers à la fois pour accélérer l'analyse des dossiers volumineux. Si elle
   prend malgré tout trop de temps, le bouton **Annuler** (à côté d'Analyser) l'interrompt
   à la volée.
7. Cliquer sur **Choisir...** (destination) pour désigner le dossier dans lequel créer
   l'arborescence. Ce dossier ne peut pas être le dossier source, ni un de ses
   sous-dossiers (refusé au moment de lancer la copie, pour éviter de copier les
   fichiers dans le dossier en cours d'analyse).
8. Choisir l'**action** : *Copier* (les originaux sont conservés, par défaut) ou
   *Déplacer* (les originaux sont supprimés une fois transférés).
9. Cliquer sur **Créer l'arborescence et copier/déplacer les fichiers**. Une copie (ou
   le fichier déplacé) est placée dans le sous-dossier correspondant (par exemple
   `destination/2024/08-Août/15/`, ou `destination/Photos/2024/08-Août/15/` si Photos et
   Vidéos sont séparés). Les fichiers déjà présents dans le dossier de destination (même
   contenu) sont détectés comme doublons : ils ne sont pas dupliqués (et sont supprimés
   de la source en mode *Déplacer*) ; leur nombre est indiqué dans le message final. Comme
   pour l'analyse, un bouton **Annuler** permet d'interrompre l'opération à la volée (le
   fichier en cours de transfert va jusqu'à son terme, seul le suivant ne démarre pas).

## Tests

Chaque module de `src/` a son fichier de tests dédié (module `unittest`) :

- `tests/test_media_date_utils.py` — filtre de plausibilité des dates (bornes acceptées/rejetées)
- `tests/test_photo_metadata.py` — lecture EXIF (photo_metadata.py). Inclut un vrai
  fichier HEIC/HEIF, avec et sans le plugin optionnel `pillow-heif` actif (chaque cas
  isolé dans un sous-processus dédié : `register_heif_opener()` s'enregistre
  globalement pour tout l'interpréteur, sans possibilité de l'annuler, ce qui
  contaminerait les autres tests du même process s'il tournait dans le process
  principal). Ces deux tests sont sautés si `pillow-heif` n'est pas installé
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

Elle est aussi lancée automatiquement sur Windows et Linux (via Xvfb), en Python 3.9
(minimum annoncé ci-dessus) et 3.12, à chaque push et pull request sur `master`
([`.github/workflows/tests.yml`](.github/workflows/tests.yml)) — `pillow-heif` y est
installé pour que les tests HEIC/HEIF ci-dessus s'y exécutent réellement plutôt que
d'y être sautés.

## Formats supportés

- **Photos** : JPEG, PNG, GIF, BMP, TIFF, WEBP, HEIC/HEIF. La date EXIF (DateTimeOriginal,
  puis DateTimeDigitized, puis DateTime) est lue quand le format et le fichier la
  portent (JPEG, TIFF, HEIC/HEIF, PNG, WEBP) ; GIF et BMP n'ont pas de mécanisme EXIF.
- **Vidéos** : MP4, MOV, M4V, 3GP, AVI, WMV, MKV, WEBM, MPG/MPEG. La date de création
  embarquée est lue directement, sans dépendance externe, pour chacun de ces conteneurs
  (sauf MPG/MPEG, qui n'a pas d'équivalent standardisé) :
  - **MP4/MOV/M4V/3GP** : boîte `moov`/`mvhd` (ISO-BMFF/QuickTime).
  - **AVI** : chunk `IDIT` du `LIST INFO` (absent de nombreux AVI modernes).
  - **WMV** : `File Properties Object` (ASF).
  - **MKV/WEBM** : élément `DateUTC` (`Segment`/`Info`, EBML/Matroska).

  Un fichier sans date exploitable dans ses métadonnées (GIF/BMP, MPG/MPEG, ou tout
  autre format dont la métadonnée de date est absente ou aberrante) n'est **pas** daté
  via sa date de modification : il est classé à part, dans un dossier `No Info`, au même
  niveau que les dossiers Année (quel que soit le niveau de tri choisi). Dans l'aperçu,
  ce dossier se déplie pour indiquer la cause probable — format sans mécanisme de date
  (GIF/BMP/MPG-MPEG), HEIC/HEIF nécessitant `pillow-heif` (voir ci-dessous), ou aucune
  date exploitable trouvée pour les autres cas (tag absent sur ce fichier précis, date
  rejetée car aberrante, fichier corrompu...).

  En activant l'option **À défaut, essayer de détecter une date dans le nom du
  fichier** (désactivée par défaut, voir Utilisation ci-dessus), un fichier de ce type
  est quand même daté normalement si son nom suit un motif répandu
  (`IMG_20230715_143022.jpg`, `IMG-20230715-WA0001.jpg`, `2023-07-15 14.30.22.jpg`...).
  Seuls des motifs sans ambiguïté sont reconnus : un nom qui ne contient aucune date
  reconnaissable reste classé dans `No Info`.

Pour la lecture des photos HEIC/HEIF (iPhone), installer en plus `pillow-heif` (version
épinglée dans `requirements-heif.txt`, aussi utilisée en CI) :

```bash
pip install -r requirements-heif.txt
```

(`TriPhotos.exe` et le script `run.sh` n'incluent que Pillow par défaut ; sans
`pillow-heif`, les fichiers HEIC sont classés dans `No Info` faute de pouvoir lire leur
date EXIF. Pour l'inclure dans l'exécutable Windows, ajouter le contenu de
`requirements-heif.txt` à `requirements.txt` puis relancer `packaging\build.bat`).

## Licence

GNU General Public License v3.0 (ou ultérieure) — voir le fichier [LICENSE](LICENSE).
Vous pouvez utiliser, étudier, modifier et redistribuer ce logiciel librement, à
condition que toute version redistribuée (modifiée ou non) reste sous la même
licence et avec son code source disponible.
