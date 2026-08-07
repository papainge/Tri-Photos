import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import media_date_utils as mdu


class TestIsPlausibleMediaDate(unittest.TestCase):
    def test_accepts_lower_bound_year(self):
        self.assertTrue(mdu.is_plausible_media_date(datetime(1990, 1, 1)))

    def test_rejects_just_below_lower_bound(self):
        self.assertFalse(mdu.is_plausible_media_date(datetime(1989, 12, 31)))

    def test_accepts_upper_bound_year(self):
        self.assertTrue(mdu.is_plausible_media_date(datetime(datetime.now().year + 1, 12, 31)))

    def test_rejects_just_above_upper_bound(self):
        self.assertFalse(mdu.is_plausible_media_date(datetime(datetime.now().year + 2, 1, 1)))

    def test_rejects_unix_epoch(self):
        # Un appareil dont l'horloge n'a jamais été réglée renvoie souvent cette date.
        self.assertFalse(mdu.is_plausible_media_date(datetime(1970, 1, 1)))

    def test_accepts_current_year(self):
        self.assertTrue(mdu.is_plausible_media_date(datetime(datetime.now().year, 6, 15)))


if __name__ == "__main__":
    unittest.main()
