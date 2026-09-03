"""
GLE Variable Comment Exporter: extrai a lista de SYMBOL instances de cada GLE
de um RDB e exporta como Excel pra edicao em bulk. O usuario edita os
comentarios das portas (input/output) e reimporta o Excel pra gerar um RDB
atualizado.

Fluxo:
  1. Usuario faz upload de um RDB.
  2. App lista os reles do RDB com selecao de GLE por rele.
  3. Botao "Exportar Excel" gera um xlsx com uma aba por (rele, GLE)
     selecionado, contendo todas as instancias de SYMBOL daquele GLE.
  4. Usuario edita as celulas "Input Comment"/"Output Comment".
  5. Botao "Importar Excel" aplica as alteracoes ao RDB e baixa um novo RDB.

Cada SYMBOL no GLE eh uma "instancia" identificada por (page, element_id). Um
mesmo nome (ex.: TMB1A) pode aparecer varias vezes em paginas diferentes;
cada ocorrencia eh uma linha no Excel.

    templates/  landing.html
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlparse

from selfiles import rdb as rdb_loader
from selfiles.gle import parse_gle
from selfiles.rdb import RdbInfo

from pacct.paths import GLE_EXPORTER_TEMPLATES_DIR, is_within
from pacct.web import rdb_write
from pacct.web.project_files import library as filelib
from pacct.web.rdb_write import (
    resolve_gle_stream_path as _resolve_gle_stream_path,
)
from pacct.web.rdb_write import (
    with_suffix_before_ext as _with_suffix_before_ext,
)
from pacct.web.session import SessionHandler
from pacct.web.xlsx_names import sanitize_sheet_name as _sanitize_sheet_name

_logger = logging.getLogger(__name__)


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (GLE_EXPORTER_TEMPLATES_DIR / name).read_text(encoding="utf-8")


_XLSX_MAX_BYTES = 50 * 1024 * 1024


# Marcadores: identificam que um xlsx veio do export desta ferramenta.
_XLSX_RELAY_MARKER = "Relay:"
_XLSX_GLE_MARKER = "GLE:"
# Header da linha 4 (cada coluna = uma celula nesse cabecalho).
_XLSX_HEADERS = (
    "Page", "Element ID", "Type", "Variable", "Side", "Port", "Label", "Comment",
)
# Coluna 1-based de cada campo (estavel pra parser e tests).
_COL_PAGE, _COL_EID, _COL_TYPE, _COL_VAR, _COL_SIDE, _COL_PORT, _COL_LABEL, _COL_CMT = range(1, 9)

# Diretorio de saida dos xlsx (reusa o sandbox de /download).
# Uploads e saidas vivem em cache/sessions/<sid>/ (ver pacct.web.session);
# nao ha mais diretorio compartilhado entre usuarios.


# -----------------------------------------------------------------------------
# Extractor: GLE.xml -> [PortInstance]
# -----------------------------------------------------------------------------

# Tipos XML do GLE com `physical_instance_name` que queremos exportar. Inclui:
#   - SYMBOL (variaveis IO, Relay Word bits): 1 in + 1 out, sem rotulos fixos.
#   - blocos stateful do 4xx/7xx: PLT, ALT, PCNDTIMER, PCN, AST, PSV, LATCH,
#     TIMER, COUNTER. Rotulos das portas vem do relay_model JSON.
# Gates puros (AND/OR/NOT/EQ/...) ficam de fora -- nao tem nome instancia
# editavel e o comment das portas nao tem semantica de comissionamento.
_EXPORTABLE_XML_TYPES = frozenset({
    "SYMBOL",
    "PLT", "ALT", "PCNDTIMER", "PCN", "AST", "PSV",
    "LATCH", "TIMER", "COUNTER",
})


@dataclass(frozen=True)
class PortInstance:
    """Uma porta concreta (em um element do GLE) com seu rotulo fixo e o
    comment livre do usuario."""
    page: str
    element_id: str
    xml_type: str        # type do <element>: SYMBOL, PLT, LATCH, ...
    name: str            # physical_instance_name (ex.: VB203, _PLT06, TMB1A)
    side: str            # "input" ou "output"
    port_index: int      # `index` atributo da <port> (0, 1, 2, ...)
    label: str           # rotulo fixo do pin (S/R/Q/in/PU/...) ou "" se ausente
    comment: str         # texto livre do usuario; "" se <comment/> vazio


def extract_port_instances_from_gle(
    gle_path: Path, relay_model=None,
) -> list[PortInstance]:
    """Le um GLE.xml e devolve uma lista de PortInstance, uma por porta de
    cada element exportavel.

    Convencao do GLE: dentro de cada `<logic_element>` ha (na ordem) dois
    blocos `<ports>` -- o 1o eh o lado de input (esquerda), o 2o eh o lado
    de output (direita). Cada `<port>` tem um `index` numerico (0, 1, 2).

    Se `relay_model` for fornecido, os rotulos dos pins (S/R/Q/...) sao
    resolvidos via `relay_model.port_label`. Sem o modelo, label fica "".
    """
    out: list[PortInstance] = []
    try:
        root = parse_gle(gle_path)
    except (OSError, ET.ParseError, UnicodeDecodeError) as e:
        _logger.warning("erro lendo GLE %s: %s", gle_path, e)
        return out

    for page in root.iter("page"):
        page_name = (page.attrib.get("name") or "").strip()
        for el in page.iter("element"):
            xml_type = (el.attrib.get("type") or "").strip()
            if xml_type not in _EXPORTABLE_XML_TYPES:
                continue
            element_id = (el.attrib.get("id") or "").strip()
            le = el.find("logic_element")
            if le is None:
                continue
            name = (le.attrib.get("physical_instance_name") or "").strip()
            if not name:
                continue
            port_groups = le.findall("ports")
            for grp_idx, pg in enumerate(port_groups[:2]):
                side = "input" if grp_idx == 0 else "output"
                for port_el in pg.findall("port"):
                    try:
                        idx = int(port_el.attrib.get("index", "0"))
                    except ValueError:
                        idx = 0
                    cmt_el = port_el.find("comment")
                    cmt = (cmt_el.text or "").strip() if cmt_el is not None else ""
                    label = ""
                    if relay_model is not None:
                        lbl = relay_model.port_label(xml_type, side, idx)
                        if lbl is not None:
                            label = lbl
                    out.append(PortInstance(
                        page=page_name, element_id=element_id,
                        xml_type=xml_type, name=name, side=side,
                        port_index=idx, label=label, comment=cmt,
                    ))
    return out


# -----------------------------------------------------------------------------
# Writer: edita os comments do GLE em bytes (mantem tamanho do stream OLE)
# -----------------------------------------------------------------------------

# Casa um <element id="N" type="TIPO" ...> ... </element>. Usamos grupos
# nomeados pra nao depender de indices (com a adicao de `type` os indices
# numericos shiftam, o que ja foi fonte de bug).
# Non-greedy no corpo: `<element>` nao aninha, e `</logic_element>` (que esta
# DENTRO) nao casa com `</element>` literal -- entao o primeiro `</element>`
# encontrado eh sempre o do bloco externo.
_GLE_ELEMENT_RE = re.compile(
    rb'(?P<open><element\s+id="(?P<id>\d+)"\s+type="(?P<type>[^"]+)"[^>]*>)'
    rb'(?P<body>.*?)'
    rb'(?P<close></element>)',
    re.DOTALL,
)

# Casa um bloco <ports>...</ports> OU <ports/> (self-closing) dentro do
# logic_element. Tambem non-greedy: `<ports>` nao aninha.
_PORTS_BLOCK_RE = re.compile(
    rb'<ports\s*/>|<ports\b[^>]*>.*?</ports>',
    re.DOTALL,
)

# Dentro de um <ports>...</ports>, casa o comment do <port index="N"> (N
# vira por substituicao do {idx}). Dois formatos de comment no GLE:
#   <comment />                  (self-closing, vazio)
#   <comment>TEXT</comment>      (com ou sem texto)
# group(1) = abertura do port + abertura do <comment;
# group(2) = corpo do comment;
# group(3) = fechamento do port.
def _port_by_index_re(idx: int) -> re.Pattern[bytes]:
    pat = (
        rb'(<port\s+index="' + str(int(idx)).encode("ascii") + rb'"[^>]*>\s*<comment)'
        rb'(\s*/>|\s*>[^<]*</comment>)'
        rb'(\s*</port>)'
    )
    return re.compile(pat, re.DOTALL)


def _xml_text_escape(s: str) -> str:
    """Escapa caracteres especiais para conteudo de texto XML."""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


def _build_comment_node(new_comment: str) -> bytes:
    """Monta o novo no <comment...> em bytes latin-1.
    Empty -> self-closing ` />`; non-empty -> `>TEXT</comment>`.
    """
    if not new_comment:
        return b" />"
    text_bytes = _xml_text_escape(new_comment).encode("latin-1", errors="replace")
    return b">" + text_bytes + b"</comment>"


def _set_port_comment(
    ports_block: bytes, port_index: int, new_comment: str,
) -> tuple[bytes, bool]:
    """Em um <ports>...</ports>, substitui o comment de <port index=port_index>.
    Retorna (novos_bytes, foi_atualizado). False se o bloco for self-closing
    (sem porta) ou nao tiver uma porta com aquele index."""
    replacement = _build_comment_node(new_comment)
    pat = _port_by_index_re(port_index)

    def _sub(m: re.Match) -> bytes:
        return m.group(1) + replacement + m.group(3)

    new_block, n = pat.subn(_sub, ports_block, count=1)
    return new_block, n > 0


# updates: { element_id -> { (side, port_index) -> new_comment } }
PortUpdates = dict[str, dict[tuple[str, int], str]]


def update_port_comments_in_gle_bytes(
    raw: bytes, updates: PortUpdates,
) -> tuple[bytes, dict]:
    """Atualiza comments de portas especificas por (element_id, side, port_index)
    em um GLE.

    `updates`: { element_id -> { (side, port_index) -> new_comment } }.
    `side` ∈ {"input","output"}. String vazia = forca <comment /> self-closing.

    Retorna (novos_bytes, stats). stats tem chaves:
      - elements_touched: # de elements que tiveram pelo menos uma porta tocada
      - ports_updated:    # total de port-comments substituidos
      - ports_skipped:    pedidos de update que nao acharam a porta esperada
      - elements_missing: ids em `updates` que nao foram encontrados no GLE
    """
    stats = {"elements_touched": 0, "ports_updated": 0,
             "ports_skipped": 0, "elements_missing": 0}
    seen_ids: set[str] = set()

    def replace_element(m: re.Match) -> bytes:
        eid = m.group("id").decode("ascii")
        if eid not in updates:
            return m.group(0)
        seen_ids.add(eid)
        upd = updates[eid]
        head = m.group("open")
        body = m.group("body")
        tail = m.group("close")

        # Localiza os <ports>...</ports> blocos (no maximo 2: input e output).
        port_matches = list(_PORTS_BLOCK_RE.finditer(body))
        # Agrupa updates por side pra processar block-by-block.
        by_side: dict[str, dict[int, str]] = {"input": {}, "output": {}}
        for (side, idx), new_cmt in upd.items():
            if side in by_side:
                by_side[side][idx] = new_cmt

        out_parts: list[bytes] = []
        last_end = 0
        touched_here = 0
        for i, pm in enumerate(port_matches[:2]):
            out_parts.append(body[last_end:pm.start()])
            side = "input" if i == 0 else "output"
            side_updates = by_side.get(side, {})
            if not side_updates:
                out_parts.append(pm.group(0))
            else:
                new_block = pm.group(0)
                for idx, new_cmt in side_updates.items():
                    new_block, ok = _set_port_comment(new_block, idx, new_cmt)
                    if ok:
                        stats["ports_updated"] += 1
                        touched_here += 1
                    else:
                        stats["ports_skipped"] += 1
                out_parts.append(new_block)
            last_end = pm.end()
        out_parts.append(body[last_end:])

        # Updates direcionadas a um side cujo <ports> nao existe -> skipped.
        for i in range(len(port_matches), 2):
            side = "input" if i == 0 else "output"
            stats["ports_skipped"] += len(by_side.get(side, {}))

        if touched_here:
            stats["elements_touched"] += 1
        return head + b"".join(out_parts) + tail

    new_raw = _GLE_ELEMENT_RE.sub(replace_element, raw)
    stats["elements_missing"] = len(set(updates.keys()) - seen_ids)
    return new_raw, stats


# -----------------------------------------------------------------------------
# Fit XML to a target byte size (mesmo padrao do vb_updater)
# -----------------------------------------------------------------------------
# Excel I/O
# -----------------------------------------------------------------------------

def build_xlsx_for_selections(
    selections: list[dict],
) -> bytes:
    """Gera um .xlsx (bytes) com uma aba por selecao.

    Cada `selection` eh um dict {relay, gle, gle_path, relay_model}. A aba
    contem:
      A1: "Relay:"      B1: <relay_name>
      A2: "GLE:"        B2: <gle_name>
      Linha 3: branco
      Linha 4: cabecalhos [Page, Element ID, Type, Variable, Side, Port, Label, Comment]
      Linha 5+: uma linha por porta de cada elemento exportavel.

    `relay_model` (opcional) habilita os rotulos fixos das portas (S/R/Q/...);
    sem ele, a coluna Label fica vazia mas a tabela continua valida.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    default = wb.active
    wb.remove(default)

    used: set[str] = set()
    header_font = Font(bold=True, color="FFFFFFFF")
    header_fill = PatternFill("solid", fgColor="FF1F6FEB")
    meta_font = Font(bold=True)

    for sel in selections:
        relay = sel["relay"]
        gle = sel["gle"]
        gle_path = sel["gle_path"]
        relay_model = sel.get("relay_model")
        ports = extract_port_instances_from_gle(gle_path, relay_model=relay_model)

        sheet_base = f"{relay} {gle}".strip()
        ws = wb.create_sheet(title=_sanitize_sheet_name(sheet_base, used))
        ws["A1"] = _XLSX_RELAY_MARKER
        ws["B1"] = relay
        ws["A1"].font = meta_font
        ws["A2"] = _XLSX_GLE_MARKER
        ws["B2"] = gle
        ws["A2"].font = meta_font

        for col, val in enumerate(_XLSX_HEADERS, start=1):
            c = ws.cell(row=4, column=col, value=val)
            c.font = header_font
            c.fill = header_fill
            c.alignment = Alignment(horizontal="left")

        for i, p in enumerate(ports, start=5):
            ws.cell(row=i, column=_COL_PAGE,  value=p.page)
            ws.cell(row=i, column=_COL_EID,   value=p.element_id)
            ws.cell(row=i, column=_COL_TYPE,  value=p.xml_type)
            ws.cell(row=i, column=_COL_VAR,   value=p.name)
            ws.cell(row=i, column=_COL_SIDE,  value=p.side)
            ws.cell(row=i, column=_COL_PORT,  value=p.port_index)
            ws.cell(row=i, column=_COL_LABEL, value=p.label)
            ws.cell(row=i, column=_COL_CMT,   value=p.comment)

        # Larguras: dimensiona aproximadamente pra exibir sem cortar.
        widths = {
            _COL_PAGE: 28, _COL_EID: 10, _COL_TYPE: 12, _COL_VAR: 18,
            _COL_SIDE: 8,  _COL_PORT: 6, _COL_LABEL: 10, _COL_CMT: 60,
        }
        for col_idx, w in widths.items():
            ws.column_dimensions[get_column_letter(col_idx)].width = w
        ws.freeze_panes = "A5"

    if not wb.sheetnames:
        wb.create_sheet(title="empty")

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# Tipo retornado pelo parser do xlsx:
#   {(relay, gle) -> {element_id -> {(side, port_index) -> new_comment}}}
XlsxUpdates = dict[tuple[str, str], PortUpdates]


def parse_xlsx_to_updates(xlsx_bytes: bytes) -> XlsxUpdates:
    """Le um xlsx gerado por `build_xlsx_for_selections` (possivelmente editado)
    e retorna a estrutura de updates port-a-port.

    Regras:
      - Aba precisa ter A1='Relay:' e A2='GLE:' (marcadores). Outras abas
        sao silenciosamente ignoradas.
      - Linhas com Element ID vazio ou Side desconhecido sao puladas.
      - Se a celula Comment for None (cell apagada/nunca preenchida no export
        original), NAO entra em updates -> nao toca a porta.
      - Comment "" explicito = pedido pra zerar o comment (vira <comment />).
      - Side eh normalizado pra "input"/"output" (aceita case insensitive).
    """
    from openpyxl import load_workbook

    wb = load_workbook(filename=BytesIO(xlsx_bytes), data_only=True, read_only=True)
    out: XlsxUpdates = {}
    try:
        for ws in wb.worksheets:
            a1 = ws.cell(row=1, column=1).value
            a2 = ws.cell(row=2, column=1).value
            if not (isinstance(a1, str) and a1.strip() == _XLSX_RELAY_MARKER):
                continue
            if not (isinstance(a2, str) and a2.strip() == _XLSX_GLE_MARKER):
                continue
            relay = ws.cell(row=1, column=2).value
            gle = ws.cell(row=2, column=2).value
            if not relay or not gle:
                continue
            relay = str(relay).strip()
            gle = str(gle).strip()
            per_gle: PortUpdates = out.setdefault((relay, gle), {})
            for row in ws.iter_rows(min_row=5, max_col=len(_XLSX_HEADERS),
                                     values_only=True):
                if row is None:
                    continue
                cells = list(row) + [None] * len(_XLSX_HEADERS)
                _page = cells[_COL_PAGE - 1]
                eid = cells[_COL_EID - 1]
                _xml_type = cells[_COL_TYPE - 1]
                _var = cells[_COL_VAR - 1]
                side_raw = cells[_COL_SIDE - 1]
                port_raw = cells[_COL_PORT - 1]
                _label = cells[_COL_LABEL - 1]
                cmt = cells[_COL_CMT - 1]
                if eid is None:
                    continue
                eid_str = str(eid).strip()
                if not eid_str.isdigit():
                    continue
                side = str(side_raw or "").strip().lower()
                if side not in ("input", "output"):
                    continue
                try:
                    port_idx = int(port_raw) if port_raw is not None else 0
                except (TypeError, ValueError):
                    continue
                if cmt is None:
                    continue  # cell vazia (nao editada) -> nao tocar
                entry = per_gle.setdefault(eid_str, {})
                entry[(side, port_idx)] = str(cmt).strip()
    finally:
        wb.close()
    return {k: v for k, v in out.items() if v}


def diff_updates_against_gle(
    gle_path: Path, updates: PortUpdates, relay_model=None,
) -> PortUpdates:
    """Filtra updates removendo (side, port_index) ja iguais ao GLE atual."""
    if not updates:
        return {}
    current: dict[str, dict[tuple[str, int], str]] = {}
    for p in extract_port_instances_from_gle(gle_path, relay_model=relay_model):
        current.setdefault(p.element_id, {})[(p.side, p.port_index)] = p.comment

    out: PortUpdates = {}
    for eid, ports in updates.items():
        if eid not in current:
            # element_id nao existe no GLE atual -- mantem pra contabilizar
            # como missing no writer.
            out[eid] = dict(ports)
            continue
        cur = current[eid]
        delta: dict[tuple[str, int], str] = {}
        for key, new_cmt in ports.items():
            if cur.get(key, "") != new_cmt:
                delta[key] = new_cmt
        if delta:
            out[eid] = delta
    return out


# -----------------------------------------------------------------------------
# Orquestrador: aplica edits do xlsx ao RDB e gera novo RDB
# -----------------------------------------------------------------------------

def apply_xlsx_updates_to_rdb(
    *,
    rdb_info: RdbInfo,
    xlsx_updates: XlsxUpdates,
    output_path: Path,
    job=None,
) -> dict:
    """Aplica as alteracoes do xlsx ao RDB e gera `output_path`.

    `xlsx_updates`: (relay, gle) -> {element_id: {(side, port_idx): comment}}.

    Duas passadas, e a ordem importa. A PRIMEIRA so le: resolve cada
    (rele, GLE) pro seu stream no OLE e calcula os bytes novos, sem tocar em
    disco. So se TODAS derem certo a segunda passada grava, de uma vez, via
    `rdb_write.write_streams` -- que usa `write_stream` quando cada stream
    mantem o tamanho e reconstroi o container quando nao mantem, sempre
    atomicamente sobre o destino.

    E' tudo-ou-nada de proposito. Antes, cada selecao era gravada no RDB de
    saida assim que ficava pronta: se a terceira falhava, as duas primeiras ja
    estavam no arquivo, a resposta ainda dizia `ok: True`, e esse RDB
    meio-aplicado ia pro acervo do projeto igual a um completo. Um arquivo de
    ajustes pela metade e' indistinguivel de um inteiro depois que sai daqui.

    Retorna {ok, output_path, results, succeeded, failed, totals, method}.
    Com `ok: False`, NENHUM arquivo foi escrito e `output_path` nao existe.
    """
    from selfiles.models import relay_models as _rm

    # Cache do RelayModel por nome (resolve so uma vez por relay).
    model_cache: dict[str, object] = {}
    def _model_for(relay_name: str):
        if relay_name in model_cache:
            return model_cache[relay_name]
        entry_relay = next(
            (r for r in rdb_info.relays if r.name == relay_name), None,
        )
        m = _rm.lookup(entry_relay.model) if (entry_relay and entry_relay.model) else None
        model_cache[relay_name] = m
        return m

    results: list[dict] = []
    totals = {"elements_touched": 0, "ports_updated": 0,
              "ports_skipped": 0, "elements_missing": 0}
    streams: dict[tuple[str, ...], bytes] = {}

    # -- passada 1: so leitura ------------------------------------------------
    if job:
        job.stage("Conferindo os GLEs alterados", 10)
    for (relay, gle), elem_updates in xlsx_updates.items():
        entry = {"relay": relay, "gle": gle}
        gle_entry = rdb_loader.find_gle(rdb_info, relay, gle)
        if gle_entry is None or not gle_entry.fs_path.is_file():
            entry["ok"] = False
            entry["error"] = "GLE nao encontrado no RDB"
            results.append(entry)
            continue

        # Filtra updates que ja batem com o conteudo atual.
        delta = diff_updates_against_gle(
            gle_entry.fs_path, elem_updates,
            relay_model=_model_for(relay),
        )
        if not delta:
            entry["ok"] = True
            entry["stats"] = {"elements_touched": 0, "ports_updated": 0,
                              "ports_skipped": 0, "elements_missing": 0}
            entry["note"] = "nada a aplicar (valores ja batem)"
            results.append(entry)
            continue

        stream_parts = _resolve_gle_stream_path(rdb_info.extract_dir,
                                                gle_entry.fs_path)
        if not stream_parts:
            entry["ok"] = False
            entry["error"] = f"stream nao localizado no OLE: {gle_entry.rel_path}"
            results.append(entry)
            continue

        # A leitura vem do arquivo EXTRAIDO, nao do OLE: e' o mesmo conteudo,
        # e assim a passada 1 nao abre o RDB nenhuma vez.
        original = gle_entry.fs_path.read_bytes()
        updated, stats = update_port_comments_in_gle_bytes(original, delta)
        streams[tuple(stream_parts)] = updated
        entry["ok"] = True
        entry["stats"] = stats
        entry["original_stream_bytes"] = len(original)
        for k in totals:
            totals[k] += stats.get(k, 0)
        results.append(entry)

    succeeded = sum(1 for r in results if r.get("ok"))
    failed = len(results) - succeeded
    if failed:
        return {
            "ok": False,
            "error": (f"{failed} de {len(results)} seleção(ões) falharam; "
                      "nenhum RDB foi gravado."),
            "results": results,
            "succeeded": succeeded,
            "failed": failed,
            "totals": totals,
        }

    # -- passada 2: uma gravacao ---------------------------------------------
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if streams:
            method = rdb_write.write_streams(rdb_info.rdb_path, output_path,
                                             streams, job=job)
        else:
            # Todas as selecoes eram no-op. O usuario ainda recebe o RDB (era
            # o que acontecia antes, quando a copia vinha primeiro), so que
            # sem nenhum stream reescrito.
            rdb_write.copy_only(rdb_info.rdb_path, output_path)
            method = "copy"
    except rdb_write.RdbWriteError as e:
        return {
            "ok": False,
            "error": str(e),
            "results": results,
            "succeeded": succeeded,
            "failed": failed,
            "totals": totals,
        }

    return {
        "ok": True,
        "output_path": str(output_path),
        "method": method,
        "results": results,
        "succeeded": succeeded,
        "failed": failed,
        "totals": totals,
    }


# -----------------------------------------------------------------------------
# Estado da sessao
# -----------------------------------------------------------------------------

@dataclass
class _SessionState:
    rdb: RdbInfo | None = None


def _state_payload(st: _SessionState) -> dict:
    if st.rdb is None:
        return {
            "has_rdb": False,
            "rdb_name": None,
            "relays": [],
        }
    return {
        "has_rdb": True,
        "rdb_name": st.rdb.display_name,
        "relays": [
            {
                "name": r.name,
                "model": r.model,
                "ip": r.ip,
                "gles": [{"name": g.name, "filename": g.filename}
                         for g in r.gles],
            }
            for r in st.rdb.relays
        ],
    }


# -----------------------------------------------------------------------------
# HTML
# -----------------------------------------------------------------------------

LANDING_HTML = load_template("landing.html")

# A navegacao numerada e' a mesma das nove telas -- mora em theme.py.


# -----------------------------------------------------------------------------
# Server
# -----------------------------------------------------------------------------

def build_gle_exporter_handler(logger: logging.Logger, sessions) -> type:
    """Devolve a classe de handler do GLE Variable Comment Exporter.

    Nao sobe servidor: quem serve e' o dispatcher unico de `pacct.web.mount`,
    que monta esse handler em `/gle-exporter/`. Estado e arquivos ficam por
    sessao (`self.sess()` / `self.sdir()`), nao por processo.
    """

    class Handler(SessionHandler):
        session_key = "gle-exporter"
        state_factory = _SessionState
        server_sessions = sessions

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                self._send(200, LANDING_HTML, "text/html; charset=utf-8")
                return
            if path == "/gle-state":
                self._send_json(200, {"ok": True})
                return
            if path == "/state":
                self._send_json(200, _state_payload(self.sess()))
                return
            if path == "/download":
                from urllib.parse import parse_qs as _parse_qs
                qs = _parse_qs(parsed.query)
                file_param = (qs.get("file") or [""])[0]
                if not file_param:
                    self._send(400, "missing 'file' param", "text/plain")
                    return
                target = Path(file_param).resolve()
                # `rdbs` saiu: os uploads agora vao pro cache por conteudo,
                # que e' compartilhado -- deixa-lo entrar no sandbox daria a um
                # visitante o arquivo derivado gerado por outro.
                if not is_within(target, (self.sdir("out"), self.sdir("xlsx"))):
                    self._send(403, "path outside allowed roots", "text/plain")
                    return
                if not target.is_file():
                    self._send(404, "file not found", "text/plain")
                    return
                data = target.read_bytes()
                ext = target.suffix.lower()
                if ext == ".rdb":
                    ctype = "application/octet-stream"
                elif ext == ".xlsx":
                    ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                else:
                    ctype = "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{target.name}"',
                )
                self.end_headers()
                self.wfile.write(data)
                return

            self._send(404, "not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0

            if path == "/select-rdb":
                # O corpo do antigo /rdb-upload, do `process_upload` pra
                # frente: o arquivo ja foi recebido e extraido em /files/.
                body = self._read_json_body()
                sha = (body.get("sha256") or "").strip()
                lib = filelib.library_for(sessions, self.session)
                with self.session.lock:
                    entry = lib.get(sha)
                if entry is None or entry.kind != filelib.KIND_RDB:
                    self._send_json(404, {
                        "error": "Arquivo não está mais no projeto."})
                    return
                info = entry.rdb
                logger.info("[gle-exporter] RDB '%s' (%s) escolhido; "
                            "%d relé(s) com GLE",
                            info.display_name, info.sha256[:16],
                            len(info.relays))
                st = self.sess()
                with self.session.lock:
                    st.rdb = info
                self._send_json(200, _state_payload(self.sess()))
                return

            if path == "/export":
                job = self.job()
                job.stage("Lendo selecao", 0)
                body = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    payload = json.loads(body or b"{}")
                    selections = payload.get("selections", [])
                except (json.JSONDecodeError, TypeError):
                    self._send_json(400, {"error": "bad request"})
                    return
                if not isinstance(selections, list) or not selections:
                    self._send_json(400, {"error": "selections vazia"})
                    return
                with self.session.lock:
                    rdb = self.sess().rdb
                if rdb is None:
                    self._send_json(409, {"error": "RDB nao carregado"})
                    return
                from selfiles.models import relay_models as _rm
                model_cache: dict[str, object] = {}
                def _model_for(relay_name: str):
                    if relay_name in model_cache:
                        return model_cache[relay_name]
                    r_entry = next(
                        (r for r in rdb.relays if r.name == relay_name), None,
                    )
                    m = (_rm.lookup(r_entry.model)
                         if (r_entry and r_entry.model) else None)
                    model_cache[relay_name] = m
                    return m

                resolved: list[dict] = []
                for sel in selections:
                    if not isinstance(sel, dict):
                        continue
                    relay = str(sel.get("relay", "")).strip()
                    gle = str(sel.get("gle", "")).strip()
                    if not (relay and gle):
                        continue
                    entry = rdb_loader.find_gle(rdb, relay, gle)
                    if entry is None or not entry.fs_path.is_file():
                        continue
                    resolved.append({
                        "relay": relay, "gle": gle, "gle_path": entry.fs_path,
                        "relay_model": _model_for(relay),
                    })
                if not resolved:
                    self._send_json(422, {"error": "nenhuma selecao valida"})
                    return
                try:
                    xlsx_bytes = build_xlsx_for_selections(resolved)
                except Exception as e:
                    logger.exception("falha gerando xlsx: %s", e)
                    self._send_json(500, {"error": str(e)})
                    return
                out_name = _with_suffix_before_ext(
                    Path(rdb.display_name), "_gle_comments",
                ).with_suffix(".xlsx").name
                job.stage("Gravando planilha", 90)
                out_path = (self.sdir("xlsx") / out_name).resolve()
                try:
                    out_path.write_bytes(xlsx_bytes)
                except OSError as e:
                    self._send_json(500, {"error": f"falha ao salvar xlsx: {e}"})
                    return
                # Conta portas exportadas pra mostrar no resumo.
                total_ports = 0
                for sel in resolved:
                    total_ports += len(extract_port_instances_from_gle(
                        sel["gle_path"], relay_model=sel.get("relay_model"),
                    ))
                logger.info(
                    "[gle-exporter] export: %d aba(s), %d porta(s) -> %s",
                    len(resolved), total_ports, out_name,
                )
                # A planilha tambem entra no acervo: nenhuma ferramenta a
                # seleciona (ninguem pede `?kind=xlsx`), mas assim ela pode
                # ser rebaixada depois de um lugar so, em vez de so da aba que
                # por acaso a gerou.
                project = self.publish_output(out_path, "GLE Exporter",
                                              job=job, logger=logger)
                job.finish("Planilha gerada")
                self._send_json(200, {
                    "ok": True,
                    "output_name": out_name,
                    "project_file": project,
                    "download_url": self.mount_prefix + "/download?file=" + quote(str(out_path), safe=""),
                    "selections_count": len(resolved),
                    "total_ports": total_ports,
                })
                return

            if path == "/import":
                job = self.job()
                job.stage("Recebendo planilha", 0)
                if length <= 0:
                    self._send_json(400, {"error": "empty upload"})
                    return
                if length > _XLSX_MAX_BYTES:
                    self._send_json(413, {
                        "error": f"xlsx grande demais (limite "
                                 f"{_XLSX_MAX_BYTES // (1024*1024)} MB)",
                    })
                    return
                with self.session.lock:
                    rdb = self.sess().rdb
                if rdb is None:
                    self._send_json(409, {"error": "RDB nao carregado"})
                    return
                xlsx_bytes = self.rfile.read(length)
                try:
                    updates = parse_xlsx_to_updates(xlsx_bytes)
                except Exception as e:
                    logger.exception("falha parseando xlsx: %s", e)
                    self._send_json(422, {"error": f"xlsx invalido: {e}"})
                    return
                if not updates:
                    self._send_json(422, {
                        "error": "nenhum update valido encontrado no xlsx "
                                 "(verifique A1='Relay:' e A2='GLE:' em cada aba)",
                    })
                    return
                # O RDB de origem mora no cache por conteudo, que e'
                # compartilhado; a saida derivada e' desta sessao.
                out_path = self.sdir("out") / _with_suffix_before_ext(
                    Path(rdb.display_name), "_gle_comments_updated",
                ).name
                try:
                    result = apply_xlsx_updates_to_rdb(
                        rdb_info=rdb, xlsx_updates=updates, output_path=out_path,
                        job=job,
                    )
                except Exception as e:
                    logger.exception("falha aplicando updates: %s", e)
                    self._send_json(500, {"error": str(e)})
                    return
                if not result.get("ok"):
                    # Tudo-ou-nada: nenhuma selecao foi gravada, entao nao ha
                    # arquivo pra baixar nem pra publicar no acervo.
                    msg = result.get("error") or "Falha ao aplicar as alterações."
                    job.fail(msg)
                    logger.warning("[gle-exporter] import abortado: %s", msg)
                    self._send_json(400, result)
                    return
                out_abs = Path(result["output_path"]).resolve()
                # O RDB com os comentarios aplicados e' entrada da proxima
                # ferramenta: entra no acervo do projeto, ja extraido.
                result["project_file"] = self.publish_output(
                    out_abs, "GLE Exporter", job=job, logger=logger)
                job.finish("RDB atualizado")
                result["download_url"] = self.mount_prefix + "/download?file=" + quote(str(out_abs), safe="")
                result["output_name"] = out_abs.name
                logger.info(
                    "[gle-exporter] import: %d (relay,gle) processado(s), "
                    "%d ok, %d falha -> %s",
                    len(updates), result.get("succeeded", 0),
                    result.get("failed", 0), out_abs.name,
                )
                self._send_json(200, result)
                return

            self._send(404, "not found", "text/plain")

    return Handler
