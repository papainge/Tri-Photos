"""Interface en ligne de commande : analyse un ou plusieurs dossiers sources et
transfère les fichiers trouvés vers un dossier de destination, sans passer par
l'interface Tkinter (app_ui.py). Pensée pour l'automatisation (tâche planifiée,
sauvegarde régulière d'une carte SD...) : réutilise scan_media/copy_files tels quels,
déjà découplés de l'UI (voir media_sorter.py).

Important : TriPhotos.exe est construit avec --windowed (voir packaging/build.bat), donc
sans console attachée — les messages ci-dessous n'apparaîtraient nulle part si on
l'invoquait avec des arguments. Cette CLI est destinée à être lancée via l'interpréteur
Python (`python src/media_sorter.py --source ... --dest ...`), pas via l'exécutable
Windows (voir le README).

Copyright (C) 2026 Guillaume Pataut
Logiciel libre distribué sous licence GNU GPL v3 (ou ultérieure) — voir le fichier
LICENSE à la racine du dépôt, ou <https://www.gnu.org/licenses/>.
"""

import argparse
import sys
from pathlib import Path

import media_sorter


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="TriPhotos",
        description=(
            "Trie des photos et vidéos par date, sans interface graphique "
            "(automatisation : tâche planifiée, sauvegarde de carte SD...)."
        ),
    )
    parser.add_argument(
        "--source", "-s", action="append", required=True, metavar="DOSSIER",
        help="Dossier source à analyser (répéter l'option pour plusieurs dossiers).",
    )
    parser.add_argument("--dest", "-d", required=True, metavar="DOSSIER", help="Dossier de destination.")
    parser.add_argument(
        "--level", choices=sorted(media_sorter.SORT_LEVELS), default="jour",
        help="Niveau de tri de l'arborescence de destination (défaut : jour).",
    )
    parser.add_argument(
        "--mode", choices=("copier", "deplacer"), default="copier",
        help="Copier (originaux conservés) ou déplacer (originaux supprimés) (défaut : copier).",
    )
    parser.add_argument(
        "--separate-media", action="store_true",
        help="Sépare Photos et Vidéos à la racine de la destination.",
    )
    parser.add_argument(
        "--recursive", action=argparse.BooleanOptionalAction, default=True,
        help="Inclut les sous-dossiers des sources (défaut : activé ; désactiver avec --no-recursive).",
    )
    parser.add_argument(
        "--rename", action="store_true",
        help="Renomme les fichiers transférés selon leur date (AAAA-MM-JJ_HHMMSS).",
    )
    parser.add_argument(
        "--filename-fallback", action="store_true",
        help="À défaut de métadonnée exploitable, tente de détecter une date dans le nom du fichier.",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="Ne demande pas de confirmation avant de transférer (nécessaire pour l'automatisation).",
    )
    return parser


def run_cli(argv) -> int:
    """Exécute la CLI et renvoie le code de sortie du processus (0 = succès, 1 = erreur
    ou annulation) — pour que la tâche planifiée appelante puisse détecter un échec.
    """
    args = build_arg_parser().parse_args(argv)

    source_paths = [Path(s) for s in args.source]
    for source_path in source_paths:
        if not source_path.is_dir():
            print(f"Erreur : le dossier source n'existe pas : {source_path}", file=sys.stderr)
            return 1

    dest_path = Path(args.dest)
    # Même garde-fou que MediaSorterApp.start_copy (voir app_ui.py, media_sorter.py) :
    # comparé aux dossiers sources demandés, pas au contenu de destination_map, pour
    # rejeter le cas avant même de lancer une analyse potentiellement longue.
    if media_sorter.is_destination_nested_in_sources(dest_path, source_paths):
        print(
            "Erreur : le dossier de destination ne peut pas être un dossier source, ni un de ses sous-dossiers.",
            file=sys.stderr,
        )
        return 1

    print(f"Analyse de {len(source_paths)} dossier(s) source(s)...")
    try:
        tree = media_sorter.scan_media(
            source_paths, recursive=args.recursive, use_filename_fallback=args.filename_fallback,
        )
    except OSError as exc:
        print(f"Erreur lors de l'analyse : {exc}", file=sys.stderr)
        return 1

    destination_map = media_sorter.build_destination_map(tree, args.level, args.separate_media)
    total = sum(len(files) for files in destination_map.values())
    if total == 0:
        print("Aucune photo ou vidéo trouvée.")
        return 0

    verb = "déplacer" if args.mode == "deplacer" else "copier"
    print(f"{total} fichier(s) trouvé(s) à {verb} vers {dest_path}.")
    if not args.force:
        try:
            answer = input(f"Confirmer ({verb}) ? [o/N] ").strip().lower()
        except EOFError:
            # Aucune entrée disponible (tâche planifiée, --force oublié) : on refuse
            # plutôt que de rester bloqué indéfiniment ou de lever une exception brute.
            answer = ""
        if answer not in ("o", "oui", "y", "yes"):
            print("Annulé.")
            return 1

    try:
        done, duplicates, errors = media_sorter.copy_files(
            destination_map, dest_path, args.mode,
            rename_files=args.rename, use_filename_fallback=args.filename_fallback,
        )
    except Exception as exc:
        # copy_files() protège déjà chaque fichier et chaque dossier de destination
        # individuellement (voir son docstring) : ce filet ne se déclencherait qu'en cas
        # de bug véritablement imprévu. Justement le pire endroit pour ne pas en avoir —
        # le CLI cible l'automatisation non surveillée (tâche planifiée), où une trace
        # Python brute non journalisée ne serait vue par personne (voir app_ui._copy_worker,
        # qui journalise déjà ce même cas côté GUI).
        media_sorter.logger.exception("Échec de la copie vers %s", dest_path)
        print(f"Erreur lors de la copie : {exc}", file=sys.stderr)
        return 1

    transferred = done - duplicates - len(errors)
    action_past = "déplacé(s)" if args.mode == "deplacer" else "copié(s)"
    dup_text = f"{duplicates} doublon(s) {'supprimé(s) de la source' if args.mode == 'deplacer' else 'ignoré(s)'}"
    print(f"Terminé : {transferred} fichier(s) {action_past}, {dup_text}.")
    if errors:
        print(f"{len(errors)} erreur(s) :", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0
