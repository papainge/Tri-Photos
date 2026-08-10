import importlib.util
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
sys.path.insert(0, SRC_DIR)

from PIL import ExifTags, Image  # noqa: E402 — après sys.path.insert, volontairement

import photo_metadata as pm  # noqa: E402

HEIC_AVAILABLE = importlib.util.find_spec("pillow_heif") is not None


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


class TestHeicSupport(unittest.TestCase):
    """Un vrai fichier HEIC/HEIF, avec et sans le plugin optionnel pillow-heif actif
    (voir README : sans lui, Pillow seul ne sait pas décoder ce conteneur).

    Chaque cas tourne dans un sous-processus dédié : register_heif_opener() enregistre
    le décodeur HEIF globalement pour tout l'interpréteur, sans possibilité de
    l'annuler. media_sorter.py l'appelle lui-même à l'import — et comme test_media_sorter*
    importent media_sorter, le décodeur serait déjà actif ici dès la collecte des tests
    (avant même l'exécution) si ces tests s'exécutaient dans le process principal,
    quel que soit l'ordre d'exécution des fichiers de test.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = Path(self.tmpdir.name) / "photo.heic"

    def _make_heic(self, exif_date=None):
        import pillow_heif

        img = Image.new("RGB", (4, 4), color="red")
        kwargs = {}
        if exif_date is not None:
            exif = img.getexif()
            exif.get_ifd(pm.ExifTags.IFD.Exif)[pm.EXIF_DATE_TIME_ORIGINAL] = exif_date
            kwargs["exif"] = exif.tobytes()
        # from_pillow()/save() encodent directement, sans passer par le système de
        # plugins de PIL (contrairement à Image.save()) : contrairement à la lecture,
        # l'écriture HEIF de pillow_heif n'a donc besoin d'aucun enregistrement préalable
        # et n'affecte pas le registre de plugins de PIL pour le reste du process.
        pillow_heif.from_pillow(img).save(self.path, quality=50, **kwargs)

    def _read_date_in_subprocess(self, register_opener):
        script = f"""
import sys
sys.path.insert(0, {SRC_DIR!r})
if {register_opener}:
    import pillow_heif
    pillow_heif.register_heif_opener()
from pathlib import Path
import photo_metadata as pm
try:
    date = pm.get_photo_exif_date(Path({str(self.path)!r}))
except Exception as exc:
    print("EXC:" + type(exc).__name__)
else:
    print(date.isoformat() if date else "None")
"""
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    @unittest.skipUnless(HEIC_AVAILABLE, "pillow-heif non installé")
    def test_reads_exif_date_from_a_real_heic_file_when_the_plugin_is_registered(self):
        self._make_heic(exif_date="2022:08:15 09:00:00")

        output = self._read_date_in_subprocess(register_opener=True)

        self.assertEqual(output, "2022-08-15T09:00:00")

    @unittest.skipUnless(HEIC_AVAILABLE, "pillow-heif requis pour générer le fichier HEIC de test")
    def test_raises_on_a_real_heic_file_when_the_plugin_is_not_registered(self):
        # Reproduit ce qu'un utilisateur sans pillow-heif installé rencontre réellement
        # (voir README) : Pillow seul ne sait pas décoder le conteneur HEIF. C'est à
        # media_sorter.get_media_date (except Exception -> None -> classement "No Info")
        # d'absorber ce cas, pas à get_photo_exif_date lui-même (voir son docstring).
        self._make_heic()

        output = self._read_date_in_subprocess(register_opener=False)

        self.assertEqual(output, "EXC:UnidentifiedImageError")


if __name__ == "__main__":
    unittest.main()
