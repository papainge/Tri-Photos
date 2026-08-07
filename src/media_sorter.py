"""Tri de photos et vidéos par date - interface graphique Tkinter (cross-platform).

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
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

from photo_metadata import get_photo_exif_date
from video_metadata import get_video_creation_date

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".webp",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv", ".mpg", ".mpeg", ".webm",
}

CATEGORY_LABELS = {"photos": "Photos", "videos": "Vidéos"}

MEDIA_CATEGORY_BY_EXTENSION = {
    **{ext: "photos" for ext in IMAGE_EXTENSIONS},
    **{ext: "videos" for ext in VIDEO_EXTENSIONS},
}


def get_media_date(path: Path) -> datetime:
    """Renvoie la date de prise de vue/création (métadonnées EXIF ou vidéo) ou, à
    défaut, la date de modification du fichier sur le disque.

    Délègue à photo_metadata (EXIF, silencieusement absent sur GIF/BMP) ou
    video_metadata (MP4/MOV/M4V/3GP, AVI, WMV, MKV/WEBM ; MPG/MPEG n'a pas d'équivalent
    standardisé et reste toujours daté via la date de modification) selon l'extension.
    """
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        try:
            date = get_photo_exif_date(path)
            if date is not None:
                return date
        except Exception:
            pass
    else:
        try:
            date = get_video_creation_date(path)
            if date is not None:
                return date
        except Exception:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


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
    """
    candidates = []
    if recursive:
        for dirpath, _dirnames, filenames in os.walk(source_dir):
            for filename in filenames:
                if cancel_event is not None and cancel_event.is_set():
                    raise ScanCancelled()
                category = MEDIA_CATEGORY_BY_EXTENSION.get(Path(filename).suffix.lower())
                if category is not None:
                    candidates.append((Path(dirpath) / filename, category))
    else:
        try:
            with os.scandir(source_dir) as it:
                for entry in it:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ScanCancelled()
                    try:
                        if not entry.is_file():
                            continue
                    except OSError:
                        continue  # supprimé/inaccessible entre l'énumération et cette vérification
                    category = MEDIA_CATEGORY_BY_EXTENSION.get(Path(entry.name).suffix.lower())
                    if category is not None:
                        candidates.append((Path(entry.path), category))
        except OSError:
            pass
    return candidates


def count_media_files(source_dir: Path, recursive: bool = True) -> dict:
    """Compte rapidement les fichiers photo/vidéo de source_dir par catégorie, sans lire
    leurs métadonnées (contrairement à scan_media) : donne un ordre de grandeur avant de
    lancer l'analyse complète, qui peut être bien plus longue sur un gros dossier.
    """
    counts = {category: 0 for category in CATEGORY_LABELS}
    for _path, category in list_media_files(source_dir, recursive):
        counts[category] += 1
    return counts


def scan_media(source_dir: Path, recursive: bool = True, cancel_event: threading.Event = None, max_workers: int = None):
    """Parcourt source_dir (récursivement par défaut, ou uniquement à sa racine) et
    regroupe photos et vidéos par catégorie ("photos" / "videos") puis par (année, mois,
    jour).

    La lecture de la date (get_media_date, dominée par des I/O disque : ouverture de
    fichier, lecture d'en-tête EXIF ou vidéo) est parallélisée sur plusieurs threads —
    le nombre de fichiers ne dépend pas de la récursivité (un dossier plat peut tout
    autant en contenir des milliers), donc le gain s'applique dans les deux cas.

    Si cancel_event est fourni et déclenché pendant l'analyse (par ex. depuis un autre
    thread), lève ScanCancelled dès que possible plutôt que d'aller au bout : pendant le
    parcours du dossier, ou entre deux résultats une fois la lecture des dates lancée.
    """
    tree = {category: {} for category in CATEGORY_LABELS}
    candidates = list_media_files(source_dir, recursive, cancel_event)

    if not candidates:
        return tree

    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        future_to_candidate = {executor.submit(get_media_date, path): (path, category) for path, category in candidates}
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
            year, month, day = str(date.year), f"{date.month:02d}", f"{date.day:02d}"
            tree[category].setdefault(year, {}).setdefault(month, {}).setdefault(day, []).append(path)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return tree


def merge_media_trees(tree: dict) -> dict:
    """Fusionne les arbres photos et vidéos en un seul arbre année/mois/jour."""
    merged = {}
    for category_tree in tree.values():
        for year, months in category_tree.items():
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
    """
    depth = SORT_LEVELS[level][1]
    result = {}
    for year, months in tree.items():
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
    de séparer ou non Photos et Vidéos à la racine."""
    if separate_media:
        return {
            CATEGORY_LABELS[category]: aggregate_tree(category_tree, level)
            for category, category_tree in tree.items()
            if count_files(category_tree) > 0
        }
    return aggregate_tree(merge_media_trees(tree), level)


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


def unique_destination(dest_dir: Path, filename: str) -> Path:
    """Évite d'écraser un fichier existant en suffixant _1, _2, ... en cas de collision."""
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
    src_file: Path, target_dir: Path, pending_by_size: dict, hashed_by_size: dict, mode: str
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

    dst_file = unique_destination(target_dir, src_file.name)
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
    cancel_event: threading.Event = None, on_progress=None,
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
                if transfer_file(src_file, target_dir, pending_by_size, hashed_by_size, mode) == "duplicate":
                    duplicates += 1
            except Exception as exc:
                errors.append(f"{src_file}: {exc}")
            done += 1
            if on_progress is not None:
                on_progress(done)
    return done, duplicates, errors


class MediaSorterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Tri de photos par date")
        self.root.geometry("760x560")

        self.source_dir = tk.StringVar()
        self.dest_dir = tk.StringVar()
        self.sort_level = tk.StringVar(value="jour")
        self.copy_mode = tk.StringVar(value="copier")
        self.separate_media = tk.BooleanVar(value=False)
        self.recursive = tk.BooleanVar(value=True)
        self.tree_data = {}
        self._scanned_source_path = None
        self._scan_cancel_event = None
        self._scan_start_time = None
        self.last_scan_duration = None
        self._copy_cancel_event = None
        self._pre_count_generation = 0

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        src_frame = ttk.Frame(self.root)
        src_frame.pack(fill="x", **pad)
        ttk.Label(src_frame, text="Dossier source :").pack(side="left")
        ttk.Entry(src_frame, textvariable=self.source_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(src_frame, text="Choisir...", command=self.choose_source).pack(side="left")
        self.scan_button = ttk.Button(src_frame, text="Analyser", command=self.start_scan)
        self.scan_button.pack(side="left", padx=(6, 0))
        self.cancel_scan_button = ttk.Button(
            src_frame, text="Annuler", command=self.cancel_scan, state="disabled",
        )
        self.cancel_scan_button.pack(side="left", padx=(6, 0))

        self.recursive_frame = ttk.Frame(self.root)
        self.recursive_frame.pack(fill="x", **pad)
        ttk.Checkbutton(
            self.recursive_frame, text="Inclure les sous-dossiers",
            variable=self.recursive, command=self._on_recursive_change,
        ).pack(side="left")

        self.pre_scan_label = ttk.Label(self.root, foreground="#555555")
        self.pre_scan_label.pack(fill="x", padx=8)

        self.level_frame = ttk.Frame(self.root)
        self.level_frame.pack(fill="x", **pad)
        ttk.Label(self.level_frame, text="Niveau de tri :").pack(side="left")
        for value in ("annee", "mois", "jour"):
            ttk.Radiobutton(
                self.level_frame, text=SORT_LEVELS[value][0], value=value,
                variable=self.sort_level, command=self._on_options_change,
            ).pack(side="left", padx=(8, 0))

        self.media_frame = ttk.Frame(self.root)
        self.media_frame.pack(fill="x", **pad)
        ttk.Checkbutton(
            self.media_frame, text="Séparer Photos et Vidéos à la racine de la destination",
            variable=self.separate_media, command=self._on_options_change,
        ).pack(side="left")

        self.status_label = ttk.Label(self.root, text="Choisissez un dossier source, puis cliquez sur Analyser.")
        self.status_label.pack(fill="x", padx=8)

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")

        self.total_label = ttk.Label(self.root, text="", font=("TkDefaultFont", 9, "bold"))
        self.total_label.pack(fill="x", padx=8)

        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill="both", expand=True, **pad)

        self.treeview = ttk.Treeview(tree_frame, columns=("count",), show="tree headings")
        self.treeview.heading("#0", text="Arborescence")
        self.treeview.heading("count", text="Nb fichiers")
        self.treeview.column("count", width=100, anchor="center")
        self.treeview.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.treeview.yview)
        scrollbar.pack(side="right", fill="y")
        self.treeview.configure(yscrollcommand=scrollbar.set)

        dest_frame = ttk.Frame(self.root)
        dest_frame.pack(fill="x", **pad)
        ttk.Label(dest_frame, text="Dossier de destination :").pack(side="left")
        ttk.Entry(dest_frame, textvariable=self.dest_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(dest_frame, text="Choisir...", command=self.choose_dest).pack(side="left")

        self.mode_frame = ttk.Frame(self.root)
        self.mode_frame.pack(fill="x", **pad)
        ttk.Label(self.mode_frame, text="Action :").pack(side="left")
        ttk.Radiobutton(
            self.mode_frame, text="Copier (originaux conservés)", value="copier",
            variable=self.copy_mode, command=self._on_copy_mode_change,
        ).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(
            self.mode_frame, text="Déplacer (originaux supprimés)", value="deplacer",
            variable=self.copy_mode, command=self._on_copy_mode_change,
        ).pack(side="left", padx=(8, 0))

        copy_button_frame = ttk.Frame(self.root)
        copy_button_frame.pack(pady=(0, 10))
        self.create_button = ttk.Button(
            copy_button_frame, text="Créer l'arborescence et copier les fichiers",
            command=self.start_copy, state="disabled",
        )
        self.create_button.pack(side="left")
        self.cancel_copy_button = ttk.Button(
            copy_button_frame, text="Annuler", command=self.cancel_copy, state="disabled",
        )
        self.cancel_copy_button.pack(side="left", padx=(6, 0))

    def _option_widgets(self):
        widgets = []
        for frame in (self.recursive_frame, self.level_frame, self.media_frame, self.mode_frame):
            widgets.extend(frame.winfo_children())
        return widgets

    def _set_options_locked(self, locked):
        # Empêche de déclencher une analyse pendant une copie (ou l'inverse), et empêche
        # de changer les options pendant l'une ou l'autre : un changement d'option
        # rafraîchit l'arborescence (_on_options_change) et pourrait sinon réactiver
        # "Créer l'arborescence..." pendant qu'une opération est déjà en cours, ouvrant
        # la voie à deux analyses/copies concurrentes sur les mêmes fichiers.
        state = "disabled" if locked else "normal"
        self.scan_button.config(state=state)
        for widget in self._option_widgets():
            widget.configure(state=state)

    def _on_copy_mode_change(self):
        verb = "copier" if self.copy_mode.get() == "copier" else "déplacer"
        self.create_button.config(text=f"Créer l'arborescence et {verb} les fichiers")

    def choose_source(self):
        path = filedialog.askdirectory(title="Choisir le dossier de photos et vidéos à trier")
        if path:
            self.source_dir.set(path)
            self._update_pre_scan_count()

    def _on_recursive_change(self):
        # Contrairement au niveau de tri et à la séparation Photos/Vidéos, ce réglage
        # ne peut pas se contenter de ré-agréger l'arborescence déjà analysée : il
        # change l'ensemble des fichiers à considérer, ce qui suppose une nouvelle
        # analyse. Le comptage rapide se met à jour immédiatement pour donner un aperçu
        # de l'effet du changement ; si une arborescence est déjà affichée, on prévient
        # explicitement qu'elle ne reflète plus le réglage courant.
        self._update_pre_scan_count()
        if self.tree_data:
            self.status_label.config(
                text="Nouveau réglage de récursivité : cliquez sur Analyser pour l'appliquer à l'arborescence."
            )

    def _update_pre_scan_count(self):
        source = self.source_dir.get().strip()
        if not source or not Path(source).is_dir():
            self.pre_scan_label.config(text="")
            return

        source_path = Path(source)
        recursive = self.recursive.get()
        self._pre_count_generation += 1
        generation = self._pre_count_generation
        self.pre_scan_label.config(text="Comptage des fichiers...")
        threading.Thread(
            target=self._pre_scan_count_worker,
            args=(source_path, recursive, generation),
            daemon=True,
        ).start()

    def _pre_scan_count_worker(self, source_path: Path, recursive: bool, generation: int):
        try:
            counts = count_media_files(source_path, recursive)
        except OSError:
            counts = None
        self.root.after(0, self._pre_scan_count_done, counts, generation)

    def _pre_scan_count_done(self, counts, generation):
        if generation != self._pre_count_generation:
            return  # une demande plus récente a déjà pris le relais (autre dossier, autre choix de récursivité)
        if counts is None:
            self.pre_scan_label.config(text="")
            return
        total = sum(counts.values())
        if total == 0:
            self.pre_scan_label.config(text="Aucune photo ou vidéo trouvée dans ce dossier.")
        else:
            self.pre_scan_label.config(
                text=f"{total} fichier(s) trouvé(s) avant analyse "
                f"({counts['photos']} photo(s), {counts['videos']} vidéo(s))."
            )

    def choose_dest(self):
        path = filedialog.askdirectory(title="Choisir le dossier où créer l'arborescence")
        if path:
            self.dest_dir.set(path)

    def start_scan(self):
        if self._copy_cancel_event is not None:
            return  # une copie/déplacement est déjà en cours

        source = self.source_dir.get().strip()
        if not source:
            messagebox.showwarning("Dossier manquant", "Veuillez choisir un dossier source.")
            return
        source_path = Path(source)
        if not source_path.is_dir():
            messagebox.showerror("Dossier invalide", "Le dossier source n'existe pas.")
            return

        self.create_button.config(state="disabled")
        self._set_options_locked(True)
        self.cancel_scan_button.config(state="normal")
        self._pre_count_generation += 1  # invalide un comptage rapide encore en cours
        self.pre_scan_label.config(text="")
        self.treeview.delete(*self.treeview.get_children())
        self.total_label.config(text="")
        self.status_label.config(text="Analyse en cours...")
        self.progress.pack(fill="x", padx=8, pady=(0, 6))
        self.progress.start(10)

        self._scan_start_time = time.time()
        self._scan_cancel_event = threading.Event()
        threading.Thread(
            target=self._scan_worker,
            args=(source_path, self.recursive.get(), self._scan_cancel_event),
            daemon=True,
        ).start()

    def cancel_scan(self):
        if self._scan_cancel_event is not None:
            self._scan_cancel_event.set()
        self.cancel_scan_button.config(state="disabled")
        self.status_label.config(text="Annulation en cours...")

    def _scan_worker(self, source_path: Path, recursive: bool, cancel_event: threading.Event):
        try:
            tree = scan_media(source_path, recursive=recursive, cancel_event=cancel_event)
        except ScanCancelled:
            self.root.after(0, self._scan_cancelled)
            return
        except Exception as exc:
            self.root.after(0, self._scan_failed, exc)
            return
        self.root.after(0, self._scan_done, tree, source_path)

    def _reset_scan_buttons(self):
        self._scan_cancel_event = None
        self._set_options_locked(False)
        self.cancel_scan_button.config(state="disabled")

    def _scan_cancelled(self):
        self.progress.stop()
        self.progress.pack_forget()
        self._reset_scan_buttons()
        self.status_label.config(text="Analyse annulée.")

    def _scan_failed(self, exc):
        self.progress.stop()
        self.progress.pack_forget()
        self._reset_scan_buttons()
        self.status_label.config(text="Erreur lors de l'analyse.")
        messagebox.showerror("Erreur", str(exc))

    def _scan_done(self, tree, source_path):
        self.progress.stop()
        self.progress.pack_forget()
        self._reset_scan_buttons()
        self.last_scan_duration = time.time() - self._scan_start_time
        try:
            self._scanned_source_path = source_path.resolve()
        except OSError:
            self._scanned_source_path = source_path
        self.tree_data = tree
        self._refresh_treeview()

    def _on_options_change(self):
        if self.tree_data:
            self._refresh_treeview()

    def _refresh_treeview(self):
        self.treeview.delete(*self.treeview.get_children())
        separate = self.separate_media.get()
        display_tree = build_display_tree(self.tree_data, self.sort_level.get(), separate)
        total = count_files(display_tree)
        month_depth = 2 if separate else 1
        self._populate_tree("", display_tree, depth=0, month_depth=month_depth)

        if total == 0:
            self.total_label.config(text="")
            self.status_label.config(text="Aucune photo ou vidéo trouvée dans ce dossier.")
            self.create_button.config(state="disabled")
        else:
            photo_count = count_files(self.tree_data.get("photos", {}))
            video_count = count_files(self.tree_data.get("videos", {}))
            duration_text = ""
            if self.last_scan_duration is not None:
                duration_text = f"  —  analysé en {format_duration(self.last_scan_duration)}"
            self.total_label.config(
                text=f"Total : {total} fichier(s)  ({photo_count} photo(s), {video_count} vidéo(s)){duration_text}"
            )
            self.status_label.config(text=f"{total} fichier(s) trouvé(s). Choisissez un dossier de destination pour les ranger.")
            self.create_button.config(state="normal")

    def _populate_tree(self, parent, node, depth, month_depth):
        for key in sorted(node):
            value = node[key]
            label = month_folder_name(key) if depth == month_depth else key
            count = count_files(value)
            if isinstance(value, list):
                self.treeview.insert(parent, "end", text=label, values=(count,), open=(depth == 0))
            else:
                child_node = self.treeview.insert(parent, "end", text=label, values=(count,), open=(depth == 0))
                self._populate_tree(child_node, value, depth + 1, month_depth)

    def start_copy(self):
        if self._scan_cancel_event is not None:
            return  # une analyse est déjà en cours

        dest = self.dest_dir.get().strip()
        if not dest:
            messagebox.showwarning("Dossier manquant", "Veuillez choisir un dossier de destination.")
            return
        if not self.tree_data:
            messagebox.showwarning("Rien à copier", "Veuillez d'abord analyser un dossier source.")
            return

        dest_path = Path(dest)
        # Comparé au dossier réellement analysé (celui qui a produit tree_data), pas au
        # contenu actuel du champ "Dossier source" : sinon il suffit de vider ou modifier
        # ce champ après l'analyse pour contourner la vérification.
        if self._scanned_source_path is not None:
            resolved_dest = dest_path.resolve()
            if resolved_dest == self._scanned_source_path or resolved_dest.is_relative_to(self._scanned_source_path):
                messagebox.showerror(
                    "Dossier de destination invalide",
                    "Le dossier de destination ne peut pas être le dossier source, ni un de ses "
                    "sous-dossiers : cela copierait les fichiers dans le dossier en cours d'analyse "
                    "(risque de doublons en cascade, voire de boucle en mode récursif).",
                )
                return

        destination_map = build_destination_map(self.tree_data, self.sort_level.get(), self.separate_media.get())
        total = sum(len(files) for files in destination_map.values())
        mode = self.copy_mode.get()
        if mode == "deplacer":
            verb, warning = "Déplacer", "Les fichiers originaux seront supprimés de leur emplacement d'origine."
        else:
            verb, warning = "Copier", "Les fichiers originaux ne seront pas modifiés (copie, pas déplacement)."
        if not messagebox.askyesno("Confirmer", f"{verb} {total} fichier(s) dans :\n{dest_path}\n\n{warning}"):
            return

        self.create_button.config(state="disabled")
        self._set_options_locked(True)
        self.cancel_copy_button.config(state="normal")
        self.status_label.config(text=f"{verb} en cours...")
        self.progress.pack(fill="x", padx=8, pady=(0, 6))
        self.progress.config(mode="determinate", maximum=total, value=0)

        self._copy_cancel_event = threading.Event()
        threading.Thread(
            target=self._copy_worker,
            args=(dest_path, destination_map, mode, self._copy_cancel_event),
            daemon=True,
        ).start()

    def cancel_copy(self):
        if self._copy_cancel_event is not None:
            self._copy_cancel_event.set()
        self.cancel_copy_button.config(state="disabled")
        self.status_label.config(text="Annulation en cours...")

    def _copy_worker(self, dest_path: Path, destination_map: dict, mode: str, cancel_event: threading.Event):
        try:
            done, duplicates, errors = copy_files(
                destination_map, dest_path, mode,
                cancel_event=cancel_event,
                on_progress=lambda done: self.root.after(0, self._update_progress, done),
            )
        except CopyCancelled as exc:
            done, duplicates, errors = exc.args
            self.root.after(0, self._copy_cancelled, done, duplicates, errors, mode)
            return
        except Exception as exc:
            self.root.after(0, self._copy_failed, exc)
            return
        self.root.after(0, self._copy_done, done, duplicates, errors, mode)

    def _reset_copy_buttons(self):
        self._copy_cancel_event = None
        self._set_options_locked(False)
        self.create_button.config(state="normal")
        self.cancel_copy_button.config(state="disabled")

    def _copy_cancelled(self, done, duplicates, errors, mode):
        self.progress.pack_forget()
        self._reset_copy_buttons()
        transferred = done - duplicates - len(errors)
        action_past = "déplacé(s)" if mode == "deplacer" else "copié(s)"
        self.status_label.config(text=f"Annulé : {transferred} fichier(s) {action_past} avant l'arrêt.")

    def _copy_failed(self, exc):
        self.progress.pack_forget()
        self._reset_copy_buttons()
        self.status_label.config(text="Erreur lors de la copie.")
        messagebox.showerror("Erreur", str(exc))

    def _update_progress(self, done):
        self.progress.config(value=done)

    def _copy_done(self, done, duplicates, errors, mode):
        self.progress.pack_forget()
        self._reset_copy_buttons()
        transferred = done - duplicates - len(errors)
        if mode == "deplacer":
            action_past = "déplacé(s)"
            dup_text = f"{duplicates} doublon(s) supprimé(s) de la source"
        else:
            action_past = "copié(s)"
            dup_text = f"{duplicates} doublon(s) ignoré(s)"
        if errors:
            self.status_label.config(text=f"Terminé avec {len(errors)} erreur(s) sur {done} fichier(s), {dup_text}.")
            messagebox.showwarning("Terminé avec erreurs", "\n".join(errors[:20]) + ("\n..." if len(errors) > 20 else ""))
        else:
            self.status_label.config(text=f"Terminé : {transferred} fichier(s) {action_past}, {dup_text}.")
            messagebox.showinfo(
                "Terminé",
                f"{transferred} fichier(s) {action_past} avec succès dans :\n{self.dest_dir.get()}\n\n"
                f"{dup_text} (déjà présent(s) dans le dossier de destination).",
            )


def main():
    root = tk.Tk()
    app = MediaSorterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
