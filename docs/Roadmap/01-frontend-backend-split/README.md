# Idea 1 — Split the frontend from the backend

**Status: discussed, not decided. Phase 0 is ready and needs no decision.**

---

## What was actually asked

> "I want to split the backend (python) from the front end."

with the reasoning:

> "to have a clear stack of the app, what I have now seems not the usual
> python web development."

That reading is correct, and the diagnosis is worth stating precisely, because
it is not the same thing as the request.

---

## The diagnosis

Python is a mainstream web backend language — Django, Flask and FastAPI are
among the most used web frameworks anywhere. Python is a perfectly ordinary
choice for this application, and nothing here suggests changing language.

But **Python never runs in the browser.** It runs on the server, produces JSON
or HTML, and sends it. The browser half is HTML, CSS and JavaScript regardless
of what the backend is written in. So a frontend/backend division is not
something a Python application *adds* — it is already there, physically. The
only question is whether it is **visible in the codebase** or smeared through
it.

In this tree it is smeared. That is what "the stack isn't clear" is detecting.

### What is genuinely unusual here

Six things, none of which appear in a conventional Flask, Django or FastAPI
project:

1. **`http.server.BaseHTTPRequestHandler` as the production server.** This is
   the standard library's teaching and debugging server; Python's own
   documentation does not recommend it for production. Conventional Python web
   applications run on WSGI or ASGI behind gunicorn or uvicorn. This is the
   largest single departure.

2. **Hand-written routing** — `if path == "/state": ... elif path ==
   "/download": ...` repeated per tool, rather than a router or decorators.

3. **`self.__class__ = handler`** in `mount.py`: dispatch by mutating an
   object's class at runtime. It works and it is well commented, but it does
   not appear in mainstream Python web code.

4. **No template engine.** No Jinja2. HTML is static `.html` files plus raw
   string constants such as `HOME_HTML = r"""<!doctype html>..."""` in
   `dashboard.py`. Server-side variation is done by **regex-substituting
   `<script>` and markup into `<head>`** (`inject_head`, `_NAV_RE`, `_HOME_RE`).

5. **Hand-rolled sessions** — cookie parsing, TTL sweeper thread, per-session
   directories: 534 lines. Every framework ships this.

6. **A `fetch`/`XMLHttpRequest` monkey-patch** injected into every page to
   re-prefix URLs, which exists purely to compensate for the mount mechanism.

### What that adds up to

**A private web framework of 3,050 lines** (`mount` + `session` + `progress` +
`theme` + `themes/`), and **6,829 lines of JavaScript with no home** — no
build, no module system, no lint, no tests, living inside HTML files inside a
Python package.

The second number is the real cost, and it is the one the request is pointing
at.

---

## Three decisions, not one

The request bundles three independent changes. Separating them is most of the
value of this document.

| | Change | Scope | Recommended |
|---|---|---|---|
| **A** | Backend framework: `http.server` → FastAPI or Flask | backend only | see `01-stack.md` — genuine, but not first |
| **B** | Frontend seam: server-rendered HTML with inline JS → JSON API + a real frontend codebase | both sides | **yes** |
| **C** | Deployment split: one process → two servers | operations | **no** |

**C is the one to reject explicitly.** Two listening processes would cost CORS
configuration, cookie `SameSite` handling, and a second port — breaking
`tools/windows/wsl-portproxy-setup.ps1` and the firewall guidance — in exchange
for nothing on an engineer's laptop in a substation. The built frontend should
be served as static files by the same Python process on 8765.

So: **"split the frontend from the backend" here means A + B, and specifically
not C.** The frontend and backend become separate *codebases with a declared
contract*, not separate *deployments*.

---

## What this is not

- **Not a rewrite of the domain logic.** The 4,389 lines of domain code inside
  `web/` do not move, and the real work — RDB, SCL, MMS, Compound File — is
  already in `sellib`, `py61850` and `cfbwrite` and is untouched.
- **Not a change of backend language.** Python stays.
- **Not a move to two servers.** See C above.
- **Not a prerequisite for Idea 2** — except Phase 0, which is.

---

## Why it is worth doing anyway

The honest counter-argument is that the current design **works**, has 904
tests, and serves nine tools in production. "Not conventional" is not "wrong".

The question that matters is not *"is this normal?"* but *"what is the
non-conventionality costing?"* — and that has a concrete answer:

- 6,829 lines of JavaScript that cannot be linted, tested, or imported as
  modules, of which 3,160 are one file's inline `<script>` (`glv/templates/
  dashboard.html`, 3,619 lines total).
- 3,050 lines of framework that only one person can maintain, and which no
  new contributor can learn from documentation that exists.
- A visual regression gate that is "run `app.py --web` and look at it in all
  three themes."

Phase 0 addresses the first of those on its own, with no framework decision,
no new dependency, and full reversibility. That is why it is separated out.

---

## Read next

- [`01-stack.md`](01-stack.md) — the technology options and the recommendation
- [`02-specification.md`](02-specification.md) — the target end state
- [`03-phases.md`](03-phases.md) — the ordered route
- [`04-open-questions.md`](04-open-questions.md) — what is genuinely undecided
