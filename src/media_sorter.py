"""Tri de photos et vidéos par date - interface graphique Tkinter (cross-platform)."""

import hashlib
import shutil
import threading
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


def scan_media(source_dir: Path, recursive: bool = True):
    """Parcourt source_dir (récursivement par défaut, ou uniquement à sa racine) et
    regroupe photos et vidéos par catégorie ("photos" / "videos") puis par (année, mois,
    jour)."""
    tree = {category: {} for category in CATEGORY_LABELS}
    entries = source_dir.rglob("*") if recursive else source_dir.glob("*")
    for path in entries:
        if not path.is_file():
            continue
        category = MEDIA_CATEGORY_BY_EXTENSION.get(path.suffix.lower())
        if category is None:
            continue
        date = get_media_date(path)
        year, month, day = str(date.year), f"{date.month:02d}", f"{date.day:02d}"
        tree[category].setdefault(year, {}).setdefault(month, {}).setdefault(day, []).append(path)
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


def transfer_file(src_file: Path, target_dir: Path, existing_hashes: set, mode: str) -> str:
    """Copie ou déplace src_file vers target_dir, sauf si son contenu s'y trouve déjà.

    En mode "deplacer", un doublon est tout de même supprimé de la source puisqu'une
    copie de son contenu existe déjà à destination. Renvoie "duplicate", "copied" ou
    "moved".
    """
    src_hash = file_hash(src_file)
    if src_hash in existing_hashes:
        if mode == "deplacer":
            src_file.unlink()
        return "duplicate"

    dst_file = unique_destination(target_dir, src_file.name)
    if mode == "deplacer":
        shutil.move(str(src_file), str(dst_file))
    else:
        shutil.copy2(src_file, dst_file)
    existing_hashes.add(src_hash)
    return "moved" if mode == "deplacer" else "copied"


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

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        src_frame = ttk.Frame(self.root)
        src_frame.pack(fill="x", **pad)
        ttk.Label(src_frame, text="Dossier source :").pack(side="left")
        ttk.Entry(src_frame, textvariable=self.source_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(src_frame, text="Choisir...", command=self.choose_source).pack(side="left")
        ttk.Button(src_frame, text="Analyser", command=self.start_scan).pack(side="left", padx=(6, 0))

        recursive_frame = ttk.Frame(self.root)
        recursive_frame.pack(fill="x", **pad)
        ttk.Checkbutton(
            recursive_frame, text="Inclure les sous-dossiers",
            variable=self.recursive,
        ).pack(side="left")

        level_frame = ttk.Frame(self.root)
        level_frame.pack(fill="x", **pad)
        ttk.Label(level_frame, text="Niveau de tri :").pack(side="left")
        for value in ("annee", "mois", "jour"):
            ttk.Radiobutton(
                level_frame, text=SORT_LEVELS[value][0], value=value,
                variable=self.sort_level, command=self._on_options_change,
            ).pack(side="left", padx=(8, 0))

        media_frame = ttk.Frame(self.root)
        media_frame.pack(fill="x", **pad)
        ttk.Checkbutton(
            media_frame, text="Séparer Photos et Vidéos à la racine de la destination",
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

        mode_frame = ttk.Frame(self.root)
        mode_frame.pack(fill="x", **pad)
        ttk.Label(mode_frame, text="Action :").pack(side="left")
        ttk.Radiobutton(
            mode_frame, text="Copier (originaux conservés)", value="copier",
            variable=self.copy_mode, command=self._on_copy_mode_change,
        ).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(
            mode_frame, text="Déplacer (originaux supprimés)", value="deplacer",
            variable=self.copy_mode, command=self._on_copy_mode_change,
        ).pack(side="left", padx=(8, 0))

        self.create_button = ttk.Button(
            self.root, text="Créer l'arborescence et copier les fichiers",
            command=self.start_copy, state="disabled",
        )
        self.create_button.pack(pady=(0, 10))

    def _on_copy_mode_change(self):
        verb = "copier" if self.copy_mode.get() == "copier" else "déplacer"
        self.create_button.config(text=f"Créer l'arborescence et {verb} les fichiers")

    def choose_source(self):
        path = filedialog.askdirectory(title="Choisir le dossier de photos et vidéos à trier")
        if path:
            self.source_dir.set(path)

    def choose_dest(self):
        path = filedialog.askdirectory(title="Choisir le dossier où créer l'arborescence")
        if path:
            self.dest_dir.set(path)

    def start_scan(self):
        source = self.source_dir.get().strip()
        if not source:
            messagebox.showwarning("Dossier manquant", "Veuillez choisir un dossier source.")
            return
        source_path = Path(source)
        if not source_path.is_dir():
            messagebox.showerror("Dossier invalide", "Le dossier source n'existe pas.")
            return

        self.create_button.config(state="disabled")
        self.treeview.delete(*self.treeview.get_children())
        self.total_label.config(text="")
        self.status_label.config(text="Analyse en cours...")
        self.progress.pack(fill="x", padx=8, pady=(0, 6))
        self.progress.start(10)

        threading.Thread(target=self._scan_worker, args=(source_path, self.recursive.get()), daemon=True).start()

    def _scan_worker(self, source_path: Path, recursive: bool):
        try:
            tree = scan_media(source_path, recursive=recursive)
        except Exception as exc:
            self.root.after(0, self._scan_failed, exc)
            return
        self.root.after(0, self._scan_done, tree)

    def _scan_failed(self, exc):
        self.progress.stop()
        self.progress.pack_forget()
        self.status_label.config(text="Erreur lors de l'analyse.")
        messagebox.showerror("Erreur", str(exc))

    def _scan_done(self, tree):
        self.progress.stop()
        self.progress.pack_forget()
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
            self.total_label.config(
                text=f"Total : {total} fichier(s)  ({photo_count} photo(s), {video_count} vidéo(s))"
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
        dest = self.dest_dir.get().strip()
        if not dest:
            messagebox.showwarning("Dossier manquant", "Veuillez choisir un dossier de destination.")
            return
        if not self.tree_data:
            messagebox.showwarning("Rien à copier", "Veuillez d'abord analyser un dossier source.")
            return

        dest_path = Path(dest)
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
        self.status_label.config(text=f"{verb} en cours...")
        self.progress.pack(fill="x", padx=8, pady=(0, 6))
        self.progress.config(mode="determinate", maximum=total, value=0)

        threading.Thread(target=self._copy_worker, args=(dest_path, destination_map, mode), daemon=True).start()

    def _copy_worker(self, dest_path: Path, destination_map: dict, mode: str):
        done = 0
        duplicates = 0
        errors = []
        for folder_names, files in destination_map.items():
            target_dir = dest_path.joinpath(*folder_names)
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                errors.append(f"{target_dir}: {exc}")
                continue

            existing_hashes = set()
            for existing_file in target_dir.iterdir():
                if existing_file.is_file():
                    try:
                        existing_hashes.add(file_hash(existing_file))
                    except Exception:
                        pass

            for src_file in files:
                try:
                    if transfer_file(src_file, target_dir, existing_hashes, mode) == "duplicate":
                        duplicates += 1
                except Exception as exc:
                    errors.append(f"{src_file}: {exc}")
                done += 1
                self.root.after(0, self._update_progress, done)
        self.root.after(0, self._copy_done, done, duplicates, errors, mode)

    def _update_progress(self, done):
        self.progress.config(value=done)

    def _copy_done(self, done, duplicates, errors, mode):
        self.progress.pack_forget()
        self.create_button.config(state="normal")
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
