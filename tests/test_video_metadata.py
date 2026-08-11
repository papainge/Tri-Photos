import os
import struct
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import video_metadata as vm

# Marge large pour rester fiable sur une machine/CI lente, tout en détectant une vraie
# régression de performance (lecture intégrale d'un fichier de centaines de Mo) qui se
# chiffrerait en dizaines de secondes, pas en millisecondes.
MAX_SECONDS_LARGE_FILE = 2.0


class TestGetVideoCreationDate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_returns_none_for_unsupported_container(self):
        # MPG/MPEG n'a pas d'équivalent standardisé aux atomes moov/mvhd (MP4), à IDIT
        # (AVI), au File Properties Object (WMV) ou à DateUTC (Matroska).
        path = self.dir / "video.mpg"
        path.write_bytes(b"\x00\x00\x01\xba" + b"\x00" * 20)

        self.assertIsNone(vm.get_video_creation_date(path))


class TestMp4CreationDate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def _write_fake_mp4(self, path, creation_time, version=0, box_type="mvhd"):
        if version == 1:
            mvhd_content = bytes([1]) + b"\x00\x00\x00" + struct.pack(">QQI", creation_time, 0, 600) + struct.pack(">Q", 0)
        else:
            mvhd_content = bytes([0]) + b"\x00\x00\x00" + struct.pack(">III", creation_time, 0, 600) + struct.pack(">I", 0)
        mvhd = struct.pack(">I", 8 + len(mvhd_content)) + box_type.encode("ascii") + mvhd_content
        moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
        ftyp_content = b"isom" + struct.pack(">I", 0) + b"isomiso2mp41"
        ftyp = struct.pack(">I", 8 + len(ftyp_content)) + b"ftyp" + ftyp_content
        path.write_bytes(ftyp + moov)

    def test_version_0(self):
        path = self.dir / "video.mp4"
        expected = datetime(2023, 6, 15, 12, 0, 0)
        creation_time = int((expected - vm.MP4_EPOCH).total_seconds())
        self._write_fake_mp4(path, creation_time, version=0)

        self.assertEqual(vm.get_mp4_creation_date(path), expected)

    def test_version_1_64bit(self):
        path = self.dir / "video.mov"
        expected = datetime(2021, 2, 3, 4, 5, 6)
        creation_time = int((expected - vm.MP4_EPOCH).total_seconds())
        self._write_fake_mp4(path, creation_time, version=1)

        self.assertEqual(vm.get_mp4_creation_date(path), expected)

    def test_returns_none_when_creation_time_is_zero(self):
        path = self.dir / "video.mp4"
        self._write_fake_mp4(path, creation_time=0)

        self.assertIsNone(vm.get_mp4_creation_date(path))

    def test_returns_none_for_implausible_date(self):
        # Une caméra dont l'horloge n'a jamais été réglée renvoie souvent l'époque Unix
        # (1970) : is_plausible_media_date() doit rejeter cette date, pas seulement
        # creation_time <= 0 (voir test_returns_none_when_creation_time_is_zero, un cas
        # distinct).
        path = self.dir / "video.mp4"
        creation_time = int((datetime(1970, 1, 1) - vm.MP4_EPOCH).total_seconds())
        self._write_fake_mp4(path, creation_time)

        self.assertIsNone(vm.get_mp4_creation_date(path))


class TestAviCreationDate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def _write_fake_avi(self, path, idit_text=None):
        def chunk(fourcc, data):
            header = struct.pack("<4sI", fourcc, len(data))
            return header + data + (b"\x00" if len(data) % 2 else b"")

        if idit_text is not None:
            idit_data = idit_text.encode("ascii") + b"\x00"
            info_list_content = b"INFO" + chunk(b"IDIT", idit_data)
        else:
            info_list_content = b"INFO" + chunk(b"ICMT", b"sans IDIT")
        riff_content = b"AVI " + chunk(b"LIST", info_list_content)
        path.write_bytes(chunk(b"RIFF", riff_content))

    def test_uses_idit_date(self):
        path = self.dir / "video.avi"
        expected = datetime(2022, 6, 15, 12, 0, 0)
        self._write_fake_avi(path, idit_text=expected.strftime("%a %b %d %H:%M:%S %Y"))

        self.assertEqual(vm.get_avi_creation_date(path), expected)

    def test_returns_none_without_idit(self):
        path = self.dir / "video.avi"
        self._write_fake_avi(path, idit_text=None)

        self.assertIsNone(vm.get_avi_creation_date(path))

    def test_returns_none_for_malformed_file(self):
        path = self.dir / "video.avi"
        path.write_bytes(b"RIFF....AVI LIST....")

        self.assertIsNone(vm.get_avi_creation_date(path))

    def test_returns_none_for_implausible_date(self):
        path = self.dir / "video.avi"
        self._write_fake_avi(path, idit_text=datetime(1970, 1, 1).strftime("%a %b %d %H:%M:%S %Y"))

        self.assertIsNone(vm.get_avi_creation_date(path))

    def _write_avi_with_declared_idit_size(self, path, idit_content, declared_size):
        # Contrairement à _write_fake_avi (taille déclarée = taille réelle du contenu),
        # permet de forger une taille de chunk IDIT différente du contenu réellement
        # présent — pour tester le plafond MAX_IDIT_SIZE au plus près de sa frontière.
        def chunk_header(fourcc, size):
            return struct.pack("<4sI", fourcc, size)

        idit_header = chunk_header(b"IDIT", declared_size)
        info_list_content = b"INFO" + idit_header + idit_content
        info_list_header = chunk_header(b"LIST", len(info_list_content))
        riff_content = b"AVI " + info_list_header + info_list_content
        path.write_bytes(chunk_header(b"RIFF", len(riff_content)) + riff_content)

    def test_reads_idit_at_exactly_the_max_size_boundary(self):
        path = self.dir / "video.avi"
        expected = datetime(2020, 3, 4, 5, 6, 7)
        idit_text = expected.strftime("%a %b %d %H:%M:%S %Y")
        idit_content = idit_text.encode("ascii").ljust(vm.MAX_IDIT_SIZE, b"\x00")
        self._write_avi_with_declared_idit_size(path, idit_content, vm.MAX_IDIT_SIZE)

        self.assertEqual(vm.get_avi_creation_date(path), expected)

    def test_rejects_idit_one_byte_over_the_max_size_boundary(self):
        # Contenu par ailleurs parfaitement valide (même date que le test ci-dessus) :
        # seule la taille déclarée du chunk dépasse le plafond d'un octet, pour isoler
        # la frontière exacte plutôt qu'un cas grossièrement surdimensionné.
        path = self.dir / "video.avi"
        idit_text = datetime(2020, 3, 4, 5, 6, 7).strftime("%a %b %d %H:%M:%S %Y")
        idit_content = idit_text.encode("ascii").ljust(vm.MAX_IDIT_SIZE + 1, b"\x00")
        self._write_avi_with_declared_idit_size(path, idit_content, vm.MAX_IDIT_SIZE + 1)

        self.assertIsNone(vm.get_avi_creation_date(path))


class TestWmvCreationDate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def _write_fake_wmv(self, path, creation_filetime):
        file_properties_data = (
            b"\x00" * 16
            + struct.pack("<Q", 0)
            + struct.pack("<Q", creation_filetime)
            + struct.pack("<Q", 0)
        )
        file_properties_object = (
            vm.ASF_FILE_PROPERTIES_OBJECT_GUID
            + struct.pack("<Q", 24 + len(file_properties_data))
            + file_properties_data
        )
        header_specific = struct.pack("<IH", 1, 0)  # 1 sous-objet, réservé
        header_object_content = header_specific + file_properties_object
        header_object_size = 24 + len(header_object_content)
        header = vm.ASF_HEADER_OBJECT_GUID + struct.pack("<Q", header_object_size) + header_object_content
        path.write_bytes(header)

    def test_uses_file_properties_creation_date(self):
        path = self.dir / "video.wmv"
        expected = datetime(2019, 4, 10, 8, 30, 0)
        delta = expected - vm.FILETIME_EPOCH
        creation_filetime = (delta.days * 86400 + delta.seconds) * 10_000_000
        self._write_fake_wmv(path, creation_filetime)

        self.assertEqual(vm.get_wmv_creation_date(path), expected)

    def test_returns_none_when_creation_date_is_zero(self):
        path = self.dir / "video.wmv"
        self._write_fake_wmv(path, creation_filetime=0)

        self.assertIsNone(vm.get_wmv_creation_date(path))

    def test_returns_none_for_implausible_date(self):
        path = self.dir / "video.wmv"
        delta = datetime(1970, 1, 1) - vm.FILETIME_EPOCH
        creation_filetime = (delta.days * 86400 + delta.seconds) * 10_000_000
        self._write_fake_wmv(path, creation_filetime)

        self.assertIsNone(vm.get_wmv_creation_date(path))

    def test_finds_file_properties_object_after_a_preceding_sub_object(self):
        # _write_fake_wmv() (et tous les tests ci-dessus) place toujours le File
        # Properties Object en premier (num_objects=1) : la ligne "offset += size" qui
        # saute un sous-objet non pertinent pour passer au suivant n'était donc jamais
        # exercée. Un fichier ASF réel place couramment d'autres objets avant lui
        # (Header Extension Object, notamment).
        path = self.dir / "video.wmv"
        expected = datetime(2019, 4, 10, 8, 30, 0)
        delta = expected - vm.FILETIME_EPOCH
        creation_filetime = (delta.days * 86400 + delta.seconds) * 10_000_000

        preceding_guid = bytes([0x11]) * 16  # GUID bidon, distinct de ASF_FILE_PROPERTIES_OBJECT_GUID
        preceding_payload = b"\x00" * 8
        preceding_object = preceding_guid + struct.pack("<Q", 24 + len(preceding_payload)) + preceding_payload

        file_properties_data = (
            b"\x00" * 16
            + struct.pack("<Q", 0)
            + struct.pack("<Q", creation_filetime)
            + struct.pack("<Q", 0)
        )
        file_properties_object = (
            vm.ASF_FILE_PROPERTIES_OBJECT_GUID
            + struct.pack("<Q", 24 + len(file_properties_data))
            + file_properties_data
        )
        header_specific = struct.pack("<IH", 2, 0)  # 2 sous-objets, réservé
        header_object_content = header_specific + preceding_object + file_properties_object
        header_object_size = 24 + len(header_object_content)
        header = vm.ASF_HEADER_OBJECT_GUID + struct.pack("<Q", header_object_size) + header_object_content
        path.write_bytes(header)

        self.assertEqual(vm.get_wmv_creation_date(path), expected)

    def test_terminates_quickly_when_a_sub_object_declares_a_size_of_zero(self):
        # Régression : un sous-objet déclarant une taille de 0 ne fait plus avancer
        # offset, ce qui bouclait sur place jusqu'à num_objects avant le correctif —
        # potentiellement des milliards d'itérations pour un en-tête corrompu déclarant
        # un nombre d'objets aberrant (ici 0xFFFFFFFF).
        path = self.dir / "corrupt.wmv"
        broken_sub_object = b"\x00" * 16 + struct.pack("<Q", 0)  # GUID bidon, taille déclarée 0
        header_object_content = struct.pack("<IH", 0xFFFFFFFF, 0) + broken_sub_object
        header = (
            vm.ASF_HEADER_OBJECT_GUID
            + struct.pack("<Q", 10_000_000)  # header_object_size très supérieur au fichier réel
            + header_object_content
        )
        path.write_bytes(header)

        start = time.time()
        result = vm.get_wmv_creation_date(path)
        elapsed = time.time() - start

        self.assertIsNone(result)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)


class TestMatroskaCreationDate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def _write_fake_matroska(self, path, date_utc_ns=None):
        def elem(id_bytes, payload):
            return id_bytes + bytes([0x80 | len(payload)]) + payload

        info_content = elem(b"\x44\x61", struct.pack(">q", date_utc_ns)) if date_utc_ns is not None else b""
        segment_content = elem(b"\x15\x49\xa9\x66", info_content)
        segment_elem = b"\x18\x53\x80\x67" + bytes([0x80 | len(segment_content)]) + segment_content
        ebml_header_elem = b"\x1a\x45\xdf\xa3" + bytes([0x80 | 4]) + b"\x00" * 4
        path.write_bytes(ebml_header_elem + segment_elem)

    def test_uses_date_utc(self):
        path = self.dir / "video.mkv"
        expected = datetime(2020, 3, 4, 5, 6, 7)
        delta = expected - vm.MATROSKA_DATE_UTC_EPOCH
        date_utc_ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000
        self._write_fake_matroska(path, date_utc_ns)

        self.assertEqual(vm.get_matroska_creation_date(path), expected)

    def test_returns_none_without_date_utc(self):
        path = self.dir / "video.webm"
        self._write_fake_matroska(path, date_utc_ns=None)

        self.assertIsNone(vm.get_matroska_creation_date(path))

    def test_returns_none_for_implausible_date(self):
        path = self.dir / "video.mkv"
        delta = datetime(1970, 1, 1) - vm.MATROSKA_DATE_UTC_EPOCH
        date_utc_ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000
        self._write_fake_matroska(path, date_utc_ns)

        self.assertIsNone(vm.get_matroska_creation_date(path))


class TestVideoParserLoad(unittest.TestCase):
    """Vérifie que chaque parseur saute par-dessus les données massives (mdat, movi,
    Data Object, Cluster) sans les lire, même quand le fichier fait des centaines de
    Mo : le temps d'exécution doit rester quasi constant, pas proportionnel à la taille
    du fichier. Les fichiers sont creux/sparse pour rester rapides à créer."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def test_mp4_skips_large_leading_mdat_efficiently(self):
        path = self.dir / "big_video.mp4"
        expected = datetime(2022, 1, 1, 0, 0, 0)
        creation_time = int((expected - vm.MP4_EPOCH).total_seconds())
        gap_size = 200 * 1024 * 1024

        ftyp_content = b"isom" + struct.pack(">I", 0) + b"isomiso2mp41"
        ftyp = struct.pack(">I", 8 + len(ftyp_content)) + b"ftyp" + ftyp_content
        mvhd_content = bytes([0]) + b"\x00\x00\x00" + struct.pack(">III", creation_time, 0, 600) + struct.pack(">I", 0)
        mvhd = struct.pack(">I", 8 + len(mvhd_content)) + b"mvhd" + mvhd_content
        moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
        mdat_header = struct.pack(">I", 8 + gap_size) + b"mdat"

        with open(path, "wb") as f:
            f.write(ftyp)
            f.write(mdat_header)
            f.seek(gap_size, os.SEEK_CUR)  # saute le "contenu" du mdat sans l'écrire (fichier creux)
            f.write(moov)
        self.assertGreater(path.stat().st_size, gap_size)

        start = time.time()
        date = vm.get_mp4_creation_date(path)
        elapsed = time.time() - start

        self.assertEqual(date, expected)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)

    def test_mp4_skips_large_trak_inside_moov_efficiently(self):
        # Régression : get_mp4_creation_date() bufferisait auparavant tout le contenu de
        # "moov" avant d'y chercher "mvhd" — alors que "trak" (qui grossit avec la
        # durée/résolution de la vidéo) peut peser plusieurs dizaines à centaines de Mo,
        # et se trouve dans la même boîte "moov" que "mvhd".
        path = self.dir / "big_moov.mp4"
        expected = datetime(2022, 5, 4, 3, 2, 1)
        creation_time = int((expected - vm.MP4_EPOCH).total_seconds())
        trak_size = 200 * 1024 * 1024

        ftyp_content = b"isom" + struct.pack(">I", 0) + b"isomiso2mp41"
        ftyp = struct.pack(">I", 8 + len(ftyp_content)) + b"ftyp" + ftyp_content
        mvhd_content = bytes([0]) + b"\x00\x00\x00" + struct.pack(">III", creation_time, 0, 600) + struct.pack(">I", 0)
        mvhd = struct.pack(">I", 8 + len(mvhd_content)) + b"mvhd" + mvhd_content
        trak_header = struct.pack(">I", 8 + trak_size) + b"trak"
        moov_size = 8 + len(mvhd) + len(trak_header) + trak_size

        with open(path, "wb") as f:
            f.write(ftyp)
            f.write(struct.pack(">I", moov_size) + b"moov")
            f.write(mvhd)
            f.write(trak_header)
            f.seek(trak_size - 1, os.SEEK_CUR)  # saute le "contenu" du trak sans l'écrire (fichier creux)
            f.write(b"\x00")
        self.assertGreater(path.stat().st_size, trak_size)

        start = time.time()
        date = vm.get_mp4_creation_date(path)
        elapsed = time.time() - start

        self.assertEqual(date, expected)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)

    def test_avi_skips_large_junk_list_efficiently(self):
        path = self.dir / "big_video.avi"
        expected = datetime(2021, 8, 20, 10, 0, 0)
        idit_text = expected.strftime("%a %b %d %H:%M:%S %Y")
        junk_size = 150 * 1024 * 1024  # pair, pour rester aligné sans octet de padding

        def chunk(fourcc, data):
            header = struct.pack("<4sI", fourcc, len(data))
            return header + data + (b"\x00" if len(data) % 2 else b"")

        idit_data = idit_text.encode("ascii") + b"\x00"
        info_list = chunk(b"LIST", b"INFO" + chunk(b"IDIT", idit_data))
        junk_content_size = 4 + junk_size  # listType "JUNK" + octets de bourrage
        junk_list_header = struct.pack("<4sI", b"LIST", junk_content_size)
        riff_content_size = 4 + len(junk_list_header) + junk_content_size + len(info_list)

        with open(path, "wb") as f:
            f.write(struct.pack("<4sI", b"RIFF", riff_content_size))
            f.write(b"AVI ")
            f.write(junk_list_header)
            f.write(b"JUNK")
            f.seek(junk_size, os.SEEK_CUR)  # LIST "JUNK" simule un énorme "movi" ignoré
            f.write(info_list)
        self.assertGreater(path.stat().st_size, junk_size)

        start = time.time()
        date = vm.get_avi_creation_date(path)
        elapsed = time.time() - start

        self.assertEqual(date, expected)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)

    def test_avi_rejects_an_oversized_idit_chunk_without_reading_it(self):
        # Régression : contrairement au LIST "JUNK" ci-dessus (jamais lu, seulement
        # sauté via sa taille déclarée), le chunk IDIT lui-même était intégralement lu
        # en mémoire selon sa taille déclarée dans l'en-tête RIFF — un fichier corrompu
        # ou forgé pouvait y déclarer une taille couvrant tout le fichier.
        #
        # Une date parfaitement valide et exploitable est placée en tête du chunk
        # IDIT, avant le padding creux qui simule sa taille énorme : sans le plafond
        # MAX_IDIT_SIZE, le parseur la lirait et la retournerait normalement (comme
        # test_uses_idit_date), ce qui prouverait que le test ne détecterait pas une
        # régression du plafond. Avec le plafond, la taille déclarée du chunk (bien
        # plus grande que MAX_IDIT_SIZE) le fait rejeter avant toute lecture, quelle
        # que soit la validité du contenu qu'il contient réellement.
        path = self.dir / "forged_idit.avi"
        idit_text = datetime(2021, 8, 20, 10, 0, 0).strftime("%a %b %d %H:%M:%S %Y")
        idit_data = idit_text.encode("ascii") + b"\x00"
        idit_size = 150 * 1024 * 1024  # pair, pour rester aligné sans octet de padding

        def chunk_header(fourcc, size):
            return struct.pack("<4sI", fourcc, size)

        idit_header = chunk_header(b"IDIT", idit_size)
        info_list_content_size = 4 + len(idit_header) + idit_size  # "INFO" + en-tête + contenu IDIT
        info_list_header = chunk_header(b"LIST", info_list_content_size)
        riff_content_size = 4 + len(info_list_header) + info_list_content_size

        with open(path, "wb") as f:
            f.write(chunk_header(b"RIFF", riff_content_size))
            f.write(b"AVI ")
            f.write(info_list_header)
            f.write(b"INFO")
            f.write(idit_header)
            f.write(idit_data)  # date valide en tête du chunk IDIT (voir commentaire ci-dessus)
            f.seek(idit_size - len(idit_data) - 1, os.SEEK_CUR)  # reste du chunk : creux, jamais écrit
            f.write(b"\x00")
        self.assertGreater(path.stat().st_size, idit_size)

        start = time.time()
        date = vm.get_avi_creation_date(path)
        elapsed = time.time() - start

        self.assertIsNone(date)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)

    def test_wmv_ignores_large_trailing_data_object_efficiently(self):
        path = self.dir / "big_video.wmv"
        expected = datetime(2019, 4, 10, 8, 30, 0)
        delta = expected - vm.FILETIME_EPOCH
        creation_filetime = (delta.days * 86400 + delta.seconds) * 10_000_000
        tail_size = 150 * 1024 * 1024

        file_properties_data = (
            b"\x00" * 16 + struct.pack("<Q", 0) + struct.pack("<Q", creation_filetime) + struct.pack("<Q", 0)
        )
        file_properties_object = (
            vm.ASF_FILE_PROPERTIES_OBJECT_GUID
            + struct.pack("<Q", 24 + len(file_properties_data))
            + file_properties_data
        )
        header_object_content = struct.pack("<IH", 1, 0) + file_properties_object
        header = vm.ASF_HEADER_OBJECT_GUID + struct.pack("<Q", 24 + len(header_object_content)) + header_object_content

        with open(path, "wb") as f:
            f.write(header)
            f.seek(tail_size, os.SEEK_CUR)  # simule le "Data Object" (audio/vidéo) qui suit
            f.write(b"\x00")
        self.assertGreater(path.stat().st_size, tail_size)

        start = time.time()
        date = vm.get_wmv_creation_date(path)
        elapsed = time.time() - start

        self.assertEqual(date, expected)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)

    def test_matroska_ignores_file_size_beyond_read_cap(self):
        path = self.dir / "big_video.mkv"
        expected = datetime(2020, 6, 1, 12, 0, 0)
        delta = expected - vm.MATROSKA_DATE_UTC_EPOCH
        date_utc_ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000
        tail_size = 150 * 1024 * 1024

        def elem(id_bytes, payload):
            return id_bytes + bytes([0x80 | len(payload)]) + payload

        info_content = elem(b"\x44\x61", struct.pack(">q", date_utc_ns))
        segment_content = elem(b"\x15\x49\xa9\x66", info_content)
        segment_elem = b"\x18\x53\x80\x67" + bytes([0x80 | len(segment_content)]) + segment_content
        ebml_header_elem = b"\x1a\x45\xdf\xa3" + bytes([0x80 | 4]) + b"\x00" * 4

        with open(path, "wb") as f:
            f.write(ebml_header_elem)
            f.write(segment_elem)
            f.seek(tail_size, os.SEEK_CUR)  # simule les Cluster (audio/vidéo) qui suivent Info
            f.write(b"\x00")
        self.assertGreater(path.stat().st_size, tail_size)

        start = time.time()
        date = vm.get_matroska_creation_date(path)
        elapsed = time.time() - start

        self.assertEqual(date, expected)
        self.assertLess(elapsed, MAX_SECONDS_LARGE_FILE)


if __name__ == "__main__":
    unittest.main()
