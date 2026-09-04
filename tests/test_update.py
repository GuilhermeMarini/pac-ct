"""The updater's five rules, one test each and then some.

These are not style rules. Every one of them is the difference between an
update that costs a coffee and an update that costs a commissioning engineer a
day in a substation with no internet -- so each is pinned where it lives.
"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from pacct import update as U

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# helpers: a bundle, and an install to put it in
# ---------------------------------------------------------------------------

def make_bundle(dest: Path, version: str, *, prefix: str | None = None,
                extra: dict[str, str] | None = None) -> Path:
    """A minimal bundle shaped like the real one."""
    root = prefix if prefix is not None else f"pac-ct-{version}"
    path = dest / f"pac-ct-{version}.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{root}/VERSION", version + "\n")
        z.writestr(f"{root}/app.py", "# stub\n")
        z.writestr(f"{root}/requirements.txt", "")
        for name, body in (extra or {}).items():
            z.writestr(name, body)
    return path


def make_install(tmp: Path, versions: list[str]) -> U.Layout:
    """`PAC-CT/` with the given versions installed and `current` on the last."""
    root = tmp / "PAC-CT"
    for v in versions:
        d = root / "versions" / v
        d.mkdir(parents=True)
        (d / "VERSION").write_text(v + "\n", encoding="utf-8")
        (d / "app.py").write_text("# stub\n", encoding="utf-8")
    layout = U.Layout(root=root, version_dir=root / "versions" / versions[-1])
    U.ensure_userdata(layout)
    U.point_current(layout, layout.version_dir)
    return layout


def release_with(version: str, assets: dict[str, str]) -> U.Release:
    return U.Release(
        version=version, tag=f"v{version}", notes="",
        assets=tuple(U.Asset(name=n, url=u, size=0) for n, u in assets.items()))


# ---------------------------------------------------------------------------
# Rule 4: only releases, and only newer ones
# ---------------------------------------------------------------------------

def test_a_snapshot_is_never_offered_as_an_update():
    """A `-dev` snapshot names a commit that may never become a release at all.
    Offering one would install a tree nobody promised anything about."""
    assert U.update_available(release_with("1.9.0.dev0+gabc1234", {}), "1.4.0") is False
    assert U.update_available(release_with("1.5.0", {}), "1.4.0") is True
    assert U.update_available(release_with("1.4.0", {}), "1.4.0") is False
    assert U.update_available(release_with("1.3.0", {}), "1.4.0") is False


def test_a_tag_with_a_v_prefix_becomes_a_plain_version(monkeypatch):
    """GitHub tags are `v1.5.0`; every comparison in this project is on
    `1.5.0`. Getting this wrong makes every release un-offerable, silently."""
    payload = {"tag_name": "v1.5.0", "body": "notas",
               "assets": [{"name": "pac-ct-1.5.0.zip",
                           "browser_download_url": "https://example.invalid/z",
                           "size": 10}]}
    monkeypatch.setattr(U, "_get_json", lambda url, timeout: payload)
    rel = U.check_latest()
    assert rel.version == "1.5.0" and rel.tag == "v1.5.0"
    assert rel.asset("pac-ct-1.5.0.zip") is not None
    assert rel.asset("pac-ct-1.5.0-win_amd64.zip") is None


# ---------------------------------------------------------------------------
# Rule 1: offline is an answer, not a crash
# ---------------------------------------------------------------------------

def test_no_internet_is_a_message_and_not_a_traceback(monkeypatch):
    """The caller is a command somebody typed in a substation. "Sem internet"
    is the correct output; a stack trace is not. This is the same reason
    `install_requirements` only runs pip when an import fails."""
    def boom(url, timeout):
        raise OSError("Network is unreachable")
    monkeypatch.setattr(U, "_get_json", boom)
    with pytest.raises(U.UpdateError) as exc:
        U.check_latest()
    assert "internet" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Rule 2: verify before unpacking
# ---------------------------------------------------------------------------

def test_a_bundle_with_the_wrong_sha_is_discarded_not_unpacked(tmp_path):
    bundle = make_bundle(tmp_path, "1.5.0")
    with pytest.raises(U.UpdateError) as exc:
        U.verify(bundle, "0" * 64)
    assert "sha256" in str(exc.value)
    assert not bundle.exists(), "a bundle that failed verification must not stay"


def test_a_manifest_that_does_not_describe_the_bundle_is_refused(tmp_path):
    """"No sha to check" and "sha matches" must never take the same branch --
    that is how verification quietly stops happening."""
    with pytest.raises(U.UpdateError):
        U.expected_sha256({"artifacts": []}, "pac-ct-1.5.0.zip")
    with pytest.raises(U.UpdateError):
        U.expected_sha256(
            {"artifacts": [{"file": "pac-ct-1.5.0.zip", "sha256": "short"}]},
            "pac-ct-1.5.0.zip")
    good = {"artifacts": [{"file": "pac-ct-1.5.0.zip", "sha256": "a" * 64}]}
    assert U.expected_sha256(good, "pac-ct-1.5.0.zip") == "a" * 64


def test_perform_update_verifies_before_it_unpacks(tmp_path, monkeypatch):
    """The order is the property. A corrupted or tampered bundle must never
    reach `zipfile.extractall`, and `current` must not move."""
    layout = make_install(tmp_path, ["1.4.0"])
    bundle = make_bundle(tmp_path, "1.5.0")
    manifest = {"artifacts": [{"file": "pac-ct-1.5.0.zip", "sha256": "b" * 64}]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def fake_download(url: str, dest: Path, timeout: float = 120.0) -> Path:
        src = manifest_path if url.endswith("manifest.json") else bundle
        dest.write_bytes(src.read_bytes())
        return dest

    unpacked = []
    monkeypatch.setattr(U, "download", fake_download)
    monkeypatch.setattr(U, "unpack", lambda *a: unpacked.append(a))

    rel = release_with("1.5.0", {"pac-ct-1.5.0.zip": "https://x.invalid/b.zip",
                                 "manifest.json": "https://x.invalid/manifest.json"})
    with pytest.raises(U.UpdateError):
        U.perform_update(layout, rel, windows=False)
    assert unpacked == [], "the bundle was unpacked despite a bad sha256"
    assert layout.current.resolve().name == "1.4.0"


def test_a_release_without_a_manifest_is_refused(tmp_path):
    layout = make_install(tmp_path, ["1.4.0"])
    rel = release_with("1.5.0", {"pac-ct-1.5.0.zip": "https://x.invalid/b.zip"})
    with pytest.raises(U.UpdateError) as exc:
        U.perform_update(layout, rel, windows=False)
    assert "manifest.json" in str(exc.value)


# ---------------------------------------------------------------------------
# Rule 3: stage, then swap
# ---------------------------------------------------------------------------

def test_unpack_flattens_the_single_root_and_refuses_anything_else(tmp_path):
    dest = tmp_path / "versions"
    dest.mkdir()
    bundle = make_bundle(tmp_path, "1.5.0")
    out = U.unpack(bundle, dest / "1.5.0")
    assert (out / "app.py").is_file() and (out / "VERSION").is_file()

    two_roots = tmp_path / "two.zip"
    with zipfile.ZipFile(two_roots, "w") as z:
        z.writestr("a/x", "1")
        z.writestr("b/x", "1")
    with pytest.raises(U.UpdateError):
        U.unpack(two_roots, dest / "1.6.0")


def test_unpack_refuses_an_entry_that_climbs_out_of_the_destination(tmp_path):
    """A bundle is downloaded from the internet and extracted with the rights
    of whoever runs the tool. `../` in a member name is the oldest trick there
    is, and `extractall` on its own does not stop it on every Python."""
    dest = tmp_path / "versions"
    dest.mkdir()
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("pac-ct-1.5.0/app.py", "# stub")
        z.writestr("pac-ct-1.5.0/../../escaped.txt", "pwned")
    with pytest.raises(U.UpdateError):
        U.unpack(evil, dest / "1.5.0")
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_unpack_never_overwrites_a_version_that_is_already_installed(tmp_path):
    dest = tmp_path / "versions"
    (dest / "1.5.0").mkdir(parents=True)
    with pytest.raises(U.UpdateError):
        U.unpack(make_bundle(tmp_path, "1.5.0"), dest / "1.5.0")


@pytest.mark.skipif(os.name == "nt", reason="junctions, not symlinks")
def test_the_swap_is_a_repointing_and_so_is_the_rollback(tmp_path):
    layout = make_install(tmp_path, ["1.4.0", "1.5.0"])
    assert layout.current.resolve().name == "1.5.0"
    assert U.current_version(layout) == "1.5.0"
    assert U.installed_versions(layout) == ["1.4.0", "1.5.0"]

    back = U.rollback(layout)
    assert back.name == "1.4.0"
    assert layout.current.resolve().name == "1.4.0"

    U.point_current(layout, layout.versions / "1.5.0")
    assert layout.current.resolve().name == "1.5.0"


def test_rollback_with_nowhere_to_go_says_so(tmp_path):
    layout = make_install(tmp_path, ["1.4.0"])
    with pytest.raises(U.UpdateError):
        U.rollback(layout)


# ---------------------------------------------------------------------------
# Rule 5: userdata is never touched
# ---------------------------------------------------------------------------

def test_an_update_does_not_read_move_or_migrate_userdata(tmp_path, monkeypatch):
    """`userdata/` holds `config.ini` with the relay's ACC/2AC passwords, the
    content-addressed RDB cache and the uploaded RDBs. The whole reason the
    install is versioned is that a swap must not be able to reach it."""
    layout = make_install(tmp_path, ["1.4.0"])
    secret = layout.userdata / "config" / "config.ini"
    secret.write_text("[tcp]\nacc_password = OTTER\n", encoding="utf-8")
    rdb = layout.userdata / "cache" / "rdb" / "abc"
    rdb.mkdir(parents=True)
    (rdb / "meta.json").write_text("{}", encoding="utf-8")
    before = {p: p.read_bytes() for p in layout.userdata.rglob("*") if p.is_file()}

    bundle = make_bundle(tmp_path, "1.5.0")
    digest = U.sha256_of(bundle)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(
        {"artifacts": [{"file": "pac-ct-1.5.0.zip", "sha256": digest}]}),
        encoding="utf-8")

    def fake_download(url: str, dest: Path, timeout: float = 120.0) -> Path:
        src = manifest_path if url.endswith("manifest.json") else bundle
        dest.write_bytes(src.read_bytes())
        return dest

    monkeypatch.setattr(U, "download", fake_download)
    monkeypatch.setattr(U, "build_venv", lambda *a, **k: Path("python"))
    rel = release_with("1.5.0", {"pac-ct-1.5.0.zip": "https://x.invalid/b.zip",
                                 "manifest.json": "https://x.invalid/manifest.json"})
    target = U.perform_update(layout, rel, windows=False)

    assert target.name == "1.5.0"
    assert layout.current.resolve().name == "1.5.0"
    after = {p: p.read_bytes() for p in layout.userdata.rglob("*") if p.is_file()}
    assert after == before, "the update touched userdata/"


def test_ensure_userdata_adopts_an_existing_one_as_it_stands(tmp_path):
    layout = make_install(tmp_path, ["1.4.0"])
    marker = layout.userdata / "config" / "config.ini"
    marker.write_text("mine", encoding="utf-8")
    U.ensure_userdata(layout)
    assert marker.read_text(encoding="utf-8") == "mine"


# ---------------------------------------------------------------------------
# The layout itself
# ---------------------------------------------------------------------------

def test_a_development_clone_is_not_a_versioned_install():
    """`Layout.detect` answering None is what stops `--atualizar` from
    swapping a git checkout under its own feet."""
    assert U.Layout.detect(ROOT) is None
    with pytest.raises(U.UpdateError):
        U.Layout.would_be(ROOT)


def test_detect_derives_everything_from_where_the_code_sits(tmp_path):
    version_dir = tmp_path / "PAC-CT" / "versions" / "1.4.0"
    version_dir.mkdir(parents=True)
    layout = U.Layout.detect(version_dir)
    assert layout is not None
    assert layout.root == tmp_path / "PAC-CT"
    assert layout.userdata == tmp_path / "PAC-CT" / "userdata"
    assert layout.current == tmp_path / "PAC-CT" / "current"
    assert layout.launcher.name == ("pac-ct.cmd" if os.name == "nt"
                                    else "pac-ct.sh")


def test_the_paths_module_reads_the_two_variables_the_launcher_sets(monkeypatch,
                                                                    tmp_path):
    """The versioned layout only works because `PACCT_ROOT` and
    `PACCT_DATA_DIR` are separate: the first moves with every update, the
    second must not. `paths.py` gained that split in Phase 1 for this."""
    import importlib

    version_dir = tmp_path / "PAC-CT" / "versions" / "1.4.0"
    (version_dir / "config").mkdir(parents=True)
    (version_dir / "app.py").touch()
    (version_dir / "config" / "config.ini.example").touch()
    userdata = tmp_path / "PAC-CT" / "userdata"
    userdata.mkdir(parents=True)

    monkeypatch.setenv("PACCT_ROOT", str(version_dir))
    monkeypatch.setenv("PACCT_DATA_DIR", str(userdata))
    paths = importlib.reload(importlib.import_module("pacct.paths"))
    try:
        assert paths.PROJECT_ROOT == version_dir
        assert paths.DEFAULT_CONFIG_FILE == userdata / "config" / "config.ini"
        assert paths.CACHE_DIR == userdata / "cache"
        assert paths.RDBS_DIR == userdata / "rdbs"
        # The templates and fonts travel INSIDE the package, so they follow the
        # version and not the data.
        assert paths.STATIC_DIR.is_relative_to(Path(sys.modules["pacct"].__file__).parent)
    finally:
        monkeypatch.undo()
        importlib.reload(importlib.import_module("pacct.paths"))


def test_a_fresh_versioned_install_finds_the_config_model(tmp_path, monkeypatch):
    """The `.example` ships with the VERSION; `config.ini` lives in `userdata/`.

    Resolving the model under `DATA_ROOT` was wrong the moment the two roots
    could differ. Measured on a real bundle installed into
    `PAC-CT/versions/<v>/`: `ensure_config_file` raised "Nao ha modelo para
    copiar" and the dashboard died before it opened the port -- an install that
    completes and then cannot start.
    """
    import importlib

    version_dir = tmp_path / "PAC-CT" / "versions" / "1.4.0"
    (version_dir / "config").mkdir(parents=True)
    (version_dir / "app.py").touch()
    (version_dir / "config" / "config.ini.example").write_text(
        "[tcp]\nip = 192.0.2.10\n", encoding="utf-8")
    userdata = tmp_path / "PAC-CT" / "userdata"
    (userdata / "config").mkdir(parents=True)

    monkeypatch.setenv("PACCT_ROOT", str(version_dir))
    monkeypatch.setenv("PACCT_DATA_DIR", str(userdata))
    paths = importlib.reload(importlib.import_module("pacct.paths"))
    try:
        assert paths.EXAMPLE_CONFIG_FILE == (
            version_dir / "config" / "config.ini.example")
        assert paths.ensure_config_file(paths.DEFAULT_CONFIG_FILE) is True
        assert (userdata / "config" / "config.ini").is_file()
        # ... and the seeded file is the engineer's, so it stays in userdata
        # where the next update cannot reach it.
        assert paths.DEFAULT_CONFIG_FILE.is_relative_to(userdata)
    finally:
        monkeypatch.undo()
        importlib.reload(importlib.import_module("pacct.paths"))


@pytest.mark.parametrize("code,expected", [
    (404, "nenhuma versao publicada"),
    (403, "60 por hora"),
])
def test_the_two_http_answers_that_are_not_a_problem_here(monkeypatch, code,
                                                          expected):
    """`/releases/latest` answers 404 until the first release exists, and 403
    when the anonymous hourly budget runs out. Neither is a fault on this
    machine, and printing the number would send somebody hunting a network
    problem that is not there."""
    import urllib.error

    def raise_http(url, timeout):
        raise urllib.error.HTTPError(url, code, "", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(U, "_get_json", raise_http)
    with pytest.raises(U.UpdateError) as exc:
        U.check_latest()
    assert expected in str(exc.value)


# ---------------------------------------------------------------------------
# The portable install: one folder, data inside it
# ---------------------------------------------------------------------------

def make_portable(tmp: Path, version: str = "1.4.0") -> Path:
    """A folder as an engineer actually keeps it: code and data together."""
    root = tmp / "pac-ct-portable"
    (root / "src" / "pacct").mkdir(parents=True)
    (root / "selprotopy").mkdir()
    (root / "vendor").mkdir()
    (root / "config").mkdir()
    (root / "cache" / "rdb" / "abc").mkdir(parents=True)
    (root / "rdbs").mkdir()
    (root / "data" / "wordbits").mkdir(parents=True)
    (root / "app.py").write_text("# stub\n", encoding="utf-8")
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    (root / "requirements.txt").write_text("", encoding="utf-8")
    (root / "src" / "pacct" / "__init__.py").write_text("old\n", encoding="utf-8")
    (root / "config" / "config.ini.example").write_text("[tcp]\n", encoding="utf-8")
    # the engineer's half
    (root / "config" / "config.ini").write_text(
        "[tcp]\nacc_password = SEGREDO\n", encoding="utf-8")
    (root / "rdbs" / "projeto.rdb").write_text("bytes", encoding="utf-8")
    (root / "cache" / "rdb" / "abc" / "meta.json").write_text("{}", encoding="utf-8")
    (root / "data" / "wordbits" / "SEL-999.json").write_text("{}", encoding="utf-8")
    return root


def user_data(root: Path) -> dict[str, bytes]:
    keep = ["config/config.ini", "rdbs/projeto.rdb",
            "cache/rdb/abc/meta.json", "data/wordbits/SEL-999.json"]
    return {k: (root / k).read_bytes() for k in keep}


def test_a_portable_folder_is_told_apart_from_the_other_two(tmp_path):
    """Three shapes, three answers. A git checkout is refused outright --
    updating it would throw uncommitted work away, and `git pull` is the right
    command there."""
    portable = make_portable(tmp_path)
    assert U.install_kind(portable) == "portable"

    layout = make_install(tmp_path / "v", ["1.4.0"])
    assert U.install_kind(layout.version_dir) == "versioned"

    (portable / ".git").mkdir()
    assert U.install_kind(portable) == "checkout"


def test_a_portable_update_replaces_the_code_and_keeps_the_data(tmp_path,
                                                                monkeypatch):
    """The whole point of the portable folder: `config.ini` with the relay
    passwords, the RDB cache, the uploads and an imported DNP profile all live
    INSIDE it, and an update must walk past every one of them."""
    root = make_portable(tmp_path, "1.4.0")
    before = user_data(root)

    bundle = make_bundle(tmp_path, "1.5.0", extra={
        "pac-ct-1.5.0/src/pacct/__init__.py": "new\n",
        "pac-ct-1.5.0/config/config.ini.example": "[tcp]\nnovo = 1\n",
    })
    digest = U.sha256_of(bundle)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(
        {"artifacts": [{"file": "pac-ct-1.5.0.zip", "sha256": digest}]}),
        encoding="utf-8")

    def fake_download(url: str, dest: Path, timeout: float = 120.0) -> Path:
        src = manifest_path if url.endswith("manifest.json") else bundle
        dest.write_bytes(src.read_bytes())
        return dest

    monkeypatch.setattr(U, "download", fake_download)
    rel = release_with("1.5.0", {"pac-ct-1.5.0.zip": "https://x.invalid/b.zip",
                                 "manifest.json": "https://x.invalid/manifest.json"})
    U.perform_portable_update(root, rel, windows=False)

    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.5.0"
    assert (root / "src" / "pacct" / "__init__.py").read_text() == "new\n"
    # the .example is code and travels; the config.ini beside it is not.
    assert "novo" in (root / "config" / "config.ini.example").read_text()
    assert user_data(root) == before, "the update touched the engineer's data"


def test_a_failed_portable_update_puts_the_folder_back(tmp_path, monkeypatch):
    """Swapping in place is genuinely riskier than repointing a junction, so
    the failure path is the one that has to work: an interrupted swap must not
    leave a folder that is half one version and half another."""
    root = make_portable(tmp_path, "1.4.0")
    before = user_data(root)
    old_code = (root / "src" / "pacct" / "__init__.py").read_text()

    bundle = make_bundle(tmp_path, "1.5.0", extra={
        "pac-ct-1.5.0/src/pacct/__init__.py": "new\n"})
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(
        {"artifacts": [{"file": "pac-ct-1.5.0.zip",
                        "sha256": U.sha256_of(bundle)}]}), encoding="utf-8")

    def fake_download(url: str, dest: Path, timeout: float = 120.0) -> Path:
        dest.write_bytes((manifest_path if url.endswith("manifest.json")
                          else bundle).read_bytes())
        return dest

    def boom(*a, **k):
        raise OSError("disk full halfway through")

    monkeypatch.setattr(U, "download", fake_download)
    monkeypatch.setattr(U, "_swap_in", boom)
    rel = release_with("1.5.0", {"pac-ct-1.5.0.zip": "https://x.invalid/b.zip",
                                 "manifest.json": "https://x.invalid/manifest.json"})
    with pytest.raises(U.UpdateError):
        U.perform_portable_update(root, rel, windows=False)

    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.4.0"
    assert (root / "src" / "pacct" / "__init__.py").read_text() == old_code
    assert user_data(root) == before


def test_the_updater_runs_even_when_the_dependencies_are_missing():
    """An updater that cannot run on a broken install cannot fix one.

    `pacct/__init__` configures `selfiles` at import, so `import pacct.update`
    used to die with `ModuleNotFoundError: No module named 'selfiles'` --
    exactly when somebody needs `--atualizar` most. Nothing is silently
    misconfigured by tolerating it: if `selfiles` is absent then every module
    that reads an SEL file fails on its own import, at the point of use.
    """
    import inspect

    import pacct

    src = inspect.getsource(pacct._configure_selfiles)
    assert "except ImportError" in src, (
        "a missing dependency must not stop the updater from importing")
    # update.py itself must stay free of the app's runtime dependencies.
    assert "selfiles" not in Path(U.__file__).read_text(encoding="utf-8")
