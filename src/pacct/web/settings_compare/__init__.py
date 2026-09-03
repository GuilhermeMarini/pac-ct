"""
Settings Compare (Comparador de Ajustes): diff lado a lado de ajustes de reles
SEL extraidos de RDBs.

Fluxo do usuario:

  1. Seleciona um ou mais RDBs (uploads novos ou ja extraidos em `rdbs/`).
  2. Escolhe ate 7 reles da MESMA familia (3xx/4xx/7xx).
  3. Escolhe grupos de ajustes (abas: L1, S1, A1, etc.).
  4. Visualiza diff por aba; cada variavel mostra um veredito por linha:
     EQUAL / EQUAL_LOGIC_DIFF_COMMENT / EQUIVALENT / DIFFERENT.

Compartilha estilo visual com `vb_updater.py` e `vlan_mapper.py`. Estado por
processo (singleton); o usuario clica "<- Menu" para voltar.

    templates/  index.html
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from selfiles.rdb import RdbInfo
from selfiles.rdb import short_sha as _short_sha
from selfiles.selogic.catalog import (
    Family,
    family_from_relaytype,
    groups_for_family,
    is_relay_device,
)
from selfiles.selogic.compare import Kind as CmpKind
from selfiles.selogic.compare import compare
from selfiles.selogic.model import (
    RelayModel,
    Variable,
    normalize_relay,
)

from pacct.paths import SETTINGS_COMPARE_TEMPLATES_DIR
from pacct.web.project_files import library as filelib
from pacct.web.session import SessionHandler

_logger = logging.getLogger(__name__)


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (SETTINGS_COMPARE_TEMPLATES_DIR / name).read_text(encoding="utf-8")


# -----------------------------------------------------------------------------
# Estado da sessao
# -----------------------------------------------------------------------------

@dataclass
class _Session:
    """RDBs que o usuario carregou (referenciados por sha256 curto)."""
    rdbs: dict[str, RdbInfo] = field(default_factory=dict)
    # Cache de RelayModel normalizado por (rdb_sha, relay_name).
    relay_cache: dict[tuple[str, str], RelayModel] = field(default_factory=dict)




# -----------------------------------------------------------------------------
# Helpers de descoberta
# -----------------------------------------------------------------------------

def _register_rdb(st: _Session, lock, info: RdbInfo) -> str:
    """Salva o RdbInfo no estado da sessao e devolve a chave (sha curto)."""
    key = _short_sha(info.sha256)
    with lock:
        st.rdbs[key] = info
    return key


def _get_or_normalize_relay(
    st: _Session, lock, rdb_key: str, relay_name: str,
) -> RelayModel | None:
    """Le e normaliza um rele do RDB indicado (cache por (rdb_sha, name))."""
    with lock:
        info = st.rdbs.get(rdb_key)
    if info is None:
        return None
    cache_key = (info.sha256, relay_name)
    with lock:
        cached = st.relay_cache.get(cache_key)
    if cached is not None:
        return cached

    # Acha o RelayEntry correspondente e infere a familia.
    target_entry = None
    for r in info.relays:
        if r.name == relay_name:
            target_entry = r
            break
    if target_entry is None:
        return None
    fam = family_from_relaytype(target_entry.model)
    if fam is None:
        return None
    relay_dir = info.extract_dir / "Relays" / relay_name
    if not relay_dir.is_dir():
        return None
    model = normalize_relay(relay_dir, fam, relay_name=relay_name)
    with lock:
        st.relay_cache[cache_key] = model
    return model


def _rdb_summary(rdb_key: str, info: RdbInfo) -> dict:
    """Resumo de um RDB pra payload do frontend."""
    relays = []
    for r in info.relays:
        fam = family_from_relaytype(r.model)
        relays.append({
            "name": r.name,
            "model": r.model,
            "ip": r.ip,
            "family": fam,                  # None se nao for rele de protecao
            "is_relay": is_relay_device(r.model),
        })
    return {
        "key": rdb_key,
        "sha256": info.sha256,
        "filename": info.display_name,
        "reused": info.reused,
        "relays": relays,
    }


def _list_groups_for_relays(
    st: _Session, lock,
    rdb_relays: list[tuple[str, str]],
) -> tuple[Family | None, list[dict]]:
    """Dado um conjunto de (rdb_key, relay_name), devolve a familia comum e a
    lista de grupos (catalogo) com `present` indicando se o grupo existe em
    todos os reles selecionados."""
    if not rdb_relays:
        return None, []
    models: list[RelayModel] = []
    for rdb_key, relay_name in rdb_relays:
        m = _get_or_normalize_relay(st, lock, rdb_key, relay_name)
        if m is None:
            return None, []
        models.append(m)
    fams = {m.family for m in models}
    if len(fams) != 1:
        return None, []
    fam = next(iter(fams))

    catalog = groups_for_family(fam)
    out = []
    for g in catalog:
        present_in = [g.key in m.groups for m in models]
        out.append({
            "key": g.key,
            "label": g.label,
            "file": g.file_basename,
            "has_logic": g.has_logic,
            "present_in_all": all(present_in),
            "present_in_any": any(present_in),
            "present_count": sum(present_in),
            "total": len(models),
        })
    return fam, out


# -----------------------------------------------------------------------------
# Computacao do diff
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class _CellPayload:
    relay: str       # "rdb_key|relay_name"
    label: str       # nome amigavel pra UI ("relay_name")
    rdb_filename: str
    value: str       # `raw` do field
    body: str
    comment: str
    source_file: str
    source_lineno: int
    present: bool    # se o campo existe nesse rele


@dataclass(frozen=True)
class _FieldRow:
    field_name: str               # 'set' / 'reset' / 'input' / 'value' / etc.
    kind: str                     # 'logic' / 'number' / 'enum' / 'string'
    cells: list[_CellPayload]
    verdict: str                  # EQUAL / EQUAL_LOGIC_DIFF_COMMENT / EQUIVALENT / DIFFERENT / MISSING
    note: str | None = None


@dataclass(frozen=True)
class _VariableRow:
    name: str                     # 'PLT11' / 'LT01' / 'TR' / etc.
    var_kind: str                 # 'latch' / 'timer' / 'direct' / etc.
    fields: list[_FieldRow]
    worst_verdict: str            # piora entre fields


# DISPLACED = todos os campos comparaveis sao EQUAL/EQUAL_LOGIC_DIFF_COMMENT,
# mas o campo "slot" (metadado de posicao em SITM<n>/ALIAS<n>) difere. O bit
# esta registrado em todos os reles com o mesmo conteudo, so muda a posicao.
#
# VB_DIFF = os valores diferem apenas em substituicoes de tokens VB### (Virtual
# Bits). Estrutura identica em ambos os lados, so o numero do VB mudou.
# Tipico em SER (SITM=VB042 vs VB055) ou em logica (PSV01 := VB001 OR IN101
# vs PSV01 := VB042 OR IN101). Pode ser renumeracao benigna OU sinal
# totalmente diferente -- exige revisao humana, mas nao eh "definitivamente
# diferente".
def _verdict_severity(v: str) -> int:
    return {
        "EQUAL": 0,
        "EQUAL_LOGIC_DIFF_COMMENT": 1,
        "DISPLACED": 2,
        "EQUIVALENT": 3,
        "VB_DIFF": 4,
        "DIFFERENT": 5,
        "MISSING": 6,   # presente em alguns reles, ausente em outros
    }.get(v, 7)


_VB_TOKEN_RE = re.compile(r'\bVB\d+\b', re.IGNORECASE)


def _is_vb_only_diff(a: str, b: str) -> bool:
    """True se `a` e `b` diferem apenas em substituicoes de tokens VB###.

    Substitui cada `VB\\d+` por um placeholder; se os textos normalizados
    sao iguais E havia pelo menos um VB em algum lado, eh diff so-de-VB.
    """
    if a == b:
        return False
    norm_a = _VB_TOKEN_RE.sub('VB#', a)
    norm_b = _VB_TOKEN_RE.sub('VB#', b)
    if norm_a != norm_b:
        return False
    return bool(_VB_TOKEN_RE.search(a) or _VB_TOKEN_RE.search(b))


# -----------------------------------------------------------------------------
# Secoes -- subdivisao das abas de Relatorios por tipo de ajuste
# -----------------------------------------------------------------------------
#
# A aba "Relatorios" mistura ajustes de natureza muito diferente: chatter do
# SER, points/aliases do SOE, Signal Profile, Event Reporting (digital +
# analog), Fast Message Read, etc. Aqui dividimos essa aba em secoes
# semanticas (uma per familia) para o usuario localizar mais rapido.
#
# Variaveis que nao casam com nenhuma secao definida caem em "Outros" no
# rodape da aba -- nada eh perdido do diff.

@dataclass(frozen=True)
class _Section:
    key: str
    label: str
    order: int
    exact: frozenset[str] = frozenset()       # casamento exato pelo nome
    prefix: tuple[str, ...] = ()              # startswith
    kinds: frozenset[str] = frozenset()       # casamento pelo Variable.kind


_DEFAULT_SECTION = _Section("_other", "Outros", 9999)


_R_SECTIONS: dict[tuple[str, str], tuple[_Section, ...]] = {
    ("4xx", "R1"): (
        _Section("ser_chatter", "SER Chatter Criteria", 10,
                 exact=frozenset({"ESERDEL", "SRDLCNT", "SRDLTIM"})),
        _Section("ser_points", "SER Points e Aliases", 20,
                 kinds=frozenset({"ser_item"})),
        _Section("signal_profile_analog",
                 "Signal Profile - Quantidades Analogicas", 30,
                 prefix=("SPAQ",)),
        _Section("signal_profile", "Signal Profile (logica)", 40,
                 prefix=("SPAR", "SPEN")),
        _Section("event_reporting", "Event Reporting", 50,
                 exact=frozenset({"ERDIG", "SRATE", "LER", "PRE"})),
        _Section("event_reporting_analog",
                 "Event Reporting - Quantidades Analogicas", 60,
                 prefix=("ERAQ",)),
        _Section("event_reporting_digital",
                 "Event Reporting - Elementos Digitais", 70,
                 prefix=("ERDG",)),
    ),
    ("7xx", "R"): (
        _Section("ser_chatter", "SER Chatter Criteria", 10,
                 exact=frozenset({"ESERDEL", "SRDLCNT", "SRDLTIM"})),
        _Section("ser_triggers", "SER Trigger Lists", 20,
                 exact=frozenset({"SER1", "SER2", "SER3", "SER4"})),
        _Section("ser_aliases", "Relay Word Bit Aliases", 30,
                 exact=frozenset({"EALIAS"}),
                 kinds=frozenset({"ser_item"})),
        _Section("event_report", "Event Report", 40,
                 exact=frozenset({"ER", "LER", "PRE"})),
        _Section("hif_event_reporting", "HIF Event Reporting", 50,
                 exact=frozenset({"HIFLER", "HIFPRE"})),
        _Section("fast_message_read", "Fast Message Read", 60,
                 prefix=("FMR",)),
        _Section("fast_message_remote_analog",
                 "Fast Message Remote Analog", 70,
                 prefix=("RA",)),
        _Section("load_profile", "Load Profile", 80,
                 exact=frozenset({"LDLIST", "LDAR"})),
    ),
    ("3xx", "R"): (
        _Section("ser_lists", "SER", 10,
                 exact=frozenset({"SER1", "SER2", "SER3"})),
    ),
}


def _classify_variable(
    family: str, group_key: str, var_name: str, var_kind: str,
) -> _Section:
    """Decide a qual secao uma variavel pertence dentro da aba. Variavel sem
    secao definida cai em `_DEFAULT_SECTION`."""
    sections = _R_SECTIONS.get((family, group_key))
    if not sections:
        return _DEFAULT_SECTION
    for s in sections:
        if var_name in s.exact:
            return s
        if var_kind in s.kinds:
            return s
        if s.prefix and any(var_name.startswith(p) for p in s.prefix):
            return s
    return _DEFAULT_SECTION


def _compute_field_row(
    field_name: str,
    kind: CmpKind,
    cells: list[_CellPayload],
    dialect: str,
) -> _FieldRow:
    """Veredicto entre N cells presentes. Se alguma cell esta ausente,
    veredicto MISSING (sem fazer compare das presentes).

    Downgrades aplicados aqui:
      - campo `slot` com diff -> DISPLACED (bit registrado em posicao diferente)
      - diff so em tokens VB### -> VB_DIFF (renumeracao de Virtual Bits)
    """
    presents = [c for c in cells if c.present]
    if len(presents) < len(cells):
        return _FieldRow(
            field_name=field_name, kind=kind, cells=cells,
            verdict="MISSING",
            note=f"presente em {len(presents)} de {len(cells)} reles",
        )
    if len(presents) <= 1:
        return _FieldRow(
            field_name=field_name, kind=kind, cells=cells,
            verdict="EQUAL",
        )

    # Veredicto pairwise contra o primeiro; pega o pior.
    worst = "EQUAL"
    note: str | None = None
    a_val = presents[0].value
    a_body = presents[0].body or a_val
    saw_real_diff = False
    all_diffs_vb_only = True
    for c in presents[1:]:
        r = compare(a_val, c.value, kind=kind, dialect=dialect)
        if r.verdict not in ("EQUAL", "EQUAL_LOGIC_DIFF_COMMENT"):
            saw_real_diff = True
            b_body = c.body or c.value
            if not _is_vb_only_diff(a_body, b_body):
                all_diffs_vb_only = False
        if _verdict_severity(r.verdict) > _verdict_severity(worst):
            worst = r.verdict
            note = r.note

    if field_name == "slot" and worst == "DIFFERENT":
        worst = "DISPLACED"
    elif worst == "DIFFERENT" and saw_real_diff and all_diffs_vb_only:
        worst = "VB_DIFF"

    return _FieldRow(
        field_name=field_name, kind=kind, cells=cells,
        verdict=worst, note=note,
    )


def _collect_variables(
    models: list[tuple[str, RelayModel]],   # (relay_key, RelayModel)
    group_key: str,
) -> list[_VariableRow]:
    """Une o set de variaveis presentes nos N reles para o grupo dado, e
    monta uma `_VariableRow` por nome com `_FieldRow`s comparativos."""
    # Indexa variaveis por (relay_key) -> dict[var_name -> Variable]
    per_relay: list[tuple[str, dict[str, Variable], RelayModel]] = []
    for relay_key, model in models:
        gm = model.groups.get(group_key)
        per_relay.append((
            relay_key,
            (gm.variables if gm else {}),
            model,
        ))

    all_var_names = sorted({n for _, vs, _ in per_relay for n in vs.keys()})

    dialect = models[0][1].dialect if models else "keyword"
    rows: list[_VariableRow] = []
    for vname in all_var_names:
        # Para cada variavel, juntar todos os field_names existentes em
        # qualquer rele (em ordem canonica per kind).
        var_kind_seen = None
        all_field_names: list[str] = []
        # ordem preferida; o resto entra no fim em ordem alfabetica
        preferred_order = (
            "set", "reset", "input", "pickup", "dropout",
            "count_up", "count_down", "load", "preset",
            "fail_safe", "value",
        )
        seen: set[str] = set()
        for _, vs, _ in per_relay:
            v = vs.get(vname)
            if v is None:
                continue
            if var_kind_seen is None:
                var_kind_seen = v.kind
            for f in v.fields:
                if f.name not in seen:
                    seen.add(f.name)
                    all_field_names.append(f.name)
        # Reorder per preferred order
        ordered = [n for n in preferred_order if n in seen]
        ordered += sorted(n for n in all_field_names if n not in ordered)

        field_rows: list[_FieldRow] = []
        for fn in ordered:
            cells: list[_CellPayload] = []
            kind_seen: str | None = None
            for relay_key, vs, model in per_relay:
                v = vs.get(vname)
                fld = v.get_field(fn) if v else None
                if fld is None:
                    cells.append(_CellPayload(
                        relay=relay_key,
                        label=model.relay_name,
                        rdb_filename="",
                        value="",
                        body="",
                        comment="",
                        source_file="",
                        source_lineno=0,
                        present=False,
                    ))
                else:
                    if kind_seen is None:
                        kind_seen = fld.kind
                    cells.append(_CellPayload(
                        relay=relay_key,
                        label=model.relay_name,
                        rdb_filename="",
                        value=fld.raw,
                        body=fld.body,
                        comment=fld.comment,
                        source_file=fld.source.file if fld.source else "",
                        source_lineno=fld.source.lineno if fld.source else 0,
                        present=True,
                    ))
            fr = _compute_field_row(fn, kind_seen or "string", cells, dialect)
            field_rows.append(fr)

        # Veredicto da variavel = pior dos campos. DISPLACED/VB_DIFF ja foram
        # aplicados em _compute_field_row.
        worst = "EQUAL"
        for fr in field_rows:
            if _verdict_severity(fr.verdict) > _verdict_severity(worst):
                worst = fr.verdict

        rows.append(_VariableRow(
            name=vname, var_kind=var_kind_seen or "direct",
            fields=field_rows, worst_verdict=worst,
        ))
    return rows


def _compute_diff_payload(
    st: _Session, lock,
    relay_refs: list[dict],                # [{rdb_key, relay_name}]
    group_keys: list[str],
    on_progress=None,
) -> dict:
    """Roda o diff completo e devolve um payload JSON-serializavel.

    `on_progress(feitos, total, etapa)` alimenta a barra do cliente: normalizar
    os ajustes de cada rele e' a parte cara, e sao ate 5 reles por comparacao.
    """
    models: list[tuple[str, RelayModel]] = []
    for i, ref in enumerate(relay_refs):
        rdb_key = ref["rdb_key"]
        relay_name = ref["relay_name"]
        if on_progress is not None:
            on_progress(i, len(relay_refs) or 1, f"Lendo ajustes: {relay_name}")
        m = _get_or_normalize_relay(st, lock, rdb_key, relay_name)
        if m is None:
            return {"error": f"rele nao encontrado: {rdb_key}/{relay_name}"}
        key = f"{rdb_key}|{relay_name}"
        models.append((key, m))

    # Famil­ia comum (ja deveria estar validada no UI)
    fams = {m.family for _, m in models}
    if len(fams) != 1:
        return {"error": "selecao de reles cruza familias diferentes"}
    fam = next(iter(fams))

    # Preenche rdb_filename nas cells -- precisamos do RDB info
    with lock:
        rdb_filenames = {
            rk: st.rdbs[rk].display_name if rk in st.rdbs else ""
            for rk in {ref["rdb_key"] for ref in relay_refs}
        }

    def _var_payload(vr: _VariableRow) -> dict:
        return {
            "name": vr.name,
            "var_kind": vr.var_kind,
            "worst_verdict": vr.worst_verdict,
            "fields": [
                {
                    "name": fr.field_name,
                    "kind": fr.kind,
                    "verdict": fr.verdict,
                    "note": fr.note,
                    "cells": [
                        {
                            "relay": c.relay,
                            "label": c.label,
                            "rdb_filename": rdb_filenames.get(
                                c.relay.split("|")[0], ""
                            ),
                            "value": c.value,
                            "body": c.body,
                            "comment": c.comment,
                            "source_file": c.source_file,
                            "source_lineno": c.source_lineno,
                            "present": c.present,
                        }
                        for c in fr.cells
                    ],
                }
                for fr in vr.fields
            ],
        }

    groups_out = []
    for gk in group_keys:
        rows = _collect_variables(models, gk)

        # Agrupa variaveis por secao. Familias/grupos sem secao definida caem
        # todos numa unica secao "_default" (rotulo vazio -- frontend nao
        # renderiza header pra essa).
        sections_meta = _R_SECTIONS.get((fam, gk))
        if sections_meta:
            buckets: dict[str, list[_VariableRow]] = {s.key: [] for s in sections_meta}
            buckets[_DEFAULT_SECTION.key] = []
            for vr in rows:
                sec = _classify_variable(fam, gk, vr.name, vr.var_kind)
                buckets[sec.key].append(vr)
            ordered_sections = list(sections_meta) + [_DEFAULT_SECTION]
            sections_out = [
                {
                    "key": sec.key,
                    "label": sec.label,
                    "variables": [_var_payload(vr) for vr in buckets[sec.key]],
                }
                for sec in ordered_sections
                if buckets[sec.key]
            ]
        else:
            sections_out = [{
                "key": "_default",
                "label": "",
                "variables": [_var_payload(vr) for vr in rows],
            }]

        groups_out.append({"key": gk, "sections": sections_out})

    # Tambem devolve os labels dos grupos pra UI mostrar nome bonito.
    catalog = {g.key: g.label for g in groups_for_family(fam)}
    return {
        "family": fam,
        "dialect": models[0][1].dialect,
        "relays": [
            {
                "key": k,
                "label": m.relay_name,
                "relaytype": m.relaytype,
                "rdb_filename": rdb_filenames.get(k.split("|")[0], ""),
            }
            for k, m in models
        ],
        "group_labels": catalog,
        "groups": groups_out,
    }


# -----------------------------------------------------------------------------
# HTML / JS / CSS
# -----------------------------------------------------------------------------

INDEX_HTML = load_template("index.html")

# A navegacao numerada e' a mesma das nove telas -- mora em theme.py.


# -----------------------------------------------------------------------------
# Server
# -----------------------------------------------------------------------------

def build_settings_compare_handler(logger: logging.Logger, sessions) -> type:
    """Devolve a classe de handler do Settings Compare.

    Nao sobe servidor: quem serve e' o dispatcher unico de `pacct.web.mount`,
    que monta esse handler em `/settings-compare/`. Estado e uploads ficam por
    sessao (`self.sess()` / `self.sdir()`), nao por processo -- inclusive a
    limpeza: o diretorio inteiro da sessao some quando ela expira.
    """

    class Handler(SessionHandler):
        session_key = "settings-compare"
        state_factory = _Session
        server_sessions = sessions

        def _read_json(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw or b"{}")
            except (json.JSONDecodeError, ValueError):
                return None

        def _ensure_rdbs(self, keys) -> None:
            """Garante que cada chave curta esteja em `st.rdbs`.

            Rede de seguranca do mesmo principio do `/state`: o RDB esta no
            acervo do visitante, entao nenhuma rota precisa exigir que a
            pagina tenha "adotado" antes. Sem isto, uma aba que ficou aberta
            enquanto a sessao perdeu o estado responderia "rele nao
            encontrado" para um arquivo que esta ali.
            """
            st = self.sess()
            for key in keys:
                with self.session.lock:
                    if key in st.rdbs:
                        continue
                entry = self.library_entry(key, filelib.KIND_RDB)
                if entry is not None and entry.rdb is not None:
                    with self.session.lock:
                        st.rdbs[_short_sha(entry.rdb.sha256)] = entry.rdb

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, INDEX_HTML, "text/html; charset=utf-8")
                return
            if path == "/settings-state":
                # Sentinela usado pela home pra detectar que essa tool subiu.
                self._send_json(200, {"ok": True})
                return
            if path == "/state":
                # O acervo do PROJETO, nao "os RDBs que esta ferramenta
                # adotou". Havia duas listas na tela -- os arquivos do
                # projeto num seletor e os adotados numa lista ao lado -- e um
                # botao "Usar" que so' copiava um ponteiro de uma pra outra.
                # Era o resto de quando o comparador tinha upload proprio.
                # Listar tambem registra em `st.rdbs`, que e' o que faz
                # escolher um arquivo virar um clique: `/groups` e `/diff`
                # continuam achando o RDB pela chave curta sem etapa nenhuma.
                st = self.sess()
                lib = filelib.library_for(sessions, self.session)
                with self.session.lock:
                    entries = [e for e in lib.list(filelib.KIND_RDB)
                               if e.rdb is not None]
                    for e in entries:
                        st.rdbs[_short_sha(e.rdb.sha256)] = e.rdb
                    rdbs = [_rdb_summary(_short_sha(e.rdb.sha256), e.rdb)
                            for e in entries]
                rdbs.reverse()
                self._send_json(200, {"rdbs": rdbs})
                return
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path

            if path == "/groups":
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"error": "JSON invalido"})
                    return
                refs = body.get("relays") or []
                pairs = [(r["rdb_key"], r["relay_name"]) for r in refs]
                self._ensure_rdbs({k for k, _ in pairs})
                fam, groups = _list_groups_for_relays(self.sess(), self.session.lock, pairs)
                if fam is None:
                    self._send_json(400, {"error": "familia inconsistente ou rele invalido"})
                    return
                self._send_json(200, {"family": fam, "groups": groups})
                return

            if path == "/diff":
                job = self.job()
                job.stage("Lendo ajustes dos reles", 10)
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"error": "JSON invalido"})
                    return
                refs = body.get("relays") or []
                group_keys = body.get("groups") or []
                self._ensure_rdbs({r.get("rdb_key") for r in refs
                                   if r.get("rdb_key")})
                try:
                    payload = _compute_diff_payload(
                        self.sess(), self.session.lock, refs, group_keys,
                        on_progress=lambda d, t, st: job.fraction(st, d, t),
                    )
                except Exception as e:
                    logger.exception("[settings-compare] erro computando diff")
                    job.fail(str(e))
                    self._send_json(500, {"error": f"erro interno: {e}"})
                    return
                if "error" in payload:
                    job.fail(payload["error"])
                    self._send_json(400, payload)
                    return
                job.finish("Diff pronto")
                self._send_json(200, payload)
                return

            self._send(404, "not found", "text/plain")

    return Handler
