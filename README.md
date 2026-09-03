# PAC CT — Protection, Automation & Control Commissioning Toolkit

Web toolkit for commissioning protection, automation and control systems. The
IEC 61850 tools serve any vendor; the ones that read the AcSELerator QuickSet
RDB serve SEL relays (SEL-311C, SEL-311L, SEL-411L, SEL-451, SEL-487E,
SEL-751, SEL-787). A single local HTTP server serves ALL of the tools at the
same time, each one under a path prefix -- you can leave the GLV in one tab and
the Editor de Mapa DNP in another without bringing anything down.

## Tools

Six shipping, three planned. The menu numbers them 1 to 9 and keeps the order,
grouped by what each tool eats: whoever reads SCD (IEC 61850) serves any
vendor, whoever reads the QuickSet RDB serves SEL only. GE and Siemens are
declared and still empty on purpose -- the tool names are what the screen
says, and the screen says them in Portuguese.

### Vendor-neutral -- SCD (IEC 61850), serves any relay

| # | Tool | Input | What it does |
|---|------|-------|--------------|
| 1 | **VLAN Mapper**<br>`/vlan-mapper/` | SCD | Per relay, the GOOSE VLANs (subscribed and published) that have to be open on the switch port. Hex/Dec and CSV export. |
| 2 | Relatório de Comissionamento | — | *Coming soon.* Checklist of the functional tests per bay, with photos and oscillography. |

### SEL -- AcSELerator QuickSet RDB

| # | Tool | Input | What it does |
|---|------|-------|--------------|
| 3 | **Visualizador de Lógica (GLV)**<br>`/glv/` | RDB + telnet | Live relay state drawn over the AcSELerator QuickSet GLE diagram (Fast Meter + Relay Word / TARGET). Several diagrams and several relays at the same time. |
| 4 | **Comparador de Ajustes**<br>`/settings-compare/` | RDB | Up to 7 relays of the same family (3xx / 4xx / 7xx) side by side, per settings group, with SELOGIC logic equivalence (not just a text diff). |
| 5 | **VB Updater**<br>`/vb-updater/` | RDB + SCD | Cross-matches Virtual Bit descriptions between the RDB's GLE (the port comment of `SYMBOL VBxxx`) and the SCD's `ExtRef` `desc`. Applies SCD -> GLE or GLE -> SCD, one at a time or in a batch. |
| 6 | **Exportador de Comentários GLE**<br>`/gle-exporter/` | RDB + XLSX | Port comments in Excel, one sheet per GLE. Edit and re-import to generate an updated RDB. |
| 7 | **Editor de Mapa DNP**<br>`/dnp-map/` | RDB | Edits each RDB relay's DNP3 points (`SET_D*.TXT`) in a table and re-exports the RDB. Warns (never blocks) about a name the relay does not know. |
| 8 | Comparador RDB ↔ SCD | RDB + SCD | *Coming soon.* |
| 9 | Validador de Ajustes | RDB | *Coming soon.* |

### GE -- EnerVista, `.urs` settings

No tool yet. This is where reading EnerVista settings lands.

### Siemens -- DIGSI

No tool yet.

Besides them, **Arquivos do Projeto** (`/files/`) -- which is not a tool,
it is the way in: it is the ONLY screen that accepts an upload. See "How it
works" below.

## Project layout

```
pac-ct/
+-- app.py                        <-- launcher (run this one; bootstraps the venv)
+-- requirements.txt              (runtime)  requirements-dev.txt (pytest)
+-- config/
|   +-- config.ini.example        (versioned model)
|   +-- config.ini                (IP, passwords -- local, OUTSIDE git; see "Configuration")
+-- pacct/                       (all first-party code)
|   +-- paths.py                  (path constants -- ALWAYS take them from here)
|   +-- core/
|   |   +-- relay_models.py       (model registry: blocks, analogs, identifiers)
|   |   +-- target_region.py      (TARGET region over ASCII -- 4xx family)
|   |   +-- selogic_parser.py     (SELOGIC expression parser)
|   |   +-- logic_compare.py      (logic equivalence: EQUAL / EQUIVALENT / DIFFERENT)
|   |   +-- settings_model.py     (normalised settings model)
|   |   +-- wordbits.py           (valid Relay Word names, per model)
|   +-- parsers/
|   |   +-- gle.py                (GLE -> SVG: element, port and connection layout)
|   |   +-- rdb.py                (OLE extractor for the RDB + cache by content)
|   |   +-- scd.py                (IEC 61850: IEDs, GSE, GOOSE subscriptions)
|   |   +-- set_dnp.py            (SET_D; contract: parse(b).serialize() == b)
|   +-- matchers/relay_scd.py     (RDB <-> SCD cross-match by IP / RID)
|   +-- cli/runner.py             (CLI mode: polling in the terminal)
|   +-- web/
|       +-- dashboard.py          (home + main(): mounts the tools)
|       +-- mount.py              (ONE server, routing by prefix)
|       +-- session.py            (session per visitor, cookie `selsid`)
|       +-- rdb_write.py          (the only place that writes bytes into an RDB)
|       +-- progress.py           (progress bar, state on the server)
|       +-- themes/               (three visual directions; tokens in tokens.py)
|       +-- project_files/        (Arquivos do Projeto -- the only screen with upload)
|       +-- glv/                  (Visualizador de Lógica: N diagrams, N relays)
|       +-- dnp_map/              (Editor de Mapa DNP)
|       +-- vb_updater/  vlan_mapper/  gle_exporter/  settings_compare/
+-- data/
|   +-- relay_models/             (profile per model: GLE + Fast Meter)
|   +-- wordbits/                 (valid names per model, for the Editor de Mapa DNP)
+-- tests/                        (pytest; see "Verification")
+-- cache/                        (generated at runtime, gitignored -- see "Cache")
+-- samples/                      (sample GLE / RDB / SCD)
+-- docs/                         (SEL manuals, DNP3 profiles, licences)
+-- selprotopy/                   (patched MIT library -- Joe Stanley; do NOT edit)
```

## Prerequisites

- Python 3.10+ (tested on 3.12)
- TCP access to the SEL relay (port 23 / Telnet) -- for the GLV only
- Linux/macOS/WSL (native Windows should work too)

## Configuration

What the app reads is `config/config.ini`, and that file **does not go into
git** (it is in `.gitignore`). It is where the relay's real IP and ACC/2AC
passwords are typed, and a substation password committed to a repository is in
the history forever.

What the repository versions is the model, `config/config.ini.example`, with a
documentation IP (`192.0.2.10`) and the SEL factory passwords (`OTTER` /
`TAIL`) -- which are meant to be replaced by the ones from the job.

```bash
cp config/config.ini.example config/config.ini
nano config/config.ini      # relay IP, passwords, default GLE
```

If you forget, no problem: on the first run the app makes that copy by itself
and says so in the log. It only stops if neither `config.ini` nor the
`.example` exists -- and then it says what is missing instead of coming up with
the fallback values.

## How to run

```bash
# 1. (Optional, GLV only) Edit the IP, passwords, etc. in config/config.ini
#    (copied from the .example on the first run -- see "Configuration" above)
nano config/config.ini

# 2. Start the toolkit
python3 app.py --web

# 3. Open http://localhost:8765/ in the browser
#    -> menu with the nine tools, ALL up at the same time
```

## How it works

Three things you cannot guess by reading a single file.

**One server, several tools, routed by prefix.** `pacct/web/mount.py` brings up
ONE `ThreadingHTTPServer` on 8765 and dispatches by path: `/glv/`,
`/vb-updater/`, `/dnp-map/` and so on. Each tool writes its own routes as if it
owned the root (`/state`, `/download`); the dispatcher strips the prefix before
delegating and injects into the HTML a shim that rewrites the `fetch` calls.
That is why only one port needs to be opened in the firewall, and why two tools
can be used in two tabs at the same time.

**Every visitor has state of their own.** A `selsid` cookie gives each person
their own per-tool state and their own directory under `cache/sessions/<sid>/`,
swept after 8 h of idleness (`[web] session_ttl_hours`). This matters in a
substation: relay names repeat between jobs, and without isolation one person's
upload replaced the other's with no warning.

**A file comes in through one place only.** `/files/` (Arquivos do Projeto)
is the ONLY screen with an upload area. The tools do not take an upload -- they
show a picker over the library and send a sha256. And what a tool GENERATES
goes back into the same library, so an SCD corrected in the VB Updater already
shows up in the VLAN Mapper without going through a download and an upload
again (that was 140 MB across the substation's network to move a file between
two tabs of the same server).

The RDB is kept by CONTENT in `cache/rdb/<sha256>/`, shared between visitors and
across restarts: two uploads of the same file are the same extraction. The SCD
stays in the session of whoever uploaded it.

### Per-tool flows

They all start the same way: **upload the RDB (and the SCD, if the tool uses
one) in Arquivos do Projeto**, once. From then on each tool only picks from the
library.

- **GLV**: `/glv/novo` -> pick RDB, relay and GLE -> the diagram opens
  DISCONNECTED, with the IP read from the relay itself (`IPADDR` of
  `SET_P5.TXT`), and you click Conectar. You can open several diagrams, of
  different relays; two diagrams on the same `ip:porta` share a single telnet
  session. To view a loose GLE with no relay at all:
  `python3 app.py --web --gle arquivo.xml`.

- **VB Updater**: pick RDB and SCD -> the screen shows the cross-match by
  IP/RID -> pick each relay's GLE -> the comparison table (VBxxx | GLE comment |
  SCD desc) opens with the apply buttons, one at a time or in a batch. A VB
  declared as an `ExtRef` with no `desc` becomes `reserva` in the GLE. The batch
  is all-or-nothing: if one selection fails, no RDB is written and the screen
  says which one.

- **VLAN Mapper**: pick SCD -> table of IED | type | IP | VLAN chips (RX/TX/
  both) | count | unresolved. Hex/Dec, Chips/Text and "Copiar CSV".

- **Exportador de Comentários GLE**: pick RDB -> tick one GLE per relay ->
  "Exportar Excel" generates an xlsx with one sheet per (relay, GLE) and one row
  per port of each `SYMBOL`/`PLT`/`ALT`/`PCNDTIMER`/`PCN`/`AST`/`PSV`/`LATCH`/
  `TIMER`/`COUNTER`. Edit the `Comment` column and re-import. **A comment longer
  than the original works**: when the stream changes size the whole RDB is
  rebuilt (by `cfbwrite`, a separate library), which verifies its own output before
  handing it over. The rebuilt file comes out much smaller than the original --
  that is expected, QuickSet never compacts.

- **Editor de Mapa DNP**: pick RDB -> pick relay and DNP session -> edit the
  points in the table -> export. The edits are a diff per session, never a
  rewritten document. "Importar perfil DNP" teaches the editor the valid names
  of a model it does not know yet.

### Launcher options

```bash
python3 app.py --web                  # web toolkit (recommended)
python3 app.py                        # GLV CLI in the terminal (polling only)
python3 app.py --web --port 9000      # custom port
python3 app.py --config outro.ini     # another configuration file
python3 app.py --skip-install         # do not re-check dependencies
python3 app.py --no-venv              # without a virtualenv (--break-system-packages)
```

### Importing as a package

With the cwd at the project's root directory:

```python
from selfiles.rdb import process_upload
from selfiles.scl.read import (
    load_scd, extract_gse_communication_map, extract_goose_subscriptions_by_ied,
)
from selfiles.match import compare_rdb_to_scd
from pacct.core import relay_models
from pacct.web.vb_updater import (
    extract_vb_instances_from_gle, extract_vb_descriptions_from_scd_ied,
)
from pacct.web.vlan_mapper import compute_ied_vlan_rows
from pacct.web.gle_exporter import (
    extract_port_instances_from_gle, build_xlsx_for_selections,
    parse_xlsx_to_updates, apply_xlsx_updates_to_rdb,
)
```

Or running modules directly:

```bash
python3 -m pacct.web.dashboard --config config/config.ini
python3 -m pacct.cli.runner --config config/config.ini
```

## Inside the GLV

1. **Connects** over Telnet to the relay (TCP 23)
2. **Autoconfigures** Fast Meter (discovers the available analog channels)
3. **Maps the bits** of the Relay Word:
   - Loads the cache if it exists (`cache/<FID>.json`)
   - Otherwise, sweeps `TAR 0..500` (~7 min the first time) -- TARGET goes up to
     500 rows on the SEL-411L (manual p. 10.5: `char[~488]`; MAP confirms
     3004h..31f7h)
   - GLE bits it does not find go into "not_findable" so they are skipped next
     time
4. **Polls** every 0.5 s, pipelined:
   - `\xa5\xd1` Fast Meter (analogs and, on the 7xx, digitals too)
   - `VIEW 1:TARGET` (500 bytes of the Relay Word) -- on the 4xx only
5. **Dashboard** shows each GLE page as an SVG with:
   - Green symbols = active (=1)
   - Grey = inactive (=0)
   - Dashed yellow = indeterminate (in the GLE but not in TARGET)
   - Animated lines with signal propagation through gate evaluation
   - PLT/TIMER/AST show their real names (PLT04, PCT01, AST01, _LT01, _SV01)
     and use the relay's real output bit

### Supported relay families

The model's JSON declares `fast_read`:
- `target_region` (4xx -- SEL-411L, SEL-487E): the Relay Word comes from TARGET
  over ASCII; A5D1 brings only a subset.
- `fast_meter_digitals` (7xx -- SEL-751, SEL-787): the whole Relay Word comes
  embedded in the A5D1 response (`numdigitalbank`/`digitaloffset`); the
  dashboard consumes `fm_data['digitals']` directly, without TARGET.

## Cache

Everything under `cache/` is generated at runtime and is in `.gitignore`. Three
different things live there, with different lifetime rules:

| Path | What it is | When it goes away |
|---|---|---|
| `cache/<FID>.json` | Relay Word bit discovery, per firmware FID | When the FID changes (firmware upgrade) -- it is ignored on its own |
| `cache/rdb/<sha256>/` | RDB extraction, keyed by CONTENT | Swept by age and size (`rdb_cache_max_age_days`, `rdb_cache_max_gb`). **Survives the restart on purpose** |
| `cache/sessions/<sid>/` | Uploads and outputs of ONE visitor | On session expiry, and the whole directory is wiped at boot |

The FID cache is what takes the first connection from ~3 min down to ~2 s:
without it, mapping name -> (row, bit) costs one round trip per bit. To force
rediscovery:

```bash
rm cache/*.json          # only the bit discovery; does not touch the extracted RDBs
```

The RDB one is shared between visitors because it is keyed by the sha256: two
uploads of the same file -- from different people, or after a restart -- reuse
the same extraction.

## Verification

```bash
.venv/bin/python -m pip install -r requirements-dev.txt   # once per machine
.venv/bin/python -m pytest tests/ -q
```

783 tests, all passing, ~13 s. `app.py` only bootstraps `requirements.txt`, so
pytest is the one `pip` you run by hand.

The suite covers the parsers (GLE -> SVG with a golden file, SCD, RDB
extraction, `SET_D`, OLE rebuild), the RDB <-> SCD cross-match, the SELOGIC
parser and comparator, the TARGET region, RDB writing and the model of the
Editor de Mapa DNP.

What it does **not** cover, and why:

- **The web screens have no unit test.** Verify by bringing up
  `python3 app.py --web` and exercising the tool in the browser -- in the three
  themes, if you touched markup.
- **Nothing that needs a relay is tested, nor simulated.** The GLV polling and
  Fast Meter speak telnet to a physical device; a simulated relay that agrees
  with its own assumptions proves nothing. If your change touches that path, say
  so explicitly -- it needs a bench.

The GLE renderer's golden file regenerates with `SEL_UPDATE_GOLDEN=1 pytest
tests/test_gle_render.py` -- **read the diff** before committing.

## Adding support for a new relay model

Create `data/relay_models/<MODEL>.json` following the template of the existing
files (`SEL-411L.json`, `SEL-487E.json`, `SEL-751.json`). The JSON declares:
- `model` / `model_aliases` -- how to match the RELAYTYPE of Cfg.txt
- `ip_address` -- SET_*.TXT file + key (e.g. `IPADDR`) that carries the IP
- `relay_id` / `mac_address` -- (optional) other identifiers for the
  cross-match with SCD
- `fast_read` -- `target_region` (4xx) or `fast_meter_digitals` (7xx)
- `blocks` -- catalogue of GLE blocks (PLT, LATCH, TIMER, ...) with kind,
  `output_bit_pattern` (template of the derived bit), port sublabels, etc.
- `analog_symbols` / `analog_name_aliases` -- families of analog channels
  (AMV, MV, MAG, ...) and GLE -> Fast Meter aliasing rules (e.g. IAS -> IA1)

The loader in `selfiles.models.relay_models` discovers the JSON automatically.

## Known limitations

- `AMVxxx` bits (math variables) and analog channels (`IAW`, `VAY`, etc.) on the
  4xx **do not appear as a digital bit** because they live in the ANALOGS region
  (7000h+), not in TARGET. They are treated as analogs and shown as blocks with
  an inline value (Fast Meter).

  Note: `VBxxx` (Virtual Bits) and `TLED_17..24` ARE in TARGET (at
  310ch..312bh and 3148h respectively).

- PLT evaluation in the JS has no persistence between polls -- it uses the
  relay's real bit via `data-output-bit`, so the correct state comes from the
  Relay Word, not from the JS.

- PCNDTIMER ignores pickup/dropout in the JS -- it propagates `in` straight
  through. The REAL value comes from the relay's `PCTxxQ` bit.

- **It is still to be confirmed with AcSELerator QuickSet** that it accepts an
  RDB rebuilt by the Exportador de Comentários GLE and by the VB Updater when
  the new comment changes the stream's size. The Editor de Mapa DNP has used the
  same writer (now `cfbwrite`) since August with no trouble, but that is evidence for the
  `SET_D` stream, not for the `.gle`. Before taking it to the field, export one
  and open it in QuickSet.

- The VB Updater **cannot fill an empty port comment**. Its regex matches
  `<comment>TEXTO</comment>` and not the `<comment />` form QuickSet writes for
  an empty comment, so that VB's description is discarded and counted as
  `skipped` -- it does not show up in the screen's "N aplicadas". The Exportador
  de Comentários GLE, with its own writer, DOES FILL IT. See
  `tests/test_gle_bytes.py`.

## Licences

- `selprotopy/` -- MIT (Joe Stanley) -- see `docs/LICENSE-selprotopy`
- This toolkit -- free use
