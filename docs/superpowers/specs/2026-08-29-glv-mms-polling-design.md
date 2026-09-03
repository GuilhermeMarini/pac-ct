# GLV over MMS — a second transport for the Graphical Logic Viewer

*2026-08-29 — design*

## Problem

The GLV reads a relay one way: SEL Fast Message over telnet, port 23. That path
is well understood and hardware-verified, and it has three costs that no amount
of tuning removes.

- **It spends a scarce resource.** A SEL relay accepts few simultaneous telnet
  sessions — the whole `LinkPool` refcount exists because of it — and it wants
  the ACC password, which is a substation credential.
- **It is slow to start.** A cold FID pays for bit discovery: `MAP 1 TARGET BL`
  on a 4xx, or a `TAR 0..N` sweep on a 3xx that docs/ENGINEERING-NOTES.md measures at ~90 s.
- **On a 7xx it cannot show the GOOSE pages at all, ever.** Digitals arrive in
  the A5D1 Fast Meter DNA block, and that block has no Virtual Bits in it.
  `RelayLink.ensure_bits` already skips anything starting with `VB` with the
  comment *"GOOSE em outra regiao"*. Measured on the bench SEL-751-R402-V2 at
  203.0.113.41: the live reading returns **1116 digitals and zero VB**, while
  its own `GL1.gle` draws 40 VB bits across 10 pages — the `Goose` page is 39 of
  39 VB, `Message_Quality` 10 of 10. Those pages are permanently blank today.

Meanwhile every relay in the corpus already answers on TCP 102: all eight bench
relays probed (411L, 311C, 487E, 751, 451) have both 23 and 102 open.

## Solution

A **second transport**, IEC 61850 MMS over port 102, selectable **per diagram**
on `/glv/novo`, defaulting to Telnet. It writes into the same `LiveState`, so
notes, highlights, the tab strip, `/values` and the SVG bit classes are
untouched.

MMS is **not a replacement**. The two transports see overlapping but different
halves of the relay, and on the 7xx MMS is the larger half:

| SEL-751-R402-V2, measured | bits |
|---|---|
| Telnet Fast Meter DNA | 1 116 |
| 61850 map (`751/010` ∪ `751/011`) | 1 903 |
| in both | 703 |
| telnet only — MMS blind | 413 |
| **MMS only — telnet blind** | **1 200** (incl. all 256 `VB`) |

### Decisions taken up front

| Question | Decision |
|---|---|
| Scan mode | **Per diagram**, radio on `/glv/novo`, **default Telnet**. Port follows the mode (23/102). Lets one tab poll by MMS while another polls the same family by Fast Message — which is what validation needs. |
| Map source | **Project SCD first, shipped ICD table as fallback.** The SCD is the as-built truth. |
| Which SCD | Filter the project's SCDs to those carrying a matching IED (name or IP); **auto-pick when exactly one qualifies**, select when several do. |
| Fallback rule | **Nearest firmware group, then verified against the relay's own directory.** Measured: 99.2 % of the `451/010` map survived that check on an R331 relay, a group that is not in the corpus. |
| Uncovered bits | **Indeterminate (existing `bit-unknown`) plus a per-page coverage badge.** Per page, not per diagram: coverage runs 100 % on GOOSE pages and ~50 % on CS89/LED pages, so a diagram-wide figure would mislead exactly where it matters. |
| Analogs | **Out of scope.** GLE analog symbols (`IAW`, `VAZ`, `VABRMS`) have no `sAddr`; they are `MX` measurements under different names. Building that map has no measured evidence behind it yet, and a wrong phase on a protection relay is worse than showing nothing. |
| Poll granularity | **One read per `LN$FC` container**, open page only. |
| Poll period | User-settable, **hard floor 100 ms**. |
| Structure | **Extract a `Transport` seam**; `TelnetTransport` moves out of `RelayLink` verbatim, `MmsTransport` is new. |

## Measured evidence

All on the bench **SEL-451-5 R331** at 203.0.113.61, workload = that relay's
own `GL1.gle` (25 pages, 249 bits), unless stated. These numbers are the reason
for the decisions above; re-measure before changing any of them.

**Read granularity**, whole GLE (170 of 249 bits are addressable):

| Strategy | requests | time | bytes | rate |
|---|---|---|---|---|
| per-DA (1 read/bit) | 170 | 739 ms | 19 612 | 1.4 Hz |
| per-DO | 170 | 731 ms | 21 431 | 1.4 Hz |
| **per-`LN$FC`** | **30** | **187 ms** | 14 970 | **5.3 Hz** |
| whole LD (64 ANN `ST` containers) | 64 | 1 020 ms | 29 051 | 1.0 Hz |
| multi-var *(needs py61850 work)* | 12 | 81 ms | 9 939 | 12.4 Hz |

The whole-diagram `LN$FC` figure came out 187 ms in this run and 178 ms in a
later one; treat ~180 ms as the number and the spread as ordinary relay jitter.
per-DO is pointless — same round trips as per-DA, more bytes. Polling the whole
logical device is the worst option. `LN$FC` wins because latency dominates: it
trades bytes (it pulls `q` and `t` alongside `stVal`) for round trips.

**Latency floor.** Single-read RTT **3.1 ms median** (min 2.65, p90 3.5–6.8),
stable across runs, with occasional spikes to **90 ms**. Cycle time is
essentially `containers × 3.1 ms`, so it is per page: 3.9 ms for a 1-container
page, 37 ms for `LED_s` (8 containers), 68 ms for `SCADA`, **~180 ms for the
whole diagram**. Hence: poll the open page, and treat the floor as a rolling
measurement rather than a constant.

**Two speed-ups measured and rejected.** Pipelining is **3× slower** despite the
relay advertising `maxServOutstanding=3` — 3 reads go 14.5 → 52.1 ms, the +40 ms
signature of a delayed-ACK stall. `TCP_NODELAY` makes no difference (9.06 vs
8.85 ms per read on one socket with the option flipped); py61850 never sets it
and it does not matter here. Do not re-litigate either without new evidence.

**Coverage**, GLE-drawn variables with no 61850 address, from
`tools/gle_vs_61850.py` over the Exemploópolis R0j RDB+SCD (27 relays):

`missing` is the **union across the relays of that model** (they draw nearly
the same logic); `GLE vars` and `covered` are **per relay**, shown as a range
where the relays differ.

| Model | relays | GLE vars/relay | missing (union) | covered/relay |
|---|---|---|---|---|
| 411L-A | 2 | 385 | 161 | 58 % |
| 487E-3 | 4 | 409–507 | 163 | 66–70 % |
| 451-5 | 3 | 249–349 | 103 | 68–70 % |
| 751 | 14 | 232–265 | 84 | 79–84 % |
| 311C-1 | 2 | 181 | 46 | 75 % |

The misses cluster into the same families every time: target LEDs (`T##_LED`),
pushbuttons (`PB##`, `PB##_PUL`), `89*` disconnector logic, SELOGIC block
outputs carrying a `Q`/`R` suffix, and the `*TC` / `UL*` / `TR` trip-conditioning
bits. **The rule: SEL exposes SELOGIC *state* (latches, variables) and all I/O
at 100 %, and exposes no SELOGIC *block output*.** Full lists live in
`fixtures/gle_sem_61850.txt` and `fixtures/model_missing.txt`.

## Architecture

### The Transport seam

`diagram.py` already talks to a link through twelve members — `start_connect`,
`ready`, `error`, `connected`, `state`, `key`, `fid`, `devid`, `mode`, `owners`,
`set_wanted_bits`, `ensure_bits` — of which only `ensure_bits` and the
`acc_password` kwarg carry any telnet flavour. That seam exists already; this
makes it explicit.

```
sellib/web/glv/
  link.py              RelayLink (lifecycle shell) + LinkPool + TooManyLinks
  transport/
    __init__.py        Transport protocol + pick_transport()
    telnet.py          TelnetTransport   — moved verbatim out of link.py
    mms.py             MmsTransport      — new; owns MmsClient and its poll loop
  poll.py              the three telnet poll loops — untouched
  mms_map.py           bit -> (ld, item) resolution + coverage accounting
data/mms_map/<PART>-<GROUP>.json    shipped ICD fallback tables
```

```python
class Transport(Protocol):
    mode: str          # target_region | tar_digitals | fast_meter_digitals | mms
    fid: str
    devid: str
    def connect(self, job) -> None: ...        # raises; sets fid/devid
    def abort(self) -> None: ...               # break a hung connect (watchdog)
    def close(self) -> None: ...
    def prepare_bits(self, names, job) -> int: ...
    def poll(self, state, interval, stop, once) -> None: ...   # runs until stop
    def coverage(self): ...                    # MmsTransport only; None for telnet
```

`RelayLink` keeps identity, `owners`, `ready`, `LiveState`, the wanted-bits
union, `info()`, the watchdog and the poll **thread**. `LinkPool` keys on
`ip:port`, and MMS is 102 against telnet's 23, so the two never collide and one
relay viewed both ways correctly gets two pool entries; the pool gains a
transport argument and nothing else.

### Rules the extraction must obey

The telnet path is hardware-verified and was moved into `glv/` once already
"sem uma linha de mudança". Risk here is proportional to logic **changed**, not
code **moved**.

1. **`TelnetTransport` methods move verbatim** — `setup_relay`,
   `_setup_ascii_reader`, `_reindex`, `ensure_bits`, `_log_fast_meter_digitals`,
   the `poll_loop*` dispatch. No logic edits.
2. **The lifecycle shell stays byte-identical.** `owners` as a set of diagram
   ids (a double click must not close the telnet under a live diagram),
   `abandon()` (a hung setup returns its slot), `release()` popping inside the
   lock (a concurrent `acquire()` must not join a closing link) — three recorded
   bug fixes, all concurrency, all in this code.
3. **`abort()` is a transport method, never a generic timeout.** Telnet aborts a
   hung login by *closing the socket*, threading an `on_socket` callback into
   `setup_relay`, because selprotopy swallows the exception and retries — nothing
   else wakes it. py61850 has a real socket timeout. Collapsing the two into
   "rely on the transport's timeout" deletes the property that makes telnet
   recover, and would pass a happy-path test.
4. **One deliberate logic change, and only one.** `ensure_bits` today stops and
   restarts the poll thread itself; since the shell now owns the thread, that
   moves to the shell. Behaviour-identical, but not a verbatim move. It applies
   to MMS too: py61850's client is explicitly not thread-safe (one socket, one
   invoke counter), so a second diagram joining a live MMS link must also pause
   polling while its bits are added.

**Acceptance gate for the refactor:** reconnect all three telnet families on the
bench — 411L (`target_region`, 203.0.113.22), 311C (`tar_digitals`, .23), 751
(`fast_meter_digitals`, .41) — and confirm the same bit counts as before. No MMS
work merges until that passes.

## Map resolution

The Relay Word name reaches 61850 only through the SCL attribute
`sAddr="db:NAME"`. That attribute is **not served over MMS** — verified on the
451, where `PLT1GGIO1$DC$Ind01$d` answers `object-non-existent` — so the map can
only come from a file. There is no MMS equivalent of the telnet path's
`TAR <name>` discovery, and there never will be.

**The FC comes from the relay, not from SCL type templates.** `sAddr` sits on a
`DAI`, but the functional constraint lives on the `DA` inside the `DOType`, so
building `LN$FC$DO$DA` the ordinary way means resolving the whole type-template
chain — which `sellib/parsers/scd.py` does not do today. It is unnecessary: the
relay publishes every fully-qualified name itself (12 735 for the 451's ANN
alone), so matching `lnName$*$DO$DA` against `GetLogicalDeviceDirectory` yields
the FC *and* verifies the entry in one step. Where a DO/DA appears under two FCs
— `LocSta` is at both `CO` and `ST` on the 487E — prefer `ST`, then `MX`.

`MmsTransport.connect()` associates on 102 and reads
`LLN0$DC$NamPlt$swRev`, which returns `FID=SEL-451-5-R331-V1-Z033014-D20250919`,
so the FID cache key and `NoteStore.adopt_devid` keep working. The IED name is
the longest common prefix of the LD names (`QPC1_TFE_UPC1` + `ANN/CFG/CON/MET/PRO`),
which needs no SCD.

`prepare_bits()` then:

1. `get_server_directory()` → LDs; derive `suffix -> full LD` from that prefix.
2. `get_logical_device_directory()` per LD → the authoritative variable set.
3. **Source A, the project SCD**: `sAddr` on the `DAI` gives
   `bit -> (ldInst, lnName, DO, DA)`, read straight off `LN`/`DOI`/`DAI`.
4. **Source B, the shipped ICD table** for the nearest group, which already
   carries FC.
5. Resolve FC against the directory; drop anything it does not confirm.
6. `get_data_definition` once per container → the child-name layout.

**The layout decode is verified.** `decode_data_definition` on `PLT1GGIO1$ST`
returns an ordered structure — `Beh`, `Ind01`…`Ind32`, each a struct of
`stVal`/`q`/`t` — matching the 33-element list the read returns. Checked across
`PLT1GGIO1$ST`, `ALT1GGIO1$ST` and `VB1XGGIO1$ST`, with 18 spot-checked bits all
equal to their individual reads.

Connect costs 1 + 5 + ~30 requests, about 0.2 s — against minutes for a cold
telnet FID.

The SCD choice happens at diagram-open on `/glv/novo`, not at connect, because
it may need a human.

## Poll loop and the period control

The read plan is built once at connect: the open page's bits grouped by
`(ld, LN$FC)`, each with its decoded layout. Each cycle reads every container
and walks the layout positionally — no name lookups on the hot path. MMS uses
`LiveState.wanted_bits` exactly as the 3xx TAR mode does, and for the same
reason: the whole diagram is ~180 ms, the open page is 4–68 ms.

- **Field in the diagram header**: period in ms, default and **hard floor
  100 ms** (10 Hz — 5× telnet's 0.5 s, and a load a protection relay will not
  notice).
- **Effective period is `max(requested, measured_cycle)`.** If a page ever costs
  more than the requested period the loop runs continuously and says so rather
  than pretending.
- **The reported floor is a rolling median** of recent cycles, not a one-time
  calibration, because of the 90 ms spikes.

Status strip beside the field:

```
MMS · 8 req/ciclo · último 37 ms · mediana 36 ms · 27 Hz · próximo em 63 ms · 28/54 bits
```

New config key `[web] glv_mms_interval_ms` (default 100) for the initial value;
`[polling] interval_seconds` remains telnet's.

## Error handling

Failures follow the telnet contract exactly: the diagram stays **open and
disconnected** with the reason on the badge.

- Association refused / host unreachable → `sem conexão com <ip>:102`.
- No map for the model → MMS mode is offered but connect fails naming the model,
  rather than producing a diagram that is silently all-grey.
- **Zero coverage fails the connect.** A live diagram with nothing on it is worse
  than a clear refusal.
- Partial coverage is normal and never an error — it is what the badge is for.
- A read error mid-poll sets `state.error` and stops the loop, as telnet does.

## Testing

Offline, as pytest — the map layer is pure functions over recorded data:

- SCD `sAddr` extraction → `bit -> (ldInst, ln, DO, DA)`.
- FC resolution against a **recorded** `GetLogicalDeviceDirectory` listing.
- Nearest-group selection and the directory-verification drop.
- Read-plan grouping (bits → containers).
- Layout alignment against a recorded `get_data_definition` plus a recorded read.
- Coverage arithmetic, per page.

Fixtures to capture from the 451 while the bench is up: one LD directory
listing, a few data definitions, one read response.

Needs a relay: association, the live poll, and the three-family telnet
regression that gates the refactor.

## Out of scope

- **Analog symbols** — see the decisions table.
- **The multi-variable read.** 2 requests / 12 ms for a page against 8 / 37 ms,
  but it belongs in py61850 and is blocked behind the COTP bug below.
- **A hybrid MMS+telnet diagram.** It is the only thing that would show a 751's
  `VB` pages *and* its `SC##QU` counters, but it means two connections per
  diagram, two failure states on one badge, and competition for the relay's
  scarce telnet sessions. Revisit once MMS mode has been used in anger.
- **Reports (BRCB/URCB)**, which would replace polling entirely. py61850 lists
  them as planned.

## For py61850

Four findings, measured here, none worked around in this repo.

1. **`CotpTransport.send()` never fragments.** It always emits one DT TPDU with
   EOT set, while the CR negotiates `tpdu_size=0x0a` (1024 B); `recv()` *does*
   reassemble, so it is asymmetric. Measured on the 451: a 938 B request works,
   a 1038 B request makes the relay **drop the association with no MMS error**.
   This is what caps batch size.
2. **The negotiated limits are parsed by nobody.** `connect()` discards the
   Initiate-Response (`localDetailCalled=12000`, `maxServOutstanding=3`) and
   `CotpTransport.connect()` ignores the CC's TPDU size — raising the *proposed*
   `tpdu_size` to 0x0c changed nothing, the relay still capped at 1024. So no
   caller can size a request safely.
3. **`build_read` is single-variable only.** `listOfVariable` is a
   `SEQUENCE OF SEQUENCE`; N entries in one Read work fine once encoded properly
   (verified: 18 values in one response). There is also no `variableListName`
   form for reading a named DataSet, and no `object_class` parameter on
   `get_logical_device_directory` to enumerate DataSets at all.
4. **`TCP_NODELAY` is never set** — measured as not mattering here, but worth a
   deliberate decision rather than an omission.

## Open items for the maintainer

- **Repo size.** The ICD fallback tables are 9.0 MB for all 50 entries, or
  **1.7 MB** for the parts in this corpus (411L, 451, 487E, 311C, 751, 2414,
  2440). Recommendation: ship the 1.7 MB. Separately `fixtures/` is untracked
  and holds a 25 MB `wordbits.json` plus the ICD corpus — decide before
  committing.
- **A third registry.** `data/mms_map/` joins `data/relay_models/` and
  `data/wordbits/`. docs/ENGINEERING-NOTES.md already documents those two drifting, guarded by
  `test_the_two_model_registries_agree_or_say_why_not`; extend that test to
  three rather than let the problem recur.
- **`py61850` must go into `requirements.txt`** — `app.py` bootstraps from it and
  will not install the dependency otherwise.
