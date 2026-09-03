"""`VERSION` is the single source, and three files have to agree with it.

The version is not decoration here: it is what the updater compares, what the
release tag has to match, and what an engineer reads off a bundle to say which
build is in the substation. Every assertion below pins a way those could
silently drift apart.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from pacct import __version__
from pacct import version as V

ROOT = Path(__file__).resolve().parent.parent


def test_the_version_file_names_a_release_shape():
    """`VERSION` names the release being prepared, so it is always `X.Y.Z`.

    Not `1.4.0.dev0`: the dev marker is added by the BUILD, per commit
    (`build_version`), because that is where the sha is known. A `VERSION`
    that already said `.dev0` would make `--release` unbuildable and would
    make every snapshot say `.dev0.dev0`.
    """
    text = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", text), text
    assert V.is_release(text)


def test_pacct_reexports_the_file():
    assert __version__ == (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_the_version_lookup_does_not_wander_up_into_home():
    """The first implementation walked EVERY parent looking for a file called
    `VERSION`, which outside a clone reaches `/home/<user>/VERSION` and reads a
    stranger's number as this application's. The marker is `VERSION` **and**
    `app.py` now, so a match has to be a PAC CT tree."""
    assert V.find_project_root(Path("/")) is None
    assert V.find_project_root() == ROOT


@pytest.mark.parametrize("candidate,current,expected", [
    ("1.5.0", "1.4.0", True),
    ("1.4.1", "1.4.0", True),
    ("2.0.0", "1.9.9", True),
    ("1.4.0", "1.4.0", False),
    ("1.3.0", "1.4.0", False),
    # Rule 4: a snapshot is never offered, whatever it claims to be.
    ("1.9.0.dev0+gabc1234", "1.4.0", False),
    ("v1.5.0", "1.4.0", False),
    ("", "1.4.0", False),
    # A snapshot IS behind the release it prepares, so 1.4.0 is an update for
    # somebody running 1.4.0.dev0+g<sha>.
    ("1.4.0", "1.4.0.dev0+gabc1234", True),
    # A version this module cannot place gets no offer at all -- guessing is
    # how an install goes backwards.
    ("1.5.0", "nonsense", False),
])
def test_is_newer(candidate, current, expected):
    assert V.is_newer(candidate, current) is expected


def test_snapshot_spelling_is_the_pep440_one():
    """`1.4.0-dev+g<sha>` and `1.4.0.dev0+g<sha>` are the same version under
    PEP 440 normalisation, and the second is what pip and
    `importlib.metadata` report back. Writing the first would guarantee the
    bundle and the installed package disagree on screen."""
    assert V.snapshot_version("1.4.0", "abc123def456") == "1.4.0.dev0+gabc123def456"
    assert not V.is_release(V.snapshot_version("1.4.0", "abc123def456"))
    assert V.ordering_key("1.4.0.dev0+gabc123def456") < V.ordering_key("1.4.0")


def test_a_snapshot_cannot_be_built_from_a_version_that_is_not_a_release():
    with pytest.raises(ValueError):
        V.snapshot_version("1.4.0.dev0", "abc1234")


def test_build_version_stamps_the_commit_when_it_is_not_a_release():
    snapshot = V.build_version(ROOT, release=False)
    assert snapshot.startswith(V.read_version(ROOT) + ".dev0+g")
    assert V.build_version(ROOT, release=True) == V.read_version(ROOT)


def _pins(text: str, name: str) -> list[str]:
    return [ln for ln in text.splitlines()
            if ln.split("#", 1)[0].strip().startswith(name)]


def test_requirements_and_pyproject_pin_the_same_libraries():
    """`requirements.txt` is what `app.py` bootstraps and what the offline
    bundle is built from; `pyproject.toml` is what a `pip install .` reads. A
    venv built from one and a bundle built from the other is exactly the kind
    of difference nobody notices until a relay is on the bench."""
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    proj = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for name in ("cfbwrite", "selfiles"):
        req_line = _pins(req, name)
        assert len(req_line) == 1, f"{name} must appear once in requirements.txt"
        pin = req_line[0].split("#", 1)[0].strip()
        assert f'"{pin}"' in proj, (
            f"pyproject.toml does not carry the same pin for {name}: {pin!r}")


def test_the_unpublished_libraries_are_pinned_to_a_commit_not_a_branch():
    """Neither is on PyPI, so both are direct references -- and CI was red for
    a day because `cfbwrite>=1.0` resolves to nothing, which `app.py` turns
    into "no tool boots at all". A direct reference has to name a commit or a
    tag: `@main` is not a dependency, it is a wish."""
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for name in ("cfbwrite", "selfiles"):
        line = _pins(req, name)[0].split("#", 1)[0].strip()
        if " @ git+" not in line:
            continue  # published; a plain specifier is right again
        ref = line.split("git+", 1)[1]
        assert "@" in ref, f"{name}: a git reference must name a commit or tag"
        assert not ref.rstrip("/").endswith("@main"), (
            f"{name}: pinned to a moving branch")
