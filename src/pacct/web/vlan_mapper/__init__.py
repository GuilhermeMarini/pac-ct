"""
VLAN Mapper: a partir de um SCD, lista as VLANs que cada rele (IED) precisa
ter habilitadas na porta do switch.

Por IED, computamos dois conjuntos:
  - RX: VLAN-IDs dos GSE Control Blocks que o IED assina via
        <ExtRef serviceType="GOOSE" iedName="<publisher>" srcCBName="..."
                srcLDInst="..."> -- resolvidos contra os <GSE> de
        <ConnectedAP iedName="<publisher>"> no <Communication>.
  - TX: VLAN-IDs dos <GSE> do PROPRIO IED no <Communication> (i.e., GOOSE
        que este rele publica).

Saida: pagina HTML com uma linha por IED, colunas =
  IED | tipo/desc | IP | VLANs (chips) | #RX / #TX

Fluxo:
  1. Usuario faz upload do SCD.
  2. App parseia e mostra a tabela.
  3. Botao "<- Menu" no header volta pra home (mesma convencao do VB Updater).

Compartilha as utilidades de pagina (LANDING + escape + dropzone) com o
VB Updater, mas mantida em modulo separado pra simplificar o estado da sessao
(so SCD aqui, sem RDB nem matcher).

    templates/  landing.html
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from selfiles.scl import read as scd_loader
from selfiles.scl.read import GooseSubscription, GseAddress, IedInfo

from pacct.paths import VLAN_MAPPER_TEMPLATES_DIR
from pacct.web.project_files import library as filelib
from pacct.web.session import SessionHandler

_logger = logging.getLogger(__name__)


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (VLAN_MAPPER_TEMPLATES_DIR / name).read_text(encoding="utf-8")


# -----------------------------------------------------------------------------
# Estado da sessao
# -----------------------------------------------------------------------------

@dataclass
class _Session:
    scd_path: Path | None = None
    scd_name: str | None = None
    # Cache do payload calculado (revalidado a cada upload).
    payload: dict | None = None




# -----------------------------------------------------------------------------
# Computacao do mapa IED -> VLANs
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class IedVlanRow:
    ied_name: str
    ip: str | None
    relay_type: str | None
    description: str | None
    rx_vlans: list[str]   # VLAN-IDs distintos (ordenados) que o IED recebe
    tx_vlans: list[str]   # VLAN-IDs distintos (ordenados) que o IED publica
    # vlan_id -> lista (ordenada) de IEDs publishers que originam GOOSE
    # naquele VLAN e que sao assinados por este IED. So inclui o lado RX --
    # pro TX o publisher e o proprio IED (rendering trata como "self").
    publishers_by_vlan: dict[str, list[str]]
    rx_count: int         # numero total de assinaturas GOOSE (mesmo vlan repetido conta)
    tx_count: int         # numero de GSE controls publicados
    unresolved: list[str] # GSEs assinados mas sem entrada em <Communication>


def _sort_vlans(values: set[str]) -> list[str]:
    """Ordena VLAN-IDs textualmente, com prioridade pra ordem numerica quando
    todos sao parseaveis como int (em base 10 ou 16)."""
    def key(v: str):
        s = v.strip()
        # Tenta hex primeiro (VLAN-IDs em SCDs frequentemente vem em hex).
        for base in (16, 10):
            try:
                return (0, int(s, base))
            except ValueError:
                continue
        return (1, s)
    return sorted(values, key=key)


def compute_ied_vlan_rows(scd_path: Path) -> list[IedVlanRow]:
    """Cruza IEDs + GSE communication + GOOSE subscriptions e retorna uma
    lista de linhas (uma por IED conhecido) com RX/TX VLANs.

    IEDs sem nenhuma subscricao e sem GSE proprio sao incluidos mesmo assim
    (a porta de switch ainda recebe trafego MMS/Reports/etc., mas pelo escopo
    desta ferramenta as VLANs ficarao vazias).
    """
    ieds: list[IedInfo] = scd_loader.load_scd(scd_path)
    gse_map: dict[tuple[str, str, str], GseAddress] = (
        scd_loader.extract_gse_communication_map(scd_path)
    )
    subs_by_ied: dict[str, list[GooseSubscription]] = (
        scd_loader.extract_goose_subscriptions_by_ied(scd_path)
    )

    # Indexa GSE por publisher pra calcular TX rapido.
    gse_by_publisher: dict[str, list[GseAddress]] = {}
    for addr in gse_map.values():
        gse_by_publisher.setdefault(addr.publisher_ied, []).append(addr)

    rows: list[IedVlanRow] = []
    for ied in ieds:
        # RX: resolve cada subscription -> VLAN-ID via gse_map.
        rx_set: set[str] = set()
        rx_publishers: dict[str, set[str]] = {}  # vlan_id -> {publisher_ied}
        rx_count = 0
        unresolved: list[str] = []
        for sub in subs_by_ied.get(ied.name, []):
            rx_count += 1
            key = (sub.publisher_ied, sub.src_ld_inst, sub.src_cb_name)
            addr = gse_map.get(key)
            if addr is None and sub.src_ld_inst:
                # Algumas ferramentas omitem ldInst no GSE mas mantem no ExtRef
                # (ou vice-versa). Tenta um fallback so com (publisher, cbName).
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

        # TX: VLAN-IDs dos proprios GSE deste IED.
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

    # Ordena IEDs por nome pra dar uma listagem determinista.
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
    # VLANs distintas em toda a substacao (RX+TX agregado) -- util pra UI.
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

# A navegacao numerada e' a mesma das nove telas -- mora em theme.py.


# -----------------------------------------------------------------------------
# Server
# -----------------------------------------------------------------------------

def build_vlan_mapper_handler(logger: logging.Logger, sessions) -> type:
    """Devolve a classe de handler do VLAN Mapper.

    Nao sobe servidor: quem serve e' o dispatcher unico de `pacct.web.mount`,
    que monta esse handler em `/vlan-mapper/`. Estado e uploads ficam por
    sessao (`self.sess()` / `self.sdir()`), nao por processo.
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
                # Sentinela usado pela home pra detectar que essa tool subiu.
                self._send_json(200, {"ok": True})
                return
            if path == "/state":
                self._send_json(200, _state_payload(self.sess()))
                return
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path

            if path == "/select-scd":
                # O corpo do antigo /scd-upload, do `load_scd` pra frente: o
                # arquivo ja foi recebido e validado em /files/.
                body = self._read_json_body()
                sha = (body.get("sha256") or "").strip()
                lib = filelib.library_for(sessions, self.session)
                with self.session.lock:
                    entry = lib.get(sha)
                if entry is None or entry.kind != filelib.KIND_SCD:
                    self._send_json(404, {
                        "error": "Arquivo não está mais no projeto."})
                    return
                job = self.job()
                job.stage("Lendo IEDs e VLANs do SCD", 10)
                try:
                    payload = _build_payload(entry.scd_path, entry.display_name)
                except Exception as e:
                    logger.exception("falha computando VLAN map: %s", e)
                    self._send_json(500, {
                        "error": f"falha computando VLAN map: {e}"})
                    return
                st = self.sess()
                with self.session.lock:
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
