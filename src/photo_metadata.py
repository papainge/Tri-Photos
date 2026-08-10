"""Lecture de la date de prise de vue depuis les métadonnées EXIF des photos.

Copyright (C) 2026 Guillaume Pataut
Logiciel libre distribué sous licence GNU GPL v3 (ou ultérieure) — voir le fichier
LICENSE à la racine du dépôt, ou <https://www.gnu.org/licenses/>.
"""

from datetime import datetime
from pathlib import Path

from PIL import Image, ExifTags

from media_date_utils import is_plausible_media_date

# Tags EXIF standards contenant une date, par ordre de préférence. DateTimeOriginal et
# DateTimeDigitized sont rangés par l'appareil photo dans le sous-IFD Exif (et non dans
# l'IFD0 renvoyé directement par Image.getexif()) : il faut passer par get_ifd() pour
# les lire, sans quoi ils ne sont jamais trouvés et on retombe systématiquement sur la
# date de modification du fichier.
EXIF_DATE_TIME_ORIGINAL = 36867
EXIF_DATE_TIME_DIGITIZED = 36868
EXIF_DATE_TIME = 306


def get_photo_exif_date(path: Path):
    """Lit la date EXIF (DateTimeOriginal, puis DateTimeDigitized, puis DateTime) d'une
    photo via Pillow. Renvoie None si absente (formats sans EXIF comme GIF/BMP),
    illisible, ou manifestement aberrante (appareil dont l'horloge n'a jamais été
    réglée, métadonnée corrompue) — voir media_sorter.get_media_date, qui classe alors
    le fichier à part plutôt que de le dater par sa date de modification."""
    with Image.open(path) as img:
        exif = img.getexif()
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        raw = (
            exif_ifd.get(EXIF_DATE_TIME_ORIGINAL)
            or exif_ifd.get(EXIF_DATE_TIME_DIGITIZED)
            or exif.get(EXIF_DATE_TIME)
        )
        if raw:
            date = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
            return date if is_plausible_media_date(date) else None
    return None
