"""Graphical Logic Viewer: N open diagrams, each with its own relay.

    state.py      LiveState (+ clear)
    poll.py       the three polling threads, one per relay family
    gle_pages.py  pages, bits and analogs of a GLE
    notes.py      notes, highlighter and groups, keyed by relay name
    link.py       RelayLink + LinkPool: one connection per relay, refcounted
                  across the diagrams that ask for it. Lifecycle only: the
                  protocol lives in the transport
    transport/    the transport seam -- the Protocol in __init__.py and the
                  telnet one (SEL Fast Message) in telnet.py
    diagram.py    GlvDiagram: one open diagram, connected or not
    handler.py    the routes
    templates/    dashboard.html and landing.html

What used to be the whole of `dashboard.py`. Only the home and the `main()`
stayed there.
"""

from __future__ import annotations

from pacct.paths import GLV_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Reads a GLV template. Read at import, as the raw string was before."""
    return (GLV_TEMPLATES_DIR / name).read_text(encoding="utf-8")
