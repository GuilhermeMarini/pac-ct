"""
VLAN Mapper: from an SCD, list the VLANs each relay (IED) needs enabled
on its switch port.

Per IED, we compute two sets:
  - RX: VLAN-IDs of the GSE Control Blocks the IED subscribes to via
        <ExtRef serviceType="GOOSE" iedName="<publisher>" srcCBName="..."
                srcLDInst="..."> -- resolved against the <GSE> of
        <ConnectedAP iedName="<publisher>"> in <Communication>.
  - TX: VLAN-IDs of the IED'S OWN <GSE> in <Communication> (i.e., GOOSE
        this relay publishes).

Output: HTML page with one row per IED, columns =
  IED | tipo/desc | IP | VLANs (chips) | #RX / #TX

Flow:
  1. The user uploads the SCD.
  2. The app parses it and shows the table.
  3. The "<- Menu" button in the header goes home (same convention as the
     VB Updater).

Shares the page utilities (LANDING + escape + dropzone) with the
VB Updater, but kept in a separate module to keep the session state simple
(SCD only here, no RDB and no matcher).

    templates/  landing.html
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from sellib.scl import read as scd_loader
from sellib.scl.read import GooseSubscription, GseAddress, IedInfo

from pacct.paths import VLAN_MAPPER_TEMPLATES_DIR
from pacct.web.project_files import library as filelib
from pacct.web.session import SessionHandler

_logger = logging.getLogger(__name__)


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (VLAN_MAPPER_TEMPLATES_DIR / name).read_text(encoding="utf-8")


# -----------------------------------------------------------------------------
# Session state
# -----------------------------------------------------------------------------

@dataclass
class _Session:
    scd_path: Path | None = None
    scd_name: str | None = None
    # Cache of the computed payload (revalidated on every upload).
    payload: dict | None = None




# -----------------------------------------------------------------------------
# Computing the IED -> VLANs map
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class IedVlanRow:
    ied_name: str
    ip: str | None
    relay_type: str | None
    description: str | None
    rx_vlans: list[str]   # distinct VLAN-IDs (sorted) the IED receives
    tx_vlans: list[str]   # distinct VLAN-IDs (sorted) the IED publishes
    # vlan_id -> (sorted) list of publisher IEDs that originate GOOSE on
    # that VLAN and are subscribed to by this IED. RX side only -- for TX
    # the publisher is the IED itself (rendering treats it as "self").
    publishers_by_vlan: dict[str, list[str]]
    rx_count: int         # total number of GOOSE subscriptions (a repeated vlan counts)
    tx_count: int         # number of GSE controls published
    unresolved: list[str] # GSEs subscribed but with no entry in <Communication>


def _sort_vlans(values: set[str]) -> list[str]:
    """Sort VLAN-IDs as text, preferring numeric order when all of them
    parse as int (in base 10 or 16)."""
    def key(v: str):
        s = v.strip()
        # Try hex first (VLAN-IDs in SCDs often come in hex).
        for base in (16, 10):
            try:
                return (0, int(s, base))
            except ValueError:
                continue
        return (1, s)
    return sorted(values, key=key)


def compute_ied_vlan_rows(scd_path: Path) -> list[IedVlanRow]:
    """Cross IEDs + GSE communication + GOOSE subscriptions and return a
    list of rows (one per known IED) with RX/TX VLANs.

    IEDs with no subscription and no GSE of their own are included anyway
    (the switch port still carries MMS/Reports/etc. traffic, but within the
    scope of this tool their VLANs come out empty).
    """
    # One parse for the three questions. Through the module-level functions
    # this was three, and on a real 22 MB substation SCD that is where the
    # time went: 1406 ms total, 1104 ms (78%) of it re-parsing the same bytes.
    # One document answers all three in 670 ms.
    #
    # `load()` is the graceful constructor, so a file the visitor uploaded
    # that will not parse gives None and a log line rather than an exception
    # -- the same behaviour the three functions had, and what this route
    # already relies on.
    doc = scd_loader.ScdDocument.load(scd_path)
    if doc is None:
        return []
    ieds: list[IedInfo] = doc.ieds()
    gse_map: dict[tuple[str, str, str], GseAddress] = doc.gse_communication_map()
    subs_by_ied: dict[str, list[GooseSubscription]] = doc.goose_subscriptions_by_ied()

    # Index GSE by publisher for a fast TX lookup.
    gse_by_publisher: dict[str, list[GseAddress]] = {}
    for pub_addr in gse_map.values():
        gse_by_publisher.setdefault(pub_addr.publisher_ied, []).append(pub_addr)

    rows: list[IedVlanRow] = []
    for ied in ieds:
        # RX: resolve each subscription -> VLAN-ID via gse_map.
        rx_set: set[str] = set()
        rx_publishers: dict[str, set[str]] = {}  # vlan_id -> {publisher_ied}
        rx_count = 0
        unresolved: list[str] = []
        for sub in subs_by_ied.get(ied.name, []):
            rx_count += 1
            key = (sub.publisher_ied, sub.src_ld_inst, sub.src_cb_name)
            addr = gse_map.get(key)
            if addr is None and sub.src_ld_inst:
                # Some tools omit ldInst in the GSE but keep it in the ExtRef
                # (or the reverse). Fall back to (publisher, cbName) alone.
                for cand in gse_by_publisher.get(sub.publisher_ied, []):
                    if cand.cb_name == sub.src_cb_name:
                        addr = cand
                        break
            if addr is None or not addr.vlan_id:
                unresolved.append(
                    f"{sub.publisher_ied}/{sub.src_ld_inst or '?'}/{sub.src_cb_name}"
                )
                continue
            rx_set.add(addr.vlan_id)
            rx_publishers.setdefault(addr.vlan_id, set()).add(sub.publisher_ied)

        # TX: VLAN-IDs of this IED's own GSE.
        tx_addrs = gse_by_publisher.get(ied.name, [])
        tx_set: set[str] = set()
        for addr in tx_addrs:
            if addr.vlan_id:
                tx_set.add(addr.vlan_id)

        publishers_by_vlan = {
            vid: sorted(pubs) for vid, pubs in rx_publishers.items()
        }

        rows.append(IedVlanRow(
            ied_name=ied.name,
            ip=ied.ip,
            relay_type=ied.relay_type,
            description=ied.description,
            rx_vlans=_sort_vlans(rx_set),
            tx_vlans=_sort_vlans(tx_set),
            publishers_by_vlan=publishers_by_vlan,
            rx_count=rx_count,
            tx_count=len(tx_addrs),
            unresolved=unresolved,
        ))

    # Sort IEDs by name so the listing is deterministic.
    rows.sort(key=lambda r: r.ied_name)
    return rows


def _row_to_dict(r: IedVlanRow) -> dict:
    return {
        "ied_name": r.ied_name,
        "ip": r.ip or "",
        "relay_type": r.relay_type or "",
        "description": r.description or "",
        "rx_vlans": r.rx_vlans,
        "tx_vlans": r.tx_vlans,
        "publishers_by_vlan": r.publishers_by_vlan,
        "rx_count": r.rx_count,
        "tx_count": r.tx_count,
        "unresolved": r.unresolved,
    }


def _build_payload(scd_path: Path, scd_name: str) -> dict:
    rows = compute_ied_vlan_rows(scd_path)
    # Distinct VLANs across the whole substation (RX+TX) -- useful for UI.
    all_vlans: set[str] = set()
    for r in rows:
        all_vlans.update(r.rx_vlans)
        all_vlans.update(r.tx_vlans)
    return {
        "has_scd": True,
        "scd_name": scd_name,
        "rows": [_row_to_dict(r) for r in rows],
        "ied_count": len(rows),
        "vlan_count": len(all_vlans),
        "all_vlans": _sort_vlans(all_vlans),
    }


def _state_payload(st: _Session) -> dict:
    if st.payload is None:
        return {"has_scd": False, "scd_name": None,
                "rows": [], "ied_count": 0,
                "vlan_count": 0, "all_vlans": []}
    return dict(st.payload)


# -----------------------------------------------------------------------------
# HTML
# -----------------------------------------------------------------------------

LANDING_HTML = load_template("landing.html")

# The numbered navigation is the same on the nine screens -- lives in theme.py.


# -----------------------------------------------------------------------------
# Server
# -----------------------------------------------------------------------------

def build_vlan_mapper_handler(logger: logging.Logger, sessions) -> type:
    """Return the VLAN Mapper handler class.

    Opens no server: serving is done by the single dispatcher of
    `pacct.web.mount`, which mounts this handler at `/vlan-mapper/`. State
    and uploads are per session (`self.sess()` / `self.sdir()`), not per
    process.
    """

    class Handler(SessionHandler):
        session_key = "vlan-mapper"
        state_factory = _Session
        server_sessions = sessions

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, LANDING_HTML, "text/html; charset=utf-8")
                return
            if path == "/vlan-state":
                # Sentinel the home uses to detect that this tool is up.
                self._send_json(200, {"ok": True})
                return
            if path == "/state":
                self._send_json(200, _state_payload(self.sess()))
                return
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path

            if path == "/select-scd":
                # The body of the old /scd-upload, from `load_scd` onward:
                # the file was already received and validated in /files/.
                body = self._read_json_body()
                sha = (body.get("sha256") or "").strip()
                lib = filelib.library_for(sessions, self.require_session())
                with self.require_session().lock:
                    entry = lib.get(sha)
                if entry is None or entry.kind != filelib.KIND_SCD:
                    self._send_json(404, {
                        "error": "Arquivo não está mais no projeto."})
                    return
                job = self.job()
                job.stage("Lendo IEDs e VLANs do SCD", 10)
                try:
                    payload = _build_payload(entry.require_scd_path(),
                                             entry.display_name)
                except Exception as e:
                    logger.exception("falha computando VLAN map: %s", e)
                    self._send_json(500, {
                        "error": f"falha computando VLAN map: {e}"})
                    return
                st = self.sess()
                with self.require_session().lock:
                    st.scd_path = entry.scd_path
                    st.scd_name = entry.display_name
                    st.payload = payload
                job.finish(f"{payload['ied_count']} IED(s), "
                           f"{payload['vlan_count']} VLAN(s)")
                logger.info(
                    "[vlan-mapper] SCD '%s': %d IED(s), %d VLAN(s) distintos",
                    entry.display_name, payload["ied_count"],
                    payload["vlan_count"],
                )
                self._send_json(200, payload)
                return

            self._send(404, "not found", "text/plain")

    return Handler
