#!/usr/bin/env python3
"""Build the offline bundle: `dist/pac-ct-<version>[-<platform>].zip`.

A substation has no internet. That single fact already shapes the boot path
(`app.py` runs pip only when an import fails) and it shapes the distributable
too: the zip carries the source AND a `vendor/` of wheels for every
dependency, so the install is `pip install --no-index --find-links vendor/`
and nothing is ever fetched on site.

    python3 tools/build_dist.py                  # snapshot, this platform
    python3 tools/build_dist.py --windows        # snapshot, win_amd64 wheels
    python3 tools/build_dist.py --release        # what a v* tag builds
    python3 tools/build_dist.py --no-vendor      # source only (fast; CI lint)

Two properties are load-bearing and both are tested:

* the version inside the zip is the version in the manifest, always. A
  snapshot writes `1.4.0.dev0+g<sha>` into the bundle's own `VERSION`, so an
  installed copy reports the commit it came from rather than the release it
  was merely heading towards;
* `--release` refuses to build unless `VERSION` matches the tag being built.
  A release whose contents do not match its number is the one artefact this
  project must never produce -- it is the thing the updater trusts.

The script imports `pacct/version.py` straight from its path rather than
importing the `pacct` package: a build must not require the application's
runtime dependencies to be installed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_version_module():
    spec = importlib.util.spec_from_file_location(
        "_pacct_version", ROOT / "src" / "pacct" / "version.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


version_mod = _load_version_module()

# What travels. Everything else -- tests, docs, samples, mockups, the caches --
# stays behind: the bundle is what an engineer runs in a substation, not the
# repository. `data/` is the USER overlay and legitimately does not exist in a
# clean clone, so it is copied only when present.
TREE_FILES = [
    "app.py",
    "requirements.txt",
    "VERSION",
    "LICENSE",
    "NOTICE.md",
    "README.md",
]
TREE_DIRS = [
    "src/pacct",
    "selprotopy",
    "tools",
    "config",
    "data",
]
# Never copied, at any depth.
EXCLUDE_DIRS = {"__pycache__", ".git", ".mypy_cache", ".pytest_cache",
                ".ruff_cache", ".venv", "dist"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
# `config/` travels for exactly one file: the model the app seeds `config.ini`
# from. A real `config.ini` holds the relay's ACC/2AC passwords and must never
# leave the machine it was typed on.
CONFIG_ALLOW = {"config.ini.example"}

MIN_PYTHON = "3.10"


def _iter_tree(src: Path) -> list[Path]:
    out = []
    for p in sorted(src.rglob("*")):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix not in EXCLUDE_SUFFIXES:
            out.append(p)
    return out


def stage_tree(dest: Path, version: str) -> None:
    """Copy the shipping tree into `dest`, with `VERSION` set to `version`."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in TREE_FILES:
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    for name in TREE_DIRS:
        src = ROOT / name
        if not src.is_dir():
            continue
        for f in _iter_tree(src):
            rel = f.relative_to(ROOT)
            if rel.parts[0] == "config" and f.name not in CONFIG_ALLOW:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
    # The bundle says what it IS, not what the tree was heading towards.
    (dest / "VERSION").write_text(version + "\n", encoding="utf-8")
    for name in ("pac-ct.sh", "pac-ct.cmd", "INSTALAR.txt"):
        shutil.copy2(ROOT / "tools" / "dist" / name, dest / name)
    (dest / "pac-ct.sh").chmod(0o755)


def split_requirements(req: Path) -> tuple[list[str], list[str]]:
    """`(direct references, index requirements)`.

    `pip download --only-binary=:all:` refuses a direct reference -- it has to
    build one -- so the two halves are fetched by different commands. Both
    `cfbwrite` and `selfiles` are pure Python, so the `py3-none-any` wheel
    built here is the same wheel Windows needs.
    """
    direct, indexed = [], []
    for raw in req.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        (direct if " @ " in line else indexed).append(line)
    return direct, indexed


# A dependency that only exists on Windows is invisible to a build running on
# Linux. `pip download --platform win_amd64` selects WHEEL TAGS for the target;
# environment markers are still evaluated against the interpreter doing the
# downloading, and pip has no flag that changes that. So `telnetlib3`'s
# `blessed>=1.41; platform_system == "Windows"` was simply never fetched, and
# the Windows bundle installed fine on the build machine and died on the
# target -- which is exactly the machine that has no network to recover with.
WINDOWS_MARKERS = ("platform_system == 'Windows'", 'platform_system == "Windows"',
                   "sys_platform == 'win32'", 'sys_platform == "win32"')


def wheel_name(filename: str) -> str:
    """The distribution name a wheel filename carries, normalised."""
    return filename.split("-")[0].replace("_", "-").lower()


def requirement_name(spec: str) -> str:
    """`blessed>=1.41` -> `blessed`; `foo[bar]>=1` -> `foo`."""
    for sep in ("[", "(", "=", ">", "<", "!", "~", " "):
        spec = spec.split(sep, 1)[0]
    return spec.strip().replace("_", "-").lower()


def windows_only_requirements(vendor: Path) -> set[str]:
    """Requirements the downloaded wheels declare for Windows and nothing else.

    Read from each wheel's own METADATA rather than from an index, so it needs
    no network of its own and reports exactly what these artefacts ask for.
    `extra ==` markers are skipped: an optional extra is not a dependency until
    somebody asks for it.
    """
    wanted: set[str] = set()
    for whl in sorted(vendor.glob("*.whl")):
        try:
            with zipfile.ZipFile(whl) as z:
                metas = [n for n in z.namelist()
                         if n.endswith(".dist-info/METADATA")]
                for meta in metas:
                    text = z.read(meta).decode("utf-8", "replace")
                    for line in text.splitlines():
                        if not line.startswith("Requires-Dist:"):
                            continue
                        req = line.split(":", 1)[1].strip()
                        if ";" not in req:
                            continue
                        spec, marker = req.split(";", 1)
                        if "extra" in marker:
                            continue
                        if any(m in marker for m in WINDOWS_MARKERS):
                            wanted.add(spec.strip())
        except (OSError, zipfile.BadZipFile):
            continue
    return wanted


def build_vendor(dest: Path, *, windows: bool, python_version: str) -> list[str]:
    """Fill `dest/vendor/` with a wheel for every dependency."""
    vendor = dest / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    direct, indexed = split_requirements(ROOT / "requirements.txt")

    for ref in direct:
        # `--no-deps`: the dependencies of cfbwrite/selfiles are in
        # requirements.txt too, and the indexed pass below fetches them with
        # the platform flags this pass cannot use.
        subprocess.check_call([sys.executable, "-m", "pip", "wheel",
                               "--no-deps", "--wheel-dir", str(vendor), ref])

    if indexed:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("\n".join(indexed) + "\n")
            tmp = Path(fh.name)
        try:
            cmd = [sys.executable, "-m", "pip", "download",
                   "--pre", "-r", str(tmp), "-d", str(vendor)]
            if windows:
                # `--platform` requires `--only-binary=:all:`; every dependency
                # here publishes a wheel, so this is a filter and not a wish.
                cmd += ["--only-binary=:all:", "--platform", "win_amd64",
                        "--python-version", python_version]
            subprocess.check_call(cmd)
        finally:
            tmp.unlink(missing_ok=True)

    if windows:
        # ... and then whatever those wheels declare for Windows alone, to a
        # fixed point: `blessed` pulls `jinxed` on Windows, which may pull
        # more. Bounded, because a resolver loop here would hang a build.
        for _ in range(5):
            present = {wheel_name(p.name) for p in vendor.glob("*.whl")}
            missing = sorted(r for r in windows_only_requirements(vendor)
                             if requirement_name(r) not in present)
            if not missing:
                break
            print(f"[INFO] dependencias so-Windows ausentes: {', '.join(missing)}")
            # Deliberately NOT --no-deps: a Windows-only package drags its
            # own subtree, and that subtree is invisible to the main pass too.
            # `blessed` requires `jinxed` with no marker at all, so nothing
            # here would ever ask for it -- pip has to resolve it.
            subprocess.check_call(
                [sys.executable, "-m", "pip", "download", "--pre",
                 "--only-binary=:all:", "--platform", "win_amd64",
                 "--python-version", python_version, "-d", str(vendor),
                 *missing])
        else:
            raise SystemExit(
                "[ERRO] As dependencias so-Windows nao fecharam em 5 rodadas.")

    return sorted(p.name for p in vendor.iterdir() if p.is_file())


def pin_direct_references(dest: Path, wheels: list[str]) -> list[str]:
    """Rewrite the BUNDLE's `requirements.txt` so `--offline` never needs a network.

    `--no-index` disables the package INDEX; it does not disable a direct
    reference. `cfbwrite @ git+https://github.com/...` therefore still sends
    pip to git, and in a substation there is neither a network nor, usually,
    git -- so the offline install would fail on the one machine it exists for.
    It did not fail on the build machine only because pip had the wheel it had
    just built sitting in its own cache, which is exactly the kind of pass that
    means nothing.

    So the bundle carries `cfbwrite==1.0.0`, the version of the wheel actually
    in `vendor/`, and `--find-links vendor/` resolves it with no index and no
    git. The repository's own `requirements.txt` keeps the direct reference:
    that is what a clone, CI and `pip download` need, and it is the file the
    pin belongs in.
    """
    by_name = {}
    for wheel in wheels:
        parts = wheel.split("-")
        if len(parts) >= 2 and wheel.endswith(".whl"):
            by_name[parts[0].replace("_", "-").lower()] = parts[1]

    req = dest / "requirements.txt"
    out, pinned = [], []
    for raw in req.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if " @ " not in line:
            out.append(raw)
            continue
        name = line.split(" @ ", 1)[0].strip()
        version = by_name.get(name.replace("_", "-").lower())
        if version is None:
            raise SystemExit(
                f"[ERRO] {name} e' uma referencia direta e nao ha' wheel dele "
                f"em vendor/. O pacote offline nao conseguiria instalar.")
        out.append(f"# Fixado pelo build a partir de vendor/{name}-{version}: "
                   f"a referencia direta do repositorio precisaria de rede.")
        out.append(f"{name}=={version}")
        pinned.append(f"{name}=={version}")
    req.write_text("\n".join(out) + "\n", encoding="utf-8")
    return pinned


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_zip(stage: Path, out: Path, prefix: str) -> None:
    """Zip `stage` under a single top-level `prefix/` directory.

    The prefix is not decoration: it is what makes unzipping safe in whatever
    directory the engineer happens to be in, and it is the directory the
    updater stages into (`versions/<version>/`).
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                z.write(f, f"{prefix}/{f.relative_to(stage).as_posix()}")
    tmp.replace(out)


def build(*, release: bool, windows: bool, vendor: bool,
          python_version: str, out_dir: Path) -> dict:
    version = version_mod.build_version(ROOT, release=release)
    if release and not version_mod.is_release(version):
        raise SystemExit(
            f"[ERRO] --release com VERSION={version!r}: um release precisa ser "
            f"X.Y.Z. Corrija o arquivo VERSION antes de marcar a tag.")
    platform = "win_amd64" if windows else "any"
    suffix = "-win_amd64" if windows else ""
    name = f"pac-ct-{version}{suffix}.zip"
    prefix = f"pac-ct-{version}"

    with tempfile.TemporaryDirectory(prefix="pacct-build-") as tmpdir:
        stage = Path(tmpdir) / prefix
        stage_tree(stage, version)
        wheels = build_vendor(stage, windows=windows,
                              python_version=python_version) if vendor else []
        if wheels:
            pin_direct_references(stage, wheels)
        zip_path = out_dir / name
        make_zip(stage, zip_path, prefix)

    entry = {
        "file": name,
        "platform": platform,
        "sha256": sha256_of(zip_path),
        "size": zip_path.stat().st_size,
        "wheels": wheels,
    }
    manifest = {
        "name": "pac-ct",
        "version": version,
        "release": version_mod.is_release(version),
        "commit": version_mod.git_sha(ROOT, length=40),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "min_python": MIN_PYTHON,
        "artifacts": [entry],
    }

    # A second run (the Windows bundle after the Linux one) adds its artefact
    # to the manifest instead of erasing it -- one release, two zips, one
    # document listing both.
    manifest_path = out_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if previous.get("version") == version:
            kept = [a for a in previous.get("artifacts", [])
                    if a.get("file") != name]
            manifest["artifacts"] = kept + [entry]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")

    (out_dir / "SHA256SUMS").write_text(
        "".join(f"{a['sha256']}  {a['file']}\n"
                for a in manifest["artifacts"]), encoding="utf-8")

    print(f"[OK] {zip_path}  ({entry['size'] / 1e6:.1f} MB, "
          f"{len(wheels)} wheels)")
    print(f"[OK] {manifest_path}")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--release", action="store_true",
                    help="Build the release named by VERSION (what a v* tag does)")
    ap.add_argument("--windows", action="store_true",
                    help="Fetch win_amd64 wheels into vendor/")
    ap.add_argument("--no-vendor", dest="vendor", action="store_false",
                    help="Skip vendor/ (source-only bundle; needs no network)")
    ap.add_argument("--python-version", default="3.10",
                    help="Target Python for the Windows wheels (default 3.10, "
                         "the minimum this project supports)")
    ap.add_argument("--out", default=str(ROOT / "dist"), type=Path)
    args = ap.parse_args()
    build(release=args.release, windows=args.windows, vendor=args.vendor,
          python_version=args.python_version, out_dir=Path(args.out))


if __name__ == "__main__":
    main()
