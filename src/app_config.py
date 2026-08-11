"""Journalisation et préférences utilisateur : deux préoccupations orthogonales à la
logique de tri/copie/hash (media_sorter.py), qui n'ont pas de raison d'y vivre — media_sorter.py
importe ce module comme n'importe quel autre consommateur (voir son usage de
app_config.logger), au même titre qu'app_ui.py et cli.py.

Copyright (C) 2026 Guillaume Pataut
Logiciel libre distribué sous licence GNU GPL v3 (ou ultérieure) — voir le fichier
LICENSE à la racine du dépôt, ou <https://www.gnu.org/licenses/>.
"""

import json
import logging
import os
import sys
from pathlib import Path


def _log_directory() -> Path:
    """Dossier de logs, selon la plateforme (Windows : %LOCALAPPDATA%/TriPhotos ; macOS :
    ~/Library/Logs/TriPhotos ; Linux/autre : ~/.local/share/TriPhotos).

    TRIPHOTOS_LOG_DIR, si définie, prend le pas sur tout le reste : utilisée par les
    tests pour ne jamais écrire dans le vrai dossier de logs de la machine qui les
    exécute (voir tests/test_load.py, test_media_sorter.py, test_app_ui.py).
    """
    override = os.environ.get("TRIPHOTOS_LOG_DIR")
    if override:
        return Path(override)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "TriPhotos"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "TriPhotos"
    return Path.home() / ".local" / "share" / "TriPhotos"


def _configure_logging() -> logging.Logger:
    """Journalise dans un fichier local les exceptions inattendues rencontrées pendant
    l'analyse ou la copie (voir media_sorter.get_media_date/copy_files, et
    app_ui._scan_worker/_copy_worker) : sans ça, un fichier classé à tort dans No Info ou
    une copie en échec ne laisse aucune trace au-delà du message affiché une fois à
    l'écran, rendant tout diagnostic a posteriori impossible (bug de parseur ? fichier
    réellement corrompu ? accès disque ?).

    N'affecte jamais le comportement utilisateur existant (classement en No Info, message
    d'erreur) : purement un journal de diagnostic en plus. Si le dossier de logs n'est pas
    accessible en écriture, l'application continue simplement sans journalisation plutôt
    que d'échouer pour cette seule raison.
    """
    logger = logging.getLogger("triphotos")
    logger.setLevel(logging.WARNING)
    try:
        log_dir = _log_directory()
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "triphotos.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        pass
    return logger


logger = _configure_logging()


def _config_directory() -> Path:
    """Dossier de configuration utilisateur, même logique multi-plateforme que
    _log_directory (override via TRIPHOTOS_CONFIG_DIR pour les tests, afin de ne jamais
    lire/écrire les vraies préférences de la machine qui les exécute).
    """
    override = os.environ.get("TRIPHOTOS_CONFIG_DIR")
    if override:
        return Path(override)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "TriPhotos"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TriPhotos"
    return Path.home() / ".local" / "share" / "TriPhotos"


PREFERENCES_FILE_NAME = "preferences.json"
DEFAULT_PREFERENCES = {"sort_level": "jour", "separate_media": False, "copy_mode": "copier"}


def load_preferences(valid_sort_levels) -> dict:
    """Charge le niveau de tri, la séparation Photos/Vidéos et le mode copier/déplacer
    choisis lors du dernier lancement, pour ne pas avoir à les reconfigurer à chaque
    fois. Retourne les valeurs par défaut si le fichier n'existe pas encore, ou si son
    contenu est illisible/corrompu/invalide : ce réglage est un simple confort, jamais
    une condition requise pour utiliser l'application.

    valid_sort_levels (ex. media_sorter.SORT_LEVELS) est fourni par l'appelant plutôt que
    codé en dur ici : ce module ne connaît rien du domaine métier (niveaux de tri), il ne
    fait que persister/valider des valeurs dont la liste légale lui vient de l'extérieur —
    évite aussi une dépendance circulaire avec media_sorter.py, qui importe ce module-ci
    pour son logger.
    """
    preferences = dict(DEFAULT_PREFERENCES)
    path = _config_directory() / PREFERENCES_FILE_NAME
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return preferences
    if not isinstance(saved, dict):
        return preferences
    if saved.get("sort_level") in valid_sort_levels:
        preferences["sort_level"] = saved["sort_level"]
    if isinstance(saved.get("separate_media"), bool):
        preferences["separate_media"] = saved["separate_media"]
    if saved.get("copy_mode") in ("copier", "deplacer"):
        preferences["copy_mode"] = saved["copy_mode"]
    return preferences


def save_preferences(sort_level: str, separate_media: bool, copy_mode: str) -> None:
    """Sauvegarde le niveau de tri, la séparation Photos/Vidéos et le mode
    copier/déplacer pour le prochain lancement. Échoue silencieusement si le dossier de
    configuration n'est pas accessible en écriture (voir load_preferences) : ce n'est
    qu'un confort, pas une fonctionnalité critique.
    """
    config_dir = _config_directory()
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / PREFERENCES_FILE_NAME
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"sort_level": sort_level, "separate_media": separate_media, "copy_mode": copy_mode},
                f,
            )
    except OSError:
        pass
