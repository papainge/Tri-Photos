"""Tests de charge : volumes réalistes (des milliers de fichiers) et fichiers vidéo
énormes (des centaines de Mo, via des fichiers creux/sparse pour rester rapides), pour
vérifier que le tri et les parseurs de métadonnées passent à l'échelle sans jamais
charger les données audio/vidéo en mémoire ni dégénérer en complexité quadratique.
"""

import os
import struct
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import media_sorter as ps

# Marge large pour rester fiable sur une machine/CI lente, tout en détectant une vraie
# régression de performance (lecture intégrale d'un fichier de centaines de Mo, boucle
# quadratique...) qui se chiffrerait en dizaines de secondes, pas en millisecondes.
MAX_SECONDS_LARGE_FILE = 2.0
MAX_SECONDS_MANY_FILES = 15.0


class TestVideoParserLoad(unittest.TestCase):
    """Vérifie que chaque parseur vidéo saute par-dessus les données massives (mdat,
    movi, Data Object, Cluster) sans les lire, même quand le fichier fait des centaines
    de Mo : le temps d'exécution doit rester quasi constant, pas proportionnel à la
    taille du fichier."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_mp4_skips_large_leading_mdat_efficiently(self):
        path = self.dir / "big_video.mp4"
        expected = datetime(2022, 1, 1, 0, 0, 0)
        creation_time = int((expected - ps.MP4_EPOCH).total_seconds())
        gap_size = 200 * 1024 * 1024

        ftyp_content = b"isom" + struct.pack(">I", 0) + b"isomiso2mp41"
        ftyp = struct.pack(">I", 8 + len(ftyp_content)) + b"ftyp" + ftyp_content
        mvhd_content = bytes([0]) + b"\x00\x00\x00" + struct.pack(">III", creation_time, 0, 600) + struct.pack(">I", 0)
        mvhd = struct.pack(">I", 8 + len(mvhd_content)) + b"mvhd" + mvhd_content
        moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
        mdat_header = struct.pack(">I", 8 + gap_size) + b"mdat"

        with open(path, "wb") as f:
            f.write(ftyp)
            f.write(mdat_header)
            f.seek(gap_size, os.SEEK_CUR)  # saute le "contenu" du mdat sans l'écrire (fichier creux)
            f.write(moov)
        self.assertGreater(path.stat().st_size, gap_size)

        start = time.time()
        date = ps.get_mp4_creation_date(path)
        elapsed = time.time() - start

        self.assertEqual(date, expected)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)

    def test_avi_skips_large_junk_list_efficiently(self):
        path = self.dir / "big_video.avi"
        expected = datetime(2021, 8, 20, 10, 0, 0)
        idit_text = expected.strftime("%a %b %d %H:%M:%S %Y")
        junk_size = 150 * 1024 * 1024  # pair, pour rester aligné sans octet de padding

        def chunk(fourcc, data):
            header = struct.pack("<4sI", fourcc, len(data))
            return header + data + (b"\x00" if len(data) % 2 else b"")

        idit_data = idit_text.encode("ascii") + b"\x00"
        info_list = chunk(b"LIST", b"INFO" + chunk(b"IDIT", idit_data))
        junk_content_size = 4 + junk_size  # listType "JUNK" + octets de bourrage
        junk_list_header = struct.pack("<4sI", b"LIST", junk_content_size)
        riff_content_size = 4 + len(junk_list_header) + junk_content_size + len(info_list)

        with open(path, "wb") as f:
            f.write(struct.pack("<4sI", b"RIFF", riff_content_size))
            f.write(b"AVI ")
            f.write(junk_list_header)
            f.write(b"JUNK")
            f.seek(junk_size, os.SEEK_CUR)  # LIST "JUNK" simule un énorme "movi" ignoré
            f.write(info_list)
        self.assertGreater(path.stat().st_size, junk_size)

        start = time.time()
        date = ps.get_avi_creation_date(path)
        elapsed = time.time() - start

        self.assertEqual(date, expected)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)

    def test_wmv_ignores_large_trailing_data_object_efficiently(self):
        path = self.dir / "big_video.wmv"
        expected = datetime(2019, 4, 10, 8, 30, 0)
        delta = expected - ps.FILETIME_EPOCH
        creation_filetime = (delta.days * 86400 + delta.seconds) * 10_000_000
        tail_size = 150 * 1024 * 1024

        file_properties_data = (
            b"\x00" * 16 + struct.pack("<Q", 0) + struct.pack("<Q", creation_filetime) + struct.pack("<Q", 0)
        )
        file_properties_object = (
            ps.ASF_FILE_PROPERTIES_OBJECT_GUID
            + struct.pack("<Q", 24 + len(file_properties_data))
            + file_properties_data
        )
        header_object_content = struct.pack("<IH", 1, 0) + file_properties_object
        header = ps.ASF_HEADER_OBJECT_GUID + struct.pack("<Q", 24 + len(header_object_content)) + header_object_content

        with open(path, "wb") as f:
            f.write(header)
            f.seek(tail_size, os.SEEK_CUR)  # simule le "Data Object" (audio/vidéo) qui suit
            f.write(b"\x00")
        self.assertGreater(path.stat().st_size, tail_size)

        start = time.time()
        date = ps.get_wmv_creation_date(path)
        elapsed = time.time() - start

        self.assertEqual(date, expected)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)

    def test_matroska_ignores_file_size_beyond_read_cap(self):
        path = self.dir / "big_video.mkv"
        expected = datetime(2020, 6, 1, 12, 0, 0)
        delta = expected - ps.MATROSKA_DATE_UTC_EPOCH
        date_utc_ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000
        tail_size = 150 * 1024 * 1024

        def elem(id_bytes, payload):
            return id_bytes + bytes([0x80 | len(payload)]) + payload

        info_content = elem(b"\x44\x61", struct.pack(">q", date_utc_ns))
        segment_content = elem(b"\x15\x49\xa9\x66", info_content)
        segment_elem = b"\x18\x53\x80\x67" + bytes([0x80 | len(segment_content)]) + segment_content
        ebml_header_elem = b"\x1a\x45\xdf\xa3" + bytes([0x80 | 4]) + b"\x00" * 4

        with open(path, "wb") as f:
            f.write(ebml_header_elem)
            f.write(segment_elem)
            f.seek(tail_size, os.SEEK_CUR)  # simule les Cluster (audio/vidéo) qui suivent Info
            f.write(b"\x00")
        self.assertGreater(path.stat().st_size, tail_size)

        start = time.time()
        date = ps.get_matroska_creation_date(path)
        elapsed = time.time() - start

        self.assertEqual(date, expected)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)


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

        start = time.time()
        existing_hashes = {ps.file_hash(p) for p in self.dest_dir.iterdir() if p.is_file()}
        elapsed_hash = time.time() - start

        self.assertEqual(len(existing_hashes), existing_count)
        self.assertLess(elapsed_hash, MAX_SECONDS_MANY_FILES)

        duplicate_src = self.src_dir / "dup.jpg"
        duplicate_src.write_bytes(b"contenu-42")  # même contenu que existing_00042.jpg
        new_src = self.src_dir / "new.jpg"
        new_src.write_bytes(b"contenu-totalement-nouveau")

        start = time.time()
        dup_result = ps.transfer_file(duplicate_src, self.dest_dir, existing_hashes, "copier")
        new_result = ps.transfer_file(new_src, self.dest_dir, existing_hashes, "copier")
        elapsed_transfer = time.time() - start

        self.assertEqual(dup_result, "duplicate")
        self.assertEqual(new_result, "copied")
        self.assertLess(elapsed_transfer, MAX_SECONDS_LARGE_FILE)


if __name__ == "__main__":
    unittest.main()
