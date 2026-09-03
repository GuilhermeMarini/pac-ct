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
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
MANIFEST_ASSET = "manifest.json"
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


def check_latest(url: str = LATEST_URL,
                 timeout: float = DEFAULT_TIMEOUT) -> Release:
    """The newest published release, or `UpdateError` saying why not.

    Offline is an ordinary answer here, not a crash: the caller is a command
    the engineer typed, and "sem internet" is what it should print.
    """
    try:
        payload = _get_json(url, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # `/releases/latest` answers 404 for a repository that has never
            # published one. That is not an error on this machine, and saying
            # "404" would send somebody looking for a network problem.
            raise UpdateError(
                "Ainda nao ha' nenhuma versao publicada no projeto."
            ) from exc
        if exc.code == 403:
            raise UpdateError(
                "GitHub recusou a consulta (limite de 60 por hora para quem "
                "nao se identifica). Tente de novo mais tarde."
            ) from exc
        raise UpdateError(
            f"GitHub respondeu {exc.code} ao consultar as versoes publicadas."
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(
            f"Sem acesso a' internet para consultar atualizacoes ({exc})."
        ) from exc
    except ValueError as exc:
        raise UpdateError("Resposta invalida da API do GitHub.") from exc

    tag = str(payload.get("tag_name") or "")
    assets = tuple(
        Asset(name=str(a.get("name") or ""),
              url=str(a.get("browser_download_url") or ""),
              size=int(a.get("size") or 0))
        for a in payload.get("assets") or []
        if a.get("name") and a.get("browser_download_url")
    )
    return Release(version=tag[1:] if tag.startswith("v") else tag,
                   tag=tag, notes=str(payload.get("body") or ""),
                   assets=assets)


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
    manifest_asset = release.asset(MANIFEST_ASSET)
    if manifest_asset is None:
        raise UpdateError(
            f"A release {release.tag} nao traz {MANIFEST_ASSET}; sem ele nao "
            f"ha' sha256 para conferir e nada sera' descompactado.")

    layout.versions.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pacct-dl-") as tmpdir:
        tmp = Path(tmpdir)
        manifest = json.loads(
            download(manifest_asset.url, tmp / MANIFEST_ASSET)
            .read_text(encoding="utf-8"))
        digest = expected_sha256(manifest, asset.name)
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
