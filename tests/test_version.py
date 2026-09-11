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
from packaging.specifiers import SpecifierSet

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
    for name in ("cfbwrite", "SELlib", "py61850"):
        req_line = _pins(req, name)
        assert len(req_line) == 1, f"{name} must appear once in requirements.txt"
        pin = req_line[0].split("#", 1)[0].strip()
        assert f'"{pin}"' in proj, (
            f"pyproject.toml does not carry the same pin for {name}: {pin!r}")


def test_the_py61850_pin_is_bounded_above():
    """The ceiling is what keeps the NEXT library minor out of THIS release.

    It used to be `<0.4`, and that was insurance against a real defect: 0.4.0
    makes the parser keep XML comments, and `strip_ns` then received a comment
    node's `tag` -- a function, not a string -- so `iter_local` and
    `children_local` raised `AttributeError: 'function' object has no attribute
    'rsplit'` on any SCD carrying one. Real vendor files carry comments.

    **0.4.0 fixed that at the source**: `strip_ns` returns `""` for a tag that
    is not a string, and the guard sits in that one function because every
    traversal in the package -- and every vendor library reaching the tree
    through it -- funnels there. So the ceiling moved to `<0.5` on 2026-09-11,
    for the same reason it existed, and only after the seam nothing in CI
    checks was measured: 931 tests here and 275 in SELlib, both against the
    released SELlib 3.0.1, which is what a clean install actually resolves to
    (SELlib's own pin is an unbounded `py61850>=0.3.0`).

    The FLOOR moved with it. `>=0.3.0,<0.5` would have been the smaller edit
    and would have left 0.3.x admissible and untested; `>=0.4.0` means exactly
    one library line is supported and it is the line the numbers above were
    measured on. That is asserted below, not just described.

    The property pinned here is not the string `<0.5`, it is what the specifier
    admits. PEP 440's exclusive comparison also refuses pre-releases OF THE
    VERSION IT NAMES, which is what keeps a `0.5.0.dev1` out of
    `--atualizar-deps` -- the one path that passes `--pre` to pip.

    Raising the ceiling is a deliberate act, taken once the next minor has been
    verified against this suite, never a side effect of another change. If this
    test is in the way, that is the test working.
    """
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    proj = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    specifiers = [_pins(req, "py61850")[0].split("#", 1)[0].strip()
                  .removeprefix("py61850")]
    specifiers += re.findall(r'"py61850([^"]*)"', proj)
    assert len(specifiers) == 2, specifiers
    for spec in specifiers:
        admits = SpecifierSet(spec)
        assert admits.contains("0.4.0") and admits.contains("0.4.9"), spec
        # One supported line: the 0.3 series is deliberately out, not merely
        # older. A pin that still admitted it would admit a version this
        # release has never been run against.
        assert not admits.contains("0.3.9"), (
            f"{spec!r} still admits 0.3.9: the floor did not move with the "
            f"ceiling, so an untested library line is still installable")
        for blocked in ("0.5.0", "0.5.0.dev1", "0.5.0rc1", "1.0.0"):
            assert not admits.contains(blocked, prereleases=True), (
                f"{spec!r} still admits {blocked}: the pin is not bounded "
                f"below the next release that could change the parser")


def test_the_unpublished_libraries_are_pinned_to_a_commit_not_a_branch():
    """Neither is on PyPI, so both are direct references -- and CI was red for
    a day because `cfbwrite>=1.0` resolves to nothing, which `app.py` turns
    into "no tool boots at all". A direct reference has to name a commit or a
    tag: `@main` is not a dependency, it is a wish."""
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for name in ("cfbwrite", "SELlib"):
        line = _pins(req, name)[0].split("#", 1)[0].strip()
        if " @ git+" not in line:
            continue  # published; a plain specifier is right again
        ref = line.split("git+", 1)[1]
        assert "@" in ref, f"{name}: a git reference must name a commit or tag"
        assert not ref.rstrip("/").endswith("@main"), (
            f"{name}: pinned to a moving branch")


def _ci_matrix() -> list[tuple[int, int]]:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines()
                if ln.strip().startswith("python-version:"))
    return sorted((int(a), int(b)) for a, b in
                  re.findall(r'"(\d+)\.(\d+)"', line))


def _requires_python_floor() -> tuple[int, int]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*">=(\d+)\.(\d+)"', text)
    assert m, "requires-python must declare a floor"
    return int(m[1]), int(m[2])


def test_ci_starts_at_the_version_pyproject_promises():
    """`requires-python` is a promise to whoever installs this. A matrix that
    starts above it means the oldest supported Python is never actually run."""
    assert _ci_matrix()[0] == _requires_python_floor()


def test_the_ci_matrix_has_no_holes_in_it():
    """Skipping a version is how a real incompatibility hides.

    The concrete one here is **3.13**: that is the release where `telnetlib`
    was removed from the standard library, and the vendored `selprotopy` still
    does a bare `import telnetlib`. `pacct/compat.py` covers it by aliasing
    `telnetlib3`'s backport into `sys.modules` -- and a matrix that jumped
    3.12 to 3.14 would never exercise the boundary where that starts mattering.
    """
    matrix = _ci_matrix()
    expected = [(matrix[0][0], minor)
                for minor in range(matrix[0][1], matrix[-1][1] + 1)]
    assert matrix == expected, f"gap in the CI matrix: {matrix}"


def test_every_version_ci_runs_is_a_version_the_metadata_claims():
    """The classifiers are what a reader believes without running anything;
    the matrix is what is actually measured. They must say the same thing."""
    proj = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    claimed = {tuple(int(p) for p in m)
               for m in re.findall(
                   r'"Programming Language :: Python :: (\d+)\.(\d+)"', proj)}
    assert claimed == set(_ci_matrix())
