import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image

import media_sorter as ps
import photo_metadata as pm
import video_metadata as vm

# Le détail par format (EXIF, MP4/MOV, AVI, WMV, MKV/WEBM...) est couvert dans
# tests/test_photo_metadata.py et tests/test_video_metadata.py. Ici, on vérifie
# seulement que get_media_date délègue correctement selon l'extension et renvoie None
# (pas la date de modification, non fiable) quand aucun module ne trouve de métadonnée.


class TestGetMediaDate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_delegates_to_photo_metadata_for_image_extensions(self):
        path = self.dir / "photo.jpg"
        img = Image.new("RGB", (2, 2), color="red")
        exif = img.getexif()
        exif.get_ifd(pm.ExifTags.IFD.Exif)[pm.EXIF_DATE_TIME_ORIGINAL] = "2020:05:17 10:30:00"
        img.save(path, exif=exif)

        self.assertEqual(ps.get_media_date(path), datetime(2020, 5, 17, 10, 30, 0))

    def test_returns_none_when_photo_has_no_exif(self):
        path = self.dir / "photo.png"
        Image.new("RGB", (2, 2), color="blue").save(path)
        expected = datetime(2019, 3, 4, 8, 15, 0)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertIsNone(ps.get_media_date(path))

    def test_delegates_to_video_metadata_for_video_extensions(self):
        path = self.dir / "video.mp4"
        expected = datetime(2023, 6, 15, 12, 0, 0)
        creation_time = int((expected - vm.MP4_EPOCH).total_seconds())
        mvhd_content = bytes([0]) + b"\x00\x00\x00" + struct.pack(">III", creation_time, 0, 600) + struct.pack(">I", 0)
        mvhd = struct.pack(">I", 8 + len(mvhd_content)) + b"mvhd" + mvhd_content
        moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
        ftyp_content = b"isom" + struct.pack(">I", 0) + b"isomiso2mp41"
        ftyp = struct.pack(">I", 8 + len(ftyp_content)) + b"ftyp" + ftyp_content
        path.write_bytes(ftyp + moov)

        wrong = datetime(1999, 1, 1)
        os.utime(path, (wrong.timestamp(), wrong.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def test_returns_none_when_video_metadata_is_absent(self):
        # MPG/MPEG n'est géré par aucun des parseurs vidéo (voir test_video_metadata.py).
        path = self.dir / "video.mpg"
        path.write_bytes(b"\x00\x00\x01\xba" + b"\x00" * 20)
        expected = datetime(2020, 7, 1, 10, 0, 0)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertIsNone(ps.get_media_date(path))

    def test_returns_none_when_photo_metadata_parser_raises(self):
        # Jusqu'ici seul le cas "pas de métadonnée trouvée" (retour None) était couvert :
        # get_photo_exif_date() peut aussi lever (fichier corrompu, format non reconnu
        # par Pillow...), et get_media_date() doit absorber ce cas-là aussi plutôt que de
        # faire échouer toute l'analyse (voir scan_media, qui ne rattrape que OSError).
        path = self.dir / "photo.jpg"
        Image.new("RGB", (2, 2)).save(path)

        # Patché sur ps (media_sorter), pas sur pm (photo_metadata) : media_sorter en a
        # importé sa propre référence ("from photo_metadata import get_photo_exif_date"),
        # patcher pm.get_photo_exif_date ne l'affecterait pas.
        with unittest.mock.patch.object(ps, "get_photo_exif_date", side_effect=OSError("fichier corrompu")):
            self.assertIsNone(ps.get_media_date(path))

    def test_returns_none_when_video_metadata_parser_raises(self):
        path = self.dir / "video.mp4"
        path.write_bytes(b"\x00" * 16)

        with unittest.mock.patch.object(ps, "get_video_creation_date", side_effect=OSError("fichier corrompu")):
            self.assertIsNone(ps.get_media_date(path))


class TestScanMedia(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def _make_png(self, relative_path, date):
        # La date est portée par le tag EXIF DateTime (IFD0, seul à survivre à
        # l'enregistrement en PNG par Pillow — contrairement au sous-IFD Exif utilisé
        # pour DateTimeOriginal, voir test_photo_metadata.py) : depuis que
        # get_media_date ne retombe plus sur la date de modification du fichier, s'appuyer
        # sur os.utime() seul ne classerait plus ces fichiers par date mais sous
        # NO_INFO_LABEL.
        path = self.dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (2, 2))
        exif = img.getexif()
        exif[pm.EXIF_DATE_TIME] = date.strftime("%Y:%m:%d %H:%M:%S")
        img.save(path, exif=exif)
        return path

    def _make_video(self, relative_path, date):
        # Boîte MP4 "moov"/"mvhd" minimale mais valide portant creation_time (voir
        # test_delegates_to_video_metadata_for_video_extensions) : un fichier vidéo sans
        # métadonnée exploitable serait désormais classé sous NO_INFO_LABEL plutôt que
        # daté par sa date de modification.
        path = self.dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        creation_time = int((date - vm.MP4_EPOCH).total_seconds())
        mvhd_content = bytes([0]) + b"\x00\x00\x00" + struct.pack(">III", creation_time, 0, 600) + struct.pack(">I", 0)
        mvhd = struct.pack(">I", 8 + len(mvhd_content)) + b"mvhd" + mvhd_content
        moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
        ftyp_content = b"isom" + struct.pack(">I", 0) + b"isomiso2mp41"
        ftyp = struct.pack(">I", 8 + len(ftyp_content)) + b"ftyp" + ftyp_content
        path.write_bytes(ftyp + moov)
        return path

    def test_groups_by_year_month_day(self):
        p1 = self._make_png("a.png", datetime(2024, 1, 15))
        p2 = self._make_png("sub/b.png", datetime(2024, 1, 15))
        p3 = self._make_png("c.png", datetime(2024, 2, 1))
        p4 = self._make_png("d.png", datetime(2023, 12, 25))

        tree = ps.scan_media(self.dir)["photos"]

        self.assertEqual(set(tree.keys()), {"2024", "2023"})
        self.assertEqual(sorted(tree["2024"]["01"]["15"]), sorted([p1, p2]))
        self.assertEqual(tree["2024"]["02"]["01"], [p3])
        self.assertEqual(tree["2023"]["12"]["25"], [p4])

    def test_groups_files_without_metadata_under_no_info(self):
        # PNG sans EXIF : get_media_date renvoie None, peu importe sa date de
        # modification (voir test_returns_none_when_photo_has_no_exif).
        no_info = self._make_png("sans_date.png", datetime(2024, 1, 1))
        dated = self._make_png("avec_date.png", datetime(2024, 1, 1))
        with unittest.mock.patch.object(
            ps, "get_media_date",
            side_effect=lambda path: None if path == no_info else datetime(2024, 1, 15),
        ):
            tree = ps.scan_media(self.dir)["photos"]

        self.assertEqual(tree[ps.NO_INFO_LABEL], [no_info])
        self.assertEqual(tree["2024"]["01"]["15"], [dated])

    def test_ignores_unsupported_files(self):
        self._make_png("a.png", datetime(2024, 1, 1))
        (self.dir / "notes.txt").write_text("pas une photo ni une vidéo")

        tree = ps.scan_media(self.dir)

        self.assertEqual(ps.count_files(tree), 1)

    def test_recurses_into_deeply_nested_subfolders(self):
        self._make_png("a.png", datetime(2024, 1, 1))
        self._make_png("n1/b.png", datetime(2024, 1, 1))
        self._make_png("n1/n2/c.png", datetime(2024, 1, 1))
        self._make_png("n1/n2/n3/d.png", datetime(2024, 1, 1))

        tree = ps.scan_media(self.dir)

        self.assertEqual(ps.count_files(tree), 4)

    def test_non_recursive_ignores_subfolders(self):
        self._make_png("a.png", datetime(2024, 1, 1))
        self._make_png("n1/b.png", datetime(2024, 1, 1))
        self._make_png("n1/n2/c.png", datetime(2024, 1, 1))

        tree = ps.scan_media(self.dir, recursive=False)

        self.assertEqual(ps.count_files(tree), 1)

    def test_separates_photos_and_videos(self):
        photo = self._make_png("a.png", datetime(2024, 1, 15))
        video = self._make_video("b.mp4", datetime(2024, 1, 15))

        tree = ps.scan_media(self.dir)

        self.assertEqual(tree["photos"]["2024"]["01"]["15"], [photo])
        self.assertEqual(tree["videos"]["2024"]["01"]["15"], [video])

    def test_raises_scan_cancelled_when_event_already_set(self):
        self._make_png("a.png", datetime(2024, 1, 1))
        cancel_event = threading.Event()
        cancel_event.set()

        with self.assertRaises(ps.ScanCancelled):
            ps.scan_media(self.dir, cancel_event=cancel_event)

    def test_not_cancelled_without_event(self):
        self._make_png("a.png", datetime(2024, 1, 1))

        tree = ps.scan_media(self.dir, cancel_event=None)

        self.assertEqual(ps.count_files(tree), 1)

    def test_not_cancelled_when_event_unset(self):
        self._make_png("a.png", datetime(2024, 1, 1))

        tree = ps.scan_media(self.dir, cancel_event=threading.Event())

        self.assertEqual(ps.count_files(tree), 1)

    def test_handles_many_files_correctly_when_parallelized(self):
        # La lecture des dates est parallélisée sur plusieurs threads : on vérifie
        # l'absence de conflit d'accès (résultats perdus, dates mélangées entre
        # fichiers...) lors de la construction de l'arbre à partir des résultats.
        expected_dates = {}
        for i in range(200):
            date = datetime(2020 + (i % 5), (i % 12) + 1, (i % 28) + 1)
            path = self._make_png(f"photo_{i:03d}.png", date)
            expected_dates[path] = date

        tree = ps.scan_media(self.dir)["photos"]

        self.assertEqual(ps.count_files(tree), 200)
        for path, date in expected_dates.items():
            year, month, day = str(date.year), f"{date.month:02d}", f"{date.day:02d}"
            self.assertIn(path, tree[year][month][day])

    def test_forwards_max_workers_to_the_thread_pool(self):
        # max_workers n'est jamais passé par l'appelant réel (l'UI s'en tient toujours au
        # défaut) ni par les autres tests d'ici : rien ne prouvait que la valeur fournie
        # atteignait bien ThreadPoolExecutor plutôt que d'être silencieusement ignorée.
        self._make_png("a.png", datetime(2024, 1, 1))

        with unittest.mock.patch.object(ps, "ThreadPoolExecutor", wraps=ThreadPoolExecutor) as mock_executor:
            tree = ps.scan_media(self.dir, max_workers=3)

        mock_executor.assert_called_once_with(max_workers=3)
        self.assertEqual(ps.count_files(tree), 1)

    def test_skips_file_that_disappears_during_scan_without_failing_the_rest(self):
        # Régression : un seul fichier devenu inaccessible entre l'énumération et la
        # lecture de sa date (supprimé par un autre programme, partage réseau débranché,
        # chemin trop long) ne doit pas faire échouer toute l'analyse — voir get_media_date
        # avant correctif, dont le repli sur path.stat() n'était protégé par rien.
        ok1 = self._make_png("a.png", datetime(2024, 1, 1))
        broken = self._make_png("broken.png", datetime(2024, 1, 1))
        ok2 = self._make_png("b.png", datetime(2024, 1, 2))

        original_get_media_date = ps.get_media_date

        def fail_for_broken_only(path, _original=original_get_media_date):
            if path == broken:
                raise OSError("fichier disparu")
            return _original(path)

        with unittest.mock.patch.object(ps, "get_media_date", side_effect=fail_for_broken_only):
            tree = ps.scan_media(self.dir)["photos"]

        self.assertEqual(ps.count_files(tree), 2)
        self.assertIn(ok1, tree["2024"]["01"]["01"])
        self.assertIn(ok2, tree["2024"]["01"]["02"])
        self.assertNotIn(broken, [f for days in tree.get("2024", {}).values() for files in days.values() for f in files])

    def test_cancels_during_parallel_result_processing(self):
        # Contrairement à test_raises_scan_cancelled_when_event_already_set (déclenché
        # avant même de lister les fichiers), ce test vérifie le second point de
        # vérification : pendant la récupération des résultats déjà lancés en parallèle.
        for i in range(20):
            self._make_png(f"photo_{i}.png", datetime(2024, 1, 1))
        cancel_event = threading.Event()

        original_get_media_date = ps.get_media_date

        def slow_get_media_date(path):
            time.sleep(0.05)
            return original_get_media_date(path)

        timer = threading.Timer(0.02, cancel_event.set)
        timer.start()
        try:
            with unittest.mock.patch.object(ps, "get_media_date", side_effect=slow_get_media_date):
                with self.assertRaises(ps.ScanCancelled):
                    ps.scan_media(self.dir, cancel_event=cancel_event)
        finally:
            timer.cancel()


class TestListMediaFiles(unittest.TestCase):
    # list_media_files() est passée de Path.rglob()/glob() + path.is_file() à
    # os.walk()/os.scandir() (voir le module) : ces tests vérifient que la distinction
    # fichier/dossier reste correcte avec la nouvelle implémentation, en particulier
    # pour un dossier dont le nom ressemble à un fichier média — os.walk() classe déjà
    # les entrées par type sans repasser par un is_file() par entrée.
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_recursive_ignores_a_subdirectory_named_like_a_media_file(self):
        (self.dir / "fake.jpg").mkdir()
        real_photo = self.dir / "real.jpg"
        Image.new("RGB", (2, 2)).save(real_photo)

        candidates = ps.list_media_files(self.dir, recursive=True)

        self.assertEqual(candidates, [(real_photo, "photos")])

    def test_non_recursive_ignores_a_subdirectory_named_like_a_media_file(self):
        (self.dir / "fake.jpg").mkdir()
        real_photo = self.dir / "real.jpg"
        Image.new("RGB", (2, 2)).save(real_photo)

        candidates = ps.list_media_files(self.dir, recursive=False)

        self.assertEqual(candidates, [(real_photo, "photos")])

    def test_non_recursive_ignores_subfolder_content(self):
        Image.new("RGB", (2, 2)).save(self.dir / "root.jpg")
        nested = self.dir / "nested"
        nested.mkdir()
        Image.new("RGB", (2, 2)).save(nested / "inside.jpg")

        candidates = ps.list_media_files(self.dir, recursive=False)

        self.assertEqual([path.name for path, _category in candidates], ["root.jpg"])


class TestFileHash(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_identical_content_same_hash(self):
        p1 = self.dir / "a.bin"
        p2 = self.dir / "b.bin"
        p1.write_bytes(b"hello world")
        p2.write_bytes(b"hello world")

        self.assertEqual(ps.file_hash(p1), ps.file_hash(p2))

    def test_different_content_different_hash(self):
        p1 = self.dir / "a.bin"
        p2 = self.dir / "b.bin"
        p1.write_bytes(b"hello world")
        p2.write_bytes(b"goodbye world")

        self.assertNotEqual(ps.file_hash(p1), ps.file_hash(p2))


class TestPartialFileHash(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_identical_content_same_partial_hash(self):
        p1 = self.dir / "a.bin"
        p2 = self.dir / "b.bin"
        p1.write_bytes(b"hello world")
        p2.write_bytes(b"hello world")

        self.assertEqual(ps.partial_file_hash(p1), ps.partial_file_hash(p2))

    def test_different_content_different_partial_hash(self):
        p1 = self.dir / "a.bin"
        p2 = self.dir / "b.bin"
        p1.write_bytes(b"hello world")
        p2.write_bytes(b"goodbye world")

        self.assertNotEqual(ps.partial_file_hash(p1), ps.partial_file_hash(p2))

    def test_ignores_middle_of_large_files_beyond_the_sample_window(self):
        # C'est précisément ce qui rend le hash partiel bon marché sur de gros
        # fichiers (vidéos) : seuls le début et la fin sont lus, jamais le milieu.
        p1 = self.dir / "a.bin"
        p2 = self.dir / "b.bin"
        p1.write_bytes(b"A" * 10 + b"milieu-1" + b"Z" * 10)
        p2.write_bytes(b"A" * 10 + b"milieu-2" + b"Z" * 10)

        self.assertEqual(ps.partial_file_hash(p1, sample_size=10), ps.partial_file_hash(p2, sample_size=10))

    def test_detects_difference_in_the_tail_of_a_large_file(self):
        p1 = self.dir / "a.bin"
        p2 = self.dir / "b.bin"
        p1.write_bytes(b"A" * 10 + b"milieu" + b"Z" * 10)
        p2.write_bytes(b"A" * 10 + b"milieu" + b"Y" * 10)

        self.assertNotEqual(ps.partial_file_hash(p1, sample_size=10), ps.partial_file_hash(p2, sample_size=10))


class TestDatedFilename(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def _make_dated_photo(self, name, raw_date):
        path = self.dir / name
        img = Image.new("RGB", (2, 2))
        exif = img.getexif()
        exif.get_ifd(pm.ExifTags.IFD.Exif)[pm.EXIF_DATE_TIME_ORIGINAL] = raw_date
        img.save(path, exif=exif)
        return path

    def test_uses_media_date_when_available(self):
        path = self._make_dated_photo("a.jpg", "2024:08:15 14:30:22")

        self.assertEqual(ps.dated_filename(path), "2024-08-15_143022.jpg")

    def test_preserves_original_suffix_case(self):
        path = self._make_dated_photo("a.JPG", "2024:08:15 14:30:22")

        self.assertEqual(ps.dated_filename(path), "2024-08-15_143022.JPG")

    def test_falls_back_to_original_name_without_date(self):
        # PNG sans EXIF : get_media_date renvoie None (voir get_photo_exif_date), rien à
        # proposer comme nom daté — le nom d'origine est conservé plutôt qu'une erreur.
        path = self.dir / "sans_date.png"
        Image.new("RGB", (2, 2)).save(path)

        self.assertEqual(ps.dated_filename(path), "sans_date.png")


class TestUniqueDestination(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_returns_same_name_when_free(self):
        self.assertEqual(ps.unique_destination(self.dir, "photo.jpg"), self.dir / "photo.jpg")

    def test_suffixes_on_collision(self):
        (self.dir / "photo.jpg").write_bytes(b"1")
        self.assertEqual(ps.unique_destination(self.dir, "photo.jpg"), self.dir / "photo_1.jpg")

        (self.dir / "photo_1.jpg").write_bytes(b"2")
        self.assertEqual(ps.unique_destination(self.dir, "photo.jpg"), self.dir / "photo_2.jpg")


class TestTransferFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.src_dir = Path(self.tmpdir.name) / "src"
        self.dest_dir = Path(self.tmpdir.name) / "dest"
        self.src_dir.mkdir()
        self.dest_dir.mkdir()

    def _empty_index(self):
        return ps.build_pending_size_index(self.dest_dir), {}

    def test_copier_leaves_source_untouched(self):
        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"contenu")
        pending_by_size, hashed_by_size = self._empty_index()

        result = ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "copier")

        self.assertEqual(result, "copied")
        self.assertTrue(src.exists())
        self.assertTrue((self.dest_dir / "photo.jpg").exists())

    def test_deplacer_removes_source(self):
        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"contenu")
        pending_by_size, hashed_by_size = self._empty_index()

        result = ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "deplacer")

        self.assertEqual(result, "moved")
        self.assertFalse(src.exists())
        self.assertTrue((self.dest_dir / "photo.jpg").exists())

    def test_duplicate_is_not_copied(self):
        (self.dest_dir / "existing.jpg").write_bytes(b"contenu")
        pending_by_size = ps.build_pending_size_index(self.dest_dir)
        hashed_by_size = {}

        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"contenu")  # même taille et même contenu que existing.jpg

        result = ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "copier")

        self.assertEqual(result, "duplicate")
        self.assertTrue(src.exists())
        self.assertEqual(list(self.dest_dir.iterdir()), [self.dest_dir / "existing.jpg"])

    def test_duplicate_is_still_removed_from_source_when_moving(self):
        (self.dest_dir / "existing.jpg").write_bytes(b"contenu")
        pending_by_size = ps.build_pending_size_index(self.dest_dir)
        hashed_by_size = {}

        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"contenu")

        result = ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "deplacer")

        self.assertEqual(result, "duplicate")
        self.assertFalse(src.exists())

    def test_same_size_but_different_content_is_not_a_duplicate(self):
        # Même taille (7 octets) qu'existing.jpg, mais contenu différent : ne doit pas
        # être pris pour un doublon même si le hachage est déclenché par la collision
        # de taille.
        (self.dest_dir / "existing.jpg").write_bytes(b"aaaaaaa")
        pending_by_size = ps.build_pending_size_index(self.dest_dir)
        hashed_by_size = {}

        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"bbbbbbb")

        result = ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "copier")

        self.assertEqual(result, "copied")
        self.assertTrue((self.dest_dir / "photo.jpg").exists())

    def test_never_hashes_files_with_a_unique_size(self):
        # Aucun autre fichier de cette taille : ni le fichier existant, ni le nouveau
        # ne doivent être hachés (hashed_by_size doit rester vide).
        (self.dest_dir / "existing.jpg").write_bytes(b"1")
        pending_by_size = ps.build_pending_size_index(self.dest_dir)
        hashed_by_size = {}

        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"22")  # taille différente (2 octets vs 1)

        result = ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "copier")

        self.assertEqual(result, "copied")
        self.assertEqual(hashed_by_size, {})

    def test_size_collision_alone_does_not_trigger_a_full_hash(self):
        # Régression : une collision de taille ne doit déclencher qu'un hash partiel
        # (bon marché) — le hash complet (coûteux, en particulier pour de grosses
        # vidéos) ne doit intervenir que si le hash partiel coïncide aussi. Simule le
        # cas des dashcams/caméras à segments de taille fixe, où de nombreux fichiers
        # non dupliqués partagent la même taille exacte.
        (self.dest_dir / "existing.jpg").write_bytes(b"aaaaaaa")
        pending_by_size = ps.build_pending_size_index(self.dest_dir)
        hashed_by_size = {}

        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"bbbbbbb")  # même taille (7 octets), contenu différent

        with unittest.mock.patch.object(ps, "file_hash", side_effect=ps.file_hash) as full_hash:
            result = ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "copier")

        self.assertEqual(result, "copied")
        full_hash.assert_not_called()

    def test_detects_duplicate_after_an_earlier_same_size_different_content_file(self):
        # Un premier "faux positif" de taille (contenu différent, donc hash partiel
        # différent) ne doit pas empêcher de détecter un vrai doublon plus tard pour
        # la même taille.
        (self.dest_dir / "existing.jpg").write_bytes(b"contenu-a")  # 9 octets
        pending_by_size = ps.build_pending_size_index(self.dest_dir)
        hashed_by_size = {}

        different_content_src = self.src_dir / "different.jpg"
        different_content_src.write_bytes(b"contenu-b")  # même taille, contenu différent
        result1 = ps.transfer_file(different_content_src, self.dest_dir, pending_by_size, hashed_by_size, "copier")
        self.assertEqual(result1, "copied")

        duplicate_src = self.src_dir / "duplicate.jpg"
        duplicate_src.write_bytes(b"contenu-a")  # même taille ET même contenu que existing.jpg
        result2 = ps.transfer_file(duplicate_src, self.dest_dir, pending_by_size, hashed_by_size, "copier")

        self.assertEqual(result2, "duplicate")

    def test_partial_hash_collision_is_confirmed_or_refuted_by_a_full_hash(self):
        # Deux fichiers peuvent partager leurs premiers/derniers octets sans être
        # identiques : le hash partiel seul ne doit jamais trancher, seul file_hash()
        # (hash complet) fait foi. On réduit ici la fenêtre de hash partiel via un mock
        # pour provoquer une collision partielle sans construire des fichiers de 64 Ko+.
        def small_sample_partial_hash(path, _original=ps.partial_file_hash):
            return _original(path, sample_size=4)

        with unittest.mock.patch.object(ps, "partial_file_hash", side_effect=small_sample_partial_hash):
            (self.dest_dir / "existing.jpg").write_bytes(b"AAAA-real-AAAA")
            pending_by_size = ps.build_pending_size_index(self.dest_dir)
            hashed_by_size = {}

            # Même tête/queue (4 premiers et 4 derniers octets : "AAAA") mais milieu
            # différent : même hash partiel, contenu réellement différent.
            different = self.src_dir / "different.jpg"
            different.write_bytes(b"AAAA-fake-AAAA")
            result_different = ps.transfer_file(different, self.dest_dir, pending_by_size, hashed_by_size, "copier")

            # Réellement identique à existing.jpg : même hash partiel ET même contenu.
            duplicate = self.src_dir / "duplicate.jpg"
            duplicate.write_bytes(b"AAAA-real-AAAA")
            result_duplicate = ps.transfer_file(duplicate, self.dest_dir, pending_by_size, hashed_by_size, "copier")

        self.assertEqual(result_different, "copied")
        self.assertEqual(result_duplicate, "duplicate")

    def test_renames_using_media_date_when_requested(self):
        src = self.src_dir / "IMG_0001.jpg"
        img = Image.new("RGB", (2, 2))
        exif = img.getexif()
        exif.get_ifd(pm.ExifTags.IFD.Exif)[pm.EXIF_DATE_TIME_ORIGINAL] = "2024:08:15 14:30:22"
        img.save(src, exif=exif)
        pending_by_size, hashed_by_size = self._empty_index()

        result = ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "copier", rename_files=True)

        self.assertEqual(result, "copied")
        self.assertTrue((self.dest_dir / "2024-08-15_143022.jpg").exists())
        self.assertFalse((self.dest_dir / "IMG_0001.jpg").exists())

    def test_rename_falls_back_to_original_name_without_date(self):
        src = self.src_dir / "sans_date.png"
        Image.new("RGB", (2, 2)).save(src)
        pending_by_size, hashed_by_size = self._empty_index()

        result = ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "copier", rename_files=True)

        self.assertEqual(result, "copied")
        self.assertTrue((self.dest_dir / "sans_date.png").exists())

    def test_rename_still_detects_duplicates_by_content(self):
        # Le renommage ne doit pas contourner la déduplication : elle porte sur le
        # contenu (taille puis hash), jamais sur le nom du fichier.
        (self.dest_dir / "existing.jpg").write_bytes(b"contenu-dup")
        pending_by_size = ps.build_pending_size_index(self.dest_dir)
        hashed_by_size = {}
        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"contenu-dup")

        result = ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "copier", rename_files=True)

        self.assertEqual(result, "duplicate")

    def test_default_does_not_rename(self):
        src = self.src_dir / "IMG_0001.jpg"
        img = Image.new("RGB", (2, 2))
        exif = img.getexif()
        exif.get_ifd(pm.ExifTags.IFD.Exif)[pm.EXIF_DATE_TIME_ORIGINAL] = "2024:08:15 14:30:22"
        img.save(src, exif=exif)
        pending_by_size, hashed_by_size = self._empty_index()

        ps.transfer_file(src, self.dest_dir, pending_by_size, hashed_by_size, "copier")

        self.assertTrue((self.dest_dir / "IMG_0001.jpg").exists())


class TestCopyFiles(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.src_dir = Path(self.tmpdir.name) / "src"
        self.dest_dir = Path(self.tmpdir.name) / "dest"
        self.src_dir.mkdir()
        self.dest_dir.mkdir()

    def _make_src_files(self, names):
        paths = []
        for name in names:
            p = self.src_dir / name
            p.write_bytes(f"contenu-{name}".encode())
            paths.append(p)
        return paths

    def test_copies_into_the_right_destination_subfolders(self):
        a, b = self._make_src_files(["a.jpg", "b.jpg"])
        c = self._make_src_files(["c.jpg"])[0]
        destination_map = {("2024", "01"): [a, b], ("2023", "12"): [c]}

        done, duplicates, errors = ps.copy_files(destination_map, self.dest_dir, "copier")

        self.assertEqual((done, duplicates, errors), (3, 0, []))
        self.assertTrue((self.dest_dir / "2024" / "01" / "a.jpg").exists())
        self.assertTrue((self.dest_dir / "2024" / "01" / "b.jpg").exists())
        self.assertTrue((self.dest_dir / "2023" / "12" / "c.jpg").exists())
        self.assertTrue(a.exists())  # mode copier : la source n'est pas touchée

    def test_deplacer_removes_sources(self):
        a = self._make_src_files(["a.jpg"])[0]
        destination_map = {("2024",): [a]}

        done, duplicates, errors = ps.copy_files(destination_map, self.dest_dir, "deplacer")

        self.assertEqual((done, duplicates, errors), (1, 0, []))
        self.assertFalse(a.exists())
        self.assertTrue((self.dest_dir / "2024" / "a.jpg").exists())

    def test_counts_duplicates_without_copying_them(self):
        (self.dest_dir / "2024").mkdir()
        (self.dest_dir / "2024" / "existing.jpg").write_bytes(b"contenu-dup")
        dup = self.src_dir / "dup.jpg"
        dup.write_bytes(b"contenu-dup")
        new = self.src_dir / "new.jpg"
        new.write_bytes(b"contenu-nouveau")
        destination_map = {("2024",): [dup, new]}

        done, duplicates, errors = ps.copy_files(destination_map, self.dest_dir, "copier")

        self.assertEqual((done, duplicates, errors), (2, 1, []))
        self.assertEqual(sorted(p.name for p in (self.dest_dir / "2024").iterdir()), ["existing.jpg", "new.jpg"])

    def test_mkdir_failure_records_one_error_per_file_and_keeps_counting(self):
        # Régression : mkdir() qui échoue ne doit pas abandonner silencieusement tout un
        # dossier avec une seule erreur générique — chaque fichier concerné doit être
        # compté (voir _copy_worker avant correctif).
        blocked = self.dest_dir / "blocked"
        blocked.write_bytes(b"un fichier bloque la creation du dossier de meme nom")
        files = self._make_src_files(["a.jpg", "b.jpg", "c.jpg"])
        destination_map = {("blocked",): files}

        done, duplicates, errors = ps.copy_files(destination_map, self.dest_dir, "copier")

        self.assertEqual(done, 3)
        self.assertEqual(duplicates, 0)
        self.assertEqual(len(errors), 3)

    def test_index_failure_records_errors_for_that_folder_but_keeps_processing_others(self):
        # Régression : mkdir() peut réussir puis lister le dossier échouer juste après
        # (partage réseau débranché entre les deux, permissions changées) — ce dossier ne
        # doit pas faire échouer toute la copie, seuls ses fichiers sont comptés en erreur.
        broken_files = self._make_src_files(["a.jpg", "b.jpg"])
        ok_files = self._make_src_files(["c.jpg"])
        destination_map = {("broken",): broken_files, ("ok",): ok_files}

        original = ps.build_pending_size_index

        def fail_for_broken_only(target_dir):
            if target_dir.name == "broken":
                raise OSError("partage réseau inaccessible")
            return original(target_dir)

        with unittest.mock.patch.object(ps, "build_pending_size_index", side_effect=fail_for_broken_only):
            done, duplicates, errors = ps.copy_files(destination_map, self.dest_dir, "copier")

        self.assertEqual(done, 3)
        self.assertEqual(duplicates, 0)
        self.assertEqual(len(errors), 2)
        self.assertEqual([p.name for p in (self.dest_dir / "ok").iterdir()], ["c.jpg"])
        self.assertFalse((self.dest_dir / "broken").exists() and any((self.dest_dir / "broken").iterdir()))

    def test_copy_failure_during_transfer_records_error_and_keeps_processing(self):
        # test_mkdir_failure... et test_index_failure... couvrent l'échec de mkdir() et
        # de l'indexation du dossier de destination, mais pas un échec du transfert
        # lui-même (shutil.copy2, disque plein/permissions refusées) une fois ces deux
        # étapes réussies — c'est le except autour de transfer_file() dans copy_files()
        # qui doit alors intervenir.
        a, b, c = self._make_src_files(["a.jpg", "b.jpg", "c.jpg"])
        destination_map = {("2024",): [a, b, c]}

        original_copy2 = shutil.copy2

        def fail_for_b_only(src, dst, *args, **kwargs):
            if Path(src) == b:
                raise OSError("disque plein")
            return original_copy2(src, dst, *args, **kwargs)

        with unittest.mock.patch.object(ps.shutil, "copy2", side_effect=fail_for_b_only):
            done, duplicates, errors = ps.copy_files(destination_map, self.dest_dir, "copier")

        self.assertEqual(done, 3)
        self.assertEqual(duplicates, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn(str(b), errors[0])
        self.assertTrue((self.dest_dir / "2024" / "a.jpg").exists())
        self.assertTrue((self.dest_dir / "2024" / "c.jpg").exists())
        self.assertFalse((self.dest_dir / "2024" / "b.jpg").exists())
        self.assertTrue(b.exists())  # échec du transfert : la source n'est pas touchée

    def test_move_failure_during_transfer_records_error_and_keeps_source(self):
        # Même défaut de couverture que ci-dessus, côté "deplacer" (shutil.move) : un
        # échec ne doit pas non plus supprimer la source ni interrompre le reste du lot.
        a, b = self._make_src_files(["a.jpg", "b.jpg"])
        destination_map = {("2024",): [a, b]}

        with unittest.mock.patch.object(ps.shutil, "move", side_effect=OSError("permission refusee")):
            done, duplicates, errors = ps.copy_files(destination_map, self.dest_dir, "deplacer")

        self.assertEqual(done, 2)
        self.assertEqual(duplicates, 0)
        self.assertEqual(len(errors), 2)
        self.assertTrue(a.exists())
        self.assertTrue(b.exists())
        self.assertFalse((self.dest_dir / "2024" / "a.jpg").exists())
        self.assertFalse((self.dest_dir / "2024" / "b.jpg").exists())

    def test_on_progress_called_once_per_file_with_running_total(self):
        files = self._make_src_files(["a.jpg", "b.jpg", "c.jpg"])
        destination_map = {("2024",): files}
        seen = []

        ps.copy_files(destination_map, self.dest_dir, "copier", on_progress=seen.append)

        self.assertEqual(seen, [1, 2, 3])

    def test_raises_copy_cancelled_with_partial_progress(self):
        files = self._make_src_files(["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
        destination_map = {("2024",): files}
        cancel_event = threading.Event()

        def on_progress(done):
            if done == 2:
                cancel_event.set()

        with self.assertRaises(ps.CopyCancelled) as ctx:
            ps.copy_files(destination_map, self.dest_dir, "copier", cancel_event=cancel_event, on_progress=on_progress)

        done, duplicates, errors = ctx.exception.args
        self.assertEqual(done, 2)
        self.assertEqual(duplicates, 0)
        self.assertEqual(errors, [])
        # Seuls les 2 premiers fichiers (dans l'ordre d'insertion du dict) ont été copiés.
        self.assertTrue((self.dest_dir / "2024" / "a.jpg").exists())
        self.assertTrue((self.dest_dir / "2024" / "b.jpg").exists())
        self.assertFalse((self.dest_dir / "2024" / "c.jpg").exists())
        self.assertFalse((self.dest_dir / "2024" / "d.jpg").exists())

    def test_cancel_event_not_set_runs_to_completion(self):
        files = self._make_src_files(["a.jpg", "b.jpg"])
        destination_map = {("2024",): files}

        done, duplicates, errors = ps.copy_files(
            destination_map, self.dest_dir, "copier", cancel_event=threading.Event()
        )

        self.assertEqual((done, duplicates, errors), (2, 0, []))

    def test_forwards_rename_files_to_transfer_file(self):
        a = self._make_src_files(["a.jpg"])[0]
        destination_map = {("2024",): [a]}

        with unittest.mock.patch.object(ps, "transfer_file", wraps=ps.transfer_file) as mock_transfer:
            ps.copy_files(destination_map, self.dest_dir, "copier", rename_files=True)

        # pending_by_size/hashed_by_size (args 3 et 4) sont mutés en place par
        # transfer_file : les comparer après coup ne refléterait plus leur état au
        # moment de l'appel, seuls la source, la cible, le mode et rename_files le sont.
        mock_transfer.assert_called_once()
        args = mock_transfer.call_args.args
        self.assertEqual((args[0], args[1], args[4], args[5]), (a, self.dest_dir / "2024", "copier", True))

    def test_renames_files_end_to_end_when_requested(self):
        photo = self.src_dir / "IMG_0001.jpg"
        img = Image.new("RGB", (2, 2))
        exif = img.getexif()
        exif.get_ifd(pm.ExifTags.IFD.Exif)[pm.EXIF_DATE_TIME_ORIGINAL] = "2024:08:15 14:30:22"
        img.save(photo, exif=exif)
        destination_map = {("2024", "08"): [photo]}

        done, duplicates, errors = ps.copy_files(destination_map, self.dest_dir, "copier", rename_files=True)

        self.assertEqual((done, duplicates, errors), (1, 0, []))
        self.assertTrue((self.dest_dir / "2024" / "08" / "2024-08-15_143022.jpg").exists())


class TestAggregateTree(unittest.TestCase):
    def setUp(self):
        self.tree = {
            "2024": {"01": {"15": ["a", "b"]}, "02": {"01": ["c"]}},
            "2023": {"12": {"25": ["d"]}},
        }

    def test_level_annee_merges_everything_under_year(self):
        result = ps.aggregate_tree(self.tree, "annee")

        self.assertEqual(sorted(result["2024"]), ["a", "b", "c"])
        self.assertEqual(result["2023"], ["d"])

    def test_level_mois_merges_days_under_month(self):
        result = ps.aggregate_tree(self.tree, "mois")

        self.assertEqual(sorted(result["2024"]["01"]), ["a", "b"])
        self.assertEqual(result["2024"]["02"], ["c"])
        self.assertEqual(result["2023"]["12"], ["d"])

    def test_level_jour_keeps_full_depth(self):
        result = ps.aggregate_tree(self.tree, "jour")

        self.assertEqual(result["2024"]["01"]["15"], ["a", "b"])
        self.assertEqual(result["2024"]["02"]["01"], ["c"])
        self.assertEqual(result["2023"]["12"]["25"], ["d"])

    def test_total_count_is_unchanged_by_level(self):
        for level in ("annee", "mois", "jour"):
            self.assertEqual(ps.count_files(ps.aggregate_tree(self.tree, level)), 4)

    def test_no_info_stays_a_flat_list_at_every_level(self):
        tree = {**self.tree, ps.NO_INFO_LABEL: ["e"]}

        for level in ("annee", "mois", "jour"):
            result = ps.aggregate_tree(tree, level)
            self.assertEqual(result[ps.NO_INFO_LABEL], ["e"])


class TestExplainNoInfo(unittest.TestCase):
    def test_gif_and_bmp_are_unsupported_format(self):
        self.assertEqual(ps.explain_no_info(Path("a.gif")), ps.NO_INFO_REASON_UNSUPPORTED_FORMAT)
        self.assertEqual(ps.explain_no_info(Path("a.bmp")), ps.NO_INFO_REASON_UNSUPPORTED_FORMAT)

    def test_mpg_and_mpeg_are_unsupported_format(self):
        self.assertEqual(ps.explain_no_info(Path("a.mpg")), ps.NO_INFO_REASON_UNSUPPORTED_FORMAT)
        self.assertEqual(ps.explain_no_info(Path("a.mpeg")), ps.NO_INFO_REASON_UNSUPPORTED_FORMAT)

    def test_heic_without_plugin_flags_the_missing_plugin(self):
        with unittest.mock.patch.object(ps, "HEIF_SUPPORTED", False):
            self.assertEqual(ps.explain_no_info(Path("a.heic")), ps.NO_INFO_REASON_HEIF_PLUGIN_MISSING)
            self.assertEqual(ps.explain_no_info(Path("a.heif")), ps.NO_INFO_REASON_HEIF_PLUGIN_MISSING)

    def test_heic_with_plugin_falls_back_to_generic_reason(self):
        # Le plugin est actif : rien de plus à dire que pour n'importe quel autre format
        # pris en charge sans date exploitable sur ce fichier précis.
        with unittest.mock.patch.object(ps, "HEIF_SUPPORTED", True):
            self.assertEqual(ps.explain_no_info(Path("a.heic")), ps.NO_INFO_REASON_NO_USABLE_DATE)

    def test_other_supported_formats_get_the_generic_reason(self):
        self.assertEqual(ps.explain_no_info(Path("a.jpg")), ps.NO_INFO_REASON_NO_USABLE_DATE)
        self.assertEqual(ps.explain_no_info(Path("a.mp4")), ps.NO_INFO_REASON_NO_USABLE_DATE)

    def test_extension_matching_is_case_insensitive(self):
        self.assertEqual(ps.explain_no_info(Path("A.GIF")), ps.NO_INFO_REASON_UNSUPPORTED_FORMAT)


class TestGroupNoInfoByReason(unittest.TestCase):
    def test_groups_flat_no_info_list_by_reason(self):
        node = {"2024": {"01": ["a"]}, ps.NO_INFO_LABEL: [Path("a.gif"), Path("b.mpg"), Path("c.jpg")]}

        result = ps.group_no_info_by_reason(node)

        self.assertEqual(result["2024"], {"01": ["a"]})  # branches sans NO_INFO_LABEL inchangées
        self.assertEqual(
            sorted(result[ps.NO_INFO_LABEL][ps.NO_INFO_REASON_UNSUPPORTED_FORMAT], key=str),
            sorted([Path("a.gif"), Path("b.mpg")], key=str),
        )
        self.assertEqual(result[ps.NO_INFO_LABEL][ps.NO_INFO_REASON_NO_USABLE_DATE], [Path("c.jpg")])

    def test_recurses_into_nested_dicts(self):
        node = {"Photos": {ps.NO_INFO_LABEL: [Path("a.gif")]}, "Vidéos": {"2024": ["v"]}}

        result = ps.group_no_info_by_reason(node)

        self.assertEqual(
            result["Photos"][ps.NO_INFO_LABEL][ps.NO_INFO_REASON_UNSUPPORTED_FORMAT], [Path("a.gif")]
        )
        self.assertEqual(result["Vidéos"], {"2024": ["v"]})

    def test_is_a_noop_without_no_info(self):
        node = {"2024": {"01": {"15": ["a", "b"]}}}

        self.assertEqual(ps.group_no_info_by_reason(node), node)


class TestMergeMediaTrees(unittest.TestCase):
    def test_merges_photos_and_videos_into_one_tree(self):
        tree = {
            "photos": {"2024": {"01": {"15": ["photo.jpg"]}}},
            "videos": {"2024": {"01": {"15": ["video.mp4"]}}},
        }

        merged = ps.merge_media_trees(tree)

        self.assertEqual(sorted(merged["2024"]["01"]["15"]), ["photo.jpg", "video.mp4"])

    def test_merges_no_info_files_from_both_categories(self):
        tree = {
            "photos": {ps.NO_INFO_LABEL: ["photo.jpg"]},
            "videos": {ps.NO_INFO_LABEL: ["video.mp4"]},
        }

        merged = ps.merge_media_trees(tree)

        self.assertEqual(sorted(merged[ps.NO_INFO_LABEL]), ["photo.jpg", "video.mp4"])


class TestBuildDisplayTree(unittest.TestCase):
    def setUp(self):
        self.tree = {
            "photos": {"2024": {"01": {"15": ["photo.jpg"]}}},
            "videos": {"2024": {"01": {"15": ["video.mp4"]}}},
        }

    def test_mixed_when_not_separated(self):
        display = ps.build_display_tree(self.tree, "jour", separate_media=False)

        self.assertEqual(sorted(display["2024"]["01"]["15"]), ["photo.jpg", "video.mp4"])

    def test_split_by_category_when_separated(self):
        display = ps.build_display_tree(self.tree, "jour", separate_media=True)

        self.assertEqual(set(display.keys()), {"Photos", "Vidéos"})
        self.assertEqual(display["Photos"]["2024"]["01"]["15"], ["photo.jpg"])
        self.assertEqual(display["Vidéos"]["2024"]["01"]["15"], ["video.mp4"])

    def test_empty_category_omitted_when_separated(self):
        tree = {"photos": {"2024": {"01": {"15": ["photo.jpg"]}}}, "videos": {}}

        display = ps.build_display_tree(tree, "jour", separate_media=True)

        self.assertEqual(set(display.keys()), {"Photos"})

    def test_groups_no_info_files_by_reason_when_not_separated(self):
        tree = {
            "photos": {ps.NO_INFO_LABEL: [Path("a.gif"), Path("b.jpg")]},
            "videos": {},
        }

        display = ps.build_display_tree(tree, "jour", separate_media=False)

        self.assertEqual(
            display[ps.NO_INFO_LABEL][ps.NO_INFO_REASON_UNSUPPORTED_FORMAT], [Path("a.gif")]
        )
        self.assertEqual(
            display[ps.NO_INFO_LABEL][ps.NO_INFO_REASON_NO_USABLE_DATE], [Path("b.jpg")]
        )

    def test_groups_no_info_files_by_reason_when_separated(self):
        tree = {
            "photos": {ps.NO_INFO_LABEL: [Path("a.gif")]},
            "videos": {},
        }

        display = ps.build_display_tree(tree, "jour", separate_media=True)

        self.assertEqual(
            display["Photos"][ps.NO_INFO_LABEL][ps.NO_INFO_REASON_UNSUPPORTED_FORMAT],
            [Path("a.gif")],
        )


class TestBuildDestinationMap(unittest.TestCase):
    def setUp(self):
        self.tree = {
            "photos": {"2024": {"08": {"15": ["photo.jpg"]}}},
            "videos": {"2024": {"08": {"15": ["video.mp4"]}}},
        }

    def test_mixed_paths_when_not_separated(self):
        dest_map = ps.build_destination_map(self.tree, "jour", separate_media=False)

        self.assertEqual(
            sorted(dest_map[("2024", "08-Août", "15")]),
            ["photo.jpg", "video.mp4"],
        )

    def test_category_prefixes_path_when_separated(self):
        dest_map = ps.build_destination_map(self.tree, "jour", separate_media=True)

        self.assertEqual(dest_map[("Photos", "2024", "08-Août", "15")], ["photo.jpg"])
        self.assertEqual(dest_map[("Vidéos", "2024", "08-Août", "15")], ["video.mp4"])

    def test_respects_sort_level_when_separated(self):
        dest_map = ps.build_destination_map(self.tree, "annee", separate_media=True)

        self.assertEqual(dest_map[("Photos", "2024")], ["photo.jpg"])
        self.assertEqual(dest_map[("Vidéos", "2024")], ["video.mp4"])

    def test_no_info_files_go_to_their_own_folder_regardless_of_sort_level(self):
        tree = {
            "photos": {ps.NO_INFO_LABEL: ["photo.jpg"]},
            "videos": {ps.NO_INFO_LABEL: ["video.mp4"]},
        }

        for level in ("annee", "mois", "jour"):
            dest_map = ps.build_destination_map(tree, level, separate_media=False)
            self.assertEqual(sorted(dest_map[(ps.NO_INFO_LABEL,)]), ["photo.jpg", "video.mp4"])

    def test_no_info_files_nested_under_category_when_separated(self):
        tree = {
            "photos": {ps.NO_INFO_LABEL: ["photo.jpg"]},
            "videos": {},
        }

        dest_map = ps.build_destination_map(tree, "jour", separate_media=True)

        self.assertEqual(dest_map[("Photos", ps.NO_INFO_LABEL)], ["photo.jpg"])


class TestFlattenTree(unittest.TestCase):
    def test_flattens_leaves_with_their_key_path(self):
        tree = {"2024": {"01": ["a", "b"]}, "2023": {"12": ["c"]}}

        result = dict(ps.flatten_tree(tree))

        self.assertEqual(result[("2023", "12")], ["c"])
        self.assertEqual(result[("2024", "01")], ["a", "b"])

    def test_flattens_shallow_tree(self):
        tree = {"2024": ["a"], "2023": ["b"]}

        result = dict(ps.flatten_tree(tree))

        self.assertEqual(result[("2024",)], ["a"])
        self.assertEqual(result[("2023",)], ["b"])


class TestFormatDuration(unittest.TestCase):
    def test_shows_milliseconds_below_one_second(self):
        self.assertEqual(ps.format_duration(0.123), "123 ms")
        self.assertEqual(ps.format_duration(0.0), "0 ms")

    def test_shows_seconds_with_one_decimal_from_one_second(self):
        self.assertEqual(ps.format_duration(1.0), "1.0 s")
        self.assertEqual(ps.format_duration(12.34), "12.3 s")


class TestMonthFolderName(unittest.TestCase):
    def test_prefixes_month_with_its_french_name(self):
        self.assertEqual(ps.month_folder_name("01"), "01-Janvier")
        self.assertEqual(ps.month_folder_name("08"), "08-Août")

    def test_unknown_month_falls_back_to_raw_value(self):
        self.assertEqual(ps.month_folder_name("13"), "13-13")


class TestPathPartsToFolderNames(unittest.TestCase):
    def test_dresses_up_month_component_when_present(self):
        self.assertEqual(
            ps.path_parts_to_folder_names(("2024", "08", "15")),
            ["2024", "08-Août", "15"],
        )
        self.assertEqual(
            ps.path_parts_to_folder_names(("2024", "01")),
            ["2024", "01-Janvier"],
        )

    def test_leaves_year_only_path_untouched(self):
        self.assertEqual(ps.path_parts_to_folder_names(("2024",)), ["2024"])


if __name__ == "__main__":
    unittest.main()
