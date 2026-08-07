"""Lecture de la date de création embarquée dans les fichiers vidéo, sans dépendance
externe. Un parseur par famille de conteneur :

- MP4/MOV/M4V/3GP : boîte "moov"/"mvhd" (ISO-BMFF/QuickTime).
- AVI              : chunk "IDIT" du "LIST INFO" (RIFF).
- WMV              : "File Properties Object" (ASF).
- MKV/WEBM         : élément "DateUTC" de Segment/Info (EBML/Matroska).

Chaque parseur ne lit jamais les données audio/vidéo elles-mêmes (potentiellement
énormes) : il ne fait que sauter par-dessus grâce aux tailles déclarées dans les
en-têtes du conteneur. MPG/MPEG n'a pas d'équivalent standardisé à ces structures et
n'est donc pas géré ici (voir media_sorter.get_media_date pour le repli sur la date de
modification du fichier).

Copyright (C) 2026 Guillaume Pataut
Logiciel libre distribué sous licence GNU GPL v3 (ou ultérieure) — voir le fichier
LICENSE à la racine du dépôt, ou <https://www.gnu.org/licenses/>.
"""

from datetime import datetime, timedelta
from pathlib import Path

# Conteneurs vidéo dont la date de création embarquée se lit ici.
MP4_LIKE_EXTENSIONS = {".mp4", ".mov", ".m4v", ".3gp"}    # ISO-BMFF/QuickTime (moov/mvhd)
RIFF_VIDEO_EXTENSIONS = {".avi"}                          # RIFF (LIST INFO / IDIT)
ASF_VIDEO_EXTENSIONS = {".wmv"}                           # ASF (File Properties Object)
MATROSKA_VIDEO_EXTENSIONS = {".mkv", ".webm"}             # EBML/Matroska (Segment/Info/DateUTC)

# Écart, en secondes, entre l'époque QuickTime/Mac (1904-01-01) et l'époque Unix
# (1970-01-01), utilisé pour convertir le "creation_time" de l'atome mvhd.
MP4_EPOCH = datetime(1904, 1, 1)

# Époque des FILETIME Windows (utilisées par ASF/WMV), en unités de 100 nanosecondes.
FILETIME_EPOCH = datetime(1601, 1, 1)

# Époque des dates Matroska/EBML (DateUTC), en nanosecondes.
MATROSKA_DATE_UTC_EPOCH = datetime(2001, 1, 1)


def _is_plausible_media_date(date: datetime) -> bool:
    """Filtre les dates aberrantes (métadonnées corrompues ou jamais renseignées),
    pour lesquelles la date de modification du fichier est plus fiable."""
    return 1990 <= date.year <= datetime.now().year + 1


def _iter_mp4_boxes(data: bytes, start: int, end: int):
    """Itère les boîtes ISO-BMFF/QuickTime (type, début, fin du contenu) dans data[start:end]."""
    offset = start
    while offset + 8 <= end:
        size = int.from_bytes(data[offset:offset + 4], "big")
        box_type = data[offset + 4:offset + 8].decode("ascii", errors="replace")
        header_size = 8
        if size == 1:
            if offset + 16 > end:
                break
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
            header_size = 16
        elif size == 0:
            size = end - offset
        if size < header_size or offset + size > end:
            break
        yield box_type, offset + header_size, offset + size
        offset += size


def get_mp4_creation_date(path: Path):
    """Lit la date de création dans l'atome "mvhd" d'un fichier MP4/MOV (boîte "moov"),
    sans dépendance externe. Renvoie None si elle est absente ou invalide.

    "mdat" (les données audio/vidéo, potentiellement énormes) est ignoré sans être lu :
    seul son en-tête est parcouru pour sauter directement à la boîte suivante.
    """
    file_size = path.stat().st_size
    moov_data = None
    with open(path, "rb") as f:
        offset = 0
        while offset + 8 <= file_size:
            header = f.read(8)
            if len(header) < 8:
                break
            size = int.from_bytes(header[0:4], "big")
            box_type = header[4:8].decode("ascii", errors="replace")
            header_size = 8
            if size == 1:
                size = int.from_bytes(f.read(8), "big")
                header_size = 16
            elif size == 0:
                size = file_size - offset
            if size < header_size:
                break
            if box_type == "moov":
                moov_data = f.read(size - header_size)
                break
            f.seek(offset + size)
            offset += size

    if not moov_data:
        return None

    mvhd = next((b for b in _iter_mp4_boxes(moov_data, 0, len(moov_data)) if b[0] == "mvhd"), None)
    if mvhd is None:
        return None
    _, start, end = mvhd
    if start >= len(moov_data):
        return None

    version = moov_data[start]
    if version == 1:
        if start + 12 > end:
            return None
        creation_time = int.from_bytes(moov_data[start + 4:start + 12], "big")
    else:
        if start + 8 > end:
            return None
        creation_time = int.from_bytes(moov_data[start + 4:start + 8], "big")

    if creation_time <= 0:
        return None
    try:
        date = MP4_EPOCH + timedelta(seconds=creation_time)
    except OverflowError:
        return None
    return date if _is_plausible_media_date(date) else None


def _iter_riff_chunks(f, start: int, end: int):
    """Itère les chunks RIFF de premier niveau entre start et end dans le fichier ouvert
    f. Les gros chunks (ex: "movi", les données audio/vidéo) ne sont jamais lus : on
    saute directement à l'octet suivant grâce à la taille déclarée dans leur en-tête."""
    offset = start
    while offset + 8 <= end:
        f.seek(offset)
        header = f.read(8)
        if len(header) < 8:
            break
        fourcc = header[0:4].decode("ascii", errors="replace")
        size = int.from_bytes(header[4:8], "little")
        data_start = offset + 8
        data_end = data_start + size
        if data_end > end:
            break
        yield fourcc, data_start, data_end
        offset = data_end + (size % 2)  # les chunks RIFF sont alignés sur 2 octets


def get_avi_creation_date(path: Path):
    """Lit la date de création dans le chunk "IDIT" (LIST "INFO") d'un fichier AVI,
    sans dépendance externe. Renvoie None si absente, invalide, ou si le fichier n'a
    pas de LIST "INFO" (chunk optionnel, souvent absent des AVI modernes)."""
    file_size = path.stat().st_size
    with open(path, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"AVI ":
            return None
        for fourcc, data_start, data_end in _iter_riff_chunks(f, 12, file_size):
            if fourcc != "LIST" or data_start + 4 > data_end:
                continue
            f.seek(data_start)
            list_type = f.read(4).decode("ascii", errors="replace")
            if list_type != "INFO":
                continue
            for sub_fourcc, sub_start, sub_end in _iter_riff_chunks(f, data_start + 4, data_end):
                if sub_fourcc != "IDIT":
                    continue
                f.seek(sub_start)
                raw = f.read(sub_end - sub_start)
                text = raw.split(b"\x00")[0].decode("ascii", errors="ignore").strip()
                for fmt in ("%a %b %d %H:%M:%S %Y", "%Y-%m-%d %H:%M:%S"):
                    try:
                        date = datetime.strptime(text, fmt)
                    except ValueError:
                        continue
                    return date if _is_plausible_media_date(date) else None
            return None
    return None


ASF_HEADER_OBJECT_GUID = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")
ASF_FILE_PROPERTIES_OBJECT_GUID = bytes.fromhex("a1dcab8c47a9cf118ee400c00c205365")

ASF_MIN_OBJECT_SIZE = 24  # GUID (16 octets) + taille (8 octets) : plus petit objet ASF valide
ASF_MAX_HEADER_OBJECTS = 1024  # largement au-dessus de ce qu'un en-tête ASF réel contient


def get_wmv_creation_date(path: Path):
    """Lit la date de création dans l'objet "File Properties" d'un fichier WMV/ASF,
    sans dépendance externe. Renvoie None si absente ou invalide.

    Seul l'objet d'en-tête ASF (généralement quelques Ko, toujours en tête de fichier)
    est parcouru ; l'objet "Data" qui suit (les données audio/vidéo) n'est jamais lu.
    """
    with open(path, "rb") as f:
        header = f.read(30)
        if len(header) < 30 or header[0:16] != ASF_HEADER_OBJECT_GUID:
            return None
        header_object_size = int.from_bytes(header[16:24], "little")
        num_objects = min(int.from_bytes(header[24:28], "little"), ASF_MAX_HEADER_OBJECTS)

        offset = 30
        for _ in range(num_objects):
            if offset + ASF_MIN_OBJECT_SIZE > header_object_size:
                break
            f.seek(offset)
            sub_header = f.read(24)
            if len(sub_header) < 24:
                break
            guid = sub_header[0:16]
            size = int.from_bytes(sub_header[16:24], "little")
            if size < ASF_MIN_OBJECT_SIZE:
                # Taille d'objet incohérente (en-tête corrompu) : offset ne progresserait
                # plus (ou plus assez), ce qui bouclerait sur place jusqu'à num_objects.
                break
            if guid == ASF_FILE_PROPERTIES_OBJECT_GUID:
                f.seek(offset + 24 + 24)  # en-tête (24) + File ID (16) + File Size (8)
                raw = f.read(8)
                if len(raw) < 8:
                    return None
                creation_100ns = int.from_bytes(raw, "little")
                if creation_100ns <= 0:
                    return None
                try:
                    date = FILETIME_EPOCH + timedelta(microseconds=creation_100ns // 10)
                except OverflowError:
                    return None
                return date if _is_plausible_media_date(date) else None
            offset += size
    return None


def _read_ebml_vint_length_and_marker(first_byte: int):
    if first_byte == 0:
        return None
    length = 1
    marker = 0x80
    while not (first_byte & marker):
        marker >>= 1
        length += 1
    return length, marker


def _read_ebml_id(data: bytes, offset: int):
    """Lit un identifiant EBML (VINT dont le bit de marqueur fait partie de la valeur,
    par convention). Renvoie (id, nouvel_offset) ou None si les données sont insuffisantes."""
    if offset >= len(data):
        return None
    info = _read_ebml_vint_length_and_marker(data[offset])
    if info is None:
        return None
    length, _ = info
    if offset + length > len(data):
        return None
    return int.from_bytes(data[offset:offset + length], "big"), offset + length


def _read_ebml_size(data: bytes, offset: int):
    """Lit une taille EBML (VINT dont le bit de marqueur est retiré de la valeur).
    Renvoie (taille, nouvel_offset), taille valant None si elle est déclarée "inconnue"
    (flux en direct), ou None si les données sont insuffisantes."""
    if offset >= len(data):
        return None
    info = _read_ebml_vint_length_and_marker(data[offset])
    if info is None:
        return None
    length, marker = info
    if offset + length > len(data):
        return None
    raw = bytearray(data[offset:offset + length])
    raw[0] &= marker - 1
    value = int.from_bytes(bytes(raw), "big")
    if value == (1 << (7 * length)) - 1:
        return None, offset + length
    return value, offset + length


EBML_HEADER_ID = 0x1A45DFA3
EBML_SEGMENT_ID = 0x18538067
EBML_INFO_ID = 0x1549A966
EBML_CLUSTER_ID = 0x1F43B675
EBML_DATE_UTC_ID = 0x4461

# Nombre d'octets lus en tête de fichier pour y chercher Segment/Info/DateUTC : les
# muxers Matroska y placent toujours ces éléments avant les Cluster (les données
# audio/vidéo, potentiellement énormes, qu'on ne lit donc jamais).
MATROSKA_HEADER_SCAN_SIZE = 1024 * 1024


def _find_matroska_date_utc(data: bytes, start: int, end: int):
    offset = start
    while offset < end:
        id_result = _read_ebml_id(data, offset)
        if id_result is None:
            break
        elem_id, offset = id_result
        size_result = _read_ebml_size(data, offset)
        if size_result is None:
            break
        elem_size, offset = size_result
        if elem_size is None:
            break
        elem_end = min(offset + elem_size, end)
        if elem_id == EBML_DATE_UTC_ID and elem_end - offset == 8:
            nanoseconds = int.from_bytes(data[offset:elem_end], "big", signed=True)
            try:
                date = MATROSKA_DATE_UTC_EPOCH + timedelta(microseconds=nanoseconds // 1000)
            except OverflowError:
                return None
            return date if _is_plausible_media_date(date) else None
        offset = elem_end
    return None


def get_matroska_creation_date(path: Path):
    """Lit la date de création ("DateUTC") d'un fichier Matroska/WebM, sans dépendance
    externe. Ne descend que dans Segment -> Info ; s'arrête dès qu'un Cluster (les
    données audio/vidéo) est rencontré, puisque Info le précède toujours."""
    with open(path, "rb") as f:
        data = f.read(MATROSKA_HEADER_SCAN_SIZE)

    header_id_result = _read_ebml_id(data, 0)
    if header_id_result is None or header_id_result[0] != EBML_HEADER_ID:
        return None
    _, offset = header_id_result
    header_size_result = _read_ebml_size(data, offset)
    if header_size_result is None or header_size_result[0] is None:
        return None
    header_size, offset = header_size_result
    offset += header_size

    segment_id_result = _read_ebml_id(data, offset)
    if segment_id_result is None or segment_id_result[0] != EBML_SEGMENT_ID:
        return None
    _, offset = segment_id_result
    segment_size_result = _read_ebml_size(data, offset)
    if segment_size_result is None:
        return None
    segment_size, offset = segment_size_result
    segment_end = len(data) if segment_size is None else min(len(data), offset + segment_size)

    while offset < segment_end:
        child_id_result = _read_ebml_id(data, offset)
        if child_id_result is None:
            break
        child_id, offset = child_id_result
        child_size_result = _read_ebml_size(data, offset)
        if child_size_result is None:
            break
        child_size, offset = child_size_result
        if child_size is None or child_id == EBML_CLUSTER_ID:
            break
        child_end = min(offset + child_size, segment_end)
        if child_id == EBML_INFO_ID:
            date = _find_matroska_date_utc(data, offset, child_end)
            if date is not None:
                return date
        offset = child_end
    return None


def get_video_creation_date(path: Path):
    """Point d'entrée unique : choisit le parseur adapté à l'extension du fichier.
    Renvoie None si le conteneur n'est pas géré ici, ou si sa métadonnée de date est
    absente/invalide."""
    suffix = path.suffix.lower()
    if suffix in MP4_LIKE_EXTENSIONS:
        return get_mp4_creation_date(path)
    if suffix in RIFF_VIDEO_EXTENSIONS:
        return get_avi_creation_date(path)
    if suffix in ASF_VIDEO_EXTENSIONS:
        return get_wmv_creation_date(path)
    if suffix in MATROSKA_VIDEO_EXTENSIONS:
        return get_matroska_creation_date(path)
    return None
