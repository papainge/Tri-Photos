"""Tests de l'interface Tkinter (MediaSorterApp) : enchaînement des états des boutons,
délégation vers scan_media/copy_files, annulation, messages affichés. Jusqu'ici cette
couche n'était vérifiée qu'à la main (scripts ad hoc avec un vrai mainloop()) — ce
fichier fige ces vérifications dans la suite automatisée.

Les callbacks self.root.after(0, ...) déclenchés depuis le thread d'arrière-plan
(scan/copie) exigent un vrai mainloop() actif pour s'exécuter (Tkinter refuse
d'enregistrer une commande depuis un thread tant qu'on n'est pas "dans la boucle
principale") : voir run_steps()/run_and_wait() ci-dessous.

run_and_wait() interroge une condition d'arrêt au lieu d'attendre un délai fixe avant de
vérifier l'état final : la durée réelle d'une opération asynchrone peut varier selon la
charge de la machine (d'autant plus quand beaucoup de tests créent/détruisent des Tk()
en rafale dans le même process), un délai fixe "large" reste donc intrinsèquement
fragile là où c'est évitable.
"""

import gc
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image

import media_sorter as ms


def run_steps(root, steps):
    """Exécute une séquence de (délai_ms, fonction) avec un vrai mainloop() actif.
    La dernière fonction doit appeler root.quit() pour arrêter la boucle. À réserver aux
    cas où l'état à observer est lui-même figé de façon déterministe (ex: un mock
    bloquant via threading.Event) — sinon préférer run_and_wait()."""
    def schedule(index):
        if index >= len(steps):
            return

        delay, func = steps[index]

        def run():
            func()
            schedule(index + 1)

        root.after(delay, run)

    schedule(0)
    root.mainloop()


def run_and_wait(root, trigger_steps, predicate, timeout_ms=2000, poll_ms=20):
    """Exécute trigger_steps (une séquence de (délai_ms, fonction), enchaînée comme dans
    run_steps), puis interroge predicate() toutes les poll_ms jusqu'à ce qu'elle
    devienne vraie ou que timeout_ms soit écoulé, avant d'arrêter mainloop(). À utiliser
    pour attendre la fin d'une opération asynchrone (scan/copie) sans deviner un délai
    fixe : l'appelant peut ensuite lire l'état final directement (on est revenu dans du
    code synchrone, mainloop() est retourné)."""
    elapsed = {"ms": 0}

    def poll():
        if predicate() or elapsed["ms"] >= timeout_ms:
            root.quit()
            return
        elapsed["ms"] += poll_ms
        root.after(poll_ms, poll)

    def schedule(index):
        if index >= len(trigger_steps):
            poll()
            return

        delay, func = trigger_steps[index]

        def run():
            func()
            schedule(index + 1)

        root.after(delay, run)

    schedule(0)
    root.mainloop()


class AppTestCase(unittest.TestCase):
    """Base commune : Tk headless, MediaSorterApp, popups modales neutralisées et
    enregistrées (pour vérifier qu'un avertissement/une confirmation a bien été
    déclenché, sans jamais bloquer les tests sur une boîte de dialogue réelle)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.src_dir = Path(self.tmpdir.name) / "src"
        self.dest_dir = Path(self.tmpdir.name) / "dest"
        self.src_dir.mkdir()
        self.dest_dir.mkdir()

        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        # Force la finalisation des tk.Variable de CE test pendant que son interpréteur
        # Tcl est encore vivant (addCleanup s'exécute en LIFO : gc.collect() tourne donc
        # juste avant root.destroy() ci-dessus). Sans ça, le ramasse-miettes peut
        # finaliser ces objets bien plus tard, pendant l'exécution d'un test suivant —
        # leur __del__ tente alors d'appeler un interpréteur Tcl déjà détruit
        # ("RuntimeError: main thread is not in main loop"), ce qui a pu perturber le
        # traitement des événements d'un test qui n'a rien à voir.
        self.addCleanup(gc.collect)

        self.messages = []
        for name in ("showwarning", "showerror", "showinfo"):
            self._patch_messagebox(name)
        self._askyesno_answer = True
        original_askyesno = messagebox.askyesno
        messagebox.askyesno = lambda *a, **k: self._askyesno_answer
        self.addCleanup(setattr, messagebox, "askyesno", original_askyesno)

        original_askdirectory = filedialog.askdirectory
        self.addCleanup(setattr, filedialog, "askdirectory", original_askdirectory)

        self.app = ms.MediaSorterApp(self.root)
        self._photo_counter = 0

    def _patch_messagebox(self, name):
        original = getattr(messagebox, name)

        def recorder(title, text, *a, **k):
            self.messages.append((name, title, text))

        setattr(messagebox, name, recorder)
        self.addCleanup(setattr, messagebox, name, original)

    def _make_photo(self, relative_path, date=None):
        # Couleur distincte à chaque appel : deux images identiques (même contenu JPEG)
        # seraient détectées comme doublons par transfer_file() et fausseraient les
        # tests de copie qui comptent les fichiers réellement transférés.
        path = self.src_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._photo_counter += 1
        n = self._photo_counter
        color = (n % 256, (n * 7) % 256, (n * 13) % 256)
        Image.new("RGB", (2, 2), color=color).save(path)
        os.utime(path, ((date or datetime(2024, 1, 15)).timestamp(),) * 2)
        return path


class TestFolderPickers(AppTestCase):
    def test_choose_source_sets_source_dir_from_dialog(self):
        filedialog.askdirectory = lambda **k: str(self.src_dir)

        self.app.choose_source()

        self.assertEqual(self.app.source_dir.get(), str(self.src_dir))

    def test_choose_source_ignores_cancelled_dialog(self):
        self.app.source_dir.set("valeur initiale")
        filedialog.askdirectory = lambda **k: ""

        self.app.choose_source()

        self.assertEqual(self.app.source_dir.get(), "valeur initiale")

    def test_choose_dest_sets_dest_dir_from_dialog(self):
        filedialog.askdirectory = lambda **k: str(self.dest_dir)

        self.app.choose_dest()

        self.assertEqual(self.app.dest_dir.get(), str(self.dest_dir))


class TestOnCopyModeChange(AppTestCase):
    def test_button_text_reflects_selected_mode(self):
        self.assertEqual(self.app.create_button.cget("text"), "Créer l'arborescence et copier les fichiers")

        self.app.copy_mode.set("deplacer")
        self.app._on_copy_mode_change()

        self.assertEqual(self.app.create_button.cget("text"), "Créer l'arborescence et déplacer les fichiers")


class TestRefreshTreeview(AppTestCase):
    def test_populates_total_label_and_tree_nodes(self):
        self.app.tree_data = {"photos": {"2024": {"01": {"15": ["a.jpg", "b.jpg"]}}}, "videos": {}}

        self.app._refresh_treeview()

        self.assertIn("2 fichier(s)", self.app.total_label.cget("text"))
        self.assertIn("2 photo(s)", self.app.total_label.cget("text"))
        self.assertIn("0 vidéo(s)", self.app.total_label.cget("text"))
        self.assertEqual(str(self.app.create_button.cget("state")), "normal")
        self.assertEqual(len(self.app.treeview.get_children()), 1)

    def test_month_node_label_includes_french_name(self):
        self.app.tree_data = {"photos": {"2024": {"08": {"15": ["a.jpg"]}}}, "videos": {}}

        self.app._refresh_treeview()

        year_node = self.app.treeview.get_children()[0]
        month_node = self.app.treeview.get_children(year_node)[0]
        self.assertEqual(self.app.treeview.item(month_node, "text"), "08-Août")

    def test_separate_media_shows_category_root_nodes(self):
        self.app.tree_data = {
            "photos": {"2024": {"01": {"15": ["a.jpg"]}}},
            "videos": {"2024": {"01": {"15": ["v.mp4"]}}},
        }
        self.app.separate_media.set(True)

        self.app._refresh_treeview()

        labels = sorted(self.app.treeview.item(c, "text") for c in self.app.treeview.get_children())
        self.assertEqual(labels, ["Photos", "Vidéos"])

    def test_empty_tree_shows_no_files_message_and_disables_create(self):
        self.app.tree_data = {"photos": {}, "videos": {}}

        self.app._refresh_treeview()

        self.assertEqual(self.app.total_label.cget("text"), "")
        self.assertEqual(self.app.status_label.cget("text"), "Aucune photo ou vidéo trouvée dans ce dossier.")
        self.assertEqual(str(self.app.create_button.cget("state")), "disabled")

    def test_on_options_change_is_a_noop_without_tree_data(self):
        self.app.tree_data = {}
        self.app.total_label.config(text="valeur figée")

        self.app._on_options_change()

        self.assertEqual(self.app.total_label.cget("text"), "valeur figée")

    def test_on_options_change_refreshes_when_tree_data_present(self):
        self.app.tree_data = {"photos": {"2024": {"01": {"15": ["a.jpg"]}}}, "videos": {}}
        self.app.total_label.config(text="valeur figée")

        self.app._on_options_change()

        self.assertNotEqual(self.app.total_label.cget("text"), "valeur figée")


class TestStartScanValidation(AppTestCase):
    def test_warns_when_source_dir_empty(self):
        self.app.start_scan()

        self.assertEqual(self.messages, [("showwarning", "Dossier manquant", "Veuillez choisir un dossier source.")])
        self.assertEqual(str(self.app.scan_button.cget("state")), "normal")

    def test_errors_when_source_dir_does_not_exist(self):
        self.app.source_dir.set(str(self.src_dir / "introuvable"))

        self.app.start_scan()

        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0][0], "showerror")


class TestScanLifecycle(AppTestCase):
    def test_successful_scan_populates_tree_and_resets_buttons(self):
        self._make_photo("a.jpg")
        self._make_photo("b.jpg")
        self.app.source_dir.set(str(self.src_dir))

        run_and_wait(
            self.root, [(10, self.app.start_scan)],
            lambda: str(self.app.scan_button.cget("state")) == "normal",
        )

        self.assertIn("2 fichier(s)", self.app.total_label.cget("text"))
        self.assertEqual(str(self.app.cancel_scan_button.cget("state")), "disabled")
        self.assertEqual(str(self.app.create_button.cget("state")), "normal")
        self.assertEqual(len(self.app.treeview.get_children()), 1)

    def test_buttons_toggle_while_scan_is_running(self):
        # get_media_date bloque tant qu'on n'a pas appelé release.set() : contrairement à
        # un délai fixe, ça garantit de pouvoir observer l'état "en cours" sans dépendre
        # de la vitesse de la machine (un seul petit fichier peut être scanné en moins
        # d'une milliseconde).
        self._make_photo("a.jpg")
        self.app.source_dir.set(str(self.src_dir))
        release = threading.Event()

        def blocking_get_media_date(path, _original=ms.get_media_date):
            release.wait(timeout=2)
            return _original(path)

        results = {}
        with unittest.mock.patch.object(ms, "get_media_date", side_effect=blocking_get_media_date):
            run_steps(self.root, [
                (10, self.app.start_scan),
                (30, lambda: results.update(
                    scan_button=str(self.app.scan_button.cget("state")),
                    cancel_scan_button=str(self.app.cancel_scan_button.cget("state")),
                )),
                (0, release.set),
                (0, self.root.quit),
            ])
            # Attend la fin réelle du thread d'arrière-plan avant de continuer : sinon il
            # peut encore tourner pendant que addCleanup() détruit self.root, ce qui a pu
            # perturber d'autres tests dans le même process (interpréteur Tcl partagé).
            run_and_wait(self.root, [], lambda: str(self.app.scan_button.cget("state")) == "normal")

        self.assertEqual(results["scan_button"], "disabled")
        self.assertEqual(results["cancel_scan_button"], "normal")

    def test_scan_of_empty_folder_shows_no_files_message(self):
        self.app.source_dir.set(str(self.src_dir))

        run_and_wait(
            self.root, [(10, self.app.start_scan)],
            lambda: str(self.app.scan_button.cget("state")) == "normal",
        )

        self.assertEqual(self.app.status_label.cget("text"), "Aucune photo ou vidéo trouvée dans ce dossier.")
        self.assertEqual(str(self.app.create_button.cget("state")), "disabled")

    def test_cancel_scan_stops_and_resets_buttons(self):
        # Déterministe plutôt que basé sur un minutage : scan_media parallélise
        # get_media_date sur plusieurs threads, donc "annuler après 20ms" n'offre aucune
        # garantie face à un nombre de coeurs variable (le scan peut finir avant). Le
        # mock déclenche l'annulation lui-même après exactement 5 appels réels.
        for i in range(30):
            self._make_photo(f"p{i}.jpg")
        self.app.source_dir.set(str(self.src_dir))

        call_count = {"n": 0}
        lock = threading.Lock()

        def cancel_after_five_calls(path, _original=ms.get_media_date):
            result = _original(path)
            with lock:
                call_count["n"] += 1
                if call_count["n"] == 5:
                    self.app._scan_cancel_event.set()
            return result

        with unittest.mock.patch.object(ms, "get_media_date", side_effect=cancel_after_five_calls):
            run_and_wait(
                self.root, [(10, self.app.start_scan)],
                lambda: str(self.app.scan_button.cget("state")) == "normal",
            )

        self.assertEqual(self.app.status_label.cget("text"), "Analyse annulée.")
        self.assertEqual(str(self.app.cancel_scan_button.cget("state")), "disabled")

    def test_scan_error_shows_message_and_resets_buttons(self):
        self.app.source_dir.set(str(self.src_dir))

        with unittest.mock.patch.object(ms, "scan_media", side_effect=RuntimeError("boom")):
            run_and_wait(
                self.root, [(10, self.app.start_scan)],
                lambda: str(self.app.scan_button.cget("state")) == "normal",
            )

        self.assertEqual(self.app.status_label.cget("text"), "Erreur lors de l'analyse.")
        self.assertTrue(any(m[0] == "showerror" for m in self.messages))


class TestStartCopyValidation(AppTestCase):
    def test_warns_when_dest_dir_empty(self):
        self.app.tree_data = {"photos": {"2024": {"01": {"15": ["a.jpg"]}}}, "videos": {}}

        self.app.start_copy()

        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0][:2], ("showwarning", "Dossier manquant"))

    def test_warns_when_tree_data_empty(self):
        self.app.dest_dir.set(str(self.dest_dir))

        self.app.start_copy()

        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0][:2], ("showwarning", "Rien à copier"))

    def test_does_nothing_when_confirmation_declined(self):
        photo = self._make_photo("a.jpg")
        self.app.tree_data = {"photos": {"2024": {"01": {"15": [photo]}}}, "videos": {}}
        self.app.dest_dir.set(str(self.dest_dir))
        self.app.create_button.config(state="normal")
        self._askyesno_answer = False

        self.app.start_copy()

        self.assertEqual(str(self.app.create_button.cget("state")), "normal")
        self.assertEqual(list(self.dest_dir.rglob("*.jpg")), [])

    def test_errors_when_dest_dir_equals_source_dir(self):
        photo = self._make_photo("a.jpg")
        self.app.tree_data = {"photos": {"2024": {"01": {"15": [photo]}}}, "videos": {}}
        self.app.source_dir.set(str(self.src_dir))
        self.app.dest_dir.set(str(self.src_dir))

        self.app.start_copy()

        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0][:2], ("showerror", "Dossier de destination invalide"))

    def test_errors_when_dest_dir_is_nested_inside_source_dir(self):
        photo = self._make_photo("a.jpg")
        self.app.tree_data = {"photos": {"2024": {"01": {"15": [photo]}}}, "videos": {}}
        nested_dest = self.src_dir / "sorted"
        nested_dest.mkdir()
        self.app.source_dir.set(str(self.src_dir))
        self.app.dest_dir.set(str(nested_dest))

        self.app.start_copy()

        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0][:2], ("showerror", "Dossier de destination invalide"))


class TestCopyLifecycle(AppTestCase):
    def test_successful_copy_transfers_files_and_shows_summary(self):
        photo = self._make_photo("a.jpg")
        self.app.tree_data = {"photos": {"2024": {"01": {"15": [photo]}}}, "videos": {}}
        self.app.dest_dir.set(str(self.dest_dir))

        run_and_wait(
            self.root, [(10, self.app.start_copy)],
            lambda: str(self.app.create_button.cget("state")) == "normal",
        )

        copied = list(self.dest_dir.rglob("*.jpg"))
        self.assertEqual(len(copied), 1)
        self.assertTrue(photo.exists())  # mode copier par défaut : la source est conservée
        self.assertIn("copié", self.app.status_label.cget("text"))
        self.assertEqual(str(self.app.cancel_copy_button.cget("state")), "disabled")
        self.assertTrue(any(m[0] == "showinfo" for m in self.messages))

    def test_deplacer_mode_removes_source_files(self):
        photo = self._make_photo("a.jpg")
        self.app.tree_data = {"photos": {"2024": {"01": {"15": [photo]}}}, "videos": {}}
        self.app.dest_dir.set(str(self.dest_dir))
        self.app.copy_mode.set("deplacer")

        run_and_wait(
            self.root, [(10, self.app.start_copy)],
            lambda: str(self.app.create_button.cget("state")) == "normal",
        )

        self.assertFalse(photo.exists())
        self.assertEqual(len(list(self.dest_dir.rglob("*.jpg"))), 1)

    def test_buttons_toggle_while_copy_is_running(self):
        # transfer_file bloque tant qu'on n'a pas appelé release.set() : contrairement à
        # un délai fixe, ça garantit de pouvoir observer l'état "en cours" sans dépendre
        # de la vitesse de la machine (copier un seul petit fichier peut être quasi
        # instantané).
        photo = self._make_photo("a.jpg")
        self.app.tree_data = {"photos": {"2024": {"01": {"15": [photo]}}}, "videos": {}}
        self.app.dest_dir.set(str(self.dest_dir))
        release = threading.Event()

        def blocking_transfer_file(*args, _original=ms.transfer_file, **kwargs):
            release.wait(timeout=2)
            return _original(*args, **kwargs)

        results = {}
        with unittest.mock.patch.object(ms, "transfer_file", side_effect=blocking_transfer_file):
            run_steps(self.root, [
                (10, self.app.start_copy),
                (30, lambda: results.update(
                    create_button=str(self.app.create_button.cget("state")),
                    cancel_copy_button=str(self.app.cancel_copy_button.cget("state")),
                )),
                (0, release.set),
                (0, self.root.quit),
            ])
            # Attend la fin réelle du thread d'arrière-plan avant de continuer : sinon il
            # peut encore tourner pendant que addCleanup() détruit self.root, ce qui a pu
            # perturber d'autres tests dans le même process (interpréteur Tcl partagé).
            run_and_wait(self.root, [], lambda: str(self.app.create_button.cget("state")) == "normal")

        self.assertEqual(results["create_button"], "disabled")
        self.assertEqual(results["cancel_copy_button"], "normal")

    def test_cancel_copy_stops_and_reports_partial_progress(self):
        # Déterministe plutôt que basé sur un minutage : le mock annule explicitement
        # après exactement 2 fichiers réellement transférés, au lieu de deviner un délai
        # qui laisserait "environ" le temps d'en traiter quelques-uns.
        photos = [self._make_photo(f"p{i}.jpg") for i in range(10)]
        self.app.tree_data = {"photos": {"2024": {"01": {"15": photos}}}, "videos": {}}
        self.app.dest_dir.set(str(self.dest_dir))

        call_count = {"n": 0}

        def transfer_then_cancel_after_two(*args, _original=ms.transfer_file, **kwargs):
            result = _original(*args, **kwargs)
            call_count["n"] += 1
            if call_count["n"] == 2:
                self.app._copy_cancel_event.set()
            return result

        with unittest.mock.patch.object(ms, "transfer_file", side_effect=transfer_then_cancel_after_two):
            run_and_wait(
                self.root, [(10, self.app.start_copy)],
                lambda: str(self.app.create_button.cget("state")) == "normal",
            )

        self.assertIn("Annulé", self.app.status_label.cget("text"))
        self.assertEqual(str(self.app.cancel_copy_button.cget("state")), "disabled")
        self.assertEqual(len(list(self.dest_dir.rglob("*.jpg"))), 2)


if __name__ == "__main__":
    unittest.main()
