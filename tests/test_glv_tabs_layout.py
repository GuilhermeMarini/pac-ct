"""The GLV tab strip wraps into rows; it does not scroll sideways.

CSS has no unit test in this suite -- the web tools are verified in the
browser (see docs/ENGINEERING-NOTES.md). What is pinned here is only what comes back in
silence: `overflow-x: auto` on a one-row strip breaks nothing, appears in no
test, and goes off screen exactly when the ceiling of 10 diagrams is used.
Measured in the browser with 10 tabs: 2 rows at 1908 px, 4 at 900 px, 10 at
420 px, and `scrollWidth == clientWidth` in all three.
"""
from __future__ import annotations

import re

from pacct.web import glv


def _tabs_rule() -> str:
    css = glv.load_template("dashboard.html")
    m = re.search(r"#tabs \{(.*?)\}", css, re.S)
    assert m, "o seletor #tabs sumiu do dashboard"
    return m.group(1)


def test_the_tab_strip_wraps():
    assert "flex-wrap: wrap" in _tabs_rule()


def test_the_tab_strip_does_not_scroll_sideways():
    """What was here before. Going back to scrolling hides the following tabs
    behind a bar nobody looks for."""
    assert "overflow-x" not in _tabs_rule()


def test_each_tab_stays_on_one_line():
    """The `nowrap` moved from the STRIP to the ITEM: the strip breaks between
    tabs, and never inside one tab's name."""
    css = glv.load_template("dashboard.html")
    assert "#tabs .tab, #tabs .tab-new { white-space: nowrap; }" in css
    assert "white-space: nowrap" not in _tabs_rule()


def test_a_long_relay_name_cannot_widen_the_strip_past_the_viewport():
    """Wrapping between tabs cannot save you from ONE tab wider than the screen
    -- a flex item does not wrap within itself. The name is cut with an
    ellipsis, and the whole text stays in the tab's `title` (`renderTabs`), so
    nothing is lost."""
    css = glv.load_template("dashboard.html")
    m = re.search(r"#tabs \.tab \.label \{(.*?)\}", css, re.S)
    assert m, "o corte do nome da aba sumiu"
    rule = m.group(1)
    assert "max-width" in rule
    assert "text-overflow: ellipsis" in rule
    # And the name has to CARRY the class that the rule paints.
    assert "name.className = 'label';" in css
