"""Utilitaire partagé entre photo_metadata et video_metadata pour écarter les dates de
prise de vue/création manifestement aberrantes, et pour la détection optionnelle d'une
date dans un nom de fichier (repli de dernier recours quand aucune métadonnée n'est
exploitable, voir media_sorter.get_media_date).

Copyright (C) 2026 Guillaume Pataut
Logiciel libre distribué sous licence GNU GPL v3 (ou ultérieure) — voir le fichier
LICENSE à la racine du dépôt, ou <https://www.gnu.org/licenses/>.
"""

import re
from datetime import datetime


def is_plausible_media_date(date: datetime) -> bool:
    """Filtre les dates aberrantes (métadonnées corrompues ou jamais renseignées, par
    exemple un appareil dont l'horloge n'a jamais été réglée) : un fichier dont la date
    est rejetée ici est traité comme sans métadonnée (voir media_sorter.NO_INFO_LABEL)."""
    return 1990 <= date.year <= datetime.now().year + 1


# AAAAMMJJ (séparateurs "-"/"_" optionnels entre les groupes), suivi optionnellement
# d'une heure HHMMSS précédée d'un séparateur "-"/"_"/espace obligatoire (avec ":"/"."
# optionnels entre les groupes). Bornée par des lookaround non-consommants pour ne
# jamais démarrer/finir au milieu d'une suite de chiffres plus longue (ex: un numéro de
# série de 12 chiffres ne doit pas être lu comme une date à un décalage arbitraire).
_FILENAME_DATE_RE = re.compile(
    r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})"
    r"(?:[-_ ](\d{2})[.:]?(\d{2})[.:]?(\d{2}))?"
    r"(?!\d)"
)


def date_from_filename(name: str):
    """Cherche une date dans un nom de fichier, selon les conventions de nommage les
    plus répandues (Android/iPhone : IMG_20230715_143022.jpg, VID-20230715-143022.mp4 ;
    WhatsApp, sans heure : IMG-20230715-WA0001.jpg ; export desktop avec heure ponctuée :
    2023-07-15 14.30.22.jpg). Renvoie None si aucun motif de date ne correspond, si la
    date est calendaire invalide (ex: mois 13), ou si elle est manifestement aberrante
    (voir is_plausible_media_date) — une suite de chiffres qui ressemble à une date par
    pure coïncidence (numéro de série, résolution...) ne doit pas produire une fausse
    date silencieusement.

    Volontairement plus restrictif que la détection EXIF/vidéo : seuls des motifs de
    date sans ambiguïté sont reconnus (voir media_sorter.get_media_date, dont c'est un
    repli optionnel — activé explicitement par l'utilisateur, jamais par défaut). En
    particulier, si des chiffres supplémentaires suivent immédiatement l'heure sans
    séparateur (ex: millisecondes des Pixel, PXL_20230715_143022123.jpg), l'heure n'est
    pas retenue plutôt que de risquer de la deviner à tort : seule la date, toujours
    fiable ici, est renvoyée (heure à minuit)."""
    match = _FILENAME_DATE_RE.search(name)
    if match is None:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        date = datetime(
            int(year), int(month), int(day),
            int(hour or 0), int(minute or 0), int(second or 0),
        )
    except ValueError:
        return None
    return date if is_plausible_media_date(date) else None
