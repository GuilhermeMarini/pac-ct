"""The browser runtimes injected into every page.

The JavaScript itself is a real file now -- `web/static/js/lib/file-picker.js`,
served by the `/static` route `mount.py` already answers. What is left here is
the one tag that reaches it.

Two runtimes live in that file. `SelLibrary` is the picker: six tools need the
same list over the same acervo, so it is written once and injected the way
`SelProgress` already is, and a seventh tool gets it for free -- including the
empty state, which has to link the tab with a RELATIVE href, a cross-page link
being one of the two things the `fetch` shim cannot reach. `PacPage` reads the
`page-data` block, which is how the server hands data to an external `.js` at
all: substituting `${...}` into a `<script>` body stopped being possible the
moment the body left the `.html`.
"""

from __future__ import annotations

# Absolute, and it resolves under every mount: `mount.py` answers `/static/...`
# at the root AND behind any prefix. It has to be absolute -- the prefix shim
# rewrites `fetch` and `XMLHttpRequest`, never a `src` attribute, which is the
# same reason the themes point at an absolute `/static/fonts/`.
CLIENT_JS_URL = "/static/js/lib/file-picker.js"

LIBRARY_TAG = f'<script src="{CLIENT_JS_URL}"></script>\n'


def inject_library_runtime(html: str) -> str:
    """Insert the runtime tag at the end of `<head>`.

    In the `<head>` and NOT before `</body>`, unlike `SelProgress`: every tool
    calls `SelLibrary.picker(...)` at the TOP LEVEL of its own script, so a
    runtime that loads after that script has not been parsed yet when the call
    runs. The `ReferenceError` aborts the whole block, and the tool's page
    comes up blank -- not just the picker, everything the script was going to
    render. `SelProgress` gets away with the tail of the body because it is
    only ever touched from inside an event handler.

    A plain `<script src>` keeps that guarantee: no `defer`, no `async`, so it
    is fetched and executed where it sits, before anything below it parses.

    It goes at the END of the head so the prefix shim (`inject_head`) is
    already in place; neither runtime touches the DOM at definition time, so
    running before `<body>` exists is safe.
    """
    idx = html.lower().rfind("</head>")
    if idx != -1:
        return html[:idx] + LIBRARY_TAG + html[idx:]
    idx = html.rfind("</body>")
    if idx == -1:
        return html + LIBRARY_TAG
    return html[:idx] + LIBRARY_TAG + html[idx:]
