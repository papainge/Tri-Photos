"""Corrige un bug de PyInstaller avec les installations Python (3.13+) dont Tcl/Tk 9
embarque sa bibliotheque directement dans la DLL (zipfs) plutot que sur disque : le
hook runtime de PyInstaller pour tkinter leve une FileNotFoundError car il s'attend a
trouver un dossier de donnees Tcl/Tk extrait, qui n'existe jamais dans ce cas. Tcl
retrouve pourtant tout seul sa bibliotheque embarquee, comme lorsque l'application
tourne depuis les sources : on rend donc cette verification non bloquante.
"""

from pathlib import Path

import PyInstaller

HOOK_PATH = Path(PyInstaller.__file__).parent / "hooks" / "rthooks" / "pyi_rth__tkinter.py"

OLD = '''    if os.path.isdir(tcldir):
        os.environ["TCL_LIBRARY"] = tcldir
    elif not is_darwin:
        raise FileNotFoundError('Tcl data directory "%s" not found.' % tcldir)

    if os.path.isdir(tkdir):
        os.environ["TK_LIBRARY"] = tkdir
    elif not is_darwin:
        raise FileNotFoundError('Tk data directory "%s" not found.' % tkdir)'''

NEW = '''    if os.path.isdir(tcldir):
        os.environ["TCL_LIBRARY"] = tcldir

    if os.path.isdir(tkdir):
        os.environ["TK_LIBRARY"] = tkdir'''

content = HOOK_PATH.read_text(encoding="utf-8")
if OLD in content:
    HOOK_PATH.write_text(content.replace(OLD, NEW), encoding="utf-8")
    print("Correctif Tcl/Tk applique.")
else:
    print("Correctif Tcl/Tk deja applique (ou non necessaire).")
