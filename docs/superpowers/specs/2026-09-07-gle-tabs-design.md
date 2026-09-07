# Organizador de Abas GLE — reorder and rename the graphical logic tabs

*2026-09-07 — design*

## Problem

A relay's graphical logic lives in `Relays/<relay>/Misc/GL*.gle` inside the RDB.
Each `<page>` in that file is one **tab** in AcSELerator QuickSet's Graphical
Logic Editor. The corpus in `cache/rdb/` has 215 such files carrying 3 111
pages; the sample `samples/GL1.gle.xml` alone has 41 tabs — `Capa`, `Entradas`,
`Saidas`, `Goose`, `TRIP`, `50/62BF`, `Watchdog / Mon. DC`, and so on.

QuickSet gives no way to move a tab. A page lands wherever it was created, and
the only way to change the order is to recreate pages — redrawing the logic by
hand, on a file whose elements carry the relay's SELOGIC slot numbers. Nobody
does that, so the tabs of a real project drift into the order they happened to
be drawn in: `Capa`, `Reserva1`, `Entradas`, `Goose`, `RESERVA-2`, `RESERVA`,
`52 - Posicao` on `QPC2_TR1_UPC3`. Renaming is possible in QuickSet but is one
page at a time, through a properties dialog.

This is a documentation problem, not a protection problem — which is exactly
why it never gets fixed, and why every reviewer of the drawing pays for it.

## What a tab is, and what it is not

```xml
<pages>
  <page name="Capa" description="13.259786 - PROTECAO LT ..." revisiondate="10/05/2026"
        page_extra_area_space_x="827" page_extra_area_space_y="1169"
        page_zoom="1" page_h_scroll="722" page_v_scroll="1129">
    <elements> ... </elements>
    <connections> ... </connections>
  </page>
  ...
</pages>
```

There is **no index attribute**. Tab order is the order of the `<page>` children
of `<pages>`, and nothing else.

**Reordering cannot change what the relay does.** The compiled SELOGIC lives in
the settings streams as numbered slots — `set_L1.txt` holds
`SET02,"R_TRIG RB04 AND NOT LT03 ... # 79 BLOQUEADO"` — and each GLE element
binds to its slot through its own `physical_instance_number`, never through its
position in the file. Grepping a fully extracted relay
(`QPC2_TR1_UPC3`, SEL-751) for its page names finds them in `Misc/GL1.gle` and
in **no other stream**: not in the 24 `set_*.txt`, not in `SET_HMI.TXT`, not in
`Misc/Cfg.txt`. A page name is a label on a drawing.

That is what makes this tool small enough to be safe: it moves and relabels
drawing furniture, and touches nothing the relay executes.

## Decisions taken up front

| Question | Decision |
|---|---|
| Where it lives | **A new SEL tool**, `src/pacct/web/gle_tabs/`, mounted at `/gle-tabs/`. Not a screen inside the GLE Exporter, whose whole identity is the Excel round-trip and which is already 942 lines. |
| Operations | **Move and rename only.** No delete, no duplicate, no moving a page between `GL1` and `GL2`. Deleting a page destroys elements that own SELOGIC slots which stay allocated in the settings; moving between files collides element ids. Neither was asked for. |
| Reorder UI | **Drag-and-drop list with inline rename**, plus ↑/↓ buttons on every row. Drag is the accelerator; the buttons are the keyboard path, so there is one list rather than two UIs. |
| Batch scope | **Every relay and every GLE in one pass.** Edits accumulate in session state; one `Gerar RDB` applies all of them all-or-nothing, as `apply_xlsx_updates_to_rdb` already does. |
| Edit mechanism | **Byte splice with spans located by `expat`.** Not ElementTree, not regex — see below. |
| Name limit | **20 characters**, measured, enforced. |
| `description` / `revisiondate` | **Out of scope.** They are per-project metadata, identical across every page of a file. |
| Where the code lives | **`pac-ct`, not `sellib`.** See "The sellib question". |

## The edit mechanism

A page is the largest span in the file — `Capa` occupies bytes 657–16 989 of
`GL1.gle.xml`, and pages carry megabytes of RTF (`<rtf_text>`) and base64
(`image_stream`). A boundary placed one byte wrong produces a settings file that
is corrupt in a way no downstream check would see, because `rdb_write` verifies
the OLE **container**, never the XML inside a stream.

Three ways to do it were considered.

**Rejected — ElementTree round-trip.** `parse_gle` + reorder + `ET.tostring`
rewrites the whole file: attribute order, whitespace, self-closing style and the
encoding declaration all change. Byte fidelity of the RTF and the base64 is lost
for what is a cosmetic edit, and the diff becomes unreviewable.

**Rejected — regex over bytes**, the style
`gle_exporter.update_port_comments_in_gle_bytes` uses. It works for a small
`<comment>` inside a known element. For whole-page spans a `</page>` inside an
XML comment or a CDATA section would silently move the boundary, and the blast
radius is a whole page rather than one comment.

**Chosen — `expat` byte offsets.** `expat.ParserCreate(encoding="iso-8859-1")`
overrides the declaration, which is where the GLE lies: QuickSet writes
`encoding="utf-8"` over latin-1 bytes. Overriding at the parser means the offsets
are the **original file's** — `parse_gle`'s declaration swap changes the file's
length by 5 bytes and would shift every offset after the prolog. `CurrentByteIndex`
in `StartElementHandler` gives the byte of `<page`; in `EndElementHandler` it
gives the byte of `</page`, and scanning to the next `>` closes the span. Pages
never nest, so a depth counter is enough to ignore the `<page`-free interior.

The emit keeps the **separators in place**: `raw[:first.start]`, then for each
output position the chosen page's bytes followed by the separator that
originally *followed that position*, then `raw[last.end:]`. Indentation and any
comment sitting between two pages stay where they were rather than travelling
with a page.

### Measured evidence

A throwaway probe ran the mechanism over every `.gle` in the local corpus —
**215 files, 3 111 pages**:

| Check | Result |
|---|---|
| Identity rebuild (order unchanged) is byte-identical to the input | **215 / 215** |
| A random shuffle re-parses with pages in exactly the requested order | **215 / 215** |
| Shuffled output has the same length as the input | **215 / 215** |

The length result is worth stating on its own: **a pure reorder does not change
the stream's size.** `olefile.write_stream` only swaps a stream for one of
exactly the same size, so `rdb_write.write_streams` takes its **in-place** path
and everything outside the touched `.gle` streams stays byte-identical. No
`cfbwrite` rebuild, and none of the rebuild's legitimate-but-alarming shrinkage
(one corpus RDB measured 142.9 MB → 36.9 MB with no edits at all). A rename
changes the size only when the new name's escaped length differs from the old,
and then the rebuild path handles it exactly as the other tools' edits do.

## Architecture

```
src/pacct/web/gle_tabs/
    __init__.py             re-exports build_gle_tabs_handler
    model.py                pure bytes: spans, validation, splice, state
    handler.py              routes; subclasses SessionHandler
    templates/landing.html  the one screen
```

Named `gle_tabs` and not `gle_pages` because `pacct/web/glv/gle_pages.py`
already exists and means something else — reading a GLE's pages for the
viewer's tab strip and its per-page bit sets.

`paths.py` gains `GLE_TABS_TEMPLATES_DIR`. `dashboard.py` mounts it. All
filesystem paths come from `paths.py`, per the project rule.

### `model.py` — the whole of the file work, with no HTTP in it

```python
@dataclass(frozen=True)
class PageSpan:
    index: int          # position in the original file, 0-based
    name: str           # unescaped
    description: str
    elements: int       # <element> count, counted in the same expat pass
    start: int          # byte offset of "<page"
    end: int            # byte offset just past "</page>"
    name_start: int     # byte offsets of the name attribute's VALUE,
    name_end: int       # inside the start tag

class GleTabsError(ValueError): ...

def read_pages(raw: bytes) -> list[PageSpan]: ...
def apply_page_edits(raw: bytes, *, order: list[int],
                     names: dict[int, str]) -> tuple[bytes, dict]: ...
```

`read_pages` calls `reject_dtd_in_bytes` first, exactly as `sellib.gle.parse_gle`
does — a GLE arrives inside an RDB somebody uploaded and is no more trusted than
an SCD.

`apply_page_edits` renames first, then permutes, then **re-parses its own output**
and raises `GleTabsError`
unless the resulting count, names and order are exactly what was asked. Nothing
leaves the function unverified. `order` is in terms of original indices, so an
identity edit is `list(range(n))` with an empty `names` — and is byte-identical
by the table above.

Stats returned: `{"moved": n, "renamed": n}`, for the log line and the response.

**The rename escape is not `rdb_write.xml_text_escape`.** That function is
documented as escaping TEXT content — it handles `&`, `<` and `>` and leaves `"`
alone, which is correct between tags and wrong inside `name="..."`. A name
carrying a double quote would close the attribute and produce a malformed GLE
that goes into the output RDB and then into the project library, where nothing
distinguishes it from a good file — the same failure `xml_text_escape`'s own
docstring was written about. `model.py` therefore owns a four-character
attribute escape (`&`, `<`, `>`, `"`), applied before the latin-1 encode. The
corpus confirms names really are escaped: the one name containing markup
characters is stored as `U&gt;U&lt; I &gt;I&lt;`.

### Validation

Rejected at `POST /stage`, before anything is held in session state:

- `order` must be a permutation of `range(len(spans))` — not a subset, not a
  superset. A page cannot be dropped by omission.
- A name must be non-empty after stripping.
- A name must be **at most 20 characters** unescaped. Measured across the corpus:
  of 3 111 page names, none exceeds 20, 67 sit exactly at 20, and several are
  visibly truncated — `52- CMD DE FECHAMENT`, `L-R/Lib.Int/Mod.Test`. The single
  22-byte outlier is `U&gt;U&lt; I &gt;I&lt;`, which is 10 characters once
  unescaped, and is also what proves names are XML-escaped in the file.
- No control characters.
- A rename must not **create** a duplicate name within the GLE. Pre-existing
  duplicates are left alone: blocking them would reject files that already ship
  that way, and `glv/gle_pages.py:safe_page_id` documents what collisions cost
  downstream.

### State

`GleTabsState`, the `state_factory` on the handler:

```python
rdbs:  dict[str, RdbInfo]                          # cache, as in dnp_map
edits: dict[tuple[str, str, str], PageEdit]        # (rdb_key, relay, gle)
```

`PageEdit` holds `order` and `names`. An edit that resolves to a no-op — order
unchanged, no name changed — is dropped rather than staged, so `Gerar RDB` never
rewrites a stream it has nothing to say about.

### Routes

Absolute, as the mount convention requires; `self.mount_prefix` is written by
hand only in the `<a href download>`.

```
GET  /                                   landing
GET  /rdbs                               RDBs in the visitor's project
GET  /gles?rdb=                          relays -> GLE files -> page counts
GET  /pages?rdb=&relay=&gle=             the tabs, plus any staged edit
POST /stage                              validate and hold one GLE's edit
POST /reset                              drop one GLE's edit, or all of them
POST /gerar                              build the output RDB (job with stages)
GET  /download?f=                        restricted to self.sdir("out")
```

An RDB in the visitor's project library counts with no extra step, through
`SessionHandler.library_entry` — the same `_rdb(key)` helper `dnp_map` uses.
There is no upload route of this tool's own: files enter the project through the
Project Files tab, which is where every other tool now gets them.

`POST /gerar` is two passes and all-or-nothing. Pass one only reads: it resolves
every staged `(relay, gle)` to its OLE stream through
`rdb_write.resolve_gle_stream_path` and computes the new bytes, touching no
disk. Only if every one succeeds does pass two hand the whole dict to
`rdb_write.write_streams`, which writes atomically onto the destination. A
half-applied RDB is indistinguishable from a whole one once it leaves here.

Output is `<stem>_abas.rdb` via `with_suffix_before_ext`, published into the
project library with `publish_output(path, "Organizador de Abas GLE", job)`.

### Interface

One screen, `templates/landing.html`, carrying `<!--NAV:gle-tabs-->` so
`mount.py:_resolve_markup()` renders the right navigation per theme. No colour,
radius, font family or padding of its own — tokens only.

1. **RDB** — the project's RDBs, the picker the other tools use.
2. **Relé / GLE** — each relay with its GLE files and each file's page count. A
   file with a staged edit carries a `modificado` badge.
3. **Abas** — one row per tab: drag handle, position, name `<input>`, read-only
   `description`, element count. Dragging reorders; the ↑/↓ buttons on each row
   do the same from the keyboard. The name input enforces `maxlength="20"` and
   shows the count as it fills. A row whose name changed is marked; so is a row
   that moved.
4. **Gerar RDB** — enabled once at least one GLE has a staged edit; the header
   says how many. Runs as a progress job, then offers the download and says the
   file is in the project.

Portuguese for everything the user reads, accents included; English for
identifiers, comments and docstrings.

## The sellib question

`CLAUDE.md` says SEL file-format work lives in `sellib`. This puts the splice in
`pac-ct` instead, deliberately, for three reasons.

- **Precedent.** GLE *mutation* already lives in `pac-ct`:
  `gle_exporter.update_port_comments_in_gle_bytes` and the VB Updater's comment
  injection are both byte-level GLE edits in the tool package. `sellib` reads
  GLEs and renders them; it has no writer at all. This tool would be the first,
  and a first writer is a `sellib` design decision worth taking on its own
  evidence rather than as a side effect of a tab reorderer.
- **Release cost.** A `pac-ct` change that uses a new `sellib` feature cannot be
  pushed until that `sellib` version is on PyPI, because CI installs the pin, not
  the sibling working tree. Putting this in `sellib` turns a one-repo change into
  a release dance for forty lines.
- **It is reversible.** `model.py` is pure `bytes -> bytes` with no imports from
  `pacct` beyond `xml_text_escape`. If a second tool ever needs it, it lifts into
  `sellib` unchanged.

## Verification

**Automated**, in `pac-ct/tests/`:

- `test_gle_tabs_model.py` — the pure model. Against the existing fixtures plus a
  new adversarial one, `tests/fixtures/tabs_hostile.gle.xml`, holding a page
  whose text contains a literal escaped `&lt;/page&gt;`, an XML comment sitting
  between two pages, an accented name, a name already duplicated, a name already
  carrying escaped markup (`U&gt;U&lt;`), and a name at exactly 20 characters.
  Properties asserted:
  - an identity edit returns bytes identical to the input;
  - a permutation preserves length and re-parses in the requested order;
  - bytes before `<pages>` and after `</pages>` are never touched;
  - a rename changes only the name attribute's value — every other byte of that
    page is unchanged;
  - renaming to `A "B" & <C>` produces a file that still parses, and reads back
    as exactly `A "B" & <C>`;
  - renaming to a name it already has is a no-op and is not staged;
  - every validation rule rejects, with the page left unstaged.
- `test_web_routes_gle_tabs.py` — the routes, through `tests/web_harness.py`.
- The existing menu tests must keep passing with the new `items.py` entry.

`ruff check .` and `mypy` clean, per the project rule that no package is
silenced.

**Manual**, because the harness covers neither the dispatcher nor anything
visual: `python3 app.py --web`, exercise the tool in the browser **in all three
themes**, including the drag and the keyboard path.

**The one check only the user can run:** open a generated RDB in AcSELerator
QuickSet and confirm the tabs appear moved and renamed. Everything here says
QuickSet reads tab order from XML child order — there is no index attribute, and
page names appear in no other stream — but that is inference from the file
format, not from the application. The tool should not be trusted on a real
project until that has been done once.

## Risks, stated

- **The 20-character limit is measured, not documented.** 3 111 names, none
  longer, several truncated at exactly 20. Strong, and still inference. The
  validation message should say it is measured rather than imply SEL documents it.
- **Tab order = XML order is inference.** See above. It is the tool's whole
  premise, and it is the thing to confirm first.
- Nothing here is relay-dependent: no telnet, no MMS, no Fast Meter. No bench
  relay is needed.

## Out of scope

- Deleting, duplicating or creating pages.
- Moving a page between `GL1` and `GL2` of the same relay, or between relays.
- Editing `description`, `revisiondate`, zoom or scroll attributes.
- Reordering anything inside a page — elements, connections, groups.
- Any change to the settings streams. This tool writes `.gle` streams and
  nothing else.
