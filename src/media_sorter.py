"""Tri de photos et vidéos par date - logique de scan/tri/copie et point d'entrée
(cross-platform). L'interface Tkinter (MediaSorterApp) vit dans app_ui.py, qui délègue
ici toute la logique métier.

Copyright (C) 2026 Guillaume Pataut

Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le modifier
selon les termes de la GNU General Public License telle que publiée par la Free
Software Foundation, soit la version 3 de la licence, soit (à votre choix) toute
version ultérieure.

Ce programme est distribué dans l'espoir qu'il sera utile, mais SANS AUCUNE GARANTIE ;
sans même la garantie implicite de COMMERCIALISATION ou d'ADÉQUATION À UN USAGE
PARTICULIER. Voir la GNU General Public License pour plus de détails.

Vous devez avoir reçu une copie de la GNU General Public License avec ce programme.
Si ce n'est pas le cas, voir <https://www.gnu.org/licenses/>.
"""

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import tkinter as tk

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:
    HEIF_SUPPORTED = False

from media_date_utils import date_from_filename
from photo_metadata import get_photo_exif_date
from video_metadata import get_video_creation_date


def _log_directory() -> Path:
    """Dossier de logs, selon la plateforme (Windows : %LOCALAPPDATA%/TriPhotos ; macOS :
    ~/Library/Logs/TriPhotos ; Linux/autre : ~/.local/share/TriPhotos).

    TRIPHOTOS_LOG_DIR, si définie, prend le pas sur tout le reste : utilisée par les
    tests pour ne jamais écrire dans le vrai dossier de logs de la machine qui les
    exécute (voir tests/test_load.py, test_media_sorter.py, test_app_ui.py).
    """
    override = os.environ.get("TRIPHOTOS_LOG_DIR")
    if override:
        return Path(override)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "TriPhotos"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "TriPhotos"
    return Path.home() / ".local" / "share" / "TriPhotos"


def _configure_logging() -> logging.Logger:
    """Journalise dans un fichier local les exceptions inattendues rencontrées pendant
    l'analyse ou la copie (voir get_media_date, copy_files, et _scan_worker/_copy_worker
    dans app_ui.py) : sans ça, un fichier classé à tort dans No Info ou une copie en échec
    ne laisse aucune trace au-delà du message affiché une fois à l'écran, rendant tout
    diagnostic a posteriori impossible (bug de parseur ? fichier réellement corrompu ?
    accès disque ?).

    N'affecte jamais le comportement utilisateur existant (classement en No Info, message
    d'erreur) : purement un journal de diagnostic en plus. Si le dossier de logs n'est pas
    accessible en écriture, l'application continue simplement sans journalisation plutôt
    que d'échouer pour cette seule raison.
    """
    logger = logging.getLogger("triphotos")
    logger.setLevel(logging.WARNING)
    try:
        log_dir = _log_directory()
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "triphotos.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        pass
    return logger


logger = _configure_logging()


def _config_directory() -> Path:
    """Dossier de configuration utilisateur, même logique multi-plateforme que
    _log_directory (override via TRIPHOTOS_CONFIG_DIR pour les tests, afin de ne jamais
    lire/écrire les vraies préférences de la machine qui les exécute).
    """
    override = os.environ.get("TRIPHOTOS_CONFIG_DIR")
    if override:
        return Path(override)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "TriPhotos"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TriPhotos"
    return Path.home() / ".local" / "share" / "TriPhotos"


PREFERENCES_FILE_NAME = "preferences.json"
DEFAULT_PREFERENCES = {"sort_level": "jour", "separate_media": False, "copy_mode": "copier"}


def load_preferences() -> dict:
    """Charge le niveau de tri, la séparation Photos/Vidéos et le mode copier/déplacer
    choisis lors du dernier lancement, pour ne pas avoir à les reconfigurer à chaque
    fois. Retourne les valeurs par défaut si le fichier n'existe pas encore, ou si son
    contenu est illisible/corrompu/invalide : ce réglage est un simple confort, jamais
    une condition requise pour utiliser l'application.
    """
    preferences = dict(DEFAULT_PREFERENCES)
    path = _config_directory() / PREFERENCES_FILE_NAME
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return preferences
    if not isinstance(saved, dict):
        return preferences
    if saved.get("sort_level") in SORT_LEVELS:
        preferences["sort_level"] = saved["sort_level"]
    if isinstance(saved.get("separate_media"), bool):
        preferences["separate_media"] = saved["separate_media"]
    if saved.get("copy_mode") in ("copier", "deplacer"):
        preferences["copy_mode"] = saved["copy_mode"]
    return preferences


def save_preferences(sort_level: str, separate_media: bool, copy_mode: str) -> None:
    """Sauvegarde le niveau de tri, la séparation Photos/Vidéos et le mode
    copier/déplacer pour le prochain lancement. Échoue silencieusement si le dossier de
    configuration n'est pas accessible en écriture (voir load_preferences) : ce n'est
    qu'un confort, pas une fonctionnalité critique.
    """
    config_dir = _config_directory()
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / PREFERENCES_FILE_NAME
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"sort_level": sort_level, "separate_media": separate_media, "copy_mode": copy_mode},
                f,
            )
    except OSError:
        pass


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".webp",
    # RAW (CR2 Canon, NEF Nikon, ARW Sony) : aucun décodage d'image nécessaire ici (le
    # transfert copie/déplace le fichier tel quel, voir transfer_file), seule la lecture
    # de date nous concerne. Ces formats étant construits sur le conteneur TIFF, Pillow
    # les ouvre via le même chemin générique que .tiff/.tif (voir photo_metadata.py) et y
    # lit l'EXIF DateTimeOriginal normalement, sans dépendance supplémentaire.
    ".cr2", ".nef", ".arw",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv", ".mpg", ".mpeg", ".webm",
}

CATEGORY_LABELS = {"photos": "Photos", "videos": "Vidéos"}

MEDIA_CATEGORY_BY_EXTENSION = {
    **{ext: "photos" for ext in IMAGE_EXTENSIONS},
    **{ext: "videos" for ext in VIDEO_EXTENSIONS},
}

NO_INFO_LABEL = "No Info"

# Causes de classement sous NO_INFO_LABEL que explain_no_info() sait distinguer sans
# rouvrir/reparser le fichier (voir explain_no_info) : uniquement les deux cas connus et
# actionnables par l'utilisateur, le reste étant regroupé sous un motif générique.
UNSUPPORTED_DATE_EXTENSIONS = {".gif", ".bmp", ".mpg", ".mpeg"}
HEIF_EXTENSIONS = {".heic", ".heif"}

NO_INFO_REASON_UNSUPPORTED_FORMAT = "Format sans date exploitable (GIF, BMP, MPG/MPEG)"
NO_INFO_REASON_HEIF_PLUGIN_MISSING = "HEIC/HEIF : installez pillow-heif pour lire sa date"
NO_INFO_REASON_NO_USABLE_DATE = "Aucune date exploitable dans les métadonnées"


def explain_no_info(path: Path) -> str:
    """Explique, pour l'affichage, pourquoi un fichier a été classé sous NO_INFO_LABEL
    (get_media_date a renvoyé None pour lui).

    Réponse best-effort à but indicatif seulement : distinguer précisément "tag de date
    absent sur ce fichier précis" de "date rejetée par le filtre de plausibilité" ou
    "fichier corrompu" supposerait de dupliquer la logique interne de chaque parseur
    (photo_metadata.py, video_metadata.py — en particulier les quatre parseurs vidéo,
    chacun avec son propre format de conteneur). Seules les deux causes connues et sur
    lesquelles l'utilisateur peut agir sont donc isolées ; tout le reste tombe dans un
    motif générique.
    """
    suffix = path.suffix.lower()
    if suffix in UNSUPPORTED_DATE_EXTENSIONS:
        return NO_INFO_REASON_UNSUPPORTED_FORMAT
    if suffix in HEIF_EXTENSIONS and not HEIF_SUPPORTED:
        return NO_INFO_REASON_HEIF_PLUGIN_MISSING
    return NO_INFO_REASON_NO_USABLE_DATE


def group_no_info_by_reason(node: dict) -> dict:
    """Parcourt un arbre d'affichage (voir build_display_tree) et remplace, à tout
    niveau où elle apparaît, la liste plate de NO_INFO_LABEL par un sous-arbre
    {raison: [fichiers]} (voir explain_no_info) — pour que l'aperçu explique pourquoi ces
    fichiers n'ont pas pu être datés plutôt que de se contenter de les compter.

    Ne modifie que l'arbre affiché : la copie continue de traiter NO_INFO_LABEL comme un
    seul dossier (voir build_destination_map, qui travaille sur l'arbre brut, jamais sur
    celui-ci) — expliquer la cause est un aspect purement visuel, pas une règle de
    classement des fichiers.
    """
    result = {}
    for key, value in node.items():
        if key == NO_INFO_LABEL and isinstance(value, list):
            grouped = {}
            for path in value:
                grouped.setdefault(explain_no_info(path), []).append(path)
            result[key] = grouped
        elif isinstance(value, dict):
            result[key] = group_no_info_by_reason(value)
        else:
            result[key] = value
    return result


def get_media_date(path: Path, use_filename_fallback: bool = False):
    """Renvoie la date de prise de vue/création lue dans les métadonnées (EXIF ou
    vidéo), ou None si aucune métadonnée de date n'est trouvée.

    Délègue à photo_metadata (EXIF, silencieusement absent sur GIF/BMP) ou
    video_metadata (MP4/MOV/M4V/3GP, AVI, WMV, MKV/WEBM ; MPG/MPEG n'a pas d'équivalent
    standardisé et renvoie donc toujours None) selon l'extension. La date de
    modification du fichier n'est volontairement pas utilisée en repli : ce n'est pas
    une date fiable de prise de vue (copie, transfert... la modifient sans rapport avec
    le contenu) — voir scan_media pour le classement des fichiers sans date dans
    NO_INFO_LABEL.

    Si use_filename_fallback est vrai et qu'aucune métadonnée n'a été trouvée, une
    dernière tentative est faite via date_from_filename (voir media_date_utils) — un
    repli optionnel, désactivé par défaut, activé explicitement par l'utilisateur
    (case à cocher) plutôt qu'appliqué silencieusement à tous les fichiers.
    """
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        try:
            date = get_photo_exif_date(path)
        except Exception:
            logger.warning("Lecture de la date EXIF échouée pour %s", path, exc_info=True)
            date = None
    else:
        try:
            date = get_video_creation_date(path)
        except Exception:
            logger.warning("Lecture de la date vidéo échouée pour %s", path, exc_info=True)
            date = None
    if date is None and use_filename_fallback:
        date = date_from_filename(path.name)
    return date


class ScanCancelled(Exception):
    """Levée par scan_media() quand cancel_event est déclenché en cours d'analyse."""


def list_media_files(source_dir: Path, recursive: bool = True, cancel_event: threading.Event = None) -> list:
    """Énumère les fichiers photo/vidéo de source_dir (récursivement par défaut, ou
    uniquement à sa racine), sans lire leurs métadonnées. Renvoie une liste de
    (Path, catégorie).

    Utilise os.walk()/os.scandir() plutôt que Path.rglob()/glob() : ces derniers ne
    renvoient qu'un chemin, sans indiquer s'il s'agit d'un fichier ou d'un dossier, ce
    qui obligeait un path.is_file() séparé — un aller-retour disque de plus par entrée.
    os.walk()/os.scandir() connaissent déjà ce type (attribut renvoyé par le système de
    fichiers lors de la lecture du dossier) et le réutilisent sans appel supplémentaire.
    Sensible sur un dossier réseau/externe lent avec des centaines de milliers
    d'entrées, où chaque aller-retour disque de plus s'additionne (l'énumération
    elle-même n'est pas parallélisée, contrairement à la lecture des dates).

    Utilisé par scan_media (avant la lecture des dates, potentiellement longue) et par
    count_media_files (comptage rapide pour donner un ordre de grandeur avant de lancer
    l'analyse complète).

    Ignore les liens symboliques (et jonctions Windows) : copy_files/transfer_file
    suivraient sinon le lien jusqu'à sa cible réelle (comportement par défaut de
    shutil.copy2 et open()), ce qui recopierait le contenu d'un fichier potentiellement
    situé hors de source_dir vers la destination — un lien nommé comme une photo mais
    pointant ailleurs sur le disque ne doit pas pouvoir faire sortir un fichier
    quelconque de son emplacement d'origine.
    """
    candidates = []
    if recursive:
        for dirpath, _dirnames, filenames in os.walk(source_dir):
            for filename in filenames:
                if cancel_event is not None and cancel_event.is_set():
                    raise ScanCancelled()
                category = MEDIA_CATEGORY_BY_EXTENSION.get(Path(filename).suffix.lower())
                if category is None:
                    continue
                candidate_path = Path(dirpath) / filename
                if candidate_path.is_symlink():
                    continue
                candidates.append((candidate_path, category))
    else:
        try:
            with os.scandir(source_dir) as it:
                for entry in it:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ScanCancelled()
                    try:
                        if entry.is_symlink() or not entry.is_file():
                            continue
                    except OSError:
                        continue  # supprimé/inaccessible entre l'énumération et cette vérification
                    category = MEDIA_CATEGORY_BY_EXTENSION.get(Path(entry.name).suffix.lower())
                    if category is not None:
                        candidates.append((Path(entry.path), category))
        except OSError:
            pass
    return candidates


def _as_source_dir_list(source_dirs) -> list:
    """Normalise source_dirs — un chemin unique ou une liste de chemins — en liste de
    Path, pour que count_media_files/scan_media acceptent indifféremment les deux (voir
    app_ui.py, qui permet d'analyser plusieurs dossiers sources en une seule fois :
    cartes SD de plusieurs appareils, exports de plusieurs téléphones)."""
    if isinstance(source_dirs, (str, Path)):
        return [Path(source_dirs)]
    return [Path(d) for d in source_dirs]


def resolve_path(path: Path) -> Path:
    """Résout un chemin, en absorbant OSError (chemin réseau capricieux, boucle de liens
    symboliques) en retombant sur le chemin non résolu plutôt que de laisser l'exception
    remonter. Partagée entre app_ui.py et cli.py, les deux consommateurs de ce module qui
    comparent des chemins fournis par l'utilisateur — une résolution non protégée dans
    l'un des deux a déjà fait planter l'UI sur ce point précis (voir start_copy)."""
    try:
        return path.resolve()
    except OSError:
        return path


def is_destination_nested_in_sources(dest, sources) -> bool:
    """Vrai si dest est égal à l'un des dossiers de sources, ou un sous-dossier de l'un
    d'eux (une fois tous deux résolus via resolve_path) — le garde-fou empêchant de
    copier/déplacer des fichiers dans un dossier en cours d'analyse (risque de doublons
    en cascade, voire de boucle en mode récursif).

    sources peut contenir des chemins déjà résolus ou non : resolve_path() est idempotente,
    une double résolution ne change rien au résultat. Partagée entre app_ui.py (start_copy)
    et cli.py (run_cli) plutôt que dupliquée : chacun affiche l'erreur à sa façon (messagebox
    contre stderr), mais la condition elle-même n'a qu'un seul endroit où évoluer.
    """
    resolved_dest = resolve_path(Path(dest))
    return any(
        resolved_dest == resolved_source or resolved_dest.is_relative_to(resolved_source)
        for resolved_source in (resolve_path(Path(source)) for source in sources)
    )


def count_media_files(source_dirs, recursive: bool = True) -> dict:
    """Compte rapidement les fichiers photo/vidéo d'un ou plusieurs dossiers sources
    (voir _as_source_dir_list) par catégorie, sans lire leurs métadonnées (contrairement
    à scan_media) : donne un ordre de grandeur avant de lancer l'analyse complète, qui
    peut être bien plus longue sur un gros dossier.
    """
    counts = {category: 0 for category in CATEGORY_LABELS}
    for source_dir in _as_source_dir_list(source_dirs):
        for _path, category in list_media_files(source_dir, recursive):
            counts[category] += 1
    return counts


def scan_media(
    source_dirs, recursive: bool = True, cancel_event: threading.Event = None, max_workers: int = None,
    use_filename_fallback: bool = False,
):
    """Parcourt un ou plusieurs dossiers sources (voir _as_source_dir_list — récursivement
    par défaut, ou uniquement à leur racine) et regroupe photos et vidéos par catégorie
    ("photos" / "videos") puis par (année, mois, jour), tous dossiers sources confondus :
    un fichier identique présent dans deux dossiers sources sera vu deux fois ici (aucune
    déduplication au scan, qui reste basée sur le contenu à la copie — voir transfer_file).

    La lecture de la date (get_media_date, dominée par des I/O disque : ouverture de
    fichier, lecture d'en-tête EXIF ou vidéo) est parallélisée sur plusieurs threads —
    le nombre de fichiers ne dépend pas de la récursivité (un dossier plat peut tout
    autant en contenir des milliers), donc le gain s'applique dans les deux cas.

    Un fichier sans date exploitable dans ses métadonnées (get_media_date renvoie None)
    est classé à part, sous la clé NO_INFO_LABEL, plutôt que d'être daté approximativement
    par sa date de modification — sauf si use_filename_fallback est vrai et qu'une date
    est détectée dans son nom (voir get_media_date), auquel cas il est daté normalement.

    Si cancel_event est fourni et déclenché pendant l'analyse (par ex. depuis un autre
    thread), lève ScanCancelled dès que possible plutôt que d'aller au bout : pendant le
    parcours du dossier, ou entre deux résultats une fois la lecture des dates lancée.
    """
    tree = {category: {} for category in CATEGORY_LABELS}
    candidates = []
    for source_dir in _as_source_dir_list(source_dirs):
        candidates.extend(list_media_files(source_dir, recursive, cancel_event))

    if not candidates:
        return tree

    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        future_to_candidate = {
            executor.submit(get_media_date, path, use_filename_fallback): (path, category)
            for path, category in candidates
        }
        for future in as_completed(future_to_candidate):
            if cancel_event is not None and cancel_event.is_set():
                raise ScanCancelled()
            path, category = future_to_candidate[future]
            try:
                date = future.result()
            except OSError:
                # Fichier supprimé ou devenu inaccessible entre l'énumération et la
                # lecture de sa date (course avec un autre programme, partage réseau
                # débranché, chemin trop long) : on l'ignore plutôt que de faire échouer
                # toute l'analyse et perdre le travail déjà accompli sur les autres.
                continue
            if date is None:
                tree[category].setdefault(NO_INFO_LABEL, []).append(path)
                continue
            year, month, day = str(date.year), f"{date.month:02d}", f"{date.day:02d}"
            tree[category].setdefault(year, {}).setdefault(month, {}).setdefault(day, []).append(path)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return tree


def merge_media_trees(tree: dict) -> dict:
    """Fusionne les arbres photos et vidéos en un seul arbre année/mois/jour, en gardant
    à part les fichiers sans date (NO_INFO_LABEL, une simple liste plutôt qu'un
    sous-arbre mois/jour)."""
    merged = {}
    for category_tree in tree.values():
        for year, months in category_tree.items():
            if year == NO_INFO_LABEL:
                merged.setdefault(year, []).extend(months)
                continue
            for month, days in months.items():
                for day, files in days.items():
                    merged.setdefault(year, {}).setdefault(month, {}).setdefault(day, []).extend(files)
    return merged


MONTH_NAMES_FR = {
    "01": "Janvier", "02": "Février", "03": "Mars", "04": "Avril",
    "05": "Mai", "06": "Juin", "07": "Juillet", "08": "Août",
    "09": "Septembre", "10": "Octobre", "11": "Novembre", "12": "Décembre",
}

SORT_LEVELS = {
    "annee": ("Année", 1),
    "mois": ("Année / Mois", 2),
    "jour": ("Année / Mois / Jour", 3),
}


def month_folder_name(month: str) -> str:
    """Formate un mois ("01") en nom de dossier ("01-Janvier")."""
    return f"{month}-{MONTH_NAMES_FR.get(month, month)}"


def path_parts_to_folder_names(path_parts):
    """Convertit un chemin (année, mois, jour) en noms de dossiers, en habillant le mois."""
    parts = list(path_parts)
    if len(parts) >= 2:
        parts[1] = month_folder_name(parts[1])
    return parts


def aggregate_tree(tree: dict, level: str) -> dict:
    """Regroupe l'arbre complet (année/mois/jour) selon le niveau de tri choisi.

    Le niveau étant imbriqué (jour implique mois et année), on ne fait
    qu'arrêter la descente plus tôt et fusionner les fichiers des niveaux
    inférieurs dans les feuilles.

    Les fichiers sans date (NO_INFO_LABEL) restent un dossier à part entière, non
    subdivisé, quel que soit le niveau de tri choisi.
    """
    depth = SORT_LEVELS[level][1]
    result = {}
    for year, months in tree.items():
        if year == NO_INFO_LABEL:
            result.setdefault(year, []).extend(months)
            continue
        if depth == 1:
            result.setdefault(year, []).extend(
                f for days in months.values() for files in days.values() for f in files
            )
            continue
        for month, days in months.items():
            if depth == 2:
                result.setdefault(year, {}).setdefault(month, []).extend(
                    f for files in days.values() for f in files
                )
                continue
            for day, files in days.items():
                result.setdefault(year, {}).setdefault(month, {}).setdefault(day, []).extend(files)
    return result


def flatten_tree(node):
    """Parcourt un arbre agrégé et renvoie des tuples (chemin, fichiers) pour chaque feuille."""
    if isinstance(node, list):
        yield (), node
        return
    for key in sorted(node):
        for sub_path, files in flatten_tree(node[key]):
            yield (key,) + sub_path, files


def count_files(node) -> int:
    if isinstance(node, list):
        return len(node)
    return sum(count_files(v) for v in node.values())


def format_duration(seconds: float) -> str:
    """Formate une durée pour l'affichage : en millisecondes en dessous d'une seconde,
    sinon en secondes avec une décimale."""
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.1f} s"


def build_display_tree(tree: dict, level: str, separate_media: bool) -> dict:
    """Construit l'arbre à afficher dans l'aperçu, selon le niveau de tri et le choix
    de séparer ou non Photos et Vidéos à la racine. Le dossier NO_INFO_LABEL, s'il est
    présent, est en plus sous-divisé par cause probable (voir group_no_info_by_reason)."""
    if separate_media:
        display = {
            CATEGORY_LABELS[category]: aggregate_tree(category_tree, level)
            for category, category_tree in tree.items()
            if count_files(category_tree) > 0
        }
    else:
        display = aggregate_tree(merge_media_trees(tree), level)
    return group_no_info_by_reason(display)


def build_destination_map(tree: dict, level: str, separate_media: bool) -> dict:
    """Construit le mapping {chemin de dossiers : fichiers} prêt à être copié/déplacé,
    selon le niveau de tri et le choix de séparer ou non Photos et Vidéos à la racine.
    """
    if separate_media:
        sources = [
            (CATEGORY_LABELS[category], category_tree)
            for category, category_tree in tree.items()
            if count_files(category_tree) > 0
        ]
    else:
        sources = [(None, merge_media_trees(tree))]

    result = {}
    for category_label, category_tree in sources:
        aggregated = aggregate_tree(category_tree, level)
        for path_parts, files in flatten_tree(aggregated):
            folder_names = path_parts_to_folder_names(path_parts)
            if category_label:
                folder_names = [category_label] + folder_names
            result.setdefault(tuple(folder_names), []).extend(files)
    return result


def file_hash(path: Path, chunk_size: int = 65536) -> str:
    """Calcule le hash SHA-256 du contenu d'un fichier, pour détecter les doublons."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def partial_file_hash(path: Path, sample_size: int = 65536) -> str:
    """Hash SHA-256 du début et de la fin d'un fichier (jusqu'à sample_size octets
    chacun), utilisé comme pré-filtre bon marché avant file_hash().

    Deux fichiers de même taille mais de hash partiel différent sont garantis
    différents (leur contenu diffère forcément quelque part dans la tête ou la queue) :
    inutile de les hacher en entier pour le savoir. Un hash partiel identique reste à
    confirmer par file_hash(), seul juge fiable d'une égalité complète — deux fichiers
    peuvent en théorie partager leurs premiers/derniers octets sans être identiques.
    """
    digest = hashlib.sha256()
    size = path.stat().st_size
    with open(path, "rb") as f:
        digest.update(f.read(sample_size))
        if size > sample_size:
            f.seek(max(size - sample_size, sample_size))
            digest.update(f.read(sample_size))
    return digest.hexdigest()


def dated_filename(path: Path, use_filename_fallback: bool = False) -> str:
    """Construit un nom de fichier basé sur la date de prise de vue/création
    (AAAA-MM-JJ_HHMMSS), pour le renommage optionnel lors du transfert (voir
    transfer_file). Relit la métadonnée plutôt que de réutiliser celle déjà lue par
    scan_media : celle-ci n'est pas conservée au-delà du regroupement par année/mois/jour,
    et cette relecture ne porte que sur l'en-tête du fichier (bon marché, y compris pour
    une vidéo). Renvoie le nom d'origine si aucune date exploitable n'est trouvée.

    use_filename_fallback doit refléter le même réglage que celui utilisé pour l'analyse
    (voir scan_media) : sans quoi un fichier daté via son nom pendant l'analyse (et donc
    sorti de NO_INFO_LABEL) se verrait ici refuser cette même date, gardant son nom
    d'origine au lieu d'être renommé de façon cohérente avec les autres fichiers.
    """
    date = get_media_date(path, use_filename_fallback)
    if date is None:
        return path.name
    return f"{date.strftime('%Y-%m-%d_%H%M%S')}{path.suffix}"


def unique_destination(dest_dir: Path, filename: str) -> Path:
    """Évite d'écraser un fichier existant en suffixant _1, _2, ... en cas de collision.

    Le test d'existence puis l'écriture réelle (shutil.copy2/move, dans transfer_file)
    sont deux étapes séparées, non atomiques : une autre instance de l'outil (ou un
    autre programme) écrivant au même moment sous le même nom dans dest_dir pourrait
    en théorie écraser silencieusement le fichier retourné ici. Risque accepté
    délibérément plutôt que corrigé par une réservation atomique (os.open avec
    O_CREAT | O_EXCL) : celle-ci forcerait shutil.move à toujours retomber sur une
    copie + suppression sous Windows (os.rename y échoue si la destination existe déjà,
    y compris pour un simple fichier vide fraîchement réservé), remplaçant un
    renommage instantané par une copie intégrale à chaque déplacement — un coût réel et
    systématique pour parer une course qui suppose deux instances de l'outil actives en
    parallèle sur le même dossier de destination, un scénario hors du cadre d'usage
    prévu (un seul utilisateur, une seule instance ; voir _set_options_locked qui
    empêche déjà scan et copie concurrents au sein d'une même instance)."""
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    i = 1
    while True:
        candidate = dest_dir / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def build_pending_size_index(target_dir: Path) -> dict:
    """Indexe par taille (en octets) les fichiers déjà présents dans target_dir, sans les
    hasher. Utilisé par transfer_file() avec un dict "hashed_by_size" (initialement
    vide) pour ne calculer de hash (d'abord partiel, puis complet seulement si
    nécessaire — voir transfer_file) que lorsqu'un autre fichier de taille identique
    apparaît réellement : la plupart des fichiers n'ont pas de doublon de taille
    identique, ce qui évite l'essentiel du hachage — en particulier coûteux pour de
    grosses vidéos, à l'inverse des parseurs de date qui évitent déjà soigneusement de
    les lire en entier.
    """
    index = {}
    for existing_file in target_dir.iterdir():
        if existing_file.is_file():
            try:
                size = existing_file.stat().st_size
            except OSError:
                continue
            index.setdefault(size, []).append(existing_file)
    return index


def transfer_file(
    src_file: Path, target_dir: Path, pending_by_size: dict, hashed_by_size: dict, mode: str,
    rename_files: bool = False, use_filename_fallback: bool = False,
) -> str:
    """Copie ou déplace src_file vers target_dir, sauf si son contenu s'y trouve déjà.

    Détection de doublons à trois niveaux, chacun paresseux (calculé seulement quand le
    niveau précédent a réellement collisionné) :
    1. Taille (pending_by_size, voir build_pending_size_index) : la plupart des
       fichiers n'ont pas de doublon de taille identique, ce qui évite l'essentiel du
       hachage, même partiel.
    2. Hash partiel (voir partial_file_hash), bon marché : ne sert qu'à confirmer que
       deux fichiers de même taille ont vraiment une chance d'être identiques. Sans ce
       niveau intermédiaire, des fichiers de taille strictement identique mais de
       contenu différent (dashcams/caméras à segments de taille fixe, par exemple)
       seraient chacun haché intégralement en pure perte.
    3. Hash SHA-256 complet (file_hash), seul juge fiable d'une égalité de contenu :
       calculé uniquement pour les fichiers dont la taille ET le hash partiel
       coïncident déjà avec un autre fichier.

    hashed_by_size (dict {taille: {hash_partiel: {"pending": [Path, ...], "hashes":
    {hash_complet, ...}}}}, à initialiser vide) et pending_by_size forment ensemble
    l'index des fichiers déjà connus dans target_dir.

    Si rename_files est vrai, le fichier transféré est renommé selon sa date de
    prise de vue/création (voir dated_filename) plutôt que de garder son nom d'origine.
    La détection de doublons ci-dessus porte toujours sur le contenu, jamais sur le nom :
    un doublon renommé reste détecté comme tel.

    En mode "deplacer", un doublon est tout de même supprimé de la source puisqu'une
    copie de son contenu existe déjà à destination. Renvoie "duplicate", "copied" ou
    "moved".
    """
    try:
        size = src_file.stat().st_size
    except OSError:
        size = None

    if size is not None and size in pending_by_size:
        partial_buckets = hashed_by_size.setdefault(size, {})
        for candidate in pending_by_size.pop(size):
            try:
                partial = partial_file_hash(candidate)
            except OSError:
                continue
            partial_buckets.setdefault(partial, {"pending": [], "hashes": set()})["pending"].append(candidate)

    src_partial = None
    src_hash = None
    is_duplicate = False
    partial_buckets = hashed_by_size.get(size) if size is not None else None
    if partial_buckets:
        try:
            src_partial = partial_file_hash(src_file)
        except OSError:
            src_partial = None
        bucket = partial_buckets.get(src_partial) if src_partial is not None else None
        if bucket is not None:
            for candidate in bucket["pending"]:
                try:
                    bucket["hashes"].add(file_hash(candidate))
                except OSError:
                    pass
            bucket["pending"] = []
            src_hash = file_hash(src_file)
            is_duplicate = src_hash in bucket["hashes"]

    if is_duplicate:
        if mode == "deplacer":
            src_file.unlink()
        return "duplicate"

    filename = dated_filename(src_file, use_filename_fallback) if rename_files else src_file.name
    dst_file = unique_destination(target_dir, filename)
    if mode == "deplacer":
        shutil.move(str(src_file), str(dst_file))
    else:
        shutil.copy2(src_file, dst_file)

    if size is not None:
        if src_hash is not None:
            hashed_by_size[size][src_partial]["hashes"].add(src_hash)
        elif src_partial is not None:
            hashed_by_size[size].setdefault(src_partial, {"pending": [], "hashes": set()})["pending"].append(dst_file)
        else:
            pending_by_size.setdefault(size, []).append(dst_file)

    return "moved" if mode == "deplacer" else "copied"


class CopyCancelled(Exception):
    """Levée par copy_files() quand cancel_event est déclenché en cours de copie.

    Porte la progression déjà accomplie (done, duplicates, errors) en arguments, pour
    que l'appelant puisse rendre compte de ce qui a été transféré avant l'arrêt.
    """


def copy_files(
    destination_map: dict, dest_path: Path, mode: str,
    cancel_event: threading.Event = None, on_progress=None, rename_files: bool = False,
    use_filename_fallback: bool = False,
) -> tuple:
    """Copie ou déplace chaque groupe de fichiers de destination_map (voir
    build_destination_map) vers dest_path, un sous-dossier par clé, avec détection de
    doublons par dossier de destination (voir transfer_file).

    Si cancel_event est fourni et déclenché en cours de route, lève CopyCancelled dès le
    fichier suivant : l'annulation est coopérative, un transfert déjà commencé va
    jusqu'au bout (impossible d'interrompre un shutil.copy2/move en cours), seul le
    fichier suivant ne démarre pas.

    on_progress(done), si fourni, est appelé après chaque fichier traité (succès,
    doublon ou erreur).

    rename_files et use_filename_fallback sont transmis tels quels à transfer_file (voir
    dated_filename) — use_filename_fallback doit refléter le réglage utilisé pour
    l'analyse qui a produit destination_map, sinon un fichier daté via son nom pendant
    l'analyse serait renommé à tort avec son nom d'origine.

    Renvoie (done, duplicates, errors).
    """
    done = 0
    duplicates = 0
    errors = []
    for folder_names, files in destination_map.items():
        target_dir = dest_path.joinpath(*folder_names)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            pending_by_size = build_pending_size_index(target_dir)
        except Exception as exc:
            # Un dossier de destination inaccessible (échec de mkdir, ou accessible au
            # moment du mkdir mais plus au moment de lister son contenu — partage réseau
            # débranché entre les deux) ne doit pas abandonner toute la copie : seuls les
            # fichiers de CE dossier sont comptés en erreur, les autres continuent.
            logger.warning("Dossier de destination inaccessible : %s", target_dir, exc_info=True)
            for src_file in files:
                if cancel_event is not None and cancel_event.is_set():
                    raise CopyCancelled(done, duplicates, errors)
                errors.append(f"{src_file}: {exc}")
                done += 1
                if on_progress is not None:
                    on_progress(done)
            continue

        hashed_by_size = {}

        for src_file in files:
            if cancel_event is not None and cancel_event.is_set():
                raise CopyCancelled(done, duplicates, errors)
            try:
                result = transfer_file(
                    src_file, target_dir, pending_by_size, hashed_by_size, mode, rename_files, use_filename_fallback,
                )
                if result == "duplicate":
                    duplicates += 1
            except Exception as exc:
                logger.warning("Échec du transfert de %s", src_file, exc_info=True)
                errors.append(f"{src_file}: {exc}")
            done += 1
            if on_progress is not None:
                on_progress(done)
    return done, duplicates, errors


def summarize_transfer(done: int, duplicates: int, errors: list, mode: str) -> tuple:
    """Calcule les éléments de résumé communs aux trois affichages d'un transfert
    (CLI, fin de copie et annulation côté UI, voir cli.py et app_ui.py) à partir du
    résultat de copy_files() : nombre de fichiers réellement transférés, libellé de
    l'action au passé, et texte décrivant les doublons rencontrés. Centralisé ici plutôt
    que réécrit trois fois indépendamment, pour que ce calcul et le choix des libellés
    n'aient qu'un seul endroit où évoluer — la composition de la phrase finale reste
    propre à chaque appelant (le message diffère selon annulation/succès/erreurs).

    Renvoie un tuple (transferred, action_past, duplicates_text), dans le même esprit
    que le tuple renvoyé par copy_files() lui-même.
    """
    transferred = done - duplicates - len(errors)
    action_past = "déplacé(s)" if mode == "deplacer" else "copié(s)"
    duplicate_word = "supprimé(s) de la source" if mode == "deplacer" else "ignoré(s)"
    duplicates_text = f"{duplicates} doublon(s) {duplicate_word}"
    return transferred, action_past, duplicates_text


def main():
    # Des arguments en ligne de commande signalent un usage automatisé (voir cli.py) :
    # sans argument, comportement inchangé (lancement de l'interface Tkinter).
    if len(sys.argv) > 1:
        from cli import run_cli

        sys.exit(run_cli(sys.argv[1:]))

    from app_ui import MediaSorterApp

    root = tk.Tk()
    MediaSorterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
