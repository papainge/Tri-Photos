# Tri de photos par date

Application locale (Python + Tkinter) qui analyse un dossier de photos, propose une
arborescence par date (Année, Année / Mois, ou Année / Mois / Jour, au choix) basée sur
la date de prise de vue (EXIF, avec repli sur la date de modification du fichier), puis
copie les photos dans un nouveau dossier selon cette arborescence.

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
2. Choisir le **niveau de tri** souhaité : *Année*, *Année / Mois* ou
   *Année / Mois / Jour*. Les niveaux étant imbriqués, choisir *Jour* implique
   automatiquement le mois et l'année, etc. Ce choix peut être changé à tout moment,
   même après l'analyse : l'aperçu se met à jour immédiatement.
3. Cliquer sur **Analyser** : l'arborescence correspondant au niveau choisi, avec le
   nombre de photos à chaque niveau, s'affiche.
4. Cliquer sur **Choisir...** (destination) pour désigner le dossier dans lequel créer
   l'arborescence.
5. Cliquer sur **Créer l'arborescence et copier les photos**. Les photos originales ne
   sont pas modifiées : une copie est placée dans le sous-dossier correspondant (par
   exemple `destination/ANNÉE/MOIS/JOUR/` au niveau *Jour*). Les photos déjà présentes
   dans le dossier de destination (même contenu) sont détectées comme doublons et ne
   sont pas copiées ; leur nombre est indiqué dans le message final.

## Formats supportés

JPEG, PNG, GIF, BMP, TIFF, WEBP, HEIC/HEIF.

Pour la lecture des photos HEIC/HEIF (iPhone), installer en plus `pillow-heif` :

```bash
pip install pillow-heif
```

(les scripts `run.bat` / `run.sh` n'installent que Pillow par défaut ; sans
`pillow-heif`, les fichiers HEIC sont classés selon leur date de modification au lieu
de la date EXIF).
