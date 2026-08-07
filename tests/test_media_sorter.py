import os
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import ExifTags, Image

import media_sorter as ps


class TestGetMediaDate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_falls_back_to_mtime_when_no_exif(self):
        path = self.dir / "photo.png"
        Image.new("RGB", (2, 2), color="blue").save(path)
        expected = datetime(2019, 3, 4, 8, 15, 0)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def test_uses_exif_date_from_png(self):
        # Le chunk "eXIf" du PNG est un format d'image longtemps non couvert : on
        # vérifie que l'élargissement à IMAGE_EXTENSIONS fonctionne bien pour lui aussi.
        path = self.dir / "photo.png"
        img = Image.new("RGB", (2, 2), color="red")
        exif = img.getexif()
        exif.get_ifd(ExifTags.IFD.Exif)[ps.EXIF_DATE_TIME_ORIGINAL] = "2021:09:09 09:09:09"
        img.save(path, exif=exif.tobytes())

        wrong = datetime(1999, 1, 1)
        os.utime(path, (wrong.timestamp(), wrong.timestamp()))

        self.assertEqual(ps.get_media_date(path), datetime(2021, 9, 9, 9, 9, 9))

    def test_formats_without_exif_support_fall_back_to_mtime(self):
        # GIF et BMP n'ont pas de mécanisme EXIF : getexif()/get_ifd() ne doit ni
        # planter, ni renvoyer de fausse date, sur ces formats.
        for fmt, ext in (("GIF", ".gif"), ("BMP", ".bmp")):
            path = self.dir / f"photo{ext}"
            Image.new("RGB", (2, 2), color="green").save(path, format=fmt)
            expected = datetime(2016, 4, 4, 4, 4, 4)
            os.utime(path, (expected.timestamp(), expected.timestamp()))

            self.assertEqual(ps.get_media_date(path), expected)

    def _save_with_exif_date(self, path, tag_id, value, in_sub_ifd):
        img = Image.new("RGB", (2, 2), color="red")
        exif = img.getexif()
        if in_sub_ifd:
            exif.get_ifd(ExifTags.IFD.Exif)[tag_id] = value
        else:
            exif[tag_id] = value
        img.save(path, exif=exif)

        # La date de modification ne doit pas être utilisée si l'EXIF est présente.
        wrong = datetime(1999, 1, 1)
        os.utime(path, (wrong.timestamp(), wrong.timestamp()))

    def test_uses_exif_date_time_original_from_sub_ifd(self):
        # C'est là qu'un vrai appareil photo range DateTimeOriginal : pas dans l'IFD0
        # renvoyé directement par getexif(), mais dans le sous-IFD Exif.
        path = self.dir / "photo.jpg"
        self._save_with_exif_date(path, ps.EXIF_DATE_TIME_ORIGINAL, "2020:05:17 10:30:00", in_sub_ifd=True)

        self.assertEqual(ps.get_media_date(path), datetime(2020, 5, 17, 10, 30, 0))

    def test_falls_back_to_date_time_digitized(self):
        path = self.dir / "photo.jpg"
        self._save_with_exif_date(path, ps.EXIF_DATE_TIME_DIGITIZED, "2018:02:03 09:00:00", in_sub_ifd=True)

        self.assertEqual(ps.get_media_date(path), datetime(2018, 2, 3, 9, 0, 0))

    def test_falls_back_to_date_time_when_no_original_or_digitized(self):
        path = self.dir / "photo.jpg"
        self._save_with_exif_date(path, ps.EXIF_DATE_TIME, "2017:11:20 14:00:00", in_sub_ifd=False)

        self.assertEqual(ps.get_media_date(path), datetime(2017, 11, 20, 14, 0, 0))

    def _write_fake_mp4(self, path, creation_time, version=0, box_type="mvhd"):
        if version == 1:
            mvhd_content = bytes([1]) + b"\x00\x00\x00" + struct.pack(">QQI", creation_time, 0, 600) + struct.pack(">Q", 0)
        else:
            mvhd_content = bytes([0]) + b"\x00\x00\x00" + struct.pack(">III", creation_time, 0, 600) + struct.pack(">I", 0)
        mvhd = struct.pack(">I", 8 + len(mvhd_content)) + box_type.encode("ascii") + mvhd_content
        moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
        ftyp_content = b"isom" + struct.pack(">I", 0) + b"isomiso2mp41"
        ftyp = struct.pack(">I", 8 + len(ftyp_content)) + b"ftyp" + ftyp_content
        path.write_bytes(ftyp + moov)

    def test_uses_mp4_creation_date_version_0(self):
        path = self.dir / "video.mp4"
        expected = datetime(2023, 6, 15, 12, 0, 0)
        creation_time = int((expected - ps.MP4_EPOCH).total_seconds())
        self._write_fake_mp4(path, creation_time, version=0)

        wrong = datetime(1999, 1, 1)
        os.utime(path, (wrong.timestamp(), wrong.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def test_uses_mp4_creation_date_version_1_64bit(self):
        path = self.dir / "video.mov"
        expected = datetime(2021, 2, 3, 4, 5, 6)
        creation_time = int((expected - ps.MP4_EPOCH).total_seconds())
        self._write_fake_mp4(path, creation_time, version=1)

        self.assertEqual(ps.get_media_date(path), expected)

    def test_mp4_falls_back_to_mtime_when_creation_time_is_zero(self):
        path = self.dir / "video.mp4"
        self._write_fake_mp4(path, creation_time=0)

        expected = datetime(2019, 3, 4, 8, 15, 0)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def test_malformed_avi_falls_back_to_mtime(self):
        path = self.dir / "video.avi"
        path.write_bytes(b"RIFF....AVI LIST....")

        expected = datetime(2020, 7, 1, 10, 0, 0)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def test_unsupported_video_format_falls_back_to_mtime(self):
        # MPG/MPEG n'a pas d'équivalent standardisé aux atomes moov/mvhd (MP4), à IDIT
        # (AVI), au File Properties Object (WMV) ou à DateUTC (Matroska).
        path = self.dir / "video.mpg"
        path.write_bytes(b"\x00\x00\x01\xba" + b"\x00" * 20)

        expected = datetime(2020, 7, 1, 10, 0, 0)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def _write_fake_avi(self, path, idit_text=None):
        def chunk(fourcc, data):
            header = struct.pack("<4sI", fourcc, len(data))
            return header + data + (b"\x00" if len(data) % 2 else b"")

        if idit_text is not None:
            idit_data = idit_text.encode("ascii") + b"\x00"
            info_list_content = b"INFO" + chunk(b"IDIT", idit_data)
        else:
            info_list_content = b"INFO" + chunk(b"ICMT", b"sans IDIT")
        riff_content = b"AVI " + chunk(b"LIST", info_list_content)
        path.write_bytes(chunk(b"RIFF", riff_content))

    def test_uses_avi_idit_date(self):
        path = self.dir / "video.avi"
        expected = datetime(2022, 6, 15, 12, 0, 0)
        self._write_fake_avi(path, idit_text=expected.strftime("%a %b %d %H:%M:%S %Y"))

        wrong = datetime(1999, 1, 1)
        os.utime(path, (wrong.timestamp(), wrong.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def test_avi_info_list_without_idit_falls_back_to_mtime(self):
        path = self.dir / "video.avi"
        self._write_fake_avi(path, idit_text=None)

        expected = datetime(2020, 1, 1)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def _write_fake_wmv(self, path, creation_filetime):
        file_properties_data = (
            b"\x00" * 16
            + struct.pack("<Q", 0)
            + struct.pack("<Q", creation_filetime)
            + struct.pack("<Q", 0)
        )
        file_properties_object = (
            ps.ASF_FILE_PROPERTIES_OBJECT_GUID
            + struct.pack("<Q", 24 + len(file_properties_data))
            + file_properties_data
        )
        header_specific = struct.pack("<IH", 1, 0)  # 1 sous-objet, réservé
        header_object_content = header_specific + file_properties_object
        header_object_size = 24 + len(header_object_content)
        header = ps.ASF_HEADER_OBJECT_GUID + struct.pack("<Q", header_object_size) + header_object_content
        path.write_bytes(header)

    def test_uses_wmv_file_properties_creation_date(self):
        path = self.dir / "video.wmv"
        expected = datetime(2019, 4, 10, 8, 30, 0)
        delta = expected - ps.FILETIME_EPOCH
        creation_filetime = (delta.days * 86400 + delta.seconds) * 10_000_000
        self._write_fake_wmv(path, creation_filetime)

        wrong = datetime(1999, 1, 1)
        os.utime(path, (wrong.timestamp(), wrong.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def test_wmv_falls_back_to_mtime_when_creation_date_is_zero(self):
        path = self.dir / "video.wmv"
        self._write_fake_wmv(path, creation_filetime=0)

        expected = datetime(2018, 8, 8, 8, 0, 0)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def _write_fake_matroska(self, path, date_utc_ns=None):
        def elem(id_bytes, payload):
            return id_bytes + bytes([0x80 | len(payload)]) + payload

        info_content = elem(b"\x44\x61", struct.pack(">q", date_utc_ns)) if date_utc_ns is not None else b""
        segment_content = elem(b"\x15\x49\xa9\x66", info_content)
        segment_elem = b"\x18\x53\x80\x67" + bytes([0x80 | len(segment_content)]) + segment_content
        ebml_header_elem = b"\x1a\x45\xdf\xa3" + bytes([0x80 | 4]) + b"\x00" * 4
        path.write_bytes(ebml_header_elem + segment_elem)

    def test_uses_matroska_date_utc(self):
        path = self.dir / "video.mkv"
        expected = datetime(2020, 3, 4, 5, 6, 7)
        delta = expected - ps.MATROSKA_DATE_UTC_EPOCH
        date_utc_ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000
        self._write_fake_matroska(path, date_utc_ns)

        wrong = datetime(1999, 1, 1)
        os.utime(path, (wrong.timestamp(), wrong.timestamp()))

        self.assertEqual(ps.get_media_date(path), expected)

    def test_matroska_info_without_date_utc_falls_back_to_mtime(self):
        path = self.dir / "video.webm"
        self._write_fake_matroska(path, date_utc_ns=None)

        expected = datetime(2017, 5, 5, 5, 5, 5)
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
