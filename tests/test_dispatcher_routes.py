"""The dispatcher, over a real socket.

Everything here was untested until B12 and was named as untested by the very
docstring of `tests/web_harness.py`: prefix stripping, the class swap,
`Set-Cookie`, and the infrastructure routes. They are the routes with the most
surprising rules in the application, which is a poor combination with being the
only ones nothing covered.

Three of the rules asserted below were paid for once already, and the record of
what they cost is in `docs/ENGINEERING-NOTES.md`: an infrastructure route that
minted a session handed every cookie-less request a fresh `selsid`, so
concurrent requests traded the browser's identity and the project's file list
appeared to erase itself; and the `/vb-updater` -> `/vb-updater/` redirect that
did NOT emit the cookie stranded every upload made before the visitor reached a
tool.
"""

from __future__ import annotations

import json

import pytest

from pacct.paths import STATIC_DIR
from tests.dispatcher_harness import probe_mount, serve, sessions_for


@pytest.fixture
def sessions(tmp_path):
    return sessions_for(tmp_path)


@pytest.fixture
def mounts():
    """A root mount and two prefixed ones, the shape `dashboard.py` builds."""
    return [probe_mount("/"), probe_mount("/tool"), probe_mount("/other-tool")]


# -- prefix stripping and the class swap --------------------------------------

def test_the_tool_never_sees_its_own_prefix(mounts, sessions):
    """The delegation the whole module is built on."""
    with serve(mounts, sessions) as c:
        body = c.get("/tool/state").json()
    assert body["path"] == "/state"


def test_the_query_string_survives_the_strip(mounts, sessions):
    """Only the prefix is removed -- `self.path` keeps everything after it."""
    with serve(mounts, sessions) as c:
        body = c.get("/tool/map?rdb=abc&relay=R1").json()
    assert body["path"] == "/map?rdb=abc&relay=R1"


def test_the_request_becomes_the_tools_handler(mounts, sessions):
    """`self.__class__ = m.handler`, which no annotation can describe."""
    with serve(mounts, sessions) as c:
        assert c.get("/tool/x").json()["became"] == "Probe_tool"
        assert c.get("/other-tool/x").json()["became"] == "Probe_other-tool"


def test_a_path_matching_no_prefix_falls_through_to_the_root_mount(mounts, sessions):
    """The root mount is the fallback, not one prefix among many."""
    with serve(mounts, sessions) as c:
        body = c.get("/anything/at/all").json()
    assert body["became"] == "Probe_root"
    assert body["path"] == "/anything/at/all"


def test_a_prefix_is_matched_on_a_segment_boundary(mounts, sessions):
    """`/toolbox` must not be routed to the tool mounted at `/tool`.

    The match is `path == prefix` or `path.startswith(prefix + "/")`, so the
    slash is what keeps a longer name from being swallowed. Worth pinning:
    a `startswith(prefix)` would send `/toolbox` to `/tool` and strip four
    characters off the front of a path that was never its own.
    """
    with serve(mounts, sessions) as c:
        body = c.get("/toolbox/state").json()
    assert body["became"] == "Probe_root"
    assert body["path"] == "/toolbox/state"


def test_with_no_root_mount_an_unmatched_path_is_404(sessions):
    with serve([probe_mount("/tool")], sessions) as c:
        assert c.get("/nowhere").status == 404


# -- the redirect, and the cookie it nearly forgot -----------------------------

def test_the_bare_prefix_redirects_to_its_slash(mounts, sessions):
    """So relative paths inside the page resolve inside the tool."""
    with serve(mounts, sessions) as c:
        r = c.get("/tool")
    assert r.status == 301
    assert r.headers.get("Location") == "/tool/"


def test_the_redirect_carries_the_session_cookie(mounts, sessions):
    """The bug `Dispatcher.end_headers` exists to fix.

    This response is written by the dispatcher itself, AFTER `resolve()` has
    created the session and WITHOUT swapping `self.__class__` -- so it used to
    fall through to `BaseHTTPRequestHandler.end_headers`, which knows nothing
    about cookies, and the visitor reached the tool still without an identity.
    """
    with serve(mounts, sessions) as c:
        r = c.get("/tool")
    assert r.cookie is not None
    assert r.cookie.startswith("selsid=")


def test_a_tools_page_mints_a_session_once(mounts, sessions):
    """First response carries `Set-Cookie`; a request that presents it does not."""
    with serve(mounts, sessions) as c:
        first = c.get("/tool/")
        assert first.cookie is not None
        sid = first.cookie.split(";")[0]
        again = c.get("/tool/", cookie=sid)
    assert again.cookie is None
    assert again.json()["has_session"] is True


# -- the rule: infrastructure routes never create a session --------------------

INFRA = ["/progress", "/theme.css", "/library"]


@pytest.mark.parametrize("path", INFRA)
def test_an_infrastructure_route_mints_no_session(path, mounts, sessions):
    """A stylesheet is not a visitor.

    When these minted, every cookie-less one added a phantom session and handed
    out a fresh `selsid`, so concurrent requests traded the browser's identity
    between them and each upload landed in a new empty project.
    """
    with serve(mounts, sessions) as c:
        r = c.get(path)
    assert r.status == 200
    assert r.cookie is None
    assert sessions.active_count == 0


@pytest.mark.parametrize("path", INFRA)
def test_an_infrastructure_route_answers_behind_a_prefix_too(path, mounts, sessions):
    """The VB Updater's page asks for `/vb-updater/theme.css`; the home asks
    for `/theme.css`. Both have to answer, and neither may mint."""
    with serve(mounts, sessions) as c:
        r = c.get("/tool" + path)
    assert r.status == 200
    assert r.cookie is None
    assert sessions.active_count == 0


def test_the_theme_css_is_css_and_is_not_cached(mounts, sessions):
    with serve(mounts, sessions) as c:
        r = c.get("/theme.css")
    assert r.headers.get("Content-Type") == "text/css; charset=utf-8"
    # Generated by Python, so a reload has to reflect an edit without the
    # visitor clearing their cache.
    assert r.headers.get("Cache-Control") == "no-store"


def test_the_library_of_a_visitor_with_no_session_is_empty_rather_than_refused(mounts, sessions):
    """`{"files": []}` is the truth: no session, no files.

    It PEEKS rather than resolving, which is what makes an empty answer honest
    instead of the first step of minting one.
    """
    with serve(mounts, sessions) as c:
        body = c.get("/library").json()
    assert body["files"] == []


def test_progress_answers_for_a_job_nobody_started(mounts, sessions):
    with serve(mounts, sessions) as c:
        r = c.get("/progress?job=nao-existe")
    assert r.status == 200
    assert r.headers.get("Content-Type") == "application/json"


# -- POST /theme, the fifth infrastructure route ------------------------------

def test_choosing_a_theme_sets_a_cookie_and_answers_204(mounts, sessions):
    with serve(mounts, sessions) as c:
        r = c.post("/theme", body=json.dumps({"theme": "folha"}).encode(),
                   headers={"Content-Type": "application/json"})
    assert r.status == 204
    assert r.cookie is not None and "seltheme=folha" in r.cookie
    # A theme is a one-year cookie chosen once in the menu, not session state.
    assert sessions.active_count == 0


def test_an_unknown_theme_falls_back_rather_than_failing(mounts, sessions):
    """`themes.normalize` is what decides, and it has a default."""
    with serve(mounts, sessions) as c:
        r = c.post("/theme", body=json.dumps({"theme": "nao-existe"}).encode())
    assert r.status == 204
    assert "seltheme=" in (r.cookie or "")


def test_a_theme_body_over_the_ceiling_is_refused_unread(mounts, sessions):
    """`{"theme": "caderno"}` is small; a body bigger than 4 KiB is not a
    theme choice, and the `Content-Length` comes from the client."""
    with serve(mounts, sessions) as c:
        r = c.post("/theme", body=b"x" * 5000,
                   headers={"Content-Type": "application/json"})
    assert r.status == 400


def test_the_theme_route_answers_only_to_post(mounts, sessions):
    """A GET of `/theme` is not the chooser -- it falls through to a tool."""
    with serve(mounts, sessions) as c:
        body = c.get("/theme").json()
    assert body["became"] == "Probe_root"


# -- /static, and its sandbox -------------------------------------------------

def test_a_static_file_is_served_with_a_type_the_browser_accepts(mounts, sessions):
    """A `.js` served as `application/octet-stream` is refused outright by a
    browser with `nosniff` on, and by every `type="module"` load regardless --
    so the type is not cosmetic."""
    with serve(mounts, sessions) as c:
        r = c.get("/static/fonts/fonts.css")
    assert r.status == 200
    assert r.headers.get("Content-Type") == "text/css; charset=utf-8"


def test_a_font_is_served_immutable_and_the_rest_is_not(mounts, sessions):
    """The fonts do not change: fixed name, fixed content."""
    with serve(mounts, sessions) as c:
        font = c.get("/static/fonts/ibm-plex-mono-400.woff2")
        css = c.get("/static/fonts/fonts.css")
    assert font.headers.get("Content-Type") == "font/woff2"
    assert "immutable" in font.headers.get("Cache-Control", "")
    assert css.headers.get("Cache-Control") == "no-store"


@pytest.mark.parametrize("escape", [
    "/static/../../../etc/passwd",
    "/static/..%2f..%2f..%2fetc%2fpasswd",
])
def test_a_dot_dot_cannot_escape_the_static_root(escape, mounts, sessions):
    """Same sandbox as `/download`: the path is unquoted before it is
    resolved, so the percent-encoded form has to be refused too."""
    with serve(mounts, sessions) as c:
        assert c.get(escape).status == 404


def test_a_static_file_that_does_not_exist_is_404(mounts, sessions):
    with serve(mounts, sessions) as c:
        assert c.get("/static/nao-existe.js").status == 404


def test_only_the_named_extensions_get_a_real_type(mounts, sessions):
    """`_STATIC_TYPES` is deliberately short, and anything outside it is
    served as `application/octet-stream` rather than guessed at."""
    unknown = STATIC_DIR / "contract-probe.bin"
    unknown.write_bytes(b"\x00\x01")
    try:
        with serve(mounts, sessions) as c:
            r = c.get("/static/contract-probe.bin")
        assert r.status == 200
        assert r.headers.get("Content-Type") == "application/octet-stream"
    finally:
        unknown.unlink()


# -- the asymmetry the document records ---------------------------------------

def test_progress_is_matched_on_the_raw_path_and_the_others_on_the_tail(mounts, sessions):
    """An oddity, recorded rather than repaired (B12 describes, it does not
    redesign).

    `/progress` is matched with `path.endswith("/progress")` against the RAW
    path, while `/theme.css`, `/library` and `/static/` are matched against the
    prefix-STRIPPED tail. So a path several segments deep still reaches
    `/progress` and does not reach the other three -- which then fall through
    to the tool. Nothing depends on the difference today; it is pinned here so
    that changing it is a decision rather than an accident.
    """
    with serve(mounts, sessions) as c:
        deep_progress = c.get("/tool/anything/progress")
        deep_theme = c.get("/tool/anything/theme.css")
    assert deep_progress.headers.get("Content-Type") == "application/json"
    assert deep_theme.json()["became"] == "Probe_tool"


def test_the_theme_cookie_reaches_the_tool_as_a_resolved_theme(mounts, sessions):
    """The dispatcher resolves the theme before delegating, so a tool never
    parses a cookie."""
    with serve(mounts, sessions) as c:
        body = c.get("/tool/x", cookie="seltheme=regua").json()
    assert body["theme"] == "regua"


def test_a_dispatcher_built_without_sessions_serves_anyway(mounts):
    """`sessions` is optional in `make_dispatcher`, and the infrastructure
    routes in particular must not depend on it."""
    with serve(mounts, None) as c:
        assert c.get("/theme.css").status == 200
        assert c.get("/tool/x").json()["has_session"] is False
