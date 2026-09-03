"""The project's canonical paths.

Everything in the app resolves paths from the constants here -- never with a
`Path(__file__).parent` of its own. When the layout changes, this is the only
file that has to.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

# The PACKAGE's directory. Everything that travels INSIDE it -- the .html
# templates, the .woff2 fonts -- comes from here rather than from the project
# root: under `src/` (and more so after a `pip install`) the package and the
# root stopped being siblings.
PACKAGE_DIR: Path = Path(__file__).resolve().parent


def _find_project_root() -> Path:
    """The PROJECT's root: where `config/`, `cache/`, `rdbs/` and the data overlay
    live.

    No longer "the package's parent". Under the `src/` layout the parent is
    `src/`; after a `pip install` it is `site-packages`. So we look upward from
    the package for a marker, and `PACCT_ROOT` in the environment beats
    everything -- which is what a versioned install uses, with `current/`
    pointing at one version and the engineer's data outside it.
    """
    env = os.environ.get("PACCT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # The marker is `config/config.ini.example` plus `app.py`: both exist in
    # every install and every clone. It is no longer `data/` -- the per-model
    # registries changed owner to the `selfiles` library, and the directory
    # left here is an overlay that starts out absent.
    for parent in PACKAGE_DIR.parents:
        if (parent / "config" / "config.ini.example").is_file() \
                and (parent / "app.py").is_file():
            return parent
    return PACKAGE_DIR.parent


PROJECT_ROOT: Path = _find_project_root()

# Where MUTABLE state lives (config.ini with the passwords, the caches, the
# RDBs). Kept apart from the root because a versioned install swaps versions on
# every update and these files must not travel with it: `PACCT_DATA_DIR` keeps
# them outside.
DATA_ROOT: Path = Path(
    os.environ.get("PACCT_DATA_DIR") or PROJECT_ROOT
).expanduser().resolve()

# Configuration (config.ini and friends). `config.ini` is NOT versioned: it
# is where a relay's real ACC/2AC passwords are typed, and a substation
# password committed to git is in history for ever. What goes to git is the
# `.example`, and the app seeds one from the other on first boot
# (`ensure_config_file`).
CONFIG_DIR: Path = DATA_ROOT / "config"
DEFAULT_CONFIG_FILE: Path = CONFIG_DIR / "config.ini"

# The MODEL, on the other hand, belongs to the VERSION and not to the data:
# it is a file this repository ships, it is the marker `_find_project_root`
# looks for, and a new version may add settings to it. Resolving it under
# DATA_ROOT was wrong the moment the two roots could differ -- in a versioned
# install `userdata/config/` holds `config.ini` and nothing else, so a fresh
# install found no model, `ensure_config_file` raised, and the app refused to
# boot with "Nao ha modelo para copiar". Measured on a real bundle installed
# into PAC-CT/versions/<v>/: the dashboard died before opening the port.
EXAMPLE_CONFIG_FILE: Path = PROJECT_ROOT / "config" / "config.ini.example"

# The per-model data OVERLAY. The registries themselves (relay profiles, the
# valid Relay Word names, the bit -> MMS item tables) belong to the `selfiles`
# LIBRARY and travel inside it: they are knowledge about relays, not
# configuration of this app. What lives here is whatever the USER added at
# runtime -- the DNP map editor's "Importar perfil DNP" writes a
# `wordbits/<MODEL>.json` here -- and the overlay is searched BEFORE the
# packaged files, per model. On a fresh install it does not exist yet, and
# that is the normal state.
#
# It sits under DATA_ROOT rather than under the project root because it is the
# engineer's data: an update swaps the installed version and must not carry
# away the profile they imported.
DATA_DIR: Path = DATA_ROOT / "data"
RELAY_MODELS_DIR: Path = DATA_DIR / "relay_models"
WORDBITS_DIR: Path = DATA_DIR / "wordbits"
MMS_MAP_DIR: Path = DATA_DIR / "mms_map"

# Static files the web dispatcher serves at /static/ (the embedded .woff2
# fonts, their licences and the NOTICE). They ship with the project on
# purpose: a substation may have no internet, and no page may ask a CDN for a
# font.
STATIC_DIR: Path = PACKAGE_DIR / "web" / "static"

# The local cache (TARGET bit discovery, diagnostic dumps, the dashboard's
# annotations). Its contents are not versioned.
CACHE_DIR: Path = DATA_ROOT / "cache"

# RDB extractions, keyed by the file's sha256. Two identical files ARE the
# same file, so an extraction is unique in the process and survives a restart
# -- unlike cache/sessions/, which is wiped at start-up.
RDB_CACHE_DIR: Path = CACHE_DIR / "rdb"

# The GLV's HTML templates. Real .html files rather than strings in a .py,
# because ~1,400 of their 2,500 lines are JavaScript: this way an editor
# colours them and a linter can see them. The substitution mechanism
# ("${PAGES_JSON}") did not change.
GLV_TEMPLATES_DIR: Path = PACKAGE_DIR / "web" / "glv" / "templates"

# HTML templates for the DNP map editor. Same reason as the GLV: real .html
# files because most of it is JavaScript.
DNP_TEMPLATES_DIR: Path = PACKAGE_DIR / "web" / "dnp_map" / "templates"

# HTML template for the Arquivos do Projeto tab. Same reason as the GLV and
# the DNP map: a real .html file, because most of it is JavaScript.
PROJECT_FILES_TEMPLATES_DIR: Path = (
    PACKAGE_DIR / "web" / "project_files" / "templates"
)

# HTML template for the VLAN Mapper. Same reason as the GLV: a real .html file,
# because most of it is JavaScript.
VLAN_MAPPER_TEMPLATES_DIR: Path = (
    PACKAGE_DIR / "web" / "vlan_mapper" / "templates"
)

# HTML template for the GLE Variable Comment Exporter. Same reason as the GLV:
# a real .html file, because most of it is JavaScript.
GLE_EXPORTER_TEMPLATES_DIR: Path = (
    PACKAGE_DIR / "web" / "gle_exporter" / "templates"
)

# HTML templates for the VB Updater. Same reason as the GLV: real .html files,
# because most of those lines are JavaScript.
VB_UPDATER_TEMPLATES_DIR: Path = (
    PACKAGE_DIR / "web" / "vb_updater" / "templates"
)

# HTML template for the Settings Compare. Same reason as the GLV, and the
# starkest case of it: the diff UI is ~1.000 lines, nearly all JavaScript.
SETTINGS_COMPARE_TEMPLATES_DIR: Path = (
    PACKAGE_DIR / "web" / "settings_compare" / "templates"
)

# Uploads de RDB feitos via landing page do dashboard.
RDBS_DIR: Path = DATA_ROOT / "rdbs"

# Amostras de GLE / RDB / SCD que acompanham o repositorio (uso de
# desenvolvimento / teste).
SAMPLES_DIR: Path = PROJECT_ROOT / "samples"

# Documentacao (manuais SEL, application guides).
DOCS_DIR: Path = PROJECT_ROOT / "docs"

# Inputs and analysis reports that are neither code nor sample: the factory
# ICD/CID files, the DNP profiles, and the .txt files `tools/` generates from
# them. Kept out of `samples/` because they are not test input -- they are the
# raw material and the result of the coverage analyses.
FIXTURES_DIR: Path = PROJECT_ROOT / "fixtures"


def ensure_dirs() -> None:
    """Create the mutable directories (cache, rdbs) if they do not exist yet."""
    for d in (CACHE_DIR, RDB_CACHE_DIR, RDBS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def is_within(target: Path, roots) -> bool:
    """Whether `target` sits inside any of `roots`.

    This is the sandbox behind the `/download` endpoints, against path
    traversal. It compares with `Path.relative_to` rather than by concatenating
    `"/"`: on Windows
    the separator is `\\`, and a textual comparison would reject every valid
    path -- a 403 on every download.
    """
    target = Path(target).resolve()
    for root in roots:
        try:
            target.relative_to(Path(root).resolve())
        except ValueError:
            continue
        return True
    return False


def resolve_gle_path(value: str) -> Path:
    """Resolve a GLE path coming from config.ini or the CLI.

    Tried in order:
      1. An absolute path, returned as it is.
      2. Relative to `PROJECT_ROOT`, for compatibility with an older config.
      3. Relative to `SAMPLES_DIR`, the current default.
    """
    p = Path(value)
    if p.is_absolute():
        return p
    candidate_root = PROJECT_ROOT / p
    if candidate_root.exists():
        return candidate_root.resolve()
    return (SAMPLES_DIR / p).resolve()


def ensure_config_file(path: Path, logger: logging.Logger | None = None) -> bool:
    """Make sure the configuration file exists, seeding it from the `.example`.

    `config/config.ini` is gitignored -- it is where the relay's real
    passwords go -- so a clean clone simply has none. Without this, the app
    came up reading a file that was not there and quietly took the fallback of
    every `cfg.get(...)`, which is the worst possible way to be wrong.

    Returns True if it copied the model, False if the file already existed.
    Raises `FileNotFoundError` when neither the file nor a model exists: there
    is nothing to read, and saying so out loud beats guessing.
    """
    path = Path(path)
    if path.is_file():
        return False

    # A model beside the requested path (which covers `--config other.ini`),
    # with the project's own config/config.ini.example as the last resort.
    candidates = [Path(str(path) + ".example"), EXAMPLE_CONFIG_FILE]
    candidates = list(dict.fromkeys(candidates))
    for example in candidates:
        if example.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(example, path)
            (logger or logging.getLogger(__name__)).info(
                "Configuracao ausente: %s criado a partir de %s. "
                "Edite-o com o IP e as senhas do rele (ele nao vai pro git).",
                path, example,
            )
            return True

    raise FileNotFoundError(
        f"Arquivo de configuracao nao encontrado: {path}. Nao ha modelo para "
        f"copiar ({', '.join(str(c) for c in candidates)}). Restaure "
        f"config/config.ini.example do repositorio ou informe --config."
    )
