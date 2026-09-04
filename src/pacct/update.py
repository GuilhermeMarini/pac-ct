"""Auto-update, and the install layout that makes it safe.

The repository is public, so the update channel is GitHub Releases read
**anonymously**: no token is embedded in a bundle, nothing has to be typed on
site, and the 60 requests/hour an unauthenticated caller gets is generous for
a check somebody runs by hand.

Five rules, each of which follows from something already established
elsewhere in this project rather than from taste:

1. **Never on the boot path.** A check is `app.py --atualizar` or a button on
   the home -- never part of starting up. Same rule, same reason, as
   `install_requirements` only running pip when an import fails: a substation
   has no internet, and a failed network call must not become a failed start.
2. **Verify before unpacking.** The sha256 comes from the release's own
   `manifest.json` and is checked against the downloaded bytes before a single
   entry is extracted.
3. **Stage, then swap, then restart.** The new version is unpacked into
   `versions/<new>/` and given its own venv while the old one keeps running;
   only then is `current` repointed. A running process cannot reliably replace
   its own loaded extension modules on Windows, which is what makes the
   versioned layout safe rather than merely tidy -- and it is also what makes
   a rollback one repointing.
4. **Only releases.** A `1.4.0.dev0+g<sha>` snapshot is never offered: the
   commit it names may never become a release at all. `version.is_newer`
   refuses anything that is not `X.Y.Z`.
5. **`userdata/` is never read, moved or migrated.** It holds `config.ini`
   with the relay's ACC/2AC passwords, the content-addressed RDB cache and the
   uploaded RDBs. An update that touched it would be an update that can cost
   an engineer a day in a substation.

The layout the rules describe:

    PAC-CT/
    |-- pac-ct.sh / pac-ct.cmd      launcher -> current/
    |-- versions/1.4.0/  1.5.0/     one directory per version, own .venv
    |-- current -> versions/1.5.0   junction on Windows
    `-- userdata/                   config.ini, cache/, rdbs/

`paths.py` already reads `PACCT_ROOT` and `PACCT_DATA_DIR`, which is exactly
the split this needs; the launcher sets both.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pacct import version as version_mod

log = logging.getLogger(__name__)

REPO = "GuilhermeMarini/pac-ct"
MANIFEST_ASSET = "manifest.json"

# The check reads the release's own manifest through the `releases/latest/`
# REDIRECT, not the REST API. That is not a micro-optimisation: the REST API
# allows 60 requests/hour to a caller that does not identify itself, counted
# PER SOURCE IP -- so a utility whose engineers all sit behind one NAT shares
# a single budget, and "tente de novo mais tarde" is a poor answer to "is
# there a new version". `releases/latest/download/<asset>` is the ordinary
# file path, served like any other download and not rated the same way.
#
# Authenticating would lift the limit to 5.000/hour and is exactly what must
# NOT happen here: the token would have to travel inside a zip handed to
# substations, which is a credential in a public artefact. The repository is
# public precisely so that no secret is needed.
#
# `latest/` resolves only to the newest NON-prerelease, which is rule 4 --
# only real releases are ever offered -- enforced by GitHub rather than by us.
LATEST_MANIFEST_URL = (
    f"https://github.com/{REPO}/releases/latest/download/{MANIFEST_ASSET}")
# Release notes are a nicety, so they stay on the API and any failure there is
# simply no notes. They are never on the path that decides anything.
NOTES_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
# GitHub refuses a request with no User-Agent, and an honest one is also what
# lets them tell this traffic apart if it ever misbehaves.
USER_AGENT = "pac-ct-updater"
DEFAULT_TIMEOUT = 15.0


class UpdateError(RuntimeError):
    """Anything that stops an update, with a message meant for the engineer."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int


@dataclass(frozen=True)
class Release:
    """One published release, as the public API describes it."""

    version: str
    tag: str
    notes: str
    assets: tuple[Asset, ...]
    # The manifest this release was described by, when the check already read
    # it. Carrying it means the update does not fetch the same document twice,
    # and -- more importantly -- the sha256 that gets verified is the one from
    # the very response that named this version, not a second read that could
    # have moved.
    manifest: dict | None = None

    def asset(self, name: str) -> Asset | None:
        for a in self.assets:
            if a.name == name:
                return a
        return None


@dataclass(frozen=True)
class Layout:
    """A versioned install, or the absence of one.

    `version_dir` is where the running code lives. Everything else is derived
    from it, and `detect()` answers None when the code is NOT sitting in a
    `versions/<name>/` -- a development clone, which must never be swapped
    under its own feet.
    """

    root: Path
    version_dir: Path

    @property
    def versions(self) -> Path:
        return self.root / "versions"

    @property
    def current(self) -> Path:
        return self.root / "current"

    @property
    def userdata(self) -> Path:
        return self.root / "userdata"

    @property
    def launcher(self) -> Path:
        return self.root / ("pac-ct.cmd" if os.name == "nt" else "pac-ct.sh")

    @classmethod
    def detect(cls, version_dir: Path) -> Layout | None:
        version_dir = Path(version_dir).resolve()
        parent = version_dir.parent
        if parent.name != "versions":
            return None
        return cls(root=parent.parent, version_dir=version_dir)

    @classmethod
    def would_be(cls, version_dir: Path) -> Layout:
        """The layout a not-yet-installed tree WILL have once `--instalar` runs.

        A freshly unzipped `PAC-CT/versions/1.4.0/` already sits in the right
        place; this is what `install_here` uses before `current` exists.
        """
        found = cls.detect(version_dir)
        if found is None:
            raise UpdateError(
                f"{version_dir} nao esta' em versions/<versao>/. Descompacte o "
                f"pacote em PAC-CT/versions/<versao>/ (veja INSTALAR.txt)."
            )
        return found


# ---------------------------------------------------------------------------
# The portable install: one folder, data inside it, updated in place
# ---------------------------------------------------------------------------
#
# The versioned layout is the safe one and stays the default, but it is not the
# only way people actually use this. A commissioning engineer copies ONE folder
# to a laptop or a USB stick, runs it there, and wants that folder to keep
# working -- with its `config.ini`, its RDB cache and its uploads inside it.
# `paths.DATA_ROOT` already falls back to `PROJECT_ROOT`, so RUNNING that way
# has always worked; only updating refused, because `Layout.detect()` demands a
# `versions/<v>/` parent.
#
# So a portable folder updates IN PLACE. That is genuinely less safe than
# repointing a junction and the difference is not hidden: the swap happens
# under the running process, so it backs the old code up first and puts it back
# if anything fails. What it must never touch is listed once, here, and the
# swap only ever moves paths it can name.

PORTABLE_CODE_DIRS = ("src", "selprotopy", "tools", "vendor")
PORTABLE_CODE_FILES = ("app.py", "VERSION", "requirements.txt", "LICENSE",
                       "NOTICE.md", "README.md", "INSTALAR.txt",
                       "pac-ct.sh", "pac-ct.cmd",
                       "config/config.ini.example")
# Never moved, never read, never migrated -- the engineer's half of the folder.
# `config/config.ini` holds the relay's ACC/2AC passwords, `cache/rdb/` can be
# gigabytes, and `data/` is the DNP profile they imported by hand.
PORTABLE_KEEP = ("config/config.ini", "cache", "rdbs", "data", ".venv")

STAGING_DIR = ".pacct-update"
BACKUP_DIR = ".pacct-backup"


def install_kind(root: Path) -> str:
    """`"versioned"`, `"portable"`, or `"checkout"`.

    A git checkout is refused outright: updating it would throw away
    uncommitted work, and `git pull` is the right command there.
    """
    root = Path(root).resolve()
    if Layout.detect(root) is not None:
        return "versioned"
    if (root / ".git").exists():
        return "checkout"
    if (root / "app.py").is_file() and (root / "src").is_dir():
        return "portable"
    return "checkout"


def _swap_in(staged: Path, root: Path, backup: Path) -> list[tuple[Path, Path]]:
    """Move every code path from `staged` into `root`, old copy to `backup`.

    Returns what was moved, so a failure can put it all back. Only paths named
    in PORTABLE_CODE_* are ever touched.
    """
    moved: list[tuple[Path, Path]] = []
    for rel in (*PORTABLE_CODE_DIRS, *PORTABLE_CODE_FILES):
        new = staged / rel
        if not new.exists():
            continue
        live = root / rel
        kept = backup / rel
        kept.parent.mkdir(parents=True, exist_ok=True)
        if live.exists():
            shutil.move(str(live), str(kept))
            moved.append((live, kept))
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(new), str(live))
    return moved


def _restore(moved: list[tuple[Path, Path]]) -> None:
    """Put the backed-up copies back where they came from."""
    for live, kept in reversed(moved):
        try:
            if live.exists():
                shutil.rmtree(live) if live.is_dir() else live.unlink()
            shutil.move(str(kept), str(live))
        except OSError:
            log.exception("Falha ao restaurar %s", live)


def _manifest_for(release: Release, tmp: Path) -> dict:
    """The manifest describing `release`, without fetching it twice.

    `check_latest` already read it -- that is how it knew the version at all --
    so the normal path costs no request here. The download branch is for a
    `Release` assembled some other way (a test, or a caller that built one by
    hand), and it keeps the old contract: no manifest, no unpacking.
    """
    if release.manifest is not None:
        return release.manifest
    asset = release.asset(MANIFEST_ASSET)
    if asset is None:
        raise UpdateError(
            f"A release {release.tag} nao traz {MANIFEST_ASSET}; sem ele nao "
            f"ha' sha256 para conferir e nada sera' descompactado.")
    return json.loads(
        download(asset.url, tmp / MANIFEST_ASSET).read_text(encoding="utf-8"))


def perform_portable_update(root: Path, release: Release, *,
                            windows: bool | None = None) -> Path:
    """Update a portable folder in place, keeping everything the user put there.

    Same order as the versioned path -- download, verify the sha256, unpack,
    and only then touch anything that is live. The venv is deliberately NOT
    replaced: on Windows its `python.exe` is the running interpreter and cannot
    be overwritten, so the packages inside it are upgraded from the new
    `vendor/` instead.
    """
    root = Path(root).resolve()
    asset_name = preferred_asset_name(release.version, windows=windows)
    asset = release.asset(asset_name) or release.asset(
        preferred_asset_name(release.version, windows=False))
    if asset is None:
        raise UpdateError(f"A release {release.tag} nao traz {asset_name}.")
    staging_root = root / STAGING_DIR
    backup = root / BACKUP_DIR
    shutil.rmtree(staging_root, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    staging_root.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="pacct-dl-") as tmpdir:
            tmp = Path(tmpdir)
            digest = expected_sha256(_manifest_for(release, tmp), asset.name)
            bundle = download(asset.url, tmp / asset.name)
            verify(bundle, digest)                 # rule 2, before unpack
            staged = unpack(bundle, staging_root / release.version)
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
        raise

    moved: list[tuple[Path, Path]] = []
    try:
        moved = _swap_in(staged, root, backup)
        target = venv_python(root)
        if target.exists():
            # The venv survives the swap; its CONTENTS are brought up to the
            # new requirements, offline, from the vendor/ that just landed.
            cmd = [str(target), "-m", "pip", "install", "--quiet",
                   "-r", str(root / "requirements.txt")]
            vendor = root / "vendor"
            if vendor.is_dir():
                cmd += ["--no-index", "--find-links", str(vendor)]
            subprocess.check_call(cmd)
    except Exception as exc:
        _restore(moved)
        shutil.rmtree(staging_root, ignore_errors=True)
        raise UpdateError(
            f"Falha ao aplicar a atualizacao ({exc}). A pasta foi restaurada "
            f"como estava; seus dados nao foram tocados."
        ) from exc

    shutil.rmtree(staging_root, ignore_errors=True)
    return root


def rollback_portable(root: Path) -> str:
    """Put back the version the last portable update replaced."""
    root = Path(root).resolve()
    backup = root / BACKUP_DIR
    if not backup.is_dir() or not any(backup.iterdir()):
        raise UpdateError(
            "Nao ha' backup de uma atualizacao anterior nesta pasta.")
    moved = []
    for rel in (*PORTABLE_CODE_DIRS, *PORTABLE_CODE_FILES):
        kept = backup / rel
        if kept.exists():
            moved.append((root / rel, kept))
    _restore(moved)
    shutil.rmtree(backup, ignore_errors=True)
    return version_mod.read_version(root)


# ---------------------------------------------------------------------------
# Rule 1 and 4: checking, and what counts as an offer
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data = resp.read()
    parsed = json.loads(data.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise UpdateError(f"Resposta inesperada de {url}")
    return parsed


def release_from_manifest(manifest: dict) -> Release:
    """Build a `Release` out of a bundle manifest.

    Every asset URL is pinned to `releases/download/v<version>/`, never left
    on `latest/`: the sha256 comes from THIS manifest, and a release published
    between the check and the download would otherwise hand back different
    bytes for the digest we are about to verify against.
    """
    version = str(manifest.get("version") or "")
    if not version:
        raise UpdateError("O manifesto da release nao diz a versao.")
    if not manifest.get("release", False):
        # Rule 4, and the one shape `latest/` cannot rule out on its own: a
        # snapshot manifest published by mistake.
        raise UpdateError(
            f"A versao publicada ({version}) nao e' um release; nao sera' "
            f"oferecida.")
    base = f"https://github.com/{REPO}/releases/download/v{version}"
    assets = tuple(
        Asset(name=str(a.get("file") or ""),
              url=f"{base}/{a.get('file')}",
              size=int(a.get("size") or 0))
        for a in manifest.get("artifacts") or [] if a.get("file")
    )
    return Release(version=version, tag=f"v{version}", notes="",
                   assets=assets, manifest=manifest)


def fetch_notes(timeout: float = DEFAULT_TIMEOUT) -> str:
    """The release body, best effort. Never fails the update.

    This is the one call still on the REST API, and it is deliberately on the
    side of the road: if the hourly limit is spent, or there is no network for
    it, the user gets no notes rather than no update.
    """
    try:
        return str(_get_json(NOTES_URL, timeout).get("body") or "")
    except (UpdateError, urllib.error.URLError, OSError, ValueError):
        return ""


def check_latest(url: str = LATEST_MANIFEST_URL,
                 timeout: float = DEFAULT_TIMEOUT) -> Release:
    """The newest published release, read from its own manifest.

    Offline is an ordinary answer here, not a crash: the caller is a command
    the engineer typed, and "sem internet" is what it should print.
    """
    try:
        manifest = _get_json(url, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # No release has ever been published, so `latest/` resolves to
            # nothing. That is not an error on this machine, and saying "404"
            # would send somebody looking for a network problem.
            raise UpdateError(
                "Ainda nao ha' nenhuma versao publicada no projeto."
            ) from exc
        raise UpdateError(
            f"GitHub respondeu {exc.code} ao procurar a versao mais nova."
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(
            f"Sem acesso a' internet para consultar atualizacoes ({exc})."
        ) from exc
    except ValueError as exc:
        raise UpdateError(
            "O manifesto da versao mais nova veio ilegivel.") from exc

    return release_from_manifest(manifest)


def update_available(release: Release, current: str) -> bool:
    """Rule 4, in one line: only a real release, and only a newer one."""
    return version_mod.is_newer(release.version, current)


# ---------------------------------------------------------------------------
# Rule 2: what is downloaded, and what is checked before it is opened
# ---------------------------------------------------------------------------

def preferred_asset_name(version: str, *, windows: bool | None = None) -> str:
    """The bundle this machine should take.

    The Windows bundle exists because its `vendor/` was fetched with
    `--platform win_amd64`; on any other platform the portable one is right.
    """
    if windows is None:
        windows = os.name == "nt"
    return f"pac-ct-{version}{'-win_amd64' if windows else ''}.zip"


def download(url: str, dest: Path, timeout: float = 120.0) -> Path:
    """Fetch `url` into `dest`, atomically."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            with tmp.open("wb") as fh:
                shutil.copyfileobj(resp, fh, 1 << 20)
    except (urllib.error.URLError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"Falha ao baixar {url}: {exc}") from exc
    tmp.replace(dest)
    return dest


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_sha256(manifest: dict, asset_name: str) -> str:
    """The sha256 the release's own manifest gives for this bundle.

    A missing entry is refused rather than skipped. "No sha to check" and
    "sha matches" must never take the same branch -- that is how verification
    quietly stops happening.
    """
    for artifact in manifest.get("artifacts") or []:
        if artifact.get("file") == asset_name:
            digest = str(artifact.get("sha256") or "")
            if len(digest) == 64:
                return digest
            raise UpdateError(
                f"manifest.json traz um sha256 invalido para {asset_name}.")
    raise UpdateError(
        f"manifest.json da release nao descreve {asset_name}; nada sera' "
        f"descompactado sem um sha256 para conferir.")


def verify(path: Path, expected: str) -> None:
    actual = sha256_of(path)
    if actual != expected:
        path.unlink(missing_ok=True)
        raise UpdateError(
            f"sha256 nao confere para {path.name}: esperado {expected}, "
            f"obtido {actual}. O arquivo foi descartado.")


# ---------------------------------------------------------------------------
# Rule 3: stage, swap, restart
# ---------------------------------------------------------------------------

def unpack(zip_path: Path, dest: Path) -> Path:
    """Extract the bundle's single top-level directory into `dest`.

    The bundle is built with a `pac-ct-<version>/` prefix, so this both flattens
    it and refuses an archive shaped like anything else -- including one whose
    entries would climb out of `dest`.
    """
    if dest.exists():
        raise UpdateError(
            f"{dest} ja' existe. Remova-o ou instale outra versao -- este "
            f"instalador nao sobrescreve uma versao ja' instalada.")
    staging = Path(tempfile.mkdtemp(prefix="pacct-update-", dir=str(dest.parent)))
    try:
        with zipfile.ZipFile(zip_path) as z:
            roots = {Path(n).parts[0] for n in z.namelist() if n.strip()}
            if len(roots) != 1:
                raise UpdateError(
                    f"O pacote deveria ter uma unica pasta na raiz; tem "
                    f"{len(roots)}.")
            for member in z.namelist():
                target = (staging / member).resolve()
                if not str(target).startswith(str(staging.resolve())):
                    raise UpdateError(
                        f"Entrada suspeita no pacote: {member!r}. Nada foi "
                        f"instalado.")
            z.extractall(staging)
        (staging / roots.pop()).replace(dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    launcher = dest / "pac-ct.sh"
    if launcher.is_file():
        launcher.chmod(0o755)
    return dest


def venv_python(version_dir: Path) -> Path:
    if os.name == "nt":
        return version_dir / ".venv" / "Scripts" / "python.exe"
    return version_dir / ".venv" / "bin" / "python"


def build_venv(version_dir: Path, *, offline: bool = True,
               python: str | None = None) -> Path:
    """Give `version_dir` its own venv, installed from its own `vendor/`.

    Offline by default and on purpose: the bundle carries a wheel for every
    dependency precisely so that this step needs no network. `offline=False`
    exists for a build machine, not for a substation.
    """
    py = python or sys.executable
    subprocess.check_call([py, "-m", "venv", str(version_dir / ".venv")])
    target = venv_python(version_dir)
    cmd = [str(target), "-m", "pip", "install", "--quiet",
           "-r", str(version_dir / "requirements.txt")]
    vendor = version_dir / "vendor"
    if offline:
        if not vendor.is_dir():
            raise UpdateError(
                f"Instalacao offline pedida mas {vendor} nao existe. Este "
                f"pacote foi gerado com --no-vendor.")
        cmd += ["--no-index", "--find-links", str(vendor)]
    else:
        cmd += ["--pre"]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as exc:
        raise UpdateError(
            f"Falha ao instalar as dependencias da nova versao (codigo "
            f"{exc.returncode}). `current` continua onde estava.") from exc
    return target


def point_current(layout: Layout, version_dir: Path) -> None:
    """Repoint `current` at `version_dir`. This is the swap, and the rollback.

    On Windows it is a junction (`mklink /J`), which -- unlike a symlink --
    needs no administrator rights. Neither platform can replace a link
    atomically, so the window exists; it is microseconds wide, it happens with
    the new version already fully installed beside the old one, and both
    endpoints of the window are a valid install.
    """
    link = layout.current
    if link.is_symlink() or link.exists():
        if link.is_dir() and not link.is_symlink() and os.name != "nt":
            raise UpdateError(
                f"{link} e' um diretorio de verdade, nao um ponteiro. Mova-o "
                f"antes de continuar -- nao vou apagar dados de ninguem.")
        try:
            link.unlink()
        except (IsADirectoryError, PermissionError, OSError):
            # A Windows junction removes as a directory, not as a file.
            os.rmdir(link)
    if os.name == "nt":
        subprocess.check_call(["cmd", "/c", "mklink", "/J",
                               str(link), str(version_dir)])
    else:
        link.symlink_to(version_dir, target_is_directory=True)


def installed_versions(layout: Layout) -> list[str]:
    if not layout.versions.is_dir():
        return []
    names = [p.name for p in layout.versions.iterdir()
             if p.is_dir() and (p / "app.py").is_file()]
    return sorted(names, key=lambda n: version_mod.ordering_key(n) or (0, 0, 0, 0))


def current_version(layout: Layout) -> str | None:
    try:
        return version_mod.read_version(layout.current.resolve())
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Rule 5: userdata, and the first install
# ---------------------------------------------------------------------------

def ensure_userdata(layout: Layout) -> Path:
    """Create `userdata/` if it is not there, and otherwise leave it alone.

    "Leave it alone" is the whole contract: no reading, no moving, no
    migrating. An existing `userdata/` from a previous install is adopted
    exactly as it stands.
    """
    layout.userdata.mkdir(parents=True, exist_ok=True)
    (layout.userdata / "config").mkdir(exist_ok=True)
    return layout.userdata


def install_here(version_dir: Path, *, offline: bool = True,
                 build: bool = True) -> Layout:
    """First install: venv, `userdata/`, `current`, launcher.

    Run once per machine, straight out of the unzipped bundle
    (`python3 PAC-CT/versions/1.4.0/app.py --instalar`). Idempotent: running it
    again on an install that already exists repoints `current` at this version,
    which is also how you roll forward by hand.
    """
    version_dir = Path(version_dir).resolve()
    layout = Layout.would_be(version_dir)
    if build and not venv_python(version_dir).exists():
        build_venv(version_dir, offline=offline)
    ensure_userdata(layout)
    for name in ("pac-ct.sh", "pac-ct.cmd"):
        src = version_dir / name
        if src.is_file():
            shutil.copy2(src, layout.root / name)
    sh = layout.root / "pac-ct.sh"
    if sh.is_file():
        sh.chmod(0o755)
    point_current(layout, version_dir)
    return layout


def restart(layout: Layout, args: list[str] | None = None) -> None:
    """Hand over to the launcher, which resolves `current` afresh.

    Deliberately the launcher and not `current/app.py`: the launcher is the
    one place that knows to set `PACCT_ROOT` and `PACCT_DATA_DIR`, and going
    through it is what proves the swap actually took.
    """
    extra = list(args or [])
    if not layout.launcher.is_file():
        raise UpdateError(
            f"Atualizacao concluida, mas {layout.launcher} nao existe. Rode "
            f"`python app.py --instalar` na nova versao.")
    cmd = ([str(layout.launcher)] if os.name != "nt"
           else ["cmd", "/c", str(layout.launcher)]) + extra
    log.info("Reiniciando via %s", layout.launcher)
    sys.exit(subprocess.call(cmd))


# ---------------------------------------------------------------------------
# The whole thing, in the order the rules put it
# ---------------------------------------------------------------------------

def perform_update(layout: Layout, release: Release, *,
                   windows: bool | None = None,
                   offline: bool = True) -> Path:
    """Download, verify, unpack, build the venv, and only then swap.

    Returns the new version's directory. Every failure before the swap leaves
    `current` exactly where it was, which is why the swap is the last line.
    """
    asset_name = preferred_asset_name(release.version, windows=windows)
    asset = release.asset(asset_name) or release.asset(
        preferred_asset_name(release.version, windows=False))
    if asset is None:
        raise UpdateError(
            f"A release {release.tag} nao traz {asset_name}.")
    layout.versions.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pacct-dl-") as tmpdir:
        tmp = Path(tmpdir)
        digest = expected_sha256(_manifest_for(release, tmp), asset.name)
        bundle = download(asset.url, tmp / asset.name)
        verify(bundle, digest)                       # rule 2, before unpack
        target = layout.versions / release.version
        unpack(bundle, target)                       # rule 3, staged beside

    build_venv(target, offline=offline)
    point_current(layout, target)                    # ... then swapped
    return target


def rollback(layout: Layout) -> Path:
    """Point `current` at the newest installed version that is not the current one."""
    running = current_version(layout)
    others = [v for v in installed_versions(layout) if v != running]
    if not others:
        raise UpdateError("Nao ha' outra versao instalada para voltar.")
    target = layout.versions / others[-1]
    point_current(layout, target)
    return target
