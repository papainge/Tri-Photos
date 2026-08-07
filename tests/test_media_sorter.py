import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image

import media_sorter as ps
import photo_metadata as pm
import video_metadata as vm

# Le détail par format (EXIF, MP4/MOV, AVI, WMV, MKV/WEBM...) est couvert dans
# tests/test_photo_metadata.py et tests/test_video_metadata.py. Ici, on vérifie
# seulement que get_media_date délègue correctement selon l'extension et retombe sur
# la date de modification quand aucun module ne trouve de métadonnée.


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

    def test_falls_back_to_mtime_when_photo_has_no_exif(self):
        path = self.dir / "photo.png"
        Image.new("RGB", (2, 2), color="blue").save(path)
        expected = datetime(2019, 3, 4, 8, 15, 0)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

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

    def test_falls_back_to_mtime_when_video_metadata_is_absent(self):
        # MPG/MPEG n'est géré par aucun des parseurs vidéo (voir test_video_metadata.py).
        path = self.dir / "video.mpg"
        path.write_bytes(b"\x00\x00\x01\xba" + b"\x00" * 20)
        expected = datetime(2020, 7, 1, 10, 0, 0)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)


class TestScanMedia(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def _make_png(self, relative_path, date):
        path = self.dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2, 2)).save(path)
        os.utime(path, (date.timestamp(), date.timestamp()))
        return path

    def _make_file(self, relative_path, date):
        path = self.dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"contenu")
        os.utime(path, (date.timestamp(), date.timestamp()))
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
        video = self._make_file("b.mp4", datetime(2024, 1, 15))

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

    def test_hashes_lazily_only_once_a_size_collision_occurs(self):
        (self.dest_dir / "existing.jpg").write_bytes(b"contenu-a")  # 9 octets
        pending_by_size = ps.build_pending_size_index(self.dest_dir)
        hashed_by_size = {}

        # Une deuxième source de taille différente : toujours aucun hachage.
        other_size_src = self.src_dir / "other_size.jpg"
        other_size_src.write_bytes(b"court")
        ps.transfer_file(other_size_src, self.dest_dir, pending_by_size, hashed_by_size, "copier")
        self.assertEqual(hashed_by_size, {})

        # Une source de même taille (9 octets) que existing.jpg déclenche enfin le hachage
        # des deux (l'existant, désormais promu, et la nouvelle source).
        same_size_src = self.src_dir / "same_size.jpg"
        same_size_src.write_bytes(b"contenu-b")
        result = ps.transfer_file(same_size_src, self.dest_dir, pending_by_size, hashed_by_size, "copier")

        self.assertEqual(result, "copied")
        self.assertEqual(len(hashed_by_size.get(9, set())), 2)


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


class TestMergeMediaTrees(unittest.TestCase):
    def test_merges_photos_and_videos_into_one_tree(self):
        tree = {
            "photos": {"2024": {"01": {"15": ["photo.jpg"]}}},
            "videos": {"2024": {"01": {"15": ["video.mp4"]}}},
        }

        merged = ps.merge_media_trees(tree)

        self.assertEqual(sorted(merged["2024"]["01"]["15"]), ["photo.jpg", "video.mp4"])


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
