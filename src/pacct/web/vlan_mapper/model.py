"""The IED -> VLAN computation, and the payload the routes answer with.

Imports nothing from `pacct.web`: the whole tool is a question about one SCD
and the answer is a dict. Nothing here knows a request exists, which is what
lets the same computation be driven from a test (`tests/test_scd.py` calls
`compute_ied_vlan_rows` directly) or, later, from something that is not a web
server at all.

Per IED, two sets are computed:
  - RX: VLAN-IDs of the GSE Control Blocks the IED subscribes to via
        <ExtRef serviceType="GOOSE" iedName="<publisher>" srcCBName="..."
                srcLDInst="..."> -- resolved against the <GSE> of
        <ConnectedAP iedName="<publisher>"> in <Communication>.
  - TX: VLAN-IDs of the IED'S OWN <GSE> in <Communication> (i.e., GOOSE
        this relay publishes).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from py61850.scl import ControlBlockAddress, IedHeader, SclDocument
from sellib.scl.read import SelGooseSubscription, sel_goose_subscriptions

# -----------------------------------------------------------------------------
# Session state
# -----------------------------------------------------------------------------

@dataclass
class VlanMapperState:
    """What one visitor has chosen. Per session, never per process."""

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
    # Two libraries read this one document and neither re-opens it. The
    # standard half -- the IEDs, their addresses, the GSE control blocks --
    # is `py61850.scl`'s and is not SEL's in any way. What sellib adds is the
    # SEL half: the `pubRxStatus` health bit joined onto each subscription,
    # which lives in a `<Private>` block and nowhere a general reader looks.
    #
    # `load()` is the graceful constructor, so a file the visitor uploaded
    # that will not parse gives None and a log line rather than an exception
    # -- the same behaviour the three functions had, and what this route
    # already relies on.
    doc = SclDocument.load(scd_path)
    if doc is None:
        return []
    ieds: dict[str, IedHeader] = doc.ied_headers
    ip_by_ied: dict[str, str] = doc.communication.ip_by_ied()
    # GSE only: an `SMV` shares the key shape and is a sampled-value stream,
    # not a GOOSE publication, so a subscription must never resolve onto one.
    gse_map: dict[tuple[str, str, str], ControlBlockAddress] = {
        key: cb
        for key, cb in doc.communication.control_block_addresses().items()
        if cb.kind == "GSE"
    }
    subs_by_ied: dict[str, list[SelGooseSubscription]] = \
        sel_goose_subscriptions(doc)

    # Index GSE by publisher for a fast TX lookup.
    gse_by_publisher: dict[str, list[ControlBlockAddress]] = {}
    for pub_addr in gse_map.values():
        gse_by_publisher.setdefault(pub_addr.ied_name, []).append(pub_addr)

    rows: list[IedVlanRow] = []
    for ied_name, ied in ieds.items():
        # RX: resolve each subscription -> VLAN-ID via gse_map.
        rx_set: set[str] = set()
        rx_publishers: dict[str, set[str]] = {}  # vlan_id -> {publisher_ied}
        rx_count = 0
        unresolved: list[str] = []
        for sub in subs_by_ied.get(ied_name, []):
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
            if addr is None or not addr.address.vlan_id:
                unresolved.append(
                    f"{sub.publisher_ied}/{sub.src_ld_inst or '?'}/{sub.src_cb_name}"
                )
                continue
            rx_set.add(addr.address.vlan_id)
            rx_publishers.setdefault(addr.address.vlan_id,
                                     set()).add(sub.publisher_ied)

        # TX: VLAN-IDs of this IED's own GSE.
        tx_addrs = gse_by_publisher.get(ied_name, [])
        tx_set: set[str] = set()
        for addr in tx_addrs:
            if addr.address.vlan_id:
                tx_set.add(addr.address.vlan_id)

        publishers_by_vlan = {
            vid: sorted(pubs) for vid, pubs in rx_publishers.items()
        }

        rows.append(IedVlanRow(
            ied_name=ied_name,
            ip=ip_by_ied.get(ied_name),
            relay_type=ied.type,
            description=ied.desc,
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


# -----------------------------------------------------------------------------
# The payload the page reads
# -----------------------------------------------------------------------------
#
# `build_payload` and `state_payload` answer the same shape and sit together
# for that reason: the empty one is what `/state` sends before any SCD has
# been chosen, and the two key sets have to stay identical or the page reads
# `undefined` on a fresh visit.

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


def build_payload(scd_path: Path, scd_name: str) -> dict:
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


def state_payload(st: VlanMapperState) -> dict:
    if st.payload is None:
        return {"has_scd": False, "scd_name": None,
                "rows": [], "ied_count": 0,
                "vlan_count": 0, "all_vlans": []}
    return dict(st.payload)
