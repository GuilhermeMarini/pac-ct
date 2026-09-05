"""`requirements.txt` has to be installable, or NOTHING in this project boots.

`app.py` runs `pip install -r requirements.txt` and `sys.exit()`s when it
fails, so one bad pin takes down all nine tools -- including the six that have
nothing to do with the pinned package. This file pins the two properties that
kept that from being noticed: the file is installable, and `app.py` can still
read a package name out of every line of it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements.txt"


def _app_module():
    """Import `app.py` without running it (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("_app_under_test",
                                                  ROOT / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_app_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _lines():
    return [ln.split("#", 1)[0].strip()
            for ln in REQ.read_text(encoding="utf-8").splitlines()
            if ln.split("#", 1)[0].strip()]


def test_py61850_requirement_reaches_a_version_that_exists():
    """The MMS client the GLV calls (`MmsClient`, `decode_data_definition`,
    `py61850.mms.pdu`) only exists from 0.2.0.dev1, and 0.2.0 final does not
    exist yet -- PyPI carries 0.0.1 and the dev. So the line has to MENTION a
    pre-release: that is what lets `pip install -r` take a dev without `--pre`
    on the command line (PEP 440). A plain `py61850>=0.2.0` matches nothing
    published, `pip install -r` fails, and `app.py` exits on it -- a clean
    clone then boots NO tool at all, not just the MMS one.

    The line was a `@ git+...@<sha>` pin while nothing was published. That is
    still acceptable here, but it costs the automatic pickup of a new dev, and
    an upstream history rewrite orphans the sha.
    """
    line = [ln for ln in _lines() if ln.lower().startswith("py61850")]
    assert len(line) == 1, "py61850 must appear exactly once"
    line = line[0]
    if " @ git+" in line:
        assert "@" in line.split("git+", 1)[1], (
            "a git reference must name a commit or tag, not a moving branch")
        return
    assert "dev" in line or "rc" in line or "a" in line.split("py61850", 1)[1], (
        "a version specifier that does not mention a pre-release resolves to "
        "nothing installable: 0.2.0 final does not exist")


def test_the_upgrade_path_asks_pip_for_pre_releases():
    """`--atualizar-deps` is the only place that goes looking for a NEW
    version, and every version of py61850 that carries the MMS client is a
    pre-release. Without `--pre` there the flag would report success and
    upgrade nothing.

    The normal boot must NOT upgrade: `install_requirements` only runs pip
    when a package fails to import, because a substation has no internet and
    `app.py` exits on a pip failure.
    """
    import inspect

    src = inspect.getsource(_app_module().install_requirements)
    assert '"--pre"' in src and '"--upgrade"' in src
    assert "if upgrade:" in src


def test_every_requirement_line_yields_a_package_name_app_py_can_import():
    """`app.py:missing_packages` imports each parsed name to decide whether to
    run pip at all. A PEP 508 direct reference whose 'name' is the whole URL is
    never importable, so pip would re-clone the repository on every launch."""
    app = _app_module()
    names = app.parse_requirements(REQ)
    assert "py61850" in names
    for name in names:
        assert " " not in name and "/" not in name, (
            f"{name!r} is not a package name app.py can import")


def test_the_venv_check_does_not_follow_the_symlink_back_home(monkeypatch):
    """`python3 app.py --web` is the documented way to launch, and it died.

    `.venv/bin/python` is a symlink chain that ends at the BASE interpreter, so
    `VENV_PYTHON.resolve()` gives `/usr/bin/pythonX.Y` -- exactly what
    `sys.executable` resolves to when running OUTSIDE the venv. Comparing the
    two resolved executables therefore answered "already inside" while standing
    outside: `main()` skipped `relaunch_in_venv()`, `install_requirements` tried
    to install into the system Python, PEP 668 refused, and the boot exited 1
    with `[ERRO] Falha ao instalar dependencias`. Measured on a rebuilt venv:
    both sides resolved to `/usr/bin/python3.12`.

    `sys.prefix` is the question actually being asked -- it points at the venv
    from inside and at the base from outside.
    """
    app = _app_module()
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    assert app.is_inside_target_venv() is False, (
        "the check said 'inside the venv' while running on the base "
        "interpreter; app.py would skip the relaunch and install system-wide")


def test_the_venv_check_says_yes_from_inside_the_venv(monkeypatch):
    """The other direction, and it is the dangerous one: a check that answers
    False from INSIDE the venv makes `relaunch_in_venv()` exec itself forever."""
    app = _app_module()
    monkeypatch.setattr(sys, "prefix", str(app.VENV_DIR))
    assert app.is_inside_target_venv() is True


def test_a_bundle_installs_from_its_own_wheels_without_being_asked():
    """`vendor/` exists in a bundle and never in a clone, so its presence is
    the question already answered.

    Shipped 1.4.0 required `--offline` for that, which meant an engineer who
    unzipped the package in a substation and ran `app.py --web` -- the command
    the README gives -- went to an index that is not reachable. The bundle was
    carrying 12 wheels the run refused to look at. Requiring a flag there is
    requiring the user to already know why it broke.
    """
    import inspect

    src = inspect.getsource(_app_module().main)
    assert "VENDOR_DIR.is_dir()" in src, (
        "the presence of vendor/ must be what turns the offline install on")
    assert "args.online" in src, "there must be an escape hatch for a bad vendor/"


def test_a_missing_dependency_is_explained_not_traced():
    """`ModuleNotFoundError: No module named 'sellib'` plus a traceback tells
    a commissioning engineer nothing, and the two causes need opposite
    answers: an incomplete unzip is a broken copy, a missing dependency is an
    install that never ran. The distinction is on disk, so it costs one
    `is_file()` to say which."""
    app = _app_module()
    msg = app._explain_import_failure(
        ModuleNotFoundError("No module named 'sellib'", name="sellib"))
    assert "sellib" in msg and "app.py --web" in msg
    assert "Traceback" not in msg
    # ... and the other cause names the real problem instead of a dependency.
    broken = app._explain_import_failure(
        ModuleNotFoundError("No module named 'pacct'", name="pacct"))
    assert "pacct" in broken
