# PAC CT — the rename, and a menu split by vendor

*2026-09-01 — design*

## Problem

The toolkit was born for SEL relays and the name says so: `SEL Commissioning
Toolkit` in the README, `Comissionamento SEL` in the home's `<h1>`, `sellib` for
the package, `Sel_comissioning` for the directory.

It stopped being only that. The **VLAN Mapper** eats an SCD, which is IEC 61850,
and serves a GE or a Siemens relay exactly as it serves a SEL — and more is
coming, with EnerVista and DIGSI settings on the horizon.

Two consequences, both of them about the menu:

1. **The name lies.** An app called "Comissionamento SEL" that opens the SCD of
   a Siemens IED introduces itself wrong on the first line of the first screen.
2. **The menu doesn't say what serves whom.** The nine tools sit in one flat
   1..9 list. Nothing on the home tells a visitor that eight of them require an
   RDB from AcSELerator QuickSet and one requires nothing from any vendor.

## What was measured

Today's catalogue, classified by what each tool **eats** — which is what decides
whether it is locked to a vendor:

| Tool | Input | Vendor-locked? |
|---|---|---|
| Visualizador de Lógica | RDB + telnet/MMS | **SEL** — the drawing is QuickSet's GLE |
| Comparador de Ajustes | RDB | **SEL** |
| VB Updater | RDB + SCD | **SEL** — needs the GLE |
| Exportador de Comentários GLE | RDB + XLSX | **SEL** |
| Editor de Mapa DNP | RDB | **SEL** |
| Comparador RDB ↔ SCD *(planned)* | RDB + SCD | **SEL** |
| Validador de Ajustes *(planned)* | RDB | **SEL** |
| VLAN Mapper | SCD | **none** — IEC 61850 |
| Relatório de Comissionamento *(planned)* | — | **none** |

**7 SEL, 2 vendor-neutral, 0 GE, 0 Siemens.** Two of the four requested sections
are born empty, and that is the design decision the rest of this document
settles.

The rename's blast radius, counted:

* `sellib` — **974 occurrences across 162 files**.
* The visible brand (`Comissionamento SEL`, `SEL Commissioning Toolkit`,
  `SEL Toolkit`) — **55 occurrences** outside `mockups/`.
* `PROJECT_ROOT = Path(__file__).resolve().parent.parent` (`sellib/paths.py`) is
  derived, so **renaming the directory costs the code nothing**.
* `selprotopy/` is vendored and hook-protected: its name is not ours and does
  not change.

## Decision 1 — the name

**PAC CT** — *Protection, Automation & Control Commissioning Toolkit*.

"PAC" is the umbrella the industry itself uses (IEEE and CIGRÉ say PACS), and it
is vendor-neutral by construction: SEL, GE and Siemens are all PAC. The
expansion of the acronym is, literally, the argument for the rename.

Spelling: **Commissioning**, with two "m"s. The current directory
(`Sel_comissioning`) carries the Portuguese spelling by accident and it is not
worth propagating.

On screen — the brand in English, the tagline in Portuguese, which is the
convention this project already follows:

```
PAC CT
Comissionamento de proteção, automação e controle
```

## Decision 2 — the menu: a group is a section

Three forms were drawn (the mockups are in `mockups/`):

1. **Group as section** — headings on the home, separators in the navigation.
2. **Group as filter** — the flat list plus a row that narrows it.
3. **Group as the home** — four large cards, tools one level down.

**Form 1 wins.** Form 2 was rejected because it *hides* the multi-vendor story
until someone clicks, which is the opposite of what the menu now has to say, and
because a filter is state (JS or `?grupo=`) where Form 1 is only markup. Form 3
is kept for later: it is where Form 1 migrates the day GE and Siemens have real
tools, and migrating is cheap because the underlying data is the same — today it
puts a click between the commissioning engineer and the six tools they use every
day.

**Order: vendor-neutral first** (geral → SEL → GE → Siemens), general to
specific. The cost is known and accepted: the Visualizador de Lógica stops being
tool 1 and becomes tool 3.

**The empty sections ship**, each with one line saying what will land there.
Empty with a roadmap is a forecast; empty and silent is vapour.

## Design

### The data — `sellib/web/themes/items.py`

A frozen dataclass `Grupo` enters:

| field | serves | example |
|---|---|---|
| `key` | `Tool.grupo` and the tests | `"sel"` |
| `nome` | folha, régua | `"SEL"` |
| `curto` | caderno (divider tabs) | `"SEL"` |
| `come` | the "what this group reads" line | `"RDB do AcSELerator QuickSet"` |
| `vazio` | the text of a group with no tools | `"Nenhuma ferramenta ainda. …"` |

The field values stay in Portuguese: they are user-facing strings, and the
convention holds. Then the `GROUPS` list, in this order:

```python
GROUPS = [
    Grupo("geral",   "Independentes de fabricante", "Geral",
          "SCD (IEC 61850) — serve qualquer relé", ""),
    Grupo("sel",     "SEL", "SEL",
          "RDB do AcSELerator QuickSet", ""),
    Grupo("ge",      "GE", "GE",
          "EnerVista — ajustes .urs",
          "Nenhuma ferramenta ainda. É aqui que a leitura de ajustes do "
          "EnerVista entra."),
    Grupo("siemens", "Siemens", "Siemens", "DIGSI",
          "Nenhuma ferramenta ainda."),
]
```

`Tool` gains a **`grupo` field, required and positional right after `key`**. No
default, for the same reason `fast_read` has none: a silent default drops a new
tool into the wrong group and nobody notices.

**`TOOLS` is physically reordered** into group order. This is not cosmetic: the
1..9 ordinal comes from `enumerate(TOOLS, 1)` in three independent renderers,
and it is what makes régua's strip and régua's cards agree ("Borne 3" on both
sides). Sorting the list keeps *one* source for the number. A test pins the
ordering, otherwise the next SEL tool appended at the end renumbers the whole
home in silence.

And the number itself stops being recomputed per renderer: `items.ORDINAL` — one
dict, `{tool.key: 1..9}`, built from the sorted `TOOLS` — is what all six
renderers print. Six `enumerate()` calls over a list that four of them also
filter is exactly how the strip and the cards would drift apart.

Resulting numbering:

```
Geral    1 VLAN Mapper              2 Relatório de Comissionamento
SEL      3 Visualizador de Lógica   4 Comparador de Ajustes
         5 VB Updater               6 Exportador de Comentários GLE
         7 Editor de Mapa DNP       8 Comparador RDB ↔ SCD
         9 Validador de Ajustes
GE       — (reserved)
Siemens  — (reserved)
```

`MENU_ITEM` and `FILES_ITEM` belong to no group: they stay navigation rather
than tools, and keep their `0` and `A`.

### The six renderers

The three directions share no markup, only tokens — so each resolves a group
with the metaphor it already owns:

| | `nav()` | `home()` | empty group |
|---|---|---|---|
| **folha** | the group's name as a `.grp` label before each run in the `.toc` | one numbered `<h2>` section per group; the `Ref.` column becomes `2.3` | dashed box holding the `vazio` line |
| **caderno** | `.tabsep` carrying the short label between tab runs | `.grp` (name + `come` + count) above each `.cards` | `.blank` — "folha em branco" |
| **régua** | one `.cap` per group (`Régua X1 — geral`), plus **`X0 — entrada`** for terminal A | `.grp` with a brass rule | one `.borne.off` "reserva" and a "sem fio" card |

The mockup drew folha's separator as a bare `|`. It is the group's name
instead: a datasheet's table of contents lists its section headings, and a bare
pipe would have made "every group appears in all three `nav()`" — the invariant
under Verification — false in one of the three.

**`X0 — entrada`** surfaced while drawing the mockup: today terminal A (Arquivos
do Projeto) floats loose at the top of the strip; the moment the groups get
captions, an uncaptioned A starts reading as part of the first group.

In folha the `Ref.` column becomes `<group>.<position>` instead of `1.<n>`. That
is its dialect — a datasheet numbers by section — and it does not conflict with
the global ordinal, which is what régua and caderno keep printing. The data
offers both; each direction picks, which is already the rule there.

New CSS goes into each direction's `DELTA_CSS` and **uses tokens only**: no
literal colour, radius, font or padding. (The mockup's literals exist because it
is a standalone file, outside `/theme.css`.)

### The copy

The three home `lead` sentences start telling the split, not just "6 no ar e 3
por fazer" — the new information is that two tools already serve any relay and
seven require an RDB. They stay in Portuguese, accented.

## The rename, in order

Order matters: a 974-occurrence `sed` does not coexist with pending work.

1. **Clean tree.** The GLV connectors work (`sellib/web/glv/connectors.py`,
   `tests/test_glv_connectors.py` and ~30 modified files) is committed or
   stashed first. A mechanical rename on top of uncommitted change is a
   guaranteed conflict and, in practice, unrecoverable.
2. **Visible brand** — `web/dashboard.py` (`<title>`, `<h1>`, `.sub`), `app.py`,
   `README.md`, `docs/ENGINEERING-NOTES.md`.
3. **`sellib` → `pacct`** — `git mv` plus a `sed` of `\bsellib\b` across
   `.py`/`.md`/`.html`/`.json`, excluding `.venv/` and `selprotopy/`. The suite
   passing **is** the verification: every broken import shows up in it.
4. **The directory** — `Sel_comissioning/` → `pac-ct/`, last. `.venv` is rebuilt
   by `app.py` on the next boot: its absolute paths break with the move.

### Documentation goes to English

`README.md` and `docs/ENGINEERING-NOTES.md` are rewritten in English as part of step 2 — the
documentation language changes with the rename. The **user-facing strings do
not**: HTML templates, tool names and error messages stay in Portuguese,
accented, which is the convention `docs/ENGINEERING-NOTES.md` states and which this design
follows in its own `Grupo` values.

**Not rewritten**: `corrections_plan.md`, the earlier specs under
`docs/superpowers/specs/`, and the ten `mockups/`. They record what was done
when the app was called something else; falsifying them is worse than dating
them. `docs/ENGINEERING-NOTES.md` gains a line saying so.

## Verification

`tests/test_theme_nav.py` grows, and starts pinning:

* every `Tool.grupo` is a `key` in `GROUPS`;
* `TOOLS` is sorted by `GROUPS` order;
* every group appears in all three `nav()` and all three `home()`;
* a group with no tools prints its `vazio` line in all three directions;
* a tool's ordinal is **the same number** in régua's strip and in régua's card —
  the docs/ENGINEERING-NOTES.md gotcha that is only a comment today.

`test_the_first_tool_is_still_number_one` breaks on purpose under the new order:
it is rewritten to assert today's numbering (`VLAN Mapper` is 1, `GLV` is 3),
keeping the docstring that explains *why* the number is a contract.

The screens have no unit tests — the final check is launching
`python3 app.py --web` and looking at the home and the navigation in all three
directions, including the two empty sections.
