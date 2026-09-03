"""
Launcher do PAC CT -- Protection, Automation & Control Commissioning Toolkit
(multi-ferramenta web).

Estrutura do projeto (resumo -- o mapa completo esta no README):
    pac-ct/
    +-- app.py                  <-- voce esta aqui (launcher + bootstrap do venv)
    +-- config/config.ini       (IP e senhas do rele; NAO versionado, semeado
    |                            do .example no primeiro boot)
    +-- pacct/                 (todo o codigo primeiro-parte)
    |   +-- paths.py            (constantes de path -- use sempre daqui)
    |   +-- core/               (modelos de rele, TARGET, SELOGIC, wordbits)
    |   +-- parsers/            (gle, rdb, scd, set_dnp, ole_rebuild)
    |   +-- matchers/           (cross-match RDB <-> SCD)
    |   +-- cli/runner.py       (modo CLI: polling no terminal)
    |   +-- web/
    |       +-- dashboard.py    (home + main(): monta as ferramentas)
    |       +-- mount.py        (UM servidor, roteando por prefixo de caminho)
    |       +-- session.py      (sessao por visitante, cookie `selsid`)
    |       +-- rdb_write.py    (o unico lugar que grava bytes num RDB)
    |       +-- project_files/  (Arquivos do Projeto: a unica tela com upload)
    |       +-- glv/ dnp_map/ vb_updater/ vlan_mapper/ gle_exporter/
    |       +-- settings_compare/  themes/  progress.py
    +-- data/relay_models/      (perfil por modelo de rele)
    +-- data/wordbits/          (nomes validos da Relay Word, por modelo)
    +-- tests/                  (pytest)
    +-- cache/                  (runtime: FID, cache de RDB por conteudo, sessoes)
    +-- samples/  docs/         (exemplos; manuais e perfis DNP3)
    +-- selprotopy/             (biblioteca MIT patcheada -- nao edite)

As ferramentas NAO sobem uma de cada vez: `mount.py` poe todas no ar na mesma
porta, cada uma num prefixo (`/glv/`, `/vb-updater/`, `/dnp-map/`, ...).

Modos:
    python3 app.py                       # GLV CLI (polling no terminal)
    python3 app.py --web                  # PAC CT web (menu de ferramentas)
    python3 app.py --web --port 9000      # Porta customizada
    python3 app.py --config outro.ini    # Outro arquivo de configuracao
    python3 app.py --skip-install        # Pula verificacao de dependencias
    python3 app.py --no-venv             # Nao usa virtualenv
"""

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# O pacote mora em `src/pacct/`; o `selprotopy` vendorizado continua na raiz.
# Os dois precisam entrar no sys.path, e sao diretorios diferentes desde que o
# projeto passou a usar o layout `src/`.
SRC_DIR = ROOT / "src"
REQ_FILE = ROOT / "requirements.txt"

VENV_DIR = ROOT / ".venv"
if os.name == "nt":
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"

# nome do pacote no pip -> modulo importavel
IMPORT_NAMES = {
    "pyserial": "serial",
    "telnetlib3": "telnetlib3",
}


# -----------------------------------------------------------------------------
# Virtualenv bootstrap
# -----------------------------------------------------------------------------

def is_inside_target_venv() -> bool:
    # Compara PREFIXOS, nunca os executaveis. `.venv/bin/python` e' um symlink
    # que aponta de volta pro interpretador base, entao
    # `VENV_PYTHON.resolve()` da `/usr/bin/pythonX.Y` -- exatamente o que
    # `sys.executable` resolve rodando FORA do venv. A comparacao dava True
    # fora dele, a app pulava o relaunch e tentava instalar as dependencias no
    # Python do sistema, onde o PEP 668 barra e o boot morre. `sys.prefix`
    # aponta pro venv quando se esta dentro e pro base quando nao, que e'
    # justamente a pergunta.
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
    # No Windows, os.execv monta a linha de comando concatenando os argumentos
    # com espacos e SEM aspas. Se o projeto estiver num caminho com espaco
    # ("06 - Ferramentas"), o Python filho recebe os pedacos como argumentos
    # separados -- e um token "-" solto vira "leia o script do stdin", que abre
    # o REPL em vez de rodar o app. subprocess recebe uma lista e faz o quoting
    # correto, entao usamos ele nessa plataforma.
    if os.name == "nt":
        try:
            sys.exit(subprocess.call(args))
        except KeyboardInterrupt:
            # Ctrl+C chega nos dois processos; o filho ja tratou e saiu.
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
        # Referencia direta do PEP 508 (`nome @ git+https://...`): o nome do
        # pacote e' o que vem ANTES do '@'. Sem esta linha o "pacote" seria a
        # URL inteira, `missing_packages` nunca conseguiria importa-lo, e o
        # `pip install -r` rodaria (re-clonando o repositorio) a cada boot.
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
    """Instala o que falta -- ou, com `upgrade`, puxa as versoes novas.

    O boot normal so' roda o pip quando algum pacote nao IMPORTA. E' o que
    mantem o `--web` utilizavel numa subestacao sem internet: uma verificacao
    de versao a cada boot faria o pip falhar sem rede, e o launcher sai no
    erro do pip. Atualizar e' um pedido explicito (`--atualizar-deps`), e ai'
    sim vale `--pre`: a py61850 e' publicada como pre-release enquanto a
    0.2.0 final nao sai (ver requirements.txt).
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
