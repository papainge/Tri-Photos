import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

import photo_sorter as ps


class TestGetPhotoDate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_falls_back_to_mtime_when_no_exif(self):
        path = self.dir / "photo.png"
        Image.new("RGB", (2, 2), color="blue").save(path)
        expected = datetime(2019, 3, 4, 8, 15, 0)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertEqual(ps.get_photo_date(path), expected)

    def test_uses_exif_date_when_present(self):
        path = self.dir / "photo.jpg"
        img = Image.new("RGB", (2, 2), color="red")
        exif = img.getexif()
        exif[ps.DATE_TAG_ID] = "2020:05:17 10:30:00"
        img.save(path, exif=exif)

        # La date de modification ne doit pas être utilisée si l'EXIF est présente.
        wrong = datetime(1999, 1, 1)
        os.utime(path, (wrong.timestamp(), wrong.timestamp()))

        self.assertEqual(ps.get_photo_date(path), datetime(2020, 5, 17, 10, 30, 0))


class TestScanPhotos(unittest.TestCase):
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

    def test_groups_by_year_month_day(self):
        p1 = self._make_png("a.png", datetime(2024, 1, 15))
        p2 = self._make_png("sub/b.png", datetime(2024, 1, 15))
        p3 = self._make_png("c.png", datetime(2024, 2, 1))
        p4 = self._make_png("d.png", datetime(2023, 12, 25))

        tree = ps.scan_photos(self.dir)

        self.assertEqual(set(tree.keys()), {"2024", "2023"})
        self.assertEqual(sorted(tree["2024"]["01"]["15"]), sorted([p1, p2]))
        self.assertEqual(tree["2024"]["02"]["01"], [p3])
        self.assertEqual(tree["2023"]["12"]["25"], [p4])

    def test_ignores_non_image_files(self):
        self._make_png("a.png", datetime(2024, 1, 1))
        (self.dir / "notes.txt").write_text("pas une photo")

        tree = ps.scan_photos(self.dir)

        self.assertEqual(ps.count_files(tree), 1)

    def test_recurses_into_deeply_nested_subfolders(self):
        self._make_png("a.png", datetime(2024, 1, 1))
        self._make_png("n1/b.png", datetime(2024, 1, 1))
        self._make_png("n1/n2/c.png", datetime(2024, 1, 1))
        self._make_png("n1/n2/n3/d.png", datetime(2024, 1, 1))

        tree = ps.scan_photos(self.dir)

        self.assertEqual(ps.count_files(tree), 4)


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

    def test_copier_leaves_source_untouched(self):
        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"contenu")

        result = ps.transfer_file(src, self.dest_dir, set(), "copier")

        self.assertEqual(result, "copied")
        self.assertTrue(src.exists())
        self.assertTrue((self.dest_dir / "photo.jpg").exists())

    def test_deplacer_removes_source(self):
        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"contenu")

        result = ps.transfer_file(src, self.dest_dir, set(), "deplacer")

        self.assertEqual(result, "moved")
        self.assertFalse(src.exists())
        self.assertTrue((self.dest_dir / "photo.jpg").exists())

    def test_duplicate_is_not_copied(self):
        (self.dest_dir / "existing.jpg").write_bytes(b"contenu")
        existing_hashes = {ps.file_hash(self.dest_dir / "existing.jpg")}

        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"contenu")

        result = ps.transfer_file(src, self.dest_dir, existing_hashes, "copier")

        self.assertEqual(result, "duplicate")
        self.assertTrue(src.exists())
        self.assertEqual(list(self.dest_dir.iterdir()), [self.dest_dir / "existing.jpg"])

    def test_duplicate_is_still_removed_from_source_when_moving(self):
        (self.dest_dir / "existing.jpg").write_bytes(b"contenu")
        existing_hashes = {ps.file_hash(self.dest_dir / "existing.jpg")}

        src = self.src_dir / "photo.jpg"
        src.write_bytes(b"contenu")

        result = ps.transfer_file(src, self.dest_dir, existing_hashes, "deplacer")

        self.assertEqual(result, "duplicate")
        self.assertFalse(src.exists())
        self.assertEqual(list(self.dest_dir.iterdir()), [self.dest_dir / "existing.jpg"])


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
