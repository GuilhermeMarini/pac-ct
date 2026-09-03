"""Leitura do GLE: paginas, bits e simbolos analogicos por pagina.

Saiu de `dashboard.py` sem mudanca. Nao fala com rele nem com HTTP: e' so
o que o diagrama precisa saber do arquivo pra montar a faixa de paginas, o
filtro de `/values?page=` e o indice da busca de variavel.
"""

from __future__ import annotations

import re

from selfiles.gle import element_info, is_const_symbol_name


def list_pages(gle_root) -> list[tuple[str, str]]:
    """Retorna [(name, safe_id)]."""
    pages = []
    for p in gle_root.findall(".//page"):
        name = p.get("name", "")
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", name) or f"page_{len(pages)}"
        pages.append((name, safe))
    return pages


def collect_bit_names(gle_root, relay_model=None) -> set[str]:
    """Todos os SYMBOLs no GLE que tem nome (candidatos a colorir).
    Constantes (nome literal numerico) sao excluidas -- nao existem na Relay
    Word do rele, sao apenas setpoints/literais no diagrama.
    SYMBOLs analogicos (AMV/PMV/MV/MAG/...) tambem sao excluidos: eles tem
    valor continuo do fast_meter, nao bit 0/1 da Relay Word.
    """
    names = set()
    for el in gle_root.findall(".//element"):
        if el.get("type") != "SYMBOL":
            continue
        info = element_info(el)
        nm = info["name"]
        if not nm or is_const_symbol_name(nm):
            continue
        if relay_model is not None and relay_model.is_analog_symbol(nm):
            continue
        names.add(nm)
    return names


def collect_analog_symbols_per_page(gle_root, relay_model) -> dict[str, dict[str, str]]:
    """
    Mapa safe_page_id -> {NAME: GROUP_KEY}: SYMBOLs analogicos referenciados
    em cada pagina. Vazio se relay_model nao tiver analog_groups (modelos sem
    JSON, ou JSON sem analog_symbols).
    Usado pelo /values pra filtrar quais analogs entregar por pagina e pelo
    JS pra agrupar a secao 'Analogicos' do painel.
    """
    out: dict[str, dict[str, str]] = {}
    if relay_model is None or not relay_model.analog_groups:
        for p in gle_root.findall(".//page"):
            name = p.get("name", "")
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", name) or f"page_{len(out)}"
            out[safe] = {}
        return out
    for p in gle_root.findall(".//page"):
        name = p.get("name", "")
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", name) or f"page_{len(out)}"
        per: dict[str, str] = {}
        for el in p.findall(".//element"):
            if (el.get("type") or "") != "SYMBOL":
                continue
            info = element_info(el)
            raw = info.get("name") or ""
            if not raw or is_const_symbol_name(raw):
                continue
            grp = relay_model.analog_group_for(raw)
            if grp is not None:
                per[raw.upper()] = grp.key
        out[safe] = per
    return out


def collect_bits_per_page(gle_root, relay_model=None) -> dict[str, set[str]]:
    """
    Mapa safe_page_id -> set(nomes de bits DIGITAIS a serem pollados naquela pagina).

    Inclui:
      - SYMBOLs (sinais I/O e Relay Word) -- exceto constantes numericas e
        exceto SYMBOLs analogicos (que tem valor continuo do fast_meter,
        tratados em collect_analog_symbols_per_page).
      - Bits derivados de blocos stateful (PLT/PCNDTIMER/AST/PSV no 4xx,
        LATCH/TIMER/COUNTER no 7xx, etc.), gerados pelo perfil do modelo
        via `relay_model.derived_bit_for(xml_type, instance, name)`.

    Se `relay_model` for None nao geramos bits derivados nem filtramos analogs
    (so SYMBOLs nomeados).
    """
    out = {}
    for p in gle_root.findall(".//page"):
        name = p.get("name", "")
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", name) or f"page_{len(out)}"
        names = set()
        for el in p.findall(".//element"):
            t = el.get("type") or ""
            info = element_info(el)
            raw = info.get("name") or ""
            if t == "SYMBOL":
                if not raw:
                    continue
                # Constantes (nome literal numerico) nao sao bits da Relay
                # Word -- nao tem sentido consultar no rele.
                if is_const_symbol_name(raw):
                    continue
                # Analogicos saem do pool digital -- tem valor continuo do FM.
                if relay_model is not None and relay_model.is_analog_symbol(raw):
                    continue
                names.add(raw.upper())
            elif relay_model is not None:
                # Blocos stateful (LATCH/TIMER/COUNTER, PLT/PCNDTIMER/AST/PSV...):
                # pergunta ao perfil do modelo qual bit derivado, se houver.
                le = el.find("logic_element")
                if le is None:
                    continue
                try:
                    inst = int(le.get("physical_instance_number") or 0)
                except ValueError:
                    inst = 0
                pname = le.get("physical_instance_name") or ""
                bit = relay_model.derived_bit_for(t, inst, pname)
                if bit:
                    names.add(bit.upper())
        out[safe] = names
    return out

