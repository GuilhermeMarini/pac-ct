# Idea 1 — Specification of the end state

What the application looks like when this is done. Written so it can be built
against, and so that partial completion is recognisable.

The stack choice is not fixed here — see `01-stack.md`. This document
specifies the *shape*, which is the same under A1+B3 and under A0+B1, and says
where the two diverge.

---

## 1. Layering

Four layers, each with one job, named so the boundary is checkable.

```
┌─────────────────────────────────────────────────────────┐
│  Frontend            static assets, no Python           │
│  web/frontend/  (source)  →  web/static/  (built)       │
├─────────────────────────────────────────────────────────┤
│  HTTP contract       routes, request/response schemas   │
│  web/api/<tool>.py                                      │
├─────────────────────────────────────────────────────────┤
│  Domain              no HTTP, no HTML, no sessions      │
│  web/<tool>/model.py, export.py, …  (unchanged)         │
├─────────────────────────────────────────────────────────┤
│  Libraries           sellib, py61850, cfbwrite          │
│  (external, untouched)                                  │
└─────────────────────────────────────────────────────────┘
```

**The rule that makes it checkable:** no module in the domain layer may import
anything from the HTTP or frontend layer. That is already almost true — it is
what let `sellib` and `cfbwrite` be extracted at all — and it should become a
test, not a habit.

---

## 2. Deployment — unchanged

- **One process, one port, 8765.** The Python process serves both the JSON
  routes and the built frontend as static files.
- **No CORS.** Same origin, so none is needed. If a document ever proposes
  CORS configuration, the deployment split (option C) has crept back in and
  should be rejected explicitly.
- **The `selsid` cookie is unchanged** — same name, same TTL, same
  per-session directory under `cache/sessions/<sid>/`, same sweeper.
- **`tools/windows/wsl-portproxy-setup.ps1` needs no change.**
- **No CDN.** All assets ship in the bundle, as the `.woff2` fonts already do.

---

## 3. The HTTP contract

### 3.1 Routes

Every tool exposes JSON under `/<tool>/api/…` and nothing else dynamic. The
current split is already close: measured today, dnp_map is 32 JSON responses
to 6 HTML, vb_updater 33 to 10, gle_exporter 19 to 6, gle_tabs 16 to 6,
project_files 13 to 5, settings_compare 9 to 3, glv 26 to 21.

The HTML responses that remain are `/`, `/editor`, `/download` and similar.
Under this specification:

- `/` and `/editor` become **static shell documents** — no server-side
  substitution beyond what the frontend router needs.
- `/download` **stays a server route**, unchanged. It serves bytes from the
  session directory, with the existing containment checks. Downloads are not
  an API concern and must not be routed through the frontend.

### 3.2 Response shape

Every JSON response carries a discriminated shape rather than a bare object,
so a client can tell success from failure without inspecting the status code
alone. The existing hand-written `{"error": "..."}` convention is already
halfway there and should be regularised, not replaced.

Under A1 (FastAPI) these are Pydantic models and the OpenAPI schema is
generated. Under A0/A2 they are documented by hand in this folder and checked
by the route tests.

### 3.3 The contract is the test boundary

`tests/web_harness.py` drives handlers in-process with no socket. This keeps
working and gets *better*: today it tests an implementation detail, and after
this it tests the published contract. Route tests become the regression suite
for the seam.

---

## 4. What is deleted

| Thing | Lines | Why it goes |
|---|---:|---|
| The `fetch`/`XMLHttpRequest` prefix shim (`_PREFIX_SHIM`) | ~35 | A frontend router with a base path, or a framework mount, handles prefixes natively |
| `self.__class__ = handler` class swap | ~10 + its two `type: ignore` | A real router dispatches without mutating objects |
| Hand-written `if path == …` route chains | ~2,100 across handlers | Replaced by the router |
| Hand-rolled session plumbing | part of 534 | Only if a framework is adopted; the *policy* (per-session directory, TTL, containment) is domain and stays |

The two `# type: ignore` comments in the tree are both on the class swap. When
the swap goes, `src/pacct` has none — worth recording, since the project conventions name
them explicitly.

---

## 5. What must survive

Each of these exists because of a bug that is documented in
`docs/ENGINEERING-NOTES.md`. Any redesign that loses one is a regression, and
these are the acceptance criteria.

1. **Infrastructure routes never create a session.** `/library`, `/progress`,
   `/theme.css` and `/static/…` read a session and never mint one. When they
   minted, every cookie-less request added a phantom session, uploads landed
   in new empty projects, and the file list appeared to keep resetting.

2. **Every response that can be the first one must emit `Set-Cookie`.**
   Including routes answered by the dispatcher itself.

3. **A duplicate upload must never delete the file**, because it is the same
   file, content-addressed. `_discard(entry, keep=existing)`.

4. **`/download` serves only from inside the session's own directory.**

5. **The `MAX_JSON_BODY` ceiling** (4 MiB) or its equivalent. Without a limit,
   reading `Content-Length` allocates whatever the client claims.

6. **The sid alphabet check** (`_SID_RE`) — it is what stops a forged cookie
   escaping the sessions directory.

7. **Progress reporting works during an in-flight POST**, because the server
   is threaded. Any move to a single-threaded async server must preserve this.

8. **The GLV's session is deliberately single and shared**, because it talks
   to one physical relay. It must not be made per-visitor by a framework's
   default session handling.

---

## 6. Themes

Today `web/themes/` (1,586 lines) emits **different markup** per theme — a
numbered table in Folha, wire-coloured terminal blocks in Régua, clipped cards
in Caderno — resolved server-side by `_resolve_markup()` against the
`<!--NAV:-->` and `<!--HOME-->` markers.

**Under this specification themes become CSS-only**, and the three markup
dialects collapse into one structure with three token sets. `/theme.css` and
the `data-theme` attribute survive; `_NAV_RE`, `_HOME_RE` and `nav_html()` /
`home_html()` do not.

This is the largest behavioural change in the specification and the one most
likely to be contested — it is listed as an open question in
`04-open-questions.md` rather than settled here. If the three markup dialects
are judged worth keeping, they become three component variants instead, which
is more work but not incoherent.

---

## 7. Frontend organisation

```
web/frontend/            source, one directory per tool
  lib/                   shared: api client, progress, library picker, theme
  glv/  dnp-map/  vb-updater/  …
web/static/              built output, shipped in the bundle
```

Under B0/B1 `web/frontend/` **is** `web/static/js/` and there is no build
step. Under B3 the source directory is separate and the built output is what
ships. In both cases the shared runtimes that `inject_head` injects today —
the progress client, the library picker client, the theme handling — become
importable modules in `lib/` rather than strings concatenated into `<head>`.

---

## 8. Recognising completion

- [ ] No `<script>` block longer than 20 lines remains in any `.html` file
- [ ] No HTML string constant remains in any `.py` file
- [ ] `web/<tool>/model.py` and siblings import nothing from `web/api/` or
      `web/mount.py`, enforced by a test
- [ ] The eight acceptance criteria in §5 each have a test
- [ ] `mypy` is clean with no `type: ignore` in `src/pacct`
- [ ] The offline bundle builds, installs with `--no-index`, and boots with no
      network — verified with `PIP_NO_CACHE_DIR=1`, as `build_dist.py`'s
      vendoring already is
- [ ] All three themes render every screen correctly (still a manual gate
      unless a frontend test story arrives with B3)
