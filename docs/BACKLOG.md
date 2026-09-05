# PAC CT — what is left

Everything the migration and the public release needed is done. This is the
list of what was deliberately **not** done, why, and what doing it would
involve. Written 2026-09-05, against `pac-ct` 1.5.1, `selfiles` 1.1.1 and
`cfbwrite` 1.0.1 — all three released, all three `main` clean and in sync.

It supersedes the open findings in `REVIEW.md`, which is a historical record
of a review taken before the migration and is stale in places: S2's premise is
wrong (see below), S8's mypy backlog is gone, D1–D5 and E1 are fixed. Read
`REVIEW.md` for the reasoning behind a finding, this file for its status.

Every number here was measured on 2026-09-05, not carried over from the review.

**Nothing in this list is load-bearing.** The tree is in a finished state.

Item 1 has since been done, and doing it corrected two things this file asserted about it — see the item.

---

## Status of the REVIEW.md findings

| | | |
|---|---|---|
| D1–D5 | defects | **fixed** before the migration |
| E1 | SCD parsed three times | **fixed** — `selfiles.scl.read.ScdDocument`, 1406 ms → 682 ms on a 22 MB SCD |
| S1 | renderer vs. relay profiles | **half done** — see item 3 |
| S2 | routes untestable in factory closures | **premise was wrong** — see the note at the end |
| S4 | `session.py` ↔ `mount.py` cycle | still open, untouched, and small |
| S8 | mypy backlog | **fixed** — `mypy.ini` silences no package; the whole of `src/pacct` is clean |
| G1 | sample files carry a substation's identity | resolved by the migration (the public repo ships neither) |

---

## 1. `sanitize_name` strips the accents off a display name — **done**

**Repo:** `pac-ct`, and only `pac-ct` — every file type keeps its accents
now, the RDB included, without a `selfiles` release.

A file's name went through `selfiles`' `rdb.sanitize_name` on the way into the
project library. Its allowlist is `[^A-Za-z0-9._\- ]` — ASCII — so
`subestação.scd` was stored and shown as `subesta__o.scd`, while
`/files/download` built its `Content-Disposition` with RFC 5987
`filename*=UTF-8''` *because these names carry accents*, with none left to
carry.

The three display call sites now use `project_files.library.display_name_for()`,
which keeps the accents and drops only what is not part of a name: a directory
(backslash included — a Windows browser used to prepend one), the control
characters, and a name that is empty or `.` once those are gone.

**Two things this item said were not true, and one of them mattered:**

- *"Nothing keys off `display_name`."* The VB Updater sets
  `st.scd_name = chosen.display_name` and derives three output **filenames**
  from it (`_comments_updated`, `_descriptions.xlsx`,
  `_descriptions_imported`). All three apply `.name`, so there was no
  traversal — but `vb_updater:1032` and `gle_exporter:748` then served those
  files with a plain `filename="{target.name}"`, and
  `http.server.send_header` encodes **latin-1, strict**. Accents survive
  latin-1; an en-dash does not, and raised `UnicodeEncodeError` after the
  status line had gone out. Both are RFC 5987 now, which is what made the rest
  of the change safe. `dnp_map`, the GLV and Arquivos do Projeto already were.
- *"`rdb.py`'s extraction directory"* builds a path with `sanitize_name`. It
  does not — the extraction directory is sha-derived. `sanitize_name`'s only
  remaining path caller is `dnp_map/export.py:130`, where it is right.

**The RDB's display name went with it, and it needed no `selfiles` release.**
`rdb._safe_rdb_name` still sanitizes — that string is the cache's own record
in `meta.json`'s `first_name`, where staying ASCII is its own business, and it
is not a path either way. What changed is that pac-ct now overwrites
`RdbInfo.display_name` at the two places it builds an RDB entry
(`handler.py:_build_rdb_entry`, `derived.py:_rdb_entry`), which is legitimate:
`RdbInfo` is a plain dataclass, it is rebuilt by every `process_upload` call
rather than cached, and the field is documented as "the name THIS upload
carried". Setting it there rather than only on the `FileEntry` is what reaches
the five screens that read the name off the `RdbInfo` — `glv/handler.py:184`,
`settings_compare:131`, `vb_updater:1113`, `gle_exporter:844` and
`dnp_map/handler.py:649`, the last two of which build an output filename with
it.

## 2. `NEG` is declared by nothing and drawn by nothing

**Repo:** `selfiles`. **Size:** small, but blocked on a question.

Measured: the 418-file reference corpus contains **2** `<element type="NEG">`.
**0 of the 7 relay profiles declare it. The renderer has 0 branches for it.**
So `render_element` falls through and draws nothing.

`tests/test_gle_registry_agreement.py` catches exactly this class — a block a
profile declares that renders empty — but it cannot see this one, because the
test compares the profiles against the renderer and `NEG` is absent from
both.

**Blocked on:** what `NEG` actually is. Presumably arithmetic negation, a
one-input block, in which case it is one line in `GATE_GLYPHS` and one block
in the 451 profile. But that is a guess about what a protection diagram means,
and this project does not guess about those.

**What it needs first:** open one of the two GLEs that contain it in
AcSELerator QuickSet and look at what QuickSet draws.

---

## 3. S1's second half — the renderer still holds geometry twice

**Repo:** `selfiles`. **Size:** medium. **Risk:** low, guarded.

`data/relay_models/*.json` declares each block's `geometry`; `gle.py` holds
`ELEMENT_MIN_SIZE`, `PORT_FIRST_OFFSET` and `DEFAULT_PORTS`. Both describe the
same fact and `compute_size()` reads only the second.

**The drift is already guarded.** `test_the_profiles_and_the_renderer_agree_or_say_why_not`
fails on any disagreement not written into `KNOWN_DISAGREEMENTS` with a
reason. That guard is what found the counter defect (every one of the corpus's
1486 COUNTER elements declares two outputs; the renderer and two profiles said
one). So this is no longer a correctness risk — it is two places holding one
fact.

**Why it stopped there:** `relay_model` appears **4 times** in the whole of
`gle.py`. It reaches `render_page` and is used only for `analog_group_for`.
Making the profile authoritative means threading it through `render_page` →
each `render_*` → `render_gate` → `compute_size`: a signature change across
about fifteen functions, for tidiness rather than a fixed bug.

One disagreement is recorded and still live: the SEL-451 profile draws `ADD`
and `DIV` at MULT's 30×18 where the renderer gives them 36×24. **A GLE records
an element's position and never its size**, so no measurement settles it and
the corpus cannot be asked. Left as drawn rather than changed on a preference.

**Do it if you are already in that file.** Item 5 belongs with it.

---

## 4. S3 — three tools never got the package split

**Repo:** `pac-ct`. **Size:** large. **Payoff:** structure only.

Six web tools. Three are packages with the concerns separated; three are one
file each mixing GLE parsing, SCD parsing, XLSX build/parse, RDB writing,
session state, HTML rendering and routes:

| single file | lines | | package | modules |
|---|---|---|---|---|
| `web/vb_updater/__init__.py` | 1454 | | `web/glv/` | 10 |
| `web/gle_exporter/__init__.py` | 951 | | `web/project_files/` | 5 |
| `web/settings_compare/__init__.py` | 752 | | `web/dnp_map/` | 4 |

**This became safe in 1.5.0.** Those three now have 22, 17 and 17 route tests
(`tests/test_web_routes_*.py`), so a split that breaks something fails
immediately. Before that there was nothing underneath it.

**Recommendation: do it opportunistically, not as a project.** The next time
one of those files needs a real change, split it then, with the tests already
in place. It buys no user-visible thing on its own.

---

## 5. E2 — `element_info()` is recomputed per element

**Repo:** `selfiles`. **Size:** small. **Value:** almost none.

Measured on a real 36-element page: `element_info` is called **180 times, 5.0×
per element** (`REVIEW.md` estimated ~4×). And the cost:

```
rendering both pages of a real GLE   1.6 ms
element_info, 380 calls              2 ms cumulative, of 6 ms in render_page
```

Real waste, and it does not matter: server-side, nothing waits on it, and
removing four fifths of the calls saves about a millisecond per diagram.

**Do not do this on its own.** It is a footnote for whoever does item 3, which
touches the same call sites.

---

## 6. S5 — three registries, three hand-rolled loaders

**Repo:** `selfiles`. **Recommendation: do not do this.**

`relay_models`, `wordbits` and `mms_tables` each glob a directory, parse JSON
per model, normalise a RELAYTYPE into lookup keys, memoise and expose
`lookup()`/`invalidate()` — implemented three different ways, ~120 duplicated
lines.

The bug this would have prevented is **D2**, where one malformed file took the
whole registry down and then answered `None` for every model it had not yet
reached. That is fixed. What is left is duplication that is not hurting
anyone, and consolidating three loaders that three subsystems depend on is
real risk for tidiness.

---

## 7. The flaky test

**Repo:** `pac-ct`. **Deliberately not loosened.**

`tests/test_mms_transport.py::test_period_zero_never_puts_two_reads_on_the_link_at_once`
uses real sleeps against a fake relay with a 20 ms RTT. It fails
intermittently on a loaded machine — observed once on 2026-09-05 while a
bundle build was running, passing on both immediate re-runs.

It pins a property that matters: at most one read in flight per link, at any
period, including 0. Loosening it would remove the reason it exists.

The honest options are to leave it, or to replace the real sleeps with a
virtual clock — which is genuine work and changes what the test proves.

---

## 8. Gate 7 — the bench relay

**Not a task. A hardware limit.**

Outstanding since the migration: GLV telnet polling on 4xx/7xx/3xx, an MMS
connect and read, and the D3 monotonic-clock fix under a real Fast Message
deadline.

Two things have been added to it since:

- `web/glv/transport/telnet.py:poll()` was restructured so the Relay Word map
  is named in the guard instead of being handed to `poll_loop*` as a possible
  `None`. Annotation-and-guard only, no behaviour change intended — but it is
  on the polling path.
- `cli/runner.py`'s serial branch had a real defect fixed: it read the TCP
  branch's `port` variable. The CLI serial path has never been exercised
  without hardware.

---

## A correction to REVIEW.md S2

S2 says the web routes cannot be tested because each tool declares its handler
class inside `build_*_handler`. **That is not true and was not true when it was
written.** The factory *returns* the type, so a test calls the factory and gets
the class; `tests/test_glv_handler_scan_mode.py` had already been doing it.

What was actually missing was a place to build a request, and coverage
everywhere but the GLV — 45 of 57 routes had nothing driving them.
`tests/web_harness.py` is that place, and the six tools that had no route
tests now have 104.

So S2 is a readability question, not a testability one. That matters for
anyone reading `REVIEW.md` and concluding a large refactor is needed before
the web layer can be tested. It is not.
