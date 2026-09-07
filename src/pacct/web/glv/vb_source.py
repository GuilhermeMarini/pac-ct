"""Where each VBnnn of a diagram comes from, read off the project SCD.

A GLE block says `VB023` and nothing more. Which relay publishes it, over
which GOOSE control block, and what the bit is called on the publisher's side
are all in the SCD -- but in two halves, and only the SCD writes them down
together:

  subscriber   <ExtRef iedName="PUB" ldInst="ANN" prefix="PSV" lnClass="GGIO"
                       lnInst="1" doName="Ind05" daName="stVal"
                       intAddr="VB003" desc="50/62BF ..."/>
  publisher    <LDevice inst="ANN"><LN prefix="PSV" lnClass="GGIO" inst="1">
                 <DOI name="Ind05"><DAI name="stVal" sAddr="db:PSV05"/>

The ExtRef names an ADDRESS inside the publisher; the publisher's `sAddr` is
what says that address is the Relay Word bit `PSV05`. Joining the two is the
only way an offline diagram can name a bit it never read -- the relay does not
serve `sAddr` over MMS and telnet has no equivalent either.

Three shapes come out, and a corpus of 256 ExtRefs says there is no fourth:

  SIGNAL       a subscription to a data attribute; has a logical node, and a
               bit whenever the publisher mapped that address to one.
  QUALITY      a `pubRxStatus` health bit. Its ExtRef names a control block
               and NO data attribute, so there is no bit to name and printing
               one would be an invention.
  PLACEHOLDER  an ExtRef with no `iedName`. SEL Architect writes all 256
               whether or not the engineer wired any, so absence looks like
               this and not like a missing entry.

Nothing here touches the network, and nothing here is live: this is the SCD's
account of how the substation was built, which is exactly what is worth
looking at while the diagram is disconnected.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sellib.scl import read as scl

_logger = logging.getLogger(__name__)

# What each VB is. Sent to the browser as-is and used there as a CSS class, so
# these stay English like every other identifier in the project.
SIGNAL = "signal"
QUALITY = "quality"
PLACEHOLDER = "placeholder"

# `intAddr="VB3"`, `VB003` and a GLE symbol drawn `VB03` are one bit.
_VB_RE = re.compile(r"^VB0*(\d+)$", re.IGNORECASE)

# The ExtRef attributes that make up the address inside the publisher. `prefix`
# is optional in SCL and is joined straight onto lnClass+lnInst, which is how
# `sel_short_addresses` spells a logical node back.
_ADDRESS = ("ldInst", "prefix", "lnClass", "lnInst", "doName", "daName")


def normalise(name: str) -> str:
    """`VB7` -> `VB003`-style key, or `""` for anything that is not a VB.

    Three digits because that is how both an SEL Architect `intAddr` and a
    GLE symbol are written; the browser normalises `data-bit` the same way
    before looking a block up. A VB past 999 keeps its own width -- there is
    no truncation to lose.
    """
    m = _VB_RE.match((name or "").strip())
    return f"VB{int(m.group(1)):03d}" if m else ""


@dataclass(frozen=True)
class VbSource:
    """One VB and what the SCD says is behind it."""
    vb: str
    kind: str
    ied: str = ""      # the publisher
    ld: str = ""       # its logical device (ldInst)
    ln: str = ""       # its logical node, prefix + lnClass + lnInst
    do: str = ""
    da: str = ""
    bit: str = ""      # the publisher's Relay Word bit, from its sAddr
    desc: str = ""     # what the engineer wrote on the ExtRef
    cb: str = ""       # the GOOSE control block that carries it
    ld_cb: str = ""    # and the logical device the control block lives in

    def to_dict(self) -> dict:
        return {"vb": self.vb, "kind": self.kind, "ied": self.ied,
                "ld": self.ld, "ln": self.ln, "do": self.do, "da": self.da,
                "bit": self.bit, "desc": self.desc, "cb": self.cb,
                "ld_cb": self.ld_cb}


@dataclass(frozen=True)
class VbSourceMap:
    """Every VB of one relay, plus how that relay was found in the SCD."""
    ied: str = ""
    matched_by: str = ""
    sources: dict = field(default_factory=dict)
    error: str = ""

    def census(self) -> dict:
        """`{kind: count}` -- the three numbers the audit view reports."""
        out = {SIGNAL: 0, QUALITY: 0, PLACEHOLDER: 0}
        for s in self.sources.values():
            out[s.kind] = out.get(s.kind, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {"ied": self.ied, "matched_by": self.matched_by,
                "error": self.error, "census": self.census(),
                "sources": {k: v.to_dict() for k, v in self.sources.items()}}


def read(scd_path, *, relay_name: str, ip: str) -> VbSourceMap:
    """The VB map of one relay, or a `VbSourceMap` carrying the reason there
    is none.

    Never raises: the SCD is a file somebody uploaded, and a diagram whose VB
    source cannot be read is still a perfectly good diagram. Parses on every
    call -- 368 ms on a real 22 MB SCD -- so the caller caches the result;
    `GlvDiagram` holds one per open diagram.
    """
    path = Path(scd_path)
    try:
        doc = scl.ScdDocument.load(path)
    except Exception as e:                       # pragma: no cover - defensive
        return VbSourceMap(error=f"SCD ilegível: {e}")
    if doc is None:
        return VbSourceMap(error=f"SCD ilegível ou ausente: {path.name}")

    ieds = doc.ieds()
    ied_name, matched_by = _match_ied(ieds, relay_name=relay_name, ip=ip)
    if not ied_name:
        return VbSourceMap(error=(
            f"o SCD tem {len(ieds)} IEDs e nenhum casa com "
            f"{relay_name!r} nem com o IP {ip or 'não informado'}"))

    element = None
    for el in scl._iter_local(doc.root, "IED"):
        if el.attrib.get("name") == ied_name:
            element = el
            break
    if element is None:                          # pragma: no cover - defensive
        return VbSourceMap(error=f"IED {ied_name!r} sumiu do SCD entre duas leituras")

    return VbSourceMap(ied=ied_name, matched_by=matched_by,
                       sources=_sources(doc, element, ied_name))


def _match_ied(ieds: list, *, relay_name: str, ip: str) -> tuple[str, str]:
    """Which IED of the SCD this diagram's relay is, and by what.

    IP first. Offline there is no DEVID -- the key the live MMS path matches
    on, read off the relay itself -- and the IP is the one identifier the
    visitor typed in on the selection screen and that the SCD states outright.
    The RDB relay name is only a hint: an RDB and an SCD are written by
    different tools and routinely spell the same bay differently.

    Nothing matching returns `("", "")`. Picking an IED anyway would draw the
    NEIGHBOURING relay's GOOSE map onto this diagram, and nothing about that
    would look wrong.
    """
    if ip:
        hit = scl.index_by_ip(ieds).get(ip)
        if hit is not None:
            return hit.name, "IP"
    if relay_name:
        hit = scl.index_by_name(ieds).get(relay_name.upper())
        if hit is not None:
            return hit.name, "nome"
    if len(ieds) == 1:
        return ieds[0].name, "único IED"
    return "", ""


def _quality_vbs(doc, ied_name: str) -> set:
    """The VBs that receive a GOOSE subscription's health.

    Read off the `pubRxStatus` SEL Architect declares, never off the shape of
    an ExtRef. It also names remote bits (`RBnn`), which are not VBs and fall
    out here.
    """
    out = set()
    for bit in doc.goose_rx_status_by_ied().get(ied_name, {}):
        key = normalise(bit)
        if key:
            out.add(key)
    return out


def _sources(doc, element, ied_name: str) -> dict:
    """`{VBnnn: VbSource}` for one IED, joined against every publisher's sAddr."""
    health = _quality_vbs(doc, ied_name)
    # `{publisher: {(ld, ln, do, da): BIT}}`, the reverse of what
    # `short_addresses` hands over. Built once for the whole SCD: an IED
    # subscribes to a handful of publishers and each lookup is a dict hit.
    bit_at: dict = {}
    for publisher, bits in doc.short_addresses().items():
        table = bit_at.setdefault(publisher, {})
        for bit, point in bits.items():
            table.setdefault((point.ld_inst, point.ln, point.do, point.da), bit)

    out: dict = {}
    for ext in scl._iter_local(element, "ExtRef"):
        vb = normalise(ext.attrib.get("intAddr") or "")
        if not vb:
            continue
        row = _one(ext.attrib, vb, quality=vb in health, bit_at=bit_at)
        old = out.get(vb)
        # Several ExtRefs can carry the same intAddr -- typically a
        # placeholder alongside the real subscription. The wired one wins,
        # whichever order they appear in.
        if old is None or (old.kind == PLACEHOLDER and row.kind != PLACEHOLDER):
            out[vb] = row
    return dict(sorted(out.items()))


def _one(attrs, vb: str, *, quality: bool, bit_at: dict) -> VbSource:
    ied = (attrs.get("iedName") or "").strip()
    cb = (attrs.get("srcCBName") or "").strip()
    ld_cb = (attrs.get("srcLDInst") or "").strip()
    desc = (attrs.get("desc") or "").strip()
    if not ied:
        return VbSource(vb=vb, kind=PLACEHOLDER, desc=desc)
    if quality:
        # A health bit names the control block it watches and nothing inside
        # it. Filling `ln`/`do`/`da` from the empty attributes is what used to
        # print `IED/CFG/LLN0/GoSB00....` -- four bare dots standing in for a
        # logical node, a data object and a data attribute that do not exist.
        return VbSource(vb=vb, kind=QUALITY, ied=ied, cb=cb, ld_cb=ld_cb,
                        desc=desc)
    ld, prefix, ln_class, ln_inst, do, da = (
        (attrs.get(k) or "").strip() for k in _ADDRESS)
    ln = f"{prefix}{ln_class}{ln_inst}"
    return VbSource(vb=vb, kind=SIGNAL, ied=ied, ld=ld, ln=ln, do=do, da=da,
                    bit=bit_at.get(ied, {}).get((ld, ln, do, da), ""),
                    desc=desc, cb=cb, ld_cb=ld_cb)
