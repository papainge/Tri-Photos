"""Tests de l'interface en ligne de commande (cli.py) : validations, transfert réel de
fichiers via un vrai media_sorter (pas de mock, sauf pour le test de report d'erreurs de
copy_files), et confirmation interactive.
"""

import io
import os
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Avant l'import : redirige les logs et préférences (app_config.py, importé par
# media_sorter/cli) hors des vrais dossiers de la machine qui exécute les tests (voir
# app_config._log_directory, app_config._config_directory et test_load.py).
os.environ.setdefault("TRIPHOTOS_LOG_DIR", str(Path(tempfile.gettempdir()) / "triphotos-tests-logs"))
os.environ.setdefault("TRIPHOTOS_CONFIG_DIR", str(Path(tempfile.gettempdir()) / "triphotos-tests-config"))

from PIL import Image

import app_config
import cli
import media_sorter as ms
import photo_metadata as pm


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.src_dir = Path(self.tmpdir.name) / "src"
        self.dest_dir = Path(self.tmpdir.name) / "dest"
        self.src_dir.mkdir()
        self._photo_counter = 0

    def _make_photo(self, relative_path, date):
        # Vraie date EXIF DateTimeOriginal (pas la date de modification, volontairement
        # ignorée par get_media_date — voir media_sorter.py) : nécessaire ici puisque ces
        # tests font tourner un vrai scan_media, pas un tree_data construit à la main
        # comme dans test_app_ui.py. Couleur distincte à chaque appel : deux images
        # identiques (même contenu JPEG) seraient détectées comme doublons par
        # transfer_file.
        path = self.src_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._photo_counter += 1
        n = self._photo_counter
        color = (n % 256, (n * 7) % 256, (n * 13) % 256)
        img = Image.new("RGB", (2, 2), color=color)
        exif = img.getexif()
        exif.get_ifd(pm.ExifTags.IFD.Exif)[pm.EXIF_DATE_TIME_ORIGINAL] = date.strftime("%Y:%m:%d %H:%M:%S")
        img.save(path, exif=exif)
        return path

    def _run(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = cli.run_cli(argv)
        return exit_code, stdout.getvalue(), stderr.getvalue()


class TestArgParsing(CliTestCase):
    def test_requires_source_and_dest(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.build_arg_parser().parse_args([])

    def test_defaults(self):
        args = cli.build_arg_parser().parse_args(["--source", "a", "--dest", "b"])

        self.assertEqual(args.level, "jour")
        self.assertEqual(args.mode, "copier")
        self.assertFalse(args.separate_media)
        self.assertTrue(args.recursive)
        self.assertFalse(args.rename)
        self.assertFalse(args.filename_fallback)
        self.assertFalse(args.force)

    def test_source_is_repeatable(self):
        args = cli.build_arg_parser().parse_args(["--source", "a", "--source", "b", "--dest", "c"])

        self.assertEqual(args.source, ["a", "b"])

    def test_no_recursive_flag_disables_recursion(self):
        args = cli.build_arg_parser().parse_args(["--source", "a", "--dest", "b", "--no-recursive"])

        self.assertFalse(args.recursive)


class TestRunCliValidation(CliTestCase):
    def test_errors_when_source_dir_does_not_exist(self):
        missing = Path(self.tmpdir.name) / "missing"

        exit_code, _out, err = self._run(["--source", str(missing), "--dest", str(self.dest_dir), "--force"])

        self.assertEqual(exit_code, 1)
        self.assertIn("n'existe pas", err)

    def test_errors_when_dest_equals_a_source_dir(self):
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        exit_code, _out, err = self._run(["--source", str(self.src_dir), "--dest", str(self.src_dir), "--force"])

        self.assertEqual(exit_code, 1)
        self.assertIn("dossier de destination", err)

    def test_errors_when_dest_is_nested_inside_a_source_dir(self):
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))
        nested_dest = self.src_dir / "sorted"

        exit_code, _out, err = self._run(["--source", str(self.src_dir), "--dest", str(nested_dest), "--force"])

        self.assertEqual(exit_code, 1)
        self.assertIn("dossier de destination", err)

    def test_reports_when_no_media_found(self):
        exit_code, out, _err = self._run(["--source", str(self.src_dir), "--dest", str(self.dest_dir), "--force"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Aucune photo ou vidéo trouvée", out)
        self.assertFalse(self.dest_dir.exists())


class TestRunCliTransfer(CliTestCase):
    def test_copies_files_with_force_flag(self):
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        exit_code, out, _err = self._run(["--source", str(self.src_dir), "--dest", str(self.dest_dir), "--force"])

        self.assertEqual(exit_code, 0)
        self.assertIn("1 fichier(s) copié(s)", out)
        self.assertTrue((self.dest_dir / "2024" / "01-Janvier" / "15" / "a.jpg").exists())
        self.assertTrue((self.src_dir / "a.jpg").exists())  # copie : original conservé

    def test_moves_files_when_mode_deplacer(self):
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        exit_code, out, _err = self._run(
            ["--source", str(self.src_dir), "--dest", str(self.dest_dir), "--mode", "deplacer", "--force"]
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("1 fichier(s) déplacé(s)", out)
        self.assertTrue((self.dest_dir / "2024" / "01-Janvier" / "15" / "a.jpg").exists())
        self.assertFalse((self.src_dir / "a.jpg").exists())

    def test_separate_media_option_creates_photos_subfolder(self):
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        exit_code, _out, _err = self._run(
            ["--source", str(self.src_dir), "--dest", str(self.dest_dir), "--separate-media", "--force"]
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue((self.dest_dir / "Photos" / "2024" / "01-Janvier" / "15" / "a.jpg").exists())

    def test_level_option_controls_destination_depth(self):
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        exit_code, _out, _err = self._run(
            ["--source", str(self.src_dir), "--dest", str(self.dest_dir), "--level", "annee", "--force"]
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue((self.dest_dir / "2024" / "a.jpg").exists())

    def test_accepts_several_source_directories(self):
        other_dir = Path(self.tmpdir.name) / "src2"
        other_dir.mkdir()
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))
        photo2 = other_dir / "b.jpg"
        Image.new("RGB", (2, 2), color=(9, 9, 9)).save(photo2)
        os.utime(photo2, (datetime(2024, 2, 20).timestamp(),) * 2)

        exit_code, out, _err = self._run(
            ["--source", str(self.src_dir), "--source", str(other_dir), "--dest", str(self.dest_dir), "--force"]
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("2 fichier(s) copié(s)", out)

    def test_reports_no_info_breakdown_by_reason_when_present(self):
        # GIF n'a pas de mécanisme EXIF (voir media_sorter.UNSUPPORTED_DATE_EXTENSIONS) :
        # toujours classé No Info, sans dépendre d'une métadonnée particulière à simuler.
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))
        Image.new("RGB", (2, 2)).save(self.src_dir / "no_date.gif", format="GIF")

        exit_code, out, _err = self._run(["--source", str(self.src_dir), "--dest", str(self.dest_dir), "--force"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Dont 1 sans date exploitable", out)
        self.assertIn(f"{ms.NO_INFO_REASON_UNSUPPORTED_FORMAT} : 1", out)
        # Le fichier No Info est malgré tout transféré, comme côté UI (voir build_destination_map).
        self.assertTrue((self.dest_dir / ms.NO_INFO_LABEL / "no_date.gif").exists())

    def test_omits_no_info_breakdown_when_absent(self):
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        exit_code, out, _err = self._run(["--source", str(self.src_dir), "--dest", str(self.dest_dir), "--force"])

        self.assertEqual(exit_code, 0)
        self.assertNotIn("sans date exploitable", out)


class TestRunCliConfirmationPrompt(CliTestCase):
    def test_cancels_when_user_declines(self):
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        with unittest.mock.patch("builtins.input", return_value="n"):
            exit_code, out, _err = self._run(["--source", str(self.src_dir), "--dest", str(self.dest_dir)])

        self.assertEqual(exit_code, 1)
        self.assertIn("Annulé", out)
        self.assertFalse(self.dest_dir.exists())

    def test_proceeds_when_user_confirms(self):
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        with unittest.mock.patch("builtins.input", return_value="o"):
            exit_code, _out, _err = self._run(["--source", str(self.src_dir), "--dest", str(self.dest_dir)])

        self.assertEqual(exit_code, 0)
        self.assertTrue((self.dest_dir / "2024" / "01-Janvier" / "15" / "a.jpg").exists())

    def test_cancels_without_hanging_when_no_input_available(self):
        # Cas d'une tâche planifiée où --force a été oublié : aucune entrée disponible sur
        # stdin. input() lève EOFError plutôt que de bloquer indéfiniment.
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        with unittest.mock.patch("builtins.input", side_effect=EOFError):
            exit_code, out, _err = self._run(["--source", str(self.src_dir), "--dest", str(self.dest_dir)])

        self.assertEqual(exit_code, 1)
        self.assertIn("Annulé", out)


class TestRunCliErrors(CliTestCase):
    def test_returns_1_and_reports_errors_from_copy_files(self):
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        with unittest.mock.patch.object(ms, "copy_files", return_value=(1, 0, ["a.jpg: boom"])):
            exit_code, _out, err = self._run(["--source", str(self.src_dir), "--dest", str(self.dest_dir), "--force"])

        self.assertEqual(exit_code, 1)
        self.assertIn("1 erreur(s)", err)
        self.assertIn("boom", err)

    def test_returns_1_logs_and_reports_an_unexpected_exception_from_copy_files(self):
        # copy_files() protège déjà chaque fichier/dossier de destination individuellement
        # (voir TestRunCliErrors ci-dessus) : ce test couvre le cas d'un échec véritablement
        # imprévu (bug, erreur non anticipée) qui échapperait à cette protection interne —
        # symétrique de app_ui._copy_worker côté GUI, qui journalise déjà ce même cas.
        self._make_photo("a.jpg", date=datetime(2024, 1, 15))

        with unittest.mock.patch.object(ms, "copy_files", side_effect=RuntimeError("boom inattendu")):
            with unittest.mock.patch.object(app_config.logger, "exception") as logger_exception:
                exit_code, _out, err = self._run(
                    ["--source", str(self.src_dir), "--dest", str(self.dest_dir), "--force"]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("Erreur lors de la copie", err)
        self.assertIn("boom inattendu", err)
        logger_exception.assert_called_once()


if __name__ == "__main__":
    unittest.main()
