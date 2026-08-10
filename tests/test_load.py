"""Tests de charge : volumes réalistes (des milliers de fichiers), pour vérifier que le
tri passe à l'échelle sans dégénérer en complexité quadratique.

Les tests de charge propres aux parseurs vidéo (fichiers de centaines de Mo) vivent
dans tests/test_video_metadata.py, à côté des parseurs qu'ils testent.
"""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Avant l'import : redirige les logs de media_sorter hors du vrai dossier de logs de la
# machine qui exécute les tests (voir media_sorter._log_directory). Les tests de charge
# génèrent volontairement de nombreux fichiers sans métadonnée exploitable, qui
# déclencheraient sinon des avertissements en rafale dans le journal réel de l'utilisateur.
os.environ.setdefault("TRIPHOTOS_LOG_DIR", str(Path(tempfile.gettempdir()) / "triphotos-tests-logs"))
os.environ.setdefault("TRIPHOTOS_CONFIG_DIR", str(Path(tempfile.gettempdir()) / "triphotos-tests-config"))

import media_sorter as ps

# Marge large pour rester fiable sur une machine/CI lente, tout en détectant une vraie
# régression de performance (boucle quadratique...) qui se chiffrerait en dizaines de
# secondes, pas en millisecondes.
MAX_SECONDS_LARGE_FILE = 2.0
MAX_SECONDS_MANY_FILES = 15.0


class TestScanLoad(unittest.TestCase):
    """Vérifie que scan_media reste rapide et correct sur un volume de fichiers
    représentatif d'un vrai dossier de photos accumulées sur des années."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_scan_media_handles_thousands_of_files_across_many_dates(self):
        total = 3000
        for i in range(total):
            # Fichiers vides : la lecture EXIF échoue puis retombe immédiatement sur la
            # date de modification (rapide), ce qui isole ici la charge propre au
            # parcours/regroupement plutôt qu'au décodage d'image.
            subdir = self.dir / f"dossier_{i % 20}"  # imite des sous-dossiers (import par lot, etc.)
            subdir.mkdir(exist_ok=True)
            path = subdir / f"photo_{i:05d}.jpg"
            path.write_bytes(b"")
            date = datetime(2010 + (i % 15), (i % 12) + 1, (i % 28) + 1, 12, 0, 0)
            os.utime(path, (date.timestamp(), date.timestamp()))

        start = time.time()
        tree = ps.scan_media(self.dir)
        elapsed = time.time() - start

        self.assertEqual(ps.count_files(tree), total)
        self.assertLess(elapsed, MAX_SECONDS_MANY_FILES)


class TestAggregationLoad(unittest.TestCase):
    """Vérifie que l'agrégation par niveau de tri et la construction du mapping de
    destination restent rapides sur un arbre représentant plusieurs milliers de
    fichiers répartis sur de nombreuses années (opérations en mémoire, sans I/O)."""

    def _build_large_tree(self, files_per_day=5):
        category_tree = {}
        total = 0
        for year in range(2005, 2025):  # 20 ans
            for month in range(1, 13):
                for day in (1, 10, 20):  # 3 jours par mois suffisent à générer un volume réaliste
                    files = [f"{year}-{month:02d}-{day:02d}_{i}.jpg" for i in range(files_per_day)]
                    category_tree.setdefault(str(year), {}).setdefault(f"{month:02d}", {})[f"{day:02d}"] = files
                    total += len(files)
        return category_tree, total

    def test_build_destination_map_handles_large_tree(self):
        photos_tree, total = self._build_large_tree(files_per_day=5)
        tree = {"photos": photos_tree, "videos": {}}

        start = time.time()
        dest_map = ps.build_destination_map(tree, "jour", separate_media=False)
        elapsed = time.time() - start

        self.assertEqual(sum(len(v) for v in dest_map.values()), total)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)

    def test_build_display_tree_handles_large_tree_when_separated(self):
        photos_tree, total = self._build_large_tree(files_per_day=5)
        tree = {"photos": photos_tree, "videos": {}}

        start = time.time()
        display = ps.build_display_tree(tree, "annee", separate_media=True)
        elapsed = time.time() - start

        self.assertEqual(ps.count_files(display), total)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)


class TestDuplicateDetectionLoad(unittest.TestCase):
    """Vérifie que la détection de doublons reste correcte et rapide face à un dossier
    de destination déjà bien rempli, comme lors d'un ré-import répété sur des années."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dest_dir = Path(self.tmpdir.name) / "dest"
        self.src_dir = Path(self.tmpdir.name) / "src"
        self.dest_dir.mkdir()
        self.src_dir.mkdir()

    def test_duplicate_check_scales_with_many_existing_files(self):
        existing_count = 1000
        for i in range(existing_count):
            (self.dest_dir / f"existing_{i:05d}.jpg").write_bytes(f"contenu-{i}".encode())

        # Construire l'index par taille ne fait que des stat(), jamais de hachage complet :
        # ça doit rester très rapide même sur un dossier de destination déjà bien rempli.
        start = time.time()
        pending_by_size = ps.build_pending_size_index(self.dest_dir)
        elapsed_index = time.time() - start

        self.assertEqual(sum(len(v) for v in pending_by_size.values()), existing_count)
        self.assertLess(elapsed_index, MAX_SECONDS_LARGE_FILE)

        hashed_by_size = {}
        duplicate_src = self.src_dir / "dup.jpg"
        duplicate_src.write_bytes(b"contenu-42")  # même taille et contenu que existing_00042.jpg
        new_src = self.src_dir / "new.jpg"
        new_src.write_bytes(b"contenu totalement nouveau, et de taille unique dans ce dossier")

        start = time.time()
        dup_result = ps.transfer_file(duplicate_src, self.dest_dir, pending_by_size, hashed_by_size, "copier")
        new_result = ps.transfer_file(new_src, self.dest_dir, pending_by_size, hashed_by_size, "copier")
        elapsed_transfer = time.time() - start

        self.assertEqual(dup_result, "duplicate")
        self.assertEqual(new_result, "copied")
        self.assertLess(elapsed_transfer, MAX_SECONDS_LARGE_FILE)


if __name__ == "__main__":
    unittest.main()
