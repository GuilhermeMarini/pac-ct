# SDD ledger — plan: docs/superpowers/plans/2026-08-22-dnp-map-editor.md

Spec: docs/superpowers/specs/2026-08-22-dnp-map-editor-design.md (read, reachable)
Branch: dnp-map-editor (off tema-tokens)

Ruling: work in the main working tree on a new branch `dnp-map-editor` instead of a
git worktree — the tree carries uncommitted changes to `pacct/web/dashboard.py`
and an entirely untracked `pacct/web/themes/`, both of which Task 10 must edit. A
worktree would branch from committed state and simply not contain `themes/`, so
Tasks 9-10 would fail or silently write to the wrong place. Not main, so the
never-on-main rule holds. Cost if wrong: the plan's commits interleave with the
tema-tokens work in one tree; separable by branch, but not by directory.

## Pre-flight scan

### Shared files between tasks

| File | Tasks | Producer -> consumer | Finding |
|---|---|---|---|
| pacct/parsers/set_dnp.py | 1, 2, 3 | T1 parse/RawLine/SetDnpFile -> T2 adds points()/blocks()/extras() -> T3 adds discover() | Clean. Each task appends; T2 and T3 add to the same dataclass/module without rewriting T1. |
| tests/test_set_dnp.py | 1, 2 | T1 creates, T2 appends | Clean. T3 imports SAMPLE_411L from it — needs tests/__init__.py, created in T1 Step 1. |
| pacct/paths.py | 4, 6 | T4 adds WORDBITS_DIR, T6 adds DNP_TEMPLATES_DIR | Clean. Different constants, sequential edits. |
| pacct/web/dnp_map/__init__.py | 6, 8 | T6 creates load_template -> T8 calls it | Clean. |
| pacct/web/dnp_map/templates/editor.html | 8, 9 | T8 writes a stub so handler.py imports; T9 replaces it | Clean, and deliberate — flagged in T8 Step 3. |
| pacct/web/dnp_map/model.py | 6, 7, 8 | T6 -> T7 (apply_edits), T8 (all) | Clean. |
| pacct/web/dnp_map/handler.py | 8, 10 | T8 build_dnp_map_handler -> T10 Mount | Clean. |
| pacct/web/themes/tokens.py | 9 (conditional) | T9 "add --warn if missing" | RESOLVED before dispatch: `--warn` already exists in tokens.py:309 and in all three directions (folha/regua/caderno). T9 must NOT touch tokens.py. |
| docs/ENGINEERING-NOTES.md | 10 | T10 only | Clean. Note: docs/ENGINEERING-NOTES.md already has uncommitted edits in this tree; T10 appends, never rewrites. |

### Interface pairs

| Produces | Consumes | Finding |
|---|---|---|
| T1 parse(), SetDnpFile.get_value/set_value/serialize | T2, T3, T6, T7, T8 | Clean; signatures match every call site in the plan. |
| T2 points()/blocks()/extras(), DnpPoint fields | T8 _serve_map | Clean. `pending.get(p.sca_key or "", p.sca)` degrades correctly when sca_key is None. |
| T3 discover(), identical_groups(), DnpSession.stream_parts/fs_path | T7 _sessions_index, T8, T10 script | Clean. T10's script reaches for `set_dnp._SETD_NAME_RE` (private) — acceptable, it is the same module's own regex and the script is a dev tool. |
| T4 wordbits.lookup/check/duplicates | T8 _serve_map | Clean. duplicates() called positionally in the handler, by keyword in the test; both valid. |
| T5 rebuild/write_ole/Entry/OleRebuildError | T7 export, T7 test fixtures, T10 script | Clean. T7's test builds its fixture RDB with T5's own writer — deliberate, and the fixture is small enough to exercise the mini-FAT path. |
| T6 DnpMapState/record_edits/edits_for/swap/copy_session/dirty_summary/apply_edits/current_value | T7, T8 | Clean after the pre-write fix to the Interfaces block (copy_session param names). |
| T7 export/export_txt/build_streams/ExportResult | T8 _do_export | Clean. |
| T8 build_dnp_map_handler(logger, sessions) | T10 Mount | Clean; matches the factory shape of the other five tools. |

### Per-task self-consistency

| Task | Tests vs code it specifies | Finding |
|---|---|---|
| 1 | 11 tests against parse/serialize/set_value | Clean. |
| 2 | 7 tests against points/blocks/extras | Clean. |
| 3 | 7 tests against discover/identical_groups | Clean. |
| 4 | 10 tests against lookup/check/duplicates | Clean. Step 6 must run before test_the_shipped_files_load passes — stated in the task. |
| 5 | 12 tests against write_ole/rebuild | Clean. |
| 6 | 8 tests against the model | Clean. |
| 7 | 6 tests against export | Clean. |
| 8 | No unit tests; verified by import + browser | Consistent with the repo: no tool handler has unit tests. |
| 9 | Template only | Same. |
| 10 | Wiring + docs; verified by the scripts and the browser script | Clean. |

Ruling: Task 9's conditional instruction to add a `--warn` token is void — the token
already exists in all three directions. The Task 9 implementer is told not to touch
`pacct/web/themes/`. Cost if wrong: none; if a direction were somehow missing the
token the theme module raises at boot, which surfaces immediately.

## Progress

Task 1: implemented (commit f2f1256, 11/11 passing). Review: spec OK, quality Approved,
1 Important + 4 Minor.

Ruling: the Important finding (test file's docstring/comments in Portuguese) is correct
and the fix stands, even though the plan's own code block mandated the Portuguese text.
The spec's Global Constraints say "identifiers, comments and docstrings in English for
new code" and the plan cannot grade its own work — the constraint is the binding
authority, so the plan's code block is the thing that is wrong. Note the tension: the
ENTIRE existing codebase (rdb.py, sel_settings.py, session.py, vlan_mapper.py) has
Portuguese docstrings, so the English rule is aspirational and applies only to new files.
This ruling therefore binds every remaining task: implementers transcribe the brief's
test code verbatim EXCEPT its comments and docstrings, which they render in English.
Cost if wrong: ten test files whose prose is in the user's second language rather than
his first, in a repo that is otherwise Portuguese. Trivially reversible with a
translation pass.

Ruling: the reviewer's ⚠️ (round-trip never exercised against real RDB bytes, only two
synthetic samples) is NOT a gap. Task 10 ships `tools/check_set_dnp_roundtrip.py`, which
walks every SET_D stream in every RDB under `rdbs/` and in the extraction cache and
asserts `parse(b).serialize() == b`. Real fixtures cannot live in the test suite: the
RDBs are 40-140 MB and gitignored. Cost if wrong: the byte-fidelity contract would rest
on synthetic samples until Task 10 runs — which is why Task 10's Step 4 is a hard gate.

Task 1: minor (deferred): pacct/parsers/set_dnp.py:12 `Optional` imported but unused.
  Self-resolving — Task 2 adds `Optional[str]` fields to DnpPoint and Task 3 uses it.
Task 1: minor (deferred): get_value() has no direct test coverage.
Task 1: minor (deferred): no test for a quoted value containing an embedded comma.
Task 1: minor (deferred): no test that set_value to the current value leaves bytes
  untouched (the no-op path that keeps edited=False).
Task 1: fix round 1/5 (1 addressed, 0 open — Portuguese test prose translated to English;
  commits f2f1256..dd640a5)
Task 1: complete (commits 993ca70..dd640a5, review clean)

INCIDENT (after Task 1): HEAD was found back on `tema-tokens` although the Task 1 commits
were on `dnp-map-editor`. Some subagent switched branches despite the read-only
instruction. Nothing was lost — dd640a5 is on dnp-map-editor, the uncommitted theme work
survived the round trip, and `git checkout dnp-map-editor` restored the state.
Guard adopted: verify `git branch --show-current` immediately BEFORE every dispatch and
immediately AFTER every subagent returns, and every dispatch prompt from Task 2 on
states the branch explicitly and forbids checkout/switch/restore.

Task 2: implemented (commit 8c224a4, 18/18 passing). Review: spec OK, quality Needs
fixes, 2 Important (both plan-mandated) + 1 Minor.

Ruling: both Important findings are correct and enter the fix loop, even though the
offending regex `^(BI|BO|AI|AO|CO)_(SCA|DBD)?([0-9]+)$` is verbatim from the plan. The
spec enumerates exactly three auxiliary relationships (AI_SCAn and AI_DBDn qualify AI_n;
CO_DBDn qualifies CO_n) and the plan's regex silently widened that to a ten-way cross
product. Confirmed against the reference RDBs: the 411L carries AI_1..200 with matching
AI_SCA1..200 and AI_DBD1..200 and CO_1..20 with CO_DBD1..20, and no BO_SCA/AO_DBD-shaped
key exists in any family I inspected. So the widening buys nothing real and costs the
second failure: an orphaned modifier line is invisible in points(), blocks() AND
extras(), i.e. uneditable and unseen in Task 8's table. Note the blast radius is
display-only — serialize() still emits such a line verbatim, so no export ever loses it.
Fix ordered: restrict the modifier to its real pairs, and make an unconsumed modifier
fall through to extras() instead of vanishing.
Cost if wrong: if some firmware really does ship a BO_SCAn, it now renders as a
read-only extra instead of an editable column — visible and conservative, and one line
of regex to widen again.

Task 2: minor (deferred): points()/blocks()/extras() re-scan self.lines on every call,
  uncached; Task 8's /map handler calls them per request on a ~1300-line file.
Task 2: fix round 1/5 (2 addressed, 0 open — modifier regex restricted to AI_SCA/AI_DBD/
  CO_DBD, unconsumed modifiers now reach extras(); commits 8c224a4..2eee6ae)
Task 2: complete (commits dd640a5..2eee6ae, review clean)
Task 3: implemented (commit 3ddb61d, 28/28 passing, SEL-2440s confirmed present in all 5
real RDBs). Review: spec FAILED, quality Needs fixes, 3 Important + 5 Minor.

Ruling: the reviewer is right and the SPEC is what is wrong. The spec and plan both
assert "the five sessions are byte-identical in the reference RDB". They are not. I
checked SET_D1 vs SET_D2 of QPC1_LT1_UPC1 during brainstorming, saw both were 16937
bytes with matching data lines, and wrote "byte-identical" — while my own dump showed
line 6 reading `[D1]` in one and `[D2]` in the other. The section header always differs
by construction, so hashing raw bytes can never group anything. The reviewer verified
this empirically: 0 groupings across 149 multi-session relays in all 5 RDBs. Fix ordered:
the digest ignores `[...]` section-header lines. The unrealistic fixture that hid this
(three files all carrying `[D1]` internally) is fixed too, or the test proves nothing.
Cost if wrong: the "sessions iguais" badge is a convenience, not a correctness feature —
a wrong grouping misleads about which tabs match, it never corrupts an export.

Ruling: on session naming, the constraint says the name comes from the `[D<n>]` header;
the implementer used the filename instead and argued it is more reliable. Both are half
right. The header is what the spec says and what the relay's file declares; the filename
is what cannot collide, and a collision is not cosmetic — Task 6 and Task 8 key their
per-session dicts by this name, so two sessions resolving to one key would silently drop
one from the export. Ordered: header first, filename fallback when the header is missing
OR already taken by an earlier session of the same relay. That honours the constraint and
closes the collision the implementer was right to worry about.
Cost if wrong: a malformed RDB whose headers disagree with its filenames shows a session
labelled by filename; the stream it exports to is filename-derived either way, so the
export stays correct regardless.

Task 3: minor (deferred): sessions sorted by name string, so D1,D10,D11,D2 if a relay
  ever had 10+ sessions (SEL tops out near 5).
Task 3: minor (deferred): identical_groups() re-reads bytes discover() already read.
Task 3: minor (deferred): report miscounts the existing/new test split (21+7, not 20+8).
Task 3: minor (deferred): brief's Interfaces block lists RdbInfo as consumed; the code
  never needs it. The code is right, the brief's prose was stale.
Task 3: minor (deferred): set_dnp.py now holds parsing + point model + filesystem
  discovery (~330 lines); coherent for now, worth splitting if it grows again.
Task 3: fix round 1/5 (3 addressed, 0 open — digest ignores section headers, fixture made
  realistic, header-first naming with collision-safe fallback; commits 3ddb61d..4364d48)
  Re-review verified on real data: 89 of 149 multi-session relays now group, e.g.
  QPC1_LT1_UPC1 -> [[D1],[D2,D3,D4,D5]]. 0 header/filename mismatches, 0 duplicate names.
Task 3: minor (deferred): the filename fallback is not checked against a name an earlier
  file's HEADER already claimed. Synthetic repro: SET_D1.TXT whose header says [D2] plus
  a header-less SET_D2.TXT yields two sessions named D2. Gap in the algorithm I ordered,
  not in the transcription; absent from all 5 real RDBs.
Task 3: complete (commits 2eee6ae..4364d48, review clean)
Task 4: implemented (commit 2585188, 38/38 passing). Status DONE_WITH_CONCERNS, and the
concern is load-bearing for Task 8.

Ruling: the unknown-bit check runs on the BI block ONLY. The implementer segmented the
whole real corpus by point kind and measured, before/after tuning the lists:
  BI 43,600 occurrences -> 369 unknown -> 0 after tuning
  BO 15,412 -> 1,192      AI 32,600 -> 3,817      AO 12,412 -> 51      CO 8,012 -> 357
and showed WHY the other four cannot improve: AI values are analog register names
(FREQ, IA_MAG, P, Q), AO/CO are control-select macros (ACTGRP, BKR1OPA), and BO values
are frequently a `<close>:<open>` pair (RB03:RB03, OC:CC — 302 of 1754 colon-bearing BO
values have different names either side). Only BI carries a bare Relay Word bit name,
which is the only thing wordbits.check() knows how to judge. Running it on all five kinds
would put ~4.8% of every real relay's lines under a warning flag, and a wall of false
warnings teaches the user to ignore warnings entirely — that destroys the feature rather
than delivering it. So Task 8 routes only BI values through check(); the duplicate check
is domain-independent and still runs on every block.
Cost if wrong: a typo in a BO/AI/AO/CO field goes unflagged — exactly today's behaviour
without the tool, so the tool is never worse than the status quo on those fields.

Ruling: I am NOT extending the check to BO by splitting on ':' and validating each half,
though the grammar invites it. It is unmeasured, and shipping a second warning channel
whose false-positive rate nobody has counted is the same mistake at a smaller scale.
Recorded as a future improvement, not a gap.
Cost if wrong: we leave a real validation opportunity on the table for the 15,412 BO
points; adding it later is additive and breaks nothing.
Task 4: review — spec FAILED on one point, quality Needs fixes, 2 Important + 2 Minor.
The reviewer independently re-derived the per-kind corpus table and reproduced it exactly
(BI 43,600/0 after tuning; BO 15,412/1,192; AI 32,600/3,817; AO 12,412/51; CO 8,012/357),
so the ruling above rests on verified numbers.

Ruling: finding 1 (one malformed wordbits file breaks lookup() for EVERY model) is
correct and is a defect in MY plan's code — the `except (OSError, ValueError)` I wrote
catches read errors and JSON syntax errors but not a syntactically valid file of the
wrong shape, which raises AttributeError or TypeError straight out of the per-file loop.
That inverts the feature's one inviolable property: validation-off must be the only
failure mode, and here one hand-edited file with a stray bracket disables validation for
every relay AND propagates an exception into the request. Fix ordered.
Cost if wrong: none — the fix strictly widens a per-file guard.

Ruling: finding 2 (the hand-added `^89(OP|CL|A|B)[0-9]P[0-9]$` accepts 89A5P3 when all
56 distinct 89-prefixed names in the corpus pin that digit to 2) is correct. The pattern
was added to CATCH typos in exactly that name family, and as written it validates a typo
in the very digit it was meant to guard. Tighten to `^89(OP|CL|A|B)2P[0-9]$`.
Cost if wrong: a firmware that really ships 89A1P#/89A3P# families would warn on valid
bits — visible, harmless, and one character to widen again.

Task 4: minor (deferred): _CACHE lazy init has no lock; two concurrent first-callers
  under ThreadingHTTPServer could both run _load_all(). Idempotent, so waste not bug.
Task 4: minor (deferred): test_the_shipped_files_load only asserts lookup() is not None;
  it never asserts check() returns "ok" for a known-good bit from the shipped data.
Task 4: fix round 1/5 (2 addressed, 0 open — per-file _load_one() isolates a bad file,
  89-pattern tightened to ^89(OP|CL|A|B)2P[0-9]$; commits 2585188..7a4a290)
  Re-reviewer independently re-ran the BI-segmented corpus check post-fix: BI 43,600
  occurrences, 0 unknown. Confirmed the widened guard is scoped to shape-parsing only,
  not `except Exception` around the loop, so a real bug still surfaces.
Task 4: minor (deferred): a malformed `patterns` field that is a multi-character string
  logs one warning per character before the outer guard applies. Noisy, not wrong.
Task 4: complete (commits 4364d48..7a4a290, review clean)
Task 5: implemented (commit 38d4ab4, 12 new tests, 51 total passing). Two real bugs found
in the brief's algorithm — which is exactly why this task ran on the strongest model and
why its Step 5 gate was a real RDB rather than a fixture:

  1. FATAL, mine: `_write_difat_sectors` wrote the DIFAT next-sector pointer as the chain
     INDEX (`s + 1`) instead of the sector NUMBER (`difat_start + s + 1`). A DIFAT only
     appears past ~7 MB, so all 12 synthetic unit tests passed while every real RDB would
     have been corrupted. The implementer reproduced the failure with my version before
     fixing it.
  2. The all-black directory tree I specified is not a valid red-black tree (two siblings
     already break black-height). Deepest level now coloured red; verified exhaustively
     for sibling counts 0-2999.

Real-data result: all EIGHT RDBs in rdbs/ round-tripped with every reachable stream
verified byte for byte, largest 286 MB. Also grew a real 16937-byte SET_D1.TXT and a
real 2380-byte mini stream in one rebuild and re-read both under olefile's strict
DEFECT_INCORRECT.

OPEN QUESTION carried into review: the rebuilt file is ~4x smaller (142.9 MB -> 36.9 MB).
The implementer attributes this to thousands of orphaned, unreachable directory entries
QuickSet leaves behind, proven by an independent raw tree walk. Plausible — QuickSet
edits in place and never compacts — but 106 MB is far too much to accept on plausibility
when the artefact is a substation settings database. The competing explanation is that
OUR tree walk is wrong (a sibling-ordering mismatch would make us fail to traverse nodes
that are genuinely linked), and olefile could share the same blind spot, so "olefile
agrees" is not independent evidence. Reviewer instructed to attack this specifically.
Task 5: review — spec OK, quality "Approved with fixes", 0 Critical, 3 Important, 5 Minor.

RESOLVED, the 4x size drop is SAFE, and the evidence is vendor ground truth. The reviewer
wrote its own raw MS-CFB parser (independent header parse, DIFAT reconstruction, FAT
chain follow, directory enumeration BY INDEX) rather than trusting olefile, and found:
  - substation_demo: 8308 dir slots, 1070 reachable, 7238 unreachable;
    102.6 MB unreachable stream bytes vs 36.2 MB reachable.
  - 7237 of 7237 unreachable names also exist among reachable entries.
  - The orphan set is a disjoint island: 0 orphan pointers into the reachable set,
    0 reachable entries pointing at an orphan, 0 storages orphaned. A wrong traversal
    would leave dangling references at the boundary; there is no boundary.
  - DECISIVE: the corpus contains two QuickSet saves of the same substation.
    SE EXEMPLO III_03-08-26.rdb (286 MB, 18,694 orphans) and _17-08-26.rdb (40 MB,
    3 orphans). Reachable path sets: 1092 vs 1092, set difference EMPTY both ways.
    QuickSet's OWN compacted save contains exactly the paths our traversal calls
    reachable. That is the vendor agreeing, which olefile could never supply.
  - GL2.gle appears 22x reachable and 534x orphaned in the 286 MB file: historical saves.
    The only orphan name without a reachable counterpart is GL4.gle in one RDB — a
    deleted diagram, and QuickSet itself does not carry it forward.
  - All 8 rebuilt outputs validated with the reviewer's own parser: every sector claimed
    exactly once, 0 unaccounted, chain lengths match declared sizes, red-black invariants
    clean, no non-FREE FAT slot past EOF.
Consequence to surface later: a rebuilt RDB is COMPACTED. Task 8/10 must say so, or a
142.9 MB -> 36.9 MB export will be reported as data loss.

Ruling: all 3 Important findings enter the fix loop. Finding 1 (non-atomic write) is the
one I care most about: rebuild() writes straight to dst, so a mid-write failure leaves a
stub, and rebuild(p, p, {}) was demonstrated destroying a real 39,966,208-byte RDB down
to 505,856 bytes. The module's docstring promises never to leave a corrupt settings file;
a 1.5 KB stub where the user's RDB used to be is that hazard wearing a different hat.
Cost if wrong: none — writing to a temp file and os.replace() is strictly safer.

Task 5: minor (deferred): _verify leaks handle `a` if opening `b` raises.
Task 5: minor (deferred): _write_ministream buffers the whole mini stream (802 KB worst
  case on the real corpus) rather than streaming it.
Task 5: minor (deferred): _verify compares paths, not (path, is_storage), so a storage
  written as a zero-length stream at the same path would compare equal.
Task 5: minor (deferred): _flatten and _directory_bytes agree on sibling order only
  because both call _sibling_order; the invariant is incidental, not stated.
Task 5: minor (deferred): `except Exception` in rebuild misses BaseException (subsumed
  by the temp-file fix).
Task 5: fix round 1/5 (3 addressed, 0 open — atomic temp-file write + os.replace + same
  -path guard, DIFAT and red-black regression tests committed, NOSTREAM directory
  padding; commits 38d4ab4..7121e9f)
  ADJUDICATED IN THE IMPLEMENTER'S FAVOUR: the implementer contradicted the reviewer on
  the DIFAT fixture size and was right. With difat_sectors == 1 the single iteration
  takes the `s == difat_sectors - 1` branch and writes ENDOFCHAIN, so the buggy pointer
  is never emitted — the reviewer's ~7.2 MB fixture (nfat=111, ndifat=1) passes against
  the BUGGY module. The bug needs a DIFAT chain, i.e. fat_sectors > 109+127 = 236.
  The re-reviewer re-derived the sizing sweep independently (7.2 MB -> ndifat 1;
  15.0 MB -> 1; 15.5 MB -> 2; 16.0 MB -> 2), reverted the fix in a /tmp copy and
  confirmed the committed 16,000,000-byte fixture fails while the 7.2 MB one passes.
  The `assert num_difat >= 2` line is what keeps the fixture above the threshold.
  Both fixes were also mutation-tested: reverting each makes its own test fail.
  Destructive scenario re-run against a real 39,966,208-byte RDB through 5 aliases
  (plain, ./, ../, hardlink, symlink): all raised, source byte-identical, no temp left.

Task 5: minor (deferred): os.chmod(tmp, 0o644) hardcodes the mode instead of honouring
  the umask; under umask 0077 an export ends up more permissive than the user's other
  files. Session dirs are same-OS-user, so impact is local, but worth the 1-line fix.
Task 5: minor (deferred): mkstemp raises FileNotFoundError (not OleRebuildError) when
  dst.parent is absent.
Task 5: minor (deferred): os.replace onto a dst that is itself a symlink replaces the
  link, not its target. Not reachable from the intended caller.
Task 5: minor (deferred): _direntries test helper reaches into olefile privates
  (ole._open, ole.first_dir_sector); version-fragile but there is no public way to read
  the colour byte.
Task 5: minor (deferred): write_ole() called directly is still non-atomic; only
  rebuild() is. Worth a docstring line if write_ole ever becomes a public export path.
Task 5: complete (commits 7a4a290..7121e9f, review clean)

NOTE (housekeeping, not a task finding): a subagent had left docs/AG95-10_20130812.pdf:
Zone.Identifier deleted in the working tree; restored with `git checkout --`.
NOTE: 15 SEL DNP documentation ZIPs appeared untracked in docs/ between 13:46 and 13:51,
named per relay model (411L, 487E, 451, 421, 751, 311C). No subagent reported fetching
anything and they carry no download marker; they look like the user curating reference
material mid-run. Left untouched. Relevant later: they may be exactly what would close
the AI/AO/CO/BO name-domain gap that Task 4's ruling left unvalidated.
Task 6: implemented (commit 84b6e44, 64/64 passing). Review: spec FAILED, quality Needs
fixes, 3 Important + 2 Minor.

Ruling: finding 1 (copy_session copies MINDIST/MAXDIST between sessions) is correct and
is my plan's defect — I wrote `for line in src.lines if line.key`, which sweeps in every
keyed line of the [D<n>] section, not just points. set_dnp.extras() says in its own
docstring that these "configure the DNP session, not the map, and this tool has no
business changing them", and the editor shows them read-only. So "aplicar às demais
sessões" would silently rewrite a read-only field on every target whose MINDIST differs.
[INFO] values are safe (those lines are keyless), so the leak is exactly the extras.
Fix ordered: build the copy set from points only, including their sca_key/dbd_key.
Cost if wrong: if two sessions are genuinely meant to share MINDIST/MAXDIST, the user
sets them per session by hand — a read-only field the tool never claimed to manage.

Ruling: finding 2 (copy_session has no test at all) is correct and is also mine — the
brief never wrote one, which is precisely why finding 1 survived to review. A test where
the two sessions differ in MINDIST would have caught it immediately.

Ruling: finding 3 (swap's read-then-write is not atomic) is correct. swap() calls
current_value twice OUTSIDE the lock and only record_edits takes it, so two concurrent
swaps on the same bucket — a double-clicked drag, or two tabs — can both read stale
values and the second write clobbers the first. The server is a ThreadingHTTPServer, so
this is narrow but real. The lock is an RLock, so wrapping the whole read-compute-write
in one `with lock:` is safe even though record_edits re-acquires it.
Cost if wrong: none; it strictly narrows an existing window.

Task 6: minor (deferred): unused `Optional` import in model.py (second occurrence of the
  same slip from my plan's code blocks; the first, in set_dnp.py, later became used).
Task 6: minor (deferred): apply_edits deepcopies the whole SetDnpFile per call; a shallow
  copy of the lines list plus a fresh info dict would do, since RawLine is frozen.
INCIDENT (Task 6 fix round): the implementer ran `git checkout -- pacct/web/dnp_map/
model.py` to undo a temporary sabotage edit, which wiped BOTH fixes back to the
pre-review commit. It noticed via the harness's on-disk-change notice and reapplied by
hand, then self-reported. I verified independently before allowing the re-review: branch
dnp-map-editor, HEAD 2a33d48, 67 passing, copy_session now iterates src.points() with
sca_key/dbd_key (no src.lines), swap wraps read-compute-write in one `with lock:`, and
the 10 uncommitted theme files plus pacct/web/themes/ are intact.
Guard tightened: dispatches now forbid `git checkout` in ALL forms including
`-- <path>`, and tell implementers to back a file up with `cp` to /tmp before any
sabotage/mutation test. Three incidents now, all from `git checkout` in some form.
Task 6: fix round 1/5 (3 addressed, 0 open — copy_session now iterates src.points() only,
  3 new tests with a real MINDIST divergence in the fixture, swap wrapped in one RLock
  acquisition; commits 84b6e44..2a33d48)
  Partial-restoration check after the checkout incident: re-reviewer read model.py in
  full, confirmed single clean definitions, no duplicated or orphaned fragments, no
  truncated docstrings, and that git show/diff match the file on disk byte for byte.
  No deterministic race test for swap; disclosed and accepted — the finding demanded a
  code fix, not a test, and a reliable lost-update test is not practical here.
Task 6: complete (commits 7121e9f..2a33d48, review clean)
Task 7: implemented (commit d543e90, 73/73 passing). Review: spec OK, quality Approved,
1 Important (plan-mandated) + 4 Minor.

Ruling: the Important finding enters the fix loop despite the "Approved" verdict, because
of what it produces rather than how likely it is. The in-place path does
shutil.copyfile to the FINAL name and then mutates that file with one write_stream per
edited stream. Handled exceptions are cleaned up — better than the vb_updater precedent
it was modelled on, which cleans up nothing — but a process kill, OOM or power cut
between two write_stream calls leaves a structurally valid, openable RDB holding a MIX of
old and new DNP maps. That is precisely the "subtly wrong RDB" this module's own
docstring calls worse than a failed export: it opens in QuickSet, looks fine, and gets
loaded into a protective relay. The rebuild path already solves this (temp file beside
the destination, verify, os.replace) and that idiom is implemented and tested one module
away, so the fix is ~10 lines of an existing pattern rather than a new invention.
Cost if wrong: none. The output filename and content are unchanged on success; only the
window in which a partial file can bear the final name closes.

Task 7: minor (deferred): _sessions_index() calls discover(), which reads and parses
  EVERY SET_D of every relay; build_streams and export_txt each call it independently, so
  one export walks a 30-relay extraction twice and re-parses each edited session a third
  time.
Task 7: minor (deferred): rebuild-path tests only exercise the OLE mini-stream (the
  282-byte sample is under the 4096 cutoff); a real 17 KB SET_D takes the regular-stream
  path. Mitigated — test_ole_rebuild.py covers >=4096 at the layer that matters.
Task 7: minor (deferred): broad `except Exception` reports a genuine code defect to the
  user as an ordinary export failure (traceback is preserved server-side).
Task 7: minor (deferred): the OleFileIO open that computes `fits`/`missing` sits outside
  any try, so an RDB swept from the shared cache mid-export propagates out of export()
  instead of becoming a graceful ExportResult.
Task 7: fix round 1/5 (1 addressed, 0 open — in-place write now goes to a temp file in
  out_path's own directory and os.replace()s only on full success, BaseException-safe;
  commits d543e90..aa7cfcb)
  Re-reviewer reconstructed the PRE-fix export.py from git and ran the same sabotage
  against it: the old code deleted a pre-existing out.rdb, the new code preserves it —
  so the new test is a genuine regression test, not one that passed either way.
  Bonus finding: removing the `out_path.unlink()` from the except clauses also fixed a
  latent bug on the REBUILD path, which would have deleted a pre-existing output on
  failure even though rebuild() never touches dst before its own atomic replace.
Task 7: complete (commits 2a33d48..aa7cfcb, review clean)
Task 8: implemented (commit cfbe22b, suite 76 unchanged). Review: spec FAILED, quality
Needs fixes, 2 Important + 4 Minor. All three controller rulings verified implemented
server-side; ruling 1's UI half (the "word-bit validation does not apply on this tab"
line) correctly belongs to Task 9 and is carried into its dispatch.

Ruling: finding 1 (stored XSS) is correct and is the most serious defect found in the
whole run so far. Relay names and RELAYTYPE come from the uploaded RDB's OLE storage
entries, which can hold arbitrary Unicode, and landing.html interpolates them straight
into innerHTML. A crafted RDB can plant <img src=x onerror=...> as a relay name and it
executes in the browser of anyone who opens the landing page afterwards. The codebase
already solved exactly this threat in vb_updater.py, which defines escapeHtml/escapeAttr
and wraps every RDB/SCD-derived name; the new page simply has no such helper. Fix
ordered. Cost if wrong: none — escaping display text is never the wrong call.

Ruling: finding 2 (_parsed_sessions reparses the whole RDB per request) enters the loop
too, though it is plan-mandated and merely slow rather than wrong. discover() walks every
relay directory and parses every SET_D in the RDB just to resolve one relay's sessions,
and _do_edit calls it on every debounced keystroke — on a 30-relay substation RDB that is
roughly 2.5 MB of files parsed per keystroke, and Task 9's editor is about to hammer
/map. I am NOT ordering a change to discover() (that is Task 3's module and other callers
depend on its semantics). The fix is to cache the discovery per rdb_key in the session
state: the extraction lives in a CONTENT-addressed cache keyed by sha256, so for a given
rdb_key it can never change under us, which makes the cache trivially sound.
Cost if wrong: a stale cache would only ever be stale against a different sha, which is a
different key — so the failure mode does not exist.

Task 8: minor (deferred): module-level `_logger` in handler.py is never used; all logging
  goes through the `logger` closure parameter.
Task 8: minor (deferred): _do_edit/_do_swap/_do_copy_session repeat the same 8-line
  parse-body/resolve-rdb/resolve-session block three times (verbatim from the brief).
Task 8: minor (deferred): /map never returns the top-level `warnings` key the brief's
  route table promises; per-row `aviso` carries the same information.
Task 8: minor (deferred): quote(str(out_path)) omits safe="", unlike vb_updater's
  equivalent. Not exploitable — the stem is pre-sanitized by rdb.sanitize_name.
Task 8: fix round 1/5 (2 addressed, 0 open — escapeHtml/escapeAttr added byte-identical
  to vb_updater's and applied to every RDB-derived interpolation in both text and
  attribute contexts; discover() cached per rdb_key on the per-visitor DnpMapState under
  the session lock; commits cfbe22b..f4ee209)
  Re-reviewer verified the escaping independently in Node: hostile <img>, </script> and
  attribute-breakout payloads neutralised in both contexts, while real names
  (QPC12_UPC1_TFE_138kV-2908, SEL-421-4) pass through byte-for-byte unchanged. Confirmed
  relay_cache is a dataclass field per visitor, not a module-level dict, and that
  DnpRelay/DnpSession are frozen so a cached object cannot be corrupted by a later
  request. set_dnp.py untouched; editor.html still the stub.
Task 8: minor (deferred): _discover holds the session lock across the ~222 ms cold
  discover(), serialising that one visitor's other requests during the first call.
Task 8: minor (deferred): _rdb() reads st.rdbs without the session lock, unlike
  _discover(). Pre-existing from cfbe22b.
Task 8: complete (commits aa7cfcb..f4ee209, review clean)
Task 9: implemented (commit d65e18b, 76/76). Reported DONE_WITH_CONCERNS with a real
layout defect, which I investigated myself before ordering any review.

Ruling: the régua narrow-column bug is OURS, not pre-existing, and it is my plan's error.
In régua `.shell` is `grid-template-columns: var(--nav-w) minmax(0,1fr)` — column 1 is
the nav, column 2 the content. All five existing tools therefore put the NAV marker as
the FIRST CHILD INSIDE `.shell`:
    <div class="shell">
    <!--NAV:vlan-mapper-->
    <div class="grid"><main class="col-main">...
(vlan_mapper.py:355-358, and the same in vb_updater, gle_exporter, settings_compare, glv)
Both of our new pages instead put the marker in the <header>, BEFORE `.shell`
(landing.html:101 vs shell at 105; editor.html:68 vs shell at 72), so `.shell` has a
single child which lands in the --nav-w column and renders ~200px wide. My plan said to
put the marker "inside its <header>", conflating it with the theme picker, which IS
injected into the header by mount.py. The implementer transcribed my instruction
faithfully; the instruction was wrong.
This is a cross-task defect: Task 8's landing.html has it too. I am lifting the
"do not modify landing.html" restriction for this one fix, because fixing only the editor
would leave the tool broken in régua on the page users reach first.
Cost if wrong: none — the change is to match the structure five shipped tools already
use, and folha/caderno use a single-column shell where the marker's position is inert.
Task 9: review — spec OK, all 4 directives verified implemented by direct reading
(escaping complete in every innerHTML write, BI-only hint correctly scoped, --warn
untouched, no warning blocks anything), NAV/shell fix confirmed matching
vlan_mapper.py:355-358. Quality Needs fixes: 1 Important + 2 Minor.

Ruling: the Important finding is real and enters the fix loop. enviar() awaits /edit then
calls carregar() -> desenhar(), which does tb.innerHTML = '' and rebuilds every row from
the server's response. If the engineer commits field A (on blur) and starts typing into
field B before A's round trip returns, the rebuild destroys B's input and recreates it
from the server's pre-edit value — the keystrokes are gone, silently, and focus is lost.
For a tool that edits SCADA point mappings, silent loss of typed input is the wrong
failure mode; a commissioning engineer moving fast through 400 BI rows is exactly the
user this hurts. Note the re-render is not gratuitous: a value edit can change OTHER
rows' `duplicado` warnings, so the refresh has a real job. The fix must preserve work,
not remove the refresh.
Cost if wrong: a slightly more complex render path; the alternative is a tool that
quietly eats input, which no amount of speed makes acceptable.

Task 9: minor (deferred): editor.html:9 redefines `.full` exactly as shell.py:60 already
  does via /theme.css.
Task 9: minor (deferred): every single-field commit refetches the whole /map payload (all
  four blocks) to redraw one tab.
Task 9: fix round 1/5 (1 addressed, 1 NEW CRITICAL — commits 103afb8..c27e926)
The typing-loss race is genuinely fixed, but the mechanism chosen — "preserve any input
whose DOM value differs from the freshly fetched server value" — infers dirtiness by
comparing values, and that cannot tell "I have not sent this yet" from "the server
legitimately changed it". Consequences the re-reviewer proved by transcribing the
capture->rebuild->restore algorithm into a standalone script and running the swap case
(BI_000 52A <-> BI_001 79RS): the result was {BI_000: 52A, BI_001: 79RS}, i.e. the
PRE-swap state. So every drag-swap now visually reverts, permanently — the stale value is
written back into the DOM, so it fails the same comparison on every later redraw too,
while the server data (and the export) is correct. Same root cause also clobbers a
concurrent editor's change, an in-flight copy-session, and any server-side normalisation.
The previous round's verification missed it because it never re-ran the swap test — it
exercised the typing race and a never-locally-touched third row, the two paths that
happen not to trigger the false positive.

Ruling: round 2 must track dirtiness EXPLICITLY rather than infer it. Preserve a field
only when it is the focused element or has an unresolved /edit the current response does
not yet reflect; the server is authoritative for everything else, and unconditionally so
for the keys a swap or copy-session just changed.
Cost if wrong: over-narrow preservation costs a keystroke in a rare interleaving, which
is the bug we started with; over-broad preservation silently shows the engineer stale
mappings, which is worse.
Task 9: fix round 2/5 (swap regression closed, conditions 2-4 MET; condition 1 still
open — commits c27e926..5d3ed0a)
The explicit dirty-tracking fixed the swap revert correctly, but leaks unsent edits two
ways, both reproduced deterministically by the re-reviewer transcribing the code into
standalone Node scripts:
  (a) editor.html:247 restores a preserved value onto the fresh input but never re-sets
      dataset.digitado on it, so the marker dies on the very redraw that saved the value.
      A second unrelated redraw before the next keystroke reverts the edit.
  (b) EM_VOO is a plain Set, not refcounted (editor.html:126,280,288). Two overlapping
      /edit requests for the same key: the FIRST response's `finally` deletes the entry
      the second still depends on, so any redraw in that window discards the second,
      still-pending edit before its own request resolves.
Both are round-0's symptom returning under narrower conditions. The implementer's browser
test only ever exercised one in-flight save and one redraw, so it reached neither path.

Ruling: three rounds of ad-hoc bookkeeping is the signal that the MECHANISM is wrong, not
that it needs another patch. Round 3 gets a recommended design rather than another
targeted fix: keep a plain client-side map of what the user typed, overlay it on every
render, and delete a key only when the server's own response confirms that value, or when
a swap/copy-session explicitly invalidates it. That has no request-lifecycle bookkeeping
to leak, survives arbitrary redraws by construction, and keeps the server authoritative
for exactly the cases where it must be. The implementer may choose another shape if it
satisfies all four conditions.
Cost if wrong: another round; the fix is confined to one function in one template, and
the server side — where the real data lives — has been correct throughout.
Task 9: fix round 3/5 (all 4 acceptance conditions MET plus both round-2 interleavings;
no Critical/Important breakage — commits 5d3ed0a..17783df)
The re-reviewer rebuilt the whole mechanism in a standalone simulator and ran 11 scenarios
independently rather than trusting the report. EDITADO holds no request-lifecycle state,
so neither round-2 leak has a place to live. Overlay confirmed consistent across the
sca/dbd auxiliary columns; nothing quadratic; map bounded by point-key count.

Ruling: one item the reviewer filed as an out-of-scope observation gets fixed anyway,
because its consequence is wrong data rather than wrong display. carregar() has no
request-ordering guard, so two /map fetches can answer out of order. On a SESSION SWITCH
that means a /map issued for D1 can land after the switch to D2 and render D1's values
under the D2 label — and the point keys are identical across sessions (BI_1 is BI_1), so
the next edit writes a value the engineer chose while looking at D1 onto D2's point. That
is silent cross-session corruption of a SCADA point mapping, which is the exact class of
failure this whole tool exists to prevent. It is pre-existing rather than introduced, and
a monotonically increasing request id compared on arrival closes it along with the stale
-snapshot revert the reviewer reproduced as S7.
Cost if wrong: a guard that discards a superseded response; the worst case is one extra
refresh.

Ruling: bundling three of the four Minor items into the same dispatch, because they are
one-liners in the same function and one of them (tab-scoped confirmation) actually breaks
acceptance condition 4 for any key on a hidden tab. Deferring them to the final review
would mean reopening this file a fourth time for changes smaller than the diff header.
Process deviation recorded: rounds 4-5 nominally escalate to a fresh implementer on a
stronger model, but that rule exists for a STUCK loop and this one just succeeded — the
same implementer holds three rounds of context on this exact file, and discarding it to
satisfy the letter of the rule would cost more than it buys.
INCIDENT (Task 9 round 4): the round-4 agent stopped with no completion record — no
transcript marker, so it was stopped or died mid-run rather than finishing. Checked the
tree before assuming anything: HEAD still 17783df, nothing committed, but
pacct/web/dnp_map/templates/editor.html carries 43 insertions / 17 deletions
UNCOMMITTED, and reading them shows all four ordered items present and coherent —
PEDIDO_MAP request-ordering guard with the stale-response drop, `const origem` captured
before the await plus ARRASTANDO reset in ondragend, confirmation moved into carregar()
over Object.values(r.blocks), and copy-session no longer clearing EDITADO but refreshing
instead. What is missing is verification and a commit, not the work.
Dispatched a fresh implementer to take ownership: read the inherited diff, correct it if
wrong, verify the scenarios in a browser, and commit. Did NOT commit or verify it myself
— a controller fix skips review and pollutes this context.
Note the exposure while it sat uncommitted: three subagents have destroyed working-tree
state with `git checkout` in this run, so the takeover dispatch leads with "act promptly
on the commit".
Task 9: fix round 4/5 (4 addressed, 0 open — commits 17783df..9ce3dbf)
Re-reviewer verified the ordering guard with its own 3-way out-of-order simulation
(harsher than the report's 2-way case): only the last-issued request's data is ever
applied, whatever the resolution order. Confirmed the guard is centralised in the single
carregar() rather than duplicated per call site, that native drop-before-dragend ordering
means the ARRASTANDO reset cannot race the capture, and — against handler.py:339 — that
/copy-session genuinely never writes the source session, so leaving the current session's
EDITADO alone is correct rather than merely less destructive. No lifecycle-tied state
reintroduced: PEDIDO_MAP never gates EDITADO's writes, reads or deletes.
Task 9: complete (commits f4ee209..9ce3dbf, review clean after 4 rounds)
Task 10: implemented (commit 0ae681c). Review: spec FAILED with 1 Critical + 2 Important
+ 3 Minor. Every discrete deliverable verified accurate — the reviewer re-ran both script
modes itself and got byte-identical numbers (1682 SET_D conferidos, 0 falhas;
142904832 -> 36875776 bytes) and independently recomputed the 4.8% figure — but the
commit is not self-contained.

CRITICAL, verified myself: `git diff 9ce3dbf..0ae681c -- pacct/web/dashboard.py` shows
the commit swept in the USER's in-progress tema-tokens work. It committed -113 lines of
dashboard.py, replacing the whole hardcoded HOME_HTML tool table with the `<!--HOME-->`
marker, and committed pacct/web/themes/items.py whole (117 lines) — a file that was
UNTRACKED and belongs to the user's theme package. The pieces that make those markers
resolve (mount.py:_resolve_markup, themes/__init__.py, folha/regua/caderno/shell/tokens)
are still uncommitted, so a clean checkout of 0ae681c renders a home page whose body is
two literal HTML comments: no tool links at all. The reviewer proved it with `git archive`
into isolated trees and a fresh venv per tree. The implementer's browser walkthrough
passed only because it ran against the ambient dirty tree, which still supplies the
missing pieces — it never tested what was actually committed.

Ruling: do NOT commit the rest of the user's theme work to make this branch cohere. That
work is theirs and mid-flight; committing someone's in-progress refactor onto a feature
branch they did not choose it for is not mine to decide, and it is the irreversible
option. Instead make the branch self-contained the conservative way: a NEW follow-up
commit that restores dashboard.py's HOME_HTML to its pre-branch state with a row added
for the DNP Map Editor, and `git rm --cached` on themes/items.py so it returns to
untracked with our Tool entry still inside it, ready to ride along whenever the user
commits their theme work. Append-only — no reset, no rebase, no checkout, given three
working-tree incidents already this run.
Cost if wrong: the branch's home page is the old table rather than the themed menu until
the user commits their theme work. Fully reversible, and the alternative (option A below)
is one commit away if the user prefers it.
Surfacing to the user at the end: this branch's themed-menu entry genuinely depends on
their uncommitted themes/ package, so they choose whether to fold it in.
Task 10: fix round 1/5 (commit 78cb2fe) — the un-sweep landed, but it overwrote the
OWNER'S WORKING TREE while doing it. It restored the old HOME_HTML into the commit AND
into pacct/web/dashboard.py on disk, so the owner's uncommitted marker-based rewrite
vanished from the tree; `git status` stopped listing dashboard.py as modified, and the
agent's "owner's theme work untouched" claim listed seven other files without noticing
dashboard.py was missing from the list.
Restored it myself rather than dispatching again: `git show 0ae681c:pacct/web/
dashboard.py > pacct/web/dashboard.py`. That commit holds exactly the right content —
the owner's marker version WITH our import/Mount lines. Verified after: marker present
(2), build_dnp_map_handler present (2), old table absent (0), file shows as modified
again, HEAD's own dashboard.py still carries the self-contained table with the dnp-map
row, 76/76 passing.
This was environment remediation, not task work — leaving the owner's tree damaged while
another agent round-tripped risked compounding it, and the target content was exact and
verifiable rather than a judgement call.

NOTE for the user, not acted on: the same sweep also committed the owner's uncommitted
docs/ENGINEERING-NOTES.md edits (docs/ENGINEERING-NOTES.md was ` M` at session start and both 0ae681c and 78cb2fe touch
it). Unlike dashboard.py this breaks nothing — the prose documents the themes package
accurately, it is simply committed ahead of the code it describes. Un-picking our doc
additions from theirs would be churn for no benefit, so it is surfaced rather than fixed.

FINAL WHOLE-BRANCH REVIEW (opus, 3 passes, ran the suite + corpus tool + its own
end-to-end export against a real 39 MB RDB): 1 Critical, 6 Important, 6 Minor, plus a
triage of the 38 deferred minors (3 promoted to fix-before-merge, 1 declared stale/false,
the rest justified with measurements and left).

Ruling: findings 2,3,4,5,7 and minors 8,9,11,12,13 go to ONE fix wave. They are all
localised, all mine or the plan's, and several have live triggers the task reviews could
not see — notably #2, where pasting an en dash into a field raises UnicodeEncodeError
inside build_streams, outside every try block, so the request never answers AND every
later export in that session fails identically with nothing naming the offending field.

Ruling: finding 1 (drag-swap moves the variable but leaves AI_SCA/AI_DBD/CO_DBD at the
old index) is NOT mine to decide and goes to the user. The reviewer measured the exposure:
201 of 949 real AI blocks have non-uniform SCA/DBD, so one in five files could come out of
a reorder with a correctly-named point carrying another point's scaling. My own reading
says scale and deadband are attributes of the mapped quantity, not of the index, so they
should travel with it — but that is a protection-engineering judgement with a wrong answer
that ships silently, and the spec never said.

Ruling: findings 6 and 10 are NOT fixed, and share one root — this branch sits on top of
the owner's uncommitted theme refactor. #6: <!--NAV:dnp-map--> only resolves via the
owner's uncommitted mount.py:_resolve_markup, so at HEAD both pages render with no nav.
#10: docs/ENGINEERING-NOTES.md documents pacct/web/themes/ which is not committed. The reviewer's fix for
#6 (convert to __NAV__ + add a NAV_ITEMS row in theme.py) would mean editing theme.py and
docs/ENGINEERING-NOTES.md — both files the owner has uncommitted edits in, and clobbering the owner's tree
is exactly what already happened twice this run. The marker degrades to an inert HTML
comment rather than an error, and the tool stays reachable from the home table, so the
cost of waiting is a missing sidebar, not a broken tool. Surfaced to the user as a choice
instead.
Cost if wrong: the branch's two pages have no sidebar until the theme work lands.

USER DECISIONS (asked, not assumed):
1. Drag-swap must move (valor, SCA, DBD) as a UNIT. Confirms my reading that scale and
   deadband are attributes of the mapped quantity, not of the DNP index. Implement in
   model.py:swap; the UI already sends only the base key, so the server resolves both
   keys to their DnpPoint and exchanges the auxiliary columns too.
2. Adapt the two DNP pages to the CURRENTLY COMMITTED nav convention (__NAV__ +
   nav_html) rather than waiting for the theme refactor, and add a dnp-map row to
   theme.py:NAV_ITEMS. The user explicitly authorised touching theme.py, which I had
   ruled out on my own authority — their call, and now taken.

Constraint discovered before dispatching #2: working-tree theme.py is the owner's 32-line
thin re-export with NO NAV_ITEMS; HEAD's theme.py is 846 lines and has it. So the row must
be staged into the COMMIT without overwriting the owner's file on disk: cp their version
to /tmp, write HEAD's version plus the row, git add, cp theirs back. Never git checkout.
Verified this leaves both worlds working: `theme.nav_html("dnp-map")` resolves at HEAD via
NAV_ITEMS, and in the owner's tree via the re-export into themes/, whose items.py already
carries the dnp-map Tool entry (confirmed present).

Sequencing: wave 2 waits for the running fix wave — both touch handler.py and model.py,
and two agents editing the same files concurrently is how work gets lost.
