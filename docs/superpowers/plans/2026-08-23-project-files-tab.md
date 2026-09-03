# Arquivos do Projeto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the six tools' individual upload panels with one session-scoped, sha256-deduped "Arquivos do Projeto" tab that every tool picks its RDB/SCD from.

**Architecture:** A new package `sellib/web/project_files/` holds the model (`library.py`, pure logic), the routes (`handler.py`) and the page, mounted at `/arquivos/`. The library object lives in `Session.data` under one key shared by every tool. `mount.py` serves `GET <prefix>/library` for every mount — like `/progress` and `/theme.css` — and injects a `SelLibrary` client runtime beside `SelProgress`, so each tool's page swaps its drop zone for a picker and its `/rdb-upload` POST for a `/select-rdb` POST whose body is the tail of the old upload handler.

**Tech Stack:** Python 3.10+ stdlib (`http.server`, `dataclasses`, `hashlib`), `olefile`, `pytest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-23-project-files-tab-design.md`

## Global Constraints

- **Language:** user-facing strings (HTML, error messages) are **Portuguese, accented**. Code identifiers, comments and docstrings are **English for new code**; when editing an existing file, match that file's existing language.
- **Paths:** always resolve through `sellib/paths.py` constants. Never `Path(__file__).parent`, never a hardcoded path.
- **No module-level singletons for session state.** Helper functions take the state as a parameter. `_state = FileLibrary()` at module scope is a bug: it is per-process, not per-visitor.
- **`Session.subdir(name)`, not `SessionHandler.sdir(name)`** for the library directory. `sdir()` prefixes the caller's `session_key`, which would give each tool a different directory and silently un-share the library.
- **Tools define no colours, radii, font stacks or paddings.** Reach for a token from `sellib/web/themes/tokens.py` (`--bg`, `--surface`, `--border`, `--text`, `--ok`, `--s1..--s5`, `--radius`, …). Reuse the shared classes already in `themes/shell.py`: `.panel`, `.btn`, `.btn.pri`, `.drop`, `.filebar`, `.hash`, `.lbl`, `.lnk`, `.j`, `.j-ok`, `.j-err`.
- **The `<!--NAV:<key>-->` marker goes as the first child inside `<div class="shell">`, never inside `<header>`.** In régua `.shell` is a two-column grid whose first column *is* the nav; a marker left in the header collapses the page to ~200 px.
- **Tool routes stay absolute** (`/upload`, `/select-rdb`). The dispatcher strips the mount prefix; the injected shim re-adds it to `fetch`/`XMLHttpRequest`. Two exceptions need `self.mount_prefix` or a relative `./` by hand: `<a href download>` URLs and cross-page links.
- **Uploads go through `SelProgress.upload()`, never `fetch()`** — `fetch` cannot report upload progress and an RDB is 40–140 MB.
- **Never link a CDN.** A substation may have no internet.
- **Size ceilings:** RDB 500 MB, SCD 200 MB — defined once, in `library.py`.
- **Run tests with** `.venv/bin/python -m pytest tests/ -q`. If pytest is missing: `.venv/bin/python -m pip install -r requirements-dev.txt` (the one sanctioned manual pip step).
- **Never edit `selprotopy/`** — vendored and patched; a PreToolUse hook blocks it.

---

### Task 1: The library model

Pure logic: dedup, kind detection, storage paths, removal. No HTTP, no handler, no template. This is the only task with a full TDD cycle over new behaviour; everything after it is wiring.

**Files:**
- Create: `sellib/web/project_files/__init__.py`
- Create: `sellib/web/project_files/library.py`
- Test: `tests/test_project_files.py`

**Interfaces:**
- Consumes: `sellib.parsers.rdb.RdbInfo`; `sellib.web.session.SessionManager` (in tests only).
- Produces:
  - `LIBRARY_KEY: str = "arquivos"`, `KIND_RDB = "rdb"`, `KIND_SCD = "scd"`
  - `RDB_MAX_BYTES: int`, `SCD_MAX_BYTES: int`
  - `kind_for(filename: str) -> str | None`
  - `max_bytes_for(kind: str) -> int`
  - `scd_path_for(files_dir: Path, sha256: str) -> Path`
  - `FileEntry(sha256, kind, display_name, size, uploaded_at=…, detail="", rdb=None, scd_path=None)` with `.short_sha` and `.to_json() -> dict`
  - `FileLibrary()` with `.entries`, `.get(sha)`, `.list(kind=None)`, `.add(entry) -> tuple[FileEntry, bool]`, `.remove(sha) -> FileEntry | None`
  - `library_for(sessions, session) -> FileLibrary`
  - `files_dir(session) -> Path`

- [ ] **Step 1: Write the failing test**

Create `tests/test_project_files.py`:

```python
"""The project file library: dedup, kind detection, removal."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from sellib.web.project_files import library
from sellib.web.session import SessionManager


def _entry(sha: str, kind: str = "rdb", name: str = "projeto.rdb",
           size: int = 10, **kw) -> library.FileEntry:
    return library.FileEntry(sha256=sha, kind=kind, display_name=name,
                             size=size, uploaded_at=time.time(), **kw)


# -- kind detection ---------------------------------------------------------

def test_kind_comes_from_the_extension():
    assert library.kind_for("projeto.rdb") == library.KIND_RDB
    assert library.kind_for("subestacao.scd") == library.KIND_SCD
    assert library.kind_for("subestacao.xml") == library.KIND_SCD


def test_kind_detection_ignores_case():
    assert library.kind_for("PROJETO.RDB") == library.KIND_RDB
    assert library.kind_for("Subestacao.SCD") == library.KIND_SCD


def test_unknown_extension_is_not_a_project_file():
    assert library.kind_for("planilha.xlsx") is None
    assert library.kind_for("perfil.zip") is None
    assert library.kind_for("") is None


def test_each_kind_has_its_own_ceiling():
    assert library.max_bytes_for(library.KIND_RDB) == 500 * 1024 * 1024
    assert library.max_bytes_for(library.KIND_SCD) == 200 * 1024 * 1024


# -- dedup ------------------------------------------------------------------

def test_the_same_content_twice_is_one_entry():
    lib = library.FileLibrary()
    first, dup = lib.add(_entry("a" * 64, name="projeto.rdb"))
    assert dup is False
    second, dup = lib.add(_entry("a" * 64, name="copia-do-projeto.rdb"))
    assert dup is True
    assert second is first
    assert len(lib.entries) == 1


def test_the_first_name_wins_on_a_duplicate():
    lib = library.FileLibrary()
    lib.add(_entry("a" * 64, name="projeto.rdb"))
    entry, _ = lib.add(_entry("a" * 64, name="outro-nome.rdb"))
    assert entry.display_name == "projeto.rdb"


def test_same_name_different_content_are_two_entries():
    lib = library.FileLibrary()
    lib.add(_entry("a" * 64, name="projeto.rdb"))
    lib.add(_entry("b" * 64, name="projeto.rdb"))
    assert len(lib.entries) == 2
    assert [e.sha256 for e in lib.list()] == ["a" * 64, "b" * 64]


def test_listing_filters_by_kind():
    lib = library.FileLibrary()
    lib.add(_entry("a" * 64, kind=library.KIND_RDB, name="projeto.rdb"))
    lib.add(_entry("b" * 64, kind=library.KIND_SCD, name="sub.scd"))
    assert [e.display_name for e in lib.list(library.KIND_RDB)] == ["projeto.rdb"]
    assert [e.display_name for e in lib.list(library.KIND_SCD)] == ["sub.scd"]
    assert len(lib.list()) == 2


# -- paths and payload ------------------------------------------------------

def test_the_scd_filename_comes_from_the_hash(tmp_path):
    p = library.scd_path_for(tmp_path, "c" * 64)
    assert p == tmp_path / ("c" * 12 + ".scd")


def test_the_payload_carries_what_the_listing_shows():
    e = _entry("d" * 64, name="projeto.rdb", size=1234)
    e.detail = "12 relés"
    payload = e.to_json()
    assert payload["sha256"] == "d" * 64
    assert payload["short_sha"] == "d" * 12
    assert payload["kind"] == "rdb"
    assert payload["name"] == "projeto.rdb"
    assert payload["size"] == 1234
    assert payload["detail"] == "12 relés"
    # The RdbInfo and the on-disk path never cross to the browser.
    assert "rdb" not in payload
    assert "scd_path" not in payload


# -- removal ----------------------------------------------------------------

def test_removing_an_scd_drops_the_entry_and_the_file(tmp_path):
    lib = library.FileLibrary()
    scd = tmp_path / "abc.scd"
    scd.write_bytes(b"<SCL/>")
    lib.add(_entry("e" * 64, kind=library.KIND_SCD, name="sub.scd",
                   scd_path=scd))
    removed = lib.remove("e" * 64)
    assert removed is not None
    assert removed.display_name == "sub.scd"
    assert lib.entries == {}
    assert not scd.exists()


def test_removing_an_rdb_leaves_the_shared_extraction_alone(tmp_path):
    """cache/rdb/<sha>/ has no owner: it is shared between visitors and swept
    by age. One visitor tidying their project must not delete it."""
    lib = library.FileLibrary()
    extraction = tmp_path / ("f" * 64)
    extraction.mkdir()
    (extraction / "source.rdb").write_bytes(b"x")
    lib.add(_entry("f" * 64, kind=library.KIND_RDB, name="projeto.rdb"))
    lib.remove("f" * 64)
    assert lib.entries == {}
    assert (extraction / "source.rdb").exists()


def test_removing_an_unknown_sha_is_not_an_error():
    lib = library.FileLibrary()
    assert lib.remove("0" * 64) is None


def test_removing_an_scd_whose_file_is_already_gone_is_not_an_error(tmp_path):
    lib = library.FileLibrary()
    lib.add(_entry("9" * 64, kind=library.KIND_SCD, name="sub.scd",
                   scd_path=tmp_path / "nao-existe.scd"))
    assert lib.remove("9" * 64) is not None


# -- one library per visitor, shared by every tool --------------------------

def test_every_tool_in_a_session_sees_the_same_library(tmp_path):
    mgr = SessionManager(root=tmp_path, logger=logging.getLogger("test"))
    sess, _ = mgr.resolve(None)
    # Two different tools asking: same object, or the library is not shared.
    assert library.library_for(mgr, sess) is library.library_for(mgr, sess)


def test_two_visitors_have_two_libraries(tmp_path):
    mgr = SessionManager(root=tmp_path, logger=logging.getLogger("test"))
    a, _ = mgr.resolve(None)
    b, _ = mgr.resolve(None)
    assert library.library_for(mgr, a) is not library.library_for(mgr, b)


def test_the_library_directory_is_not_prefixed_by_a_tool_key(tmp_path):
    """Session.subdir, not SessionHandler.sdir -- the latter prefixes the
    caller's session_key and would hand each tool its own directory."""
    mgr = SessionManager(root=tmp_path, logger=logging.getLogger("test"))
    sess, _ = mgr.resolve(None)
    d = library.files_dir(sess)
    assert d.name == "files"
    assert d.parent == sess.dir
    assert d.is_dir()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_project_files.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'sellib.web.project_files'`.

- [ ] **Step 3: Write the package**

Create `sellib/web/project_files/__init__.py`:

```python
"""Arquivos do Projeto: o acervo de RDB e SCD de um visitante.

    library.py   o acervo em si -- dedup por sha256, sem saber que ha HTTP
    handler.py   as rotas de /arquivos/
    client.py    o runtime `SelLibrary`, injetado em toda pagina
    templates/   library.html

Antes disto cada ferramenta tinha o proprio painel de upload: o mesmo RDB de
40-140 MB era transferido uma vez por ferramenta, e dois uploads do mesmo SCD
eram dois arquivos. Aqui o acervo e' um so por sessao, e as ferramentas
escolhem dentro dele.
"""

from __future__ import annotations

from sellib.paths import PROJECT_FILES_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (PROJECT_FILES_TEMPLATES_DIR / name).read_text(encoding="utf-8")
```

Create `sellib/web/project_files/library.py`:

```python
"""One visitor's project files, keyed by the sha256 of their content.

Two uploads of the same bytes are the same file, so the key is the content and
never the name: `projeto.rdb` from two different substations coexist, and the
same substation sent twice does not.

This module knows nothing about HTTP -- `handler.py` serves the routes. It
also holds no lock of its own: callers hold `Session.lock`, the way every
other tool already guards its own state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from sellib.parsers.rdb import RdbInfo

# The key the library lives under in `Session.data`. It is deliberately the
# SAME for every tool: that is what makes the library one library.
LIBRARY_KEY = "arquivos"

KIND_RDB = "rdb"
KIND_SCD = "scd"

# The single definition of the ceilings. They used to be copied into six tool
# modules, and the copies had already drifted apart.
RDB_MAX_BYTES = 500 * 1024 * 1024
SCD_MAX_BYTES = 200 * 1024 * 1024

_EXTENSIONS = {".rdb": KIND_RDB, ".scd": KIND_SCD, ".xml": KIND_SCD}


def kind_for(filename: str) -> "str | None":
    """The file's kind, by extension. None means it is not a project file."""
    return _EXTENSIONS.get(Path(filename or "").suffix.lower())


def max_bytes_for(kind: str) -> int:
    return RDB_MAX_BYTES if kind == KIND_RDB else SCD_MAX_BYTES


def scd_path_for(files_dir: Path, sha256: str) -> Path:
    """Where this content's SCD lives. The name comes from the hash, never
    from the upload: two files called `sub.scd` with different content must be
    able to sit side by side."""
    return Path(files_dir) / f"{sha256[:12]}.scd"


@dataclass
class FileEntry:
    """One file in the project."""

    sha256: str
    kind: str
    display_name: str
    size: int
    uploaded_at: float = field(default_factory=time.time)
    # One line for the listing: "12 relés" / "31 IEDs".
    detail: str = ""
    # kind == KIND_RDB: the extraction, already done at upload time.
    rdb: "RdbInfo | None" = None
    # kind == KIND_SCD: the file inside this session's directory.
    scd_path: "Path | None" = None

    @property
    def short_sha(self) -> str:
        return self.sha256[:12]

    def to_json(self) -> dict:
        """What the browser is allowed to see. Neither the RdbInfo nor the
        on-disk path crosses: a page has no use for either, and the path is
        inside another visitor's sandbox as far as it is concerned."""
        return {
            "sha256": self.sha256,
            "short_sha": self.short_sha,
            "kind": self.kind,
            "name": self.display_name,
            "size": self.size,
            "uploaded_at": self.uploaded_at,
            "detail": self.detail,
        }


class FileLibrary:
    """A visitor's files, in arrival order, without repeats."""

    def __init__(self) -> None:
        self.entries: "dict[str, FileEntry]" = {}

    def get(self, sha256: str) -> "FileEntry | None":
        return self.entries.get(sha256)

    def list(self, kind: "str | None" = None) -> "list[FileEntry]":
        return [e for e in self.entries.values()
                if kind is None or e.kind == kind]

    def add(self, entry: FileEntry) -> "tuple[FileEntry, bool]":
        """Store `entry`; return `(entry, already_there)`.

        Idempotent on purpose, so a caller that hashed, then did slow work
        outside the lock, can add unconditionally. The FIRST upload's name is
        the one that stays: renaming an entry under a tool that is already
        showing it on screen is worse than ignoring the second name.
        """
        existing = self.entries.get(entry.sha256)
        if existing is not None:
            return existing, True
        self.entries[entry.sha256] = entry
        return entry, False

    def remove(self, sha256: str) -> "FileEntry | None":
        """Drop the entry; return what left, or None.

        An SCD's file goes with it -- it belongs to this session. An RDB's
        extraction (`cache/rdb/<sha256>/`) does NOT: it has no owner, is
        shared between visitors and across restarts, and is pruned by age in
        `rdb_cache.sweep()`.
        """
        entry = self.entries.pop(sha256, None)
        if entry is not None and entry.scd_path is not None:
            try:
                Path(entry.scd_path).unlink(missing_ok=True)
            except OSError:
                pass
        return entry


def library_for(sessions, session) -> FileLibrary:
    """This visitor's library. A function taking the session, never a
    module-level singleton: a singleton is per PROCESS, and the library is per
    visitor (see the docstring of `sellib/web/session.py`)."""
    return sessions.state(session, LIBRARY_KEY, FileLibrary)


def files_dir(session) -> Path:
    """The library's directory: `cache/sessions/<sid>/files/`.

    `Session.subdir`, and NOT `SessionHandler.sdir` -- the second one prefixes
    the calling tool's `session_key`, which would give every tool a different
    directory and undo the whole point of a shared library.
    """
    return session.subdir("files")
```

- [ ] **Step 4: Add the templates path constant**

In `sellib/paths.py`, after the `DNP_TEMPLATES_DIR` block, add:

```python
# HTML template for the Arquivos do Projeto tab. Same reason as the GLV and
# the DNP map: a real .html file, because most of it is JavaScript.
PROJECT_FILES_TEMPLATES_DIR: Path = (
    PROJECT_ROOT / "sellib" / "web" / "project_files" / "templates"
)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_project_files.py -q`
Expected: PASS, 17 tests. If `PROJECT_FILES_TEMPLATES_DIR` is missing the import of `__init__.py` fails — that is Step 4.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — the pre-existing 116 tests plus the new ones.

- [ ] **Step 7: Commit**

```bash
git add sellib/web/project_files/__init__.py sellib/web/project_files/library.py \
        sellib/paths.py tests/test_project_files.py
git commit -m "Add the project file library: one sha256-deduped acervo per visitor"
```

---

### Task 2: The `/arquivos/` page and its routes

**Files:**
- Create: `sellib/web/project_files/handler.py`
- Create: `sellib/web/project_files/templates/library.html`
- Modify: `sellib/web/dashboard.py` (the `mounts` list in `main()`, around line 166)
- Modify: `sellib/web/mount.py` (the common-route block in `_dispatch`, around line 262; the module docstring, around line 40)
- Test: `tests/test_project_files.py` (append)

**Interfaces:**
- Consumes: everything Task 1 produced; `sellib.parsers.rdb.process_upload`, `sha256_bytes`, `sanitize_name`; `sellib.parsers.scd.load_scd`; `sellib.web.session.SessionHandler`.
- Produces: `build_project_files_handler(logger, sessions) -> type` (a `SessionHandler` subclass with `session_key == "arquivos"`); the routes `GET /`, `POST /upload`, `POST /remove`; the dispatcher route `GET <prefix>/library[?kind=rdb|scd]` → `{"files": [entry.to_json(), …]}`; the module constant `LIBRARY_HTML`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_project_files.py`:

```python
# -- the page ---------------------------------------------------------------

def test_the_nav_marker_sits_inside_the_shell_not_the_header():
    """In régua, `.shell` is a two-column grid whose first column IS the nav.
    A marker left in `<header>` collapses the page to about 200px wide."""
    from sellib.web.project_files import handler as pf_handler

    html = pf_handler.LIBRARY_HTML
    shell = html.index('<div class="shell">')
    marker = html.index("<!--NAV:arquivos-->")
    header_end = html.index("</header>")
    assert marker > shell
    assert marker > header_end


def test_the_page_never_bakes_a_nav_at_import_time():
    """The three directions do not share nav markup; resolving the marker
    here would freeze one direction's markup into all three."""
    from sellib.web.project_files import handler as pf_handler

    html = pf_handler.LIBRARY_HTML
    for frozen in ('class="toc"', 'class="strip"', 'class="tabs"'):
        assert frozen not in html


def test_the_handler_owns_the_library_key(tmp_path):
    from sellib.web.project_files import handler as pf_handler

    log = logging.getLogger("test")
    mgr = SessionManager(root=tmp_path, logger=log)
    cls = pf_handler.build_project_files_handler(log, mgr)
    assert cls.session_key == library.LIBRARY_KEY
    assert cls.state_factory is library.FileLibrary
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_project_files.py -q -k "nav_marker or bakes or library_key"`
Expected: FAIL — `ModuleNotFoundError: No module named 'sellib.web.project_files.handler'`.

- [ ] **Step 3: Write the template**

Create `sellib/web/project_files/templates/library.html`:

```html
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arquivos do Projeto &mdash; Comissionamento SEL</title>
<style>
  .files { width: 100%; border-collapse: collapse; }
  .files th { text-align: left; }
  .files td, .files th { padding: var(--s2) var(--s3); }
  .files tbody tr { border-top: 1px solid var(--border); }
  .files tr.dup { background: var(--surface-2); }
  .files .num { text-align: right; white-space: nowrap; }
  .empty { color: var(--text-2); padding: var(--s5); text-align: center; }
</style>
</head>
<body>
<div class="page">
<header>
  <div>
    <h1>Arquivos do Projeto</h1>
    <div class="sub">RDB e SCD que as ferramentas v&atilde;o usar</div>
  </div>
  <span class="spacer"></span>
  <a class="lnk" href="/">&larr; Menu</a>
</header>
<div class="shell">
<!--NAV:arquivos-->
<main class="col-main">
  <div class="panel">
    <label class="drop" id="drop">
      <span class="ic">[+]</span>
      <b>Enviar arquivo</b>
      <small>Clique ou arraste um .rdb, .scd ou .xml</small>
      <input type="file" id="file" accept=".rdb,.scd,.xml" style="display:none">
    </label>
    <div id="status"></div>
  </div>

  <div class="panel">
    <h2>No projeto</h2>
    <table class="files">
      <thead>
        <tr>
          <th>Nome</th><th>Tipo</th><th class="num">Tamanho</th>
          <th>Conte&uacute;do</th><th>sha256</th><th></th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
    <div class="empty" id="empty">
      Nenhum arquivo ainda. Envie o RDB e o SCD do projeto acima &mdash;
      as ferramentas escolhem daqui.
    </div>
  </div>
</main>
</div>
</div>
<script>
const $ = (id) => document.getElementById(id);

function setStatus(msg, kind) {
  const el = $('status');
  el.textContent = msg || '';
  el.className = kind ? 'j j-' + kind : '';
}

function fmtSize(n) {
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(0) + ' kB';
  return n + ' B';
}

function escHtml(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

let _rows = [];

function render(highlightSha) {
  const tb = $('rows');
  tb.innerHTML = '';
  $('empty').style.display = _rows.length ? 'none' : '';
  _rows.forEach((f) => {
    const tr = document.createElement('tr');
    if (f.sha256 === highlightSha) tr.className = 'dup';
    tr.innerHTML =
      '<td>' + escHtml(f.name) + '</td>' +
      '<td>' + f.kind.toUpperCase() + '</td>' +
      '<td class="num">' + fmtSize(f.size) + '</td>' +
      '<td>' + escHtml(f.detail) + '</td>' +
      '<td class="hash">' + f.short_sha + '</td>' +
      '<td><button class="btn" type="button">Remover</button></td>';
    tr.querySelector('button').addEventListener('click', () => remove(f));
    tb.appendChild(tr);
  });
}

async function load(highlightSha) {
  const r = await fetch('/library');
  const d = await r.json();
  _rows = d.files || [];
  render(highlightSha);
}

async function remove(f) {
  if (!confirm('Remover "' + f.name + '" do projeto?')) return;
  const r = await SelProgress.post('/remove', {sha256: f.sha256},
                                   {label: 'Removendo'});
  if (!r.ok) {
    setStatus('Falha ao remover: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  setStatus('"' + f.name + '" removido do projeto.', 'ok');
  load();
}

async function upload(file) {
  if (!file) return;
  setStatus('Enviando ' + file.name + '...', '');
  // SelProgress.upload, never fetch: only XMLHttpRequest.upload.onprogress
  // can report progress, and an RDB is 40-140 MB.
  const r = await SelProgress.upload('/upload', file, {
    headers: {'X-Filename': encodeURIComponent(file.name)},
    label: 'Enviando ' + file.name,
    doneLabel: 'Arquivo carregado.',
  });
  if (!r.ok) {
    setStatus('Falha no envio: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  const d = r.data || {};
  const e = d.entry || {};
  if (d.duplicate) {
    setStatus('Já está no projeto (enviado como "' + e.name + '").', 'warn');
  } else {
    setStatus('"' + e.name + '" adicionado ao projeto.', 'ok');
  }
  load(e.sha256);
}

const drop = $('drop');
const input = $('file');
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); e.stopPropagation(); drop.classList.add('drag');
}));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); e.stopPropagation(); drop.classList.remove('drag');
}));
drop.addEventListener('drop', (e) => upload(e.dataTransfer.files[0]));
input.addEventListener('change', () => { upload(input.files[0]); input.value = ''; });

load();
</script>
</body>
</html>
```

- [ ] **Step 4: Write the handler**

Create `sellib/web/project_files/handler.py`:

```python
"""Routes for the Arquivos do Projeto tab.

Absolute routes, like every other tool: the single dispatcher in `mount.py`
strips the mount prefix before delegating, and the injected shim re-adds it to
the page's `fetch` calls.

Listing is NOT served here. `GET <prefix>/library` is served by the dispatcher
for every mount, next to `/progress` and `/theme.css`, because every tool's
picker needs it -- see `mount.py`.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import unquote, urlparse

from sellib.parsers import rdb as rdb_loader
from sellib.parsers import scd as scd_loader
from sellib.web.project_files import library, load_template
from sellib.web.session import SessionHandler

# The `<!--NAV:arquivos-->` marker inside `.shell` is resolved per request by
# `mount.py:_resolve_markup()`, with the visitor's theme in hand. It must NOT
# be substituted here: the three directions do not share nav markup (`.toc`,
# `.strip`/`.borne`, `.tabs`), so resolving at import time would freeze one
# direction's markup into all three.
LIBRARY_HTML = load_template("library.html")


def build_project_files_handler(logger: logging.Logger, sessions) -> type:
    """Return the tab's handler class. Opens no socket: `mount.py` serves it."""

    class Handler(SessionHandler):
        # The same key the library itself lives under. Intentional: this tab's
        # state IS the library.
        session_key = library.LIBRARY_KEY
        state_factory = library.FileLibrary
        server_sessions = sessions

        # -- GET ------------------------------------------------------------

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "", "/index.html"):
                self._send(200, LIBRARY_HTML, "text/html; charset=utf-8")
                return
            self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        # -- POST -----------------------------------------------------------

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/upload":
                self._do_upload()
            elif path == "/remove":
                self._do_remove()
            else:
                self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                return {}

        def _do_upload(self):
            job = self.job()
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                job.fail("Arquivo vazio.")
                self._send_json(400, {"ok": False, "error": "Arquivo vazio."})
                return

            filename = unquote(self.headers.get("X-Filename") or "")
            kind = library.kind_for(filename)
            if kind is None:
                msg = "Tipo não reconhecido — envie .rdb, .scd ou .xml."
                job.fail(msg)
                self._send_json(400, {"ok": False, "error": msg})
                return

            cap = library.max_bytes_for(kind)
            if length > cap:
                msg = f"Arquivo grande demais (limite {cap // (1024*1024)} MB)."
                job.fail(msg)
                self._send_json(413, {"ok": False, "error": msg})
                return

            job.stage("Recebendo arquivo", 0)
            data = self.rfile.read(length)
            sha = rdb_loader.sha256_bytes(data)

            lib = self.sess()
            with self.session.lock:
                existing = lib.get(sha)
            if existing is not None:
                # Nothing written, nothing extracted: the bytes are already
                # in this project under the name they arrived with first.
                job.finish("Arquivo já estava no projeto")
                self._send_json(200, {"ok": True, "duplicate": True,
                                      "entry": existing.to_json()})
                return

            if kind == library.KIND_RDB:
                entry = self._build_rdb_entry(data, filename, sha, job)
            else:
                entry = self._build_scd_entry(data, filename, sha, job)
            if entry is None:
                return  # the builder already answered

            with self.session.lock:
                entry, duplicate = lib.add(entry)
            logger.info("[arquivos] %s '%s' (%s) no projeto: %s",
                        entry.kind.upper(), entry.display_name,
                        entry.short_sha, entry.detail)
            job.finish(entry.detail or "Arquivo carregado")
            self._send_json(200, {"ok": True, "duplicate": duplicate,
                                  "entry": entry.to_json()})

        def _build_rdb_entry(self, data, filename, sha, job):
            """The RDB's bytes never reach the session directory: the
            content-addressed cache already holds them at
            `cache/rdb/<sha256>/source.rdb`, shared between visitors."""
            try:
                info = rdb_loader.process_upload(
                    data, filename,
                    on_progress=lambda d, t, s: job.fraction(s, d, t),
                )
            except (OSError, ValueError) as e:
                job.fail(str(e))
                self._send_json(500, {"ok": False,
                                      "error": f"falha ao salvar/extrair: {e}"})
                return None
            except Exception as e:      # olefile raises several types
                job.fail(str(e))
                self._send_json(400, {"ok": False, "error": f"RDB inválido: {e}"})
                return None
            return library.FileEntry(
                sha256=sha, kind=library.KIND_RDB,
                display_name=info.display_name, size=len(data),
                detail=f"{len(info.relays)} relé(s)", rdb=info,
            )

        def _build_scd_entry(self, data, filename, sha, job):
            job.stage("Lendo SCD", 40)
            target = library.scd_path_for(
                library.files_dir(self.session), sha)
            try:
                target.write_bytes(data)
            except OSError as e:
                job.fail(str(e))
                self._send_json(500, {"ok": False,
                                      "error": f"falha ao salvar SCD: {e}"})
                return None
            try:
                ieds = scd_loader.load_scd(target)
            except Exception as e:
                target.unlink(missing_ok=True)
                job.fail(str(e))
                self._send_json(400, {"ok": False, "error": f"SCD inválido: {e}"})
                return None
            if not ieds:
                # A file that does not validate never enters the library --
                # no half-entries for a tool to trip over later.
                target.unlink(missing_ok=True)
                job.fail("nenhum IED encontrado no SCD")
                self._send_json(400, {"ok": False,
                                      "error": "nenhum IED encontrado no SCD"})
                return None
            return library.FileEntry(
                sha256=sha, kind=library.KIND_SCD,
                display_name=rdb_loader.sanitize_name(filename) or "arquivo.scd",
                size=len(data), detail=f"{len(ieds)} IED(s)", scd_path=target,
            )

        def _do_remove(self):
            sha = (self._body().get("sha256") or "").strip()
            lib = self.sess()
            with self.session.lock:
                removed = lib.remove(sha)
            if removed is None:
                self._send_json(404, {"ok": False,
                                      "error": "Arquivo não está no projeto."})
                return
            logger.info("[arquivos] %s '%s' removido do projeto",
                        removed.kind.upper(), removed.display_name)
            self._send_json(200, {"ok": True, "removed": removed.to_json()})

    return Handler
```

- [ ] **Step 5: Serve the list from the dispatcher**

This route is Task 4's in the original plan; it moved here because this tab's page is its first consumer — `load()` in `library.html` calls `fetch('/library')`, so without it Step 7 below cannot pass.

In `sellib/web/mount.py`, inside `Dispatcher._dispatch`, next to the existing `/theme.css` handling (the `tail == ...` block):

```python
            if tail == "/library" and verb == "do_GET":
                self._serve_library()
                return
```

and the method, beside `_serve_progress`:

```python
        def _serve_library(self):
            """O acervo do visitante, servido em QUALQUER prefixo.

            Fica aqui, e nao numa ferramenta, pelo mesmo motivo de /progress e
            /theme.css: as seis paginas precisam dele, e uma ferramenta nao e'
            dona da lista de arquivos de outra.
            """
            from urllib.parse import parse_qs
            from sellib.web.project_files import library as filelib

            kind = (parse_qs(urlparse(self.path).query).get("kind") or [""])[0]
            files = []
            if sessions is not None and getattr(self, "session", None) is not None:
                lib = filelib.library_for(sessions, self.session)
                with self.session.lock:
                    files = [e.to_json() for e in lib.list(kind or None)]
            body = json.dumps({"files": files}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
```

Also extend the module docstring's list of common routes (around line 40) from "`/progress`, `/theme.css` e `/static/...`" to include `/library`, saying it is the visitor's project file list and that every tool's picker reads it.

- [ ] **Step 6: Mount it**

In `sellib/web/dashboard.py`, inside `main()`, add the import beside the others (near line 118) and the mount as the **first entry after the home** in the `mounts` list (near line 166), so its order matches its place in the nav:

```python
    from sellib.web.project_files.handler import build_project_files_handler
```

```python
    mounts = [
        Mount("/", build_home_handler(logger), "Home"),
        Mount("/arquivos",
              build_project_files_handler(logger, sessions),
              "Arquivos do Projeto"),
        Mount("/glv", build_glv_handler(logger, sessions, glv_defaults),
              "Graphical Logic Viewer"),
        # ... the rest unchanged
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_project_files.py -q`
Expected: PASS, 20 tests.

- [ ] **Step 8: Verify the page in a browser**

Run: `python3 app.py --web`
Then, at `http://localhost:8765/arquivos/`:
1. The page loads and shows the empty state.
2. Upload an RDB from `rdbs/` or `samples/` — the progress bar moves, the row appears with `N relé(s)` and a 12-char sha.
3. Upload the **same** RDB again — no second row; the status reads "Já está no projeto (enviado como …)" and the existing row is highlighted.
4. Upload an SCD — a second row appears with `N IED(s)`.
5. Upload a `.xlsx` — rejected with "Tipo não reconhecido".
6. Remove the SCD — the row goes, and `cache/sessions/<sid>/files/` no longer holds its `.scd`.
7. Confirm `cache/rdb/<sha256>/` still exists after removing the RDB row.

- [ ] **Step 9: Commit**

```bash
git add sellib/web/project_files/handler.py \
        sellib/web/project_files/templates/library.html \
        sellib/web/mount.py sellib/web/dashboard.py tests/test_project_files.py
git commit -m "Serve the Arquivos do Projeto tab at /arquivos/"
```

---

### Task 3: The tab in the three directions

The nav is not one nav: folha writes `.toc`, régua `.strip`/`.borne`, caderno `.tabs`/`.tab`. Each direction gets the entry in its own markup.

Tool numbering stays `1..9` in all three. This is not cosmetic: régua's home cards read "Borne *i* · ligado" and must keep matching its strip, and all three `home()` renderers index `TOOLS` from 1. So Menu keeps `0` and Arquivos takes **`A`**.

**Files:**
- Modify: `sellib/web/themes/items.py` (after `MENU_ITEM`, around line 91)
- Modify: `sellib/web/themes/folha.py:40-62` (`nav`, `_link`), `folha.py:64+` (`home`)
- Modify: `sellib/web/themes/caderno.py:105-123` (`nav`, `_tab`), `caderno.py:126+` (`home`)
- Modify: `sellib/web/themes/regua.py:108-123` (`nav`), `regua.py:126+` (`home`)
- Test: `tests/test_theme_nav.py` (create)

**Interfaces:**
- Consumes: `sellib.web.themes.items.TOOLS`, `MENU_ITEM`; `sellib.web.themes.nav_html(theme, active)`.
- Produces: `items.FILES_ITEM: tuple[str, str, str, str, str]` = `("arquivos", "/arquivos/", "Arquivos do Projeto", "Arquivos", "RDB e SCD do projeto")`. `folha._link` and `caderno._tab` accept `n: str | int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_theme_nav.py`:

```python
"""The Arquivos do Projeto tab must exist, and be reachable, in all three
directions -- and it must not disturb the tools' numbering."""

from __future__ import annotations

import pytest

from sellib.web import themes
from sellib.web.themes import items

ALL = list(themes.THEMES)


@pytest.mark.parametrize("theme", ALL)
def test_every_direction_links_the_files_tab(theme):
    html = themes.nav_html(theme, "")
    assert 'href="/arquivos/"' in html


@pytest.mark.parametrize("theme", ALL)
def test_the_files_tab_marks_itself_as_the_current_screen(theme):
    html = themes.nav_html(theme, "arquivos")
    assert 'aria-current="page"' in html
    # and exactly one screen claims it
    assert html.count('aria-current="page"') == 1


@pytest.mark.parametrize("theme", ALL)
def test_the_first_tool_is_still_number_one(theme):
    """régua's home cards say "Borne i"; the strip has to keep matching, and
    all three home() renderers index TOOLS from 1."""
    html = themes.nav_html(theme, "glv")
    assert ">1<" in html or ">01<" in html


@pytest.mark.parametrize("theme", ALL)
def test_the_files_tab_is_not_numbered_with_the_tools(theme):
    html = themes.nav_html(theme, "arquivos")
    assert ">A<" in html


def test_the_files_tab_is_not_in_the_tool_catalogue():
    """It is the input surface, not a commissioning tool: it must not inflate
    the tool count or claim an `entrada` column."""
    assert all(t.key != "arquivos" for t in items.TOOLS)
    assert items.FILES_ITEM[0] == "arquivos"
    assert items.FILES_ITEM[1] == "/arquivos/"


@pytest.mark.parametrize("theme", ALL)
def test_the_home_points_at_the_files_tab(theme):
    assert "/arquivos/" in themes.home_html(theme)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_theme_nav.py -q`
Expected: FAIL — `AttributeError: module 'sellib.web.themes.items' has no attribute 'FILES_ITEM'`, and the nav assertions fail.

- [ ] **Step 3: Add the catalogue entry**

In `sellib/web/themes/items.py`, right after the `MENU_ITEM` definition:

```python
# A aba dos arquivos do projeto. Como o "Menu", NAO e' uma ferramenta: e' a
# superficie de entrada, a unica tela que aceita um RDB ou um SCD. Fora de
# TOOLS de proposito -- ela nao tem "funcao" nem "entrada" pra declarar, e
# contar como ferramenta estragaria os numeros da home.
FILES_ITEM = ("arquivos", "/arquivos/", "Arquivos do Projeto",
              "Arquivos", "RDB e SCD do projeto")
```

- [ ] **Step 4: Fold it into folha**

In `sellib/web/themes/folha.py`, change the import line to include `FILES_ITEM`, then:

```python
def nav(active: str = "") -> str:
    """O sumario: Menu, os arquivos do projeto e as oito ferramentas.

    O rotulo e' texto solto dentro do `<a>`, sem `<span class=lbl>`: a folha nao
    tem subtitulo, e o mockup escreve assim.

    Os arquivos levam "A" e nao um numero: as ferramentas seguem numeradas de
    1 a 9 e a home as conta por essa mesma ordem.
    """
    out = ['<nav class="toc" aria-label="Ferramentas">']
    key, href, nome, _curto, _dica = MENU_ITEM
    out.append(_link(key == active, href, 0, nome))
    fkey, fhref, fnome, _fcurto, _fdica = FILES_ITEM
    out.append(_link(fkey == active, fhref, "A", fnome))
    for i, t in enumerate(TOOLS, start=1):
        out.append(_link(t.key == active, t.href, i, t.nome))
    out.append("</nav>")
    return "\n".join(out)


def _link(on: bool, href: "str | None", n: "int | str", label: str) -> str:
    inner = f'<span class="n">{n}</span>{label}'
    if href is None:
        return f'  <span class="off">{inner}</span>'
    if on:
        return f'  <a class="on" href="{href}" aria-current="page">{inner}</a>'
    return f'  <a href="{href}">{inner}</a>'
```

In folha's `home()`, add the pointer line immediately before the table is assembled, as the first thing in the main column:

```python
    lead = ('<p class="lead">Os RDB e SCD do projeto entram uma vez em '
            '<a href="/arquivos/">Arquivos do Projeto</a>; cada ferramenta '
            'escolhe dali.</p>')
```

and include `lead` at the start of the returned main-column HTML.

- [ ] **Step 5: Fold it into caderno**

In `sellib/web/themes/caderno.py`, add `FILES_ITEM` to the import, then:

```python
def nav(active: str = "") -> str:
    """As divisórias: rotulo curto e numero de dois digitos, como no mockup.

    Os arquivos do projeto levam "A" no lugar do numero: as ferramentas
    continuam 01..09, que e' a ordem que a home conta.
    """
    out = ['<nav class="tabs" aria-label="Ferramentas">']
    key, href, _nome, curto, _dica = MENU_ITEM
    out.append(_tab(key == active, href, "00", curto))
    fkey, fhref, _fnome, fcurto, _fdica = FILES_ITEM
    out.append(_tab(fkey == active, fhref, "A", fcurto))
    for i, t in enumerate(TOOLS, start=1):
        out.append(_tab(t.key == active, t.href, f"{i:02d}", t.curto))
    out.append("</nav>")
    return "\n".join(out)


def _tab(on: bool, href: "str | None", n: str, label: str) -> str:
    inner = f'<span class="n">{n}</span>{label}'
    if href is None:
        return f'  <span class="tab off">{inner}</span>'
    if on:
        return (f'  <a class="tab on" href="{href}" '
                f'aria-current="page">{inner}</a>')
    return f'  <a class="tab" href="{href}">{inner}</a>'
```

Note `_tab` now takes the number already formatted — the `{n:02d}` moved to the caller, so `"A"` and `"00"` go through the same slot. In caderno's `home()`, add the same pointer sentence as folha, before the cards.

- [ ] **Step 6: Fold it into régua**

Régua has **no Menu borne by design** — the home is reached through "← Menu" in the top bar. Without a special case the tab would be unreachable there, so it gets an `A` borne at the top of the strip, above borne 1, using the existing `.borne` markup. In `sellib/web/themes/regua.py`, add `FILES_ITEM` to the import and:

```python
def nav(active: str = "") -> str:
    """A régua: um borne por ferramenta, numerado de 1 a 9, sem o item Menu.

    O borne "A" e' a excecao: a regua nao tem borne de Menu (a volta pra home
    e' o "← Menu" da barra superior), entao sem ele os arquivos do projeto
    seriam inalcancaveis nesta direcao.
    """
    fkey, fhref, fnome, _fcurto, fdica = FILES_ITEM
    finner = (f'<span class="num">A</span>'
              f'<span class="lbl">{fnome}<small>{fdica}</small></span>')
    fcls = "borne on" if fkey == active else "borne"
    fcur = ' aria-current="page"' if fkey == active else ""
    out = ['<nav class="strip" aria-label="Ferramentas">',
           '  <div class="cap">Régua X1 &mdash; ferramentas</div>',
           f'  <a class="{fcls}" href="{fhref}"{fcur}>{finner}</a>']
    for i, t in enumerate(TOOLS, start=1):
        inner = (f'<span class="num">{i}</span>'
                 f'<span class="lbl">{t.nome}<small>{t.dica}</small></span>')
        if t.href is None:
            out.append(f'  <span class="borne off">{inner}</span>')
        elif t.key == active:
            out.append(f'  <a class="borne on" href="{t.href}" '
                       f'aria-current="page">{inner}</a>')
        else:
            out.append(f'  <a class="borne" href="{t.href}">{inner}</a>')
    out.append("</nav>")
    return "\n".join(out)
```

In régua's `home()`, add the same pointer sentence before the cards.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_theme_nav.py -q`
Expected: PASS, 16 tests.

- [ ] **Step 8: Verify all three in a browser**

Run: `python3 app.py --web`. On `http://localhost:8765/`, switch theme with the picker in the header and check, for each of folha / régua / caderno:
1. The Arquivos entry sits between Menu and the first tool (in régua, at the top of the strip).
2. Clicking it reaches `/arquivos/` and the entry is highlighted as the current screen.
3. **The page is full width in régua** — a collapsed ~200 px page means the nav marker escaped `.shell`.
4. The tools still read 1..9 and the home cards still match régua's borne numbers.

- [ ] **Step 9: Commit**

```bash
git add sellib/web/themes/items.py sellib/web/themes/folha.py \
        sellib/web/themes/regua.py sellib/web/themes/caderno.py \
        tests/test_theme_nav.py
git commit -m "Put the Arquivos do Projeto tab in all three directions"
```

---

### Task 4: `GET /library` and the `SelLibrary` runtime

Both are shared plumbing: served and injected for **every** mount, so the six tool tasks that follow are each a small, uniform edit.

**Files:**
- Create: `sellib/web/project_files/client.py`
- Modify: `sellib/web/session.py` (`SessionHandler._send`, around line 280)
- Test: `tests/test_project_files.py` (append)

**Interfaces:**
- Consumes: `library.library_for`, `FileLibrary.list`, `FileEntry.to_json`.
- Produces:
  - `client.LIBRARY_JS: str` — a `<script>` block defining `window.SelLibrary`.
  - `client.inject_library_runtime(html: str) -> str`.
  - Browser API: `SelLibrary.list(kind) -> Promise<Array>`; `SelLibrary.picker(el, {kind, multi, label, onPick})`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_project_files.py`:

```python
# -- the shared runtime -----------------------------------------------------

def test_the_runtime_goes_in_before_the_closing_body():
    from sellib.web.project_files import client

    html = "<html><body><p>oi</p></body></html>"
    out = client.inject_library_runtime(html)
    assert out.index("SelLibrary") < out.index("</body>")
    assert out.count("</body>") == 1


def test_the_runtime_survives_a_page_without_a_body_tag():
    from sellib.web.project_files import client

    out = client.inject_library_runtime("<p>oi</p>")
    assert "SelLibrary" in out


def test_the_runtime_refuses_to_define_itself_twice():
    """Every page gets it injected; a tool that also inlined it must not
    clobber a picker that is already mounted."""
    from sellib.web.project_files import client

    assert "if (window.SelLibrary) return;" in client.LIBRARY_JS


def test_the_picker_links_the_tab_relatively():
    """A cross-page link is one of the two things the fetch shim cannot
    reach, so it must be relative and not absolute."""
    from sellib.web.project_files import client

    assert "../arquivos/" in client.LIBRARY_JS
    assert 'href="/arquivos/"' not in client.LIBRARY_JS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_project_files.py -q -k "runtime or picker"`
Expected: FAIL — `ModuleNotFoundError: No module named 'sellib.web.project_files.client'`.

- [ ] **Step 3: Write the client runtime**

Create `sellib/web/project_files/client.py`:

```python
"""The `SelLibrary` browser runtime, injected into every page.

Six tools need the same picker over the same list. Written once here and
injected the way `SelProgress` already is, a seventh tool gets it for free --
and the empty state, which has to link the tab with a RELATIVE href (a
cross-page link is one of the two things the `fetch` shim cannot reach), is
written once too.
"""

from __future__ import annotations

LIBRARY_JS = r"""<script>
(function () {
  if (window.SelLibrary) return;

  function fmtSize(n) {
    if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
    if (n >= 1024) return (n / 1024).toFixed(0) + ' kB';
    return n + ' B';
  }

  function list(kind) {
    var url = '/library' + (kind ? '?kind=' + encodeURIComponent(kind) : '');
    return fetch(url).then(function (r) { return r.json(); })
                     .then(function (d) { return (d && d.files) || []; });
  }

  // opts: {kind, multi, label, onPick}
  // onPick receives one entry, or an array of entries when multi is true.
  function picker(el, opts) {
    opts = opts || {};
    var node = (typeof el === 'string') ? document.getElementById(el) : el;
    if (!node) return {refresh: function () {}};

    function render(files) {
      node.innerHTML = '';
      var bar = document.createElement('div');
      bar.className = 'filebar';

      var cap = document.createElement('span');
      cap.className = 'lbl';
      cap.textContent = opts.label || 'Arquivo do projeto';
      bar.appendChild(cap);

      if (!files.length) {
        var msg = document.createElement('span');
        msg.textContent = 'Nenhum ' + (opts.kind || 'arquivo').toUpperCase() +
                          ' no projeto — envie em ';
        bar.appendChild(msg);
        var a = document.createElement('a');
        a.className = 'lnk';
        // Relative on purpose: the fetch shim rewrites fetch/XHR, never an
        // <a href>, so an absolute path would break under a mount prefix.
        a.href = '../arquivos/';
        a.textContent = 'Arquivos do Projeto';
        bar.appendChild(a);
        node.appendChild(bar);
        return;
      }

      var sel = document.createElement('select');
      if (opts.multi) { sel.multiple = true; sel.size = Math.min(files.length, 6); }
      files.forEach(function (f) {
        var o = document.createElement('option');
        o.value = f.sha256;
        o.textContent = f.name + '  ·  ' + fmtSize(f.size) +
                        (f.detail ? '  ·  ' + f.detail : '');
        o.title = f.short_sha;
        sel.appendChild(o);
      });
      bar.appendChild(sel);

      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn pri';
      btn.textContent = opts.multi ? 'Adicionar' : 'Usar';
      btn.addEventListener('click', function () {
        var by = {};
        files.forEach(function (f) { by[f.sha256] = f; });
        if (opts.multi) {
          var chosen = Array.prototype.filter.call(sel.options, function (o) {
            return o.selected;
          }).map(function (o) { return by[o.value]; });
          if (chosen.length && opts.onPick) opts.onPick(chosen);
        } else if (sel.value && opts.onPick) {
          opts.onPick(by[sel.value]);
        }
      });
      bar.appendChild(btn);

      var more = document.createElement('a');
      more.className = 'lnk';
      more.href = '../arquivos/';
      more.textContent = 'Arquivos do Projeto';
      bar.appendChild(document.createElement('span')).className = 'spacer';
      bar.appendChild(more);

      node.appendChild(bar);
    }

    function refresh() { return list(opts.kind).then(render); }
    refresh();
    return {refresh: refresh};
  }

  window.SelLibrary = {list: list, picker: picker, fmtSize: fmtSize};
})();
</script>
"""


def inject_library_runtime(html: str) -> str:
    """Insert the runtime just before `</body>` (or at the end)."""
    idx = html.rfind("</body>")
    if idx == -1:
        return html + LIBRARY_JS
    return html[:idx] + LIBRARY_JS + html[idx:]
```

- [ ] **Step 4: Inject it beside the progress runtime**

In `sellib/web/session.py`, add the import and one line in `SessionHandler._send`:

```python
from sellib.web.project_files.client import inject_library_runtime
```

```python
    def _send(self, code: int, body, ctype: str):
        if isinstance(body, str):
            if ctype.startswith("text/html"):
                body = inject_head(body, self.mount_prefix, self.theme)
                body = inject_progress_runtime(body)
                body = inject_library_runtime(body)
            body = body.encode("utf-8")
```

`client.py` imports nothing from `sellib.web`, so this introduces no import cycle.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Verify the runtime in a browser**

Run: `python3 app.py --web`. On any tool page, open the browser console:
1. `typeof SelLibrary` is `"object"` — the runtime is injected everywhere.
2. `await SelLibrary.list()` resolves to the project's files.
3. `await SelLibrary.list('scd')` returns only the SCDs.

- [ ] **Step 7: Commit**

```bash
git add sellib/web/project_files/client.py \
        sellib/web/session.py tests/test_project_files.py
git commit -m "Inject the SelLibrary picker runtime into every page"
```

---

### Task 5: VLAN Mapper picks its SCD

The simplest of the six — one file, one kind — so it establishes the pattern the next five repeat.

**Files:**
- Modify: `sellib/web/vlan_mapper.py` — CSS (~225-246), markup (~359-366), JS (~428-467), the route (~837-893), the cap substitution (~795)
- Test: none new (web layer, per the project's convention); verified in a browser

**Interfaces:**
- Consumes: `library.library_for`, `FileEntry.scd_path`, `FileEntry.display_name`, `KIND_SCD`; `SelLibrary.picker`.
- Produces: `POST /select-scd {"sha256": …}` → the same payload `/scd-upload` returned (`_build_payload`'s dict).

- [ ] **Step 1: Delete the upload zone's CSS**

Remove the `.upload-row` and `.upload-zone` rules (the block starting `.upload-row { margin-bottom: var(--s4); }` through `.upload-zone input[type=file] { display: none; }`). The picker uses `.filebar`, which already lives in `themes/shell.py`.

- [ ] **Step 2: Replace the markup**

Replace the whole `<div class="upload-row">…</div>` block (the `<label class="upload-zone" id="drop-scd">` and its children) with:

```html
  <div id="pick-scd"></div>
```

- [ ] **Step 3: Replace the client JS**

Delete `setupDrop(...)`, `upload(...)` and the `setupDrop('drop-scd', …, __SCD_CAP__);` call. In their place:

```js
// O SCD entra uma vez em Arquivos do Projeto; aqui so se escolhe qual.
SelLibrary.picker('pick-scd', {
  kind: 'scd', label: 'SCD do projeto',
  onPick: (f) => selectScd(f),
});

async function selectScd(f) {
  setStatus('Lendo ' + f.name + '...', '');
  const r = await SelProgress.post('/select-scd', {sha256: f.sha256},
                                   {label: 'Lendo ' + f.name});
  if (!r.ok) {
    setStatus('Falha: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  setStatus(f.name + ' carregado.', 'ok');
  render(r.data);
}
```

- [ ] **Step 4: Remove the now-dead cap substitution**

Delete `LANDING_HTML = LANDING_HTML.replace("__SCD_CAP__", str(_SCD_MAX_BYTES))` (~line 795). `_SCD_MAX_BYTES` itself goes with the route in Step 5 — the ceiling now lives once, in `library.py`.

- [ ] **Step 5: Replace the route**

Add the import at the top of the module:

```python
from sellib.web.project_files import library as filelib
```

Then replace the entire `if path == "/scd-upload":` branch in `do_POST` with:

```python
            if path == "/select-scd":
                # O corpo do antigo /scd-upload, do `load_scd` pra frente: o
                # arquivo ja foi recebido e validado em /arquivos/.
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    body = {}
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
```

Then delete `_SCD_MAX_BYTES` and any import left unused (`unquote`, `rdb_loader`, `scd_loader`) — check with `grep -n` before removing each.

- [ ] **Step 6: Verify in a browser**

Run: `python3 app.py --web`
1. With an empty project, `/vlan-mapper/` shows "Nenhum SCD no projeto — envie em Arquivos do Projeto", and the link reaches `/arquivos/`.
2. Upload an SCD in `/arquivos/`, return to `/vlan-mapper/`, pick it, press Usar — the VLAN table renders as before.
3. There is no drop zone anywhere on the page.
4. Check the same in all three themes.

- [ ] **Step 7: Commit**

```bash
git add sellib/web/vlan_mapper.py
git commit -m "VLAN Mapper picks its SCD from the project instead of uploading one"
```

---

### Task 6: GLE Exporter picks its RDB

The XLSX round-trip (`/import`) is **untouched** — it takes back a spreadsheet this tool just produced, which is not a project input.

**Files:**
- Modify: `sellib/web/gle_exporter.py` — CSS (~687-709), markup (~807-813), JS (~874-913), `init()` (~1093-1103), the route (~1187-1227), the cap substitution (~1109)

**Interfaces:**
- Consumes: `library.library_for`, `FileEntry.rdb`, `KIND_RDB`; `SelLibrary.picker`.
- Produces: `POST /select-rdb {"sha256": …}` → `_state_payload(st)`.

- [ ] **Step 1: Delete the upload zone's CSS**

Remove the `.upload-zone` rules (from `.upload-zone {` through `.upload-zone input[type=file] { display: none; }`).

- [ ] **Step 2: Replace the markup**

Replace the `<label class="upload-zone" id="drop-rdb">…</label>` block with:

```html
  <div id="pick-rdb"></div>
```

- [ ] **Step 3: Replace the client JS**

Delete `setupDrop(...)`, `upload(...)` and the `setupDrop('drop-rdb', …, __RDB_CAP__);` call, and put in their place:

```js
SelLibrary.picker('pick-rdb', {
  kind: 'rdb', label: 'RDB do projeto',
  onPick: (f) => selectRdb(f),
});

async function selectRdb(f) {
  setStatus('Carregando ' + f.name + '...', '');
  const r = await SelProgress.post('/select-rdb', {sha256: f.sha256},
                                   {label: 'Carregando ' + f.name});
  if (!r.ok) {
    setStatus('Falha: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  setStatus(f.name + ' carregado.', 'ok');
  renderRelays(r.data);
}
```

In the `init()` IIFE at the bottom, drop the two lines that referenced the deleted zone:

```js
    if (data.has_rdb) {
      renderRelays(data);
    }
```

- [ ] **Step 4: Remove the now-dead cap substitution**

Delete `LANDING_HTML = LANDING_HTML.replace("__RDB_CAP__", str(_RDB_MAX_BYTES))`.

- [ ] **Step 5: Replace the route**

Add `from sellib.web.project_files import library as filelib` at the top, then replace the whole `if path == "/rdb-upload":` branch with:

```python
            if path == "/select-rdb":
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    body = {}
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
```

Then delete `_RDB_MAX_BYTES` if nothing else uses it (`grep -n "_RDB_MAX_BYTES" sellib/web/gle_exporter.py`) and prune imports the same way.

- [ ] **Step 6: Verify in a browser**

Run: `python3 app.py --web`
1. `/gle-exporter/` with an empty project shows the empty state and its link.
2. Pick an RDB from the project — the relay list renders as before.
3. Export an XLSX, edit a comment, and re-import it through the tool's own import control — **the round-trip still works and did not move**.
4. Reload the page: the picked RDB is still in effect (`/state` still reports it).

- [ ] **Step 7: Commit**

```bash
git add sellib/web/gle_exporter.py
git commit -m "GLE Exporter picks its RDB from the project"
```

---

### Task 7: VB Updater picks its RDB and SCD

Two kinds, two pickers. `/import-descriptions` (the XLSX round-trip) is **untouched**; note it borrows `_SCD_MAX_BYTES` for its own ceiling, so that constant stays in this module.

**Files:**
- Modify: `sellib/web/vb_updater.py` — CSS (~1005-1033), markup (~1159-1176), JS (~1209-1250), the routes (~2267-2350), the cap substitutions (~1627-1628)

**Interfaces:**
- Consumes: `library.library_for`, `FileEntry.rdb`, `FileEntry.scd_path`, both kinds; `SelLibrary.picker`.
- Produces: `POST /select-rdb {"sha256": …}` and `POST /select-scd {"sha256": …}`, both → `_state_payload(st)` after `_maybe_match(st)`.

- [ ] **Step 1: Delete the upload zone's CSS**

Remove the `.upload-row` and `.upload-zone` rules.

- [ ] **Step 2: Replace the markup**

Replace the whole `<div class="upload-row">` block (both labels) with:

```html
  <div id="pick-rdb"></div>
  <div id="pick-scd"></div>
```

- [ ] **Step 3: Replace the client JS**

Delete `setupDrop(...)`, `upload(...)` and both `setupDrop(...)` calls, and put in their place:

```js
// Os arquivos entram uma vez em Arquivos do Projeto; aqui se escolhe o par.
SelLibrary.picker('pick-rdb', {
  kind: 'rdb', label: 'RDB do projeto',
  onPick: (f) => select('/select-rdb', f, 'RDB'),
});
SelLibrary.picker('pick-scd', {
  kind: 'scd', label: 'SCD do projeto',
  onPick: (f) => select('/select-scd', f, 'SCD'),
});

async function select(endpoint, f, kind) {
  setStatus('Carregando ' + kind + '...', '');
  const r = await SelProgress.post(endpoint, {sha256: f.sha256},
                                   {label: 'Carregando ' + f.name});
  if (!r.ok) {
    setStatus('Falha: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  setStatus(kind + ' carregado.', 'ok');
  refreshState();
}
```

- [ ] **Step 4: Remove the now-dead cap substitutions**

Delete the two `.replace("{sizeCap}_RDB", …)` / `.replace("{sizeCap}_SCD", …)` lines (~1627-1628).

- [ ] **Step 5: Replace both routes**

Add `from sellib.web.project_files import library as filelib` at the top, then replace the `if path == "/rdb-upload":` and `if path == "/scd-upload":` branches with:

```python
            if path in ("/select-rdb", "/select-scd"):
                want = (filelib.KIND_RDB if path == "/select-rdb"
                        else filelib.KIND_SCD)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}") \
                        if length > 0 else {}
                except ValueError:
                    body = {}
                sha = (body.get("sha256") or "").strip()
                lib = filelib.library_for(sessions, self.session)
                with self.session.lock:
                    entry = lib.get(sha)
                if entry is None or entry.kind != want:
                    self._send_json(404, {
                        "error": "Arquivo não está mais no projeto."})
                    return
                job = self.job()
                with self.session.lock:
                    st = self.sess()
                    if want == filelib.KIND_RDB:
                        st.rdb = entry.rdb
                        logger.info("[vb-updater] RDB '%s' (%s) escolhido; "
                                    "%d relé(s) com GLE",
                                    entry.display_name, entry.short_sha,
                                    len(entry.rdb.relays))
                    else:
                        st.scd_path = entry.scd_path
                        st.scd_name = entry.display_name
                        logger.info("[vb-updater] SCD '%s' (%s) escolhido",
                                    entry.display_name, entry.short_sha)
                    # O cruzamento RDB x SCD so acontece quando os dois
                    # existem; `_maybe_match` ja sabe disso.
                    job.stage("Cruzando RDB com SCD", 60)
                    _maybe_match(st)
                job.finish("Arquivo carregado")
                self._send_json(200, _state_payload(self.sess()))
                return
```

Then delete `_RDB_MAX_BYTES` (now unused). **Keep `_SCD_MAX_BYTES`** — `/import-descriptions` uses it as its own ceiling.

- [ ] **Step 6: Verify in a browser**

Run: `python3 app.py --web`
1. `/vb-updater/` with an empty project shows two empty states.
2. Pick only an RDB — the page reports the RDB and no matches yet.
3. Pick an SCD too — the matched relays render exactly as they did after two uploads.
4. Export descriptions to XLSX and re-import through the tool's own control — **untouched and still working**.
5. Pick a *different* RDB: the match re-runs against the same SCD.

- [ ] **Step 7: Commit**

```bash
git add sellib/web/vb_updater.py
git commit -m "VB Updater picks its RDB and SCD from the project"
```

---

### Task 8: Settings Compare picks several RDBs

The only multi-select picker: the tool compares up to 7 relays and may pull them from different RDBs, which is why a single "active RDB" was never an option.

**Files:**
- Modify: `sellib/web/settings_compare.py` — CSS (~654-665), markup (~914-919), JS (~1585-1610), the route (~1679-1718), the cap substitution (~1624)

**Interfaces:**
- Consumes: `library.library_for`, `FileEntry.rdb`, `KIND_RDB`; `SelLibrary.picker` with `multi: true`.
- Produces: `POST /select-rdb {"sha256s": [...]}` → `{"ok": true, "keys": [short_sha, …]}`.

- [ ] **Step 1: Delete the upload zone's CSS**

Remove the `.upload-zone` rules.

- [ ] **Step 2: Replace the markup**

Inside `.rdb-grid`, replace the `<label class="upload-zone" id="upload-zone">…</label>` with:

```html
      <div id="pick-rdb"></div>
```

- [ ] **Step 3: Replace the client JS**

Delete the `// ----- upload ---` block: the `dz`/`input` wiring and `uploadRdb(...)`. In its place:

```js
  // ----- RDBs do projeto ---------------------------------------------------
  // Multi de proposito: a comparacao aceita ate 7 reles, e eles podem vir de
  // RDBs diferentes -- e' o caso que impede um "RDB ativo" unico.
  SelLibrary.picker('pick-rdb', {
    kind: 'rdb', multi: true, label: 'RDBs do projeto',
    onPick: (files) => addRdbs(files),
  });

  async function addRdbs(files) {
    setStatus(`Carregando ${files.length} RDB(s)...`);
    const r = await SelProgress.post(
      '/select-rdb', {sha256s: files.map((f) => f.sha256)},
      {label: 'Carregando RDBs'});
    if (!r.ok) {
      setStatus(`Falha: ${(r.data && r.data.error) || r.status}`, 'err');
      return;
    }
    setStatus(`${files.length} RDB(s) carregado(s).`, 'ok');
    await loadState();
  }
```

Also update the empty-state string in the RDB list (~line 1057) from "Use o uploader ao lado." to "Escolha ao lado, entre os arquivos do projeto."

- [ ] **Step 4: Remove the now-dead cap substitution**

Delete `INDEX_HTML = INDEX_HTML.replace("__RDB_CAP_MB__", …)`.

- [ ] **Step 5: Replace the route**

Add `from sellib.web.project_files import library as filelib` at the top, then replace the whole `if path == "/rdb-upload":` branch with:

```python
            if path == "/select-rdb":
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"error": "JSON inválido"})
                    return
                shas = body.get("sha256s") or []
                if isinstance(shas, str):
                    shas = [shas]
                lib = filelib.library_for(sessions, self.session)
                keys, faltando = [], []
                for sha in shas:
                    with self.session.lock:
                        entry = lib.get((sha or "").strip())
                    if entry is None or entry.kind != filelib.KIND_RDB:
                        faltando.append(sha)
                        continue
                    key = _register_rdb(self.sess(), self.session.lock,
                                        entry.rdb)
                    keys.append(key)
                    logger.info("[settings-compare] RDB '%s' (%s) escolhido; "
                                "%d relé(s)",
                                entry.display_name, key, len(entry.rdb.relays))
                if faltando and not keys:
                    self._send_json(404, {
                        "error": "Arquivo não está mais no projeto."})
                    return
                self._send_json(200, {"ok": True, "keys": keys,
                                      "faltando": faltando})
                return
```

Then delete `_RDB_MAX_BYTES` if unused and prune imports.

- [ ] **Step 6: Verify in a browser**

Run: `python3 app.py --web`
1. With two different RDBs in the project, select both in the picker and press Adicionar — both appear in the RDB list.
2. Pick relays from **both** RDBs (same family) and run a comparison — the diff renders as before.
3. The 2..7 relay bounds and the same-family check still behave.
4. Reload: `/state` still reports both RDBs.

- [ ] **Step 7: Commit**

```bash
git add sellib/web/settings_compare.py
git commit -m "Settings Compare picks its RDBs from the project"
```

---

### Task 9: DNP Map Editor picks its RDB

Its landing has its own markup (a `<section class="card">` step list) rather than a shared drop zone, and it keeps a per-tab memory of the last RDB used. **`/import-profile` is untouched** — a DNP device profile is project-wide reference data written into `data/wordbits/`, not session input.

**Files:**
- Modify: `sellib/web/dnp_map/templates/landing.html` — the "1. Envie o RDB" section (~135-145), the `#enviar` handler (~223-237)
- Modify: `sellib/web/dnp_map/handler.py` — `_do_upload` (~299-330), the POST table (~286)

**Interfaces:**
- Consumes: `library.library_for`, `FileEntry.rdb`, `KIND_RDB`; `SelLibrary.picker`.
- Produces: `POST /select-rdb {"sha256": …}` → `{"ok": true, "rdb": key, "name": …, "relays": [...]}` — the same shape `/upload` returned, so `mostrarReles()` needs no change.

- [ ] **Step 1: Replace the markup**

Replace the section's `<div class="linha">` (the `<input type="file" id="rdb">` and its `<button id="enviar">`) and the hint paragraph beneath it with:

```html
    <div id="pick-rdb"></div>
```

and retitle the section:

```html
    <h2>1. Escolha o RDB</h2>
```

Leave `#rdbs-anteriores` alone: it is this tool's own memory of which RDB the tab had open, and still useful.

- [ ] **Step 2: Replace the client JS**

Replace the `document.getElementById('enviar').onclick = async () => {…}` handler with:

```js
SelLibrary.picker('pick-rdb', {
  kind: 'rdb', label: 'RDB do projeto',
  onPick: async (f) => {
    const r = await SelProgress.post('/select-rdb', {sha256: f.sha256},
                                     {label: 'Carregando ' + f.name});
    if (!r.ok) { alert((r.data && r.data.error) || 'Falha ao carregar'); return; }
    RDB = r.data.rdb;
    guardarEscolha(RDB);
    mostrarReles(r.data.relays);
    await listarRdbs();
    await atualizarPendentes();
  },
});
```

- [ ] **Step 3: Replace the route**

In `handler.py`, add `from sellib.web.project_files import library as filelib` to the imports, change the POST table entry from `"/upload"` to `"/select-rdb"` (calling `self._do_select_rdb()`), and replace `_do_upload` with:

```python
        def _do_select_rdb(self):
            """The tail of the old /upload: the RDB was received, validated and
            extracted in /arquivos/, so all that is left is to adopt it."""
            sha = (self._body().get("sha256") or "").strip()
            lib = filelib.library_for(sessions, self.session)
            with self.session.lock:
                entry = lib.get(sha)
            if entry is None or entry.kind != filelib.KIND_RDB:
                self._send_json(404, {
                    "ok": False, "error": "Arquivo não está mais no projeto."})
                return
            info = entry.rdb
            key = _short_sha(info.sha256)
            st = self.sess()
            with self.session.lock:
                st.rdbs[key] = info
            self._send_json(200, {
                "ok": True, "rdb": key, "name": info.display_name,
                "relays": self._relay_payload(key, info),
            })
```

Then delete `_RDB_MAX_BYTES` (`/import-profile` has its own `_PROFILE_MAX_BYTES`) and prune imports left unused.

- [ ] **Step 4: Verify in a browser**

Run: `python3 app.py --web`
1. `/dnp-map/` with an empty project shows the empty state and its link.
2. Pick an RDB — the relay table renders, including relays with settings but no GLE (a SEL-2440), since `set_dnp.discover()` walks `Relays/` directly.
3. Open a relay's editor, make an edit, go back with "← Relés" — the page is still populated (the `#rdbs-anteriores` memory still works).
4. Export the edited RDB and download it.
5. **"Importar perfil DNP" still works and still writes into `data/wordbits/`.**

- [ ] **Step 5: Commit**

```bash
git add sellib/web/dnp_map/handler.py sellib/web/dnp_map/templates/landing.html
git commit -m "DNP Map Editor picks its RDB from the project"
```

---

### Task 10: GLV picks its RDB

Only `/glv/novo` (the GLE selector) changes. The diagram page, `LinkPool`, the polling threads and everything about connecting are untouched.

**Files:**
- Modify: `sellib/web/glv/templates/landing.html` — CSS (~9-28), markup (~196-202), `uploadFile` (~352-368), the drop wiring (~411-419), the "Outro arquivo" button (~421+)
- Modify: `sellib/web/glv/handler.py` — the route table (~284) and `_rdb_upload` (~336-375)

**Interfaces:**
- Consumes: `library.library_for`, `FileEntry.rdb`, `KIND_RDB`; `SelLibrary.picker`.
- Produces: `POST /select-rdb {"sha256": …}` → `self._landing_state()` — the same payload `/rdb-upload` returned, so `renderRdbInfo()` needs no change.

- [ ] **Step 1: Delete the upload zone's CSS**

Remove the `.upload-zone` rules from the template's `<style>`.

- [ ] **Step 2: Replace the markup**

Replace the `<label class="upload-zone" id="drop">…</label>` with:

```html
  <div id="pick-rdb"></div>
```

- [ ] **Step 3: Replace the client JS**

Delete `uploadFile(...)`, the three `drop.addEventListener(...)` lines and the `fileInput.addEventListener('change', …)` line. In their place:

```js
SelLibrary.picker('pick-rdb', {
  kind: 'rdb', label: 'RDB do projeto',
  onPick: (f) => selectRdb(f),
});

async function selectRdb(f) {
  setStatus('Carregando ' + f.name + '...', '');
  const r = await SelProgress.post('/select-rdb', {sha256: f.sha256},
                                   {label: 'Carregando ' + f.name});
  if (!r.ok) {
    setStatus('Erro: ' + ((r.data && r.data.error) || r.status), 'err');
    return;
  }
  setStatus('Pronto. Escolha um GLE para plotar.', 'ok');
  renderRdbInfo(r.data);
}
```

In the `#reload-btn` ("Outro arquivo") handler, drop the `fileInput.value = '';` line — there is no input any more — and keep the rest, which clears the info bar and the relay list.

Also drop the now-dangling `const fileInput = …;` and `const drop = …;` declarations (~244-245).

- [ ] **Step 4: Replace the route**

In `glv/handler.py`, add `from sellib.web.project_files import library as filelib`, change the route from `"/rdb-upload"` to `"/select-rdb"` (calling `self._select_rdb()`), and replace `_rdb_upload` with:

```python
        def _select_rdb(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            sha = (body.get("sha256") or "").strip()
            lib = filelib.library_for(sessions, self.session)
            with self.session.lock:
                entry = lib.get(sha)
            if entry is None or entry.kind != filelib.KIND_RDB:
                self._send_json(404, {
                    "error": "Arquivo não está mais no projeto."})
                return
            info = entry.rdb
            self.sess().rdb = info
            logger.info("[glv] RDB '%s' (%s) escolhido; %d relé(s) com GLE",
                        info.display_name, info.sha256[:16], len(info.relays))
            self._send_json(200, self._landing_state())
```

Then delete `_RDB_MAX_BYTES` and prune the now-unused `process_upload` import (keep `find_gle` and `relays_to_dict`).

- [ ] **Step 5: Verify in a browser**

Run: `python3 app.py --web`
1. `/glv/novo` with an empty project shows the empty state and its link.
2. Pick an RDB — the relay/GLE list renders, and `RdbInfo.relays` still lists only relays that own a `.gle`.
3. Open a diagram with an IP — it opens **disconnected**, all bits indeterminate, as before.
4. Open a second diagram from the same RDB — the tab strip carries both.
5. "Outro arquivo" clears the info bar and lets a different RDB be picked.

Connecting to a live relay cannot be verified without hardware; nothing in this task touches that path.

- [ ] **Step 6: Commit**

```bash
git add sellib/web/glv/handler.py sellib/web/glv/templates/landing.html
git commit -m "GLV picks its RDB from the project"
```

---

### Task 11: Documentation and the end-to-end sweep

**Files:**
- Modify: `docs/ENGINEERING-NOTES.md` — the "Project layout" and "Gotchas" sections
- Verify: the whole toolkit

- [ ] **Step 1: Confirm no upload surface survives outside the tab**

Run:

```bash
grep -rn "upload-zone\|rdb-upload\|scd-upload" sellib/ --include=*.py --include=*.html
```

Expected: no hits. (`/arquivos/` uses `.drop` and `POST /upload`; the XLSX and DNP-profile imports use their own route names.) Investigate every hit before continuing.

- [ ] **Step 2: Confirm nothing kept a private ceiling**

Run:

```bash
grep -rn "500 \* 1024 \* 1024\|200 \* 1024 \* 1024" sellib/
```

Expected: only `sellib/web/project_files/library.py`, plus `vb_updater._SCD_MAX_BYTES` **if** you kept it for `/import-descriptions` (that one is intentional; leave a comment saying so).

- [ ] **Step 3: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — the pre-existing 116 tests plus the new ones from Tasks 1, 2, 3 and 4.

- [ ] **Step 4: Update docs/ENGINEERING-NOTES.md**

While here, correct the "Verification" section's stale test count: it says 76,
and the suite ran 116 before this work started. Re-run
`.venv/bin/python -m pytest tests/ -q` and write down what it actually reports.

Add to the "Project layout" list, right after the `sellib/web/dnp_map/` entry:

```markdown
  - `sellib/web/project_files/` — the **Arquivos do Projeto** tab (`/arquivos/`): the only screen that accepts an RDB or an SCD. `library.py` is the model (a session-scoped `FileLibrary` keyed by sha256; `library_for(sessions, session)`, never a module singleton), `handler.py` the routes, `client.py` the `SelLibrary` picker injected into every page. The six tools pick from it and no longer upload.
```

Replace the "Web dashboard binds to `0.0.0.0:8765`" gotcha's second sentence ("The landing page accepts RDB uploads to `rdbs/`.") and add these gotchas:

```markdown
- **Uploads happen in exactly one place.** `/arquivos/` is the only screen with a drop zone; every other tool shows a `SelLibrary.picker` and POSTs `/select-rdb` or `/select-scd` with a sha256. A tool's select handler is the tail of its old upload handler — everything after `process_upload`/`load_scd` returned — so adding a tool means writing that tail, never another upload panel. The 500 MB / 200 MB ceilings live once, in `project_files/library.py`.
- **The library is keyed by content, and it dedups twice on purpose.** `rdb.process_upload()` dedups the *extraction* across visitors and restarts (`cache/rdb/<sha256>/`); `FileLibrary` dedups the *entry* within one visitor's project. A second upload of the same bytes returns the existing entry and keeps the FIRST name — renaming an entry under a tool that is already showing it is worse than ignoring the second name. RDB bytes never reach the session directory; only SCDs do, at `cache/sessions/<sid>/files/<sha12>.scd`.
- **The library's directory is `Session.subdir("files")`, never `SessionHandler.sdir("files")`.** `sdir()` prefixes the caller's `session_key`, so using it would hand each tool a different directory and silently un-share the library. Derived outputs still go to `self.sdir("out")`, and `/download` stays sandboxed there.
- **`/library` is served by the dispatcher**, like `/progress` and `/theme.css` — with and without a mount prefix, filtered by `?kind=`. `SelLibrary` is injected into every page beside `SelProgress`, so a new tool gets the picker and its empty state for free. The empty state links `../arquivos/` **relatively**: a cross-page `<a href>` is one of the two things the `fetch` shim cannot reach.
- **Arquivos do Projeto is a nav entry, not a tool.** It is `items.FILES_ITEM` beside `MENU_ITEM`, never in `TOOLS` — it has no `funcao`/`entrada` to declare and would inflate the home's counts. Tool numbering stays 1..9 (régua's home cards read "Borne *i*" and must match its strip), so Menu keeps `0` and Arquivos takes `A`. Régua has no Menu borne by design, so it gets an `A` borne at the top of `.strip` — without it the tab is unreachable in that direction.
- **What did NOT move into the library:** the XLSX round-trips (`gle_exporter:/import`, `vb_updater:/import-descriptions`) take back a spreadsheet the tool itself produced, and `dnp_map:/import-profile` writes project-wide reference data into `data/wordbits/`. None of the three is a project input; all three keep their own upload control.
```

- [ ] **Step 5: The end-to-end sweep**

Run: `python3 app.py --web`. In **each of the three themes**:

1. `/arquivos/` — upload one RDB and one SCD; re-upload each and confirm the duplicate message and that no second row appears.
2. Every screen is full width (a ~200 px page in régua means a nav marker escaped `.shell`).
3. GLV `/glv/novo` → open a diagram; Comparador → two RDBs, a comparison; VB Updater → RDB + SCD, matches; VLAN Mapper → the VLAN table; Exportador GLE → XLSX export **and** re-import; Mapa DNP → an edit, an export, **and** "Importar perfil DNP".
4. Remove both files from `/arquivos/`; every tool that had already selected one keeps working with it, and the pickers fall back to their empty state.
5. In a private window (a fresh session), `/arquivos/` is empty — the library is per visitor.

- [ ] **Step 6: Commit**

```bash
git add docs/ENGINEERING-NOTES.md
git commit -m "Document the Arquivos do Projeto tab and the picker convention"
```
