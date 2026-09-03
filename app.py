"""
Launcher for PAC CT -- Protection, Automation & Control Commissioning Toolkit.

Project layout (a summary; the full map is in the README):
    pac-ct/
    +-- app.py                  <-- you are here (launcher + venv bootstrap)
    +-- config/config.ini       (the relay's IP and passwords; NOT versioned,
    |                            seeded from the .example on first boot)
    +-- src/pacct/              (this application's own code)
    |   +-- paths.py            (path constants -- always resolve from here)
    |   +-- core/               (what talks to a relay: relay_conn, TARGET)
    |   +-- cli/runner.py       (CLI mode: polling in the terminal)
    |   +-- web/
    |       +-- dashboard.py    (the home + main(): mounts the tools)
    |       +-- mount.py        (ONE server, routed by path prefix)
    |       +-- session.py      (per-visitor session, cookie `selsid`)
    |       +-- rdb_write.py    (the only place that writes bytes into an RDB)
    |       +-- project_files/  (Arquivos do Projeto: the only screen with an
    |       |                    upload)
    |       +-- glv/ dnp_map/ vb_updater/ vlan_mapper/ gle_exporter/
    |       +-- settings_compare/  themes/  progress.py
    +-- data/                   (overlay for user-supplied model data; starts
    |                            out absent, see paths.py)
    +-- tests/                  (pytest)
    +-- cache/                  (runtime: FID cache, content-addressed RDB
    |                            cache, sessions)
    +-- samples/  docs/         (examples; documentation)
    +-- selprotopy/             (vendored, patched MIT library -- do not edit)

The file formats live in two libraries extracted from this project, not here:
`selfiles` (RDB, SET_*.TXT, DNP maps, GLE, SELOGIC, IEC 61850 SCL, and the
per-model registries) and `cfbwrite` (the Compound File writer).

The tools do NOT come up one at a time: `mount.py` puts them all on the same
port, each under its own prefix (`/glv/`, `/vb-updater/`, `/dnp-map/`, ...).

Modes:
    python3 app.py                        # GLV CLI (polling in the terminal)
    python3 app.py --web                  # PAC CT web (the tool menu)
    python3 app.py --web --port 9000      # a custom port
    python3 app.py --config other.ini     # another configuration file
    python3 app.py --skip-install         # skip the dependency check
    python3 app.py --no-venv              # do not use a virtualenv
"""

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# The package lives in `src/pacct/`; the vendored `selprotopy` stays at the
# root. Both have to go on sys.path, and they have been different directories
# since the project moved to the `src/` layout.
SRC_DIR = ROOT / "src"
REQ_FILE = ROOT / "requirements.txt"

VENV_DIR = ROOT / ".venv"
if os.name == "nt":
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"

# pip package name -> the module you can actually import
IMPORT_NAMES = {
    "pyserial": "serial",
    "telnetlib3": "telnetlib3",
}


# -----------------------------------------------------------------------------
# Virtualenv bootstrap
# -----------------------------------------------------------------------------

def is_inside_target_venv() -> bool:
    # Compare PREFIXES, never the executables. `.venv/bin/python` is a
    # symlink back to the base interpreter, so `VENV_PYTHON.resolve()` gives
    # `/usr/bin/pythonX.Y` -- exactly what `sys.executable` resolves to when
    # running OUTSIDE the venv. The comparison was therefore True outside it,
    # the app skipped the relaunch and tried to install the dependencies into
    # the system Python, where PEP 668 refuses and the boot dies. `sys.prefix`
    # points at the venv when you are inside one and at the base when you are
    # not, which is precisely the question being asked.
    try:
        return Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        return False


def create_venv() -> bool:
    if VENV_PYTHON.exists():
        return True
    print(f"[INFO] Criando virtualenv em {VENV_DIR}...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "venv", str(VENV_DIR)],
            stderr=subprocess.STDOUT,
        )
        subprocess.check_call(
            [str(VENV_PYTHON), "-m", "pip", "install", "--quiet", "--upgrade", "pip"]
        )
        return True
    except subprocess.CalledProcessError:
        import shutil
        if VENV_DIR.exists():
            shutil.rmtree(VENV_DIR, ignore_errors=True)
        print("[AVISO] Nao foi possivel criar virtualenv.")
        print("[AVISO] Para venv completo, instale: sudo apt install python3.12-venv")
        print("[AVISO] Continuando com --break-system-packages.")
        return False


def relaunch_in_venv() -> None:
    print(f"[INFO] Re-executando dentro do venv ({VENV_PYTHON})...")
    args = [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
    # On Windows, os.execv builds the command line by joining the arguments
    # with spaces and NO quoting. If the project sits in a path containing a
    # space ("06 - Ferramentas"), the child Python receives the pieces as
    # separate arguments -- and a bare "-" token means "read the script from
    # stdin", which opens the REPL instead of running the app. subprocess
    # takes a list and quotes it correctly, so that is what we use here.
    if os.name == "nt":
        try:
            sys.exit(subprocess.call(args))
        except KeyboardInterrupt:
            # Ctrl+C reaches both processes; the child has already handled
            # it and exited.
            sys.exit(130)
    os.execv(str(VENV_PYTHON), args)


# -----------------------------------------------------------------------------
# Dependencias
# -----------------------------------------------------------------------------

def parse_requirements(req_file: Path) -> list[str]:
    if not req_file.is_file():
        return []
    pkgs = []
    for raw in req_file.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # A PEP 508 direct reference (`name @ git+https://...`): the package
        # name is what comes BEFORE the '@'. Without this line the "package"
        # would be the whole URL, `missing_packages` could never import it, and
        # `pip install -r` would run -- re-cloning the repository -- on every
        # boot.
        name = line.split(" @ ", 1)[0].strip() if " @ " in line else line
        for sep in ("==", ">=", "<=", "~=", ">", "<", "!="):
            if sep in name:
                name = name.split(sep, 1)[0]
                break
        pkgs.append(name.strip())
    return pkgs


def missing_packages(pkgs: list[str]) -> list[str]:
    missing = []
    for pkg in pkgs:
        module = IMPORT_NAMES.get(pkg, pkg).replace("-", "_")
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(pkg)
    return missing


def install_requirements(allow_break: bool = False,
                         upgrade: bool = False) -> None:
    """Install what is missing -- or, with `upgrade`, pull the newer versions.

    A normal boot runs pip only when a package fails to IMPORT. That is what
    keeps `--web` usable in a substation with no internet: a version check on
    every boot would make pip fail without a network, and the launcher exits on
    pip's error. Updating is an explicit request (`--atualizar-deps`), and
    there `--pre` earns its place: py61850 is published as a pre-release while
    0.2.0 final does not exist (see requirements.txt).
    """
    if not REQ_FILE.is_file():
        print(f"[AVISO] requirements.txt nao encontrado em {REQ_FILE}")
        return
    pkgs = parse_requirements(REQ_FILE)
    missing = missing_packages(pkgs)
    if not missing and not upgrade:
        print("[OK] Todas as dependencias ja estao instaladas.")
        return
    if upgrade:
        print("[INFO] Atualizando dependencias (inclusive pre-releases)...")
    else:
        print(f"[INFO] Instalando dependencias ausentes: {', '.join(missing)}")
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(REQ_FILE)]
    if upgrade:
        cmd.extend(["--pre", "--upgrade"])
    if allow_break:
        cmd.append("--break-system-packages")
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as exc:
        sys.exit(f"[ERRO] Falha ao instalar dependencias (codigo {exc.returncode}).")


# -----------------------------------------------------------------------------
# Execucao
# -----------------------------------------------------------------------------

def _ensure_import_path() -> None:
    """`src/` para o `pacct`, a raiz para o `selprotopy` vendorizado."""
    for d in (SRC_DIR, ROOT):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))


def run_cli(extra_args: list[str]) -> None:
    _ensure_import_path()
    sys.argv = ["pacct.cli.runner", *extra_args]
    print("[INFO] Iniciando CLI (pacct.cli.runner)...")
    print("-" * 60)
    from pacct.cli.runner import main as cli_main
    cli_main()


def run_web(extra_args: list[str], port: int) -> None:
    _ensure_import_path()
    sys.argv = ["pacct.web.dashboard", *extra_args, "--port", str(port)]
    print(f"[INFO] Iniciando dashboard web na porta {port}...")
    print(f"[INFO] Abra http://localhost:{port}/ no navegador")
    print("-" * 60)
    from pacct.web.dashboard import main as web_main
    web_main()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launcher do PAC CT (venv + dependencias + CLI/dashboard)"
    )
    parser.add_argument("--skip-install", action="store_true",
                        help="Nao verifica/instala dependencias")
    parser.add_argument("--atualizar-deps", "--update-deps", dest="update_deps",
                        action="store_true",
                        help="Puxa as versoes novas das dependencias "
                             "(inclusive pre-releases) e sai")
    parser.add_argument("--no-venv", action="store_true",
                        help="Nao usa virtualenv (--break-system-packages)")
    parser.add_argument("--config",
                        help="Caminho para um config.ini alternativo")
    parser.add_argument("--web", action="store_true",
                        help="Roda dashboard web (em vez do CLI)")
    parser.add_argument("--port", type=int, default=8765,
                        help="Porta do dashboard web (default 8765)")
    args, unknown = parser.parse_known_args()

    use_break_system = args.no_venv
    if not args.no_venv and not is_inside_target_venv():
        if create_venv():
            relaunch_in_venv()
        else:
            use_break_system = True

    if args.update_deps:
        install_requirements(allow_break=use_break_system, upgrade=True)
        return
    if not args.skip_install:
        install_requirements(allow_break=use_break_system)

    extra = list(unknown)
    if args.config:
        extra.extend(["--config", args.config])

    if args.web:
        run_web(extra, args.port)
    else:
        run_cli(extra)


if __name__ == "__main__":
    main()
