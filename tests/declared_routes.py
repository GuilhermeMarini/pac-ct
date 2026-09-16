"""Every HTTP route this application answers, declared rather than discovered.

This is the machine-readable half of `docs/HTTP-CONTRACT.md`. The document
carries the reasoning -- what a route is for, which oddities are deliberate,
which are merely old -- and this carries the list, so that
`test_http_contract.py` can assert the two never drift from the tree.

**It is test data, not a shipped artifact.** Nothing under `src/` imports it,
and the dispatcher still routes from each tool's own `if path ==` chain. The
contract's authority comes from the document plus the test that guards it,
which is the shape `test_version.py` already uses to keep `requirements.txt`
and `pyproject.toml` from disagreeing about a pin. A runtime route table with
no runtime consumer would be a second source of truth, which is the defect the
contract exists to remove rather than to add.

Two things are deliberately NOT declared here, because a test cannot check
them from the syntax tree and a declaration nobody verifies is worse than none:

- **Parameters and response bodies.** They live in the document. The drift
  test guards `(mount, method, path)`, which is what AST extraction can see.
- **Status codes.** A route's failure modes are prose, for the same reason.
"""

from __future__ import annotations

from typing import NamedTuple


class Route(NamedTuple):
    """One declared route. `mount` is the package, not the URL prefix."""

    mount: str
    method: str
    path: str
    summary: str


#: Package under `pacct.web` -> the prefix `dashboard.py` mounts it at.
#: The home is mounted at the root, so its prefix is the empty string; a
#: request matching no other prefix falls through to it (`mount.py`'s `root`).
MOUNTS: dict[str, str] = {
    "dashboard": "",
    "files": "/files",
    "glv": "/glv",
    "vb_updater": "/vb-updater",
    "vlan_mapper": "/vlan-mapper",
    "gle_exporter": "/gle-exporter",
    "settings_compare": "/settings-compare",
    "dnp_map": "/dnp-map",
    "gle_tabs": "/gle-tabs",
}

#: The landing page of every mount answers to these three spellings. Only `/`
#: is declared below; the other two are aliases the extractor folds in, and the
#: document records which mounts spell it which way (they do not agree).
LANDING_ALIASES = ("/", "", "/index.html")

#: Served by the dispatcher in `mount.py`, before any prefix is matched and --
#: this is the rule, not an accident -- before a session is resolved. None of
#: them mints a `selsid`. They answer at EVERY prefix as well as at the root,
#: so `/glv/theme.css` and `/theme.css` are the same route.
INFRASTRUCTURE: tuple[Route, ...] = (
    Route("mount", "GET", "/progress", "Job progress for the shared bar. Matched on the RAW path by endswith, not on the stripped tail."),
    Route("mount", "GET", "/theme.css", "The active theme's generated stylesheet. no-store while the theme system is iterated on."),
    Route("mount", "POST", "/theme", "Choose a theme. 204 plus a one-year Set-Cookie; 400 on a body over 4 KiB, refused unread."),
    Route("mount", "GET", "/static/", "Prefix route. Fonts, licences and the pages' JavaScript, sandboxed to STATIC_DIR."),
    Route("mount", "GET", "/library", "The visitor's project files. PEEKS the session and never creates one, so an empty answer means no session."),
)

#: The nine mounts' own routes, 83 of them.
ROUTES: tuple[Route, ...] = (
    # -- dashboard (the home, mounted at the root) ---------------------------
    Route("dashboard", "GET", "/", "The menu. Resolves <!--VERSION--> here; the nav and menu body are resolved by the dispatcher, which knows the theme."),
    Route("dashboard", "GET", "/home-state", "Liveness sentinel. The only one written as a JSON string literal rather than through _send_json."),
    Route("dashboard", "GET", "/update-check", "Cached GitHub release check, one question per process rather than one per tab."),

    # -- files (the project library, the roadmap's project_files) ------------
    Route("files", "GET", "/", "The project files screen."),
    Route("files", "GET", "/download", "?sha256= -- takes a CONTENT HASH, not a path, and therefore serves the shared RDB cache. Not the tools' /download."),
    Route("files", "POST", "/upload", "Raw body, name in X-Filename. The only route that reads a non-JSON request body by design."),
    Route("files", "POST", "/remove", "{sha256} -- drops one file from this project."),

    # -- glv (Graphical Logic Viewer) ----------------------------------------
    Route("glv", "GET", "/", "The dashboard shell. 302s to /novo when the visitor has no open tabs."),
    Route("glv", "GET", "/novo", "The picker. A landing page in its own right, not a redirect target only."),
    Route("glv", "GET", "/landing-state", "What the picker shows: the project's RDBs and the current selection."),
    Route("glv", "GET", "/diagrams", "The open tabs. Same payload the shell boots with."),
    Route("glv", "GET", "/meta", "?d= -- one diagram's metadata."),
    Route("glv", "GET", "/values", "?d=&page= -- live values. Emitted through _send, not _send_json, so it is JSON that the JSON scan does not see."),
    Route("glv", "GET", "/events", "?d=&page= -- Server-Sent Events. Held open, no Content-Length, ends when the connection does."),
    Route("glv", "GET", "/pages/", "Prefix route. ?d=&have= -- the page's SVG, or 204 with no body when the client says it already has it."),
    Route("glv", "GET", "/group-state", "?d= -- the diagram's note groups."),
    Route("glv", "GET", "/note", "?d= -- the diagram's notes."),
    Route("glv", "GET", "/highlights", "?d= -- the diagram's highlights."),
    Route("glv", "GET", "/vb-source", "?d= -- where each VB in the diagram comes from in the SCD."),
    Route("glv", "GET", "/unreachable", "?d= -- logic the relay cannot reach, as JSON."),
    Route("glv", "GET", "/unreachable.txt", "?d= -- the same, as a text report. The only route whose path carries an extension."),
    Route("glv", "GET", "/debug/analogs", "?d= -- the only route with a path SEGMENT, and the only one named debug."),
    Route("glv", "POST", "/select-rdb", "{sha256} -- point the tool at an RDB already in the project."),
    Route("glv", "POST", "/diagrams", "Open one diagram as a tab. Same path as the GET, different method, different meaning."),
    Route("glv", "POST", "/diagrams/batch", "Open several at once."),
    Route("glv", "POST", "/diagrams/close", "?d= -- close a tab and pick the next active one."),
    Route("glv", "POST", "/diagrams/activate", "?d= -- make a tab active. Returns {active} and nothing else."),
    Route("glv", "POST", "/connect", "?d= -- connect to the relay. 202 with a job handle; the only 202 in the application."),
    Route("glv", "POST", "/disconnect", "?d= -- drop the relay connection."),
    Route("glv", "POST", "/period", "?d= {interval_ms} -- polling cadence."),
    Route("glv", "POST", "/group-state", "?d= -- write the note groups."),
    Route("glv", "POST", "/note", "?d= -- write a note. Refuses a body over NOTE_MAX_BYTES."),
    Route("glv", "POST", "/highlights", "?d= -- write the highlights."),

    # -- vb_updater ----------------------------------------------------------
    Route("vb_updater", "GET", "/", "The landing page."),
    Route("vb_updater", "GET", "/vb-state", "Liveness sentinel."),
    Route("vb_updater", "GET", "/state", "The tool's session state."),
    Route("vb_updater", "GET", "/download", "?file= -- takes a PATH and is sandboxed to this session's out/ and scd/."),
    Route("vb_updater", "GET", "/compare", "?relay=&ied=&gle= -- a rendered comparison page. HTML, with text/plain errors."),
    Route("vb_updater", "POST", "/select-rdb", "{sha256} -- choose the RDB from the project."),
    Route("vb_updater", "POST", "/select-scd", "{sha256} -- choose the SCD. Shares a branch with /select-rdb and differs only by the kind it demands."),
    Route("vb_updater", "POST", "/apply", "Apply one direction's update. Status is chosen from the model payload's own ok."),
    Route("vb_updater", "POST", "/apply-batch", "Apply several. 422 carries per-item results so the screen can say which one failed."),
    Route("vb_updater", "POST", "/export-descriptions", "Write the descriptions out as a spreadsheet."),
    Route("vb_updater", "POST", "/import-descriptions", "Read edited descriptions back in."),

    # -- vlan_mapper ---------------------------------------------------------
    Route("vlan_mapper", "GET", "/", "The landing page."),
    Route("vlan_mapper", "GET", "/vlan-state", "Liveness sentinel."),
    Route("vlan_mapper", "GET", "/state", "The tool's session state."),
    Route("vlan_mapper", "POST", "/select-scd", "{sha256} -- read IEDs and VLANs out of one SCD. The tool writes nothing."),

    # -- gle_exporter --------------------------------------------------------
    Route("gle_exporter", "GET", "/", "The landing page."),
    Route("gle_exporter", "GET", "/gle-state", "Liveness sentinel."),
    Route("gle_exporter", "GET", "/state", "The tool's session state."),
    Route("gle_exporter", "GET", "/download", "?file= -- takes a PATH, sandboxed to this session's out/ and xlsx/."),
    Route("gle_exporter", "POST", "/select-rdb", "{sha256} -- choose the RDB from the project."),
    Route("gle_exporter", "POST", "/export", "Write the variable comments out."),
    Route("gle_exporter", "POST", "/import", "Apply edited comments back into the RDB. All-or-nothing; status chosen from the model payload's ok."),

    # -- settings_compare ----------------------------------------------------
    Route("settings_compare", "GET", "/", "The landing page."),
    Route("settings_compare", "GET", "/settings-state", "Liveness sentinel."),
    Route("settings_compare", "GET", "/state", "The PROJECT's RDBs, not the ones this tool has adopted. Listing also registers them."),
    Route("settings_compare", "POST", "/groups", "{relays:[{rdb_key,relay_name}]} -- the setting groups those relays share."),
    Route("settings_compare", "POST", "/diff", "{relays,groups} -- the comparison. Status is derived from whether the payload carries an error key."),

    # -- dnp_map -------------------------------------------------------------
    Route("dnp_map", "GET", "/", "The landing page."),
    Route("dnp_map", "GET", "/editor", "The map editor screen."),
    Route("dnp_map", "GET", "/copiar", "The copy-between-relays screen."),
    Route("dnp_map", "GET", "/rdbs", "Every RDB in the project, newest first."),
    Route("dnp_map", "GET", "/relays", "?rdb= -- the relays inside one RDB."),
    Route("dnp_map", "GET", "/wordbits", "The word-bit models loaded at import. Session-independent."),
    Route("dnp_map", "GET", "/map", "?rdb=&relay= -- one relay's DNP map."),
    Route("dnp_map", "GET", "/download", "?f= -- NOT ?file=. Takes a path, sandboxed to this session's out/. Answers 403 for every failure, including a missing parameter."),
    Route("dnp_map", "POST", "/edit", "Edit one point."),
    Route("dnp_map", "POST", "/swap", "Exchange two points."),
    Route("dnp_map", "POST", "/copy-session", "Copy the staged map between sessions of one relay."),
    Route("dnp_map", "POST", "/copy-to-relays", "Copy a map onto other relays."),
    Route("dnp_map", "POST", "/import-profile", "Install a SEL DNP3 device profile as the model's name domain."),
    Route("dnp_map", "POST", "/export", "Write the edited RDB out."),

    # -- gle_tabs ------------------------------------------------------------
    Route("gle_tabs", "GET", "/", "The landing page."),
    Route("gle_tabs", "GET", "/editor", "One GLE per page. Reads rdb/relay/gle from its own query string."),
    Route("gle_tabs", "GET", "/rdbs", "Every RDB in the project, newest first."),
    Route("gle_tabs", "GET", "/gles", "?rdb= -- the GLEs inside one RDB."),
    Route("gle_tabs", "GET", "/pages", "?rdb=&relay=&gle= -- one GLE's pages. Note the plural is a LIST here and an SVG under glv."),
    Route("gle_tabs", "GET", "/download", "?f= -- NOT ?file=, and a bare FILE NAME resolved against out/, not a path. 403 for every failure."),
    Route("gle_tabs", "POST", "/stage", "Validate one GLE's edit and hold it. Stages nothing on refusal."),
    Route("gle_tabs", "POST", "/reset", "Drop what is staged."),
    Route("gle_tabs", "POST", "/gerar", "Write the staged edits into a new RDB."),
)


def declared_pairs() -> set[tuple[str, str, str]]:
    """`(mount, method, path)` for every tool route declared above."""
    return {(r.mount, r.method, r.path) for r in ROUTES}
