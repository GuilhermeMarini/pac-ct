"""One open diagram: a rendered GLE that may or may not be connected.

It is what used to be the CLASS attributes of `DashboardHandler` -- one
diagram per process. Now there are N, each with its own pair (GLE + relay).

A diagram is born disconnected: `build_diagram()` never touches the network.
Connecting is `connect_async()`, which fires a thread because the bit
discovery of the `AsciiTargetReader` on an uncached FID takes minutes and
cannot hold the HTTP response. A failed connection does NOT bring the diagram
down: it stays open and disconnected, with the reason on the badge.
"""

from __future__ import annotations

import re
import threading

from selfiles.gle import parse_gle, render_page

from pacct.web.glv.connectors import extract as extract_connectors
from pacct.web.glv.connectors import nets_on_page as connectors_on_page
from pacct.web.glv.gle_pages import (
    collect_analog_symbols_per_page,
    collect_bit_names,
    collect_bits_per_page,
    list_pages,
)
from pacct.web.glv.link import TooManyLinks
from pacct.web.glv.notes import NOTES, note_key
from pacct.web.glv.state import LiveState
from pacct.web.glv.transport import SCAN_MMS, SCAN_TELNET, pick_transport
from pacct.web.progress import REGISTRY, JobReporter

# Status of a diagram, what the dot on the tab shows.
IDLE = "idle"
CONNECTING = "connecting"
LIVE = "live"
ERROR = "error"


class GlvDiagram:
    """A rendered GLE + notes + (maybe) a RelayLink."""

    def __init__(self, diagram_id: str, *, relay_name: str, gle_name: str,
                 gle_path, ip: str, port: int, relay_model, logger,
                 scan_mode: str = SCAN_TELNET, scd_sha: str | None = None,
                 scd_path=None):
        self.id = diagram_id
        self.relay_name = relay_name
        self.gle_name = gle_name
        self.gle_path = gle_path
        self.ip = ip
        self.port = port
        self.relay_model = relay_model
        self.logger = logger
        self.title = f"{relay_name} · {gle_name}"
        # Read mode chosen on the selection screen, per diagram -- it is what
        # allows telnet and MMS to be compared side by side on the same relay.
        # `scd_sha` is what the client sent; `scd_path` (resolved by the
        # handler, which has the session and the project library in hand --
        # this object does not) is the real path the MMS transport reads the
        # SCD from.
        self.scan_mode = scan_mode
        self.scd_sha = scd_sha
        self.scd_path = scd_path
        # Explicit period override, asked for through `/period`. Kept whether
        # or not the link is alive: it is what makes a request issued while
        # "conectando" (or already disconnected) count on the next connect()
        # instead of being dropped silently -- `_poll_interval` reads it first.
        self._interval_ms: int | None = None

        self.pages_meta: list = []
        # The page this diagram is showing. It lives HERE and not in the
        # browser because the diagram list is already the server's: switching
        # tabs fetches `/meta?d=` and re-renders, and without this `meta()`
        # always returned the initial page -- whoever was on page 7 of a
        # 12-page GLE came back to the first one on every trip between two
        # relays. Kept per diagram, so it holds after an F5 as well.
        self.open_page: str = ""
        self.svgs: dict = {}
        self.bits_per_page: dict = {}
        self.analogs_per_page: dict = {}
        self.analog_groups_meta: dict = {}
        self.var_index: dict = {}
        self.all_wanted_bits: set = set()
        # The connector networks of the whole GLE (`label -> ConnectorNet`).
        # A connector is not an edge in the XML: it is a NAME that two ends
        # share, and without this the signal dies on it. See `connectors.py`.
        self.connectors: dict = {}

        self.notes = NOTES.get(note_key(relay_name))
        # What you see disconnected: empty, or the reason of the last failure.
        self.idle = LiveState()
        self.link = None
        self.status = IDLE
        self.error = ""
        # One job per diagram. It used to be a fixed id ("glv-session"), with
        # the comment "there is only one GLV session".
        self.job_id = f"glv-connect-{diagram_id}"
        self._lock = threading.RLock()
        # Generation of the connection attempt. Disconnecting or closing
        # increments it, which invalidates whichever attempt is in flight:
        # without it, closing the diagram while it connects would let the
        # thread finish and hold the connection forever -- `self.link` is
        # still None in that window, so `disconnect()` has nothing to release.
        self._gen = 0

    # -- displayed state ----------------------------------------------------

    @property
    def state(self) -> LiveState:
        """The LiveState the screen should show.

        Connected, it is the RelayLink's (shared with the other diagrams on
        the same relay). Disconnected, it is `idle`, which is empty -- that is
        how disconnecting puts everything back to indeterminate without
        clearing a single dictionary.
        """
        link = self.link
        return link.state if link is not None else self.idle

    @property
    def connected(self) -> bool:
        """There IS a link AND it is still reading.

        Both halves matter. A link that GAVE UP (the reading ended on its own
        -- a dropped MMS association does not come back by itself -- and
        `_poll_gave_up` marked the link as not connected) is still hanging
        here, because releasing it is the pool's business and the pool is with
        whoever holds `pool`. While this answered just `self.link is not
        None`, the tab stayed LIVE with "Desconectar" after the error, and the
        only way out was to disconnect before reconnecting.
        """
        link = self.link
        return link is not None and link.connected

    # -- connection ---------------------------------------------------------

    def connect_async(self, pool, defaults) -> str:
        """Fires the connection on a thread and returns the job id.

        It cannot block the response: on an uncached FID the AsciiTargetReader
        takes minutes building the name -> (row, bit) map. What follows it is
        the progress bar, through the job `glv-connect-<id>`.
        """
        dead = None
        with self._lock:
            link = self.link
            if link is not None and not link.connected:
                # A link that gave up cannot block a reconnection: it is not
                # reading anything. Without this the button said "Conectar"
                # (because `connected` already tells the truth) and did
                # nothing, and the user had to go through Desconectar first.
                dead, self.link = link, None
            elif link is not None or self.status == CONNECTING:
                return self.job_id
            self.status = CONNECTING
            self.error = ""
            self.idle.clear()
            self._gen += 1
            gen = self._gen
        if dead is not None:
            # Outside `_lock`: `release` may close the link.
            pool.release(dead, self.id)
            self.logger.info("[glv] diagrama %s: link %s tinha desistido; "
                             "soltei antes de reconectar", self.id, dead.key)
        threading.Thread(
            target=self._connect, args=(pool, defaults, gen), daemon=True,
            name=f"glv-connect-{self.id}",
        ).start()
        return self.job_id

    def _cancelled(self, gen: int) -> bool:
        with self._lock:
            return gen != self._gen

    def _poll_interval(self, defaults) -> float:
        """Initial polling period: the `/period` override if there already is
        one (asked for while the diagram was still connecting, or between two
        connections -- it must not be dropped silently), otherwise the MMS
        mode's ([web] glv_mms_interval_ms) for a diagram on MMS, otherwise the
        usual telnet one."""
        if self._interval_ms is not None:
            return self._interval_ms / 1000
        if self.scan_mode == SCAN_MMS:
            return defaults.mms_interval_ms / 1000
        return defaults.poll_interval

    def _make_transport(self, *, ip, port, acc_password, relay_model, logger):
        """The factory `LinkPool` calls in `connect()`: the mode chosen on
        THIS diagram's selection screen, and not a process-wide default -- it
        is what lets the same relay be watched over telnet and over MMS at
        the same time, in different tabs."""
        return pick_transport(self.scan_mode, ip=ip, port=port,
                              acc_password=acc_password,
                              relay_model=relay_model, logger=logger,
                              scd_path=self.scd_path, ied_name=self.relay_name)

    def _connect(self, pool, defaults, gen: int) -> None:
        job = JobReporter(self.job_id)
        link = None
        try:
            job.stage("Pedindo conexão com o relé...", 4)
            try:
                link, is_new = pool.acquire(self.ip, self.port, self.id,
                                            make_transport=self._make_transport)
            except TooManyLinks as e:
                self._fail(str(e), job)
                return
            if is_new:
                link.start_connect(acc_password=defaults.acc_password,
                                   relay_model=self.relay_model,
                                   poll_interval=self._poll_interval(defaults),
                                   job=job,
                                   setup_timeout=defaults.setup_timeout)
            else:
                # Another diagram already opened (or is opening) this relay.
                job.stage("Entrando na conexão existente...", 40)
            # Waiting on `ready` (instead of calling connect directly) is what
            # allows disconnecting or closing halfway: this thread never gets
            # stuck inside selprotopy. The wait has no deadline because bit
            # discovery on an uncached FID takes minutes of its own accord --
            # what has a deadline is the setup, through the link's watchdog.
            while not link.ready.wait(timeout=2.0):
                if self._cancelled(gen):
                    self._abandon(link, pool, job)
                    return
            if link.error or not link.connected:
                reason = link.error or "conexão não ficou pronta"
                pool.release(link, self.id)
                link = None
                self._fail(reason, job, gen)
                return
            if self._cancelled(gen):
                self._abandon(link, pool, job)
                return
            job.stage("Localizando bits do diagrama...", 70)
            link.ensure_bits(self.all_wanted_bits, job=job)
            # Discovery takes minutes on an uncached FID: look again.
            if self._cancelled(gen):
                self._abandon(link, pool, job)
                return
            # First connection: adopt whatever was written under the DEVID.
            self.notes.adopt_devid(link.devid, self.logger)
            with self._lock:
                if gen != self._gen:
                    link_to_drop, link = link, None
                    self._abandon(link_to_drop, pool, job)
                    return
                self.link = link
                self.status = LIVE
                self.error = ""
            job.finish(f"Conectado ({link.fid or link.key})")
            self.logger.info("[glv] diagrama %s ao vivo em %s (modo %s)",
                             self.id, link.key, link.mode)
        except Exception as e:      # never takes the thread or diagram down
            if link is not None:
                pool.release(link, self.id)
            self._fail(f"falha ao conectar: {e}", job, gen)

    def _abandon(self, link, pool, job) -> None:
        """The user disconnected or closed while this was connecting."""
        pool.release(link, self.id)
        job.finish("Conexão cancelada")
        REGISTRY.drop(self.job_id)
        self.logger.info("[glv] diagrama %s: conexao com %s abandonada "
                         "(desconectado no meio)", self.id, link.key)

    def _fail(self, reason: str, job=None, gen: int | None = None) -> None:
        with self._lock:
            if gen is not None and gen != self._gen:
                # Already disconnected: do not paint red a diagram the user
                # deliberately left at rest.
                self.logger.info("[glv] diagrama %s: %s (ignorado, tentativa "
                                 "abandonada)", self.id, reason)
                return
            self.link = None
            self.status = ERROR
            self.error = reason
            # The badge reads values.error and paints it red, prefix "ERRO:".
            self.idle.error = reason
        if job:
            job.fail(reason)
        self.logger.warning("[glv] diagrama %s: %s", self.id, reason)

    def disconnect(self, pool) -> None:
        """Releases the link and puts the diagram back to indeterminate."""
        with self._lock:
            link, self.link = self.link, None
            self.status = IDLE
            self.error = ""
            # Invalidates the attempt in flight, if any: it is what makes the
            # connection thread release the reference instead of attaching it
            # to a diagram the user has already told to stop.
            self._gen += 1
            # The screen can never keep showing the old reading.
            self.idle.clear()
        if link is not None:
            pool.release(link, self.id)
            self.logger.info("[glv] diagrama %s desconectado de %s",
                             self.id, link.key)
        REGISTRY.drop(self.job_id)

    def close(self, pool) -> None:
        """Closes the diagram: releases the connection and drops the SVGs."""
        self.disconnect(pool)
        self.svgs.clear()
        self.logger.info("[glv] diagrama %s fechado", self.id)

    def _current_interval_ms(self, defaults=None) -> int:
        """The period in force now, or the one that would take effect on the
        next connect(): the live link's if there is one, otherwise this
        session's last override, otherwise -- when `defaults` is passed -- the
        same computation as `_poll_interval(defaults)` (the MMS mode default
        or the usual telnet one), and only in the absence of all that, 0.

        The `defaults=None` matters: `set_interval_ms` calls this to return
        the period on a REFUSAL (telnet) without touching anything, and on
        that path the value never reaches the screen (the control does not
        even exist for telnet). `tab()`/`meta()`, on the other hand, call it
        with `defaults` in hand, because an MMS diagram with no override at
        all also needs a true number in the field -- a 0 there would be a
        placeholder lying that there is no period at all, when in fact there
        is one (the mode default) that will apply as soon as reading starts."""
        link = self.link
        if link is not None:
            return int(round(link.poll_interval * 1000))
        if defaults is not None:
            return int(round(self._poll_interval(defaults) * 1000))
        return self._interval_ms if self._interval_ms is not None else 0

    def set_interval_ms(self, ms: int) -> dict:
        """Applies (or defers, or refuses) a new polling period.

        The route is MMS only. On MMS the loop is one synchronous batched read
        per turn and the cycle itself sets the pace (see
        `transport.mms.effective_interval`), so any period asked for --
        including 0 -- at most pushes the loop up against the relay's speed.
        The telnet modes have no such guarantee written down, nor a bench to
        measure it now: 4xx and 7xx talk Fast Message over several round trips
        per turn, and the 3xx floors at 1.5 s inside its own transport because
        a `TAR <row>` costs ~200 ms ON THE RELAY. Tightening that blind from a
        text box is the kind of thing that only shows up as a slow relay
        during commissioning. So a telnet diagram is REFUSED, and gets back
        the period that was already in force, with nothing touched.

        The three possible answers, for the client to tell apart:
          "aplicado"  -- there was a reading running now, and it already uses
                         the new period from the next cycle on.
          "adiado"    -- MMS, but with no reading running NOW (connecting,
                         disconnected, or a zombie reading that has not died
                         yet -- see `RelayLink.set_poll_interval`). The value
                         is kept (`self._interval_ms`) and `_poll_interval`
                         uses it on the next connect() or restart; it is not
                         dropped silently.
          "recusado"  -- not MMS. Nothing changed.

        The only adjustment made to the value asked for is the cut at 0: a
        negative period means nothing, and would become a negative `sleep`
        further down the line.
        """
        if self.scan_mode != SCAN_MMS:
            return {
                "interval_ms": self._current_interval_ms(),
                "status": "recusado",
                "reason": "o período só é ajustável no modo MMS; este "
                         "diagrama está em telnet",
            }
        ms = max(int(ms), 0)
        self._interval_ms = ms
        link = self.link
        applied = link.set_poll_interval(ms / 1000) if link is not None else False
        if applied:
            return {"interval_ms": ms, "status": "aplicado", "reason": ""}
        return {
            "interval_ms": ms,
            "status": "adiado",
            "reason": "sem leitura ativa agora; vale a partir da próxima "
                     "leitura",
        }

    # -- payloads for the client ---------------------------------------------

    def tab(self, defaults=None) -> dict:
        """What the tab strip needs to know.

        `defaults` is optional (the None default keeps whoever still calls
        without it) but the handler always has one in hand and should pass it:
        it is what makes `interval_ms` the REAL period of this tab --
        including the MMS/telnet mode default when there has been no /period
        and no connection yet -- instead of 0, which would lie "no period at
        all". Without it, switching between two MMS diagrams with different
        periods left the field showing the previous tab's value."""
        link = self.link
        status, error = self.status, self.error
        if link is not None and not link.connected:
            # The reading stopped on its own. `self.status` still says LIVE
            # because what found out was the polling thread, which does not
            # touch the diagram -- so the translation happens here, when
            # telling the screen, and the reason comes from the link.
            status = ERROR
            error = link.error or "a leitura parou"
        return {
            "id": self.id,
            "title": self.title,
            "relay": self.relay_name,
            "gle": self.gle_name,
            "ip": self.ip,
            "port": self.port,
            "status": status,
            "error": error,
            "connected": self.connected,
            "refs": len(link.owners) if link is not None else 0,
            "fid": link.fid if link is not None else "",
            "scan_mode": self.scan_mode,
            "interval_ms": self._current_interval_ms(defaults),
        }

    def default_page(self) -> str:
        """The page for whoever never opened one: the SECOND, when it exists.

        The first page of a QuickSet GLE is a cover/index in almost every file
        of the corpus; the second is the first one with logic drawn on it.
        """
        if len(self.pages_meta) > 1:
            return self.pages_meta[1][1]
        return self.pages_meta[0][1] if self.pages_meta else ""

    def remember_page(self, safe_id: str) -> None:
        """Records which page the visitor opened. A string assignment, with no
        `_lock`: there is no composite state to keep coherent here."""
        if safe_id and safe_id in self.svgs:
            self.open_page = safe_id

    def meta(self, defaults=None) -> dict:
        """All a tab switch needs to re-render without reloading the page."""
        d = self.tab(defaults)
        # The open page beats the initial one -- it is what brings the tab
        # back where it was. A recorded page that no longer exists (GLE
        # swapped under the same diagram) falls back to the initial one
        # instead of opening empty.
        initial = (self.open_page if self.open_page in self.svgs
                   else self.default_page())
        d.update({
            "pages": self.pages_meta,
            "initial": initial,
            "var_index": self.var_index,
            "analog_groups": self.analog_groups_meta,
            "notes_key": self.notes.key,
        })
        return d

    def values(self, page: str) -> dict:
        """Snapshot filtered by the open page (was the handler's /values)."""
        snap = self.state.snapshot()
        if page and page in self.bits_per_page:
            wanted = set(self.bits_per_page[page])
            # The bits that DRIVE this page's connectors. The emitting end
            # may be on one page and the drive on another (measured: 9
            # networks of the corpus cross pages), and then nothing on this
            # page asks for those bits -- the connector would stay
            # indeterminate forever. It costs <=10 bits per connector (median
            # 10, maximum 10 in the corpus), and they are ALREADY in
            # `all_wanted_bits`, so the MMS map covers them: what was missing
            # was the per-page filter narrowing them back down.
            page_nets = connectors_on_page(self.connectors, page)
            for net in page_nets.values():
                wanted |= set(net.bits)
            # TAR mode (3xx): tells the poll what is worth reading. With two
            # diagrams on the same relay, the link publishes their UNION.
            link = self.link
            if link is not None:
                link.set_wanted_bits(self.id, wanted)
            else:
                self.idle.set_wanted_bits(wanted)
            # Case-insensitive index of the bits we have from the relay
            ci_digitals = {k.upper(): v for k, v in snap["digitals"].items()}
            # Returns ALL the page's bits: value 0/1 when known, or null
            # (indeterminate) when it is not in the relay's map.
            snap["digitals"] = {
                bit: ci_digitals.get(bit, None) for bit in sorted(wanted)
            }
            snap["page"] = page
            # Structure, not value: what EVALUATES the tree is `evaluatePage`,
            # with the same primitives it already uses for the drawn blocks.
            # Evaluating here would put NOT/RTRIG/latch in two languages.
            snap["connectors"] = {
                label: {"label": net.label, "equation": net.equation,
                        "tree": net.tree, "bits": sorted(net.bits),
                        "driver_page": net.driver_page}
                for label, net in page_nets.items()
            }
            snap["page_bits_total"] = len(wanted)
            snap["page_bits_known"] = sum(
                1 for v in snap["digitals"].values() if v is not None)
            # Coverage of the open PAGE. What answers is the transport,
            # through the seam -- this used to be a
            # `getattr(link.transport, "_map")`, which is the diagram reaching
            # into the private of a transport it does not even know which is.
            # Telnet returns None and the client hides the badge, instead of
            # showing zero (which would read as "nothing mapped" when the
            # right answer is "does not apply").
            transport = getattr(link, "transport", None) if link else None
            snap["coverage"] = (transport.coverage_for(wanted)
                                if transport is not None else None)
        # Analogs: if the page has an analog map (a relay_model with
        # analog_groups configured), we deliver { NAME: {value, group} },
        # where value=null means "the relay does not expose that channel"
        # (rendered as N/A). Otherwise we leave snap["analogs"] raw to keep
        # compatibility with models that have no analog_groups.
        if page and page in self.analogs_per_page:
            wanted_an = self.analogs_per_page[page]
            ci_analogs = {k.upper(): v for k, v in snap["analogs"].items()}
            # Resolves the alias when the relay exposes the channel under
            # another name in the Fast Meter (e.g. SEL-487E: IAS -> IA1).
            # With no aliases, it is the identity.
            resolve = (self.relay_model.resolve_analog_name
                       if self.relay_model is not None else (lambda nm: nm))
            snap["analogs"] = {
                nm: {"value": ci_analogs.get(resolve(nm)), "group": wanted_an[nm]}
                for nm in sorted(wanted_an)
            }
            snap["analog_groups"] = self.analog_groups_meta
        snap["status"] = self.status
        snap["connected"] = self.connected
        return snap

    def unreachable(self) -> dict:
        """The variables of the WHOLE DRAWING this connection cannot reach.

        Of the whole drawing, and not of the open page -- unlike the coverage
        on the status strip, which is per page on purpose. The two numbers
        answer different questions: the coverage says how much of what is on
        screen is live, and this list exists to build the IED's server model,
        where walking page by page to collect the names is exactly the work it
        saves.

        `available: False` when there is no way to know (disconnected, no map,
        no DNA): a `0` on a screen nobody connected would read as "it is all
        on the relay".
        """
        total = len(self.all_wanted_bits)
        link = self.link
        transport = getattr(link, "transport", None) if link is not None else None
        out = (transport.unreachable(self.all_wanted_bits)
               if transport is not None else None)
        if not out:
            return {"available": False, "names": [], "count": 0,
                    "total": total, "reason": "", "scan_mode": self.scan_mode}
        names = sorted(out.get("names") or [])
        return {"available": True, "names": names, "count": len(names),
                "total": total, "reason": out.get("reason", ""),
                "scan_mode": self.scan_mode}

    def debug_analogs(self) -> dict:
        """Raw names/values the relay exposes over Fast Meter, with no page
        filter and no alias. Use it to check against analog_name_aliases."""
        raw = self.state.snapshot().get("analogs", {})
        resolved = {}
        if self.relay_model is not None:
            all_names: set = set()
            for pg_map in self.analogs_per_page.values():
                all_names.update(pg_map.keys())
            ci_raw = {k.upper(): v for k, v in raw.items()}
            for gle_name in sorted(all_names):
                fm = self.relay_model.resolve_analog_name(gle_name)
                resolved[gle_name] = {
                    "fm_key": fm,
                    "value": ci_raw.get(fm.upper()),
                    "in_fm": fm.upper() in ci_raw,
                }
        return {"fm_keys": sorted(raw.keys()), "fm_n": len(raw),
                "gle_resolved": resolved}


def build_diagram(diagram_id: str, gle_path, relay_name: str, gle_name: str,
                  ip: str, port: int, relay_model, logger, *,
                  scan_mode: str = SCAN_TELNET, scd_sha: str | None = None,
                  scd_path=None) -> GlvDiagram:
    """Builds a diagram WITHOUT touching the network: parse, render, indexes,
    notes.

    Runs in ~1 s. Connecting is another matter, and comes later, when the user
    asks for it.
    """
    d = GlvDiagram(diagram_id, relay_name=relay_name, gle_name=gle_name,
                   gle_path=gle_path, ip=ip, port=port,
                   relay_model=relay_model, logger=logger,
                   scan_mode=scan_mode, scd_sha=scd_sha, scd_path=scd_path)

    logger.info("[glv] carregando GLE: %s", gle_path)
    gle_root = parse_gle(gle_path)
    d.pages_meta = list_pages(gle_root)
    for p in gle_root.findall(".//page"):
        name = p.get("name", "")
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", name) or f"page_{len(d.svgs)}"
        d.svgs[safe] = render_page(p, relay_model=relay_model)
    logger.info("  %d paginas renderizadas", len(d.svgs))

    d.bits_per_page = collect_bits_per_page(gle_root, relay_model=relay_model)
    d.analogs_per_page = collect_analog_symbols_per_page(gle_root, relay_model)
    gle_bits = collect_bit_names(gle_root, relay_model=relay_model)

    # Collects the derived outputs TOO (PLT04, PCT03Q, AST01Q, ...). The
    # patterns come from the model JSON:
    #   LATCH (PLT)  -> "PLT{instance:02d}"   -> PLT04
    #   TIMER (PCT)  -> "PCT{instance:02d}Q"  -> PCT03Q
    #   AST          -> "AST{instance:02d}Q"  -> AST01Q
    #   PSV          -> "PSV{instance:02d}"   -> PSV05
    # With no model loaded, we generate no derived bits.
    derived_bits: set = set()
    if relay_model is not None:
        for p in gle_root.findall(".//page"):
            for el in p.findall(".//element"):
                xml_type = el.get("type") or ""
                le = el.find("logic_element")
                if le is None:
                    continue
                try:
                    instance = int(le.get("physical_instance_number") or 0)
                except ValueError:
                    instance = 0
                name = le.get("physical_instance_name") or ""
                bit = relay_model.derived_bit_for(xml_type, instance, name)
                if bit:
                    derived_bits.add(bit)
    d.all_wanted_bits = set(b.upper() for b in gle_bits) | derived_bits

    # The connector networks. They add no bit at all to `all_wanted_bits`:
    # what drives a connector is already drawn on SOME page of the GLE, so it
    # is already there. What they change is the filter by open page, in
    # `values()`.
    d.connectors = extract_connectors(gle_root, relay_model=relay_model)
    if d.connectors:
        logger.info(
            "  %d rede(s) de conector: %s", len(d.connectors),
            ", ".join(f"{lab} ({len(n.emitters)} saida(s), {len(n.bits)} bit(s))"
                      for lab, n in sorted(d.connectors.items())))

    if relay_model is not None and relay_model.analog_groups:
        d.analog_groups_meta = {g.key: g.label for g in relay_model.analog_groups}

    # Global index of variables -> pages, used by the header's search box:
    # digital (bits + derived outputs) and analog (FM channels). Key in
    # UPPER; value: ordered list of safe_page_ids where the variable appears.
    var_index: dict = {}
    for safe_id, names in d.bits_per_page.items():
        for nm in names:
            ent = var_index.setdefault(nm, {"kind": "bit", "pages": []})
            if safe_id not in ent["pages"]:
                ent["pages"].append(safe_id)
    for safe_id, names_map in d.analogs_per_page.items():
        for nm in names_map.keys():
            # If it already existed as a bit (rare: a reused name), keep it as
            # a bit but record the page; otherwise it stays analog.
            ent = var_index.setdefault(nm, {"kind": "analog", "pages": []})
            if safe_id not in ent["pages"]:
                ent["pages"].append(safe_id)
    d.var_index = var_index

    logger.info(
        "  %d bits do GLE (%d com derivados), %d analogicos em %d familia(s); "
        "notas na chave %r",
        len(gle_bits), len(d.all_wanted_bits),
        sum(len(v) for v in d.analogs_per_page.values()),
        len(d.analog_groups_meta), d.notes.key)
    return d
