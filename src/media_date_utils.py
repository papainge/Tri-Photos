"""Utilitaire partagé entre photo_metadata et video_metadata pour écarter les dates de
prise de vue/création manifestement aberrantes.

Copyright (C) 2026 Guillaume Pataut
Logiciel libre distribué sous licence GNU GPL v3 (ou ultérieure) — voir le fichier
LICENSE à la racine du dépôt, ou <https://www.gnu.org/licenses/>.
"""

from datetime import datetime


def is_plausible_media_date(date: datetime) -> bool:
    """Filtre les dates aberrantes (métadonnées corrompues ou jamais renseignées, par
    exemple un appareil dont l'horloge n'a jamais été réglée), pour lesquelles la date
    de modification du fichier est plus fiable."""
    return 1990 <= date.year <= datetime.now().year + 1
