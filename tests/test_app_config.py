import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Avant l'import : redirige les logs hors du vrai dossier de logs de la machine qui
# exécute les tests (voir app_config._log_directory et test_load.py) — app_config
# configure sa journalisation dès l'import (logger = _configure_logging()).
os.environ.setdefault("TRIPHOTOS_LOG_DIR", str(Path(tempfile.gettempdir()) / "triphotos-tests-logs"))
os.environ.setdefault("TRIPHOTOS_CONFIG_DIR", str(Path(tempfile.gettempdir()) / "triphotos-tests-config"))

import app_config as ac

VALID_SORT_LEVELS = ("annee", "mois", "jour")


class TestLogDirectory(unittest.TestCase):
    def test_override_takes_precedence(self):
        with unittest.mock.patch.dict(os.environ, {"TRIPHOTOS_LOG_DIR": "/tmp/somewhere"}, clear=False):
            self.assertEqual(ac._log_directory(), Path("/tmp/somewhere"))


class TestConfigDirectory(unittest.TestCase):
    def test_override_takes_precedence(self):
        with unittest.mock.patch.dict(os.environ, {"TRIPHOTOS_CONFIG_DIR": "/tmp/elsewhere"}, clear=False):
            self.assertEqual(ac._config_directory(), Path("/tmp/elsewhere"))


class TestPreferences(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        # Isole du dossier partagé TRIPHOTOS_CONFIG_DIR (voir en tête de fichier) : sinon
        # une préférence sauvegardée par un test polluerait celles lues par un autre.
        original = os.environ.get("TRIPHOTOS_CONFIG_DIR")
        os.environ["TRIPHOTOS_CONFIG_DIR"] = self.tmpdir.name
        self.addCleanup(self._restore_env_var, original)

    @staticmethod
    def _restore_env_var(original):
        if original is None:
            os.environ.pop("TRIPHOTOS_CONFIG_DIR", None)
        else:
            os.environ["TRIPHOTOS_CONFIG_DIR"] = original

    def test_load_returns_defaults_when_file_missing(self):
        self.assertEqual(ac.load_preferences(VALID_SORT_LEVELS), ac.DEFAULT_PREFERENCES)

    def test_save_then_load_roundtrips(self):
        ac.save_preferences("annee", True, "deplacer")

        self.assertEqual(
            ac.load_preferences(VALID_SORT_LEVELS),
            {"sort_level": "annee", "separate_media": True, "copy_mode": "deplacer"},
        )

    def test_load_falls_back_to_defaults_on_corrupt_json(self):
        config_path = Path(self.tmpdir.name) / ac.PREFERENCES_FILE_NAME
        config_path.write_text("not valid json{{{", encoding="utf-8")

        self.assertEqual(ac.load_preferences(VALID_SORT_LEVELS), ac.DEFAULT_PREFERENCES)

    def test_load_ignores_invalid_values(self):
        config_path = Path(self.tmpdir.name) / ac.PREFERENCES_FILE_NAME
        config_path.write_text(
            '{"sort_level": "siecle", "separate_media": "oui", "copy_mode": "voler"}',
            encoding="utf-8",
        )

        self.assertEqual(ac.load_preferences(VALID_SORT_LEVELS), ac.DEFAULT_PREFERENCES)

    def test_load_rejects_a_sort_level_not_in_the_caller_supplied_list(self):
        # valid_sort_levels vient de l'appelant (ex. media_sorter.SORT_LEVELS) plutôt que
        # d'être codé en dur ici (voir load_preferences) : une valeur par ailleurs bien
        # formée mais absente de la liste fournie doit retomber sur la valeur par défaut,
        # pas être acceptée telle quelle.
        ac.save_preferences("mois", False, "copier")

        self.assertEqual(ac.load_preferences(valid_sort_levels=("jour",))["sort_level"], "jour")


if __name__ == "__main__":
    unittest.main()
