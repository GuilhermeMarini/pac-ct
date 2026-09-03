"""What goes into a bundle, and what must never go into one.

The bundle is what runs in a substation, so two of these are about safety
rather than tidiness: a real `config.ini` holds the relay's ACC/2AC passwords
and must not leave the machine it was typed on, and the version the bundle
reports has to be the version its manifest promises -- that is the number the
updater compares.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _build_module():
    """Import `tools/build_dist.py` without running it (it is a script)."""
    spec = importlib.util.spec_from_file_location(
        "_build_dist_under_test", ROOT / "tools" / "build_dist.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_build_dist_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One source-only build, reused. `--no-vendor`: the wheels need a network
    and are not what these assertions are about."""
    out = tmp_path_factory.mktemp("dist")
    mod = _build_module()
    manifest = mod.build(release=False, windows=False, vendor=False,
                         python_version="3.10", out_dir=out)
    return mod, out, manifest


def tmp_requirements() -> Path:
    """A requirements file with one of each shape, written where the test can
    reach it. Testing the split against the LIVE file made the assertion
    change every time a dependency did."""
    import tempfile
    fh = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8")
    fh.write("olefile>=0.47\n"
             "# a comment, and a blank line\n\n"
             "somelib @ git+https://example.invalid/somelib@abc123\n"
             "py61850>=0.2.0.dev1\n")
    fh.close()
    return Path(fh.name)


def _names(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as z:
        return z.namelist()


def test_the_zip_has_one_top_level_directory(built):
    _, out, manifest = built
    zip_path = out / manifest["artifacts"][0]["file"]
    roots = {Path(n).parts[0] for n in _names(zip_path)}
    assert roots == {f"pac-ct-{manifest['version']}"}, (
        "the bundle must unzip into one directory: it is what makes unzipping "
        "safe anywhere, and it is the directory the updater stages into")


def test_the_bundle_reports_the_version_the_manifest_promises(built):
    """A snapshot writes `1.4.0.dev0+g<sha>` into its own VERSION, so an
    installed copy names the commit it came from -- not the release it was
    merely heading towards."""
    _, out, manifest = built
    zip_path = out / manifest["artifacts"][0]["file"]
    prefix = f"pac-ct-{manifest['version']}"
    with zipfile.ZipFile(zip_path) as z:
        inside = z.read(f"{prefix}/VERSION").decode("utf-8").strip()
    assert inside == manifest["version"]
    assert ".dev0+g" in inside and manifest["release"] is False


def test_no_config_ini_travels_only_the_example(built):
    """`config/config.ini` is where a commissioning engineer types a live
    substation's ACC/2AC passwords. It is gitignored for that reason and it
    must not ride out in a zip either -- so `config/` travels for exactly one
    file, the model the app seeds from."""
    _, out, manifest = built
    names = _names(out / manifest["artifacts"][0]["file"])
    config = [n for n in names if "/config/" in n]
    assert config, "the example has to be there or a fresh install has no model"
    assert all(n.endswith("config.ini.example") for n in config), config


def test_the_repository_scaffolding_stays_behind(built):
    """Tests, docs, samples, mockups and the caches are the repository, not the
    product. The samples alone are 63 MB."""
    _, out, manifest = built
    names = _names(out / manifest["artifacts"][0]["file"])
    for unwanted in ("/tests/", "/docs/", "/samples/", "/mockups/",
                     "__pycache__", "/.git/", "/cache/"):
        assert not any(unwanted in n for n in names), unwanted
    assert not any(n.endswith(".pyc") for n in names)


def test_what_the_bundle_needs_in_order_to_run(built):
    _, out, manifest = built
    prefix = f"pac-ct-{manifest['version']}"
    names = set(_names(out / manifest["artifacts"][0]["file"]))
    for needed in ("app.py", "requirements.txt", "VERSION", "LICENSE",
                   "NOTICE.md", "INSTALAR.txt", "pac-ct.sh", "pac-ct.cmd",
                   "src/pacct/paths.py", "src/pacct/update.py",
                   "config/config.ini.example"):
        assert f"{prefix}/{needed}" in names, needed
    # The vendored MIT library the GLV's Fast Message path imports.
    assert any(n.startswith(f"{prefix}/selprotopy/") for n in names)


def test_the_manifest_sha_is_the_sha_of_the_file_beside_it(built):
    mod, out, manifest = built
    entry = manifest["artifacts"][0]
    zip_path = out / entry["file"]
    assert entry["sha256"] == mod.sha256_of(zip_path)
    assert entry["size"] == zip_path.stat().st_size
    sums = (out / "SHA256SUMS").read_text(encoding="utf-8")
    assert f"{entry['sha256']}  {entry['file']}" in sums


def test_a_release_build_refuses_a_version_that_is_not_a_release(tmp_path,
                                                                monkeypatch):
    """`--release` is what a `v*` tag runs. A release whose contents do not
    match its number is the one artefact this project must never produce: it is
    what the updater trusts, sight unseen, on a machine in a substation."""
    mod = _build_module()
    monkeypatch.setattr(mod.version_mod, "read_version",
                        lambda root=None: "1.4.0.dev0")
    with pytest.raises(SystemExit):
        mod.build(release=True, windows=False, vendor=False,
                  python_version="3.10", out_dir=tmp_path)


def test_the_two_halves_of_requirements_are_fetched_by_different_commands():
    """`pip download --only-binary=:all:` refuses a direct reference -- it has
    to build one. So a direct reference is built with `pip wheel` and the rest
    downloaded with the platform flags, and the split is by SHAPE rather than
    by name -- which is what lets it keep working now that `cfbwrite` and
    `selfiles` are published and neither is a direct reference any more."""
    mod = _build_module()
    direct, indexed = mod.split_requirements(tmp_requirements())
    assert [d.split(" @ ")[0] for d in direct] == ["somelib"]
    assert indexed == ["olefile>=0.47", "py61850>=0.2.0.dev1"]


def test_todays_requirements_need_no_direct_reference_at_all():
    """Both libraries are on PyPI since 2026-09-03, so `pin_direct_references`
    is dormant. It stays because the mechanism is the guard: the day something
    is pinned to a URL again, the offline bundle must not silently go back to
    needing a network."""
    mod = _build_module()
    direct, _ = mod.split_requirements(ROOT / "requirements.txt")
    assert direct == [], direct


def test_a_second_build_adds_to_the_manifest_instead_of_erasing_it(tmp_path):
    """One release, two zips (portable and Windows), one document listing
    both. The release workflow runs the build twice."""
    mod = _build_module()
    mod.build(release=False, windows=False, vendor=False,
              python_version="3.10", out_dir=tmp_path)
    manifest = mod.build(release=False, windows=True, vendor=False,
                         python_version="3.10", out_dir=tmp_path)
    platforms = {a["platform"] for a in manifest["artifacts"]}
    assert platforms == {"any", "win_amd64"}
    on_disk = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert len(on_disk["artifacts"]) == 2
    assert len((tmp_path / "SHA256SUMS").read_text(
        encoding="utf-8").strip().splitlines()) == 2


def test_the_bundle_pins_direct_references_to_the_wheel_beside_them(tmp_path):
    """`--no-index` disables the package INDEX, not a direct reference.

    `cfbwrite @ git+https://github.com/...` sends pip to git even with
    `--no-index --find-links vendor/`, and a substation has neither a network
    nor, usually, git -- so the offline install would fail on the one machine
    it exists for. It passed on the build machine only because pip had the
    wheel it had just built in its own cache, which is exactly the kind of
    pass that means nothing.

    The bundle therefore carries `cfbwrite==<version of the wheel in
    vendor/>`. The repository's own requirements.txt keeps the direct
    reference: that is what a clone, CI and `pip download` need.
    """
    mod = _build_module()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "requirements.txt").write_text(
        "olefile>=0.47\n"
        "cfbwrite @ git+https://example.invalid/cfbwrite@abc123\n"
        "selfiles @ git+https://example.invalid/selfiles@def456\n",
        encoding="utf-8")
    pinned = mod.pin_direct_references(
        stage, ["cfbwrite-1.0.0-py3-none-any.whl",
                "selfiles-1.0.0-py3-none-any.whl",
                "olefile-0.47-py2.py3-none-any.whl"])
    assert pinned == ["cfbwrite==1.0.0", "selfiles==1.0.0"]
    text = (stage / "requirements.txt").read_text(encoding="utf-8")
    assert "git+" not in text
    assert "cfbwrite==1.0.0" in text and "selfiles==1.0.0" in text
    assert "olefile>=0.47" in text


def test_a_direct_reference_with_no_wheel_fails_the_build(tmp_path):
    """Better a failed build than a bundle whose offline install cannot work."""
    mod = _build_module()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "requirements.txt").write_text(
        "cfbwrite @ git+https://example.invalid/cfbwrite@abc123\n",
        encoding="utf-8")
    with pytest.raises(SystemExit):
        mod.pin_direct_references(stage, ["olefile-0.47-py2.py3-none-any.whl"])
