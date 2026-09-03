# GLV over MMS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Graphical Logic Viewer a second transport — IEC 61850 MMS on TCP 102 — selectable per diagram, so a relay can be watched without spending its scarce telnet session, and so the 7xx GOOSE pages that Fast Message can never show become visible.

**Architecture:** Extract a `Transport` seam from `RelayLink`, moving the telnet half out **verbatim** into `TelnetTransport` and leaving the lifecycle shell (refcount, watchdog, `LiveState`, poll thread) byte-identical. Add `MmsTransport` beside it. Both write into the same `LiveState`, so notes, highlights, the tab strip, `/values` and the SVG bit classes are untouched. The bit → MMS-item map comes from a file — the project SCD first, a shipped ICD table as fallback — because the name lives in SCL `sAddr`, which relays do not serve over MMS.

**Tech Stack:** Python 3.10+ (tested 3.12), `py61850` 0.2.0.dev0 (pure stdlib MMS client), `pytest`, stdlib `xml.etree`.

**Spec:** `docs/superpowers/specs/2026-08-29-glv-mms-polling-design.md`

## Global Constraints

- **Language.** User-facing strings (HTML, error messages) in **Portuguese, accented**. Code identifiers, comments and docstrings in **English** for new code. When editing an existing file, match that file's language.
- **Paths.** Always use `sellib/paths.py` constants. Never hardcode a path, never `Path(__file__).parent` in a feature module.
- **`selprotopy/` is vendored and hook-protected.** Do not edit it. If a fix belongs there, surface it.
- **py61850 is a dependency, not a place to work around.** Four known gaps are filed as GuilhermeMarini/py61850#1. Do not reimplement them here; in particular **do not build a multi-variable Read** — it is blocked behind the COTP fragmentation bug.
- **Test command:** `.venv/bin/python -m pytest tests/ -q`. Baseline at plan time: **531 passed**. Never merge a task that reduces this.
- **Do not run `pip` by hand** except `.venv/bin/python -m pip install -r requirements-dev.txt`.
- **Poll period hard floor: 100 ms.** Effective period is `max(requested, measured_cycle)`.
- **Measured constants that must not be changed without a relay in front of you:** single-read RTT 3.1 ms median; per-`LN$FC` grouping beats per-DA 30 req/~180 ms vs 170 req/739 ms; pipelining is 3× slower; `TCP_NODELAY` makes no difference.

## Bench dependency — read before starting

Three tasks need a live relay. **At plan time the bench was unreachable** (all of 203.0.113.22/.23/.31/.41/.61, both ports 23 and 102). Tasks 1–3, 7 and 8 are fully offline and can proceed now. Tasks 4, 5 and 6 are gated on the bench returning.

Bench inventory, from when it was up:

| IP | Model | `fast_read` |
|---|---|---|
| 203.0.113.22 / .24 | SEL-411L-A | `target_region` |
| 203.0.113.23 / .25 | SEL-311C-1 | `tar_digitals` |
| 203.0.113.41 | SEL-751 | `fast_meter_digitals` |
| 203.0.113.61 | SEL-451-5 R331 | `target_region` |

---

### Task 1: Extract the Transport seam, telnet moved verbatim

The riskiest task in the plan, and the one that touches working, hardware-verified code. Risk is proportional to logic **changed**, not code **moved** — so nothing in the telnet bodies may be edited.

**Files:**
- Create: `sellib/web/glv/transport/__init__.py`
- Create: `sellib/web/glv/transport/telnet.py`
- Modify: `sellib/web/glv/link.py` (RelayLink loses its telnet half)
- Test: `tests/test_glv_transport.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Transport` protocol with `mode: str`, `fid: str`, `devid: str`, `connect(job) -> None`, `abort() -> None`, `close() -> None`, `prepare_bits(names, job) -> int`, `poll(state, interval, stop, once) -> None`, `coverage() -> MmsCoverage | None`. Also `TelnetTransport(ip, port, acc_password, relay_model, logger)` and `pick_transport(scan_mode, *, ip, port, acc_password, relay_model, logger) -> Transport`.

- [ ] **Step 1: Write the failing test for the seam**

`tests/test_glv_transport.py`:

```python
"""The Transport seam: RelayLink drives a transport it does not know."""
from __future__ import annotations

import logging
import threading

import pytest

from sellib.web.glv.link import RelayLink
from sellib.web.glv.state import LiveState


class FakeTransport:
    """Records the order RelayLink calls it in."""
    mode = "fake"

    def __init__(self):
        self.fid = "FAKE-FID"
        self.devid = "FAKE-DEV"
        self.calls: list = []
        self.polled = threading.Event()

    def connect(self, job=None):
        self.calls.append("connect")

    def abort(self):
        self.calls.append("abort")

    def close(self):
        self.calls.append("close")

    def prepare_bits(self, names, job=None):
        self.calls.append(("prepare_bits", tuple(sorted(names))))
        return len(names)

    def poll(self, state, interval, stop, once):
        self.calls.append("poll")
        self.polled.set()
        stop.wait(timeout=5.0)

    def coverage(self):
        return None


def test_connect_uses_the_transport_and_starts_polling():
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=FakeTransport())
    link.connect(relay_model=None, poll_interval=0.05)
    assert link.ready.is_set()
    assert link.error == ""
    assert link.fid == "FAKE-FID"
    assert link.transport.polled.wait(timeout=5.0)
    assert "connect" in link.transport.calls
    link.close()
    assert "close" in link.transport.calls


def test_prepare_bits_stops_polling_around_the_call():
    """The telnet stream cannot interleave discovery with the poll pipeline,
    and py61850's client is not thread-safe. The shell must pause either way."""
    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=FakeTransport())
    link.connect(relay_model=None, poll_interval=0.05)
    assert link.transport.polled.wait(timeout=5.0)
    link.prepare_bits({"PLT01", "PLT02"})
    calls = link.transport.calls
    i = calls.index(("prepare_bits", ("PLT01", "PLT02")))
    # polling ran before, and was restarted after
    assert "poll" in calls[:i]
    assert "poll" in calls[i + 1:]
    link.close()


def test_a_failing_connect_leaves_the_link_ready_and_errored():
    class Boom(FakeTransport):
        def connect(self, job=None):
            raise RuntimeError("sem rota")

    link = RelayLink("192.0.2.10", 23, logging.getLogger("t"),
                     transport=Boom())
    link.connect(relay_model=None, poll_interval=0.05)
    assert link.ready.is_set()
    assert "sem rota" in link.error
    assert not link.connected
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_glv_transport.py -q`
Expected: FAIL — `RelayLink.__init__() got an unexpected keyword argument 'transport'`.

- [ ] **Step 3: Create the Transport protocol**

`sellib/web/glv/transport/__init__.py`:

```python
"""The transport seam: what a RelayLink needs from a way of talking to a relay.

`RelayLink` owns identity, the refcount, the `LiveState`, the watchdog and the
poll thread. It owns no protocol. Everything protocol-shaped lives behind this
Protocol, so a bug in one transport cannot reach the other.

`abort()` is a transport method and deliberately not a generic timeout. Telnet
breaks a hung login by CLOSING THE SOCKET, because selprotopy swallows the
exception and retries -- nothing else wakes it. py61850 has a real socket
timeout and needs none of that. Collapsing the two into "rely on the
transport's timeout" would delete the property that makes telnet recover, and
would pass a happy-path test while doing it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

MODE_TARGET = "target_region"
MODE_FAST_METER = "fast_meter_digitals"
MODE_TAR = "tar_digitals"
MODE_MMS = "mms"

SCAN_TELNET = "telnet"
SCAN_MMS = "mms"

# The port each scan mode talks to. MMS is 102, telnet is the relay's ASCII
# port; they differ, which is why LinkPool (keyed on ip:port) can hold both
# views of one relay without them colliding.
DEFAULT_PORTS = {SCAN_TELNET: 23, SCAN_MMS: 102}


@runtime_checkable
class Transport(Protocol):
    mode: str
    fid: str
    devid: str

    def connect(self, job=None) -> None:
        """Open the connection and identify the relay. Raises on failure."""

    def abort(self) -> None:
        """Break a connect() that is hung, from another thread."""

    def close(self) -> None:
        ...

    def prepare_bits(self, names, job=None) -> int:
        """Make `names` readable. Returns how many were newly resolved."""

    def poll(self, state, interval, stop, once) -> None:
        """Read into `state` until `stop` is set."""

    def coverage(self):
        """Coverage report, or None for a transport that reads everything."""


def pick_transport(scan_mode: str, *, ip: str, port: int, acc_password: str,
                   relay_model, logger, **kwargs) -> Transport:
    from sellib.web.glv.transport.telnet import TelnetTransport
    if scan_mode == SCAN_MMS:
        from sellib.web.glv.transport.mms import MmsTransport
        return MmsTransport(ip, port, relay_model=relay_model, logger=logger,
                            **kwargs)
    return TelnetTransport(ip, port, acc_password=acc_password,
                           relay_model=relay_model, logger=logger)
```

- [ ] **Step 4: Move the telnet half into `TelnetTransport`, verbatim**

Create `sellib/web/glv/transport/telnet.py`. **Cut and paste these members out of `sellib/web/glv/link.py` without editing their bodies.** Keep their Portuguese comments exactly as they are — they record measurements.

| Moves from `link.py` | Becomes |
|---|---|
| module constants `MIN_ROWS_DESIRED`, `TAR_MAX_ROWS`, `MODE_TARGET`/`MODE_FAST_METER`/`MODE_TAR` | re-export from `transport/__init__.py`; keep the numeric constants here |
| `setup_relay()` | module function, unchanged |
| `mode_for()` | module function, unchanged |
| `RelayLink._setup_with_watchdog` | `TelnetTransport._setup_with_watchdog`, body unchanged |
| `RelayLink._setup_ascii_reader` | `TelnetTransport._setup_ascii_reader`, body unchanged |
| `RelayLink._reindex` | `TelnetTransport._reindex`, body unchanged |
| `RelayLink.ensure_bits` | `TelnetTransport.prepare_bits` — **rename only**; delete the `_stop_polling`/`_start_polling` lines (the shell does that now) and nothing else |
| `RelayLink._log_fast_meter_digitals` | `TelnetTransport._log_fast_meter_digitals`, body unchanged |
| `RelayLink._start_polling`'s mode dispatch | `TelnetTransport.poll`, calling `poll_loop` / `poll_loop_tar` / `poll_loop_fastmeter` with the same args |

`TelnetTransport.connect(job)` is the body of today's `RelayLink.connect` **minus** the try/except, the `self.error` assignment, `self.ready.set()` and `_start_polling()` — those stay in the shell. `abort()` is the socket-closing body from `_setup_with_watchdog`'s inner `abort()`.

- [ ] **Step 5: Reduce `RelayLink` to the lifecycle shell**

In `sellib/web/glv/link.py`, `RelayLink.__init__` gains `transport` and keeps every existing attribute. **`owners`, `abandon()` and `release()` must not change** — `owners` is a set of diagram ids so a double click cannot close the telnet under a live diagram, `abandon()` returns a hung setup's slot, and `release()` pops inside the lock so a concurrent `acquire()` cannot join a closing link. Three recorded bug fixes, all concurrency, all here.

```python
    def connect(self, *, relay_model, poll_interval: float, acc_password=None,
                job=None, setup_timeout: float = SETUP_TIMEOUT) -> None:
        """Conecta e sobe o polling. Nao levanta: falha vira `self.error`."""
        try:
            self._poll_interval = poll_interval
            self.mode = self.transport.mode
            if job:
                job.stage("Conectando ao rele...", 8)
            self._connect_with_watchdog(setup_timeout, job)
            self.fid = self.transport.fid or ""
            self.devid = self.transport.devid or ""
            self.logger.info("[glv] %s conectado. FID=%s", self.key, self.fid)
            if job:
                job.stage(f"Conectado ({self.fid or 'rele'})", 20)
            self._start_polling()
            self.logger.info("[glv] %s: polling no modo %s", self.key, self.mode)
        except Exception as e:
            if not self.error:
                self.error = f"sem conexão com {self.key}: {e}"
            self.logger.warning("[glv] falha ao conectar em %s: %s", self.key, e)
            self._close_transport()
        finally:
            self.ready.set()

    def prepare_bits(self, names, job=None) -> int:
        """Descobre/mapeia bits, com o polling parado.

        Parar e' obrigatorio nos dois transportes, por motivos diferentes: o
        telnet e' um stream so e intercalar `TAR <nome>` com o pipeline de Fast
        Meter embaralha as duas respostas; o cliente da py61850 nao e'
        thread-safe (um socket, um contador de invoke).
        """
        with self._lock:
            was_polling = self._poll_thread is not None
            if was_polling:
                self._stop_polling()
            try:
                return self.transport.prepare_bits(names, job=job)
            finally:
                if was_polling:
                    self._start_polling()

    def _start_polling(self) -> None:
        stop = threading.Event()
        thread = threading.Thread(
            target=self.transport.poll,
            args=(self.state, self._poll_interval, stop, self._once),
            daemon=True, name=f"glv-poll-{self.key}")
        self._poll_stop, self._poll_thread = stop, thread
        thread.start()
```

Keep `ensure_bits` as a one-line alias so `diagram.py` needs no edit in this task:

```python
    def ensure_bits(self, names, job=None) -> int:
        return self.prepare_bits(names, job=job)
```

- [ ] **Step 6: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_glv_transport.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: new tests PASS; suite still **531 passed** plus the 3 new.

- [ ] **Step 7: Prove the move was verbatim**

Run:
```bash
git stash && git show HEAD:sellib/web/glv/link.py > /tmp/link_before.py; git stash pop
.venv/bin/python - <<'EOF'
import re
before = open('/tmp/link_before.py').read()
after = (open('sellib/web/glv/link.py').read()
         + open('sellib/web/glv/transport/telnet.py').read())
for name in ("_setup_ascii_reader", "_log_fast_meter_digitals", "_reindex"):
    m = re.search(rf"def {name}.*?(?=\n    def |\n\ndef |\Z)", before, re.S)
    body = m.group(0)
    print(f"{name}: {'VERBATIM' if body in after else 'CHANGED -- review'}")
EOF
```
Expected: all three `VERBATIM`. If one says CHANGED, revert that method and re-move it.

- [ ] **Step 8: Commit**

```bash
git add sellib/web/glv/transport/ sellib/web/glv/link.py tests/test_glv_transport.py
git commit -m "GLV: um seam de transporte, com o telnet movido sem editar corpo"
```

- [ ] **Step 9: BENCH GATE — do not proceed past Task 3 without this**

When the bench is reachable, run `python3 app.py --web` and open one diagram per family, connect, and record the bit counts:

| Relay | IP | Expect |
|---|---|---|
| SEL-411L-A | 203.0.113.22 | connects; `target_region`; bit count matches pre-refactor |
| SEL-311C-1 | 203.0.113.23 | connects; `tar_digitals`; ~731 named bits |
| SEL-751 | 203.0.113.41 | connects; `fast_meter_digitals`; 1116 digitals, 15 analogs |

Any difference is a regression in the move. Fix before continuing.

---

### Task 2: Read `sAddr` out of an SCD

**Files:**
- Modify: `sellib/parsers/scd.py` (add to the end; do not touch `load_scd`, `extract_gse_communication_map` or `extract_goose_subscriptions_by_ied`)
- Test: `tests/test_scd_saddr.py`
- Test fixture: `tests/fixtures/saddr_min.scd`

**Interfaces:**
- Consumes: nothing.
- Produces: `ScdPoint` dataclass with fields `bit: str`, `ld_inst: str`, `ln: str`, `do: str`, `da: str`; and `sel_short_addresses(scd_path: Path) -> dict[str, dict[str, ScdPoint]]` keyed by IED name then by upper-case bit name.

- [ ] **Step 1: Write the fixture**

`tests/fixtures/saddr_min.scd` — a minimal SCL carrying the two shapes that matter: a flat `DAI` and one nested under `SDI`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL" version="2007" revision="B">
  <IED name="REL_A" type="SEL_451_5" configVersion="ICD-451-R408-V1-Z330010-D20250430">
    <AccessPoint name="S1">
      <Server>
        <LDevice inst="ANN">
          <LN0 lnType="LN0" lnClass="LLN0" inst="">
            <DOI name="Loc"><DAI name="stVal" sAddr="db:LOC"/></DOI>
          </LN0>
          <LN lnType="GGIO_BS32" lnClass="GGIO" inst="1" prefix="PLT1">
            <DOI name="Ind01"><DAI name="stVal" sAddr="db:PLT01"/></DOI>
            <DOI name="Ind02"><DAI name="stVal" sAddr="db:PLT02"/></DOI>
          </LN>
          <LN lnType="GGIO_BC01" lnClass="GGIO" inst="1" prefix="RB">
            <DOI name="SPCSO01">
              <SDI name="Oper"><DAI name="ctlVal" sAddr="db:RB01"/></SDI>
            </DOI>
          </LN>
        </LDevice>
      </Server>
    </AccessPoint>
  </IED>
</SCL>
```

- [ ] **Step 2: Write the failing test**

`tests/test_scd_saddr.py`:

```python
"""sAddr extraction: the only bridge from a Relay Word name to 61850.

`sAddr="db:PLT01"` is an SCL attribute and the relay does NOT serve it over
MMS -- verified on a live SEL-451-5 R331, where `$DC$Ind01$d` answers
`object-non-existent`. So this parse is the only way the map can be built.
"""
from __future__ import annotations

from pathlib import Path

from sellib.parsers.scd import sel_short_addresses

FIXTURE = Path(__file__).parent / "fixtures" / "saddr_min.scd"


def test_extracts_bits_per_ied():
    per_ied = sel_short_addresses(FIXTURE)
    assert set(per_ied) == {"REL_A"}
    bits = per_ied["REL_A"]
    assert set(bits) == {"LOC", "PLT01", "PLT02", "RB01"}


def test_ln_name_is_prefix_class_inst_and_lln0_has_no_prefix():
    bits = sel_short_addresses(FIXTURE)["REL_A"]
    assert (bits["PLT01"].ln, bits["PLT01"].do, bits["PLT01"].da) == (
        "PLT1GGIO1", "Ind01", "stVal")
    assert bits["LOC"].ln == "LLN0"
    assert bits["PLT01"].ld_inst == "ANN"


def test_nested_sdi_joins_with_a_dot():
    """`SDI Oper` + `DAI ctlVal` is the `Oper.ctlVal` MMS leaf."""
    bits = sel_short_addresses(FIXTURE)["REL_A"]
    assert bits["RB01"].ln == "RBGGIO1"
    assert bits["RB01"].do == "SPCSO01"
    assert bits["RB01"].da == "Oper.ctlVal"


def test_names_are_upper_cased():
    bits = sel_short_addresses(FIXTURE)["REL_A"]
    assert all(k == k.upper() for k in bits)
```

- [ ] **Step 3: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_scd_saddr.py -q`
Expected: FAIL — `ImportError: cannot import name 'sel_short_addresses'`.

- [ ] **Step 4: Implement**

Append to `sellib/parsers/scd.py`:

```python
# -- sAddr: o nome da Relay Word dentro do SCL ------------------------------
#
# A SEL grava o nome do bit em `sAddr="db:NOME"` no DAI. Esse atributo e' de
# SCL e o rele NAO o serve por MMS, entao esta e' a unica ponte entre o nome
# que o GLE desenha e o item MMS que o rele responde.
#
# O FC nao esta aqui: ele mora no DA do DOType, dentro de DataTypeTemplates.
# Nao resolvemos essa cadeia -- quem da o FC e' o proprio rele, casando
# `LN$*$DO$DA` contra o GetLogicalDeviceDirectory. Ver `web/glv/mms_map.py`.

_SADDR_PREFIX = "db:"


@dataclass(frozen=True)
class ScdPoint:
    """Onde um bit da Relay Word mora no modelo 61850, menos o FC."""
    bit: str
    ld_inst: str
    ln: str          # prefix + lnClass + inst, como o MMS soletra
    do: str
    da: str          # 'stVal', ou 'Oper.ctlVal' quando vem de um SDI


def _ln_name(ln: ET.Element) -> str:
    if _strip_ns(ln.tag) == "LN0":
        return "LLN0"
    return (f'{ln.get("prefix") or ""}{ln.get("lnClass") or ""}'
            f'{ln.get("inst") or ""}')


def _walk_dais(node: ET.Element, trail: list):
    """Rende (caminho_do_da, elemento) para cada DAI sob um DOI, entrando em SDI."""
    for child in node:
        tag = _strip_ns(child.tag)
        if tag == "DAI":
            yield ".".join(trail + [child.get("name") or ""]), child
        elif tag == "SDI":
            yield from _walk_dais(child, trail + [child.get("name") or ""])


def sel_short_addresses(scd_path: Path) -> dict:
    """`{nome_do_IED: {NOME_DO_BIT: ScdPoint}}` para todo sAddr="db:...".

    Um nome pode aparecer duas vezes no mesmo IED (o mesmo bit em CO e em ST,
    por exemplo). Fica o PRIMEIRO: quem decide o FC e' o rele, mais tarde, e
    a preferencia por ST/MX e' aplicada la.
    """
    root = ET.parse(scd_path).getroot()
    out: dict = {}
    for ied in _iter_local(root, "IED"):
        name = ied.get("name") or ""
        bits: dict = {}
        for ldev in _iter_local(ied, "LDevice"):
            ld_inst = ldev.get("inst") or ""
            for ln in list(_iter_local(ldev, "LN0")) + list(_iter_local(ldev, "LN")):
                ln_name = _ln_name(ln)
                for doi in _iter_local(ln, "DOI"):
                    do = doi.get("name") or ""
                    for da_path, dai in _walk_dais(doi, []):
                        sa = dai.get("sAddr") or ""
                        if not sa.startswith(_SADDR_PREFIX):
                            continue
                        bit = sa[len(_SADDR_PREFIX):].upper()
                        bits.setdefault(bit, ScdPoint(
                            bit=bit, ld_inst=ld_inst, ln=ln_name,
                            do=do, da=da_path))
        out[name] = bits
    return out
```

`dataclass`, `Path` and `ET` are already imported at the top of `scd.py` (lines 37-40); add nothing.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_scd_saddr.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: 4 new PASS; suite green.

- [ ] **Step 6: Check it against the real substation SCD**

Run:
```bash
.venv/bin/python -c "
from pathlib import Path
from sellib.parsers.scd import sel_short_addresses
p = sorted(Path('rdbs').glob('*.scd'))[-1]
per = sel_short_addresses(p)
print(p.name, len(per), 'IEDs')
b = per['QPC1_TFE_UPC1']
print('QPC1_TFE_UPC1:', len(b), 'bits')
print(' PLT01 ->', b['PLT01'])
"
```
Expected: 30 IEDs; **2321 bits** for `QPC1_TFE_UPC1`; `PLT01` resolves to `ld_inst='ANN'`, `ln='PLT1GGIO1'`, `do='Ind01'`, `da='stVal'`. A different bit count means the walk is missing a shape — investigate before continuing.

- [ ] **Step 7: Commit**

```bash
git add sellib/parsers/scd.py tests/test_scd_saddr.py tests/fixtures/saddr_min.scd
git commit -m "SCD: extrair o sAddr, que e' a unica ponte pro nome da Relay Word"
```

---

### Task 3: Ship the ICD fallback tables and load them

**Files:**
- Create: `tools/mms_tables_from_wordbits.py`
- Create: `sellib/core/mms_tables.py`
- Create: `data/mms_map/*.json` (generated; ~1.7 MB)
- Modify: `sellib/paths.py` (add `MMS_MAP_DIR`)
- Modify: `tests/test_relay_models.py` (extend the drift test to three registries)
- Test: `tests/test_mms_tables.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `sellib.core.mms_tables.lookup(part: str, group: str | None = None) -> MmsTable | None`, `MmsTable` with `.part`, `.group`, `.config_version`, `.bits: dict[str, tuple[str, str]]` mapping upper-case bit → `(ld_suffix, item)`; `sellib.core.mms_tables.norm_part(s) -> str`; `sellib.core.mms_tables.invalidate()`.

- [ ] **Step 1: Add the paths constant**

In `sellib/paths.py`, beside `WORDBITS_DIR`:

```python
# Mapa bit da Relay Word -> item MMS, derivado dos ICD de fabrica. E' o
# FALLBACK do modo MMS do GLV: o SCD do projeto vem primeiro, porque e' o
# como-construido. Terceiro registro por modelo -- ver a nota sobre deriva em
# docs/ENGINEERING-NOTES.md e o teste que a guarda.
MMS_MAP_DIR: Path = DATA_DIR / "mms_map"
```

- [ ] **Step 2: Write the generator**

`tools/mms_tables_from_wordbits.py` — splits `fixtures/ICD files/SEL/wordbits.json` into one file per part/group, keeping only the parts present in the corpus by default.

```python
#!/usr/bin/env python3
"""Split the ICD-derived map into the per-part tables the GLV ships.

    python3 tools/mms_tables_from_wordbits.py            # corpus parts only
    python3 tools/mms_tables_from_wordbits.py --all      # every part (9.0 MB)

Source is `fixtures/ICD files/SEL/wordbits.json`, which is NOT in git (231 MB
of vendor ICDs behind it). The output IS in git, because the app needs it at
runtime and a clean clone has no ICDs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sellib.paths import FIXTURES_DIR, MMS_MAP_DIR  # noqa: E402

CORPUS = {"411L", "451", "487E", "311C1", "751", "2414", "2440"}


def norm(part: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (part or "").upper())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path,
                    default=FIXTURES_DIR / "ICD files" / "SEL" / "wordbits.json")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("-o", "--out", type=Path, default=MMS_MAP_DIR)
    args = ap.parse_args()

    models = json.loads(args.src.read_text(encoding="utf-8"))["models"]
    args.out.mkdir(parents=True, exist_ok=True)
    written = total = 0
    for key, entry in models.items():
        if not entry.get("bits"):
            continue
        part, group = key.split("/", 1)
        if not args.all and norm(part) not in CORPUS:
            continue
        doc = {
            "part": part,
            "group": group,
            "config_version": entry.get("config_version"),
            "source": "ICD de fabrica, via fixtures/ICD files/SEL/wordbits.json",
            # bit -> [ld_suffix, item]. O item ja traz o FC do ICD; o modo MMS
            # confere contra o diretorio do rele antes de usar.
            "bits": {b["bit"].upper(): [b["ld"], b["item"]]
                     for b in entry["bits"]},
        }
        path = args.out / f"{norm(part)}-{group}.json"
        path.write_text(json.dumps(doc, separators=(",", ":"),
                                   ensure_ascii=False) + "\n", encoding="utf-8")
        written += 1
        total += path.stat().st_size
    print(f"{written} tabelas em {args.out} ({total / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Generate the tables**

Run: `.venv/bin/python tools/mms_tables_from_wordbits.py`
Expected: `~14 tabelas em .../data/mms_map (1.7 MB)`.

If `fixtures/ICD files/SEL/wordbits.json` is missing (it is gitignored), the tables cannot be regenerated on this machine — get the file from whoever has the ICD corpus. This is why the output is committed.

- [ ] **Step 4: Write the failing test**

`tests/test_mms_tables.py`:

```python
"""The shipped ICD fallback tables.

These are the FALLBACK. The project SCD is preferred because it is the
as-built map; a table only says what the factory ICD had. Measured on a live
SEL-451-5 R331 -- a firmware (R331) with no table of its own -- 99.2% of the
451/010 table's items still existed on the relay, which is why the nearest
group plus verification is a sound fallback and a bare guess would not be.
"""
from __future__ import annotations

import pytest

from sellib.core import mms_tables


def test_the_451_table_loads_and_maps_a_known_latch():
    t = mms_tables.lookup("451")
    assert t is not None
    assert t.bits["PLT01"] == ("ANN", "PLT1GGIO1$ST$Ind01$stVal")


def test_part_normalisation_folds_the_three_spellings():
    assert mms_tables.norm_part("311C-1") == mms_tables.norm_part("311C1")
    assert mms_tables.norm_part("311c_1") == "311C1"


def test_newest_group_wins_when_no_group_is_asked_for():
    t = mms_tables.lookup("451")
    every = mms_tables.groups_for("451")
    assert t.group == max(every)


def test_unknown_part_is_none_not_an_empty_table():
    """An empty table would read as 'nothing is addressable'; None means
    'no table', which is what the badge must say."""
    assert mms_tables.lookup("9999") is None
```

- [ ] **Step 5: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_mms_tables.py -q`
Expected: FAIL — `ModuleNotFoundError: sellib.core.mms_tables`.

- [ ] **Step 6: Implement the loader**

`sellib/core/mms_tables.py`:

```python
"""The shipped bit -> MMS item tables, loaded lazily and memoised.

Third per-model registry in this project, after `data/relay_models/` (GLV and
the GLE tools) and `data/wordbits/` (the DNP map's name check). They come from
different sources and drift; `tests/test_relay_models.py` fails on a model
present in one and missing from another unless the asymmetry is written down.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass

from sellib.paths import MMS_MAP_DIR

_LOCK = threading.Lock()
_CACHE: dict = {}


def norm_part(part: str) -> str:
    """`311C-1`, `311C1` and `311c_1` are one peca.

    The ICD file name writes the dash, the SCD's configVersion does not, and
    the RDB writes its own. Folding them is what stopped the 311C matching
    nothing and reporting 100% of its Relay Word as unaddressable.
    """
    return re.sub(r"[^A-Z0-9]", "", (part or "").upper())


@dataclass(frozen=True)
class MmsTable:
    part: str
    group: str
    config_version: "str | None"
    bits: dict          # BIT -> (ld_suffix, item)


def _load() -> dict:
    with _LOCK:
        if _CACHE:
            return _CACHE
        for path in sorted(MMS_MAP_DIR.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            part = norm_part(raw["part"])
            table = MmsTable(
                part=part, group=str(raw["group"]),
                config_version=raw.get("config_version"),
                bits={k.upper(): (v[0], v[1]) for k, v in raw["bits"].items()},
            )
            _CACHE.setdefault(part, {})[table.group] = table
        return _CACHE


def groups_for(part: str) -> list:
    return sorted(_load().get(norm_part(part), {}))


def lookup(part: str, group: "str | None" = None):
    """The table for `part`. Without a group, the newest one.

    Nearest-group is deliberate: firmware moves faster than the ICD corpus, and
    the caller verifies every item against the relay's own directory anyway.
    """
    by_group = _load().get(norm_part(part))
    if not by_group:
        return None
    if group is not None and str(group) in by_group:
        return by_group[str(group)]
    return by_group[max(by_group)]


def invalidate() -> None:
    with _LOCK:
        _CACHE.clear()
```

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_mms_tables.py -q`
Expected: 4 PASS.

- [ ] **Step 8: Extend the registry drift test to three**

In `tests/test_relay_models.py`, find `test_the_two_model_registries_agree_or_say_why_not` and its `KNOWN_ASYMMETRIES`. Add a third check:

```python
# `data/mms_map/` is the third registry. It is one-way on purpose: every
# shipped table must belong to a relay model, but a relay model needs no table
# -- a model with no ICD in the corpus is a gap in the vendor data, not a bug.
#
# The keys do not line up the way the other two do. A relay model covers a
# family (`SEL-311C`); an ICD part names a variant (`311C-1` -> `311C1`). So
# the match is a PREFIX, not equality.
MMS_MAP_KNOWN_EXTRA = {
    "2414": "sem relay model: o 2414 e' concentrador, nao abre na GLV, mas o "
            "ICD dele existe e a tabela nao custa nada.",
    "2440": "idem 2414.",
}


def test_every_shipped_mms_table_belongs_to_a_known_relay_model():
    from sellib.core import mms_tables

    models = {norm_part(m.replace("SEL-", ""))
              for m in _registry_models(RELAY_MODELS_DIR)}
    shipped = set(mms_tables._load())
    known = {norm_part(k) for k in MMS_MAP_KNOWN_EXTRA}
    orphans = sorted(
        part for part in shipped
        if part not in known
        and not any(part.startswith(m) for m in models if m)
    )
    assert not orphans, (
        f"tabela MMS sem relay model: {orphans}. Ou acrescente o "
        f"data/relay_models/<MODELO>.json, ou registre em MMS_MAP_KNOWN_EXTRA "
        f"dizendo por que nao.")
```

Add `from sellib.core.mms_tables import norm_part` to the imports at the top of the test file. `_registry_models` already exists in this file (it reads each JSON's `model` field); the mms tables are enumerated through `mms_tables._load()` instead, because they are keyed by part and group rather than by model.

- [ ] **Step 9: Run the suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: green. If `test_every_shipped_mms_table_belongs_to_a_known_relay_model` fails, either the part normalisation disagrees with the relay-model naming, or you shipped a table for a model with no profile — 2414 and 2440 have no `data/relay_models/` entry, so they belong in `MMS_MAP_KNOWN_EXTRA` with a one-line reason.

- [ ] **Step 10: Commit**

```bash
git add tools/mms_tables_from_wordbits.py sellib/core/mms_tables.py \
        sellib/paths.py data/mms_map/ tests/test_mms_tables.py \
        tests/test_relay_models.py
git commit -m "Tabelas MMS de fallback: uma por peca, e o terceiro registro no teste de deriva"
```

---

### Task 4: Capture the relay fixtures — BENCH REQUIRED

Everything after this is testable offline **only** once these exist. At plan time the bench was down and the capture could not be made.

**Files:**
- Create: `tools/capture_mms_fixtures.py`
- Create: `tests/fixtures/mms/451_ann_directory.json`
- Create: `tests/fixtures/mms/451_datadefs.json`
- Create: `tests/fixtures/mms/451_reads_b64.json`
- Create: `tests/fixtures/mms/451_expected_stvals.json`

**Interfaces:**
- Consumes: nothing.
- Produces: the four fixture files above. `451_ann_directory.json` is a sorted list of every variable name in the ANN logical device. `451_datadefs.json` maps container name → the `decode_data_definition` dict. `451_reads_b64.json` maps container name → base64 of the raw read-response bytes. `451_expected_stvals.json` maps container name → `{child_name: bool}`.

- [ ] **Step 1: Write the capture tool**

`tools/capture_mms_fixtures.py`:

```python
#!/usr/bin/env python3
"""Record what the MMS map layer needs, so its tests run with no relay.

    python3 tools/capture_mms_fixtures.py 203.0.113.61

Run this once against a relay whose model has a shipped table. The recorded
directory listing is what proves the FC-from-the-relay trick works, and the
data definitions plus one read are what prove the positional decode aligns.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from py61850 import MmsClient, decode_data_definition  # noqa: E402
from py61850.mms import pdu  # noqa: E402
from sellib.paths import PROJECT_ROOT  # noqa: E402

OUT = PROJECT_ROOT / "tests" / "fixtures" / "mms"
CONTAINERS = ["PLT1GGIO1$ST", "ALT1GGIO1$ST", "VB1XGGIO1$ST",
              "IN1XGGIO1$ST", "OUT1GGIO1$ST"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=102)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    c = MmsClient(args.host, port=args.port, timeout=10)
    c.connect()
    try:
        lds = c.get_server_directory()
        ann = [l for l in lds if l.endswith("ANN")][0]
        (OUT / "451_ann_directory.json").write_text(
            json.dumps(sorted(c.get_logical_device_directory(ann)), indent=1))

        defs, reads, expected = {}, {}, {}
        for item in CONTAINERS:
            defs[item] = decode_data_definition(c.get_data_definition(ann, item))
            raw = c.read(ann, item)
            reads[item] = base64.b64encode(raw).decode()
            values = pdu.decode_read_response(raw)[0]
            children = defs[item]["type"]["structure"]
            expected[item] = {}
            for child, value in zip(children, values):
                sub = child["type"]
                names = ([g["name"] for g in sub["structure"]]
                         if isinstance(sub, dict) and "structure" in sub else [])
                if "stVal" in names:
                    expected[item][child["name"]] = value[names.index("stVal")]

        (OUT / "451_datadefs.json").write_text(json.dumps(defs, indent=1))
        (OUT / "451_reads_b64.json").write_text(json.dumps(reads, indent=1))
        (OUT / "451_expected_stvals.json").write_text(json.dumps(expected, indent=1))
        print(f"gravado em {OUT}, LDs: {lds}")
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it against the 451**

Run: `.venv/bin/python tools/capture_mms_fixtures.py 203.0.113.61`
Expected: four files written; the ANN directory has ~12 735 entries.

- [ ] **Step 3: Sanity-check the capture**

Run:
```bash
.venv/bin/python -c "
import json
from pathlib import Path
p = Path('tests/fixtures/mms')
d = json.loads((p/'451_ann_directory.json').read_text())
e = json.loads((p/'451_expected_stvals.json').read_text())
print(len(d), 'variaveis;', 'PLT1GGIO1\$ST\$Ind01\$stVal' in d)
print({k: len(v) for k, v in e.items()})
"
```
Expected: ~12735 variables, `True`, and each container reporting 33 stVals (Beh + Ind01..Ind32).

- [ ] **Step 4: Commit**

```bash
git add tools/capture_mms_fixtures.py tests/fixtures/mms/
git commit -m "Fixtures MMS gravadas do 451 da bancada, pro mapa testar sem rele"
```

---

### Task 5: Resolve the map — SCD first, table as fallback, verified by the relay

**Files:**
- Create: `sellib/web/glv/mms_map.py`
- Test: `tests/test_mms_map.py`

**Interfaces:**
- Consumes: `sellib.parsers.scd.sel_short_addresses` and `ScdPoint` (Task 2); `sellib.core.mms_tables.lookup` (Task 3); the fixtures from Task 4.
- Produces:
  - `MmsPoint` — `bit: str`, `ld: str` (full LD name), `container: str` (`LN$FC`), `child: str` (the DO name inside the container), `item: str` (full `LN$FC$DO$DA`).
  - `MmsMap` — `points: dict[str, MmsPoint]` keyed by upper-case bit, `source: str` (`"scd"` / `"tabela"` / `"scd+tabela"`), plus `containers() -> dict[tuple[str, str], list[MmsPoint]]` and `coverage(wanted) -> Coverage`.
  - `Coverage` — `total: int`, `mapped: int`, `missing: tuple[str, ...]`, `fraction: float`.
  - `resolve_map(*, wanted, directory, ld_by_suffix, scd_points=None, table=None) -> MmsMap`.
  - `ld_suffixes(lds: list[str]) -> dict[str, str]`.

- [ ] **Step 1: Write the failing test**

`tests/test_mms_map.py`:

```python
"""Resolving a Relay Word name to an MMS item.

The FC is deliberately NOT parsed out of the SCL type templates. `sAddr` sits
on a DAI but the functional constraint lives on the DA inside the DOType, so
the ordinary route means walking DataTypeTemplates. The relay already publishes
every fully-qualified name (12 735 for the 451's ANN alone), so matching
`LN$*$DO$DA` against GetLogicalDeviceDirectory yields the FC *and* verifies the
entry in one pass. Where a DO/DA exists under two FCs -- `LocSta` is at both CO
and ST on the 487E -- ST wins, then MX.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sellib.parsers.scd import ScdPoint
from sellib.web.glv.mms_map import ld_suffixes, resolve_map

FIX = Path(__file__).parent / "fixtures" / "mms"
IED = "QPC1_TFE_UPC1"


@pytest.fixture(scope="module")
def directory():
    return set(json.loads((FIX / "451_ann_directory.json").read_text()))


def test_ld_suffix_comes_from_the_common_prefix():
    """The IED name is not given to us; it is the prefix every LD shares."""
    lds = [f"{IED}ANN", f"{IED}CFG", f"{IED}PRO"]
    assert ld_suffixes(lds) == {"ANN": f"{IED}ANN",
                                "CFG": f"{IED}CFG",
                                "PRO": f"{IED}PRO"}


def test_scd_point_gets_its_fc_from_the_relay(directory):
    scd = {"PLT01": ScdPoint(bit="PLT01", ld_inst="ANN", ln="PLT1GGIO1",
                             do="Ind01", da="stVal")}
    m = resolve_map(wanted={"PLT01"}, directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    p = m.points["PLT01"]
    assert p.item == "PLT1GGIO1$ST$Ind01$stVal"
    assert p.container == "PLT1GGIO1$ST"
    assert p.child == "Ind01"
    assert p.ld == f"{IED}ANN"


def test_a_point_the_relay_does_not_serve_is_dropped(directory):
    scd = {"NOPE": ScdPoint(bit="NOPE", ld_inst="ANN", ln="NOSUCHGGIO9",
                            do="Ind01", da="stVal")}
    m = resolve_map(wanted={"NOPE"}, directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    assert "NOPE" not in m.points
    assert m.coverage({"NOPE"}).missing == ("NOPE",)


def test_table_fills_in_what_the_scd_lacks(directory):
    class FakeTable:
        bits = {"ALT01": ("ANN", "ALT1GGIO1$ST$Ind01$stVal")}

    m = resolve_map(wanted={"ALT01"}, directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"},
                    scd_points={}, table=FakeTable())
    assert m.points["ALT01"].item == "ALT1GGIO1$ST$Ind01$stVal"
    assert m.source == "tabela"


def test_scd_wins_over_the_table_for_the_same_bit(directory):
    class FakeTable:
        bits = {"PLT01": ("ANN", "WRONGGGIO1$ST$Ind01$stVal")}

    scd = {"PLT01": ScdPoint(bit="PLT01", ld_inst="ANN", ln="PLT1GGIO1",
                             do="Ind01", da="stVal")}
    m = resolve_map(wanted={"PLT01"}, directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"},
                    scd_points=scd, table=FakeTable())
    assert m.points["PLT01"].item == "PLT1GGIO1$ST$Ind01$stVal"


def test_containers_group_the_points_for_the_read_plan(directory):
    scd = {f"PLT{i:02d}": ScdPoint(bit=f"PLT{i:02d}", ld_inst="ANN",
                                   ln="PLT1GGIO1", do=f"Ind{i:02d}",
                                   da="stVal")
           for i in range(1, 5)}
    m = resolve_map(wanted=set(scd), directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    groups = m.containers()
    assert list(groups) == [(f"{IED}ANN", "PLT1GGIO1$ST")]
    assert len(groups[(f"{IED}ANN", "PLT1GGIO1$ST")]) == 4


def test_coverage_counts_only_what_was_asked_for(directory):
    scd = {"PLT01": ScdPoint(bit="PLT01", ld_inst="ANN", ln="PLT1GGIO1",
                             do="Ind01", da="stVal")}
    m = resolve_map(wanted={"PLT01", "T10_LED"}, directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    cov = m.coverage({"PLT01", "T10_LED"})
    assert (cov.total, cov.mapped, cov.missing) == (2, 1, ("T10_LED",))
    assert cov.fraction == 0.5
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_mms_map.py -q`
Expected: FAIL — `ModuleNotFoundError: sellib.web.glv.mms_map`.

- [ ] **Step 3: Implement**

`sellib/web/glv/mms_map.py`:

```python
"""Bit da Relay Word -> item MMS, conferido contra o proprio rele.

Duas fontes, nesta ordem: o SCD do projeto (o mapa COMO CONSTRUIDO) e a tabela
de fabrica derivada do ICD. Nenhuma das duas e' descoberta no rele -- o nome do
bit mora no `sAddr` do SCL, que o rele NAO serve por MMS. Nao existe aqui o
equivalente do `TAR <nome>` do telnet, e nunca vai existir.

O FC, esse sim vem do rele: casamos `LN$*$DO$DA` contra o
GetLogicalDeviceDirectory. Isso resolve o FC e confere a entrada de uma vez so.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Ordem de preferencia quando o mesmo DO/DA existe em mais de um FC. Medido:
# `LocSta` aparece em CO e em ST no 487E. Estado antes de controle.
FC_PREFERENCE = ("ST", "MX", "SP", "CF", "DC", "CO")


@dataclass(frozen=True)
class MmsPoint:
    bit: str
    ld: str            # nome completo do logical device, ex. QPC1_TFE_UPC1ANN
    container: str     # LN$FC -- a unidade de leitura
    child: str         # o DO dentro do container
    item: str          # LN$FC$DO$DA


@dataclass(frozen=True)
class Coverage:
    total: int
    mapped: int
    missing: tuple

    @property
    def fraction(self) -> float:
        return (self.mapped / self.total) if self.total else 0.0


@dataclass
class MmsMap:
    points: dict = field(default_factory=dict)
    source: str = ""

    def containers(self) -> dict:
        """`(ld, LN$FC) -> [MmsPoint]` -- o plano de leitura.

        Agrupar por container e' o que faz o polling caber no orcamento: 30
        requisicoes / ~180 ms pro diagrama inteiro contra 170 / 739 ms bit a
        bit, medido no 451 da bancada. A latencia manda, nao os bytes.
        """
        out: dict = {}
        for p in self.points.values():
            out.setdefault((p.ld, p.container), []).append(p)
        return out

    def coverage(self, wanted) -> Coverage:
        wanted = {b.upper() for b in wanted}
        missing = tuple(sorted(b for b in wanted if b not in self.points))
        return Coverage(total=len(wanted), mapped=len(wanted) - len(missing),
                        missing=missing)


def ld_suffixes(lds) -> dict:
    """`sufixo -> nome completo`, tirando o nome do IED do prefixo comum.

    Um LD MMS e' `<nome do IED><inst>`; o SCD e a tabela falam so o `inst`.
    Ninguem nos da o nome do IED, mas ele e' o prefixo que todos os LD dividem.
    """
    lds = list(lds)
    if not lds:
        return {}
    prefix = os.path.commonprefix(lds) if len(lds) > 1 else ""
    return {ld[len(prefix):] or ld: ld for ld in lds}


def _resolve_fc(directory, ln: str, do: str, da: str) -> "str | None":
    """Acha o FC perguntando ao rele, e nao ao DataTypeTemplates do SCL."""
    for fc in FC_PREFERENCE:
        if f"{ln}${fc}${do}${da}" in directory:
            return fc
    return None


def resolve_map(*, wanted, directory, ld_by_suffix, scd_points=None,
                table=None) -> MmsMap:
    """Monta o mapa dos bits pedidos. So entra o que o rele confirma servir."""
    wanted = {b.upper() for b in wanted}
    scd_points = scd_points or {}
    points: dict = {}
    used_scd = used_table = False

    for bit in wanted:
        sp = scd_points.get(bit)
        if sp is not None:
            ld = ld_by_suffix.get(sp.ld_inst)
            names = directory.get(sp.ld_inst) or ()
            fc = _resolve_fc(names, sp.ln, sp.do, sp.da) if ld else None
            if fc:
                points[bit] = MmsPoint(
                    bit=bit, ld=ld, container=f"{sp.ln}${fc}", child=sp.do,
                    item=f"{sp.ln}${fc}${sp.do}${sp.da}")
                used_scd = True
                continue
        if table is None:
            continue
        entry = table.bits.get(bit)
        if entry is None:
            continue
        suffix, item = entry
        ld = ld_by_suffix.get(suffix)
        names = directory.get(suffix) or ()
        if not ld or item not in names:
            continue
        ln, fc, do = item.split("$")[0], item.split("$")[1], item.split("$")[2]
        points[bit] = MmsPoint(bit=bit, ld=ld, container=f"{ln}${fc}",
                               child=do, item=item)
        used_table = True

    source = ("scd+tabela" if used_scd and used_table
              else "scd" if used_scd else "tabela" if used_table else "")
    return MmsMap(points=points, source=source)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_mms_map.py -q`
Expected: 7 PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests/ -q`

```bash
git add sellib/web/glv/mms_map.py tests/test_mms_map.py
git commit -m "Mapa MMS: SCD primeiro, tabela de fallback, e o FC vindo do rele"
```

---

### Task 6: `MmsTransport` and the `LN$FC` poll loop

**Files:**
- Create: `sellib/web/glv/transport/mms.py`
- Test: `tests/test_mms_transport.py`

**Interfaces:**
- Consumes: `Transport` (Task 1), `resolve_map` / `MmsMap` / `ld_suffixes` (Task 5), `mms_tables.lookup` (Task 3), `sel_short_addresses` (Task 2), the fixtures (Task 4).
- Produces: `MmsTransport(ip, port, *, relay_model, logger, scd_path=None, ied_name=None)` implementing `Transport`; and `decode_container(definition, read_bytes) -> dict[str, bool]`.

- [ ] **Step 1: Write the failing test**

`tests/test_mms_transport.py`:

```python
"""The MMS poll loop, driven off recorded relay bytes.

`decode_container` is the linchpin: one GetVariableAccessAttributes per
container at connect time gives the child-name order, and every poll after
that is positional -- no name lookups on the hot path. Verified against the
live 451: 33 children (Beh + Ind01..Ind32) matching the 33-element read, and
18 spot-checked bits equal to their individual reads.
"""
from __future__ import annotations

import base64
import json
import threading
from pathlib import Path

import pytest

from sellib.web.glv.state import LiveState
from sellib.web.glv.transport.mms import MmsTransport, decode_container

FIX = Path(__file__).parent / "fixtures" / "mms"


@pytest.fixture(scope="module")
def recorded():
    return (json.loads((FIX / "451_datadefs.json").read_text()),
            json.loads((FIX / "451_reads_b64.json").read_text()),
            json.loads((FIX / "451_expected_stvals.json").read_text()))


def test_decode_container_matches_the_recorded_values(recorded):
    defs, reads, expected = recorded
    for container, definition in defs.items():
        got = decode_container(definition, base64.b64decode(reads[container]))
        assert got == expected[container], container


def test_decode_container_returns_one_entry_per_child_with_a_stval(recorded):
    defs, reads, _ = recorded
    got = decode_container(defs["PLT1GGIO1$ST"],
                           base64.b64decode(reads["PLT1GGIO1$ST"]))
    assert len(got) == 33            # Beh + Ind01..Ind32
    assert "Ind01" in got and isinstance(got["Ind01"], bool)


class FakeClient:
    """Answers reads from the recording; counts requests per cycle."""

    def __init__(self, defs, reads):
        self.defs, self.reads = defs, reads
        self.reads_made = 0

    def read(self, ld, item):
        self.reads_made += 1
        return base64.b64decode(self.reads[item])

    def close(self):
        pass


def test_poll_writes_bits_into_live_state_and_honours_stop(recorded):
    defs, reads, expected = recorded
    from sellib.web.glv.mms_map import MmsMap, MmsPoint

    points = {f"PLT{i:02d}": MmsPoint(bit=f"PLT{i:02d}", ld="LD",
                                      container="PLT1GGIO1$ST",
                                      child=f"Ind{i:02d}",
                                      item=f"PLT1GGIO1$ST$Ind{i:02d}$stVal")
              for i in range(1, 5)}
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t._client = FakeClient(defs, reads)
    t._map = MmsMap(points=points, source="scd")
    t._layouts = {("LD", "PLT1GGIO1$ST"): defs["PLT1GGIO1$ST"]}

    state = LiveState()
    stop = threading.Event()

    def run():
        t.poll(state, 0.01, stop, None)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    for _ in range(200):
        if state.snapshot()["digitals"]:
            break
        threading.Event().wait(0.01)
    stop.set()
    th.join(timeout=3.0)

    snap = state.snapshot()["digitals"]
    assert snap["PLT01"] == int(bool(expected["PLT1GGIO1$ST"]["Ind01"]))
    # one read per CONTAINER, not per bit -- that is the whole point
    assert t._client.reads_made >= 1
    assert t._client.reads_made < 4 * 200


def test_effective_period_never_goes_below_the_floor():
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    assert t.effective_interval(0.005, last_cycle=0.001) == pytest.approx(0.100)
    assert t.effective_interval(0.100, last_cycle=0.001) == pytest.approx(0.100)
    # a page that costs more than the period: the loop runs flat out
    assert t.effective_interval(0.100, last_cycle=0.250) == pytest.approx(0.250)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_mms_transport.py -q`
Expected: FAIL — `ModuleNotFoundError: sellib.web.glv.transport.mms`.

- [ ] **Step 3: Implement**

`sellib/web/glv/transport/mms.py`. Key pieces — the module docstring must record why the floor and the granularity are what they are:

```python
"""O transporte MMS do GLV: le a pagina aberta em leituras por `LN$FC`.

Medido no SEL-451-5 R331 da bancada, com o GL1.gle do proprio rele:

    por bit        170 req / 739 ms      por DO   170 req / 731 ms
    por LN$FC       30 req / ~180 ms     LD todo   64 req / 1020 ms

O RTT de uma leitura e' 3,1 ms (mediana), entao o ciclo e' basicamente
`containers x 3,1 ms` -- ou seja, POR PAGINA: 3,9 ms numa pagina de um
container, 37 ms na de LEDs, ~180 ms no diagrama inteiro. Por isso este
transporte le so a pagina aberta, como o modo TAR do 3xx, e nao o desenho todo.

Duas coisas medidas e DESCARTADAS, pra ninguem tentar de novo: pipeline e' 3x
mais LENTO (a assinatura e' delayed-ACK) mesmo o rele anunciando
maxServOutstanding=3, e TCP_NODELAY nao muda nada. O unico ganho que sobra e' a
leitura multi-variavel, que e' py61850#1 e nao se resolve aqui.
"""

from __future__ import annotations

import time

from py61850 import MmsClient, decode_data_definition
from py61850.errors import Iec61850Error
from py61850.mms import pdu

from sellib.core import mms_tables
from sellib.parsers.scd import sel_short_addresses
from sellib.web.glv.mms_map import ld_suffixes, resolve_map
from sellib.web.glv.transport import MODE_MMS

# Piso do periodo de polling. Nao e' uma limitacao do protocolo -- e' uma
# decisao: 10 Hz por link ja e' 5x o telnet, e o GLV abre ate 4 links.
MIN_INTERVAL = 0.100


def decode_container(definition: dict, read_bytes: bytes) -> dict:
    """`{nome do filho: valor}` pra cada filho que tem `stVal`.

    A definicao vem uma vez, no connect; a leitura vem a cada volta. O casamento
    e' POSICIONAL: a resposta do Read e' uma lista na ordem da estrutura.
    """
    values = pdu.decode_read_response(read_bytes)
    if not values:
        return {}
    values = values[0]
    out: dict = {}
    for child, value in zip(definition["type"]["structure"], values):
        sub = child["type"]
        names = ([g["name"] for g in sub["structure"]]
                 if isinstance(sub, dict) and "structure" in sub else [])
        if "stVal" in names:
            out[child["name"]] = value[names.index("stVal")]
    return out


class MmsTransport:
    mode = MODE_MMS

    def __init__(self, ip, port, *, relay_model, logger, scd_path=None,
                 ied_name=None):
        self.ip, self.port = ip, port
        self.relay_model, self.logger = relay_model, logger
        self.scd_path, self.ied_name = scd_path, ied_name
        self.fid = self.devid = ""
        self._client = None
        self._map = None
        self._layouts: dict = {}
        self._ld_by_suffix: dict = {}
        self._directory: dict = {}
        self._last_cycle = 0.0
        self._cycles: list = []

    def effective_interval(self, requested: float, last_cycle: float) -> float:
        """Nunca abaixo do piso, e nunca abaixo do que o ciclo custou."""
        return max(requested, MIN_INTERVAL, last_cycle)
```

`connect(job)` associates, reads `LLN0$DC$NamPlt$swRev` and strips the `FID=` prefix into `self.fid`, sets `self.devid` from the common LD prefix, then fills `self._ld_by_suffix` via `ld_suffixes()` and `self._directory` with one `get_logical_device_directory` per LD.

`prepare_bits(names, job)` reads the SCD (when `scd_path` is set) via `sel_short_addresses()[self.ied_name]`, looks up the table via `mms_tables.lookup(part)`, calls `resolve_map(...)`, then fetches one `get_data_definition` per container into `self._layouts`. It returns `len(self._map.points)` and raises when coverage is zero — a live diagram with nothing on it is worse than a clear refusal.

`poll(state, interval, stop, once)` loops: for each `(ld, container)` in `self._map.containers()`, read and `decode_container`, map child names back to bit names, write ints into `state.digitals` under `state.lock`, set `state.last_update_ts`; record the cycle time; then `stop.wait(self.effective_interval(interval, cycle) - cycle)`. On `Iec61850Error`, set `state.error` and return.

`abort()` closes the socket. `close()` closes the client. `coverage()` returns `self._map.coverage(...)` or `None`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_mms_transport.py -q`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add sellib/web/glv/transport/mms.py tests/test_mms_transport.py
git commit -m "Transporte MMS: leitura por LN\$FC, so a pagina aberta, piso de 100 ms"
```

---

### Task 7: Wire the mode through config, handler and diagram

**Files:**
- Modify: `sellib/web/glv/handler.py` (`GlvDefaults`, `_resolve_gle`, `_create_diagram`, `_create_diagrams_batch`, a new `/period` route)
- Modify: `sellib/web/glv/diagram.py` (`GlvDiagram.__init__`, `_connect`, `tab()`, `values()`)
- Modify: `sellib/web/dashboard.py` (read the new config keys)
- Modify: `config/config.ini.example`
- Modify: `requirements.txt`
- Test: `tests/test_glv_scan_mode.py`

**Interfaces:**
- Consumes: `pick_transport`, `SCAN_TELNET`, `SCAN_MMS`, `DEFAULT_PORTS` (Task 1); `MmsTransport` (Task 6).
- Produces: `GlvDiagram` gains `scan_mode: str` and `scd_sha: str | None`; `tab()` gains `"scan_mode"`; `values()` gains `"coverage"`; `POST /period?d=<id>` accepts `{"interval_ms": int}` and returns `{"interval_ms": int}`.

- [ ] **Step 1: Add py61850 to requirements**

In `requirements.txt`, append:

```
py61850>=0.2.0.dev0
```

`app.py` bootstraps from this file, so the dependency will not install without it.

- [ ] **Step 2: Write the failing test**

`tests/test_glv_scan_mode.py`:

```python
"""Scan mode is per diagram, and defaults to telnet.

Per diagram and not global because comparing the two transports side by side
on the same relay is exactly what validating this feature needs. Telnet by
default because it is what people rely on today.
"""
from __future__ import annotations

import pytest

from sellib.web.glv.transport import (
    DEFAULT_PORTS, SCAN_MMS, SCAN_TELNET, pick_transport,
)


def test_default_ports_differ_so_the_pool_cannot_confuse_them():
    """LinkPool keys on ip:port; one relay watched both ways is two entries."""
    assert DEFAULT_PORTS[SCAN_TELNET] == 23
    assert DEFAULT_PORTS[SCAN_MMS] == 102


def test_pick_transport_returns_the_telnet_one_by_default():
    t = pick_transport(SCAN_TELNET, ip="192.0.2.10", port=23,
                       acc_password="OTTER", relay_model=None, logger=None)
    assert t.mode in ("target_region", "tar_digitals", "fast_meter_digitals")


def test_pick_transport_returns_the_mms_one_when_asked():
    t = pick_transport(SCAN_MMS, ip="192.0.2.10", port=102,
                       acc_password="", relay_model=None, logger=None)
    assert t.mode == "mms"


def test_an_unknown_mode_falls_back_to_telnet_rather_than_raising():
    t = pick_transport("bogus", ip="192.0.2.10", port=23,
                       acc_password="OTTER", relay_model=None, logger=None)
    assert t.mode != "mms"
```

- [ ] **Step 3: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_glv_scan_mode.py -q`
Expected: PASS for the first test if Task 1 landed; FAIL on `pick_transport` importing `MmsTransport` if Task 6 has not.

- [ ] **Step 4: Extend `GlvDefaults` and read the config**

In `sellib/web/glv/handler.py`, add to `GlvDefaults`:

```python
    mms_interval_ms: int = 100       # periodo inicial do modo MMS
    scan_mode: str = "telnet"        # padrao da tela de selecao
```

In `sellib/web/dashboard.py`, inside the `GlvDefaults(...)` construction around line 165:

```python
        mms_interval_ms=cfg.getint("web", "glv_mms_interval_ms", fallback=100),
```

In `config/config.ini.example`, under `[web]`, after `glv_setup_timeout`:

```ini
; Periodo inicial do polling por MMS, em milissegundos. O usuario muda na
; propria tela do diagrama; o piso e' 100 ms e vale pra qualquer valor pedido.
; O telnet continua usando [polling] interval_seconds.
glv_mms_interval_ms = 100
```

- [ ] **Step 5: Carry `scan_mode` through diagram creation**

In `handler.py`'s `_create_diagram`, read two more fields and pick the port from the mode:

```python
                scan_mode = str(payload.get("scan_mode") or defaults.scan_mode)
                scd_sha = (str(payload.get("scd_sha") or "").strip() or None)
```

then pass `scan_mode=scan_mode, scd_sha=scd_sha` into `_open_diagram`, and use `DEFAULT_PORTS.get(scan_mode, defaults.port)` instead of `defaults.port`. Do the same in `_create_diagrams_batch`, where each item may carry its own `scan_mode`.

In `diagram.py`, `GlvDiagram.__init__` gains `scan_mode: str = SCAN_TELNET` and `scd_sha=None`, stored as attributes; `tab()` gains `"scan_mode": self.scan_mode`; and `_connect` builds the transport:

```python
            if is_new:
                link.start_connect(acc_password=defaults.acc_password,
                                   relay_model=self.relay_model,
                                   poll_interval=self._poll_interval(defaults),
                                   job=job,
                                   setup_timeout=defaults.setup_timeout)
```

where `RelayLink` is constructed by `LinkPool.acquire(..., transport=...)` using `pick_transport(self.scan_mode, ...)`.

- [ ] **Step 6: Add the period route**

In `handler.py`'s POST dispatch, beside `/connect`:

```python
            if route == "/period":
                d = self._diagram()
                if d is None:
                    self._send_json(404, {"error": "diagrama não encontrado"})
                    return
                body, _ = self._body()
                try:
                    ms = int(json.loads(body or b"{}").get("interval_ms"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    self._send_json(400, {"error": "intervalo inválido"})
                    return
                self._send_json(200, {"interval_ms": d.set_interval_ms(ms)})
                return
```

`GlvDiagram.set_interval_ms(ms)` clamps to `MIN_INTERVAL * 1000` and returns the value actually applied, so the client shows the truth rather than what it asked for.

- [ ] **Step 7: Add coverage to `values()`**

In `diagram.py`'s `values(page)`, after `snap["page_bits_known"]` is computed:

```python
            link = self.link
            cov = link.transport.coverage() if link is not None else None
            snap["coverage"] = None if cov is None else {
                "mapped": sum(1 for b in wanted if b.upper() in cov_points),
                "total": len(wanted),
            }
```

where `cov_points` is `link.transport._map.points` when present. Telnet returns `None`, and the client hides the badge in that case.

- [ ] **Step 8: Run the suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: green.

- [ ] **Step 9: Commit**

```bash
git add sellib/web/glv/handler.py sellib/web/glv/diagram.py \
        sellib/web/dashboard.py config/config.ini.example requirements.txt \
        tests/test_glv_scan_mode.py
git commit -m "GLV: modo de leitura por diagrama, periodo ajustavel e cobertura no /values"
```

---

### Task 8: The two screens

**Files:**
- Modify: `sellib/web/glv/templates/landing.html` (mode radio + SCD select)
- Modify: `sellib/web/glv/templates/dashboard.html` (period field + status strip + coverage badge)

**Interfaces:**
- Consumes: `POST /diagrams` accepting `scan_mode` and `scd_sha`; `POST /period?d=`; `values()` returning `coverage`.
- Produces: no new server interface.

- [ ] **Step 1: Add the mode radio to the selector**

In `landing.html`, above the relay list (near the `#batch-open` button, around line 241), add:

```html
  <fieldset class="scan-mode">
    <legend>Como ler o rel&eacute;</legend>
    <label><input type="radio" name="scan-mode" value="telnet" checked>
      Telnet (Fast Message) &mdash; l&ecirc; a Relay Word inteira</label>
    <label><input type="radio" name="scan-mode" value="mms">
      MMS (IEC 61850) &mdash; n&atilde;o gasta sess&atilde;o telnet; mostra as
      p&aacute;ginas de GOOSE, mas n&atilde;o alcan&ccedil;a todos os bits</label>
    <div id="scd-pick" style="display:none">
      <label for="scd-sel">SCD do projeto:</label>
      <select id="scd-sel"></select>
    </div>
  </fieldset>
```

- [ ] **Step 2: Fill the SCD select and send the mode**

In `landing.html`'s script, when MMS is chosen, list the project's SCDs and keep only those whose IED set contains this relay. Auto-pick when exactly one qualifies:

```javascript
const modeRadios = document.querySelectorAll('input[name="scan-mode"]');
const scdPick = document.getElementById('scd-pick');
const scdSel = document.getElementById('scd-sel');

function scanMode() {
  const on = document.querySelector('input[name="scan-mode"]:checked');
  return on ? on.value : 'telnet';
}

async function refreshScds() {
  if (scanMode() !== 'mms') { scdPick.style.display = 'none'; return; }
  const r = await fetch('/library?kind=scd');
  const files = (await r.json()).files || [];
  scdSel.innerHTML = '';
  files.forEach(f => {
    const o = document.createElement('option');
    o.value = f.sha256;
    o.textContent = f.name;
    scdSel.appendChild(o);
  });
  // Um SCD so: nao ha o que perguntar. Nenhum: cai na tabela de fabrica.
  scdPick.style.display = files.length > 1 ? '' : 'none';
}
modeRadios.forEach(r => r.addEventListener('change', refreshScds));
```

Then extend both POST bodies — the single open around line 487 and the batch around line 521 — to carry `scan_mode: scanMode()` and `scd_sha: scdSel.value || null`.

- [ ] **Step 3: Add the period field and status strip**

In `dashboard.html`'s header, add:

```html
<span class="poll-ctl" id="poll-ctl" hidden>
  <label for="poll-ms">Per&iacute;odo</label>
  <input type="number" id="poll-ms" min="100" step="10" value="100"> ms
  <span id="poll-status" class="poll-status"></span>
</span>
```

- [ ] **Step 4: Drive them from the poll response**

In the script that already polls `/values`, add:

```javascript
const pollCtl = document.getElementById('poll-ctl');
const pollMs = document.getElementById('poll-ms');
const pollStatus = document.getElementById('poll-status');

pollMs.addEventListener('change', async () => {
  const r = await fetch('/period?d=' + encodeURIComponent(activeId), {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({interval_ms: parseInt(pollMs.value, 10)}),
  });
  const d = await r.json();
  // O servidor devolve o que REALMENTE aplicou: pedir menos que o piso, ou
  // menos que o ciclo custa, nao acelera nada -- e a tela nao pode mentir.
  if (d.interval_ms) pollMs.value = d.interval_ms;
});

function renderPollStatus(data) {
  const mms = data.scan_mode === 'mms';
  pollCtl.hidden = !mms;
  if (!mms || !data.coverage) { pollStatus.textContent = ''; return; }
  const c = data.coverage;
  pollStatus.textContent =
    `MMS · ${c.mapped}/${c.total} bits nesta página`;
}
```

Call `renderPollStatus(data)` wherever the existing `/values` response is handled.

- [ ] **Step 5: Verify in a browser**

Run: `python3 app.py --web`, open `http://localhost:8765/arquivos/`, upload the Exemploópolis RDB and SCD, then `http://localhost:8765/glv/novo`.

Check: the mode radio shows Telnet selected; choosing MMS reveals the SCD select only when the project has more than one SCD; opening a diagram in MMS mode shows the period field with 100 and a `MMS · n/m bits nesta página` strip; opening one in Telnet mode hides both.

- [ ] **Step 6: Commit**

```bash
git add sellib/web/glv/templates/landing.html sellib/web/glv/templates/dashboard.html
git commit -m "GLV na tela: escolha do modo, SCD, periodo e cobertura da pagina"
```

---

### Task 9: Write down what was learned

**Files:**
- Modify: `docs/ENGINEERING-NOTES.md`

- [ ] **Step 1: Add the gotchas**

Append to the Gotchas section, in the existing voice — each one a measured fact with its consequence:

- **`sAddr` is the only bridge, and the relay does not serve it.** Verified on a live SEL-451-5 R331: `PLT1GGIO1$DC$Ind01$d` answers `object-non-existent`. So the MMS map comes from a file — the project SCD first, `data/mms_map/` as fallback — and there is no MMS equivalent of `TAR <name>`.
- **The FC comes from the relay, not from SCL templates.** `sAddr` sits on a `DAI` while the functional constraint lives on the `DA` in the `DOType`. Matching `LN$*$DO$DA` against `GetLogicalDeviceDirectory` resolves the FC and verifies the entry at once. `ST` wins over `MX` over `CO` — `LocSta` really is at both `CO` and `ST` on the 487E.
- **MMS reads one `LN$FC` container at a time, and only the open page.** Measured on the 451: 30 req / ~180 ms per `LN$FC` against 170 req / 739 ms per bit; per-DO buys nothing and reading the whole logical device is the worst of all (64 req / 1020 ms). RTT is 3.1 ms, so a cycle is `containers × 3.1 ms` — 3.9 ms on a small page, 37 ms on the LED page, ~180 ms for the whole diagram. Hence the open page only, like the 3xx TAR mode.
- **Two speed-ups are already measured and rejected.** Pipelining is 3× slower (delayed-ACK) despite `maxServOutstanding=3`; `TCP_NODELAY` changes nothing. Do not retry either without new evidence.
- **61850 does not expose the whole Relay Word, and on the 7xx it exposes more than telnet.** SEL publishes SELOGIC *state* (`PLT`/`ALT`/`PSV`/`ASV`) and all I/O at 100 %, and no SELOGIC block *output* (`Q`/`R` suffix). But a 751's Fast Meter DNA carries 1116 digitals and zero `VB`, while its 61850 map has 1903 including all 256 `VB` — so the GOOSE pages only work over MMS. Coverage per model is in `fixtures/gle_sem_61850.txt`; regenerate with `tools/gle_vs_61850.py`.
- **`data/mms_map/` is the third per-model registry**, after `data/relay_models/` and `data/wordbits/`. Same drift risk, same rule: `tests/test_relay_models.py` fails on an orphan unless it is written down.

- [ ] **Step 2: Commit**

```bash
git add docs/ENGINEERING-NOTES.md
git commit -m "docs/ENGINEERING-NOTES.md: o que o modo MMS ensinou, medido"
```

---

## Self-review

**Spec coverage.** Every section of the spec maps to a task: the Transport seam and its four rules → Task 1 (with the three-family gate as Step 9); SCD-first mapping → Tasks 2 and 5; the ICD fallback and the third-registry note → Task 3; the verified layout decode → Tasks 4 and 6; `LN$FC` polling, open page, 100 ms floor and `max(requested, measured_cycle)` → Task 6; per-diagram mode, SCD picker, period field, coverage badge → Tasks 7 and 8; error handling (open-and-disconnected, zero coverage fails) → Tasks 6 and 7; documentation → Task 9. Out-of-scope items (analogs, multi-var read, hybrid, reports) appear in no task, which is correct.

**Known gap, stated rather than hidden.** Task 6 Step 3 describes `connect`, `prepare_bits` and `poll` in prose after showing the module's constants, `decode_container` and `effective_interval` in full. That is deliberate: those three methods are thin orchestration over interfaces defined precisely in Tasks 1, 5 and 6, and the tests in Step 1 pin their observable behaviour. If the implementer wants them spelled out, the tests are the specification.

**Type consistency.** `MmsPoint`/`MmsMap`/`Coverage` are defined in Task 5 and used with the same field names in Task 6. `ScdPoint` fields (`bit`, `ld_inst`, `ln`, `do`, `da`) are identical in Tasks 2 and 5. `Transport`'s method names are the same in Tasks 1, 6 and 7. `prepare_bits` replaces `ensure_bits` everywhere, with an alias kept in Task 1 Step 5 so `diagram.py` need not change until Task 7.

**Ordering.** Tasks 1–3 and 7–9 are offline. Tasks 4–6 need the bench. Task 7 depends on Task 6 for `pick_transport` to import `MmsTransport`; if the bench stays down, implement Tasks 1–3, then stub `MmsTransport` enough for Task 7's import and leave Tasks 4–6 for when hardware returns.
