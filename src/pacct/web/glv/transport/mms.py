"""The GLV's MMS transport: it reads the open page in one BATCHED Read.

Measured on the bench SEL-451-5 R331, with the relay's own GL1.gle, back when
each reading cost one request:

    per bit        170 req / 739 ms      per DO   170 req / 731 ms
    per LN$FC       30 req / ~180 ms     whole LD  64 req / 1020 ms

That is where the read per `LN$FC` came from: the RTT is 3.1 ms and the cycle
came out as `containers x 3.1 ms`. What was missing was the MMS multi-variable
Read -- ONE request naming SEVERAL variables --, which py61850 started to
offer in `read_refs` (pin dcbc20c). With it the cycle is no longer counted in
containers: it is `ceil(request bytes / max_pdu_size)`. Measured on the three
bench relays -- 751 (.41, R402: 1055 bits/194 containers), 487E (.31, R323:
784/122) and 451-5 (.62, R331: 1422/207) -- by page SHAPE, which is what
changes everything here: a drawing asks for BITS, and they land scattered
across the `LN$FC`. Per LN$FC -> batch:

                             751 (7xx)          487E (4xx)        451 (4xx)
    scattered,  80 bits   190 ->  5.8 (33x)   214 -> 25 (8.7x)  298 -> 22 (14x)
    scattered, 200 bits   242 -> 13.7 (18x)   376 -> 58 (6.4x)  437 -> 53 (8.2x)
    whole map             400 ->  72  (5.6x)  612 -> 320 (1.9x) 948 -> 435 (2.2x)
    concentrated, 8 cont.  10.7 ->  5.9 (1.8x)  42 -> 63 (0.7x)   36 -> 53 (0.7x)

The last line is the only one where the batch LOSES, and only on the two 4xx:
a page whose bits fit in 8 containers becomes a request of ~230 names (~10 kB,
fragmented into 1024 B TPDUs) against 8 short reads. The numbers on both sides
are below the polling's 100 ms floor, so the loop sleeps the same either way
-- and the real page is the scattered one (80 bits land in 50 containers on
the 451), where the difference is an order of magnitude.

The values agree: on the three relays, the map's 1055, 784 and 1422 bits come
out IDENTICAL by either path (zero differences). In an earlier measurement, 2
of the 1055 differed -- SV36/SV36T, which move on their own between two turns
of the SAME path.

The batch is of LEAVES (`LN$FC$DO$DA`), never of containers. py61850 budgets
the REQUEST against the negotiated `max_pdu_size`, and the RESPONSE is not in
that sum: 16 containers already answer ~11.2 kB against a 12000 ceiling, and
32 containers come back `MmsError: service/3`. A boolean leaf answers ~6
bytes, so a batch of leaves never gets near the ceiling -- 1055 of them fit in
5 requests.

The open page is still the filter (`state.wanted_bits`), now bit by bit and no
longer container by container: before, asking for ONE bit dragged in the other
30 of its `LN$FC`. The whole map would even fit in one turn on the 751
(72 ms, below the 100 ms floor), but not on the 487E (320 ms) -- and reading
what the screen does not show helps nobody on any relay.

Two things measured and DISCARDED, so nobody tries them again: pipelining is
3x SLOWER (the signature is delayed-ACK) even with the relay announcing
maxServOutstanding=3, and TCP_NODELAY changes nothing.

py61850's client is a socket and an invoke counter: it is NOT thread-safe, and
that is why there are never two threads reading the same `MmsClient`.
`prepare_bits` still takes the shell's `pause` by the `Transport` contract,
but today it does NOT talk to the relay any more: the map comes from the
directory read at connect, and the containers' structure stopped being read
once the reading became per leaf.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from py61850 import MmsClient
from py61850.errors import Iec61850Error
from selfiles.scl import mms_tables
from selfiles.scl.mms_tables import decode_bit
from selfiles.scl.read import sel_short_addresses

from pacct.web.glv.mms_map import ld_suffixes, resolve_map
from pacct.web.glv.poll import FirstTimeLog
from pacct.web.glv.transport import MODE_MMS, drawing_variables

# There is no period floor: asking for 0 ms reads as fast as the relay
# answers. What keeps the link from flooding is not a hand-picked number -- it
# is the shape of the loop. The read is SYNCHRONOUS and there is exactly one
# per turn, and `effective_interval` never returns less than the previous turn
# cost, so the next request only leaves after the previous one was answered:
# there is never more than one request in flight per link, and the worst case
# is 100% occupancy with the RELAY setting the pace. That is what "no floor"
# can mean without turning into a flood.
#
# The one minimum period left is that of the turn that did NOT talk to the
# relay: a page with no mapped bit, or an exception that returns immediately.
# There is no answer to wait for there, and period 0 would be a hot loop
# burning a CPU without touching the network. It is not a communications
# period; it is how often the loop looks to see whether there is already
# something to read.
IDLE_INTERVAL = 0.050

# py61850's socket deadline. It covers the association AND every read -- it is
# what spares this transport the telnet trick of closing the socket to wake a
# stuck read.
SOCKET_TIMEOUT = 10

# The item that carries the FID. It is `DC` (description) and not `ST`: NamPlt
# is a nameplate-data DO, not a state.
SW_REV_ITEM = "LLN0$DC$NamPlt$swRev"

_logger = logging.getLogger(__name__)


class MmsSetupError(RuntimeError):
    """The relay answered, but it does not serve this diagram.

    It is not an `Iec61850Error`: py61850 did its own job without any failure.
    What is missing belongs to the domain -- no logical device announced, or
    no bit of the drawing with an MMS address. Calling that a protocol error
    would send the user hunting the network for a project-file problem.
    """


def _part_from(relay_model, fid: str) -> str:
    """The part that names the fallback table: `SEL-451` -> `451`.

    The model comes first because it is what the RDB asserts; the FID is plan
    B, and carries the part in its second field (`SEL-451-5-R331-...`).
    """
    model = getattr(relay_model, "model", "") or ""
    if model:
        return model.replace("SEL-", "").replace("sel-", "")
    parts = (fid or "").split("-")
    return parts[1] if len(parts) > 1 else ""


def _model_label(relay_model, fid: str) -> str:
    """How to NAME the relay in an error message.

    The spec asks that the refusal for a missing map "names the model". The
    RDB is the best source, the FID the second, and the part extracted from
    one of the two the third -- "this relay" on its own tells nobody what to
    look for nor what to send the manufacturer.
    """
    model = getattr(relay_model, "model", "") or ""
    if model:
        return model
    if fid:
        return f"FID {fid}"
    part = _part_from(relay_model, fid)
    return f"peça {part}" if part else "modelo desconhecido"


class MmsTransport:
    """MMS/IEC 61850 over TCP 102. Implements the same `Transport` as telnet.

    What it does NOT do, deliberately: discover a bit name on the relay. The
    Relay Word name lives in the SCL's `sAddr`, which the relay does not serve
    -- there is no equivalent of `TAR <name>` here. The two sources are the
    project SCD and the factory table, and both are checked against the
    relay's directory.
    """

    mode = MODE_MMS

    def __init__(self, ip, port, *, relay_model=None, logger=None,
                 scd_path=None, ied_name=None):
        self.ip, self.port = ip, port
        self.relay_model, self.logger = relay_model, logger or _logger
        self.scd_path, self.ied_name = scd_path, ied_name
        self.key = f"{ip}:{port}"
        self.fid = self.devid = ""
        self._client = None
        self._map = None
        self._ld_by_suffix: dict = {}
        self._directory: dict = {}      # suffix -> LD names (what resolve_map reads)
        self._directory_by_ld: dict = {}
        self._lds: list = []
        self._wanted: set = set()
        self._last_cycle = 0.0
        self._cycles: list = []
        self._lock = threading.RLock()
        # The read plan published to the polling thread: the tuple of
        # `MmsPoint` that becomes the list of `(ld, item)` pairs for
        # `read_refs`. It is an object swapped WHOLE under `self._lock`; the
        # thread only READS it (an attribute reference, atomic), and never
        # takes the lock -- taking it would deadlock against `pause()`, which
        # waits for that very thread to die.
        self._plan: tuple = ()
        # How far the watchdog deadline reaches, same as telnet. Here the
        # boundary is the association: the directory sweep that follows is ONE
        # `GetNameList` per LD (~12,735 names in the 451's ANN alone) and
        # nobody timed it. Without this event the 60s deadline covered the
        # sweep as well, and blowing it there told the user the relay "did not
        # answer", pointing at the network when the problem would be the size
        # of the directory. py61850 has a real socket deadline
        # (`SOCKET_TIMEOUT`), so the sweep is not left unprotected -- it is
        # left with the right protection.
        self.setup_done = threading.Event()

    def effective_interval(self, requested: float, last_cycle: float) -> float:
        """The requested period, never below what the previous turn cost.

        There is no floor beyond that, and it is this `last_cycle` that makes
        the missing floor safe: the cycle already includes the relay's answer,
        so asking for 0 ms does not stack requests -- it pins the loop to the
        relay's own pace, with one request in flight at a time.
        """
        return max(requested, last_cycle, 0.0)

    # -- conexao ------------------------------------------------------------

    def connect(self, job=None) -> None:
        """Associates, identifies the relay and reads each LD's directory.

        The directory is what checks the map: it is where each `LN$*$DO$DA`'s
        FC comes from (see `mms_map.resolve_map`). One read per LD, once per
        connection -- it is the expensive part, and this is where it belongs,
        with the job in hand to say what is going on.
        """
        if job:
            job.stage("Associando com o relé (MMS, porta 102)...", 10)
        client = MmsClient(self.ip, self.port, timeout=SOCKET_TIMEOUT)
        client.connect()
        with self._lock:
            self._client = client
        # Associated: from here on the watchdog deadline no longer rules.
        self.setup_done.set()

        lds = list(client.get_server_directory())
        if not lds:
            raise MmsSetupError(
                "o relé não anunciou nenhum logical device; "
                "confira se o servidor 61850 está habilitado")
        self._lds = lds
        # `commonprefix` on a single LD just returns its own name -- which is
        # right: with no second name to compare against there is nothing to
        # split off.
        self.devid = (os.path.commonprefix(lds).rstrip("_") if len(lds) > 1
                      else lds[0])

        self.fid = self._read_fid(client, lds)

        if job:
            job.stage("Lendo o diretório dos logical devices...", 40)
        for i, ld in enumerate(lds):
            self._directory_by_ld[ld] = list(
                client.get_logical_device_directory(ld))
            if job:
                job.fraction(f"Diretório de {ld}...", i + 1, len(lds))
        self.logger.info(
            "[glv] %s: MMS associado, FID=%s, %d LD(s), %d nomes no diretório",
            self.key, self.fid or "?", len(lds),
            sum(len(v) for v in self._directory_by_ld.values()))

    def _read_fid(self, client, lds) -> str:
        """`LLN0$DC$NamPlt$swRev` -> `SEL-451-5-R331-V1-Z033014-D20250919`.

        The relay answers with the `FID=` prefix attached; the rest of the
        project keeps the FID without it (that is how the `AsciiTargetReader`
        cache is named), so we strip it here and not in whoever reads it.

        Any LD will do -- LLN0 exists in all of them -- but not every LD
        answers, so we try until one does.
        """
        for ld in lds:
            try:
                value = client.read_value(ld, SW_REV_ITEM)
            except Iec61850Error:
                continue
            if isinstance(value, bytes):
                value = value.decode("latin-1")
            if isinstance(value, str) and value:
                return value[4:] if value.startswith("FID=") else value
        self.logger.warning("[glv] %s: nenhum LD respondeu %s; seguindo sem FID",
                            self.key, SW_REV_ITEM)
        return ""

    def abort(self) -> None:
        """Closes the socket to lift a stuck association.

        py61850's socket deadline already covers the normal case; this is so
        that a caller on another thread (the `RelayLink` watchdog) does not
        have to wait out the whole deadline.
        """
        client = self._client
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def unreachable(self, bits):
        """The names with no point in the map -- what the server model lacks.

        A name only enters the map when the relay's own directory confirms it
        (see `resolve_map`), so what is left here is precisely what the IED
        does not publish: either it is not in the model, or it is there under
        another instance name (measured on the 487E: the table asks for
        `IT01PTOC1`, the relay publishes `IT1PTOC46`).

        `None` before there is a map, for the same reason as `coverage_for`.
        """
        if self._map is None:
            return None
        cov = self._map.coverage(drawing_variables(bits))
        return {"names": list(cov.missing), "reason": "mms"}

    def coverage_for(self, bits):
        """How many of THESE bits this transport can read, and from what.

        The bits are those of the open PAGE, and not the link's accumulated
        union: coverage runs close to 100% on a GOOSE page and close to 50% on
        a CS89/LED one, and the union would grow with every new diagram even
        if this page in particular is poorly covered.

        `None` before there is a map -- there is nothing to report, and zero
        would be a lie (it would sound like "nothing mapped" when it is "not
        known yet").
        """
        if self._map is None:
            return None
        cov = self._map.coverage(bits)
        return {"mapped": cov.mapped, "total": cov.total,
                "source": self._map.source}

    # -- descoberta ---------------------------------------------------------

    def _sources(self):
        """`(SCD points, factory table)` -- the map's two sources.

        Neither of them talks to the relay: the SCD is a project file and the
        table is embedded data. What checks them is the directory, already
        read.
        """
        scd_points: dict = {}
        if self.scd_path:
            try:
                by_ied = sel_short_addresses(self.scd_path)
            except Exception as e:
                self.logger.warning("[glv] %s: SCD ilegível (%s); seguindo só "
                                    "com a tabela de fábrica", self.key, e)
                by_ied = {}
            scd_points = self._points_for_ied(by_ied)
        table = mms_tables.lookup(_part_from(self.relay_model, self.fid))
        if table is None:
            self.logger.info("[glv] %s: sem tabela de fábrica pra essa peça; "
                             "só o SCD responde pelo mapa", self.key)
        return scd_points, table

    def _points_for_ied(self, by_ied) -> dict:
        """Which IED of the SCD this relay is.

        Explicit wins; an SCD with a single IED has no ambiguity; failing
        that we match by DEVID, which is the LDs' prefix. With no match, using
        no SCD at all is better than using the neighbouring relay's.
        """
        if not by_ied:
            return {}
        # The name comes from the RDB (`GlvDiagram.relay_name`) and the SCD
        # is from another tool: the two are not obliged to spell the relay the
        # same way. So it is a HINT, not a verdict -- returning `{}` on a name
        # that does not match made the two heuristics below dead code on the
        # live path, and a single name mismatch degraded, with nothing but a
        # log line, this branch's headline decision: "the project SCD first,
        # because it is the truth as built".
        #
        # The name PRESENT in the SCD, that one is a verdict: if the IED is
        # there and has no sAddr at all, its map really is empty -- falling
        # through to the heuristics there would fetch the neighbouring relay's
        # map.
        if self.ied_name:
            if self.ied_name in by_ied:
                return by_ied[self.ied_name]
            self.logger.warning(
                "[glv] %s: IED '%s' não está no SCD; tentando casar por outro "
                "caminho antes de desistir do SCD do projeto",
                self.key, self.ied_name)
        if len(by_ied) == 1:
            name, points = next(iter(by_ied.items()))
            self.ied_name = name
            return points
        # The DEVID is the LDs' common prefix, that is, the relay naming
        # itself. The match is by PREFIX, in both directions, and not by
        # substring: with `name in self.devid` an IED called "TR1" would match
        # the DEVID "QPC1_TR1_UPC1" -- and also "QPC2_TR1_UPC1", the
        # neighbouring relay. While this heuristic was dead code that never
        # showed up; now that the RDB name is only a hint, it is genuinely
        # reachable.
        devid = (self.devid or "").upper()
        for name, points in by_ied.items():
            up = (name or "").upper()
            if devid and up and (devid.startswith(up) or up.startswith(devid)):
                self.ied_name = name
                return points
        self.logger.warning(
            "[glv] %s: o SCD tem %d IEDs e nenhum casa com '%s'; seguindo só "
            "com a tabela de fábrica", self.key, len(by_ied), self.devid)
        return {}

    def prepare_bits(self, names, job=None, pause=None) -> int:
        """Resolves the diagram's bits against the directory read at connect.

        Returns how many bits got resolved NOW -- that is the `Transport`
        contract, and it is what a second diagram joining a live connection
        has to say: what IT added.

        `pause` exists by the `Transport` contract and is NOT used here: ever
        since the read became per leaf, nothing in this function talks to the
        relay. What it consults -- `_directory_by_ld` -- was read in one go at
        connect, and before it there was the structure lookup for each
        `LN$FC`, which was the only reason to stop the reader. A second
        diagram joining now costs neither a request nor a lost polling turn.
        """
        with self._lock:
            client = self._client
            if client is None:
                return 0
            wanted = {b.upper() for b in names if b and not b.isdigit()}
            before = set(self._map.points) if self._map else set()
            self._wanted |= wanted
            if wanted <= before:
                self.logger.info(
                    "[glv] %s: todos os %d bits do diagrama já estão no mapa "
                    "MMS", self.key, len(wanted))
                return 0

            if job:
                job.stage(f"Mapeando {len(self._wanted)} bits para itens MMS...",
                          60)
            scd_points, table = self._sources()
            if not scd_points and table is None:
                # With NEITHER source there is no map to build. The
                # refusal for zero coverage would even fire further down, but
                # saying "no bit has an MMS address ON THIS RELAY" -- which
                # sends the user looking on the relay for a problem that is in
                # a file. The spec asks for the refusal that NAMES the model;
                # this is it. The two absences are different and send the user
                # to different places. Telling someone who has JUST associated
                # an SCD that "no SCD was associated" sends them looking again
                # for what is already there; what is missing in that case is
                # an `sAddr` for this IED inside it.
                if self.scd_path:
                    why = (f"o SCD escolhido ({os.path.basename(str(self.scd_path))}) "
                           f"não traz endereço 61850 (sAddr) para este relé "
                           f"-- ou o IED não está nele, ou está sem sAddr")
                    fix = ("Confira o IED no SCD, escolha outro SCD na tela "
                           "de seleção, ou use o modo telnet.")
                else:
                    why = "nenhum SCD do projeto foi associado a este diagrama"
                    fix = ("Informe o SCD na tela de seleção, ou use o modo "
                           "telnet.")
                raise MmsSetupError(
                    f"não há mapa 61850 para "
                    f"{_model_label(self.relay_model, self.fid)}: não existe "
                    f"tabela de fábrica para essa peça e {why}. {fix}")
            # `ld_suffixes` NEEDS the suffixes the sources name. Without
            # them it falls back to a common prefix, and two LDs sharing more
            # than the IED name (`...ANN` and `...CON`) resolve to the wrong
            # one -- silently, with the whole map pointing at the next LD.
            suffixes = {p.ld_inst for p in scd_points.values() if p.ld_inst}
            if table is not None:
                suffixes |= {suf for suf, _ in table.bits.values() if suf}
            self._ld_by_suffix = ld_suffixes(self._lds, suffixes=suffixes)
            self._directory = {
                suf: self._directory_by_ld.get(ld, ())
                for suf, ld in self._ld_by_suffix.items()
            }

            self._map = resolve_map(
                wanted=self._wanted, directory=self._directory,
                ld_by_suffix=self._ld_by_suffix, scd_points=scd_points,
                table=table)
            # The refusal is judged over the bits THIS diagram asked for,
            # not over the union accumulated on the link. `_wanted` never
            # shrinks when a diagram disconnects, so judging by the union
            # would let a second, 100% unaddressable diagram come up live and
            # empty, riding on the first one's coverage.
            cov = self._map.coverage(self._wanted)
            cov_call = self._map.coverage(wanted)
            if not cov_call.mapped:
                raise MmsSetupError(
                    f"nenhum dos {cov_call.total} bits deste diagrama tem "
                    f"endereço MMS neste relé. Um diagrama conectado e vazio é "
                    f"pior que uma recusa: use o modo telnet, ou informe o SCD "
                    f"do projeto para este IED.")

            # Publishes the plan to the polling thread. Without this it
            # would keep reading the plan from the FIRST call and the second
            # diagram's bits would stay indeterminate until somebody
            # reconnected.
            #
            # Sorted by (LD, item), and not in the order the bits came out of
            # a `set`: py61850 batches the pairs AS THEY COME, so a list that
            # alternates LD spends one extra request at every batch boundary
            # -- and a plan that is stable between runs makes the log and the
            # network capture give the same sequence twice.
            self._plan = tuple(sorted(self._map.points.values(),
                                      key=lambda p: (p.ld, p.item)))

            added = len(set(self._map.points) - before)
            self.logger.info(
                "[glv] %s: mapa MMS por %s -- %d/%d bits (%.0f%%), %d "
                "container(es), +%d agora",
                self.key, self._map.source or "nenhuma fonte", cov.mapped,
                cov.total, 100 * cov.fraction, len(self._map.containers()),
                added)
            if cov.missing:
                self.logger.info("[glv] %s: sem endereço MMS: %s", self.key,
                                 ", ".join(cov.missing[:20])
                                 + (" ..." if len(cov.missing) > 20 else ""))
            return added

    # -- polling ------------------------------------------------------------

    def poll(self, state, interval, stop, once) -> None:
        """Reads the open page's bits until `stop`, one batch per turn.

        An empty `state.wanted_bits` means the whole map (the diagram has not
        said yet which page is on screen); with bits, we ask for exactly
        those. It is the same rule as `poll_loop_tar`, and the filter is now
        per BIT: with the read per container, a single requested bit dragged
        in the other 30 of its `LN$FC`.

        A relay error ends the loop: reopening is the `RelayLink`'s job, and a
        reader insisting on a dead socket only fills the log.
        """
        if once is None:
            once = FirstTimeLog(self.logger)
        while True:
            if stop is not None and stop.is_set():
                return
            client = self._client
            if client is None:
                return         # `close()` from another thread; nothing to read
            # Re-read every turn, and not captured before the loop:
            # `prepare_bits` swaps this object when a second diagram joins,
            # and there is not always a pause to restart the thread. It costs
            # one attribute read against a 3.1 ms RTT.
            plan = self._plan
            t0 = time.monotonic()
            # Did this turn actually talk to the relay? With no period
            # floor, that is what tells a cycle whose cost IS the wait on the
            # relay (and pays for itself) from one that came back free and
            # needs `IDLE_INTERVAL`.
            talked = False
            try:
                with state.lock:
                    wanted = set(state.wanted_bits)
                points = [p for p in plan
                          if not wanted or p.bit in wanted]
                digitals: dict = {}
                expected = len(points)
                # The requested leaves, WITHOUT repeats: a decorated point
                # (`db:52A|52B?0:1:2:3`) puts two bits on the same
                # `LN$FC$DO$DA`, and naming the leaf twice only spends TPDU
                # bytes. Order preserved -- the plan already comes sorted by
                # (LD, item) so as not to spend one extra request at every
                # batch boundary.
                refs: list = []
                seen: set = set()
                for p in points:
                    ref = (p.ld, p.item)
                    if ref not in seen:
                        seen.add(ref)
                        refs.append(ref)
                # ONE Read naming every leaf of the page -- py61850 splits
                # it into as many requests as the negotiated `max_pdu_size`
                # demands and returns the values ALREADY decoded, in the order
                # they were asked for. That is why the `zip` is safe:
                # `read_refs` promises one answer per pair, always.
                values = dict(zip(refs, client.read_refs(refs), strict=True))
                talked = bool(refs)
                for p in points:
                    value = values.get((p.ld, p.item))
                    # A failed access comes back as `{"error": ...}` IN
                    # PLACE of the value, and not as an exception: a name this
                    # relay does not serve drops the bit from the reading
                    # without taking the other thousand down.
                    # `int(bool({...}))` would be 1 -- a bit painted on that
                    # nobody read, on the screen of whoever is commissioning.
                    if value is None or isinstance(value, dict):
                        continue
                    if p.rule is not None:
                        # The item carries more than one bit and the value
                        # is an enumeration, not a boolean: a Dbpos comes back
                        # from py61850 as the STRING "10", and `bool("00")` is
                        # True. `decode_bit` returns `None` on a value that
                        # matches no alternative -- a Dbpos 3 (bad-state)
                        # against a `?1:2` point --, and then the bit leaves
                        # the payload like any reading that did not happen.
                        reading = decode_bit(p.rule, value)
                        if reading is None:
                            continue
                        digitals[p.bit] = reading
                        continue
                    digitals[p.bit] = int(bool(value))

                # A partial read has to show, as in TAR mode: a bit that
                # did not arrive vanishes from the payload and the diagram
                # paints it indeterminate. In commissioning "I could not read
                # it" and "the relay does not know" are very different things.
                err = ("" if len(digitals) >= expected else
                       f"leitura parcial: {len(digitals)}/{expected} bits")
                with state.lock:
                    state.error = err
                    if expected:
                        state.digitals = digitals
                    state.mark_updated()
                if digitals:
                    once.info("mms_first",
                              f"[poll] 1a leitura MMS ok: {len(digitals)} bits "
                              f"em lote, {1000 * (time.monotonic() - t0):.0f} ms")
            except Iec61850Error as e:
                with state.lock:
                    state.error = f"MMS: {e}"
                self.logger.warning("[glv] %s: erro MMS no polling: %s",
                                    self.key, e)
                return
            except Exception as e:
                with state.lock:
                    state.error = f"poll: {e}"
                self.logger.warning("[glv] %s: erro no polling MMS: %s",
                                    self.key, e)

            cycle = time.monotonic() - t0
            self._last_cycle = cycle
            sleep_for = max(0.0, self.effective_interval(interval, cycle) - cycle)
            if talked:
                self._record_cycle(cycle)
            else:
                # Nothing was asked of the relay (a page with no mapped
                # bit) or the error came back immediately: there is no network
                # cost to measure, and at period 0 this branch would be a hot
                # loop.
                sleep_for = max(sleep_for, IDLE_INTERVAL)
            if stop is None:
                time.sleep(sleep_for)
            elif stop.wait(timeout=sleep_for):
                return

    def _record_cycle(self, cycle: float) -> None:
        """Keeps the last cycles and summarises one every 100.

        The cycle cost is the only number that justifies this transport's
        whole design (read per `LN$FC`, only the open page); measuring it in
        the field is what says whether the bench measurement holds up in the
        substation.
        """
        self._cycles.append(cycle)
        if len(self._cycles) < 100:
            return
        ordered = sorted(self._cycles)
        self.logger.info(
            "[glv] %s: 100 ciclos MMS -- mediana %.0f ms, pior %.0f ms",
            self.key, 1000 * ordered[len(ordered) // 2], 1000 * ordered[-1])
        self._cycles.clear()
