# DNP Map Editor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Uma sexta ferramenta no toolkit, em `/dnp-map/`, que abre um RDB, edita os mapas DNP3 (`SET_D*.TXT`) dos relés numa tabela e reexporta o RDB com as alterações.

**Architecture:** Quatro módulos puros (parser de `SET_D`, writer de OLE, registry de word bits, modelo de edição) sob um pacote web `sellib/web/dnp_map/` que só faz rotas e HTML. O export é híbrido: `olefile.write_stream` quando o novo stream tem exatamente o tamanho do original, reconstrução completa do arquivo OLE quando não tem.

**Tech Stack:** Python 3.10+, `olefile`, `http.server` (`ThreadingHTTPServer` via `sellib/web/mount.py`), HTML/JS sem build step nem CDN. `pytest` novo, só para desenvolvimento.

**Spec:** `docs/superpowers/specs/2026-08-22-dnp-map-editor-design.md` — leia antes de começar. O plano argumenta a partir dele.

## Global Constraints

- Strings visíveis ao usuário em **português acentuado**. Identificadores, comentários e docstrings em **inglês** para arquivos novos.
- `from __future__ import annotations` no topo de todo módulo novo.
- Caminhos **sempre** via constantes de `sellib/paths.py`. Nunca `Path(__file__).parent` fora de `paths.py`.
- Imports absolutos: `from sellib.x import y`. Nunca relativos.
- A ferramenta **não** define cor, raio, família de fonte ou padding próprios — só tokens de `sellib/web/themes/tokens.py` (`--bg`, `--surface`, `--border`, `--text`, `--ok`, `--s1..--s5`, `--sans`, `--radius`).
- Rotas escritas como se a ferramenta fosse dona da raiz (`/map`, `/export`). `self.mount_prefix` só à mão no `<a href download>`.
- Upload de RDB via `SelProgress.upload()`, nunca `fetch()`.
- Trabalho longo no servidor reporta estágios via `self.job()`.
- Nada de singleton de módulo para estado: estado por sessão via `self.sess()`, arquivos por sessão via `self.sdir()`.
- `selprotopy/` é vendorizado e read-only. Um hook bloqueia edições lá.
- Verificação com relé vivo é impossível aqui; nada nesta ferramenta precisa de relé.
- Nunca linkar CDN. Todo asset é local.

## Ambiente de teste

O projeto não tinha framework de testes. Este plano adiciona `pytest` **só para desenvolvimento**: `app.py` continua bootstrapando apenas `requirements.txt`, e nada em produção importa `pytest`.

```bash
.venv/bin/python -m pip install -r requirements-dev.txt   # uma vez
.venv/bin/python -m pytest tests/ -v                       # rodar
```

Essa é a única exceção sancionada à regra "não rode pip à mão" do docs/ENGINEERING-NOTES.md, e a Task 10 registra a exceção lá.

## Estrutura de arquivos

| Arquivo | Responsabilidade | Task |
|---|---|---|
| `requirements-dev.txt` | `pytest` | 1 |
| `tests/test_set_dnp.py` | Round-trip e modelo de pontos do parser | 1, 2 |
| `sellib/parsers/set_dnp.py` | Parse/serialize de um `SET_D*.TXT` e descoberta dos mapas num RDB extraído | 1, 2, 3 |
| `tests/test_set_dnp_discovery.py` | Descoberta de relés/sessões | 3 |
| `sellib/core/wordbits.py` | Registry sobre `data/wordbits/*.json` | 4 |
| `data/wordbits/SEL-411L.json`, `SEL-751.json` | Listas de word bits | 4 |
| `tools/wordbits_from_glv_cache.py` | Semeia uma lista a partir de `cache/<FID>.json` | 4 |
| `tests/test_wordbits.py` | Lookup, aliases, check, ausência de arquivo | 4 |
| `sellib/parsers/ole_rebuild.py` | Writer CFBF v3 + `rebuild()` com autoverificação | 5 |
| `tests/test_ole_rebuild.py` | Escrita/leitura de um OLE sintético, mini streams, storages | 5 |
| `sellib/web/dnp_map/__init__.py` | `load_template()` | 6 |
| `sellib/web/dnp_map/model.py` | Estado de edição por sessão | 6 |
| `tests/test_dnp_map_model.py` | Diffs, swap, copiar sessão | 6 |
| `sellib/web/dnp_map/export.py` | Export híbrido | 7 |
| `tests/test_dnp_map_export.py` | Escolha do caminho in-place vs rebuild | 7 |
| `sellib/web/dnp_map/handler.py` | Rotas | 8, 9 |
| `sellib/web/dnp_map/templates/landing.html` | Upload + escolha de relé | 8 |
| `sellib/web/dnp_map/templates/editor.html` | A tabela | 9 |
| `sellib/paths.py` | `WORDBITS_DIR`, `DNP_TEMPLATES_DIR` | 4, 8 |
| `sellib/web/themes/items.py` | Entrada no catálogo | 10 |
| `sellib/web/dashboard.py` | `Mount("/dnp-map", …)` | 10 |
| `tools/check_set_dnp_roundtrip.py` | Varredura sobre os RDBs reais | 10 |
| `docs/ENGINEERING-NOTES.md` | Gotchas novos | 10 |

---

### Task 1: Parser `SET_D` — round-trip byte a byte

Fidelidade de round-trip é o alicerce de tudo: estes bytes voltam para o arquivo de ajustes de um relé de proteção. Antes de qualquer modelo de pontos, `parse` seguido de `serialize` tem que devolver os bytes idênticos.

Três armadilhas medidas nos RDBs reais:

1. Cada linha de dados termina `KEY,"VALUE"\x1c\r\n`. O `0x1C` (File Separator) fica **dentro** da linha, entre o valor e o CRLF. As linhas de cabeçalho (`[INFO]`, `RELAYTYPE=…`, `[D1]`) terminam só com `\r\n`.
2. O padding do índice varia: o SEL-411L escreve `BI_1`, o SEL-751 e o SEL-2440 escrevem `BI_00`. A chave é preservada literalmente, nunca reconstruída.
3. O valor de slot livre varia: `""` no 411L, `"NA"` no 751/2440.

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py` (vazio)
- Create: `tests/test_set_dnp.py`
- Create: `sellib/parsers/set_dnp.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `RawLine(key: str, value: str, quoted: bool, terminator: bytes, raw: bytes)` — frozen dataclass.
  - `SetDnpFile(info: dict[str, str], section: str, lines: list[RawLine])`
  - `SetDnpFile.serialize() -> bytes`
  - `parse(data: bytes) -> SetDnpFile`

- [ ] **Step 1: Criar o ambiente de teste**

`requirements-dev.txt`:

```
# Dependencias so de desenvolvimento. `app.py` bootstrapa apenas
# requirements.txt -- nada daqui e' importado em producao.
pytest>=8.0
```

`tests/__init__.py` fica **vazio**, e não é decoração: com ele o `pytest` reconhece `tests/` como pacote e põe a raiz do repositório no `sys.path`, que é o que faz `import sellib` e `from tests.test_set_dnp import SAMPLE_411L` (usado a partir da Task 3) funcionarem sem instalar nada.

Rodar:

```bash
touch tests/__init__.py
.venv/bin/python -m pip install -r requirements-dev.txt
```

- [ ] **Step 2: Escrever o teste que falha**

`tests/test_set_dnp.py`:

```python
"""Round-trip do parser de SET_D*.TXT."""

from __future__ import annotations

import pytest

from sellib.parsers import set_dnp


# Um SET_D de 411L em miniatura: CRLF no cabecalho, 0x1C + CRLF nos dados,
# indice sem padding, slot livre como "".
SAMPLE_411L = (
    b"[INFO]\r\n"
    b"RELAYTYPE=SEL-411L-A\r\n"
    b"FID=SEL-411L-A-RXXX-VX-Z022004-DXXXXXXXX\r\n"
    b"BFID=SLBT-4XX-R300-V0-Z001002-D20200229\r\n"
    b"PARTNO=0411LAX6X5C7DDXH5D474XX\r\n"
    b"[D1]\r\n"
    b'MINDIST,"1.0"\x1c\r\n'
    b'MAXDIST,"10000.0"\x1c\r\n'
    b'BI_1,"PSV22"\x1c\r\n'
    b'BI_2,""\x1c\r\n'
    b'AI_1,"IAMAG"\x1c\r\n'
    b'AI_SCA1,"1.0"\x1c\r\n'
    b'AI_DBD1,"0.5"\x1c\r\n'
    b'CO_1,""\x1c\r\n'
    b'CO_DBD1,""\x1c\r\n'
)

# Um SET_D de 751: indice com padding de dois digitos, slot livre como "NA".
SAMPLE_751 = (
    b"[INFO]\r\n"
    b"RELAYTYPE=SEL-751\r\n"
    b"FID=SEL-751-RXXX-VX-Z102100-DXXXXXXXX\r\n"
    b"BFID=SLBT7XX-RXXX-VX-Z000000-DXXXXXXXX\r\n"
    b"PARTNO=751401A4A3A2A85AG30\r\n"
    b"[D1]\r\n"
    b'BO_00,"89OC2P1"\x1c\r\n'
    b'BO_01,"NA"\x1c\r\n'
    b'AI_00,"NA"\x1c\r\n'
)


@pytest.mark.parametrize("sample", [SAMPLE_411L, SAMPLE_751])
def test_roundtrip_is_byte_identical(sample):
    assert set_dnp.parse(sample).serialize() == sample


def test_parses_info_header():
    f = set_dnp.parse(SAMPLE_411L)
    assert f.info["RELAYTYPE"] == "SEL-411L-A"
    assert f.info["PARTNO"] == "0411LAX6X5C7DDXH5D474XX"


def test_parses_section_name():
    assert set_dnp.parse(SAMPLE_411L).section == "D1"
    assert set_dnp.parse(SAMPLE_751).section == "D1"


def test_keeps_the_file_separator_out_of_the_value():
    f = set_dnp.parse(SAMPLE_411L)
    line = next(l for l in f.lines if l.key == "BI_1")
    assert line.value == "PSV22"
    assert line.terminator == b"\x1c\r\n"


def test_header_lines_have_a_plain_crlf_terminator():
    f = set_dnp.parse(SAMPLE_411L)
    line = next(l for l in f.lines if l.raw == b"[INFO]")
    assert line.terminator == b"\r\n"


def test_empty_value_stays_quoted_and_empty():
    f = set_dnp.parse(SAMPLE_411L)
    line = next(l for l in f.lines if l.key == "BI_2")
    assert line.value == ""
    assert line.quoted is True


def test_serialize_reflects_an_edited_value():
    f = set_dnp.parse(SAMPLE_411L)
    f.set_value("BI_2", "IN205")
    assert b'BI_2,"IN205"\x1c\r\n' in f.serialize()
    # O resto do arquivo nao foi tocado.
    assert b'BI_1,"PSV22"\x1c\r\n' in f.serialize()


def test_set_value_on_an_unknown_key_raises():
    f = set_dnp.parse(SAMPLE_411L)
    with pytest.raises(KeyError):
        f.set_value("BI_999", "IN205")


def test_index_padding_is_preserved_on_edit():
    f = set_dnp.parse(SAMPLE_751)
    f.set_value("BO_01", "52A")
    assert b'BO_01,"52A"\x1c\r\n' in f.serialize()


def test_a_file_with_lf_only_terminators_still_roundtrips():
    sample = SAMPLE_411L.replace(b"\r\n", b"\n")
    assert set_dnp.parse(sample).serialize() == sample
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_set_dnp.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sellib.parsers.set_dnp'`

- [ ] **Step 4: Implementar o parser**

`sellib/parsers/set_dnp.py`:

```python
"""Parser/serializer of a SEL DNP map file (``SET_D<n>.TXT``) from an RDB.

Each relay in an RDB carries one ``SET_D<n>.TXT`` per DNP session. The file is
a settings file with an ``[INFO]`` header and a single ``[D<n>]`` section whose
data lines are ``KEY,"VALUE"`` followed by ``0x1C`` (File Separator) and CRLF.

This module is deliberately NOT built on ``sellib.parsers.sel_settings``: that
one treats the trailing ``0x1C`` as part of the value, which is fine for
reading and fatal for writing. Here round-trip fidelity is the contract --
``parse(data).serialize() == data`` for every SET_D in the reference RDBs --
because these bytes go back into a protection relay's settings.

Nothing here knows about the web, the session, or OLE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# Data line: KEY,"VALUE" or KEY,VALUE. The terminator (0x1C, CR, LF) is split
# off before this runs, so `$` is the real end of the payload.
_KV_RE = re.compile(r'^([^,]+),(.*)$', re.DOTALL)
_SECTION_RE = re.compile(r'^\[([^\]]+)\]$')
_INFO_RE = re.compile(r'^([^=]+)=(.*)$', re.DOTALL)


@dataclass(frozen=True)
class RawLine:
    """One physical line, kept faithfully enough to rebuild it byte for byte.

    ``raw`` is the line without its terminator, exactly as read. It is what
    ``serialize`` emits for any line that was never edited, which is how
    unknown or malformed lines survive a round trip untouched.
    """

    raw: bytes
    terminator: bytes          # b"\x1c\r\n", b"\r\n", b"\n", or b""
    key: str = ""              # "" when the line is not KEY,VALUE
    value: str = ""
    quoted: bool = False
    edited: bool = False

    def emit(self) -> bytes:
        if not self.edited:
            return self.raw + self.terminator
        payload = f'"{self.value}"' if self.quoted else self.value
        return f"{self.key},{payload}".encode("latin-1") + self.terminator


@dataclass
class SetDnpFile:
    """The parsed content of one SET_D<n>.TXT."""

    info: dict[str, str] = field(default_factory=dict)
    section: str = ""
    lines: list[RawLine] = field(default_factory=list)

    def index_of(self, key: str) -> int:
        for i, line in enumerate(self.lines):
            if line.key == key:
                return i
        raise KeyError(key)

    def get_value(self, key: str) -> str:
        return self.lines[self.index_of(key)].value

    def set_value(self, key: str, value: str) -> None:
        """Replace one key's value. Raises KeyError for a key the file lacks.

        Refusing unknown keys is deliberate: a DNP map has a fixed key set
        decided by the relay's firmware, so a key that isn't there is a bug on
        the caller's side, never a new point to invent.
        """
        i = self.index_of(key)
        old = self.lines[i]
        if old.value == value:
            return
        self.lines[i] = RawLine(
            raw=old.raw, terminator=old.terminator, key=old.key,
            value=value, quoted=old.quoted, edited=True,
        )

    def serialize(self) -> bytes:
        return b"".join(line.emit() for line in self.lines)


def _split_terminator(line: bytes) -> tuple[bytes, bytes]:
    """Peel b"\\x1c\\r\\n" / b"\\r\\n" / b"\\n" off the end of a raw line."""
    for term in (b"\x1c\r\n", b"\x1c\n", b"\r\n", b"\n"):
        if line.endswith(term):
            return line[: -len(term)], term
    return line, b""


def parse(data: bytes) -> SetDnpFile:
    """Tokenize a SET_D<n>.TXT. Never raises on malformed input.

    Anything that does not look like ``KEY,VALUE`` is carried through as an
    opaque line, so a file this parser does not fully understand still
    round-trips unchanged.
    """
    out = SetDnpFile()
    in_info = False

    # keepends so each physical line keeps its own terminator; a file with
    # mixed CRLF and LF (never seen, but cheap to survive) round-trips too.
    for raw_with_term in data.splitlines(keepends=True):
        body, term = _split_terminator(raw_with_term)
        text = body.decode("latin-1")

        ms = _SECTION_RE.match(text)
        if ms:
            name = ms.group(1)
            if name.upper() == "INFO":
                in_info = True
            else:
                in_info = False
                if not out.section:
                    out.section = name
            out.lines.append(RawLine(raw=body, terminator=term))
            continue

        if in_info:
            mi = _INFO_RE.match(text)
            if mi:
                out.info[mi.group(1)] = mi.group(2)
            out.lines.append(RawLine(raw=body, terminator=term))
            continue

        mk = _KV_RE.match(text)
        if mk:
            key, rest = mk.group(1), mk.group(2)
            quoted = len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"'
            value = rest[1:-1] if quoted else rest
            out.lines.append(RawLine(
                raw=body, terminator=term, key=key, value=value, quoted=quoted,
            ))
            continue

        out.lines.append(RawLine(raw=body, terminator=term))

    return out
```

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_set_dnp.py -v`
Expected: PASS, 11 testes.

- [ ] **Step 6: Commit**

```bash
git add requirements-dev.txt tests/__init__.py tests/test_set_dnp.py sellib/parsers/set_dnp.py
git commit -m "Parse and re-emit a SET_D DNP map byte for byte"
```

---

### Task 2: Modelo de pontos DNP

O parser entrega linhas; a interface precisa de pontos. Um ponto DNP é uma linha `BI_n`/`BO_n`/`AI_n`/`AO_n`/`CO_n`, e as chaves auxiliares dobram como colunas dele: `AI_SCAn` e `AI_DBDn` pertencem ao `AI_n`, `CO_DBDn` pertence ao `CO_n`. `MINDIST` e `MAXDIST` não são pontos.

**Files:**
- Modify: `sellib/parsers/set_dnp.py`
- Modify: `tests/test_set_dnp.py`

**Interfaces:**
- Consumes: `SetDnpFile`, `RawLine` da Task 1.
- Produces:
  - `POINT_KINDS: tuple[str, ...]` = `("BI", "BO", "AI", "AO", "CO")`
  - `DnpPoint(kind: str, index: int, key: str, value: str, sca_key: str | None, sca: str | None, dbd_key: str | None, dbd: str | None)`
  - `SetDnpFile.points() -> list[DnpPoint]` — ordem do arquivo
  - `SetDnpFile.blocks() -> dict[str, list[DnpPoint]]` — só os blocos presentes
  - `SetDnpFile.extras() -> list[tuple[str, str]]` — `MINDIST`/`MAXDIST` e afins, read-only

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `tests/test_set_dnp.py`:

```python
def test_points_carry_kind_and_index():
    pts = set_dnp.parse(SAMPLE_411L).points()
    bi1 = next(p for p in pts if p.key == "BI_1")
    assert (bi1.kind, bi1.index, bi1.value) == ("BI", 1, "PSV22")


def test_padded_index_is_parsed_as_a_number_but_key_is_kept():
    pts = set_dnp.parse(SAMPLE_751).points()
    bo1 = next(p for p in pts if p.index == 1 and p.kind == "BO")
    assert bo1.key == "BO_01"
    assert bo1.value == "NA"


def test_ai_sca_and_dbd_fold_into_the_ai_point():
    pts = set_dnp.parse(SAMPLE_411L).points()
    ai1 = next(p for p in pts if p.kind == "AI" and p.index == 1)
    assert ai1.value == "IAMAG"
    assert (ai1.sca_key, ai1.sca) == ("AI_SCA1", "1.0")
    assert (ai1.dbd_key, ai1.dbd) == ("AI_DBD1", "0.5")
    # E nao aparecem como pontos proprios.
    assert not any(p.key.startswith("AI_SCA") for p in pts)
    assert not any(p.key.startswith("AI_DBD") for p in pts)


def test_co_dbd_folds_into_the_co_point():
    ptss = set_dnp.parse(SAMPLE_411L).points()
    co1 = next(p for p in ptss if p.kind == "CO" and p.index == 1)
    assert (co1.dbd_key, co1.dbd) == ("CO_DBD1", "")
    assert not any(p.key.startswith("CO_DBD") for p in ptss)


def test_blocks_only_contains_what_the_file_has():
    blocks = set_dnp.parse(SAMPLE_751).blocks()
    assert set(blocks) == {"BO", "AI"}
    assert [p.key for p in blocks["BO"]] == ["BO_00", "BO_01"]


def test_mindist_and_maxdist_are_extras_not_points():
    f = set_dnp.parse(SAMPLE_411L)
    assert dict(f.extras()) == {"MINDIST": "1.0", "MAXDIST": "10000.0"}
    assert not any(p.key in ("MINDIST", "MAXDIST") for p in f.points())


def test_a_file_without_extras_reports_none():
    assert set_dnp.parse(SAMPLE_751).extras() == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_set_dnp.py -v`
Expected: FAIL — `AttributeError: 'SetDnpFile' object has no attribute 'points'`

- [ ] **Step 3: Implementar**

Acrescentar a `sellib/parsers/set_dnp.py`, antes de `_split_terminator`:

```python
# The five DNP point blocks, in the order the UI shows them as tabs.
POINT_KINDS: tuple[str, ...] = ("BI", "BO", "AI", "AO", "CO")

# BI_1, BO_00, AI_SCA12, CO_DBD3 -> (kind, modifier, index). The modifier is
# what makes AI_SCA1 a column of AI_1 instead of a point of its own.
_POINT_KEY_RE = re.compile(
    r'^(?P<kind>BI|BO|AI|AO|CO)_(?P<mod>SCA|DBD)?(?P<index>[0-9]+)$'
)


@dataclass(frozen=True)
class DnpPoint:
    """One row of the editor: a DNP point plus its auxiliary columns."""

    kind: str                    # "BI" | "BO" | "AI" | "AO" | "CO"
    index: int
    key: str                     # the literal key, original padding intact
    value: str
    sca_key: Optional[str] = None
    sca: Optional[str] = None
    dbd_key: Optional[str] = None
    dbd: Optional[str] = None
```

E os métodos, dentro de `SetDnpFile`:

```python
    def points(self) -> list[DnpPoint]:
        """The editable rows, in file order.

        ``AI_SCAn``/``AI_DBDn``/``CO_DBDn`` are not rows: they fold into the
        row of the point they qualify, which is how the relay means them.
        """
        mods: dict[tuple[str, int, str], tuple[str, str]] = {}
        base: list[tuple[str, int, str, str]] = []
        for line in self.lines:
            m = _POINT_KEY_RE.match(line.key) if line.key else None
            if m is None:
                continue
            kind = m.group("kind")
            index = int(m.group("index"))
            mod = m.group("mod")
            if mod:
                mods[(kind, index, mod)] = (line.key, line.value)
            else:
                base.append((kind, index, line.key, line.value))

        out: list[DnpPoint] = []
        for kind, index, key, value in base:
            sca = mods.get((kind, index, "SCA"))
            dbd = mods.get((kind, index, "DBD"))
            out.append(DnpPoint(
                kind=kind, index=index, key=key, value=value,
                sca_key=sca[0] if sca else None,
                sca=sca[1] if sca else None,
                dbd_key=dbd[0] if dbd else None,
                dbd=dbd[1] if dbd else None,
            ))
        return out

    def blocks(self) -> dict[str, list[DnpPoint]]:
        """Points grouped by kind. Only the blocks this file actually has."""
        out: dict[str, list[DnpPoint]] = {}
        for p in self.points():
            out.setdefault(p.kind, []).append(p)
        return {k: out[k] for k in POINT_KINDS if k in out}

    def extras(self) -> list[tuple[str, str]]:
        """Data keys that are not points: MINDIST, MAXDIST, anything unknown.

        Shown read-only. They configure the DNP session, not the map, and this
        tool has no business changing them.
        """
        return [
            (line.key, line.value)
            for line in self.lines
            if line.key and _POINT_KEY_RE.match(line.key) is None
        ]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_set_dnp.py -v`
Expected: PASS, 18 testes.

- [ ] **Step 5: Commit**

```bash
git add tests/test_set_dnp.py sellib/parsers/set_dnp.py
git commit -m "Group SET_D lines into DNP points with their SCA/DBD columns"
```

---

### Task 3: Descoberta de relés e sessões DNP num RDB extraído

**Achado importante que muda o desenho:** `RdbInfo.relays` **não serve** aqui. `sellib/parsers/rdb.py` só cria um `RelayEntry` para relé que tem arquivo `.gle` (`if gles:` em `_scan_existing`, e o dict só é populado a partir de streams `.gle` em `_extract_and_collect`). No RDB de referência isso descarta 3 dos 30 relés — e os 3 são justamente os SEL-2440, concentradores de dados, que têm `set_D1/D2/D3` e são alvo óbvio de um editor de mapa DNP.

Então a descoberta aqui varre o filesystem da extração, e usa `RdbInfo.relays` só para enriquecer com modelo e IP quando existir.

**Files:**
- Modify: `sellib/parsers/set_dnp.py`
- Create: `tests/test_set_dnp_discovery.py`

**Interfaces:**
- Consumes: `parse()` da Task 1; `RdbInfo` de `sellib.parsers.rdb`.
- Produces:
  - `DnpSession(name: str, fs_path: Path, stream_parts: tuple[str, ...], size: int)`
  - `DnpRelay(name: str, relaytype: str | None, sessions: list[DnpSession])`
  - `discover(extract_dir: Path) -> list[DnpRelay]`
  - `identical_groups(relay: DnpRelay) -> list[list[str]]`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_set_dnp_discovery.py`:

```python
"""Descoberta de reles com mapa DNP dentro de um RDB ja extraido."""

from __future__ import annotations

from sellib.parsers import set_dnp
from tests.test_set_dnp import SAMPLE_411L, SAMPLE_751


def _make_extraction(tmp_path):
    """Monta uma extracao de RDB de mentira, no mesmo layout do real."""
    relays = tmp_path / "Relays"

    # Rele com GLE e tres sessoes, duas delas identicas.
    a = relays / "QPC1_LT1_UPC1"
    (a / "Misc").mkdir(parents=True)
    (a / "Misc" / "GL1.gle").write_bytes(b"<xml/>")
    (a / "SET_D1.TXT").write_bytes(SAMPLE_411L)
    (a / "SET_D2.TXT").write_bytes(SAMPLE_411L)
    (a / "SET_D3.TXT").write_bytes(SAMPLE_411L.replace(b'"PSV22"', b'"PSV23"'))
    (a / "SET_G.TXT").write_bytes(b"[INFO]\r\n")

    # Rele SEM GLE nenhum -- o caso que RdbInfo.relays perde.
    b = relays / "SEL-2440 008 to 002"
    (b / "Misc").mkdir(parents=True)
    (b / "set_D1.txt").write_bytes(SAMPLE_751)

    # Rele sem mapa DNP: nao deve aparecer.
    c = relays / "TR1-2414"
    (c / "Misc").mkdir(parents=True)
    (c / "set_G.txt").write_bytes(b"[INFO]\r\n")

    return tmp_path


def test_finds_relays_that_have_a_dnp_map(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    assert [r.name for r in found] == ["QPC1_LT1_UPC1", "SEL-2440 008 to 002"]


def test_finds_a_relay_with_no_gle_at_all(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    r = next(r for r in found if r.name == "SEL-2440 008 to 002")
    assert [s.name for s in r.sessions] == ["D1"]


def test_sessions_are_sorted_and_named_by_their_section(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    r = next(r for r in found if r.name == "QPC1_LT1_UPC1")
    assert [s.name for s in r.sessions] == ["D1", "D2", "D3"]


def test_stream_parts_mirror_the_ole_path_with_original_case(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    a = next(r for r in found if r.name == "QPC1_LT1_UPC1")
    assert a.sessions[0].stream_parts == ("Relays", "QPC1_LT1_UPC1", "SET_D1.TXT")
    b = next(r for r in found if r.name == "SEL-2440 008 to 002")
    assert b.sessions[0].stream_parts == ("Relays", "SEL-2440 008 to 002", "set_D1.txt")


def test_relaytype_comes_from_the_info_header(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    assert found[0].relaytype == "SEL-411L-A"
    assert found[1].relaytype == "SEL-751"


def test_identical_sessions_are_grouped(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    a = next(r for r in found if r.name == "QPC1_LT1_UPC1")
    assert set_dnp.identical_groups(a) == [["D1", "D2"], ["D3"]]


def test_a_missing_extraction_yields_nothing(tmp_path):
    assert set_dnp.discover(tmp_path / "nao-existe") == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_set_dnp_discovery.py -v`
Expected: FAIL — `AttributeError: module 'sellib.parsers.set_dnp' has no attribute 'discover'`

- [ ] **Step 3: Implementar**

Acrescentar a `sellib/parsers/set_dnp.py` (e ao topo, `import hashlib`, `from pathlib import Path`):

```python
# SET_D1.TXT / set_D3.txt. The 4xx extract uppercase, the 3xx/7xx lowercase --
# the OLE is case-insensitive, the filesystem after extraction is not.
_SETD_NAME_RE = re.compile(r'^set_d([0-9]+)\.txt$', re.IGNORECASE)


@dataclass(frozen=True)
class DnpSession:
    """One SET_D<n> of one relay: a DNP session's map."""

    name: str                        # "D1"
    fs_path: Path                    # absolute path in the extraction
    stream_parts: tuple[str, ...]    # the path inside the RDB's OLE storage
    size: int                        # bytes of the original stream


@dataclass(frozen=True)
class DnpRelay:
    """A relay that has at least one DNP map."""

    name: str
    relaytype: Optional[str]
    sessions: list[DnpSession]


def discover(extract_dir: Path) -> list[DnpRelay]:
    """Find every relay with a DNP map inside an extracted RDB.

    This walks the extraction instead of reusing ``RdbInfo.relays`` on
    purpose: ``sellib.parsers.rdb`` only builds a ``RelayEntry`` for a relay
    that owns a ``.gle`` file, which silently drops data concentrators like the
    SEL-2440 -- exactly the devices whose DNP map someone wants to edit.
    """
    relays_dir = Path(extract_dir) / "Relays"
    if not relays_dir.is_dir():
        return []

    out: list[DnpRelay] = []
    for relay_dir in sorted(relays_dir.iterdir(), key=lambda p: p.name):
        if not relay_dir.is_dir():
            continue
        sessions: list[DnpSession] = []
        relaytype: Optional[str] = None
        for child in sorted(relay_dir.iterdir(), key=lambda p: p.name.upper()):
            if not child.is_file() or not _SETD_NAME_RE.match(child.name):
                continue
            data = child.read_bytes()
            parsed = parse(data)
            if relaytype is None:
                relaytype = parsed.info.get("RELAYTYPE")
            sessions.append(DnpSession(
                name=parsed.section or child.stem.upper().replace("SET_", ""),
                fs_path=child,
                stream_parts=("Relays", relay_dir.name, child.name),
                size=len(data),
            ))
        if sessions:
            out.append(DnpRelay(
                name=relay_dir.name, relaytype=relaytype, sessions=sessions,
            ))
    return out


def identical_groups(relay: DnpRelay) -> list[list[str]]:
    """Group a relay's sessions by identical content.

    Relays usually carry the same map on every DNP session -- all five are
    byte-identical in the reference RDB -- so the editor says so instead of
    making someone diff five tabs by eye.
    """
    by_digest: dict[str, list[str]] = {}
    order: list[str] = []
    for s in relay.sessions:
        digest = hashlib.sha256(s.fs_path.read_bytes()).hexdigest()
        if digest not in by_digest:
            order.append(digest)
        by_digest.setdefault(digest, []).append(s.name)
    return [by_digest[d] for d in order]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: PASS, 25 testes.

- [ ] **Step 5: Verificar contra os RDBs de verdade**

```bash
.venv/bin/python -c "
from pathlib import Path
from sellib.parsers import set_dnp
root = Path('cache/rdb')
for entry in root.iterdir():
    ext = entry / 'extracted'
    if not ext.is_dir():
        continue
    relays = set_dnp.discover(ext)
    print(entry.name[:12], len(relays), 'reles com mapa DNP')
    for r in relays[:3]:
        print('  ', r.name, r.relaytype, [s.name for s in r.sessions],
              set_dnp.identical_groups(r))
"
```

Expected: pelo menos um RDB com dezenas de relés, os SEL-2440 presentes na lista, e grupos de sessões idênticas aparecendo.

- [ ] **Step 6: Commit**

```bash
git add tests/test_set_dnp_discovery.py sellib/parsers/set_dnp.py
git commit -m "Discover DNP maps by walking the extraction, not the GLE list"
```

---

### Task 4: Validador de word bits

Avisa, nunca bloqueia. Sem arquivo para o modelo, a validação fica desligada e a página diz isso.

**Files:**
- Modify: `sellib/paths.py`
- Create: `sellib/core/wordbits.py`
- Create: `data/wordbits/SEL-411L.json`
- Create: `data/wordbits/SEL-751.json`
- Create: `tools/wordbits_from_glv_cache.py`
- Create: `tests/test_wordbits.py`

**Interfaces:**
- Consumes: nada dos módulos anteriores.
- Produces:
  - `WordbitSet.check(value: str) -> str` — `"ok"` ou `"desconhecido"`
  - `lookup(relaytype: str | None) -> WordbitSet | None`
  - `duplicates(values: list[str], always_valid: set[str]) -> set[str]`

- [ ] **Step 1: Acrescentar a constante de caminho**

Em `sellib/paths.py`, logo abaixo de `RELAY_MODELS_DIR`:

```python
# Listas de word bits validos por modelo de rele, usadas pelo editor de mapa
# DNP pra avisar (nunca bloquear) sobre um nome que o rele nao conhece.
WORDBITS_DIR: Path = DATA_DIR / "wordbits"
```

- [ ] **Step 2: Escrever o teste que falha**

`tests/test_wordbits.py`:

```python
"""Registry de word bits validos por modelo."""

from __future__ import annotations

import json

from sellib.core import wordbits


def _write(tmp_path, name, payload):
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")


def _load(tmp_path, monkeypatch):
    monkeypatch.setattr(wordbits, "_CACHE", None, raising=False)
    monkeypatch.setattr(wordbits, "WORDBITS_DIR", tmp_path)
    return wordbits


def test_lookup_finds_by_model_alias(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": ["411L-A"],
        "always_valid": ["", "NA"], "bits": ["LOP"], "patterns": [],
    })
    wb = _load(tmp_path, monkeypatch)
    assert wb.lookup("SEL-411L-A") is not None
    assert wb.lookup("SEL-411L") is not None


def test_unknown_model_has_no_set(tmp_path, monkeypatch):
    wb = _load(tmp_path, monkeypatch)
    assert wb.lookup("SEL-999") is None
    assert wb.lookup(None) is None


def test_check_accepts_a_listed_bit(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": [],
        "always_valid": ["", "NA"], "bits": ["LOP", "52A"], "patterns": [],
    })
    wb = _load(tmp_path, monkeypatch)
    s = wb.lookup("SEL-411L")
    assert s.check("LOP") == "ok"
    assert s.check("52A") == "ok"


def test_check_accepts_always_valid_placeholders(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": [],
        "always_valid": ["", "NA", "0", "1"], "bits": ["LOP"], "patterns": [],
    })
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert s.check("") == "ok"
    assert s.check("NA") == "ok"
    assert s.check("0") == "ok"


def test_check_accepts_a_pattern_match(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": [],
        "always_valid": [], "bits": [],
        "patterns": [{"re": "^PSV[0-9]{2}$", "label": "SELOGIC"}],
    })
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert s.check("PSV22") == "ok"
    assert s.check("PSV2") == "desconhecido"


def test_check_is_case_insensitive(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": [],
        "always_valid": [], "bits": ["LOP"], "patterns": [],
    })
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert s.check("lop") == "ok"


def test_check_rejects_an_unknown_bit(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": [],
        "always_valid": [], "bits": ["LOP"], "patterns": [],
    })
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert s.check("BANANA") == "desconhecido"


def test_duplicates_ignores_placeholders():
    found = wordbits.duplicates(
        ["IN101", "NA", "NA", "IN101", "", "", "IN102"],
        always_valid={"", "NA"},
    )
    assert found == {"IN101"}


def test_duplicates_is_case_insensitive():
    assert wordbits.duplicates(["LOP", "lop"], always_valid=set()) == {"LOP"}


def test_the_shipped_files_load(monkeypatch):
    monkeypatch.setattr(wordbits, "_CACHE", None, raising=False)
    assert wordbits.lookup("SEL-411L-A") is not None
    assert wordbits.lookup("SEL-751") is not None
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_wordbits.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sellib.core.wordbits'`

- [ ] **Step 4: Implementar o registry**

`sellib/core/wordbits.py`:

```python
"""Word bits a relay model accepts, used to warn about a DNP map entry.

The DNP map editor writes Relay Word bit names into a relay's settings. A typo
("PSV2" for "PSV02") is silently accepted by the RDB and only shows up as a
dead point during commissioning. This module flags those.

It only ever WARNS. There is no arrangement of data here that stops an export:
the lists are curated by hand and will always trail the firmware, so a bit this
module has never heard of is at least as likely to be our gap as the user's
mistake.

The lists live in ``data/wordbits/<MODEL>.json`` and are seeded from the GLV's
own per-FID Relay Word discovery -- see ``tools/wordbits_from_glv_cache.py``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sellib.paths import WORDBITS_DIR

_logger = logging.getLogger(__name__)

_CACHE: "dict[str, WordbitSet] | None" = None


@dataclass
class WordbitSet:
    """The bits one relay model accepts, plus the placeholders that always do."""

    model: str
    bits: set[str] = field(default_factory=set)          # upper-cased
    always_valid: set[str] = field(default_factory=set)  # upper-cased
    patterns: list[tuple[re.Pattern, str]] = field(default_factory=list)

    def check(self, value: str) -> str:
        """``"ok"`` or ``"desconhecido"``. Never raises, never blocks."""
        v = (value or "").strip().upper()
        if v in self.always_valid or v in self.bits:
            return "ok"
        for rx, _label in self.patterns:
            if rx.match(v):
                return "ok"
        return "desconhecido"


def duplicates(values: list[str], always_valid: set[str]) -> set[str]:
    """Bits that appear at more than one DNP index in the same block.

    Legal in DNP and occasionally deliberate; almost always a copy-paste slip.
    Placeholders are excluded -- a hundred free slots reading "NA" are not a
    hundred duplicates.
    """
    skip = {a.strip().upper() for a in always_valid}
    seen: set[str] = set()
    dup: set[str] = set()
    for raw in values:
        v = (raw or "").strip().upper()
        if not v or v in skip:
            continue
        if v in seen:
            dup.add(v)
        seen.add(v)
    return dup


def _key_variants(relaytype: str) -> list[str]:
    """'SEL-411L-A' -> ['411L-A', '411L', 'SEL-411L-A', 'SEL-411L']."""
    raw = (relaytype or "").strip().upper()
    if not raw:
        return []
    stripped = raw[4:] if raw.startswith("SEL-") else raw
    out = [stripped, raw]
    # Vai tirando sufixos de opcao ("-A", "-3") ate sobrar so o modelo.
    parts = stripped.split("-")
    while len(parts) > 1:
        parts = parts[:-1]
        out.append("-".join(parts))
        out.append("SEL-" + "-".join(parts))
    seen: list[str] = []
    for k in out:
        if k not in seen:
            seen.append(k)
    return seen


def _load_all() -> "dict[str, WordbitSet]":
    index: dict[str, WordbitSet] = {}
    if not WORDBITS_DIR.is_dir():
        return index
    for path in sorted(WORDBITS_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            _logger.warning("[wordbits] %s ilegivel: %s", path.name, e)
            continue
        model = str(raw.get("model", "")).strip().upper()
        if not model:
            _logger.warning("[wordbits] %s sem 'model'; ignorado", path.name)
            continue
        patterns: list[tuple[re.Pattern, str]] = []
        for p in raw.get("patterns", []):
            try:
                patterns.append((re.compile(p["re"], re.IGNORECASE),
                                 p.get("label", "")))
            except (KeyError, re.error) as e:
                _logger.warning("[wordbits] %s: padrao invalido (%s)",
                                path.name, e)
        wbs = WordbitSet(
            model=model,
            bits={str(b).strip().upper() for b in raw.get("bits", [])},
            always_valid={str(a).strip().upper()
                          for a in raw.get("always_valid", [])},
            patterns=patterns,
        )
        for alias in [model] + [str(a) for a in raw.get("model_aliases", [])]:
            for key in _key_variants(alias):
                index.setdefault(key, wbs)
    return index


def lookup(relaytype: "str | None") -> Optional[WordbitSet]:
    """Find the set for a RELAYTYPE. ``None`` means validation is off."""
    global _CACHE
    if _CACHE is None:
        _CACHE = _load_all()
    if not relaytype:
        return None
    for key in _key_variants(relaytype):
        found = _CACHE.get(key)
        if found is not None:
            return found
    return None
```

Nota para quem implementa: os testes trocam `wordbits.WORDBITS_DIR` por `monkeypatch`, então `_load_all` tem que ler a variável do módulo em tempo de chamada — é o que o código acima faz, porque `WORDBITS_DIR` é resolvido dentro da função.

- [ ] **Step 5: Escrever o script que semeia as listas**

`tools/wordbits_from_glv_cache.py`:

```python
#!/usr/bin/env python3
"""Seed data/wordbits/<MODEL>.json from what the GLV already harvested.

Two sources, because the cache holds two shapes and neither is available for
every relay:

* ``cache/<FID>.json`` -- the GLV's name -> (row, bit) map. Its ``bit_to_pos``
  keys are every named bit that firmware reports.
* ``cache/<FID>_DNA.txt`` -- the raw Relay Word dump: a header line, then rows
  of eight quoted bit names plus a checksum, with ``*`` for an unnamed slot.

    python3 tools/wordbits_from_glv_cache.py cache/SEL-411L-A-R133-....json \\
        --model 411L --alias 411L-A
    python3 tools/wordbits_from_glv_cache.py cache/SEL-751-R402-..._DNA.txt \\
        --model 751

Merges into an existing file (union of ``bits``), so hand edits survive a
re-harvest.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sellib.paths import WORDBITS_DIR  # noqa: E402

DEFAULT_ALWAYS_VALID = ["", "NA", "0", "1"]

_QUOTED = re.compile(r'"([^"]*)"')


def _clean(names) -> set:
    """Upper-case, drop blanks, drop the `*` that marks an unnamed slot."""
    return {
        str(n).strip().upper()
        for n in names
        if str(n).strip() and str(n).strip() != "*"
    }


def harvest_json(path: Path) -> tuple[set, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _clean(raw.get("bit_to_pos", {})), str(raw.get("fid", ""))


def harvest_dna(path: Path) -> tuple[set, str]:
    """Read a Relay Word dump: eight quoted names per row, then a checksum."""
    bits: set = set()
    for line in path.read_text(encoding="latin-1").splitlines():
        tokens = _QUOTED.findall(line)
        if len(tokens) < 2:
            continue
        bits |= _clean(tokens[:-1])          # o ultimo token e' o checksum
    # O nome do arquivo e' "<FID>_DNA.txt".
    return bits, path.name[: -len("_DNA.txt")] if path.name.endswith("_DNA.txt") else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cache_file", type=Path,
                    help="cache/<FID>.json ou cache/<FID>_DNA.txt do GLV")
    ap.add_argument("--model", required=True, help="ex.: 411L")
    ap.add_argument("--alias", action="append", default=[],
                    help="alias de modelo; pode repetir")
    args = ap.parse_args()

    if args.cache_file.suffix.lower() == ".json":
        bits, fid = harvest_json(args.cache_file)
    else:
        bits, fid = harvest_dna(args.cache_file)
    harvested = sorted(bits)
    if not harvested:
        print(f"nenhum bit encontrado em {args.cache_file}", file=sys.stderr)
        return 1

    WORDBITS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = WORDBITS_DIR / f"SEL-{args.model}.json"

    if out_path.is_file():
        doc = json.loads(out_path.read_text(encoding="utf-8"))
        before = len(doc.get("bits", []))
    else:
        doc = {
            "schema_version": 1,
            "model": args.model,
            "model_aliases": [],
            "always_valid": DEFAULT_ALWAYS_VALID,
            "bits": [],
            "patterns": [],
        }
        before = 0

    doc["model_aliases"] = sorted(set(doc.get("model_aliases", [])) | set(args.alias))
    doc["bits"] = sorted(set(doc.get("bits", [])) | set(harvested))
    doc["source"] = {"fid": fid, "harvested_at": date.today().isoformat()}

    out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"{out_path}: {before} -> {len(doc['bits'])} bits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Gerar as duas listas que o repositório já consegue gerar**

O 411L tem um `cache/<FID>.json` do GLV; o 751 só tem o dump bruto do Relay Word, e por isso o script aceita os dois formatos.

```bash
mkdir -p data/wordbits
.venv/bin/python tools/wordbits_from_glv_cache.py \
    cache/SEL-411L-A-R133-V2-Z022004-D20251103.json --model 411L --alias 411L-A
.venv/bin/python tools/wordbits_from_glv_cache.py \
    cache/SEL-751-R402-V2-Z102100-D20250731_DNA.txt --model 751
```

Confira o que saiu antes de seguir:

```bash
.venv/bin/python -c "
import json
for m in ('411L', '751'):
    d = json.load(open(f'data/wordbits/SEL-{m}.json'))
    print(m, len(d['bits']), 'bits', d['bits'][:8])
"
```

Expected: centenas de bits em cada. Se algum sair com zero, o arquivo de origem não era o esperado — não invente uma lista vazia, resolva a origem primeiro.

Depois, acrescente os padrões a `data/wordbits/SEL-411L.json`, no campo `patterns`, para as faixas grandes demais para enumerar:

```json
"patterns": [
  {"re": "^PSV[0-9]{2}$", "label": "SELOGIC protection variable"},
  {"re": "^ASV[0-9]{3}$", "label": "SELOGIC automation variable"},
  {"re": "^PLT[0-9]{2}$", "label": "protection latch"},
  {"re": "^VB[0-9]{3}$", "label": "virtual bit"},
  {"re": "^IN[0-9]{3}$", "label": "contact input"},
  {"re": "^OUT[0-9]{3}$", "label": "contact output"}
]
```

E a `data/wordbits/SEL-751.json`:

```json
"patterns": [
  {"re": "^SV[0-9]{2}T?$", "label": "SELOGIC variable"},
  {"re": "^LT[0-9]{2}$", "label": "latch"},
  {"re": "^SC[0-9]{2}$", "label": "SELOGIC counter"},
  {"re": "^VB[0-9]{3}$", "label": "virtual bit"},
  {"re": "^IN[0-9]{3}$", "label": "contact input"},
  {"re": "^OUT[0-9]{3}$", "label": "contact output"}
]
```

- [ ] **Step 7: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_wordbits.py -v`
Expected: PASS, 10 testes — incluindo `test_the_shipped_files_load`, que só passa depois do Step 6.

- [ ] **Step 8: Conferir a validação contra um mapa real**

```bash
.venv/bin/python -c "
from pathlib import Path
from sellib.parsers import set_dnp
from sellib.core import wordbits
for entry in Path('cache/rdb').iterdir():
    ext = entry / 'extracted'
    if not ext.is_dir(): continue
    for r in set_dnp.discover(ext)[:4]:
        s = wordbits.lookup(r.relaytype)
        if s is None:
            print(r.name, r.relaytype, '-> sem lista'); continue
        f = set_dnp.parse(r.sessions[0].fs_path.read_bytes())
        bad = [p.key + '=' + p.value for p in f.points()
               if s.check(p.value) == 'desconhecido']
        print(r.name, r.relaytype, len(bad), 'desconhecidos', bad[:8])
"
```

Expected: poucos desconhecidos. Se um relé real acusar centenas, a lista está incompleta — acrescente os padrões que faltam antes de seguir, senão a interface vira um mar de avisos falsos.

- [ ] **Step 9: Commit**

```bash
git add sellib/paths.py sellib/core/wordbits.py data/wordbits tools/wordbits_from_glv_cache.py tests/test_wordbits.py
git commit -m "Warn about DNP map entries a relay model does not know"
```

---

### Task 5: Writer de OLE — reconstruir um RDB com streams de tamanho novo

`olefile.write_stream` exige que o novo stream tenha **exatamente** o tamanho do original. Rearranjar valores dentro de um bloco conserva os bytes, mas preencher um slot livre (`BI_44,""` → `BI_44,"IN210"`) cresce o arquivo, e não há folga num `SET_D`. Este módulo é o que permite crescer.

Formato: Compound File Binary v3 (MS-CFB). Setor 512 B, mini-setor 64 B, corte do mini stream em 4096 B.

**Files:**
- Create: `sellib/parsers/ole_rebuild.py`
- Create: `tests/test_ole_rebuild.py`

**Interfaces:**
- Consumes: nada dos módulos anteriores.
- Produces:
  - `Entry(name: str, is_storage: bool, size: int, read: Callable[[], bytes] | None, children: list[Entry])`
  - `write_ole(dst: Path, root_children: list[Entry]) -> None`
  - `rebuild(src: Path, dst: Path, replacements: dict[tuple[str, ...], bytes]) -> None`
  - `OleRebuildError(Exception)`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_ole_rebuild.py`:

```python
"""Escrita de um Compound File valido, lido de volta pelo olefile."""

from __future__ import annotations

import olefile
import pytest

from sellib.parsers import ole_rebuild as ore


def _stream(name: str, data: bytes) -> ore.Entry:
    return ore.Entry(name=name, is_storage=False, size=len(data),
                     read=lambda d=data: d, children=[])


def _storage(name: str, children) -> ore.Entry:
    return ore.Entry(name=name, is_storage=True, size=0, read=None,
                     children=list(children))


def test_writes_a_file_olefile_recognises(tmp_path):
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Hello", b"world")])
    assert olefile.isOleFile(str(dst))


def test_a_small_stream_roundtrips_through_the_mini_fat(tmp_path):
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Small", b"abc")])
    ole = olefile.OleFileIO(str(dst))
    try:
        assert ole.openstream(["Small"]).read() == b"abc"
        assert ole.get_size(["Small"]) == 3
    finally:
        ole.close()


def test_a_large_stream_roundtrips_through_the_main_fat(tmp_path):
    data = bytes(range(256)) * 400        # 102.400 bytes, bem acima do corte
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Big", data)])
    ole = olefile.OleFileIO(str(dst))
    try:
        assert ole.openstream(["Big"]).read() == data
    finally:
        ole.close()


def test_a_stream_exactly_at_the_cutoff_is_not_mini(tmp_path):
    data = b"x" * 4096
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Edge", data)])
    ole = olefile.OleFileIO(str(dst))
    try:
        assert ole.openstream(["Edge"]).read() == data
    finally:
        ole.close()


def test_an_empty_stream_roundtrips(tmp_path):
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [_stream("Empty", b"")])
    ole = olefile.OleFileIO(str(dst))
    try:
        assert ole.openstream(["Empty"]).read() == b""
    finally:
        ole.close()


def test_nested_storages_roundtrip(tmp_path):
    dst = tmp_path / "out.bin"
    ore.write_ole(dst, [
        _storage("Relays", [
            _storage("QPC1_LT1_UPC1", [
                _stream("SET_D1.TXT", b"[D1]\r\n"),
                _storage("Misc", [_stream("Cfg.txt", b"[INFO]\r\n")]),
            ]),
        ]),
    ])
    ole = olefile.OleFileIO(str(dst))
    try:
        found = {tuple(e) for e in ole.listdir(streams=True, storages=False)}
        assert ("Relays", "QPC1_LT1_UPC1", "SET_D1.TXT") in found
        assert ("Relays", "QPC1_LT1_UPC1", "Misc", "Cfg.txt") in found
        assert ole.openstream(
            ["Relays", "QPC1_LT1_UPC1", "SET_D1.TXT"]).read() == b"[D1]\r\n"
    finally:
        ole.close()


def test_many_siblings_are_all_reachable(tmp_path):
    """A arvore de diretorio e' rubro-negra; com 200 irmaos, uma arvore mal
    montada perde entradas na busca em vez de estourar."""
    dst = tmp_path / "out.bin"
    names = [f"S{i:03d}" for i in range(200)]
    ore.write_ole(dst, [_stream(n, n.encode()) for n in names])
    ole = olefile.OleFileIO(str(dst))
    try:
        for n in names:
            assert ole.openstream([n]).read() == n.encode()
    finally:
        ole.close()


def test_a_name_too_long_is_refused(tmp_path):
    with pytest.raises(ore.OleRebuildError):
        ore.write_ole(tmp_path / "out.bin", [_stream("x" * 32, b"a")])


def test_rebuild_reproduces_a_file_byte_for_byte_when_nothing_changes(tmp_path):
    src = tmp_path / "src.bin"
    ore.write_ole(src, [
        _storage("Relays", [
            _storage("R1", [_stream("SET_D1.TXT", b"[D1]\r\n" + b"a" * 9000)]),
        ]),
        _stream("Tiny", b"z" * 10),
    ])
    dst = tmp_path / "dst.bin"
    ore.rebuild(src, dst, {})

    a = olefile.OleFileIO(str(src))
    b = olefile.OleFileIO(str(dst))
    try:
        ea = sorted(tuple(e) for e in a.listdir(streams=True, storages=True))
        eb = sorted(tuple(e) for e in b.listdir(streams=True, storages=True))
        assert ea == eb
        for e in a.listdir(streams=True, storages=False):
            assert a.openstream(e).read() == b.openstream(e).read()
    finally:
        a.close()
        b.close()


def test_rebuild_replaces_a_stream_with_a_longer_one(tmp_path):
    src = tmp_path / "src.bin"
    ore.write_ole(src, [
        _storage("Relays", [_storage("R1", [_stream("SET_D1.TXT", b"short")])]),
    ])
    dst = tmp_path / "dst.bin"
    grown = b"a much longer stream than before" * 500
    ore.rebuild(src, dst, {("Relays", "R1", "SET_D1.TXT"): grown})

    ole = olefile.OleFileIO(str(dst))
    try:
        assert ole.openstream(["Relays", "R1", "SET_D1.TXT"]).read() == grown
    finally:
        ole.close()


def test_rebuild_refuses_a_replacement_for_a_stream_that_is_not_there(tmp_path):
    src = tmp_path / "src.bin"
    ore.write_ole(src, [_stream("A", b"a")])
    with pytest.raises(ore.OleRebuildError):
        ore.rebuild(src, tmp_path / "dst.bin", {("Nope",): b"x"})


def test_rebuild_deletes_the_output_when_verification_fails(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    ore.write_ole(src, [_stream("A", b"aaaa")])
    dst = tmp_path / "dst.bin"

    # Sabota a escrita: o verificador tem que pegar e apagar a saida.
    real = ore.write_ole

    def sabotaged(path, children):
        real(path, [_stream("A", b"bbbb")])

    monkeypatch.setattr(ore, "write_ole", sabotaged)
    with pytest.raises(ore.OleRebuildError):
        ore.rebuild(src, dst, {})
    assert not dst.exists()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_ole_rebuild.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sellib.parsers.ole_rebuild'`

- [ ] **Step 3: Implementar o writer**

`sellib/parsers/ole_rebuild.py`. O algoritmo, antes do código:

1. **Coletar** a árvore (storages, streams, tamanhos, leitores preguiçosos).
2. **Classificar** cada stream: `size < 4096` vai para o mini stream, o resto para setores normais.
3. **Contar setores**, nesta ordem fixa de layout, o que torna os números de setor deriváveis sem um segundo passe sobre os dados:
   `[FAT][DIFAT][diretório][miniFAT][mini stream][streams normais]`
   - `mini_sectors  = Σ ceil(size/64)` para os streams mini
   - `ministream_sectors = ceil(mini_sectors*64 / 512)`
   - `dir_sectors   = ceil((n_entries+1) / 4)` (4 entradas de 128 B por setor)
   - `minifat_sectors = ceil(mini_sectors*4 / 512)`
   - `data_sectors  = ceil(size/512)` somado sobre os streams normais
   - `fat`/`difat`: ponto fixo, porque a FAT precisa de entradas para si mesma:
     ```
     fat_sectors = difat_sectors = 0
     repetir ate estabilizar:
         total  = fat_sectors + difat_sectors + dir + minifat + ministream + data
         fat_sectors   = ceil(total / 128)
         difat_sectors = 0 if fat_sectors <= 109 else ceil((fat_sectors-109)/127)
     ```
4. **Montar as entradas de diretório**. Ordem canônica dos irmãos: `(len(nome), nome.upper())`. A árvore rubro-negra vira uma BST balanceada construída do array ordenado (raiz no meio), com todos os nós pretos — é o que a especificação da Microsoft manda ler com tolerância, e o que o `olefile` aceita.
5. **Escrever** o cabeçalho de 512 B e depois cada bloco na ordem do layout, sempre completando o último setor com zeros.

```python
"""Write a Compound File (MS-CFB v3), so an RDB stream can change size.

``olefile.write_stream`` replaces a stream only with data of exactly the same
length. That is enough to rearrange a DNP map -- moving values conserves bytes
-- and not enough to fill a free slot, which grows the file and has no slack to
grow into. This module rebuilds the whole container instead.

Layout written, in this fixed order, which is what makes every sector number
derivable without a second pass over the data:

    [header 512B][FAT][DIFAT][directory][miniFAT][mini stream][streams]

``rebuild()`` verifies its own output before returning: it reopens the result
with ``olefile`` and compares every stream against the source, except the ones
that were meant to change. A bug in this writer has to surface as a failed
export, never as a silently corrupt relay settings file.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

import olefile

SECTOR_SIZE = 512
MINI_SECTOR_SIZE = 64
MINI_CUTOFF = 4096
DIR_ENTRY_SIZE = 128
DIR_PER_SECTOR = SECTOR_SIZE // DIR_ENTRY_SIZE          # 4
FAT_PER_SECTOR = SECTOR_SIZE // 4                        # 128
DIFAT_PER_SECTOR = FAT_PER_SECTOR - 1                    # 127
MINI_PER_SECTOR = SECTOR_SIZE // MINI_SECTOR_SIZE        # 8

MAXREGSECT = 0xFFFFFFFA
DIFSECT = 0xFFFFFFFC
FATSECT = 0xFFFFFFFD
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
NOSTREAM = 0xFFFFFFFF

_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Entry types (MS-CFB 2.6.1)
_TYPE_STORAGE = 1
_TYPE_STREAM = 2
_TYPE_ROOT = 5
_COLOR_BLACK = 1


class OleRebuildError(Exception):
    """Refusing to write, or refusing to hand over what was written."""


@dataclass
class Entry:
    """One node of the tree to write. ``read`` is called once, lazily."""

    name: str
    is_storage: bool
    size: int
    read: Optional[Callable[[], bytes]]
    children: list["Entry"] = field(default_factory=list)


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _sibling_order(entries: list[Entry]) -> list[Entry]:
    """Canonical CFB sibling order: shorter names first, then case-insensitive."""
    return sorted(entries, key=lambda e: (len(e.name), e.name.upper()))


def _flatten(root_children: list[Entry]) -> list[Entry]:
    """Depth-first list of every node, root excluded."""
    out: list[Entry] = []

    def walk(nodes: list[Entry]) -> None:
        for n in _sibling_order(nodes):
            out.append(n)
            if n.is_storage:
                walk(n.children)

    walk(root_children)
    return out


def _build_tree(ids: list[int]) -> tuple[int, dict[int, tuple[int, int]]]:
    """Balanced BST over an already-sorted sibling list.

    Returns ``(root_id, {id: (left, right)})``. All nodes are written black:
    the format wants a red-black tree, readers in practice want a tree whose
    in-order walk is the sorted order, and a balanced all-black tree is both
    for any list a real RDB contains.
    """
    links: dict[int, tuple[int, int]] = {}

    def build(lo: int, hi: int) -> int:
        if lo > hi:
            return NOSTREAM
        mid = (lo + hi) // 2
        node = ids[mid]
        links[node] = (build(lo, mid - 1), build(mid + 1, hi))
        return node

    root = build(0, len(ids) - 1)
    return root, links
```

Continua no mesmo arquivo — a montagem e a escrita:

```python
def write_ole(dst: Path, root_children: list[Entry]) -> None:
    """Write a complete Compound File containing ``root_children``."""
    dst = Path(dst)
    nodes = _flatten(root_children)
    for n in nodes:
        if len(n.name) > 31:
            raise OleRebuildError(
                f"nome longo demais para o formato OLE (max 31): {n.name!r}")

    # id 0 e' sempre a Root Entry; os demais seguem a ordem depth-first.
    ids = {id(n): i + 1 for i, n in enumerate(nodes)}

    mini_nodes = [n for n in nodes
                  if not n.is_storage and 0 < n.size < MINI_CUTOFF]
    reg_nodes = [n for n in nodes
                 if not n.is_storage and n.size >= MINI_CUTOFF]

    mini_sectors = sum(_ceil_div(n.size, MINI_SECTOR_SIZE) for n in mini_nodes)
    ministream_bytes = mini_sectors * MINI_SECTOR_SIZE
    ministream_sectors = _ceil_div(ministream_bytes, SECTOR_SIZE)
    dir_sectors = _ceil_div(len(nodes) + 1, DIR_PER_SECTOR)
    minifat_sectors = _ceil_div(mini_sectors * 4, SECTOR_SIZE)
    data_sectors = sum(_ceil_div(n.size, SECTOR_SIZE) for n in reg_nodes)

    fat_sectors = 0
    difat_sectors = 0
    for _ in range(16):                      # converge em 2-3 voltas
        total = (fat_sectors + difat_sectors + dir_sectors
                 + minifat_sectors + ministream_sectors + data_sectors)
        new_fat = _ceil_div(total, FAT_PER_SECTOR)
        new_difat = (0 if new_fat <= 109
                     else _ceil_div(new_fat - 109, DIFAT_PER_SECTOR))
        if (new_fat, new_difat) == (fat_sectors, difat_sectors):
            break
        fat_sectors, difat_sectors = new_fat, new_difat
    else:
        raise OleRebuildError("nao consegui dimensionar a FAT")

    # Numeracao dos setores, na ordem do layout.
    cur = 0
    fat_start = cur; cur += fat_sectors
    difat_start = cur; cur += difat_sectors
    dir_start = cur; cur += dir_sectors
    minifat_start = cur; cur += minifat_sectors
    ministream_start = cur; cur += ministream_sectors
    data_start = cur; cur += data_sectors

    fat = [FREESECT] * (fat_sectors * FAT_PER_SECTOR)

    def chain(start: int, count: int, mark: int = 0) -> None:
        """Link ``count`` consecutive sectors from ``start`` in the FAT."""
        for i in range(count):
            fat[start + i] = (mark if mark
                              else (start + i + 1 if i < count - 1
                                    else ENDOFCHAIN))

    chain(fat_start, fat_sectors, FATSECT)
    chain(difat_start, difat_sectors, DIFSECT)
    chain(dir_start, dir_sectors)
    chain(minifat_start, minifat_sectors)
    chain(ministream_start, ministream_sectors)

    # Cada stream normal ganha uma cadeia propria.
    stream_start: dict[int, int] = {}
    cursor = data_start
    for n in reg_nodes:
        count = _ceil_div(n.size, SECTOR_SIZE)
        stream_start[id(n)] = cursor
        chain(cursor, count)
        cursor += count

    # Mini FAT: uma cadeia por stream pequeno, em mini-setores.
    minifat = [FREESECT] * (minifat_sectors * FAT_PER_SECTOR)
    mini_start: dict[int, int] = {}
    mcursor = 0
    for n in mini_nodes:
        count = _ceil_div(n.size, MINI_SECTOR_SIZE)
        mini_start[id(n)] = mcursor
        for i in range(count):
            minifat[mcursor + i] = (mcursor + i + 1 if i < count - 1
                                    else ENDOFCHAIN)
        mcursor += count

    dir_bytes = _directory_bytes(
        nodes, ids, root_children, stream_start, mini_start,
        ministream_start if mini_sectors else ENDOFCHAIN, ministream_bytes,
    )

    with dst.open("wb") as fh:
        fh.write(_header_bytes(
            fat_sectors=fat_sectors, fat_start=fat_start,
            difat_sectors=difat_sectors, difat_start=difat_start,
            dir_start=dir_start,
            minifat_sectors=minifat_sectors,
            minifat_start=minifat_start if minifat_sectors else ENDOFCHAIN,
        ))
        _write_uint32_sectors(fh, fat)
        _write_difat_sectors(fh, fat_sectors, fat_start, difat_sectors)
        _write_padded(fh, dir_bytes, dir_sectors)
        _write_uint32_sectors(fh, minifat)
        _write_ministream(fh, mini_nodes, ministream_sectors)
        for n in reg_nodes:
            data = n.read() if n.read else b""
            if len(data) != n.size:
                raise OleRebuildError(
                    f"{n.name}: tamanho declarado {n.size}, lido {len(data)}")
            _write_padded(fh, data, _ceil_div(n.size, SECTOR_SIZE))
```

E os auxiliares de serialização:

```python
def _write_padded(fh, data: bytes, sectors: int) -> None:
    fh.write(data)
    pad = sectors * SECTOR_SIZE - len(data)
    if pad < 0:
        raise OleRebuildError("dados maiores que os setores reservados")
    if pad:
        fh.write(b"\x00" * pad)


def _write_uint32_sectors(fh, values: list[int]) -> None:
    if values:
        fh.write(struct.pack(f"<{len(values)}I", *values))


def _write_difat_sectors(fh, fat_sectors: int, fat_start: int,
                         difat_sectors: int) -> None:
    """DIFAT sectors hold FAT sector numbers 110 and up, 127 per sector."""
    if not difat_sectors:
        return
    remaining = [fat_start + i for i in range(109, fat_sectors)]
    for s in range(difat_sectors):
        chunk = remaining[s * DIFAT_PER_SECTOR:(s + 1) * DIFAT_PER_SECTOR]
        chunk = chunk + [FREESECT] * (DIFAT_PER_SECTOR - len(chunk))
        nxt = ENDOFCHAIN if s == difat_sectors - 1 else s + 1
        fh.write(struct.pack(f"<{DIFAT_PER_SECTOR}I", *chunk))
        fh.write(struct.pack("<I", nxt))


def _write_ministream(fh, mini_nodes: list[Entry], sectors: int) -> None:
    """The mini stream: every small stream, each padded to 64 bytes."""
    buf = bytearray()
    for n in mini_nodes:
        data = n.read() if n.read else b""
        if len(data) != n.size:
            raise OleRebuildError(
                f"{n.name}: tamanho declarado {n.size}, lido {len(data)}")
        buf += data
        pad = _ceil_div(len(data), MINI_SECTOR_SIZE) * MINI_SECTOR_SIZE - len(data)
        buf += b"\x00" * pad
    _write_padded(fh, bytes(buf), sectors)


def _dir_entry(name: str, etype: int, left: int, right: int, child: int,
               start: int, size: int) -> bytes:
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    if len(encoded) > 64:
        raise OleRebuildError(f"nome longo demais para o formato OLE: {name!r}")
    return b"".join((
        encoded.ljust(64, b"\x00"),
        struct.pack("<H", len(encoded)),
        struct.pack("<BB", etype, _COLOR_BLACK),
        struct.pack("<III", left, right, child),
        b"\x00" * 16,                    # CLSID
        struct.pack("<I", 0),            # state bits
        b"\x00" * 16,                    # creation + modified time
        struct.pack("<I", start),
        struct.pack("<Q", size),
    ))


def _directory_bytes(nodes, ids, root_children, stream_start, mini_start,
                     ministream_start: int, ministream_size: int) -> bytes:
    def child_of(children: list[Entry]) -> tuple[int, dict[int, tuple[int, int]]]:
        ordered = [ids[id(c)] for c in _sibling_order(children)]
        return _build_tree(ordered)

    root_child, links = child_of(root_children)
    all_links: dict[int, tuple[int, int]] = dict(links)
    child_id: dict[int, int] = {}
    for n in nodes:
        if n.is_storage:
            c, sub = child_of(n.children)
            child_id[ids[id(n)]] = c
            all_links.update(sub)

    out = [_dir_entry("Root Entry", _TYPE_ROOT, NOSTREAM, NOSTREAM,
                      root_child, ministream_start, ministream_size)]
    for n in nodes:
        i = ids[id(n)]
        left, right = all_links.get(i, (NOSTREAM, NOSTREAM))
        if n.is_storage:
            out.append(_dir_entry(n.name, _TYPE_STORAGE, left, right,
                                  child_id.get(i, NOSTREAM), 0, 0))
        elif n.size == 0:
            out.append(_dir_entry(n.name, _TYPE_STREAM, left, right,
                                  NOSTREAM, ENDOFCHAIN, 0))
        elif n.size < MINI_CUTOFF:
            out.append(_dir_entry(n.name, _TYPE_STREAM, left, right,
                                  NOSTREAM, mini_start[id(n)], n.size))
        else:
            out.append(_dir_entry(n.name, _TYPE_STREAM, left, right,
                                  NOSTREAM, stream_start[id(n)], n.size))
    return b"".join(out)


def _header_bytes(*, fat_sectors: int, fat_start: int, difat_sectors: int,
                  difat_start: int, dir_start: int, minifat_sectors: int,
                  minifat_start: int) -> bytes:
    difat_head = [fat_start + i for i in range(min(fat_sectors, 109))]
    difat_head += [FREESECT] * (109 - len(difat_head))
    return b"".join((
        _SIGNATURE,
        b"\x00" * 16,                       # CLSID
        struct.pack("<HH", 0x003E, 3),      # minor, major version
        struct.pack("<H", 0xFFFE),          # byte order, little endian
        struct.pack("<HH", 9, 6),           # sector shift 512, mini shift 64
        b"\x00" * 6,                        # reserved
        struct.pack("<I", 0),               # num directory sectors (v3: 0)
        struct.pack("<I", fat_sectors),
        struct.pack("<I", dir_start),
        struct.pack("<I", 0),               # transaction signature
        struct.pack("<I", MINI_CUTOFF),
        struct.pack("<I", minifat_start),
        struct.pack("<I", minifat_sectors),
        struct.pack("<I", difat_start if difat_sectors else ENDOFCHAIN),
        struct.pack("<I", difat_sectors),
        struct.pack("<109I", *difat_head),
    ))
```

E, por fim, o `rebuild` com a autoverificação:

```python
def _tree_from_ole(ole: olefile.OleFileIO,
                   replacements: dict[tuple[str, ...], bytes]) -> list[Entry]:
    """Mirror an open OLE as an Entry tree, applying replacements by path."""
    roots: list[Entry] = []
    index: dict[tuple[str, ...], Entry] = {}

    def ensure_storage(path: tuple[str, ...]) -> Entry:
        node = index.get(path)
        if node is not None:
            return node
        node = Entry(name=path[-1], is_storage=True, size=0, read=None)
        index[path] = node
        if len(path) == 1:
            roots.append(node)
        else:
            ensure_storage(path[:-1]).children.append(node)
        return node

    for raw in ole.listdir(streams=False, storages=True):
        ensure_storage(tuple(raw))

    for raw in ole.listdir(streams=True, storages=False):
        path = tuple(raw)
        if path in replacements:
            data = replacements[path]
            node = Entry(name=path[-1], is_storage=False, size=len(data),
                         read=lambda d=data: d)
        else:
            node = Entry(name=path[-1], is_storage=False,
                         size=ole.get_size(raw),
                         read=lambda p=raw: ole.openstream(p).read())
        index[path] = node
        if len(path) == 1:
            roots.append(node)
        else:
            ensure_storage(path[:-1]).children.append(node)
    return roots


def rebuild(src: Path, dst: Path,
            replacements: dict[tuple[str, ...], bytes]) -> None:
    """Write ``dst`` as ``src`` with ``replacements`` applied, any size.

    Verifies the result before returning. On any mismatch ``dst`` is removed
    and ``OleRebuildError`` is raised -- a half-right RDB is worse than none.
    """
    src, dst = Path(src), Path(dst)
    ole = olefile.OleFileIO(str(src))
    try:
        present = {tuple(e) for e in ole.listdir(streams=True, storages=False)}
        missing = [p for p in replacements if p not in present]
        if missing:
            raise OleRebuildError(
                "stream inexistente no RDB: "
                + ", ".join("/".join(p) for p in missing))
        write_ole(dst, _tree_from_ole(ole, replacements))
    finally:
        ole.close()

    try:
        _verify(src, dst, replacements)
    except Exception:
        dst.unlink(missing_ok=True)
        raise


def _verify(src: Path, dst: Path,
            replacements: dict[tuple[str, ...], bytes]) -> None:
    a = olefile.OleFileIO(str(src))
    b = olefile.OleFileIO(str(dst))
    try:
        ea = sorted(tuple(e) for e in a.listdir(streams=True, storages=True))
        eb = sorted(tuple(e) for e in b.listdir(streams=True, storages=True))
        if ea != eb:
            raise OleRebuildError(
                "a arvore do RDB reconstruido nao bate com a do original")
        for raw in a.listdir(streams=True, storages=False):
            path = tuple(raw)
            got = b.openstream(raw).read()
            want = replacements.get(path)
            if want is None:
                want = a.openstream(raw).read()
            if got != want:
                raise OleRebuildError(
                    f"stream divergente apos reconstrucao: {'/'.join(path)}")
    finally:
        a.close()
        b.close()
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_ole_rebuild.py -v`
Expected: PASS, 12 testes.

- [ ] **Step 5: Verificar contra um RDB de verdade**

Este é o passo que importa: reconstruir um RDB real sem nenhuma edição e conferir todos os streams.

```bash
.venv/bin/python -c "
from pathlib import Path
from sellib.parsers import ole_rebuild as ore
src = sorted(Path('rdbs').glob('*.rdb'))[0]
dst = Path('/tmp/rebuild-check.rdb')
print('origem:', src, src.stat().st_size, 'bytes')
ore.rebuild(src, dst, {})
print('saida :', dst, dst.stat().st_size, 'bytes -- verificado')
"
```

Expected: termina sem exceção. O tamanho pode diferir do original (o layout é nosso), mas cada stream foi conferido byte a byte pelo `_verify`.

- [ ] **Step 6: Commit**

```bash
git add sellib/parsers/ole_rebuild.py tests/test_ole_rebuild.py
git commit -m "Rebuild an RDB's OLE container so a stream can change size"
```

---

### Task 6: Modelo de edição por sessão

O estado guarda **diffs**, não documentos: `{relay: {sessão: {chave: valor}}}`. Assim sair do editor para outro relé e voltar preserva tudo, o export em lote recebe a entrada pronta, e nada de 17 MB por relé fica pendurado na sessão.

**Files:**
- Create: `sellib/web/dnp_map/__init__.py`
- Create: `sellib/web/dnp_map/model.py`
- Create: `tests/test_dnp_map_model.py`
- Modify: `sellib/paths.py`

**Interfaces:**
- Consumes: `set_dnp.parse`, `set_dnp.DnpSession`, `set_dnp.DnpRelay`.
- Produces:
  - `DnpMapState(rdbs: dict[str, RdbInfo], edits: dict[str, dict[str, dict[str, str]]])`
  - `edits_for(st, rdb_key, relay, session) -> dict[str, str]`
  - `apply_edits(parsed: SetDnpFile, edits: dict[str, str]) -> SetDnpFile`
  - `record_edits(st, lock, rdb_key, relay, session, changes: dict[str, str], original: SetDnpFile) -> int`
  - `swap(st, lock, rdb_key, relay, session, key_a, key_b, original) -> None`
  - `copy_session(st, lock, rdb_key, relay, src_session, target_sessions, parsed: dict[str, SetDnpFile]) -> int`
  - `dirty_summary(st, rdb_key) -> list[dict]`
  - `load_template(name: str) -> str`

- [ ] **Step 1: Acrescentar a constante de caminho**

Em `sellib/paths.py`, junto de `GLV_TEMPLATES_DIR`:

```python
# Templates HTML do editor de mapa DNP. Mesma razao do GLV: sao arquivos .html
# de verdade porque a maior parte e' JavaScript.
DNP_TEMPLATES_DIR: Path = PROJECT_ROOT / "sellib" / "web" / "dnp_map" / "templates"
```

- [ ] **Step 2: Escrever o teste que falha**

`tests/test_dnp_map_model.py`:

```python
"""Estado de edicao do editor de mapa DNP."""

from __future__ import annotations

import threading

from sellib.parsers import set_dnp
from sellib.web.dnp_map import model
from tests.test_set_dnp import SAMPLE_411L


def _fresh():
    return model.DnpMapState(), threading.RLock()


def test_recording_an_edit_stores_only_the_difference():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    n = model.record_edits(st, lock, "abc", "R1", "D1",
                           {"BI_1": "PSV22", "BI_2": "IN205"}, original)
    # BI_1 ja valia PSV22: nao e' uma edicao.
    assert n == 1
    assert model.edits_for(st, "abc", "R1", "D1") == {"BI_2": "IN205"}


def test_editing_back_to_the_original_clears_the_edit():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_2": "IN205"}, original)
    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_2": ""}, original)
    assert model.edits_for(st, "abc", "R1", "D1") == {}


def test_apply_edits_produces_the_edited_bytes():
    original = set_dnp.parse(SAMPLE_411L)
    edited = model.apply_edits(original, {"BI_2": "IN205"})
    assert b'BI_2,"IN205"\x1c\r\n' in edited.serialize()
    # O original nao foi mutado.
    assert b'BI_2,""\x1c\r\n' in original.serialize()


def test_apply_edits_ignores_a_key_the_file_does_not_have():
    original = set_dnp.parse(SAMPLE_411L)
    edited = model.apply_edits(original, {"BI_999": "IN205"})
    assert edited.serialize() == original.serialize()


def test_swap_exchanges_two_values():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    model.swap(st, lock, "abc", "R1", "D1", "BI_1", "BI_2", original)
    assert model.edits_for(st, "abc", "R1", "D1") == {
        "BI_1": "", "BI_2": "PSV22",
    }


def test_swap_uses_pending_edits_not_just_the_original():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_2": "IN205"}, original)
    model.swap(st, lock, "abc", "R1", "D1", "BI_1", "BI_2", original)
    assert model.edits_for(st, "abc", "R1", "D1") == {
        "BI_1": "IN205", "BI_2": "PSV22",
    }


def test_dirty_summary_lists_relays_with_pending_edits():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_2": "IN205"}, original)
    model.record_edits(st, lock, "abc", "R2", "D3", {"BI_1": "LOP"}, original)
    summary = model.dirty_summary(st, "abc")
    assert summary == [
        {"relay": "R1", "sessions": {"D1": 1}, "total": 1},
        {"relay": "R2", "sessions": {"D3": 1}, "total": 1},
    ]


def test_dirty_summary_is_empty_for_an_untouched_rdb():
    st, _ = _fresh()
    assert model.dirty_summary(st, "abc") == []
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_dnp_map_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sellib.web.dnp_map'`

- [ ] **Step 4: Implementar**

`sellib/web/dnp_map/__init__.py`:

```python
"""DNP Map Editor: edita os SET_D<n> de um RDB e reexporta o arquivo.

    model.py    estado de edicao por sessao (diffs, nao documentos)
    export.py   o export hibrido: write_stream quando cabe, rebuild quando nao
    handler.py  as rotas
    templates/  landing.html e editor.html

O parser dos SET_D mora em `sellib.parsers.set_dnp` e o writer de OLE em
`sellib.parsers.ole_rebuild`: nenhum dos dois sabe que existe web.
"""

from __future__ import annotations

from sellib.paths import DNP_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Le um template do editor. Lido no import, como no GLV."""
    return (DNP_TEMPLATES_DIR / name).read_text(encoding="utf-8")
```

`sellib/web/dnp_map/model.py`:

```python
"""Per-session edit state for the DNP map editor.

The state holds DIFFS, never documents: ``{relay: {session: {key: value}}}``.
A relay's SET_D is ~17 KB and a visitor may touch a dozen relays before
exporting; keeping parsed copies around would pin megabytes per session for
edits that are a handful of strings. It also means leaving the editor and
coming back loses nothing, and the batch export gets its input already shaped.

An edit that restores the original value is not an edit -- it is removed. That
keeps "alteracoes pendentes" honest: a relay someone typed in and undid does
not show up as dirty.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional

from sellib.parsers.rdb import RdbInfo
from sellib.parsers.set_dnp import SetDnpFile


@dataclass
class DnpMapState:
    """What one visitor has open and has changed."""

    # sha curto -> RdbInfo, como no settings_compare
    rdbs: dict[str, RdbInfo] = field(default_factory=dict)
    # rdb_key -> relay -> session -> {key: value}
    edits: dict[str, dict[str, dict[str, dict[str, str]]]] = field(
        default_factory=dict)


def edits_for(st: DnpMapState, rdb_key: str, relay: str,
              session: str) -> dict[str, str]:
    return dict(st.edits.get(rdb_key, {}).get(relay, {}).get(session, {}))


def apply_edits(parsed: SetDnpFile, edits: dict[str, str]) -> SetDnpFile:
    """A copy of ``parsed`` with ``edits`` applied. Unknown keys are skipped.

    Skipping instead of raising is deliberate here (unlike ``set_value``): the
    edits come from a browser and may name a key from a session the visitor has
    since navigated away from. Dropping one is right; refusing the export is
    not.
    """
    out = copy.deepcopy(parsed)
    for key, value in edits.items():
        try:
            out.set_value(key, value)
        except KeyError:
            continue
    return out


def _bucket(st: DnpMapState, rdb_key: str, relay: str,
            session: str) -> dict[str, str]:
    return (st.edits.setdefault(rdb_key, {})
                    .setdefault(relay, {})
                    .setdefault(session, {}))


def _prune(st: DnpMapState, rdb_key: str, relay: str, session: str) -> None:
    """Drop empty levels so ``dirty_summary`` never reports a clean relay."""
    by_relay = st.edits.get(rdb_key)
    if not by_relay:
        return
    by_session = by_relay.get(relay)
    if by_session is None:
        return
    if not by_session.get(session):
        by_session.pop(session, None)
    if not by_session:
        by_relay.pop(relay, None)
    if not by_relay:
        st.edits.pop(rdb_key, None)


def record_edits(st: DnpMapState, lock, rdb_key: str, relay: str, session: str,
                 changes: dict[str, str], original: SetDnpFile) -> int:
    """Store ``changes`` as diffs against ``original``. Returns how many stuck."""
    stored = 0
    with lock:
        bucket = _bucket(st, rdb_key, relay, session)
        for key, value in changes.items():
            try:
                base = original.get_value(key)
            except KeyError:
                continue
            if value == base:
                bucket.pop(key, None)
            else:
                bucket[key] = value
                stored += 1
        _prune(st, rdb_key, relay, session)
    return stored


def current_value(st: DnpMapState, rdb_key: str, relay: str, session: str,
                  key: str, original: SetDnpFile) -> str:
    """What the field shows now: the pending edit, else the original."""
    pending = st.edits.get(rdb_key, {}).get(relay, {}).get(session, {})
    if key in pending:
        return pending[key]
    return original.get_value(key)


def swap(st: DnpMapState, lock, rdb_key: str, relay: str, session: str,
         key_a: str, key_b: str, original: SetDnpFile) -> None:
    """Exchange two points' values.

    Swap and not insert-and-shift: a DNP index is a contract with the SCADA
    master, and inserting would silently renumber every point in between.
    """
    a = current_value(st, rdb_key, relay, session, key_a, original)
    b = current_value(st, rdb_key, relay, session, key_b, original)
    record_edits(st, lock, rdb_key, relay, session,
                 {key_a: b, key_b: a}, original)


def copy_session(st: DnpMapState, lock, rdb_key: str, relay: str,
                 src_session: str, target_sessions: list[str],
                 parsed: dict[str, SetDnpFile]) -> int:
    """Make the target sessions read like ``src_session`` reads now.

    ``parsed`` maps session name to its original parsed file. The result is
    recorded as diffs against each target's own original, so a target that
    already matched gains no edits at all.
    """
    src = parsed[src_session]
    src_edits = edits_for(st, rdb_key, relay, src_session)
    wanted = {line.key: (src_edits.get(line.key, line.value))
              for line in src.lines if line.key}

    touched = 0
    for name in target_sessions:
        target = parsed.get(name)
        if target is None or name == src_session:
            continue
        touched += record_edits(st, lock, rdb_key, relay, name,
                                wanted, target)
    return touched


def dirty_summary(st: DnpMapState, rdb_key: str) -> list[dict]:
    """Relays with pending edits, for the 'alteracoes pendentes' panel."""
    out: list[dict] = []
    for relay in sorted(st.edits.get(rdb_key, {})):
        sessions = st.edits[rdb_key][relay]
        counts = {name: len(sessions[name]) for name in sorted(sessions)
                  if sessions[name]}
        if counts:
            out.append({
                "relay": relay,
                "sessions": counts,
                "total": sum(counts.values()),
            })
    return out
```

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_dnp_map_model.py -v`
Expected: PASS, 8 testes.

- [ ] **Step 6: Commit**

```bash
git add sellib/paths.py sellib/web/dnp_map/__init__.py sellib/web/dnp_map/model.py tests/test_dnp_map_model.py
git commit -m "Keep DNP map edits as per-session diffs, not documents"
```

---

### Task 7: Export híbrido

Regra sem aposta: **tamanho exato ou reconstrução**. Nada de encher com espaços torcendo para o QuickSet aceitar.

**Files:**
- Create: `sellib/web/dnp_map/export.py`
- Create: `tests/test_dnp_map_export.py`

**Interfaces:**
- Consumes: `ole_rebuild.rebuild`, `set_dnp.discover/parse`, `model.apply_edits`.
- Produces:
  - `ExportResult(ok: bool, path: Path | None, method: str, streams: int, error: str)` — `method` é `"in-place"` ou `"rebuild"`
  - `build_streams(extract_dir, edits) -> dict[tuple[str, ...], bytes]`
  - `export(rdb_path, extract_dir, edits, out_path, job=None) -> ExportResult`
  - `export_txt(extract_dir, edits, out_dir) -> list[Path]`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_dnp_map_export.py`:

```python
"""Escolha do caminho de export e o resultado de cada um."""

from __future__ import annotations

import olefile

from sellib.parsers import ole_rebuild as ore
from sellib.parsers import set_dnp
from sellib.web.dnp_map import export as exp
from tests.test_set_dnp import SAMPLE_411L


def _make_rdb(tmp_path):
    """Um RDB de mentira com um rele e duas sessoes, e a extracao dele."""
    rdb = tmp_path / "obra.rdb"
    ore.write_ole(rdb, [
        ore.Entry(name="Relays", is_storage=True, size=0, read=None, children=[
            ore.Entry(name="R1", is_storage=True, size=0, read=None, children=[
                ore.Entry(name="SET_D1.TXT", is_storage=False,
                          size=len(SAMPLE_411L), read=lambda: SAMPLE_411L),
                ore.Entry(name="SET_D2.TXT", is_storage=False,
                          size=len(SAMPLE_411L), read=lambda: SAMPLE_411L),
            ]),
        ]),
    ])
    extract = tmp_path / "extracted"
    (extract / "Relays" / "R1").mkdir(parents=True)
    (extract / "Relays" / "R1" / "SET_D1.TXT").write_bytes(SAMPLE_411L)
    (extract / "Relays" / "R1" / "SET_D2.TXT").write_bytes(SAMPLE_411L)
    return rdb, extract


def test_a_same_length_edit_takes_the_in_place_path(tmp_path):
    rdb, extract = _make_rdb(tmp_path)
    out = tmp_path / "out.rdb"
    # "PSV22" -> "PSV23": mesmo numero de bytes.
    r = exp.export(rdb, extract, {"R1": {"D1": {"BI_1": "PSV23"}}}, out)
    assert r.ok
    assert r.method == "in-place"
    ole = olefile.OleFileIO(str(out))
    try:
        got = ole.openstream(["Relays", "R1", "SET_D1.TXT"]).read()
    finally:
        ole.close()
    assert b'BI_1,"PSV23"\x1c\r\n' in got
    assert len(got) == len(SAMPLE_411L)


def test_a_longer_edit_takes_the_rebuild_path(tmp_path):
    rdb, extract = _make_rdb(tmp_path)
    out = tmp_path / "out.rdb"
    # "" -> "IN205": cresce cinco bytes, nao cabe.
    r = exp.export(rdb, extract, {"R1": {"D1": {"BI_2": "IN205"}}}, out)
    assert r.ok
    assert r.method == "rebuild"
    ole = olefile.OleFileIO(str(out))
    try:
        got = ole.openstream(["Relays", "R1", "SET_D1.TXT"]).read()
        untouched = ole.openstream(["Relays", "R1", "SET_D2.TXT"]).read()
    finally:
        ole.close()
    assert b'BI_2,"IN205"\x1c\r\n' in got
    assert untouched == SAMPLE_411L


def test_a_single_growing_stream_forces_rebuild_for_all_of_them(tmp_path):
    rdb, extract = _make_rdb(tmp_path)
    out = tmp_path / "out.rdb"
    r = exp.export(rdb, extract, {"R1": {
        "D1": {"BI_1": "PSV23"},     # cabe
        "D2": {"BI_2": "IN205"},     # nao cabe
    }}, out)
    assert r.ok
    assert r.method == "rebuild"
    assert r.streams == 2


def test_no_edits_is_refused(tmp_path):
    rdb, extract = _make_rdb(tmp_path)
    r = exp.export(rdb, extract, {}, tmp_path / "out.rdb")
    assert not r.ok
    assert "nenhuma altera" in r.error.lower()


def test_build_streams_returns_the_ole_path_of_each_edited_session(tmp_path):
    _rdb, extract = _make_rdb(tmp_path)
    streams = exp.build_streams(extract, {"R1": {"D1": {"BI_2": "IN205"}}})
    assert list(streams) == [("Relays", "R1", "SET_D1.TXT")]


def test_export_txt_writes_one_file_per_edited_session(tmp_path):
    _rdb, extract = _make_rdb(tmp_path)
    out_dir = tmp_path / "txt"
    paths = exp.export_txt(extract, {"R1": {
        "D1": {"BI_2": "IN205"}, "D2": {"BI_1": "LOP"},
    }}, out_dir)
    assert sorted(p.name for p in paths) == [
        "R1_SET_D1.TXT", "R1_SET_D2.TXT",
    ]
    assert b'BI_2,"IN205"' in (out_dir / "R1_SET_D1.TXT").read_bytes()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_dnp_map_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sellib.web.dnp_map.export'`

- [ ] **Step 3: Implementar**

`sellib/web/dnp_map/export.py`:

```python
"""Write an RDB back out with the pending DNP map edits applied.

Two paths, chosen by one blunt rule -- exact size or rebuild:

1. Every edited stream serializes to exactly the byte count it had. Copy the
   RDB and ``olefile.write_stream`` each one. The output is byte-identical
   outside the streams we touched, and this is the path already proven by
   ``vb_updater``. Pure rearrangement inside a block always lands here, since
   moving values conserves the byte multiset.
2. Anything changed size. Rebuild the whole container via
   ``sellib.parsers.ole_rebuild``, which verifies its own output.

There is deliberately no third path that pads a settings file with whitespace
until it fits. Nobody here knows what AcSELerator QuickSet tolerates, and a
guess about that would be a guess about a protection relay's settings.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import olefile

from sellib.parsers import ole_rebuild, set_dnp
from sellib.web.dnp_map.model import apply_edits

_logger = logging.getLogger(__name__)


@dataclass
class ExportResult:
    ok: bool
    path: Optional[Path] = None
    method: str = ""            # "in-place" | "rebuild"
    streams: int = 0
    error: str = ""


def _sessions_index(extract_dir: Path) -> dict[str, dict[str, set_dnp.DnpSession]]:
    return {
        relay.name: {s.name: s for s in relay.sessions}
        for relay in set_dnp.discover(extract_dir)
    }


def build_streams(extract_dir: Path,
                  edits: dict) -> dict[tuple[str, ...], bytes]:
    """The new bytes of every edited session, keyed by its path in the OLE."""
    index = _sessions_index(Path(extract_dir))
    out: dict[tuple[str, ...], bytes] = {}
    for relay, by_session in sorted(edits.items()):
        for session, changes in sorted(by_session.items()):
            if not changes:
                continue
            found = index.get(relay, {}).get(session)
            if found is None:
                _logger.warning("[dnp-map] sessao sumiu da extracao: %s/%s",
                                relay, session)
                continue
            parsed = set_dnp.parse(found.fs_path.read_bytes())
            out[found.stream_parts] = apply_edits(parsed, changes).serialize()
    return out


def export(rdb_path: Path, extract_dir: Path, edits: dict, out_path: Path,
           job=None) -> ExportResult:
    """Produce ``out_path``: the RDB with every pending edit applied."""
    rdb_path, out_path = Path(rdb_path), Path(out_path)
    streams = build_streams(extract_dir, edits)
    if not streams:
        return ExportResult(ok=False, error="Nenhuma alteração pendente para exportar.")

    if job:
        job.stage("Conferindo os mapas alterados", 10)

    ole = olefile.OleFileIO(str(rdb_path))
    try:
        missing = [p for p in streams if not ole.exists(list(p))]
        if missing:
            return ExportResult(
                ok=False,
                error="Stream não encontrado no RDB: "
                      + ", ".join("/".join(p) for p in missing))
        fits = all(ole.get_size(list(p)) == len(data)
                   for p, data in streams.items())
    finally:
        ole.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if fits:
            if job:
                job.stage("Copiando o RDB", 30)
            shutil.copyfile(rdb_path, out_path)
            if job:
                job.stage("Gravando os mapas", 70)
            handle = olefile.OleFileIO(str(out_path), write_mode=True)
            try:
                for parts, data in streams.items():
                    handle.write_stream(list(parts), data)
            finally:
                handle.close()
            method = "in-place"
        else:
            if job:
                job.stage("Reconstruindo o RDB", 40)
            ole_rebuild.rebuild(rdb_path, out_path, streams)
            method = "rebuild"
    except ole_rebuild.OleRebuildError as e:
        out_path.unlink(missing_ok=True)
        return ExportResult(ok=False, error=str(e))
    except Exception as e:                       # olefile, disco cheio, etc.
        out_path.unlink(missing_ok=True)
        _logger.exception("[dnp-map] falha no export")
        return ExportResult(ok=False, error=f"Falha ao gravar o RDB: {e}")

    if job:
        job.stage("Verificando", 95)
    return ExportResult(ok=True, path=out_path, method=method,
                        streams=len(streams))


def export_txt(extract_dir: Path, edits: dict, out_dir: Path) -> list[Path]:
    """Write the edited SET_D files on their own.

    The plan B: if QuickSet ever refuses a rebuilt RDB, these still import.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = _sessions_index(Path(extract_dir))
    written: list[Path] = []
    for relay, by_session in sorted(edits.items()):
        for session, changes in sorted(by_session.items()):
            found = index.get(relay, {}).get(session)
            if found is None or not changes:
                continue
            parsed = set_dnp.parse(found.fs_path.read_bytes())
            target = out_dir / f"{relay}_{found.fs_path.name}"
            target.write_bytes(apply_edits(parsed, changes).serialize())
            written.append(target)
    return written
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: PASS, todos.

- [ ] **Step 5: Commit**

```bash
git add sellib/web/dnp_map/export.py tests/test_dnp_map_export.py
git commit -m "Export the RDB in place when the map fits, rebuild when it grows"
```

---

### Task 8: Rotas e a landing page

**Files:**
- Create: `sellib/web/dnp_map/handler.py`
- Create: `sellib/web/dnp_map/templates/landing.html`

**Interfaces:**
- Consumes: tudo das tasks 1-7.
- Produces: `build_dnp_map_handler(logger, sessions) -> type`

Rotas, absolutas (o dispatcher tira o prefixo):

| Método | Rota | Devolve |
|---|---|---|
| GET | `/` | landing.html |
| POST | `/upload` | `{ok, rdb, relays:[...]}` |
| GET | `/relays?rdb=` | `{relays:[{name, relaytype, sessions:[...], groups:[[...]]}]}` |
| GET | `/editor?rdb=&relay=&d=` | editor.html |
| GET | `/map?rdb=&relay=&d=` | `{blocks, extras, warnings, wordbits_ok, groups, dirty}` |
| POST | `/edit` | `{ok, stored, dirty}` |
| POST | `/swap` | `{ok, dirty}` |
| POST | `/copy-session` | `{ok, touched}` |
| POST | `/export` | `{ok, download_url, txt_urls, method}` |
| GET | `/download?f=` | o arquivo, sandboxed em `self.sdir("out")` |

- [ ] **Step 1: Escrever a landing**

`sellib/web/dnp_map/templates/landing.html` — copie a estrutura de `sellib/web/glv/templates/landing.html` para o `<head>`, o `<header>` e o `<style>`, trocando o conteúdo. Regras que valem aqui:

- `<!--NAV:dnp-map-->` logo dentro do `<header>`; o `mount.py:_resolve_markup()` o troca pela navegação da direção ativa.
- Nada de cor/raio/fonte literal: só `var(--surface)`, `var(--border)`, `var(--text)`, `var(--s3)`, `var(--radius)`, `var(--sans)`.
- Upload via `SelProgress.upload(url, file, opts)`, nunca `fetch`. Duas coisas que o `vb_updater` já fixou como convenção e que é fácil errar: o nome do arquivo viaja no header **`X-Filename`**, com `encodeURIComponent`, e o retorno é **`{ok, status, data}`** — a resposta do servidor está em `r.data`, não em `r`. O mesmo vale para `SelProgress.post`.

Conteúdo:

```html
<h1>Editor de Mapa DNP</h1>
<p class="lede">
  Edite os pontos DNP3 dos relés de um RDB e gere um RDB novo com as
  alterações aplicadas.
</p>

<section class="card">
  <h2>1. Envie o RDB</h2>
  <input type="file" id="rdb" accept=".rdb">
  <button id="enviar">Enviar</button>
  <p class="hint">O arquivo pode ter de 40 a 140 MB; a barra no topo mostra o progresso.</p>
</section>

<section class="card" id="passo-reles" hidden>
  <h2>2. Escolha o relé</h2>
  <p class="hint" id="resumo"></p>
  <table id="reles">
    <thead><tr><th>Relé</th><th>Modelo</th><th>Sessões DNP</th><th></th></tr></thead>
    <tbody></tbody>
  </table>
</section>

<section class="card" id="pendentes" hidden>
  <h2>Alterações pendentes</h2>
  <ul id="lista-pendentes"></ul>
  <button id="exportar">Exportar RDB</button>
  <p id="saida"></p>
</section>
```

O JavaScript, ao fim do arquivo:

```html
<script>
let RDB = null;

document.getElementById('enviar').onclick = async () => {
  const f = document.getElementById('rdb').files[0];
  if (!f) { alert('Escolha um arquivo .rdb'); return; }
  const r = await SelProgress.upload('/upload', f, {
    headers: {'X-Filename': encodeURIComponent(f.name)},
    label: 'Enviando RDB',
    doneLabel: 'RDB carregado.',
  });
  if (!r.ok) { alert((r.data && r.data.error) || 'Falha no envio'); return; }
  RDB = r.data.rdb;
  mostrarReles(r.data.relays);
  await atualizarPendentes();
};

function mostrarReles(relays) {
  const tb = document.querySelector('#reles tbody');
  tb.innerHTML = '';
  document.getElementById('resumo').textContent =
    relays.length + ' relé(s) com mapa DNP neste RDB.';
  for (const r of relays) {
    const tr = document.createElement('tr');
    const grupos = r.groups.map(g => g.join('=')).join(' · ');
    tr.innerHTML =
      '<td>' + r.name + '</td>' +
      '<td>' + (r.relaytype || '—') + '</td>' +
      '<td>' + grupos + '</td>' +
      '<td><a href="./editor?rdb=' + encodeURIComponent(RDB) +
      '&relay=' + encodeURIComponent(r.name) +
      '&d=' + encodeURIComponent(r.sessions[0]) + '">Editar</a></td>';
    tb.appendChild(tr);
  }
  document.getElementById('passo-reles').hidden = false;
}

async function atualizarPendentes() {
  if (!RDB) return;
  const r = await (await fetch('/relays?rdb=' + encodeURIComponent(RDB))).json();
  const ul = document.getElementById('lista-pendentes');
  ul.innerHTML = '';
  for (const d of (r.dirty || [])) {
    const li = document.createElement('li');
    const ses = Object.entries(d.sessions)
      .map(([k, v]) => k + ' (' + v + ')').join(', ');
    li.textContent = d.relay + ' — ' + ses;
    ul.appendChild(li);
  }
  document.getElementById('pendentes').hidden = (r.dirty || []).length === 0;
}

document.getElementById('exportar').onclick = async () => {
  const r = await SelProgress.post('/export', {rdb: RDB},
                                   {label: 'Exportando RDB'});
  const out = document.getElementById('saida');
  const d = r.data || {};
  if (!r.ok) { out.textContent = 'Falha: ' + (d.error || r.status); return; }
  const links = [
    '<a href="' + d.download_url + '" download>Baixar RDB</a>',
  ].concat((d.txt_urls || []).map(
    u => '<a href="' + u.url + '" download>' + u.name + '</a>'));
  out.innerHTML = 'Pronto (' +
    (d.method === 'rebuild' ? 'RDB reconstruído' : 'gravado no lugar') +
    '): ' + links.join(' · ');
};

atualizarPendentes();
</script>
```

Nota: os `<a href>` de download recebem o prefixo do servidor (`download_url` já vem com `self.mount_prefix`), porque o shim de `fetch` não alcança navegação direta. O link `./editor?...` é relativo de propósito, pela mesma razão.

- [ ] **Step 2: Escrever o handler**

`sellib/web/dnp_map/handler.py`:

```python
"""Rotas do editor de mapa DNP.

Rotas absolutas, como as demais ferramentas: o dispatcher de `mount.py` tira o
prefixo antes de delegar, e o shim reescreve os `fetch` do cliente. As duas
excecoes que o shim nao alcanca -- `download_url` e o link pra outra pagina --
levam `self.mount_prefix` na mao e `./` respectivamente.
"""

from __future__ import annotations

import json
import logging
from html import escape
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

from sellib.core import wordbits
from sellib.parsers import rdb as rdb_loader
from sellib.parsers import set_dnp
from sellib.paths import is_within
from sellib.web.dnp_map import load_template, model
from sellib.web.dnp_map import export as exporter
from sellib.web.session import SessionHandler

_logger = logging.getLogger(__name__)

_RDB_MAX_BYTES = 500 * 1024 * 1024

LANDING_HTML = load_template("landing.html")
EDITOR_HTML = load_template("editor.html")


def _short_sha(s: str) -> str:
    return s[:12]


def build_dnp_map_handler(logger: logging.Logger, sessions) -> type:
    """Devolve a classe de handler do editor de mapa DNP.

    Nao sobe servidor: quem serve e' o dispatcher unico de `sellib.web.mount`,
    que monta esse handler em `/dnp-map/`.
    """

    class Handler(SessionHandler):
        session_key = "dnp-map"
        state_factory = model.DnpMapState
        server_sessions = sessions

        # -- helpers ----------------------------------------------------

        def _query(self) -> dict:
            return {k: v[0] for k, v in
                    parse_qs(urlparse(self.path).query).items()}

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                return {}

        def _rdb(self, key: str):
            st = self.sess()
            info = st.rdbs.get(key)
            if info is None:
                self._send_json(404, {"ok": False,
                                      "error": "RDB não está nesta sessão."})
                return None
            return info

        def _relay_payload(self, info) -> list[dict]:
            out = []
            for r in set_dnp.discover(info.extract_dir):
                out.append({
                    "name": r.name,
                    "relaytype": r.relaytype,
                    "sessions": [s.name for s in r.sessions],
                    "groups": set_dnp.identical_groups(r),
                })
            return out

        def _parsed_sessions(self, info, relay_name: str):
            for r in set_dnp.discover(info.extract_dir):
                if r.name == relay_name:
                    return r, {s.name: set_dnp.parse(s.fs_path.read_bytes())
                               for s in r.sessions}
            return None, {}

        # -- GET --------------------------------------------------------

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", ""):
                self._send(200, LANDING_HTML, "text/html; charset=utf-8")
                return
            if path == "/editor":
                self._send(200, EDITOR_HTML, "text/html; charset=utf-8")
                return
            if path == "/relays":
                self._serve_relays()
                return
            if path == "/map":
                self._serve_map()
                return
            if path == "/download":
                self._serve_download()
                return
            self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        def _serve_relays(self):
            q = self._query()
            info = self._rdb(q.get("rdb", ""))
            if info is None:
                return
            self._send_json(200, {
                "ok": True,
                "relays": self._relay_payload(info),
                "dirty": model.dirty_summary(self.sess(), q.get("rdb", "")),
            })

        def _serve_map(self):
            q = self._query()
            key = q.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            relay_name = q.get("relay", "")
            session_name = q.get("d", "")
            relay, parsed = self._parsed_sessions(info, relay_name)
            if relay is None or session_name not in parsed:
                self._send_json(404, {"ok": False,
                                      "error": "Relé ou sessão inexistente."})
                return

            st = self.sess()
            f = parsed[session_name]
            pending = model.edits_for(st, key, relay_name, session_name)
            wbs = wordbits.lookup(relay.relaytype)
            always = wbs.always_valid if wbs else set()

            blocks = {}
            for kind, points in f.blocks().items():
                values = [pending.get(p.key, p.value) for p in points]
                dups = wordbits.duplicates(values, always)
                rows = []
                for p, value in zip(points, values):
                    aviso = ""
                    if wbs is not None:
                        if wbs.check(value) == "desconhecido":
                            aviso = "desconhecido"
                        elif value.strip().upper() in dups:
                            aviso = "duplicado"
                    rows.append({
                        "key": p.key, "index": p.index, "value": value,
                        "sca_key": p.sca_key,
                        "sca": pending.get(p.sca_key or "", p.sca),
                        "dbd_key": p.dbd_key,
                        "dbd": pending.get(p.dbd_key or "", p.dbd),
                        "aviso": aviso,
                    })
                blocks[kind] = rows

            self._send_json(200, {
                "ok": True,
                "relay": relay_name,
                "relaytype": relay.relaytype,
                "session": session_name,
                "sessions": [s.name for s in relay.sessions],
                "groups": set_dnp.identical_groups(relay),
                "blocks": blocks,
                "extras": [{"key": k, "value": v} for k, v in f.extras()],
                "wordbits_ok": wbs is not None,
                "dirty": model.dirty_summary(st, key),
            })

        def _serve_download(self):
            q = self._query()
            target = Path(unquote(q.get("f", "")))
            if not is_within(target, [self.sdir("out")]) or not target.is_file():
                self._send(403, "Proibido", "text/plain; charset=utf-8")
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{target.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # -- POST -------------------------------------------------------

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/upload":
                self._do_upload()
            elif path == "/edit":
                self._do_edit()
            elif path == "/swap":
                self._do_swap()
            elif path == "/copy-session":
                self._do_copy_session()
            elif path == "/export":
                self._do_export()
            else:
                self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        def _do_upload(self):
            job = self.job()
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > _RDB_MAX_BYTES:
                job.fail("Arquivo vazio ou grande demais.")
                self._send_json(400, {"ok": False,
                                      "error": "Arquivo vazio ou grande demais."})
                return
            data = self.rfile.read(length)
            # Convencao do vb_updater: o nome vai em X-Filename, urlencoded.
            filename = unquote(self.headers.get("X-Filename") or "upload.rdb")
            try:
                info = rdb_loader.process_upload(
                    data, filename,
                    on_progress=lambda d, t, s: job.fraction(s, d, t))
            except Exception as e:
                logger.exception("[dnp-map] falha ao processar RDB")
                job.fail(str(e))
                self._send_json(400, {"ok": False, "error": str(e)})
                return

            key = _short_sha(info.sha256)
            st = self.sess()
            with self.session.lock:
                st.rdbs[key] = info
            job.finish("RDB carregado")
            self._send_json(200, {
                "ok": True, "rdb": key, "name": info.display_name,
                "relays": self._relay_payload(info),
            })

        def _do_edit(self):
            body = self._body()
            key = body.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            relay_name = body.get("relay", "")
            session_name = body.get("session", "")
            _relay, parsed = self._parsed_sessions(info, relay_name)
            if session_name not in parsed:
                self._send_json(404, {"ok": False,
                                      "error": "Sessão inexistente."})
                return
            stored = model.record_edits(
                self.sess(), self.session.lock, key, relay_name, session_name,
                {str(k): str(v) for k, v in (body.get("changes") or {}).items()},
                parsed[session_name])
            self._send_json(200, {
                "ok": True, "stored": stored,
                "dirty": model.dirty_summary(self.sess(), key),
            })

        def _do_swap(self):
            body = self._body()
            key = body.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            relay_name = body.get("relay", "")
            session_name = body.get("session", "")
            _relay, parsed = self._parsed_sessions(info, relay_name)
            if session_name not in parsed:
                self._send_json(404, {"ok": False,
                                      "error": "Sessão inexistente."})
                return
            model.swap(self.sess(), self.session.lock, key, relay_name,
                       session_name, str(body.get("a", "")),
                       str(body.get("b", "")), parsed[session_name])
            self._send_json(200, {
                "ok": True,
                "dirty": model.dirty_summary(self.sess(), key),
            })

        def _do_copy_session(self):
            body = self._body()
            key = body.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            relay_name = body.get("relay", "")
            src = body.get("session", "")
            _relay, parsed = self._parsed_sessions(info, relay_name)
            if src not in parsed:
                self._send_json(404, {"ok": False,
                                      "error": "Sessão inexistente."})
                return
            targets = [s for s in parsed if s != src]
            touched = model.copy_session(self.sess(), self.session.lock, key,
                                         relay_name, src, targets, parsed)
            self._send_json(200, {"ok": True, "touched": touched,
                                  "sessions": targets})

        def _do_export(self):
            job = self.job()
            body = self._body()
            key = body.get("rdb", "")
            info = self._rdb(key)
            if info is None:
                return
            st = self.sess()
            edits = st.edits.get(key, {})
            out_dir = self.sdir("out")
            stem = Path(info.display_name).stem
            out_path = out_dir / f"{stem}_dnp_updated.rdb"

            result = exporter.export(info.rdb_path, info.extract_dir, edits,
                                     out_path, job=job)
            if not result.ok:
                job.fail(result.error)
                self._send_json(400, {"ok": False, "error": result.error})
                return

            txt_dir = out_dir / "txt"
            txts = exporter.export_txt(info.extract_dir, edits, txt_dir)
            job.finish("Exportado")
            self._send_json(200, {
                "ok": True,
                "method": result.method,
                "streams": result.streams,
                "download_url": self.mount_prefix + "/download?f="
                                + quote(str(out_path)),
                "txt_urls": [
                    {"name": p.name,
                     "url": self.mount_prefix + "/download?f=" + quote(str(p))}
                    for p in txts
                ],
            })

    return Handler
```

- [ ] **Step 3: Verificar que o módulo importa**

Run: `.venv/bin/python -c "import logging; from sellib.web.dnp_map.handler import build_dnp_map_handler; print('ok')"`
Expected: falha em `load_template("editor.html")` — o template da Task 9 ainda não existe. Crie um marcador vazio para destravar:

```bash
printf '<!doctype html>\n<html><head><title>Editor</title></head><body>\n<header><!--NAV:dnp-map--></header>\n</body></html>\n' > sellib/web/dnp_map/templates/editor.html
```

Rode de novo. Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add sellib/web/dnp_map/handler.py sellib/web/dnp_map/templates/
git commit -m "Serve the DNP map editor's routes and its landing page"
```

---

### Task 9: A tabela

**Files:**
- Modify: `sellib/web/dnp_map/templates/editor.html`

- [ ] **Step 1: Escrever a página**

Estrutura, dentro do esqueleto que o Step 3 da Task 8 criou:

```html
<header><!--NAV:dnp-map--></header>

<h1 id="titulo">Mapa DNP</h1>

<section class="barra">
  <span id="modelo"></span>
  <label>Sessão
    <select id="sessao"></select>
  </label>
  <span id="grupos" class="hint"></span>
  <button id="copiar">Aplicar às demais sessões</button>
  <a id="voltar" href="./">← Relés</a>
</section>

<p id="sem-wordbits" class="aviso" hidden>
  Sem lista de word bits para este modelo: a validação está desligada.
</p>

<section id="extras" class="card"></section>

<nav id="abas" class="abas"></nav>

<table id="pontos">
  <thead><tr id="cabecalho"></tr></thead>
  <tbody></tbody>
</table>

<p id="rodape" class="hint"></p>
```

Estilo — só tokens, nada literal:

```html
<style>
  .barra { display:flex; gap:var(--s3); align-items:center;
           flex-wrap:wrap; margin-bottom:var(--s3); }
  .abas { display:flex; gap:var(--s2); margin:var(--s3) 0; }
  .abas button[aria-selected="true"] { border-bottom:2px solid var(--text); }
  #pontos { width:100%; border-collapse:collapse; font-family:var(--sans); }
  #pontos th, #pontos td { border-bottom:1px solid var(--border);
                           padding:var(--s2); text-align:left; }
  #pontos tr.arrastando { opacity:.4; }
  #pontos tr.alvo td { outline:2px dashed var(--border); }
  #pontos input { width:100%; background:var(--surface); color:var(--text);
                  border:1px solid var(--border); border-radius:var(--radius);
                  padding:var(--s1); font-family:var(--sans); }
  #pontos td.aviso-desconhecido input { border-color:var(--warn); }
  #pontos td.aviso-duplicado input { border-style:dashed; }
  .puxador { cursor:grab; user-select:none; }
</style>
```

Se `--warn` ainda não existir em `sellib/web/themes/tokens.py`, acrescente-o ao `_TOKEN_CSS` e às três direções (`folha.py`, `regua.py`, `caderno.py`) — o módulo levanta no boot se um tema não define um nome, então não dá para esquecer nenhum.

O JavaScript:

```html
<script>
const P = new URLSearchParams(location.search);
const RDB = P.get('rdb'), RELAY = P.get('relay');
let SESSAO = P.get('d'), DADOS = null, ABA = null, ARRASTANDO = null;

async function carregar() {
  const r = await (await fetch('/map?rdb=' + encodeURIComponent(RDB) +
    '&relay=' + encodeURIComponent(RELAY) +
    '&d=' + encodeURIComponent(SESSAO))).json();
  if (!r.ok) { alert(r.error); return; }
  DADOS = r;
  document.getElementById('titulo').textContent = 'Mapa DNP — ' + r.relay;
  document.getElementById('modelo').textContent = r.relaytype || '—';
  document.getElementById('sem-wordbits').hidden = r.wordbits_ok;
  document.getElementById('grupos').textContent =
    'Sessões iguais: ' + r.groups.map(g => g.join('=')).join(' · ');

  const sel = document.getElementById('sessao');
  sel.innerHTML = '';
  for (const s of r.sessions) {
    const o = document.createElement('option');
    o.value = o.textContent = s;
    o.selected = (s === SESSAO);
    sel.appendChild(o);
  }

  document.getElementById('extras').innerHTML = r.extras.length
    ? r.extras.map(e => '<span class="hint">' + e.key + ': ' + e.value +
        '</span>').join(' · ')
    : '';

  const abas = document.getElementById('abas');
  abas.innerHTML = '';
  const kinds = Object.keys(r.blocks);
  if (!kinds.includes(ABA)) ABA = kinds[0];
  for (const k of kinds) {
    const n = r.blocks[k].filter(p => p.aviso).length;
    const b = document.createElement('button');
    b.textContent = k + (n ? ' ' + n + '⚠' : '');
    b.setAttribute('aria-selected', k === ABA);
    b.onclick = () => { ABA = k; desenhar(); };
    abas.appendChild(b);
  }
  desenhar();
}

function desenhar() {
  const rows = DADOS.blocks[ABA] || [];
  const temSca = rows.some(p => p.sca_key);
  const temDbd = rows.some(p => p.dbd_key);
  document.getElementById('cabecalho').innerHTML =
    '<th></th><th>#</th><th>Variável</th>' +
    (temSca ? '<th>Escala</th>' : '') +
    (temDbd ? '<th>Banda morta</th>' : '') + '<th></th>';

  const tb = document.querySelector('#pontos tbody');
  tb.innerHTML = '';
  for (const p of rows) {
    const tr = document.createElement('tr');
    tr.draggable = true;
    tr.dataset.key = p.key;
    tr.innerHTML =
      '<td class="puxador">⠿</td>' +
      '<td>' + p.index + '</td>' +
      '<td class="' + (p.aviso ? 'aviso-' + p.aviso : '') + '">' +
        '<input value="' + (p.value || '').replace(/"/g, '&quot;') +
        '" data-key="' + p.key + '"></td>' +
      (temSca ? '<td>' + campo(p.sca_key, p.sca) + '</td>' : '') +
      (temDbd ? '<td>' + campo(p.dbd_key, p.dbd) + '</td>' : '') +
      '<td class="hint">' + (p.aviso || '') + '</td>';
    tb.appendChild(tr);
  }

  tb.querySelectorAll('input').forEach(i => {
    i.onchange = () => enviar({[i.dataset.key]: i.value});
  });
  ligarArrasto(tb);

  const avisos = rows.filter(p => p.aviso).length;
  document.getElementById('rodape').textContent =
    rows.length + ' ponto(s), ' + avisos + ' aviso(s). ' +
    'Avisos não impedem a exportação.';
}

function campo(key, value) {
  if (!key) return '';
  return '<input value="' + (value || '').replace(/"/g, '&quot;') +
         '" data-key="' + key + '">';
}

async function enviar(changes) {
  const r = await (await fetch('/edit', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rdb: RDB, relay: RELAY, session: SESSAO, changes}),
  })).json();
  if (r.ok) carregar();
}

// Arrastar troca os dois pontos de lugar; nao insere-e-desloca. O indice DNP
// e' contrato com o mestre SCADA, e inserir renumeraria tudo entre os dois.
function ligarArrasto(tb) {
  tb.querySelectorAll('tr').forEach(tr => {
    tr.ondragstart = e => {
      ARRASTANDO = tr.dataset.key;
      tr.classList.add('arrastando');
      e.dataTransfer.effectAllowed = 'move';
    };
    tr.ondragend = () => {
      tr.classList.remove('arrastando');
      tb.querySelectorAll('tr').forEach(x => x.classList.remove('alvo'));
    };
    tr.ondragover = e => { e.preventDefault(); tr.classList.add('alvo'); };
    tr.ondragleave = () => tr.classList.remove('alvo');
    tr.ondrop = async e => {
      e.preventDefault();
      const alvo = tr.dataset.key;
      if (!ARRASTANDO || ARRASTANDO === alvo) return;
      const r = await (await fetch('/swap', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({rdb: RDB, relay: RELAY, session: SESSAO,
                              a: ARRASTANDO, b: alvo}),
      })).json();
      if (r.ok) carregar();
    };
  });
}

document.getElementById('sessao').onchange = e => {
  SESSAO = e.target.value;
  history.replaceState({}, '', '?rdb=' + encodeURIComponent(RDB) +
    '&relay=' + encodeURIComponent(RELAY) +
    '&d=' + encodeURIComponent(SESSAO));
  carregar();
};

document.getElementById('copiar').onclick = async () => {
  if (!confirm('Aplicar o mapa de ' + SESSAO +
               ' às demais sessões deste relé?')) return;
  const r = await (await fetch('/copy-session', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rdb: RDB, relay: RELAY, session: SESSAO}),
  })).json();
  if (r.ok) alert(r.touched + ' campo(s) alterado(s) em ' +
                  r.sessions.join(', '));
};

carregar();
</script>
```

- [ ] **Step 2: Commit**

```bash
git add sellib/web/dnp_map/templates/editor.html
git commit -m "Draw the DNP map as tabs of points, dragged by swap"
```

---

### Task 10: Montar a ferramenta e documentar

**Files:**
- Modify: `sellib/web/themes/items.py`
- Modify: `sellib/web/dashboard.py`
- Create: `tools/check_set_dnp_roundtrip.py`
- Modify: `docs/ENGINEERING-NOTES.md`

- [ ] **Step 1: Catálogo**

Em `sellib/web/themes/items.py`, dentro de `TOOLS`, logo após a entrada `gle-exporter`:

```python
    Tool("dnp-map", "/dnp-map/",
         "Editor de Mapa DNP", "Mapa DNP",
         "pontos DNP3 do relé",
         "Edita os pontos DNP3 (SET_D) de cada relé do RDB e gera um RDB "
         "novo com as alterações aplicadas.",
         "RDB"),
```

- [ ] **Step 2: Montagem**

Em `sellib/web/dashboard.py`, junto do import das outras ferramentas:

```python
from sellib.web import dnp_map
```

E na lista de `Mount(...)`, depois de `settings-compare`:

```python
        Mount("/dnp-map",
              dnp_map.handler.build_dnp_map_handler(logger, sessions),
              "DNP Map Editor"),
```

Se `dnp_map/__init__.py` não reexportar `handler`, importe direto:

```python
from sellib.web.dnp_map.handler import build_dnp_map_handler
```

e use `Mount("/dnp-map", build_dnp_map_handler(logger, sessions), "DNP Map Editor")`.

- [ ] **Step 3: Script de varredura sobre os RDBs reais**

`tools/check_set_dnp_roundtrip.py`:

```python
#!/usr/bin/env python3
"""Check the SET_D parser and the OLE writer against real RDBs.

Two things a unit test cannot cover, because the files are 40-140 MB and are
not in the repository:

    python3 tools/check_set_dnp_roundtrip.py
        Every SET_D in every RDB under rdbs/ and in the extraction cache:
        parse(b).serialize() == b.

    python3 tools/check_set_dnp_roundtrip.py --rebuild rdbs/obra.rdb
        Rebuild that RDB with no edits at all and compare every stream with
        the source. A writer that cannot reproduce a file byte for byte has
        no business writing a relay's settings.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import olefile  # noqa: E402

from sellib.parsers import ole_rebuild, set_dnp  # noqa: E402
from sellib.paths import RDBS_DIR, RDB_CACHE_DIR  # noqa: E402


def check_roundtrip() -> int:
    checked = failed = 0
    for rdb_path in sorted(RDBS_DIR.glob("*.rdb")):
        ole = olefile.OleFileIO(str(rdb_path))
        try:
            for entry in ole.listdir(streams=True, storages=False):
                if not set_dnp._SETD_NAME_RE.match(entry[-1]):
                    continue
                data = ole.openstream(entry).read()
                checked += 1
                if set_dnp.parse(data).serialize() != data:
                    failed += 1
                    print(f"FALHOU {rdb_path.name}:{'/'.join(entry)}")
        finally:
            ole.close()

    for cache_entry in sorted(RDB_CACHE_DIR.glob("*/extracted")):
        for relay in set_dnp.discover(cache_entry):
            for session in relay.sessions:
                data = session.fs_path.read_bytes()
                checked += 1
                if set_dnp.parse(data).serialize() != data:
                    failed += 1
                    print(f"FALHOU {session.fs_path}")

    print(f"{checked} SET_D conferidos, {failed} falha(s)")
    return 1 if failed else 0


def check_rebuild(src: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "rebuilt.rdb"
        try:
            ole_rebuild.rebuild(src, dst, {})
        except ole_rebuild.OleRebuildError as e:
            print(f"FALHOU {src.name}: {e}")
            return 1
        print(f"{src.name}: {src.stat().st_size} -> {dst.stat().st_size} bytes, "
              "todos os streams conferidos")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rebuild", type=Path, default=None,
                    help="reconstroi este RDB sem edicoes e confere tudo")
    args = ap.parse_args()
    if args.rebuild:
        return check_rebuild(args.rebuild)
    return check_roundtrip()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Rodar a verificação completa**

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python tools/check_set_dnp_roundtrip.py
.venv/bin/python tools/check_set_dnp_roundtrip.py --rebuild "$(ls rdbs/*.rdb | head -1)"
```

Expected: pytest todo verde; zero falhas de round-trip; o rebuild termina conferido.

- [ ] **Step 5: Exercitar no navegador**

```bash
python3 app.py --web
```

Roteiro, em `http://localhost:8765/dnp-map/`:

1. A ferramenta aparece no menu da home, nas três direções (`folha`, `regua`, `caderno`) — troque pelo seletor de tema.
2. Envie um RDB de `rdbs/`. A barra de progresso do topo se move durante o upload e a extração.
3. A lista traz os relés com mapa DNP, **incluindo os SEL-2440**, e mostra quais sessões são idênticas.
4. Abra um relé. As abas BI/BO/AI/CO aparecem só para os blocos que existem; contagens de aviso no rótulo.
5. Digite um bit inválido (`BANANA`): o campo é marcado, o rodapé conta o aviso, e nada é bloqueado.
6. Arraste uma linha sobre outra: os dois valores trocam de lugar, e só eles.
7. Troque a sessão no seletor e volte: a edição pendente continua lá.
8. Use *aplicar às demais sessões* e confirme pelo seletor.
9. Volte aos relés, edite um segundo relé, e confira que "alterações pendentes" lista os dois.
10. Exporte. Baixe o RDB e os `.txt`.
11. **Abra o RDB exportado no AcSELerator QuickSet.** É a única verificação que este ambiente não faz. Se o QuickSet recusar um RDB reconstruído, reporte — o fallback é restringir o export ao caminho de tamanho exato mais os `.txt`.

- [ ] **Step 6: Documentar no docs/ENGINEERING-NOTES.md**

Acrescentar em **Project layout**, junto das outras ferramentas:

```markdown
  - `sellib/web/dnp_map/` — o **Editor de Mapa DNP**: `model.py` (edições como diffs por sessão), `export.py` (o export híbrido), `handler.py` (rotas), `templates/`. O parser dos `SET_D` mora em `sellib/parsers/set_dnp.py` e o writer de OLE em `sellib/parsers/ole_rebuild.py`.
```

E em **Gotchas**:

```markdown
- **`RdbInfo.relays` só lista relé que tem GLE.** `parsers/rdb.py` monta um `RelayEntry` a partir dos streams `.gle`, então um relé sem diagrama — um SEL-2440, por exemplo — não aparece ali, mesmo tendo settings. Quem precisa de todos os relés varre `extract_dir/Relays/` direto; é o que `parsers/set_dnp.py:discover()` faz.
- **Uma linha de `SET_D` termina `KEY,"VALUE"\x1c\r\n`.** O `0x1C` (File Separator) fica *dentro* da linha, antes do CRLF, e não é whitespace. `parsers/sel_settings.py` não o trata e deixa o byte colado no valor — o que serve para ler e é fatal para escrever. Quem vai reescrever um settings usa `parsers/set_dnp.py`, cujo contrato é `parse(b).serialize() == b`.
- **Índice de ponto DNP não tem padding consistente**: o 411L escreve `BI_1`, o 751 e o 2440 escrevem `BI_00`. Slot livre é `""` no 411L e `"NA"` no 751/2440. A chave é sempre preservada literal, nunca reconstruída a partir do índice.
- **`olefile.write_stream` só troca um stream por outro do mesmo tamanho.** Rearranjar um mapa DNP conserva os bytes e cabe; preencher um slot livre cresce e não cabe. `parsers/ole_rebuild.py` reconstrói o Compound File inteiro para esse caso, e **verifica a própria saída** antes de entregar (reabre e compara todos os streams com a origem, apagando o arquivo se algo divergir). Não existe caminho que encha o arquivo de espaços até caber: ninguém aqui sabe o que o QuickSet tolera.
- **O validador de word bits avisa e nunca bloqueia.** `data/wordbits/<MODELO>.json`, semeado de um `cache/<FID>.json` do GLV por `tools/wordbits_from_glv_cache.py`. Sem arquivo para o modelo, a validação fica desligada e a página diz isso — as listas são curadas à mão e sempre atrasadas em relação ao firmware.
```

E em **Dependencies**:

```markdown
Testes (só desenvolvimento): `pytest`, em `requirements-dev.txt`. `app.py` bootstrapa apenas `requirements.txt`; para rodar os testes, `.venv/bin/python -m pip install -r requirements-dev.txt` e `.venv/bin/python -m pytest tests/`. Essa é a única exceção à regra de não rodar pip à mão.
```

- [ ] **Step 7: Commit**

```bash
git add sellib/web/themes/items.py sellib/web/dashboard.py tools/check_set_dnp_roundtrip.py docs/ENGINEERING-NOTES.md
git commit -m "Mount the DNP Map Editor and document what it assumes"
```
