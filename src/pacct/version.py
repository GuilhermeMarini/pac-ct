"""The version, and the three questions the distribution asks about it.

`VERSION` at the project root is the single source: `pyproject.toml` reads it
(`version = {file = "VERSION"}`), `pacct.__version__` re-exports it, and
`tools/build_dist.py` stamps it into the bundle's manifest. One file, so a
release cannot disagree with itself.

Semantic versioning, with the boundaries written for *this* project rather
than borrowed:

* **MAJOR** -- a change to what gets written into a relay: the `SET_D`
  round-trip contract, the RDB write path, or a settings format. Also removing
  a tool or a supported relay family.
* **MINOR** -- a new tool, a new relay model, a new theme, a new route.
* **PATCH** -- fixes, documentation, dependency bumps.

`VERSION` names the release being PREPARED. Until the matching `v<version>`
tag exists, every build off this tree is a snapshot -- `1.4.0.dev0+g<sha>` --
and `is_release()` answers False for it. That is the whole gate behind rule 4
of the updater: a snapshot is never offered as an update, because the sha it
carries may never become a release at all.

The spelling is the PEP 440 one. `1.4.0-dev+g<sha>` (how the plan wrote it)
and `1.4.0.dev0+g<sha>` are the same version -- PEP 440 normalises the first
into the second -- and it is the second that `pip` and `importlib.metadata`
report back, so writing the first would guarantee a cosmetic mismatch between
what the bundle says and what the installed package says.

This module deliberately imports nothing from `pacct`. `tools/build_dist.py`
loads it straight from its path, so a build never needs the application's
runtime dependencies installed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# `X.Y.Z` and nothing else. A release is the only thing the updater will ever
# offer, so the shape it must recognise is exactly that shape -- anything
# richer would be a PEP 440 parser this project does not need and could get
# subtly wrong.
_RELEASE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# A snapshot: the release being prepared, marked as preceding it, plus the
# commit it came from.
_SNAPSHOT_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.dev0\+g([0-9a-f]{7,40})$")

UNKNOWN_VERSION = "0.0.0+unknown"


def find_project_root(start: Path | None = None) -> Path | None:
    """The directory holding `VERSION`, or None when there is none above us.

    The marker is `VERSION` **plus** `app.py`: `VERSION` alone is a common
    enough filename that a stray one in a parent directory would be picked up,
    and a version read out of somebody else's file is worse than no version.
    """
    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        if (parent / "VERSION").is_file() and (parent / "app.py").is_file():
            return parent
    return None


def read_version(root: Path | None = None) -> str:
    """What `VERSION` says, or what the installed distribution says.

    A clone reads the file. A `pip install`ed package has no project root at
    all, so the answer comes from the distribution metadata -- which setuptools
    filled in from this same file at build time.
    """
    found = find_project_root(root) if root is None else Path(root)
    if found is not None and (found / "VERSION").is_file():
        text = (found / "VERSION").read_text(encoding="utf-8").strip()
        if text:
            return text
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("pac-ct")
        except PackageNotFoundError:
            return UNKNOWN_VERSION
    except ImportError:  # pragma: no cover -- importlib.metadata is stdlib
        return UNKNOWN_VERSION


def is_release(value: str) -> bool:
    """Whether `value` names a published release rather than a snapshot."""
    return _RELEASE_RE.match(value.strip()) is not None


def ordering_key(value: str) -> tuple[int, int, int, int] | None:
    """A sortable key, or None when the string is neither shape.

    The fourth element is what keeps a snapshot BELOW the release it is
    preparing: `1.4.0.dev0+gabc` sorts under `1.4.0`, which is the truth --
    it is a commit on the way there, not the thing itself.
    """
    text = value.strip()
    m = _RELEASE_RE.match(text)
    if m:
        return int(m[1]), int(m[2]), int(m[3]), 1
    m = _SNAPSHOT_RE.match(text)
    if m:
        return int(m[1]), int(m[2]), int(m[3]), 0
    return None


def is_newer(candidate: str, current: str) -> bool:
    """Whether `candidate` is a release strictly newer than `current`.

    Both halves matter. A candidate that is not a release is refused outright
    (updater rule 4), and a `current` this module cannot parse -- a hand-edited
    `VERSION`, an install nobody built here -- answers False rather than
    guessing, because offering an update over a version you cannot place is
    how an engineer's install goes backwards.
    """
    if not is_release(candidate):
        return False
    left, right = ordering_key(candidate), ordering_key(current)
    if left is None or right is None:
        return False
    return left > right


def git_sha(root: Path, length: int = 12) -> str | None:
    """The short sha of `root`'s HEAD, or None outside a git work tree."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    sha = out.stdout.strip()
    return sha[:length] if re.fullmatch(r"[0-9a-f]{40}", sha) else None


def snapshot_version(base: str, sha: str) -> str:
    """`1.4.0` + `abc123def456` -> `1.4.0.dev0+gabc123def456`."""
    if not is_release(base):
        raise ValueError(
            f"VERSION must name a release (X.Y.Z) to build a snapshot from; "
            f"got {base!r}"
        )
    return f"{base}.dev0+g{sha}"


def build_version(root: Path, *, release: bool) -> str:
    """The version a bundle built from `root` should carry.

    `release=True` is the tag path and hands back `VERSION` untouched -- the
    tag is what promises the tree matches. `release=False` is every other
    build, and stamps the commit in, so a snapshot on an engineer's machine
    can always be traced back to the tree it came from.
    """
    base = read_version(root)
    if release:
        return base
    sha = git_sha(root)
    return snapshot_version(base, sha) if sha else f"{base}.dev0+gunknown"
