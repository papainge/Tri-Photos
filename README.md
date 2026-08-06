# Tri de photos par année / mois

Application locale (Python + Tkinter) qui analyse un dossier de photos, propose une
arborescence Année / Mois basée sur la date de prise de vue (EXIF, avec repli sur la
date de modification du fichier), puis copie les photos dans un nouveau dossier selon
cette arborescence.

Fonctionne sous Windows, Linux et macOS.

## Prérequis

- Python 3.9 ou plus récent : https://www.python.org/downloads/
  (cocher "Add python.exe to PATH" lors de l'installation sous Windows)

## Lancer l'application

- **Windows** : double-cliquer sur `run.bat` (une fenêtre de terminal apparaît
  brièvement puis se ferme dès que l'application est lancée), ou sur
  `run_silent.vbs` pour ne voir apparaître aucun terminal du tout.
- **Linux / macOS** : `./run.sh`

Le script crée automatiquement un environnement virtuel (`venv`) et installe les
dépendances (Pillow) au premier lancement. Avec `run_silent.vbs`, si Python n'est
pas installé ou qu'une erreur survient pendant l'installation, aucun message ne
s'affiche (tout est masqué) : en cas de souci, relancer via `run.bat` pour voir
le message d'erreur.

## Utilisation

1. Cliquer sur **Choisir...** pour sélectionner le dossier contenant les photos à trier.
2. Cliquer sur **Analyser** : l'arborescence Année / Mois avec le nombre de photos par
   mois s'affiche.
3. Cliquer sur **Choisir...** (destination) pour désigner le dossier dans lequel créer
   l'arborescence.
4. Cliquer sur **Créer l'arborescence et copier les photos**. Les photos originales ne
   sont pas modifiées : une copie est placée dans `destination/ANNÉE/MOIS/`.

## Formats supportés

JPEG, PNG, GIF, BMP, TIFF, WEBP, HEIC/HEIF.

Pour la lecture des photos HEIC/HEIF (iPhone), installer en plus `pillow-heif` :

```bash
pip install pillow-heif
```

(les scripts `run.bat` / `run.sh` n'installent que Pillow par défaut ; sans
`pillow-heif`, les fichiers HEIC sont classés selon leur date de modification au lieu
de la date EXIF).
