# The HTTP contract

Every route this application answers, what it takes, what it answers with, and
which of its oddities are deliberate. Written down in B12 because until then the
contract existed only as the union of nine `if path ==` chains and nobody had
said what it was.

**This describes what is true today.** Where a route is odd, the oddity is
recorded rather than repaired — B12's own rule, and the reason the document can
be trusted: a contract that quietly documented the intended behaviour instead of
the actual one would be worse than none. Three things found while writing it are
recorded as findings with nothing done about them, and they are collected in
[Findings](#findings) at the end.

## How this document is kept honest

Prose drifts from code. So the route list is duplicated, once, into a place a
test can read:

| | |
|---|---|
| `docs/HTTP-CONTRACT.md` | this file — the reasoning, the parameters, the oddities |
| `tests/declared_routes.py` | the same 88 routes as data |
| `tests/test_http_contract.py` | asserts the tree and the declaration agree **in both directions** |

A route added to a handler and not declared fails the suite. A route deleted
from a handler and left in the declaration fails it too — the direction that
matters in a year, because deleting a route is easy to forget to write down.

The declaration is **test data and ships with nothing**. Nothing under `src/`
imports it and the dispatcher still routes from each tool's own chain. A runtime
route table with no runtime consumer would be a second source of truth, which is
the defect this contract exists to remove rather than to add. It is the same
shape `tests/test_version.py` already uses to stop `requirements.txt` and
`pyproject.toml` disagreeing about a pin.

What the test **cannot** see, and what therefore lives only here: parameters,
response bodies and status codes. AST extraction reads `(mount, method, path)`
and no more, and a declaration nobody verifies is worse than no declaration.

## The shape of the API

- **Two verbs.** `GET` and `POST`. There is no `do_PUT` or `do_DELETE` anywhere
  in the tree, and 83 routes are spelled with the two. `POST /remove` deletes a
  file; `POST /diagrams/close` closes a tab.
- **JSON in, JSON out**, with the exceptions named below: the screens are HTML,
  the downloads are bytes, one route is Server-Sent Events, and every
  unknown-route 404 is `text/plain`.
- **A JSON request body is never trusted to be a dict.** `_read_json_body()`
  turns an empty body, invalid JSON, or JSON that is not an object into `{}`, so
  a handler never takes an `AttributeError` off a `.get()`. A body larger than
  the ceiling becomes `{}` **and is not read**, because the `Content-Length`
  comes from the client and without a ceiling the client chooses how much memory
  the server allocates.
- **HTTP/1.0.** `protocol_version` is left at the `BaseHTTPRequestHandler`
  default, so every response closes its connection and there is no keep-alive.
  That is load-bearing rather than incidental: several `do_POST` paths answer
  without reading the request body, which is harmless only because the
  connection closes. Turning on HTTP/1.1 means draining or refusing an unread
  body on every route first.

### Success and failure

**The declared rule, as of B12:**

> The **HTTP status code is the result.** A 2xx is success and a 4xx/5xx is
> failure. No response body carries a top-level `ok` that restates it. A failure
> answers `{"error": "<mensagem>"}`.

The reason is not tidiness. A body field that restates the status is a second
source of truth that can disagree with the first, and then a client's behaviour
depends on which one it happened to read. Removing the duplicate removes the
failure mode. RFC 9110 already makes the status the result of the request.

Two things the rule does **not** touch:

- **A per-item `ok` inside a batch result stays.** `vb_updater`'s `/apply-batch`
  answering `422` with `{"results": [{"ok": false, …}], …}` is not duplicating
  the status: a 422 for the batch says nothing about *which* entry failed, and
  the screen has to say which.
- **The model layer keeps returning `{"ok": …}`.** Those are Python function
  results, not HTTP bodies. A handler reading that `ok` to *choose* the status
  is exactly right; what stops is forwarding it onto the wire.

Full RFC 9457 `application/problem+json` was considered and **not adopted**. Its
payoff is a stable `type` URI a third-party client can branch on; this API has
one client, written in the same repository, and its error strings are Portuguese
sentences shown to a commissioning engineer. What is worth taking from it is an
optional `"code"` beside `"error"`, and only for the errors the JavaScript
actually branches on — not a taxonomy invented across every site.

### The tree does not conform yet, and here is exactly how much

Measured over the 154 `_send_json` call sites:

| Shape | Sites | Mounts |
|---|---:|---|
| `{"ok": false, "error": …}` | 42 | `dnp_map`, `files`, `gle_tabs` |
| `{"error": …}`, no `ok` | 53 | `gle_exporter`, `glv`, `settings_compare`, `vb_updater`, `vlan_mapper` |
| delegated model payload | 28 | crosses both |
| `2xx` with `{"ok": true, …}` | 25 | six mounts |
| `2xx` with no `ok` | 6 | `glv`, `settings_compare` |

The split is clean **between** mounts and never inside one. Three points worth
keeping:

1. **Status and payload never disagree today.** No 2xx carries `ok: false` and
   no 4xx/5xx carries `ok: true`, at any of the 154 sites. Nothing enforces
   that; it has held by discipline.
2. **The shared client wrapper already discriminates on the status and ignores
   the payload's `ok`.** `SelProgress.post` returns `{ok: r.ok, status:
   r.status, data: d}` where `r.ok` is the native `Response.ok`, and it reads
   `data.error` for the message. Both families of mount go through it.
3. **The discriminated shape already exists one layer down.** `vb_updater` and
   `gle_exporter`'s model functions return `{"ok": bool, "error": …}` and their
   handlers read that `ok` to pick the status code. `settings_compare` does the
   same off the presence of an `error` key. The payload is the source of truth
   and the status is derived — the inverse of how it looks from outside.

**Conforming the tree is B12b**, a phase of its own: it touches 53 server sites
and about 25 client sites (`dnp_map` and `files` read `r.ok` off the parsed
body), and it is the only client-visible change in this area. It was split off
deliberately so that a description and a behaviour change never ride in
together.

## The dispatcher

One `ThreadingHTTPServer` on port 8765. Every tool is a `Mount(prefix, handler,
label)` and none opens a socket of its own.

A request arrives, and in this order:

1. The theme is resolved from the `seltheme` cookie.
2. **The five infrastructure routes are answered**, before any prefix is matched
   and before any session exists.
3. The session is resolved from the `selsid` cookie, minting one if absent.
4. The mount prefix is stripped off `self.path` and `self.__class__` is swapped
   for the tool's handler, so the tool's routes are written as though it owned
   the root.
5. A path matching no prefix falls through to the root mount (the home).

The prefix match is `path == prefix` or `path.startswith(prefix + "/")`, so
`/toolbox` is not routed to the tool mounted at `/tool`.

### The infrastructure routes never create a session

**This is a rule and not an accident**, and it is the one property in this
document with a test asserting the *structure* rather than the behaviour
(`test_the_infrastructure_routes_are_answered_before_a_session_exists`): a
socket test would prove that today's code does not mint, while the structural
one proves it cannot without somebody moving the branch.

A stylesheet is not a visitor. When these routes did mint, every cookie-less one
added a phantom session and handed out a fresh `selsid`, so concurrent requests
traded the browser's identity between them and the project's file list appeared
to erase itself.

| Method | Path | Notes |
|---|---|---|
| GET | `/progress` | `?job=` — job progress for the shared bar. Answers in parallel with the POST it is reporting on, because the server is threaded. |
| GET | `/theme.css` | The active theme's generated stylesheet. `no-store`. |
| POST | `/theme` | `{theme}` → `204` plus a one-year `Set-Cookie`. `400` for a body over 4 KiB, refused **unread**. |
| GET | `/static/…` | Fonts, their licences, and the pages' JavaScript. Sandboxed to `STATIC_DIR`; `.woff2` is `immutable`, everything else `no-store`. |
| GET | `/library` | `?kind=` — the visitor's project files. **Peeks** the session and never creates one, so an empty answer means no session, which is the truth. |

All five answer **at every prefix as well as at the root**: `/glv/theme.css` and
`/theme.css` are the same route, because the VB Updater's page asks for one and
the home asks for the other.

> **`POST /theme` is the fifth, and the roadmap counted four.** It sits with the
> others above the session line and mints nothing. Recorded here because a rule
> stated with the wrong membership is a rule that gets broken by accident.

### The redirect, and the cookie it nearly forgot

`GET /<prefix>` (with no trailing slash) answers `301` to `/<prefix>/`, so
relative paths in the page resolve inside the tool.

That response is written by the dispatcher itself, **after** the session was
created and **without** swapping `self.__class__` — so it used to fall through
to `BaseHTTPRequestHandler.end_headers`, which knows nothing about cookies, and
the visitor reached the tool still without an identity. Every upload made before
that point was stranded. `Dispatcher.end_headers` mirrors `SessionHandler`'s
now, and the two never run on the same response.


## The routes

### `dashboard` — the home

Mounted at `/` (the root, and the fallback for any unmatched path) — 3 routes.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The menu. Resolves <!--VERSION--> here; the nav and menu body are resolved by the dispatcher, which knows the theme. |
| GET | `/home-state` | Liveness sentinel. The only one written as a JSON string literal rather than through _send_json. |
| GET | `/update-check` | Cached GitHub release check, one question per process rather than one per tab. |

### `files` — Arquivos do Projeto

Mounted at `/files` — 4 routes.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The project files screen. |
| GET | `/download` | ?sha256= -- takes a CONTENT HASH, not a path, and therefore serves the shared RDB cache. Not the tools' /download. |
| POST | `/remove` | {sha256} -- drops one file from this project. |
| POST | `/upload` | Raw body, name in X-Filename. The only route that reads a non-JSON request body by design. |

### `glv` — Visualizador de Lógica

Mounted at `/glv` — 26 routes.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The dashboard shell. 302s to /novo when the visitor has no open tabs. |
| GET | `/debug/analogs` | ?d= -- the only route with a path SEGMENT, and the only one named debug. |
| GET | `/diagrams` | The open tabs. Same payload the shell boots with. |
| GET | `/events` | ?d=&page= -- Server-Sent Events. Held open, no Content-Length, ends when the connection does. |
| GET | `/group-state` | ?d= -- the diagram's note groups. |
| GET | `/highlights` | ?d= -- the diagram's highlights. |
| GET | `/landing-state` | What the picker shows: the project's RDBs and the current selection. |
| GET | `/meta` | ?d= -- one diagram's metadata. |
| GET | `/note` | ?d= -- the diagram's notes. |
| GET | `/novo` | The picker. A landing page in its own right, not a redirect target only. |
| GET | `/pages/` | Prefix route. ?d=&have= -- the page's SVG, or 204 with no body when the client says it already has it. |
| GET | `/unreachable` | ?d= -- logic the relay cannot reach, as JSON. |
| GET | `/unreachable.txt` | ?d= -- the same, as a text report. The only route whose path carries an extension. |
| GET | `/values` | ?d=&page= -- live values. Emitted through _send, not _send_json, so it is JSON that the JSON scan does not see. |
| GET | `/vb-source` | ?d= -- where each VB in the diagram comes from in the SCD. |
| POST | `/connect` | ?d= -- connect to the relay. 202 with a job handle; the only 202 in the application. |
| POST | `/diagrams` | Open one diagram as a tab. Same path as the GET, different method, different meaning. |
| POST | `/diagrams/activate` | ?d= -- make a tab active. Returns {active} and nothing else. |
| POST | `/diagrams/batch` | Open several at once. |
| POST | `/diagrams/close` | ?d= -- close a tab and pick the next active one. |
| POST | `/disconnect` | ?d= -- drop the relay connection. |
| POST | `/group-state` | ?d= -- write the note groups. |
| POST | `/highlights` | ?d= -- write the highlights. |
| POST | `/note` | ?d= -- write a note. Refuses a body over NOTE_MAX_BYTES. |
| POST | `/period` | ?d= {interval_ms} -- polling cadence. |
| POST | `/select-rdb` | {sha256} -- point the tool at an RDB already in the project. |

### `vb_updater` — VB Updater

Mounted at `/vb-updater` — 11 routes.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The landing page. |
| GET | `/compare` | ?relay=&ied=&gle= -- a rendered comparison page. HTML, with text/plain errors. |
| GET | `/download` | ?file= -- takes a PATH and is sandboxed to this session's out/ and scd/. |
| GET | `/state` | The tool's session state. |
| GET | `/vb-state` | Liveness sentinel. |
| POST | `/apply` | Apply one direction's update. Status is chosen from the model payload's own ok. |
| POST | `/apply-batch` | Apply several. 422 carries per-item results so the screen can say which one failed. |
| POST | `/export-descriptions` | Write the descriptions out as a spreadsheet. |
| POST | `/import-descriptions` | Read edited descriptions back in. |
| POST | `/select-rdb` | {sha256} -- choose the RDB from the project. |
| POST | `/select-scd` | {sha256} -- choose the SCD. Shares a branch with /select-rdb and differs only by the kind it demands. |

### `vlan_mapper` — VLAN Mapper

Mounted at `/vlan-mapper` — 4 routes.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The landing page. |
| GET | `/state` | The tool's session state. |
| GET | `/vlan-state` | Liveness sentinel. |
| POST | `/select-scd` | {sha256} -- read IEDs and VLANs out of one SCD. The tool writes nothing. |

### `gle_exporter` — Exportador de Comentários GLE

Mounted at `/gle-exporter` — 7 routes.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The landing page. |
| GET | `/download` | ?file= -- takes a PATH, sandboxed to this session's out/ and xlsx/. |
| GET | `/gle-state` | Liveness sentinel. |
| GET | `/state` | The tool's session state. |
| POST | `/export` | Write the variable comments out. |
| POST | `/import` | Apply edited comments back into the RDB. All-or-nothing; status chosen from the model payload's ok. |
| POST | `/select-rdb` | {sha256} -- choose the RDB from the project. |

### `settings_compare` — Comparador de Ajustes

Mounted at `/settings-compare` — 5 routes.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The landing page. |
| GET | `/settings-state` | Liveness sentinel. |
| GET | `/state` | The PROJECT's RDBs, not the ones this tool has adopted. Listing also registers them. |
| POST | `/diff` | {relays,groups} -- the comparison. Status is derived from whether the payload carries an error key. |
| POST | `/groups` | {relays:[{rdb_key,relay_name}]} -- the setting groups those relays share. |

### `dnp_map` — Editor de Mapa DNP

Mounted at `/dnp-map` — 14 routes.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The landing page. |
| GET | `/copiar` | The copy-between-relays screen. |
| GET | `/download` | ?f= -- NOT ?file=. Takes a path, sandboxed to this session's out/. Answers 403 for every failure, including a missing parameter. |
| GET | `/editor` | The map editor screen. |
| GET | `/map` | ?rdb=&relay= -- one relay's DNP map. |
| GET | `/rdbs` | Every RDB in the project, newest first. |
| GET | `/relays` | ?rdb= -- the relays inside one RDB. |
| GET | `/wordbits` | The word-bit models loaded at import. Session-independent. |
| POST | `/copy-session` | Copy the staged map between sessions of one relay. |
| POST | `/copy-to-relays` | Copy a map onto other relays. |
| POST | `/edit` | Edit one point. |
| POST | `/export` | Write the edited RDB out. |
| POST | `/import-profile` | Install a SEL DNP3 device profile as the model's name domain. |
| POST | `/swap` | Exchange two points. |

### `gle_tabs` — GLE Tabs

Mounted at `/gle-tabs` — 9 routes.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The landing page. |
| GET | `/download` | ?f= -- NOT ?file=, and a bare FILE NAME resolved against out/, not a path. 403 for every failure. |
| GET | `/editor` | One GLE per page. Reads rdb/relay/gle from its own query string. |
| GET | `/gles` | ?rdb= -- the GLEs inside one RDB. |
| GET | `/pages` | ?rdb=&relay=&gle= -- one GLE's pages. Note the plural is a LIST here and an SVG under glv. |
| GET | `/rdbs` | Every RDB in the project, newest first. |
| POST | `/gerar` | Write the staged edits into a new RDB. |
| POST | `/reset` | Drop what is staged. |
| POST | `/stage` | Validate one GLE's edit and hold it. Stages nothing on refusal. |


## The two downloads that must never be unified

| | `files` `/download?sha256=` | a tool's `/download?f=` / `?file=` |
|---|---|---|
| Takes | a **content hash** | a **path**, from the request |
| Sandbox | none needed | the session's own directories |
| Can serve | the shared RDB cache | only what this session generated |

The tool route takes the path from the request, so widening its sandbox to the
cache would let one visitor ask for another visitor's generated file. The
library route has nothing to sandbox because there is nothing for the client to
point anywhere, which is what lets it hand back a 40–140 MB RDB out of
`cache/rdb/`. Both stream in 1 MB chunks and name the file with RFC 5987
`filename*=UTF-8''`, because these names carry accents and `send_header`
encodes latin-1 strict.

**`/download` is a server route, not an API concern.** It serves bytes with
containment checks and answers `text/plain` on refusal, never `{"error": …}`.

## Oddities, recorded

Each of these is live, none is a mistake worth a phase of its own, and all of
them would otherwise be rediscovered by the next person to write a client.

**1. The download parameter has two spellings, and one of them swallows every
failure.** `gle_exporter` and `vb_updater` take `?file=` and separate the
failures into `400` (no parameter), `403` (outside the sandbox) and `404` (no
such file). `dnp_map` and `gle_tabs` take **`?f=`** and answer `403` to all
three. A client written against one pair and pointed at the other gets a `403`
that says nothing about why. Neither is a security hole — refusal is the safe
direction in every case — which is exactly why it survived: nothing ever failed
loudly.

**2. `/progress` is matched differently from the other four.** It is matched
with `path.endswith("/progress")` against the **raw** path, while `/theme.css`,
`/theme`, `/library` and `/static/` are matched against the prefix-**stripped**
tail. So `/glv/anything/progress` reaches progress and `/glv/anything/theme.css`
does not — it falls through to the tool. Nothing depends on the difference
today; it is pinned by a test so that changing it is a decision.

**3. The landing page has three spellings and no two mounts agree.** `dnp_map`
and `gle_tabs` accept `/` and `""`; `files` accepts `/`, `""` and
`/index.html`; the other six accept `/` and `/index.html`. They are one route.

**4. The unknown-route 404 is `text/plain` everywhere, in two languages.**
`gle_exporter`, `vb_updater`, `glv`, `vlan_mapper`, `settings_compare` and the
home answer `"not found"`; `dnp_map`, `gle_tabs` and `files` answer
`"Não encontrado"` with an explicit charset. A JSON client asking for a path
that does not exist gets neither JSON nor a consistent message.

**5. `POST /glv/period` answers `200` to a refusal, and is right to.** It
returns a three-valued `status` — `aplicado`, `adiado`, `recusado` — because the
outcomes are not success and failure. A telnet diagram is refused because its
cadence is several round trips per turn and nobody has a bench to measure
tightening it; the request was understood and answered, so a 4xx would be a lie.
**This is the only route in the application where the HTTP status alone does not
tell the caller what happened**, and it is a deliberate exception to the rule
above rather than an inconsistency to be regularised.

**6. Five liveness sentinels, and one of them is hand-written.** `/vb-state`,
`/gle-state`, `/settings-state`, `/vlan-state` and `/home-state` all answer
`{"ok": true}` so the menu can grey out a tool that is down. Here `ok` **is**
the resource, not an envelope, so the rule above does not touch them. Four go
through `_send_json`; the home writes the same two bytes as a string literal
through `_send`.

**7. Two JSON routes do not go through `_send_json`.** `glv`'s `/values` and
`/debug/analogs` use `_send(200, json.dumps(…), "application/json")`, and the
three notes routes write their `{"error": …}` as string literals. They are
indistinguishable to a client and invisible to any scan of the error
convention — which is why this document names the emission path and not only
the shape.

**8. `GET /glv/pages/<id>?have=1` answers `204` with no body.** The client
caches the parsed SVG, but the GET still happens, because `/pages/<id>` is the
**only** path by which the server learns which page the visitor opened — the
memory that brings a tab back to page 27 instead of page 2. That invariant must
not depend on whether the browser had the bytes.

**9. `POST /glv/connect` is the only `202`.** Connecting cannot block the
response: on a fresh FID, mapping name → (row, bit) takes minutes. It answers a
job handle and the work runs in a thread.

**10. `/glv/diagrams` is two routes.** `GET` lists the open tabs; `POST` opens
one. Same path, different method, different meaning — which is ordinary REST and
the only place this application does it.

**11. Two paths are shaped unlike the other 81.** `/glv/unreachable.txt` is the
only one carrying an extension, and `/glv/debug/analogs` the only one with a
path segment — and the only one called `debug`.

**12. `GET /files/download` is the only `410`.** "The file is no longer on
disk" is genuinely not "there is no such file", and the entry may still be in
the library.

## What is tested, and what is not

**Every one of the 83 tool routes is now executed by the suite**, measured by
tracing the handler files during a full run rather than by grepping the tests
for paths — 80 of 80 branch bodies (three branches serve two routes each). The
five infrastructure routes have 31 tests of their own.

Two harnesses, and the difference between them is itself part of the contract:

| | `tests/web_harness.py` | `tests/dispatcher_harness.py` |
|---|---|---|
| Reaches | one tool's handler | the dispatcher, end to end |
| Socket | none — captures where the socket would be | a real `ThreadingHTTPServer` on an ephemeral port |
| Headers | **a plain `dict`** | `email.message.Message`, case-insensitive |

**The tool harness's headers are a `dict`, and the real ones are
case-insensitive.** Every route that reads `Content-Length` or `Cookie` goes
through that difference, so a client sending `content-length` in lower case is
served correctly by the application and is not covered by most of this suite.
That is why the dispatcher — where those two headers actually decide
something — is tested over a socket instead.

Named as untested, deliberately:

- **`GET /glv/events`.** Server-Sent Events: it holds the connection open and
  has no `Content-Length`, so a test driving it has to decide when to stop
  listening, which is a timing judgement and the flakiest kind. Its `?d=` guard
  **is** covered; the streaming body is not.
- **The success path of three round-trips.** `dnp_map POST /export`,
  `gle_exporter POST /import` and `vb_updater POST /import-descriptions` end in
  writing a real Compound File or parsing a real `.xlsx`. The suite carries no
  genuine RDB (`fake_rdb` writes a placeholder), and `tests/test_rdb_write.py`
  is where the writer is tested against real bytes. Their **refusals** are
  covered, which is the half a client has to handle.
- **The success path of `POST /dnp-map/import-profile`.** It rewrites
  process-wide reference data that every later test would then see, and a test
  that mutates a shared registry is worse than an untested branch.
- **Anything in a browser.** Nothing in this suite runs JavaScript, so the
  `fetch` shim that re-prefixes absolute paths on the client is exercised by
  nothing. Only the server half of that arrangement is covered. A tool whose
  script throws at the top level renders blank and still answers `200`.

## Findings

Three things found while writing this, none of them acted on. B12 describes;
each of these is a phase with its own reason.

**F1 — `POST /settings-compare/groups` can answer nothing at all.**
`settings_compare/handler.py:122` builds its pairs with
`[(r["rdb_key"], r["relay_name"]) for r in refs]` — a subscript, on data from
the request body. A ref missing either key raises `KeyError` out of `do_POST`,
and nothing above it in `http.server` turns an exception into a response:
`handle_error` logs a traceback and closes the socket. The client gets
`RemoteDisconnected`, which it cannot tell from the server dying. Measured to
be the **only** one of its kind — `glv`'s `/diagrams`, `/diagrams/batch`,
`/group-state` and `/highlights` all subscript client data inside
`except (…, KeyError, …)` and answer `400`. Pinned by a characterisation test
that is meant to fail the day it is fixed.

**F2 — the `{"error": …}` convention is two conventions.** Measured above. It
is B12b's whole content and is not restated here.

**F3 — the download parameter is `?f=` in two mounts and `?file=` in two
others**, and the `?f=` pair collapses three distinct failures into one `403`.
Oddity 1 above. Unifying it is client-visible, which is why it is a finding and
not an edit.
