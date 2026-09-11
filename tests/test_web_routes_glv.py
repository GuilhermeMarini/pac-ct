"""What the GLV's two templates carry, now that neither carries JavaScript.

The other five tools pin the same two things in their own
`test_web_routes_<tool>.py` (see `test_web_routes_dnp_map.py`): the tag and
the file must agree about the path, and no inline body may come back. Nothing
in this suite RUNS JavaScript, so this is the one failure a test can see --
a rename or a file that did not ship renders the screen blank on a 200.

The GLV adds a third thing nobody else has. It is the only page besides
`/files/` that hands the browser server data, and the only one that does it
per request: `const BOOT = ${BOOT_JSON};` sat inside the script body until the
body left the `.html`, and it travels in a `page-data` block now.
"""
from __future__ import annotations

import json

from pacct.paths import STATIC_DIR
from pacct.web.glv import handler


def test_the_landing_reaches_its_script_file():
    assert '<script src="/static/js/glv/landing.js">' in handler.LANDING_HTML
    assert (STATIC_DIR / "js" / "glv" / "landing.js").is_file()


def test_the_landing_carries_no_inline_script_body():
    assert "<script>" not in handler.LANDING_HTML


def test_the_dashboard_reaches_its_script_file():
    assert '<script src="/static/js/glv/dashboard.js">' in handler.DASHBOARD_HTML
    assert (STATIC_DIR / "js" / "glv" / "dashboard.js").is_file()


def test_the_dashboard_carries_no_inline_script_body():
    assert "<script>" not in handler.DASHBOARD_HTML


def test_the_dashboard_hands_its_boot_state_down_through_page_data():
    """One marker, inside the block, and the block BEFORE the script that
    reads it -- `PacPage.data()` runs at the top level of `dashboard.js`, so a
    block that comes after it is a block that is not in the document yet."""
    html = handler.DASHBOARD_HTML
    assert html.count("${PAGE_DATA}") == 1
    block = '<script type="application/json" id="page-data">${PAGE_DATA}</script>'
    assert block in html
    assert html.index(block) < html.index('<script src="/static/js/glv/dashboard.js">')


def test_the_boot_payload_is_keyed_and_parses():
    """What the handler substitutes has to be JSON the block can hold, keyed
    under `boot` because `dashboard.js` reads `PacPage.data().boot`. A payload
    written at the top level would leave every tab closed and say nothing."""
    payload = {"diagrams": [{"id": "d1"}], "active": "d1", "no_relay": False}
    html = handler.DASHBOARD_HTML.replace(
        "${PAGE_DATA}", json.dumps({"boot": payload}, ensure_ascii=False))
    start = html.index("id=\"page-data\">") + len("id=\"page-data\">")
    end = html.index("</script>", start)
    assert json.loads(html[start:end])["boot"] == payload
