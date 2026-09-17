"""The two absences the offline bundle is built on: no node, and no network.

Stage 2 committed the frontend's build output under `src/`, which makes both of
these easy -- and makes it easy to stop noticing they were ever hard, which is
how they break. `tools/build_dist.py` has no node step because `frontend/` is
not in its `TREE_DIRS`, and `app.py` reaches for pip only when a package fails
to import. Neither fact is written down anywhere the machine can read it. These
tests are that writing down.

**Both are imposed, not assumed.** The precedent is `pin_direct_references()`:
the failure it guards passed on the build machine because pip's cache was warm,
and what settled it was `PIP_NO_CACHE_DIR=1` and a measurement of zero network
reads. So the node test runs the build with node actually removed from `PATH`,
not merely with nothing calling it; and the boot test runs the bundle under a
`sitecustomize.py` that refuses every non-loopback connection, in the child and
in anything the child spawns -- pip included, since `PYTHONPATH` is inherited.
`test_the_network_block_refuses_a_real_address` guards the guard: a blocker
that silently stopped blocking would make the boot test pass for the wrong
reason.
"""
from __future__ import annotations

import http.client
import importlib.util
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SERVED_PILOT = ROOT / "src" / "pacct" / "web" / "static" / "js" / "vlan_mapper" / "landing.js"

# Installed into the child through `PYTHONPATH`, where the interpreter imports
# it before it runs anything else. `0.0.0.0` is allowed because the dashboard
# binds it; AF_UNIX addresses are not tuples and are not the network.
_BLOCKER = '''
import socket

MARKER = "pac-ct test: network access blocked"
_LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


def _is_local(address):
    if not isinstance(address, tuple) or not address:
        return True
    host = str(address[0])
    return host in _LOCAL or host.startswith("127.")


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo


def connect(self, address):
    if not _is_local(address):
        raise OSError(f"{MARKER}: connect({address!r})")
    return _connect(self, address)


def connect_ex(self, address):
    if not _is_local(address):
        raise OSError(f"{MARKER}: connect_ex({address!r})")
    return _connect_ex(self, address)


def getaddrinfo(host, *args, **kwargs):
    if not _is_local((host,)):
        raise OSError(f"{MARKER}: getaddrinfo({host!r})")
    return _getaddrinfo(host, *args, **kwargs)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.getaddrinfo = getaddrinfo
'''

_MARKER = "pac-ct test: network access blocked"


def _blocked_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A child environment that cannot reach anything but loopback."""
    site = tmp_path / "no_network_site"
    site.mkdir(exist_ok=True)
    (site / "sitecustomize.py").write_text(_BLOCKER, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(site)
    env["PYTHONUNBUFFERED"] = "1"
    env.update(extra)
    return env


def _path_without_node() -> str:
    """`PATH` with every directory that holds a node toolchain removed.

    Removing the whole of `PATH` would prove something else: the build shells
    out to `git` for the version it stamps, and a failure there would look like
    a pass for this test's purposes and a red build for everyone.
    """
    kept = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        d = Path(entry)
        if any((d / exe).exists() for exe in ("node", "npm", "npx",
                                              "node.exe", "npm.cmd")):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def test_the_bundle_builds_with_node_absent_from_path(tmp_path):
    env = dict(os.environ)
    env["PATH"] = _path_without_node()
    out = tmp_path / "dist"

    # First prove the removal worked. Without this the test would pass on a
    # machine where the scrub silently missed, which is the same defect as
    # asserting that nothing called node.
    probe = subprocess.run(
        [sys.executable, "-c",
         "import shutil;print(shutil.which('node'), shutil.which('npm'))"],
        env=env, capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "None None", probe.stdout

    built = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_dist.py"),
         "--no-vendor", "--out", str(out)],
        cwd=ROOT, env=env, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    assert list(out.glob("*.zip")), built.stdout


def test_the_network_block_refuses_a_real_address(tmp_path):
    """The guard's own guard.

    A numeric address on purpose: a hostname would fail to resolve on a machine
    with no DNS and the test would pass without the blocker doing anything.
    """
    probe = subprocess.run(
        [sys.executable, "-c",
         "import socket;socket.create_connection(('8.8.8.8', 53), timeout=2)"],
        env=_blocked_env(tmp_path), capture_output=True, text=True)
    assert probe.returncode != 0
    assert _MARKER in probe.stderr, probe.stderr


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(port: int, path: str) -> tuple[int, str, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        return r.status, r.headers.get("content-type", ""), r.read()
    finally:
        conn.close()


def test_a_built_bundle_boots_and_serves_the_pilot_with_no_network(tmp_path):
    """The whole constraint, end to end.

    A source-only bundle carries no wheels, so this is the substation case with
    nothing pre-arranged: `app.py` finds every dependency importable and never
    reaches for pip. If it ever did, the blocker turns that into a failure here
    rather than into a thirty-second stall in a substation.

    The script's bytes are compared, not just its status: a bundle that served
    a 200 and an empty file would satisfy a status check and render the tool
    blank, which is the failure mode this project has already paid for once.
    """
    spec = importlib.util.spec_from_file_location(
        "_build_dist_for_boot_test", ROOT / "tools" / "build_dist.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_build_dist_for_boot_test"] = mod
    spec.loader.exec_module(mod)

    out = tmp_path / "dist"
    manifest = mod.build(release=False, windows=False, vendor=False,
                         python_version=mod.MIN_PYTHON, out_dir=out)
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(out / manifest["artifacts"][0]["file"]) as z:
        z.extractall(unpacked)
    bundle = next(p for p in unpacked.iterdir() if p.is_dir())
    assert not (bundle / "frontend").exists(), "the build travelled with the bundle"

    port = _free_port()
    log = tmp_path / "boot.log"
    env = _blocked_env(tmp_path, PACCT_DATA_DIR=str(tmp_path / "data"))
    with log.open("wb") as sink:
        proc = subprocess.Popen(
            [sys.executable, "app.py", "--web", "--no-venv", "--port", str(port)],
            cwd=bundle, env=env, stdout=sink, stderr=subprocess.STDOUT)
    try:
        for _ in range(120):
            if proc.poll() is not None:
                raise AssertionError(
                    "the bundle did not boot:\n" + log.read_text(encoding="utf-8", errors="replace"))
            try:
                if _get(port, "/")[0]:
                    break
            except OSError:
                time.sleep(0.25)
        else:
            raise AssertionError(
                "the bundle never answered:\n" + log.read_text(encoding="utf-8", errors="replace"))

        status, ctype, body = _get(port, "/vlan-mapper/")
        assert status == 200, log.read_text(encoding="utf-8", errors="replace")
        assert ctype.startswith("text/html")
        assert b'<script src="/static/js/vlan_mapper/landing.js">' in body

        status, ctype, body = _get(port, "/static/js/vlan_mapper/landing.js")
        assert status == 200
        assert ctype.startswith("text/javascript")
        assert body == _SERVED_PILOT.read_bytes()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)

    # Nothing in the boot may have reached for a network.
    assert _MARKER not in log.read_text(encoding="utf-8", errors="replace")
