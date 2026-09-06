"""Notes, highlighter and group checkboxes of one relay.

The three files live in `cache/` and the format has not changed:

    cache/groups_<key>.json        {"version":1, "checked":[...]}
    cache/notes_<key>.json         {"version":2, "html_relay":..., "pages":{...}}
    cache/highlights_<key>.json    {"version":1, "pages":{page:{item:true}}}

What changed is the KEY. It was the DEVID when connected and the sanitised
relay name in drawing mode -- that is, the same relay wrote to two different
files depending on whether there was a connection or not, and a note written
before connecting vanished from the screen after connecting. Now it is always
the relay's name in the RDB, which exists from before any connection. On the
first connection, `adopt_devid()` adopts what was written under the DEVID.

The registry is the PROCESS's, not the session's: two visitors with the same
relay open write to the same files and need the same object and the same
lock, otherwise the last one to save wipes what the other wrote.
"""

from __future__ import annotations

import json
import os
import re
import threading

from pacct.paths import CACHE_DIR, atomic_write_text

# Limite de seguranca para o HTML do notepad (evita DoS via /note POST).
NOTE_MAX_BYTES = 256 * 1024


def note_key(relay_name: str) -> str:
    """Relay name in the RDB -> file key."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", relay_name or "") or "unknown"


def _path(kind: str, key: str):
    return CACHE_DIR / f"{kind}_{key}.json"


# -----------------------------------------------------------------------------
# Reading / writing the three files
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
    """Annotated state of ONE relay, loaded from disk on first access."""

    def __init__(self, key: str):
        self.key = key
        self._lock = threading.RLock()
        self.group_checked: set = _load_groups(key)
        self.note_relay, self.note_pages = _load_note(key)
        self.highlights: dict = _load_highlights(key)
        self._adopted = False

    # -- writing ------------------------------------------------------------

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
                # Filters out empty pages (keeps the file lean)
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
        # Atomic, like every other write in this project that a reader can
        # race: `_load_note` and friends treat unreadable JSON as an empty
        # file, so a truncated write is not an error the user ever sees -- it
        # is their notes quietly gone.
        atomic_write_text(_path(kind, self.key), json.dumps(payload, indent=2))

    # -- reading ------------------------------------------------------------

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
        """First connection: adopt what was written under the DEVID.

        Per file, and only when the file under the new key does NOT exist: a
        note written before connecting is already in the right file and must
        not be overwritten by what came from the DEVID. Runs once per store.
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
    """One NoteStore per key, process-wide."""

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
