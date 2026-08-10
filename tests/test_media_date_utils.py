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


class TestDateFromFilename(unittest.TestCase):
    def test_matches_android_style_underscore_with_time(self):
        self.assertEqual(mdu.date_from_filename("IMG_20230715_143022.jpg"), datetime(2023, 7, 15, 14, 30, 22))

    def test_matches_dash_separated_with_time(self):
        self.assertEqual(mdu.date_from_filename("VID-20230715-143022.mp4"), datetime(2023, 7, 15, 14, 30, 22))

    def test_matches_whatsapp_style_date_only(self):
        # Pas d'heure dans ce format : minuit par défaut.
        self.assertEqual(mdu.date_from_filename("IMG-20230715-WA0001.jpg"), datetime(2023, 7, 15, 0, 0, 0))

    def test_matches_dashed_date_with_dotted_time(self):
        self.assertEqual(mdu.date_from_filename("2023-07-15 14.30.22.jpg"), datetime(2023, 7, 15, 14, 30, 22))

    def test_matches_dashed_date_with_colon_time(self):
        self.assertEqual(mdu.date_from_filename("Capture 2023-07-15 14:30:22.png"), datetime(2023, 7, 15, 14, 30, 22))

    def test_falls_back_to_date_only_when_extra_digits_glue_onto_the_time(self):
        # Format Pixel (PXL_...) : millisecondes collées directement après les secondes,
        # sans séparateur. Le motif ne devine jamais une heure à partir de chiffres
        # excédentaires ambigus : il se limite alors à la date (toujours correcte),
        # plutôt que de risquer une heure fausse.
        self.assertEqual(mdu.date_from_filename("PXL_20230715_143022123.jpg"), datetime(2023, 7, 15, 0, 0, 0))

    def test_returns_none_without_a_date_like_substring(self):
        self.assertIsNone(mdu.date_from_filename("vacances_famille.jpg"))

    def test_returns_none_for_invalid_calendar_date(self):
        self.assertIsNone(mdu.date_from_filename("IMG_20231332_000000.jpg"))  # mois 13, jour 32

    def test_returns_none_for_implausible_year(self):
        self.assertIsNone(mdu.date_from_filename("IMG_18000101_000000.jpg"))

    def test_does_not_match_inside_a_longer_digit_run(self):
        # Un numéro à 12 chiffres ne doit pas être lu comme une date à un décalage
        # arbitraire (bornes non-consommantes autour du motif de date).
        self.assertIsNone(mdu.date_from_filename("IMG_123456789012.jpg"))

    def test_returns_none_for_empty_name(self):
        self.assertIsNone(mdu.date_from_filename(""))


if __name__ == "__main__":
    unittest.main()
