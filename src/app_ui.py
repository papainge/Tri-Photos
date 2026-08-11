"""Interface graphique Tkinter (MediaSorterApp) : construction des widgets, gestion
d'état, threading pour ne jamais bloquer l'interface pendant l'analyse ou la copie.
Délègue toute la logique de tri/copie/hash à media_sorter (voir ce module), toujours
via des appels qualifiés (media_sorter.scan_media, media_sorter.copy_files...) plutôt
que des imports directs de noms : les tests (test_app_ui.py) patchent ces
fonctions sur le module media_sorter, un import direct (from media_sorter import
scan_media) capturerait la référence d'origine avant patch et la rendrait invisible ici.

Copyright (C) 2026 Guillaume Pataut
Logiciel libre distribué sous licence GNU GPL v3 (ou ultérieure) — voir le fichier
LICENSE à la racine du dépôt, ou <https://www.gnu.org/licenses/>.
"""

import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import media_sorter


class MediaSorterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Tri de photos par date")

        preferences = media_sorter.load_preferences()

        self.source_dirs = []  # dossiers sources sélectionnés (voir add_source)
        self.dest_dir = tk.StringVar()
        self.sort_level = tk.StringVar(value=preferences["sort_level"])
        self.copy_mode = tk.StringVar(value=preferences["copy_mode"])
        self.separate_media = tk.BooleanVar(value=preferences["separate_media"])
        self.rename_files = tk.BooleanVar(value=False)
        self.recursive = tk.BooleanVar(value=True)
        self.use_filename_fallback = tk.BooleanVar(value=False)
        self.tree_data = {}
        self._scanned_source_paths = []
        self._scan_cancel_event = None
        self._scan_start_time = None
        self.last_scan_duration = None
        self._copy_cancel_event = None
        self._pre_count_generation = 0

        self._build_ui()
        self._set_initial_geometry()

    def _set_initial_geometry(self):
        # Calculée à partir des widgets réellement construits plutôt qu'une taille fixe
        # codée en dur : une taille fixe se désynchronise silencieusement à chaque ajout
        # de widget (case à cocher, ligne de dossiers sources...) et finit par ouvrir une
        # fenêtre trop petite pour son propre contenu — la zone basse (destination,
        # boutons de transfert) passe alors sous le bord visible de la fenêtre, invisible
        # tant qu'on ne l'agrandit pas à la main. minsize() empêche en plus de revenir à
        # ce même problème en redimensionnant la fenêtre à la main par la suite.
        self.root.update_idletasks()
        width = max(760, self.root.winfo_reqwidth())
        height = self.root.winfo_reqheight()
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(width, height)

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        src_frame = ttk.Frame(self.root)
        src_frame.pack(fill="x", **pad)
        ttk.Label(src_frame, text="Dossiers sources :").pack(anchor="w")
        src_row = ttk.Frame(src_frame)
        src_row.pack(fill="x", pady=(4, 0))
        self.sources_listbox = tk.Listbox(src_row, height=4, exportselection=False)
        self.sources_listbox.pack(side="left", fill="x", expand=True)
        src_buttons = ttk.Frame(src_row)
        src_buttons.pack(side="left", padx=(6, 0))
        ttk.Button(src_buttons, text="Ajouter...", command=self.add_source).pack(fill="x")
        ttk.Button(src_buttons, text="Retirer", command=self.remove_source).pack(fill="x", pady=(4, 0))

        scan_row = ttk.Frame(self.root)
        scan_row.pack(fill="x", **pad)
        self.scan_button = ttk.Button(scan_row, text="Analyser", command=self.start_scan)
        self.scan_button.pack(side="left")
        self.cancel_scan_button = ttk.Button(
            scan_row, text="Annuler", command=self.cancel_scan, state="disabled",
        )
        self.cancel_scan_button.pack(side="left", padx=(6, 0))

        self.recursive_frame = ttk.Frame(self.root)
        self.recursive_frame.pack(fill="x", **pad)
        ttk.Checkbutton(
            self.recursive_frame, text="Inclure les sous-dossiers",
            variable=self.recursive, command=self._on_recursive_change,
        ).pack(side="left")

        self.filename_fallback_frame = ttk.Frame(self.root)
        self.filename_fallback_frame.pack(fill="x", **pad)
        ttk.Checkbutton(
            self.filename_fallback_frame,
            text="À défaut, essayer de détecter une date dans le nom du fichier (ex: IMG_20230715_143022.jpg)",
            variable=self.use_filename_fallback, command=self._on_filename_fallback_change,
        ).pack(side="left")

        self.pre_scan_label = ttk.Label(self.root, foreground="#555555")
        self.pre_scan_label.pack(fill="x", padx=8)

        self.level_frame = ttk.Frame(self.root)
        self.level_frame.pack(fill="x", **pad)
        ttk.Label(self.level_frame, text="Niveau de tri :").pack(side="left")
        for value in ("annee", "mois", "jour"):
            ttk.Radiobutton(
                self.level_frame, text=media_sorter.SORT_LEVELS[value][0], value=value,
                variable=self.sort_level, command=self._on_options_change,
            ).pack(side="left", padx=(8, 0))

        self.media_frame = ttk.Frame(self.root)
        self.media_frame.pack(fill="x", **pad)
        ttk.Checkbutton(
            self.media_frame, text="Séparer Photos et Vidéos à la racine de la destination",
            variable=self.separate_media, command=self._on_options_change,
        ).pack(side="left")

        self.rename_frame = ttk.Frame(self.root)
        self.rename_frame.pack(fill="x", **pad)
        ttk.Checkbutton(
            self.rename_frame, text="Renommer les fichiers selon la date (AAAA-MM-JJ_HHMMSS)",
            variable=self.rename_files,
        ).pack(side="left")

        self.status_label = ttk.Label(self.root, text="Ajoutez un dossier source, puis cliquez sur Analyser.")
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
        for frame in (
            self.recursive_frame, self.filename_fallback_frame, self.level_frame,
            self.media_frame, self.rename_frame, self.mode_frame,
        ):
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
        self._save_preferences()

    def _save_preferences(self):
        media_sorter.save_preferences(self.sort_level.get(), self.separate_media.get(), self.copy_mode.get())

    @staticmethod
    def _resolve_key(path: Path):
        try:
            return path.resolve()
        except OSError:
            return path

    def add_source(self):
        # Note UX : filedialog.askdirectory() ne permet de choisir qu'un seul dossier à
        # la fois (limitation de tk_chooseDirectory, sans équivalent "askdirectories" au
        # pluriel comme pour les fichiers) — plusieurs clics sur "Ajouter..." sont donc
        # nécessaires pour plusieurs dossiers, il n'existe pas de sélection multiple native.
        path = filedialog.askdirectory(title="Choisir un dossier de photos et vidéos à trier")
        if not path:
            return
        candidate = Path(path)
        key = self._resolve_key(candidate)
        if key in {self._resolve_key(existing) for existing in self.source_dirs}:
            messagebox.showinfo("Dossier déjà ajouté", "Ce dossier fait déjà partie des sources sélectionnées.")
            return

        self.source_dirs.append(candidate)
        self.sources_listbox.insert("end", str(candidate))
        self._update_pre_scan_count()

    def remove_source(self):
        selection = self.sources_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        del self.source_dirs[index]
        self.sources_listbox.delete(index)
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

    def _on_filename_fallback_change(self):
        # Comme la récursivité (voir _on_recursive_change) : ce réglage détermine si un
        # fichier obtient une date ou non pendant l'analyse elle-même, donc change quels
        # fichiers finissent sous NO_INFO_LABEL — une simple réagrégation de
        # l'arborescence déjà analysée ne suffit pas. Contrairement à la récursivité, il
        # n'affecte pas le comptage rapide (celui-ci ne lit aucune métadonnée/nom).
        if self.tree_data:
            self.status_label.config(
                text=(
                    "Nouveau réglage de détection par nom de fichier : cliquez sur Analyser "
                    "pour l'appliquer à l'arborescence."
                )
            )

    def _update_pre_scan_count(self):
        if not self.source_dirs:
            self.pre_scan_label.config(text="")
            return
        source_paths = list(self.source_dirs)

        recursive = self.recursive.get()
        self._pre_count_generation += 1
        generation = self._pre_count_generation
        self.pre_scan_label.config(text="Comptage des fichiers...")
        threading.Thread(
            target=self._pre_scan_count_worker,
            args=(source_paths, recursive, generation),
            daemon=True,
        ).start()

    def _pre_scan_count_worker(self, source_paths: list, recursive: bool, generation: int):
        try:
            counts = media_sorter.count_media_files(source_paths, recursive)
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

        if not self.source_dirs:
            messagebox.showwarning("Dossier manquant", "Veuillez ajouter au moins un dossier source.")
            return
        source_paths = list(self.source_dirs)

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
            args=(source_paths, self.recursive.get(), self._scan_cancel_event, self.use_filename_fallback.get()),
            daemon=True,
        ).start()

    def cancel_scan(self):
        if self._scan_cancel_event is not None:
            self._scan_cancel_event.set()
        self.cancel_scan_button.config(state="disabled")
        self.status_label.config(text="Annulation en cours...")

    def _scan_worker(
        self, source_paths: list, recursive: bool, cancel_event: threading.Event, use_filename_fallback: bool,
    ):
        try:
            tree = media_sorter.scan_media(
                source_paths, recursive=recursive, cancel_event=cancel_event,
                use_filename_fallback=use_filename_fallback,
            )
        except media_sorter.ScanCancelled:
            self.root.after(0, self._scan_cancelled)
            return
        except Exception as exc:
            media_sorter.logger.exception("Échec de l'analyse de %s", source_paths)
            self.root.after(0, self._scan_failed, exc)
            return
        self.root.after(0, self._scan_done, tree, source_paths)

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

    def _scan_done(self, tree, source_paths):
        self.progress.stop()
        self.progress.pack_forget()
        self._reset_scan_buttons()
        self.last_scan_duration = time.time() - self._scan_start_time
        self._scanned_source_paths = [self._resolve_key(p) for p in source_paths]
        self.tree_data = tree
        self._refresh_treeview()

    def _on_options_change(self):
        self._save_preferences()
        if self.tree_data:
            self._refresh_treeview()

    def _refresh_treeview(self):
        self.treeview.delete(*self.treeview.get_children())
        separate = self.separate_media.get()
        display_tree = media_sorter.build_display_tree(self.tree_data, self.sort_level.get(), separate)
        total = media_sorter.count_files(display_tree)
        month_depth = 2 if separate else 1
        self._populate_tree("", display_tree, depth=0, month_depth=month_depth)

        if total == 0:
            self.total_label.config(text="")
            self.status_label.config(text="Aucune photo ou vidéo trouvée dans ce dossier.")
            self.create_button.config(state="disabled")
        else:
            photo_count = media_sorter.count_files(self.tree_data.get("photos", {}))
            video_count = media_sorter.count_files(self.tree_data.get("videos", {}))
            duration_text = ""
            if self.last_scan_duration is not None:
                duration_text = f"  —  analysé en {media_sorter.format_duration(self.last_scan_duration)}"
            self.total_label.config(
                text=f"Total : {total} fichier(s)  ({photo_count} photo(s), {video_count} vidéo(s)){duration_text}"
            )
            no_info_count = sum(
                len(category_tree.get(media_sorter.NO_INFO_LABEL, [])) for category_tree in self.tree_data.values()
            )
            if no_info_count:
                status_text = (
                    f"{total} fichier(s) trouvé(s), dont {no_info_count} sans date exploitable "
                    "(développez « No Info » dans l'arborescence pour savoir pourquoi). "
                    "Choisissez un dossier de destination pour les ranger."
                )
            else:
                status_text = f"{total} fichier(s) trouvé(s). Choisissez un dossier de destination pour les ranger."
            self.status_label.config(text=status_text)
            self.create_button.config(state="normal")

    def _populate_tree(self, parent, node, depth, month_depth, in_no_info=False):
        # in_no_info évite d'appliquer le formatage "mois" (month_folder_name) aux
        # raisons affichées sous NO_INFO_LABEL (voir group_no_info_by_reason) : ces
        # raisons peuvent tomber, par coïncidence de profondeur, au même niveau que les
        # dossiers de mois habituels (ex: non séparé, month_depth=1) sans en être.
        for key in sorted(node):
            value = node[key]
            is_no_info_node = key == media_sorter.NO_INFO_LABEL
            label = key if (in_no_info or is_no_info_node) else (
                media_sorter.month_folder_name(key) if depth == month_depth else key
            )
            count = media_sorter.count_files(value)
            if isinstance(value, list):
                self.treeview.insert(parent, "end", text=label, values=(count,), open=(depth == 0))
            else:
                child_node = self.treeview.insert(parent, "end", text=label, values=(count,), open=(depth == 0))
                self._populate_tree(child_node, value, depth + 1, month_depth, in_no_info=in_no_info or is_no_info_node)

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
        # Comparé aux dossiers réellement analysés (ceux qui ont produit tree_data), pas
        # au contenu actuel du champ "Dossier source" : sinon il suffit de vider ou
        # modifier ce champ après l'analyse pour contourner la vérification.
        if self._scanned_source_paths:
            resolved_dest = self._resolve_key(dest_path)
            if any(
                resolved_dest == scanned or resolved_dest.is_relative_to(scanned)
                for scanned in self._scanned_source_paths
            ):
                messagebox.showerror(
                    "Dossier de destination invalide",
                    "Le dossier de destination ne peut pas être un dossier source analysé, ni un de "
                    "ses sous-dossiers : cela copierait les fichiers dans un dossier en cours "
                    "d'analyse (risque de doublons en cascade, voire de boucle en mode récursif).",
                )
                return

        destination_map = media_sorter.build_destination_map(
            self.tree_data, self.sort_level.get(), self.separate_media.get()
        )
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
        self._copy_verb = verb
        self._copy_total = total
        self.status_label.config(text=f"{verb} en cours... 0 % (0/{total})")
        self.progress.pack(fill="x", padx=8, pady=(0, 6))
        self.progress.config(mode="determinate", maximum=total, value=0)

        self._copy_cancel_event = threading.Event()
        threading.Thread(
            target=self._copy_worker,
            args=(
                dest_path, destination_map, mode, self._copy_cancel_event,
                self.rename_files.get(), self.use_filename_fallback.get(),
            ),
            daemon=True,
        ).start()

    def cancel_copy(self):
        if self._copy_cancel_event is not None:
            self._copy_cancel_event.set()
        self.cancel_copy_button.config(state="disabled")
        self.status_label.config(text="Annulation en cours...")

    def _copy_worker(
        self, dest_path: Path, destination_map: dict, mode: str, cancel_event: threading.Event,
        rename_files: bool, use_filename_fallback: bool,
    ):
        try:
            done, duplicates, errors = media_sorter.copy_files(
                destination_map, dest_path, mode,
                cancel_event=cancel_event,
                on_progress=lambda done: self.root.after(0, self._update_progress, done),
                rename_files=rename_files,
                use_filename_fallback=use_filename_fallback,
            )
        except media_sorter.CopyCancelled as exc:
            done, duplicates, errors = exc.args
            self.root.after(0, self._copy_cancelled, done, duplicates, errors, mode)
            return
        except Exception as exc:
            media_sorter.logger.exception("Échec de la copie vers %s", dest_path)
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
        percent = int(done * 100 / self._copy_total) if self._copy_total else 100
        self.status_label.config(text=f"{self._copy_verb} en cours... {percent} % ({done}/{self._copy_total})")

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
