"""Tri de photos par année/mois - interface graphique Tkinter (cross-platform)."""

import hashlib
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ExifTags

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".webp",
}

DATE_TAG_ID = next(
    (tag_id for tag_id, name in ExifTags.TAGS.items() if name == "DateTimeOriginal"),
    None,
)


def get_photo_date(path: Path) -> datetime:
    """Renvoie la date de prise de vue (EXIF) ou, à défaut, la date de modification du fichier."""
    if DATE_TAG_ID is not None and path.suffix.lower() in {".jpg", ".jpeg", ".tiff", ".tif", ".heic", ".heif"}:
        try:
            with Image.open(path) as img:
                exif = img.getexif()
                raw = exif.get(DATE_TAG_ID)
                if raw:
                    return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
        except Exception:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def scan_photos(source_dir: Path):
    """Parcourt récursivement source_dir et regroupe les photos par (année, mois, jour)."""
    tree = {}
    files = [p for p in source_dir.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
    for path in files:
        date = get_photo_date(path)
        year, month, day = str(date.year), f"{date.month:02d}", f"{date.day:02d}"
        tree.setdefault(year, {}).setdefault(month, {}).setdefault(day, []).append(path)
    return tree


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


class PhotoSorterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Tri de photos par date")
        self.root.geometry("760x560")

        self.source_dir = tk.StringVar()
        self.dest_dir = tk.StringVar()
        self.sort_level = tk.StringVar(value="jour")
        self.copy_mode = tk.StringVar(value="copier")
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

        level_frame = ttk.Frame(self.root)
        level_frame.pack(fill="x", **pad)
        ttk.Label(level_frame, text="Niveau de tri :").pack(side="left")
        for value in ("annee", "mois", "jour"):
            ttk.Radiobutton(
                level_frame, text=SORT_LEVELS[value][0], value=value,
                variable=self.sort_level, command=self._on_level_change,
            ).pack(side="left", padx=(8, 0))

        self.status_label = ttk.Label(self.root, text="Choisissez un dossier source, puis cliquez sur Analyser.")
        self.status_label.pack(fill="x", padx=8)

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")

        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill="both", expand=True, **pad)

        self.treeview = ttk.Treeview(tree_frame, columns=("count",), show="tree headings")
        self.treeview.heading("#0", text="Arborescence")
        self.treeview.heading("count", text="Nb photos")
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
            self.root, text="Créer l'arborescence et copier les photos",
            command=self.start_copy, state="disabled",
        )
        self.create_button.pack(pady=(0, 10))

    def _on_copy_mode_change(self):
        verb = "copier" if self.copy_mode.get() == "copier" else "déplacer"
        self.create_button.config(text=f"Créer l'arborescence et {verb} les photos")

    def choose_source(self):
        path = filedialog.askdirectory(title="Choisir le dossier de photos à trier")
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
        self.status_label.config(text="Analyse en cours...")
        self.progress.pack(fill="x", padx=8, pady=(0, 6))
        self.progress.start(10)

        threading.Thread(target=self._scan_worker, args=(source_path,), daemon=True).start()

    def _scan_worker(self, source_path: Path):
        try:
            tree = scan_photos(source_path)
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

    def _on_level_change(self):
        if self.tree_data:
            self._refresh_treeview()

    def _refresh_treeview(self):
        self.treeview.delete(*self.treeview.get_children())
        aggregated = aggregate_tree(self.tree_data, self.sort_level.get())
        total = count_files(aggregated)
        self._populate_tree("", aggregated, depth=0)

        if total == 0:
            self.status_label.config(text="Aucune photo trouvée dans ce dossier.")
            self.create_button.config(state="disabled")
        else:
            self.status_label.config(text=f"{total} photo(s) trouvée(s). Choisissez un dossier de destination pour les ranger.")
            self.create_button.config(state="normal")

    def _populate_tree(self, parent, node, depth):
        for key in sorted(node):
            value = node[key]
            label = month_folder_name(key) if depth == 1 else key
            count = count_files(value)
            if isinstance(value, list):
                self.treeview.insert(parent, "end", text=label, values=(count,), open=(depth == 0))
            else:
                child_node = self.treeview.insert(parent, "end", text=label, values=(count,), open=(depth == 0))
                self._populate_tree(child_node, value, depth + 1)

    def start_copy(self):
        dest = self.dest_dir.get().strip()
        if not dest:
            messagebox.showwarning("Dossier manquant", "Veuillez choisir un dossier de destination.")
            return
        if not self.tree_data:
            messagebox.showwarning("Rien à copier", "Veuillez d'abord analyser un dossier source.")
            return

        dest_path = Path(dest)
        aggregated = aggregate_tree(self.tree_data, self.sort_level.get())
        total = count_files(aggregated)
        mode = self.copy_mode.get()
        if mode == "deplacer":
            verb, warning = "Déplacer", "Les photos originales seront supprimées de leur emplacement d'origine."
        else:
            verb, warning = "Copier", "Les photos originales ne seront pas modifiées (copie, pas déplacement)."
        if not messagebox.askyesno("Confirmer", f"{verb} {total} photo(s) dans :\n{dest_path}\n\n{warning}"):
            return

        self.create_button.config(state="disabled")
        self.status_label.config(text=f"{verb} en cours...")
        self.progress.pack(fill="x", padx=8, pady=(0, 6))
        self.progress.config(mode="determinate", maximum=total, value=0)

        threading.Thread(target=self._copy_worker, args=(dest_path, aggregated, mode), daemon=True).start()

    def _copy_worker(self, dest_path: Path, aggregated: dict, mode: str):
        done = 0
        duplicates = 0
        errors = []
        for path_parts, files in flatten_tree(aggregated):
            target_dir = dest_path.joinpath(*path_parts_to_folder_names(path_parts))
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
            action_past = "déplacée(s)"
            dup_text = f"{duplicates} doublon(s) supprimé(s) de la source"
        else:
            action_past = "copiée(s)"
            dup_text = f"{duplicates} doublon(s) ignoré(s)"
        if errors:
            self.status_label.config(text=f"Terminé avec {len(errors)} erreur(s) sur {done} photo(s), {dup_text}.")
            messagebox.showwarning("Terminé avec erreurs", "\n".join(errors[:20]) + ("\n..." if len(errors) > 20 else ""))
        else:
            self.status_label.config(text=f"Terminé : {transferred} photo(s) {action_past}, {dup_text}.")
            messagebox.showinfo(
                "Terminé",
                f"{transferred} photo(s) {action_past} avec succès dans :\n{self.dest_dir.get()}\n\n"
                f"{dup_text} (déjà présent(s) dans le dossier de destination).",
            )


def main():
    root = tk.Tk()
    app = PhotoSorterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
