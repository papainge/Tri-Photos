import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import ExifTags, Image

import photo_metadata as pm


class TestGetPhotoExifDate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def _save_with_exif_date(self, path, tag_id, value, in_sub_ifd):
        img = Image.new("RGB", (2, 2), color="red")
        exif = img.getexif()
        if in_sub_ifd:
            exif.get_ifd(ExifTags.IFD.Exif)[tag_id] = value
        else:
            exif[tag_id] = value
        img.save(path, exif=exif)

    def test_returns_none_without_exif(self):
        path = self.dir / "photo.png"
        Image.new("RGB", (2, 2), color="blue").save(path)

        self.assertIsNone(pm.get_photo_exif_date(path))

    def test_returns_none_for_formats_without_exif_support(self):
        # GIF et BMP n'ont pas de mécanisme EXIF : getexif()/get_ifd() ne doit ni
        # planter, ni renvoyer de fausse date, sur ces formats.
        for fmt, ext in (("GIF", ".gif"), ("BMP", ".bmp")):
            path = self.dir / f"photo{ext}"
            Image.new("RGB", (2, 2), color="green").save(path, format=fmt)

            self.assertIsNone(pm.get_photo_exif_date(path))

    def test_uses_exif_date_from_png(self):
        # Le chunk "eXIf" du PNG est un format d'image longtemps non couvert : on
        # vérifie qu'il est bien lu (nécessite exif.tobytes(), contrairement au JPEG).
        path = self.dir / "photo.png"
        img = Image.new("RGB", (2, 2), color="red")
        exif = img.getexif()
        exif.get_ifd(ExifTags.IFD.Exif)[pm.EXIF_DATE_TIME_ORIGINAL] = "2021:09:09 09:09:09"
        img.save(path, exif=exif.tobytes())

        self.assertEqual(pm.get_photo_exif_date(path), datetime(2021, 9, 9, 9, 9, 9))

    def test_uses_date_time_original_from_sub_ifd(self):
        # C'est là qu'un vrai appareil photo range DateTimeOriginal : pas dans l'IFD0
        # renvoyé directement par getexif(), mais dans le sous-IFD Exif.
        path = self.dir / "photo.jpg"
        self._save_with_exif_date(path, pm.EXIF_DATE_TIME_ORIGINAL, "2020:05:17 10:30:00", in_sub_ifd=True)

        self.assertEqual(pm.get_photo_exif_date(path), datetime(2020, 5, 17, 10, 30, 0))

    def test_falls_back_to_date_time_digitized(self):
        path = self.dir / "photo.jpg"
        self._save_with_exif_date(path, pm.EXIF_DATE_TIME_DIGITIZED, "2018:02:03 09:00:00", in_sub_ifd=True)

        self.assertEqual(pm.get_photo_exif_date(path), datetime(2018, 2, 3, 9, 0, 0))

    def test_falls_back_to_date_time_when_no_original_or_digitized(self):
        path = self.dir / "photo.jpg"
        self._save_with_exif_date(path, pm.EXIF_DATE_TIME, "2017:11:20 14:00:00", in_sub_ifd=False)

        self.assertEqual(pm.get_photo_exif_date(path), datetime(2017, 11, 20, 14, 0, 0))

    def test_returns_none_for_implausible_date(self):
        # Un appareil dont l'horloge n'a jamais été réglée renvoie souvent l'époque Unix
        # (1970) ou une date fixe d'usine : cette date n'est pas fiable et le fichier
        # doit être traité comme sans métadonnée (voir media_date_utils.is_plausible_media_date,
        # aussi appliqué côté vidéo).
        path = self.dir / "photo.jpg"
        self._save_with_exif_date(path, pm.EXIF_DATE_TIME_ORIGINAL, "1970:01:01 00:00:00", in_sub_ifd=True)

        self.assertIsNone(pm.get_photo_exif_date(path))


if __name__ == "__main__":
    unittest.main()
