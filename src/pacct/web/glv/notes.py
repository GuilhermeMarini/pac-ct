"""Notas, marca-texto e checkboxes de grupo de um rele.

Os tres arquivos ficam em `cache/` e o formato nao mudou:

    cache/groups_<chave>.json      {"version":1, "checked":[...]}
    cache/notes_<chave>.json       {"version":2, "html_relay":..., "pages":{...}}
    cache/highlights_<chave>.json  {"version":1, "pages":{pagina:{item:true}}}

O que mudou e' a CHAVE. Era o DEVID quando conectado e o nome do rele
sanitizado em modo desenho -- ou seja, o mesmo rele gravava em dois arquivos
diferentes conforme houvesse conexao ou nao, e a nota escrita antes de
conectar sumia da tela depois de conectar. Agora e' sempre o nome do rele no
RDB, que existe desde antes de qualquer conexao. Na primeira conexao,
`adopt_devid()` adota o que ficou gravado pelo DEVID.

O registro e' do PROCESSO, e nao da sessao: dois visitantes com o mesmo rele
aberto escrevem nos mesmos arquivos e precisam do mesmo objeto e da mesma
trava, senao o ultimo a salvar apaga o que o outro escreveu.
"""

from __future__ import annotations

import json
import os
import re
import threading

from pacct.paths import CACHE_DIR

# Limite de seguranca para o HTML do notepad (evita DoS via /note POST).
NOTE_MAX_BYTES = 256 * 1024


def note_key(relay_name: str) -> str:
    """Nome do rele no RDB -> chave de arquivo."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", relay_name or "") or "unknown"


def _path(kind: str, key: str):
    return CACHE_DIR / f"{kind}_{key}.json"


# -----------------------------------------------------------------------------
# Leitura / escrita dos tres arquivos
# -----------------------------------------------------------------------------

def _load_groups(key: str) -> set:
    p = _path("groups", key)
    if not p.is_file():
        return set()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return set(str(x) for x in d.get("checked", []))
    except (OSError, json.JSONDecodeError):
        return set()


def _load_note(key: str) -> tuple[str, dict]:
    """Retorna (html_relay, pages). Migra v1 ({"html": ...}) -> v2."""
    p = _path("notes", key)
    if not p.is_file():
        return "", {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", {}
    if d.get("version") in (None, 1) or "html" in d:
        return str(d.get("html", "") or ""), {}
    relay = str(d.get("html_relay", "") or "")
    raw_pages = d.get("pages", {}) or {}
    pages = {}
    if isinstance(raw_pages, dict):
        for k, v in raw_pages.items():
            if isinstance(v, str) and v:
                pages[str(k)] = v
    return relay, pages


def _load_highlights(key: str) -> dict:
    """Retorna {page_safe_id: {item_id: True}}."""
    p = _path("highlights", key)
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        pages = d.get("pages", {}) or {}
        out = {}
        for pg, items in pages.items():
            if not isinstance(items, dict):
                continue
            out[str(pg)] = {str(k): True for k, v in items.items() if v}
        return out
    except (OSError, json.JSONDecodeError):
        return {}


class NoteStore:
    """Estado anotado de UM rele, carregado do disco no primeiro acesso."""

    def __init__(self, key: str):
        self.key = key
        self._lock = threading.RLock()
        self.group_checked: set = _load_groups(key)
        self.note_relay, self.note_pages = _load_note(key)
        self.highlights: dict = _load_highlights(key)
        self._adopted = False

    # -- escrita ------------------------------------------------------------

    def set_group(self, group_id: str, checked: bool) -> None:
        with self._lock:
            if checked:
                self.group_checked.add(group_id)
            else:
                self.group_checked.discard(group_id)
            self._write("groups", {
                "version": 1,
                "key": self.key,
                "checked": sorted(self.group_checked),
            })

    def set_note(self, scope: str, page_id: str, html: str) -> None:
        with self._lock:
            if scope == "relay":
                self.note_relay = html
            elif html:
                self.note_pages[page_id] = html
            else:
                self.note_pages.pop(page_id, None)
            self._write("notes", {
                "version": 2,
                "key": self.key,
                "html_relay": self.note_relay,
                # Filtra paginas vazias (mantem o arquivo enxuto)
                "pages": {k: v for k, v in self.note_pages.items() if v},
            })

    def set_highlight(self, page: str, item_id: str, on: bool) -> None:
        with self._lock:
            page_dict = self.highlights.setdefault(page, {})
            if on:
                page_dict[item_id] = True
            else:
                page_dict.pop(item_id, None)
                if not page_dict:
                    self.highlights.pop(page, None)
            self._write("highlights", {
                "version": 1, "key": self.key, "pages": self.highlights,
            })

    def _write(self, kind: str, payload: dict) -> None:
        p = _path(kind, self.key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- leitura ------------------------------------------------------------

    def note_payload(self) -> dict:
        with self._lock:
            return {"key": self.key, "html_relay": self.note_relay,
                    "pages": dict(self.note_pages)}

    def group_payload(self) -> dict:
        with self._lock:
            return {"key": self.key, "checked": sorted(self.group_checked)}

    def highlight_payload(self) -> dict:
        with self._lock:
            return {"key": self.key, "pages": self.highlights}

    # -- migracao -----------------------------------------------------------

    def adopt_devid(self, devid: str, logger) -> list:
        """Primeira conexao: adota o que ficou gravado pelo DEVID.

        Por arquivo, e so quando o arquivo pela chave nova NAO existe: uma nota
        escrita antes de conectar ja esta no arquivo certo e nao pode ser
        sobrescrita pelo que veio do DEVID. Roda uma vez por store.
        """
        with self._lock:
            if self._adopted:
                return []
            self._adopted = True
            old_key = note_key(devid)
            if not devid or old_key == self.key:
                return []
            adopted = []
            for kind in ("groups", "notes", "highlights"):
                old, new = _path(kind, old_key), _path(kind, self.key)
                if not old.is_file() or new.is_file():
                    continue
                try:
                    os.replace(old, new)
                except OSError as e:
                    logger.warning("[glv] nao consegui adotar %s: %s", old.name, e)
                    continue
                adopted.append(kind)
            if adopted:
                self.group_checked = _load_groups(self.key)
                self.note_relay, self.note_pages = _load_note(self.key)
                self.highlights = _load_highlights(self.key)
                logger.info(
                    "[glv] notas adotadas do DEVID %r para a chave %r: %s",
                    devid, self.key, ", ".join(adopted))
            return adopted


class NoteRegistry:
    """Um NoteStore por chave, do processo."""

    def __init__(self):
        self._stores: dict[str, NoteStore] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> NoteStore:
        with self._lock:
            st = self._stores.get(key)
            if st is None:
                st = self._stores[key] = NoteStore(key)
            return st


NOTES = NoteRegistry()
