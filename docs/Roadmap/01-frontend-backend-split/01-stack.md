# Idea 1 — Stack

Two independent choices: the backend framework, and the frontend toolchain.
They can be made in either order, or one and not the other.

Everything here is judged against the constraints in
[`../00-baseline.md` §4](../00-baseline.md): no internet on site, one port, a
failed dependency resolution takes the whole application down, and the
application writes bytes into protection relays.

---

## Part A — Backend framework

### A0. Keep `http.server` (the null option)

**Cost:** zero. **Benefit:** zero. **Risk:** the 3,050-line private framework
keeps growing, and it is the part with a single maintainer.

This stays on the table honestly. It is the right answer if the frontend
extraction (Part B) turns out to deliver most of the clarity that was wanted,
which is plausible — see `03-phases.md`, Phase 0.

### A1. FastAPI + uvicorn — **recommended, if a framework is adopted**

New wheels: `fastapi`, `starlette`, `pydantic`, `pydantic-core`, `anyio`,
`sniffio`, `typing-extensions`, `uvicorn`, `h11`, `click`.

| For | Against |
|---|---|
| **OpenAPI schema for free** — a machine-readable API contract, which is exactly what both Idea 1 and Idea 2 need, and which can generate a typed TypeScript client | `pydantic-core` is compiled Rust — platform wheels matter for `build_dist.py --windows`, which is the one place this could bite |
| Type-first, and `mypy` is already clean across all of `src/pacct` — the discipline is already there | Async model; the GLV's poll threads and relay locks would need care, or `def` endpoints run in a threadpool |
| Pydantic models make the request/response shape explicit, replacing hand-written `_read_json_body` validation | Larger dependency footprint than Flask |
| Dependency injection replaces the handler-factory closures | Team familiarity unknown |

The OpenAPI point is the decisive one. A declared contract is a prerequisite
for Idea 2 anyway; FastAPI produces it as a side effect rather than as a
document someone has to keep in step.

### A2. Flask + gunicorn/waitress

New wheels: `flask`, `werkzeug`, `jinja2`, `markupsafe`, `itsdangerous`,
`click`, `blinker` — seven, all pure Python, all small.

| For | Against |
|---|---|
| **No compiled dependency** — the safest possible change to the offline bundle | No OpenAPI without an extension |
| Synchronous, which matches the existing threaded design exactly; the GLV needs no rethinking | Sessions and blueprints are conventional, but the contract stays undeclared |
| Jinja2 arrives with it, if any server-side rendering is kept | |
| Blueprints map almost one-to-one onto the current `Mount` concept | |

**A2 is the lower-risk option and A1 is the higher-value one.** If the
offline-bundle risk of a compiled wheel is judged unacceptable, A2 is not a
consolation prize — Flask blueprints are a good fit for the mount model.

### On the dependency-footprint objection

The instinct that "every dependency is a risk here" is correct and well
earned — the `py61850` pre-release incident is recorded in the project
conventions for a reason. But it is weaker than it looks here: the bundle already
vendors six packages including `openpyxl` and `telnetlib3`. Adding five to ten
pure-Python wheels is not a categorical change to a supply chain that already
exists and already works offline. The one genuine new risk is `pydantic-core`'s
platform wheel under `--windows`, and `build_dist.py` already has
`windows_only_requirements()` for exactly that class of problem.

---

## Part B — Frontend

### B0. Extract JavaScript to static files — **do this first, regardless**

Move the 6,829 lines of inline `<script>` out of the templates into
`web/static/js/<tool>/*.js`, served by the existing `/static` route that
`mount.py` already handles.

- **New dependencies: none.** **New build step: none.**
- Immediately buys: real files, real diffs, lintable and testable JavaScript,
  browser caching, and a genuine frontend/backend seam.
- Fully reversible.
- **A prerequisite for every other option in Part B, and for Idea 2.**

This is the single highest value-to-cost item in this folder. It does not
require the framework question to be answered first, and it does not commit to
any answer.

### B1. Stay vanilla, with modules

After B0, adopt ES modules and a small amount of shared code
(`static/js/lib/`). Optionally add `eslint`.

| For | Against |
|---|---|
| Still no build step; no node on the build machine | 3,160 lines of GLV JavaScript stay hand-written |
| Zero risk to the offline bundle | No component model, no type checking |
| Fits the project's stated dislike of unnecessary machinery | |

### B2. A no-build framework (Preact + htm, or Alpine)

A single vendored ESM file, no bundler, no node.

| For | Against |
|---|---|
| Component model without a build step | Preact + htm is a smaller ecosystem than the React it resembles |
| One vendored file to ship in `static/` — same shape as the `.woff2` fonts | Still no TypeScript |
| Reversible per screen | |

### B3. Vite + TypeScript + a component framework

The conventional 2026 answer. Build to static files, served by Python.

| For | Against |
|---|---|
| The largest payoff on the GLV specifically — 3,160 lines of imperative DOM code is where a component model earns its keep | **Introduces a second supply chain into an offline-first product**: node and npm at build time, and built assets that must be vendored into the zip exactly as the wheels are |
| Type safety across the seam, generated from OpenAPI if A1 is taken | `build_dist.py` and CI both grow a node step |
| A conventional stack a new contributor recognises | The "it passed on the build machine because the cache was warm" failure mode reappears in a new form — see `pin_direct_references()` for the precedent |
| Real frontend testing becomes possible | Node is a build-machine dependency, never a substation one — but that distinction has to be enforced |

---

## Recommendation

**Take B0 now.** It needs no decision from anyone, adds nothing, breaks
nothing, and is a prerequisite for everything else including Idea 2.

**Then re-ask the question.** B0 may deliver most of the clarity that motivated
the request, at which point A0 + B1 is a defensible end state and the roadmap
stops there — which would be a good outcome, not a failure.

**If it does not, take A1 + B3 together**, in that order, and treat the GLV as
the last thing migrated rather than the first. The two are worth taking
together because B3's main payoff (typed client generated from the contract)
depends on A1's main payoff (a contract that generates itself).

**A2 + B2 is the coherent conservative pairing** if the offline supply chain is
judged the dominant risk.

The pairing to avoid is a backend framework with no frontend work — that
rewrites 3,600 lines of plumbing and leaves all 6,829 lines of the actual
problem exactly where they are.
