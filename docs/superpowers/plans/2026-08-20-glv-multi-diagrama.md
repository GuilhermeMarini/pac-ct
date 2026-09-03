# GLV multi-diagrama + cache de RDB por conteúdo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O GLV passa a abrir N diagramas ao mesmo tempo, cada um começando desconectado, com conexão por diagrama compartilhada por relé; e o GLV sai de `dashboard.py` para `sellib/web/glv/`.

**Architecture:** Três camadas novas. `RelayLink`/`LinkPool` (`glv/link.py`) donos da conexão telnet, do `AsciiTargetReader` e da thread de polling, contados por referência e chaveados por `ip:porta` no processo. `GlvDiagram` (`glv/diagram.py`) é um GLE renderizado + notas + um `RelayLink` ou `None`. `glv/handler.py` guarda `dict[id, GlvDiagram]` por sessão (cookie `selsid`) e serve todas as rotas. Antes disso, `sellib/parsers/rdb.py` passa a extrair num cache de processo chaveado por sha256, o que elimina o `RDBS_DIR` compartilhado que a landing do GLV usa hoje.

**Tech Stack:** Python 3.10+ stdlib (`http.server`, `threading`), `olefile`, `telnetlib3`/`telnetlib` shim, `selprotopy` vendorizado. Sem framework, sem build, sem suíte de teste.

**Spec:** `docs/PROMPT-glv-multi-diagrama.md`

## Global Constraints

- Strings de interface em **português acentuado**; identificadores, comentários e docstrings de código novo em **inglês**. Ao editar arquivo existente, seguir a língua daquele arquivo (o `dashboard.py` atual comenta em português sem acento — o código movido mantém os comentários como estão).
- **Nenhuma cor, raio, pilha de fonte ou padding literal novo** em `sellib/web/**`. Só tokens de `sellib/web/theme.py`. Checagem: `grep -c "^\s*--bg:" sellib/web/*.py` continua devolvendo 1, e só em `theme.py`.
- **O desenho do GLE não muda.** `parsers/gle.py:render_page()` intocado; as classes de estado ao vivo (`.bit-1`, `.bit-0`, `.bit-unknown`, `polyline.connection`) e as cores do marca-texto e da busca continuam literais dentro do template do GLV. Só a moldura (`--viewer-bg`) é do tema.
- **`selprotopy/` é vendorizado** — um hook PreToolUse bloqueia edições. Se algo parecer bug de lá, reportar ao usuário, não corrigir.
- **Caminhos só por `sellib/paths.py`.** Nada de `Path(__file__).parent` em arquivo novo.
- Handlers escrevem **rotas absolutas** (`/values`, `/connect`); `self.mount_prefix` à mão só em `<a href download>` e links entre páginas.
- **Uploads e saídas derivadas no diretório da sessão** (`self.sdir(...)`); a única exceção é o cache de RDB por conteúdo, que é do processo e read-only para as ferramentas.
- **Sem framework de teste.** Verificação é `python3 app.py --web` + navegador, mais um smoke de import (`python3 -c "import sellib.web.dashboard"`) rodado dentro do venv do projeto: `.venv/bin/python`.
- `app.py` faz `from sellib.web.dashboard import main as web_main`. Esse ponto de entrada **não pode sumir**.
- Commits pequenos e frequentes, um por tarefa no mínimo. Branch atual: `tema-tokens`.

---

## Decisões abertas que este plano fecha

**Teto de conexões.** `[web] glv_max_links = 4` (novas conexões telnet simultâneas no processo) e `[web] glv_max_diagrams = 8` (diagramas abertos por sessão). Entrar numa conexão que já existe **nunca** conta para o teto — não custa nada ao relé. Estourar o teto devolve 409 com motivo em português, e o diagrama continua aberto e desconectado. 4 é o número de baias que uma equipe acompanha de uma vez sem transformar a rede da subestação num teste de carga; 8 diagramas é memória (cada GLE renderizado são alguns MB de SVG em RAM).

**Política do cache de RDB.** `cache/rdb/<sha256>/` com `source.rdb`, `extracted/` e `meta.json`. Varrido pelo mesmo sweeper das sessões (a cada 15 min) e uma vez no boot:
1. remove entradas com `last_used` mais velho que `[web] rdb_cache_max_age_days` (padrão 30);
2. enquanto o total passar de `[web] rdb_cache_max_gb` (padrão 8), remove a mais antiga por `last_used`;
3. **nunca** remove entrada tocada há menos que o TTL de sessão — uma sessão viva pode voltar a usar o RDB que subiu.
`meta.json` só é escrito depois da extração terminar; entrada sem `meta.json` é extração interrompida e é reextraída. Diferente de `cache/sessions/`, o cache **não** é apagado no boot — sobreviver ao restart é o ponto.

---

## File Structure

**Criados**

| Arquivo | Responsabilidade |
|---|---|
| `sellib/web/glv/__init__.py` | `build_glv_handler(logger, sessions, defaults)`, `GlvDefaults`; reexporta o que o `main()` precisa |
| `sellib/web/glv/state.py` | `LiveState` (+ `clear()`) |
| `sellib/web/glv/poll.py` | `poll_loop`, `poll_loop_fastmeter`, `poll_loop_tar`, `_read_fast_meter_analogs` |
| `sellib/web/glv/gle_pages.py` | `list_pages`, `collect_bit_names`, `collect_analog_symbols_per_page`, `collect_bits_per_page` |
| `sellib/web/glv/notes.py` | `NoteStore`, `NoteRegistry` (notas, marca-texto, grupos) chaveados por nome de relé |
| `sellib/web/glv/link.py` | `setup_relay`, `RelayLink`, `LinkPool`, `TooManyLinks` |
| `sellib/web/glv/diagram.py` | `GlvDiagram`, `build_diagram()` |
| `sellib/web/glv/handler.py` | todas as rotas do GLV + estado de sessão |
| `sellib/web/glv/templates/dashboard.html` | era `HTML_TEMPLATE` |
| `sellib/web/glv/templates/landing.html` | era `LANDING_HTML` |
| `sellib/parsers/rdb_cache.py` | cache por sha256 + varredura |

**Modificados**

| Arquivo | O quê |
|---|---|
| `sellib/paths.py` | `+RDB_CACHE_DIR`, `+GLV_TEMPLATES_DIR` |
| `sellib/parsers/rdb.py` | `process_upload` no cache por conteúdo; `RdbInfo.display_name` |
| `sellib/matchers/relay_scd.py` | chamada e comentário do layout antigo |
| `sellib/web/vb_updater.py` | `display_name`; saídas derivadas na sessão; sandbox do `/download` |
| `sellib/web/gle_exporter.py` | idem |
| `sellib/web/settings_compare.py` | `display_name` |
| `sellib/web/dashboard.py` | fica só com home + `main()` (~260 linhas) |
| `config/config.ini` | `[web] rdb_cache_max_gb`, `rdb_cache_max_age_days`, `glv_max_links`, `glv_max_diagrams` |
| `docs/ENGINEERING-NOTES.md` | layout ganha `sellib/web/glv/`; a gotcha "GLV is deliberately NOT per-user" some |

---

## Plano de corte de `dashboard.py` (4.462 linhas)

| Hoje | Linhas | Vai para |
|---|---|---|
| Cabeçalho, imports, shim do telnetlib | 1–64 | dividido: `glv/poll.py` (selprotopy, parser), `dashboard.py` (o que a home e o `main()` usam) |
| `GLV_SETUP_JOB` | 65 | **apagado** — vira `f"glv-connect-{diagram_id}"` em `glv/diagram.py` |
| `_RDBS_DIR` | 67 | **apagado** — a landing não escreve mais em `rdbs/` |
| `LiveState` | 73–103 | `glv/state.py`, **+ `clear()`** |
| `poll_loop`, `poll_loop_fastmeter`, `_read_fast_meter_analogs`, `poll_loop_tar` | 106–481 | `glv/poll.py`, **verbatim** (assinaturas e corpos intocados) |
| `list_pages`, `collect_bit_names`, `collect_analog_symbols_per_page`, `collect_bits_per_page` | 482–598 | `glv/gle_pages.py`, verbatim |
| `HTML_TEMPLATE` | 599–2677 | `glv/templates/dashboard.html` (Tarefa 5 move; Tarefa 10 reescreve a camada de abas) |
| grupos / notas / highlights | 2678–2790 | `glv/notes.py`, reescrito como `NoteStore`/`NoteRegistry` chaveados por nome de relé |
| `DashboardHandler` | 2791–3050 | `glv/handler.py` — mesmas rotas, agora com `?d=<id>` e lendo do `GlvDiagram` em vez de atributo de classe |
| `GlvMount`, `_glv_activate` | 3051–3095 | **apagados** — não há mais duas páginas se revezando |
| `HOME_HTML`, `build_home_handler` | 3096–3242 | **fica** em `dashboard.py` |
| `LANDING_HTML` (+ `.replace("__NAV__")`) | 3243–3711 | `glv/templates/landing.html`; o `replace` do nav vai para `glv/handler.py` |
| `_RDB_MAX_BYTES`, `_IP_RE`, `_valid_ipv4` | 3712–3722 | `glv/handler.py` |
| `run_landing_page` | 3724–3886 | **dissolvida** — vira as rotas `/novo`, `/landing-state`, `/rdb-upload`, `POST /diagrams` em `glv/handler.py`; o `threading.Event` some junto |
| `setup_relay` | 3887–3916 | `glv/link.py`, **assinatura nova** `(ip, port, acc_password, logger)` |
| `_glv_session_loop` | 3917–4365 | **dissolvida**: render/descoberta → `glv/diagram.py:build_diagram()`; conexão/poll → `glv/link.py:RelayLink.connect()`; a thread bloqueante e o `return_event` somem |
| `main()` | 4366–4462 | **fica** em `dashboard.py`, sem a thread do GLV, com `LinkPool` + `GlvDefaults` + sweeper do cache |

Resultado: `dashboard.py` ≈ 260 linhas (147 da home + ~110 do `main()`), continua sendo o alvo de `from sellib.web.dashboard import main as web_main`.

---

## Modelo: interfaces e regras de trava

### `glv/link.py`

```python
class TooManyLinks(RuntimeError):
    """Teto de conexoes simultaneas atingido."""


class RelayLink:
    """Uma conexao telnet com um rele, compartilhada por N diagramas.

    Dona do SELClient, do AsciiTargetReader, do LiveState e da thread de
    polling. Nunca decide o proprio tempo de vida: quem cria e destroi e' o
    LinkPool, com o lock do pool na mao.
    """

    key: str                      # "203.0.113.22:23"
    ip: str
    port: int
    state: LiveState              # unico; escrito pela thread de polling
    client: "SELClient | None"
    reader: "AsciiTargetReader | None"
    fid: str
    devid: str
    mode: str                     # "target_region" | "fast_meter_digitals" | "tar_digitals"
    ready: threading.Event        # setado quando connect() termina (com ou sem erro)
    error: str                    # motivo da falha, vazio se conectado
    owners: set[str]              # ids de diagrama; refs == len(owners)

    # -- ciclo de vida (so o LinkPool chama) --------------------------------
    def connect(self, *, acc_password, relay_model, poll_interval, logger,
                job=None) -> None
    def close(self) -> None

    # -- descoberta e polling ----------------------------------------------
    def ensure_bits(self, names: set[str], logger, job=None) -> int
    def set_wanted_bits(self, owner: str, bits: set[str]) -> None
    def snapshot(self) -> dict
    def info(self) -> dict        # {ip, port, fid, devid, mode, refs, connected, error}
```

**O que cada trava protege**

| Trava | Protege | Nunca é segurada durante |
|---|---|---|
| `LinkPool._lock` (RLock) | o mapa `key -> RelayLink` e **toda** transição de `owners` | `connect()`, `close()`, descoberta de bits — tudo que leva segundos ou minutos |
| `RelayLink._lock` (RLock) | `client`, `reader`, a thread de polling e o `stop_event` | I/O de rede longo dentro do poll (o poll roda fora dela; `ensure_bits` para o poll antes de entrar) |
| `LiveState.lock` | `digitals`, `analogs`, `error`, `ts`, `wanted_bits` | qualquer coisa |
| `NoteStore._lock` | os três JSON daquele relé | — |

**Regras do refcount** (é aqui que mora o risco que o usuário apontou):

1. `owners` é um **conjunto de ids de diagrama**, não um inteiro. `refs == len(owners)`. Adicionar duas vezes o mesmo dono é idempotente; remover duas vezes também. Um duplo clique em "Conectar" ou "Desconectar" não consegue desbalancear a conta.
2. Só o `LinkPool` mexe em `owners`, e só com `LinkPool._lock` segurado.
3. `GlvDiagram` guarda **no máximo uma** referência, em `self.link`. `connect()` sai cedo se `self.link is not None`; `disconnect()` faz `link, self.link = self.link, None` **antes** de chamar `pool.release(link, self.id)` — as duas coisas sob `diagram._lock`.
4. `connect()` que falha solta a referência no `finally`. Falha nunca deixa link pendurado.
5. Quando `owners` fica vazio, o pool tira o link do mapa e chama `close()` **fora** do lock do pool. Um `acquire()` concorrente que chegue no meio disso não encontra o link no mapa (já saiu) e cria um novo — nunca entra num link que está fechando.
6. Fechar diagrama e expirar sessão passam pelo mesmo caminho de `disconnect()`. O sweeper de sessão fecha os diagramas da sessão expirada antes de apagar o diretório; é isso que impede conexão pendurada para sempre.
7. Aquisição em duas fases: o pool cria o objeto com `owners={owner}` e `ready` limpo, **devolve** e solta o lock; quem pediu é que chama `connect()` na sua thread. Um segundo diagrama que peça o mesmo `ip:porta` nesse intervalo acha o objeto, entra em `owners` e espera `ready.wait()` — não abre um segundo telnet. Se `ready` vier com `error`, o segundo diagrama mostra o mesmo motivo no badge dele.

```python
class LinkPool:
    """Mapa `ip:porta -> RelayLink` do processo."""

    def __init__(self, logger, max_links: int = 4)

    def acquire(self, ip: str, port: int, owner: str) -> tuple[RelayLink, bool]:
        """Devolve `(link, criei_agora)`. `criei_agora=True` significa que quem
        chamou tem que rodar `link.connect(...)`; `False` significa que basta
        esperar `link.ready`. Levanta TooManyLinks quando uma chave NOVA
        estouraria o teto -- entrar numa conexao existente nunca estoura."""

    def release(self, link: RelayLink, owner: str) -> None:
        """Tira `owner` de `link.owners`. Se sobrar zero, tira o link do mapa e
        fecha (polling parado, telnet fechado) fora do lock."""

    def snapshot(self) -> list[dict]   # diagnostico / log
```

`setup_relay(ip, port, acc_password, logger)` deixa de ler `config.ini`. O `config.ini` vira só a origem dos valores padrão, lidos uma vez no boot para dentro de:

```python
@dataclass(frozen=True)
class GlvDefaults:
    ip: str
    port: int
    acc_password: str
    poll_interval: float
    relay_name: str
    gle_file: "str | None"     # --gle: semeia um diagrama na sessao nova
    no_relay: bool             # --no-relay: botao Conectar desabilitado
    max_links: int
    max_diagrams: int
```

### `glv/diagram.py`

```python
class GlvDiagram:
    """Um diagrama aberto: um GLE renderizado + notas + (talvez) um RelayLink."""

    id: str                       # "<sid8>-1", unico no processo
    title: str                    # rotulo da aba: "QPC1_LT1_UPC1 · GL1"
    relay_name: str               # nome do rele no RDB -> chave das notas
    gle_name: str
    gle_path: Path
    ip: str
    port: int
    relay_model: "RelayModel | None"

    pages_meta: list[tuple[str, str]]
    svgs: dict[str, str]
    bits_per_page: dict[str, set[str]]
    analogs_per_page: dict[str, dict[str, str]]
    analog_groups_meta: dict[str, str]
    var_index: dict[str, dict]
    all_wanted_bits: set[str]     # bits do GLE + derivados, para ensure_bits

    notes: NoteStore
    idle: LiveState               # o que se ve desconectado
    link: "RelayLink | None"
    status: str                   # "idle" | "connecting" | "live" | "error"
    error: str
    job_id: str                   # f"glv-connect-{id}"

    @property
    def state(self) -> LiveState:
        """O LiveState que a tela deve mostrar."""
        link = self.link
        return link.state if link is not None else self.idle

    def connect_async(self, pool, defaults, logger) -> str   # devolve job_id
    def disconnect(self, pool, logger) -> None
    def close(self, pool, logger) -> None
    def values(self, page: str) -> dict
    def meta(self) -> dict
    def tab(self) -> dict         # {id, title, relay, ip, status, error, refs}
```

`state` é uma property de propósito: **o `LiveState` mora no `RelayLink`**, porque as três `poll_loop*` escrevem num objeto só e não podem ser tocadas. Dois diagramas no mesmo relé leem o mesmo `LiveState` — que é o certo, já que a Relay Word é do relé, não do desenho. Desconectado, o diagrama cai para o `idle`, que é vazio, e a tela volta inteira a indeterminado sem ninguém precisar limpar nada. `LiveState.clear()` existe e é usado nos dois pontos onde estado velho poderia sobreviver: no `idle` ao desconectar (ele pode carregar um `error` da tentativa anterior) e no `link.state` quando o polling para.

`build_diagram(...)` é o que sobrou do `_glv_session_loop` sem a conexão: parse do GLE, `render_page` por página, `bits_per_page`, `analogs_per_page`, `var_index`, `all_wanted_bits` (bits do GLE + derivados do modelo), `NoteStore`. Roda em ~1 s e não fala com a rede.

### `glv/notes.py`

```python
def note_key(relay_name: str) -> str
    """Nome do rele no RDB -> chave de arquivo. `re.sub(r"[^A-Za-z0-9._-]", "_", ...)`."""


class NoteStore:
    """Notas, marca-texto e checkboxes de grupo de UM rele, em cache/."""

    key: str
    group_checked: set[str]
    note_relay: str
    note_pages: dict[str, str]
    highlights: dict[str, dict[str, bool]]

    def save_groups(self) -> None
    def save_note(self) -> None
    def save_highlights(self) -> None
    def adopt_devid(self, devid: str, logger) -> list[str]
        """Primeira conexao: para cada um dos tres arquivos, se existir pelo
        DEVID e NAO existir pela chave, renomeia e recarrega. Devolve a lista
        do que adotou (para o log). Idempotente."""


class NoteRegistry:
    """Um NoteStore por chave, do processo -- dois visitantes no mesmo rele
    escrevem no mesmo arquivo e precisam do mesmo objeto e da mesma trava."""

    def get(self, key: str) -> NoteStore
```

Arquivos e formato não mudam: `cache/groups_<chave>.json`, `notes_<chave>.json`, `highlights_<chave>.json`, mesmos `version`/campos. Só a chave passa de DEVID para nome do relé. Como a chave é o nome desde antes de conectar, nota escrita com o diagrama desconectado já nasce no arquivo certo — e por isso a adoção só acontece quando o arquivo pela chave **não existe**: nada escrito se perde.

### Estado de sessão do GLV

```python
class _GlvSession:              # state_factory de SessionHandler, session_key="glv"
    diagrams: dict[str, GlvDiagram]
    order: list[str]            # ordem das abas
    active: "str | None"
    counter: int                # sufixo dos ids
    rdb: "RdbInfo | None"       # ultimo RDB do seletor
```

### Rotas (todas sob `/glv/`)

| Rota | O que faz |
|---|---|
| `GET /` | casca: faixa de abas + faixa de páginas + viewer. Aba ativa por `?d=<id>` |
| `GET /novo` | seletor (era a landing) |
| `GET /landing-state` | estado do RDB da sessão (como hoje) |
| `POST /rdb-upload` | upload → `process_upload` (cache por conteúdo) |
| `GET /diagrams` | `{diagrams:[tab...], active, max_diagrams, no_relay}` |
| `POST /diagrams` | cria a partir de `{relay, gle, ip, port}` → `{id}` |
| `POST /diagrams/close?d=` | fecha e solta a conexão |
| `POST /diagrams/activate?d=` | marca a aba ativa (o servidor lembra qual era) |
| `POST /connect?d=` | dispara a thread de conexão; responde 202 `{job}` |
| `POST /disconnect?d=` | solta o link, `idle.clear()`, volta a indeterminado |
| `GET /meta?d=` | páginas, índice de variáveis, grupos de analógicos, relé, status |
| `GET /pages/<safe>?d=` | SVG da página |
| `GET /values?d=&page=` | como hoje, do `diagram.state` |
| `GET/POST /group-state?d=`, `/note?d=`, `/highlights?d=` | como hoje, via `NoteStore` |
| `GET /debug/analogs?d=` | como hoje |

`GlvMount` e `_glv_activate` somem: há uma página só.

**Decisão de tela a confirmar com o usuário:** a aba "+" **navega** para `/novo` e volta para `/?d=<novo>` depois de escolher, em vez de abrir o seletor como overlay dentro da mesma página. Motivo: `landing.html` são 460 linhas de markup e JS que já funcionam (upload com progresso, lista de relés, campo de IP); transformar em overlay é risco desproporcional. A exigência de "não recarregar" vale para **trocar de diagrama**, que é o que acontece o tempo todo; abrir mais um é raro. Se preferir o overlay, é uma tarefa a mais no fim.

---

## Task 1: cache de RDB por sha256

**Files:**
- Create: `sellib/parsers/rdb_cache.py`
- Modify: `sellib/paths.py`, `sellib/parsers/rdb.py`, `sellib/matchers/relay_scd.py:325-341`

**Interfaces:**
- Consumes: nada.
- Produces: `paths.RDB_CACHE_DIR`; `rdb_cache.entry_for(sha, root=None) -> CacheEntry`; `rdb_cache.touch(entry)`; `RdbInfo.display_name: str`; `process_upload(data, filename, cache_root=None, on_progress=None) -> RdbInfo`.

- [ ] **Step 1: `RDB_CACHE_DIR` em `paths.py`**

Depois de `CACHE_DIR`, antes de `RDBS_DIR`:

```python
# Extracoes de RDB, chaveadas pelo sha256 do arquivo. Dois arquivos iguais SAO
# o mesmo arquivo, entao a extracao e' unica no processo e sobrevive ao
# restart -- diferente de cache/sessions/, que e' apagado no boot.
RDB_CACHE_DIR: Path = CACHE_DIR / "rdb"
```

E inclua em `ensure_dirs()`: `for d in (CACHE_DIR, RDB_CACHE_DIR, RDBS_DIR):`.

- [ ] **Step 2: `sellib/parsers/rdb_cache.py`**

```python
"""Cache de extracao de RDB chaveado pelo conteudo.

Antes cada sessao guardava a propria copia do RDB (40-140 MB) e a propria
extracao, porque `process_upload` chaveava por NOME dentro do `base_dir` de
cada ferramenta. Como dois arquivos com o mesmo sha256 SAO o mesmo arquivo, a
extracao passou a morar em `cache/rdb/<sha256>/`, unica no processo:

    cache/rdb/<sha256>/source.rdb
    cache/rdb/<sha256>/extracted/Relays/...
    cache/rdb/<sha256>/meta.json

`meta.json` so e' escrito DEPOIS que a extracao termina. Entrada sem ele e'
extracao interrompida (kill -9, disco cheio) e e' refeita -- e' o que substitui
a comparacao de hash do arquivo em disco que existia antes.

O nome que o usuario ve nao mora aqui: cada sessao carrega o seu em
`RdbInfo.display_name`, senao todo mundo veria o nome de quem subiu primeiro.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from sellib.paths import RDB_CACHE_DIR

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# Uma trava por hash: dois visitantes subindo o mesmo RDB ao mesmo tempo
# extraiam por cima um do outro. O segundo espera e reaproveita.
_LOCKS: "dict[str, threading.Lock]" = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class CacheEntry:
    sha256: str
    root: Path

    @property
    def rdb_path(self) -> Path:
        return self.root / "source.rdb"

    @property
    def extract_dir(self) -> Path:
        return self.root / "extracted"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    @property
    def complete(self) -> bool:
        return self.meta_path.is_file() and self.rdb_path.is_file()


def entry_for(sha256: str, root: "Path | None" = None) -> CacheEntry:
    if not _SHA_RE.match(sha256 or ""):
        raise ValueError(f"sha256 invalido: {sha256!r}")
    base = Path(root) if root is not None else RDB_CACHE_DIR
    return CacheEntry(sha256=sha256, root=base / sha256)


def lock_for(sha256: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lk = _LOCKS.get(sha256)
        if lk is None:
            lk = _LOCKS[sha256] = threading.Lock()
        return lk


def write_meta(entry: CacheEntry, display_name: str, n_relays: int) -> None:
    entry.meta_path.write_text(json.dumps({
        "version": 1,
        "sha256": entry.sha256,
        "first_name": display_name,
        "relays": n_relays,
        "created": time.time(),
        "last_used": time.time(),
    }, indent=2), encoding="utf-8")


def touch(entry: CacheEntry) -> None:
    """Marca a entrada como em uso -- o sweeper olha `last_used`."""
    try:
        meta = json.loads(entry.meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    meta["last_used"] = time.time()
    try:
        entry.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass


def _last_used(entry_root: Path) -> float:
    try:
        meta = json.loads((entry_root / "meta.json").read_text(encoding="utf-8"))
        return float(meta.get("last_used") or 0.0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def sweep(logger, max_gb: float = 8.0, max_age_days: float = 30.0,
          min_age_seconds: float = 8 * 3600, root: "Path | None" = None) -> int:
    """Remove entradas velhas e, se ainda passar do teto, as menos usadas.

    `min_age_seconds` e' o TTL da sessao: uma sessao viva pode nao tocar o RDB
    por horas e ainda voltar a usa-lo, entao nada mais novo que isso sai.
    Devolve quantas entradas foram removidas.
    """
    base = Path(root) if root is not None else RDB_CACHE_DIR
    if not base.is_dir():
        return 0
    now = time.time()
    entries = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or not _SHA_RE.match(child.name):
            continue
        entries.append((child, _last_used(child), _dir_size(child)))

    removed = 0

    def drop(path: Path, why: str) -> None:
        nonlocal removed
        try:
            shutil.rmtree(path)
        except OSError as e:
            logger.warning("[rdb-cache] nao consegui remover %s: %s", path.name[:12], e)
            return
        removed += 1
        logger.info("[rdb-cache] %s removido (%s)", path.name[:12], why)

    keep = []
    for path, used, size in entries:
        age = now - used
        if age < min_age_seconds:
            keep.append((path, used, size))
            continue
        if age > max_age_days * 86400:
            drop(path, f"ocioso ha {age / 86400:.1f} dias")
            continue
        keep.append((path, used, size))

    cap = int(max_gb * (1 << 30))
    total = sum(s for _, _, s in keep)
    for path, used, size in sorted(keep, key=lambda t: t[1]):
        if total <= cap:
            break
        if now - used < min_age_seconds:
            continue
        drop(path, f"teto de {max_gb:.1f} GB")
        total -= size
    return removed
```

- [ ] **Step 3: `process_upload` sobre o cache**

Em `sellib/parsers/rdb.py`: acrescente `display_name: str = ""` a `RdbInfo` (campo com default, no fim, para não quebrar construção posicional) e troque `process_upload` inteiro:

```python
def process_upload(data: bytes, filename: str, cache_root: "Path | None" = None,
                   on_progress=None) -> RdbInfo:
    """Extrai o RDB no cache por conteudo e devolve o que ele contem.

    O arquivo vai para `cache/rdb/<sha256>/source.rdb` e a extracao para
    `.../extracted/`. Dois uploads do mesmo conteudo -- do mesmo usuario ou de
    outro, hoje ou depois de um restart -- reaproveitam a mesma extracao.
    `cache_root` troca a raiz do cache (uso fora da web); None usa
    `paths.RDB_CACHE_DIR`.

    `display_name` sai do nome que ESTE upload trouxe, e nao do cache: senao
    todo mundo veria na tela o nome de quem subiu primeiro.
    """
    def _report(done, total, stage):
        if on_progress is not None:
            on_progress(done, total, stage)

    if not data:
        raise ValueError("arquivo RDB vazio")

    safe_name = sanitize_name(filename)
    if not safe_name.lower().endswith(".rdb"):
        safe_name = safe_name + ".rdb"

    _report(0, 1, "Verificando arquivo")
    sha = sha256_bytes(data)
    entry = rdb_cache.entry_for(sha, root=cache_root)

    with rdb_cache.lock_for(sha):
        if entry.complete:
            _report(0, 1, "Reaproveitando extracao existente")
            relays = _scan_existing(entry.extract_dir)
            reused = bool(relays)
        else:
            relays = []
            reused = False
        if not reused:
            _report(0, 1, "Gravando RDB em disco")
            entry.root.mkdir(parents=True, exist_ok=True)
            entry.meta_path.unlink(missing_ok=True)   # incompleto ate o fim
            entry.rdb_path.write_bytes(data)
            relays = _extract_and_collect(entry.rdb_path, entry.extract_dir,
                                          on_progress)
            rdb_cache.write_meta(entry, safe_name, len(relays))
        else:
            rdb_cache.touch(entry)

    return RdbInfo(
        rdb_path=entry.rdb_path,
        extract_dir=entry.extract_dir,
        sha256=sha,
        reused=reused,
        relays=relays,
        display_name=safe_name,
    )
```

Importe `from sellib.parsers import rdb_cache` no topo e atualize o docstring do módulo (o parágrafo que descreve `rdbs/<nome>.rdb` está errado a partir daqui).

- [ ] **Step 4: `relay_scd.py` continua funcionando**

Em `sellib/matchers/relay_scd.py:325-341`, troque o bloco do `base_dir` e o comentário:

```python
    rdb_path = Path(rdb_path)
    # A extracao vai pro cache por conteudo (`cache/rdb/<sha256>/`), nao mais
    # pra um diretorio ao lado do arquivo. `base_dir`, quando dado, vira a raiz
    # desse cache -- util pra rodar isolado fora da web.
    data = rdb_path.read_bytes()
    info = rdb_loader.process_upload(data, rdb_path.name,
                                     cache_root=Path(base_dir) if base_dir else None)
```

Ajuste a assinatura/docstring da função para dizer `base_dir` = raiz alternativa do cache.

- [ ] **Step 5: smoke**

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from sellib.parsers.rdb import process_upload
data = Path('samples').glob('*.rdb').__next__().read_bytes()
a = process_upload(data, "primeiro.rdb")
b = process_upload(data, "segundo.rdb")
print(a.rdb_path, a.display_name, a.reused)
print(b.rdb_path, b.display_name, b.reused)
assert a.rdb_path == b.rdb_path and b.reused and b.display_name == "segundo.rdb"
PY
```
Esperado: mesmo caminho no cache, `reused=True` no segundo, nomes diferentes. Se `samples/` não tiver `.rdb`, use um RDB real do usuário.

- [ ] **Step 6: Commit**

```bash
git add sellib/paths.py sellib/parsers/rdb_cache.py sellib/parsers/rdb.py sellib/matchers/relay_scd.py
git commit -m "Extract RDBs into a content-addressed cache"
```

---

## Task 2: consumidores do cache — nome na tela e saídas derivadas

**Files:**
- Modify: `sellib/web/vb_updater.py:893,1914,2005,2019,2095,2288`, `sellib/web/gle_exporter.py:661,1152,1217,1284,1344`, `sellib/web/settings_compare.py:129,519,1707`, `sellib/web/dashboard.py:3759,3816`

**Interfaces:**
- Consumes: `RdbInfo.display_name` (Task 1).
- Produces: `/download` das quatro ferramentas passa a aceitar `self.sdir("out")`.

- [ ] **Step 1: nome na tela**

Troque `info.rdb_path.name` / `st.rdb.rdb_path.name` por `.display_name` em: `gle_exporter.py:661,1217`, `settings_compare.py:129,519,1707`, `vb_updater.py:893,2288`, `dashboard.py:3759,3816`. Confira que sobrou nada: `grep -rn "rdb_path.name" sellib/` deve voltar vazio.

- [ ] **Step 2: saídas derivadas na sessão (vb_updater)**

`vb_updater.py:2004` e `:2094` constroem o `.rdb` de saída ao lado do de origem. Passe a construir dentro da sessão, a partir do nome que o usuário conhece:

```python
                        out_name = _with_suffix_before_ext(
                            Path(rdb.display_name), "_comments_updated").name
                        out_path = self.sdir("out") / out_name
```

Idem em `:2094` com `"_batch_comments_updated"`. `:2018` (saída de SCD) já fica em `self.sdir("scd")` — deixe.

- [ ] **Step 3: saídas derivadas na sessão (gle_exporter)**

`gle_exporter.py:1284` (nome do `.xlsx`) e `:1344` (`.rdb` atualizado):

```python
                out_name = _with_suffix_before_ext(
                    Path(rdb.display_name), "_gle_comments").with_suffix(".xlsx").name
```
```python
                out_path = self.sdir("out") / _with_suffix_before_ext(
                    Path(rdb.display_name), "_gle_comments_updated").name
```

- [ ] **Step 4: sandbox do `/download`**

`vb_updater.py:1914` → `if not is_within(target, (self.sdir("out"), self.sdir("scd"))):`
`gle_exporter.py:1152` → `if not is_within(target, (self.sdir("out"), self.sdir("xlsx"))):`

`self.sdir("rdbs")` sai das duas tuplas: uploads não vão mais para lá, e o cache **não** entra no sandbox — deixar entrar faria um visitante baixar arquivo derivado de outro.

- [ ] **Step 5: verificação no navegador**

`python3 app.py --web`; em duas janelas anônimas diferentes, suba o mesmo RDB no VB Updater. A segunda tem que reaproveitar (log `reaproveitado`, barra quase instantânea) e cada tela mostra o nome que **aquela** janela subiu. Gere o RDB com comentários atualizados e baixe: o arquivo tem que vir com o nome do upload daquela sessão.

- [ ] **Step 6: Commit**

```bash
git add sellib/web/vb_updater.py sellib/web/gle_exporter.py sellib/web/settings_compare.py sellib/web/dashboard.py
git commit -m "Show the uploaded RDB name and keep derived files in the session"
```

---

## Task 3: varredura do cache

**Files:**
- Modify: `sellib/web/dashboard.py:4366-4462` (`main()`), `config/config.ini`

**Interfaces:**
- Consumes: `rdb_cache.sweep(logger, max_gb, max_age_days, min_age_seconds)` (Task 1).
- Produces: chaves `[web] rdb_cache_max_gb`, `rdb_cache_max_age_days`.

- [ ] **Step 1: config**

Em `config/config.ini`, na seção `[web]`, depois de `session_ttl_hours`:

```ini
; Cache de extracao de RDB (`cache/rdb/<sha256>/`). Diferente das sessoes, ele
; NAO e' apagado no boot: reaproveitar entre usuarios e entre reinicios e' o
; motivo dele existir. E' varrido junto com as sessoes (a cada 15 min): sai o
; que esta ocioso ha mais que `rdb_cache_max_age_days` e, se ainda passar de
; `rdb_cache_max_gb`, sai o menos usado -- nunca o que foi tocado ha menos que
; `session_ttl_hours`, porque uma sessao viva ainda pode precisar dele.
rdb_cache_max_gb = 8.0
rdb_cache_max_age_days = 30
```

- [ ] **Step 2: sweeper**

Em `main()`, logo depois de `sessions.start_sweeper()`:

```python
    cache_gb = cfg.getfloat("web", "rdb_cache_max_gb", fallback=8.0)
    cache_days = cfg.getfloat("web", "rdb_cache_max_age_days", fallback=30.0)

    def _sweep_rdb_cache():
        rdb_cache.sweep(logger, max_gb=cache_gb, max_age_days=cache_days,
                        min_age_seconds=ttl_hours * 3600)

    _sweep_rdb_cache()          # uma vez no boot: restart recupera espaco
    sessions.on_sweep = _sweep_rdb_cache
```

e em `SessionManager.start_sweeper`, dentro do `loop()`, depois de `self.sweep()`:

```python
                    hook = getattr(self, "on_sweep", None)
                    if hook is not None:
                        hook()
```

com `on_sweep: "Callable[[], None] | None" = None` declarado em `SessionManager.__init__`.

- [ ] **Step 3: verificação**

`python3 app.py --web` e confira no log a linha do sweep no boot. Force a poda com `rdb_cache_max_gb = 0.001` e `session_ttl_hours = 0.001` num config de teste (`--config`) e veja `cache/rdb/` esvaziar em até 15 min (ou reinicie para o sweep do boot).

- [ ] **Step 4: Commit**

```bash
git add config/config.ini sellib/web/dashboard.py sellib/web/session.py
git commit -m "Sweep the RDB cache alongside the session sweeper"
```

---

## Task 4: pacote `sellib/web/glv/` — mover o que não muda

**Files:**
- Create: `sellib/web/glv/__init__.py`, `glv/state.py`, `glv/poll.py`, `glv/gle_pages.py`
- Modify: `sellib/web/dashboard.py:73-598`

**Interfaces:**
- Produces: `glv.state.LiveState` (com `clear()`), `glv.poll.poll_loop|poll_loop_fastmeter|poll_loop_tar`, `glv.gle_pages.list_pages|collect_bit_names|collect_analog_symbols_per_page|collect_bits_per_page`.

- [ ] **Step 1: `glv/state.py`**

Mova `LiveState` (dashboard.py:73-103) sem alterar nada e acrescente:

```python
    def clear(self) -> None:
        """Volta tudo a indeterminado.

        Chamado ao desconectar: a tela nunca deve mostrar um valor que nao
        esta sendo lido agora. `wanted_bits` fica -- e' o que a pagina aberta
        pediu, nao um valor lido.
        """
        with self.lock:
            self.digitals = {}
            self.analogs = {}
            self.last_update_ts = 0.0
            self.error = ""
```

- [ ] **Step 2: `glv/poll.py`**

Mova dashboard.py:106-481 **verbatim** (as três `poll_loop*` e `_read_fast_meter_analogs`). Leve para o topo do arquivo os imports que elas usam e que hoje vivem no cabeçalho do `dashboard.py`: o shim `ensure_telnetlib()`, `selprotopy`, `SELClient`, `commands`, `sel_parser`, `AsciiTargetReader`, `time`, `logging`, `threading`, `defaultdict`, `PROJECT_ROOT` no `sys.path`. Nenhuma assinatura muda.

- [ ] **Step 3: `glv/gle_pages.py`**

Mova dashboard.py:482-598 verbatim. Imports: `re`, `parse_gle`/`element_info`/`is_const_symbol_name` de `sellib.parsers.gle`.

- [ ] **Step 4: `dashboard.py` importa do pacote**

Apague as regiões movidas e ponha, por enquanto:

```python
from sellib.web.glv.state import LiveState
from sellib.web.glv.poll import poll_loop, poll_loop_fastmeter, poll_loop_tar
from sellib.web.glv.gle_pages import (
    list_pages, collect_bit_names, collect_analog_symbols_per_page,
    collect_bits_per_page,
)
```

- [ ] **Step 5: smoke + navegador**

```bash
.venv/bin/python -c "import sellib.web.dashboard, sellib.web.glv.poll; print('ok')"
python3 app.py --web
```
O GLV tem que continuar exatamente como estava (landing → escolher GLE → dashboard).

- [ ] **Step 6: Commit**

```bash
git add sellib/web/glv sellib/web/dashboard.py
git commit -m "Move LiveState, the poll loops and the GLE page helpers into sellib/web/glv"
```

---

## Task 5: templates viram arquivos

**Files:**
- Create: `sellib/web/glv/templates/dashboard.html`, `sellib/web/glv/templates/landing.html`
- Modify: `sellib/paths.py`, `sellib/web/dashboard.py:599-2677,3243-3711`

**Interfaces:**
- Produces: `paths.GLV_TEMPLATES_DIR`; `glv.templates.load(name) -> str`.

- [ ] **Step 1: `GLV_TEMPLATES_DIR` em `paths.py`**

```python
# Templates HTML do GLV. Sao arquivos .html de verdade (e nao string no .py)
# porque ~1.400 das 2.500 linhas sao JavaScript: assim o editor colore e o
# linter enxerga. A mecanica de substituicao (`${PAGES_JSON}`) nao mudou.
GLV_TEMPLATES_DIR: Path = PROJECT_ROOT / "sellib" / "web" / "glv" / "templates"
```

- [ ] **Step 2: mover o conteúdo**

`dashboard.html` recebe dashboard.py:600-2676 (o corpo do raw string, sem as aspas). `landing.html` recebe 3244-3704. Nada dentro muda — nem os `${...}`, nem o `__NAV__`.

- [ ] **Step 3: carregador**

Em `glv/__init__.py`:

```python
from sellib.paths import GLV_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Le um template do GLV. Lido no import, como a string era antes."""
    return (GLV_TEMPLATES_DIR / name).read_text(encoding="utf-8")
```

Em `dashboard.py`, por enquanto:

```python
HTML_TEMPLATE = glv.load_template("dashboard.html")
LANDING_HTML = glv.load_template("landing.html").replace(
    "__NAV__", theme_mod.nav_html("glv"))
```

- [ ] **Step 4: verificação**

`python3 app.py --web`; abra `/glv/`, suba um RDB, escolha um GLE em modo visualização. As duas telas têm que estar idênticas nos três temas (troque no seletor do cabeçalho).

- [ ] **Step 5: Commit**

```bash
git add sellib/paths.py sellib/web/glv/templates sellib/web/glv/__init__.py sellib/web/dashboard.py
git commit -m "Turn the GLV templates into real .html files"
```

---

## Task 6: notas por nome de relé

**Files:**
- Create: `sellib/web/glv/notes.py`
- Modify: `sellib/web/dashboard.py:2678-2790` (apagar), `:2791-3050` (usar o store)

**Interfaces:**
- Consumes: nada.
- Produces: `notes.NoteStore`, `notes.NoteRegistry`, `notes.NOTES` (instância do processo), `notes.note_key(relay_name)`.

- [ ] **Step 1: `glv/notes.py`**

Reescreva as três famílias de funções (dashboard.py:2678-2790) como um objeto. Mesmos arquivos, mesmo formato, mesma migração v1→v2 da nota; só a chave muda de DEVID para nome do relé:

```python
class NoteStore:
    def __init__(self, key: str):
        self.key = key
        self._lock = threading.RLock()
        self.group_checked: set[str] = _load_groups(key)
        self.note_relay, self.note_pages = _load_note(key)
        self.highlights = _load_highlights(key)

    def set_group(self, group_id: str, checked: bool) -> None: ...
    def set_note(self, scope: str, page_id: str, html: str) -> None: ...
    def set_highlight(self, page: str, item_id: str, on: bool) -> None: ...

    def adopt_devid(self, devid: str, logger) -> list[str]:
        """Primeira conexao: adota os arquivos gravados pelo DEVID.

        So adota o arquivo que NAO existe pela chave nova -- nota escrita antes
        de conectar ja esta no arquivo certo e nao pode ser sobrescrita. Roda
        uma vez por store; depois vira no-op.
        """
```

`adopt_devid` faz, por arquivo, `os.replace(old, new)` quando `old.is_file() and not new.is_file()`, recarrega o campo correspondente e loga `"[glv] notas adotadas do DEVID %s para a chave %s: %s"`.

`NoteRegistry.get(key)` devolve sempre o mesmo `NoteStore` por chave, sob lock — dois visitantes no mesmo relé escrevem nos mesmos arquivos e precisam da mesma trava. `NOTES = NoteRegistry()` no fim do módulo.

- [ ] **Step 2: `DashboardHandler` usa o store**

Nas rotas `/group-state`, `/note` e `/highlights` (GET e POST), troque os atributos de classe e os `_save_*` por `store = <diagrama>.notes` e os métodos acima. O campo `"devid"` do JSON de resposta passa a ser `"key"` (o JS só o usa para exibir; ajuste o consumo no template se houver).

- [ ] **Step 3: verificação**

Sem relé: abra um GLE em modo visualização, escreva uma nota, marque um grupo, use o marca-texto. Confira que apareceram `cache/notes_<NOME_DO_RELE>.json`, `groups_...`, `highlights_...` — com o nome do relé, não com DEVID. Recarregue e veja tudo voltar.

Adoção: renomeie um `notes_<NOME>.json` para `notes_<DEVID>.json`, reinicie e conecte (precisa de relé) — o arquivo tem que voltar a se chamar pelo nome e a nota reaparecer, com a linha no log.

- [ ] **Step 4: Commit**

```bash
git add sellib/web/glv/notes.py sellib/web/dashboard.py
git commit -m "Key notes, highlights and group checkboxes by relay name"
```

---

## Task 7: `RelayLink` e `LinkPool`

**Files:**
- Create: `sellib/web/glv/link.py`
- Modify: `sellib/web/dashboard.py:3887-3916` (apagar `setup_relay`)

**Interfaces:**
- Consumes: `glv.state.LiveState`, `glv.poll.*`.
- Produces: `link.setup_relay(ip, port, acc_password, logger) -> SELClient`; `link.RelayLink`; `link.LinkPool`; `link.TooManyLinks`.

- [ ] **Step 1: `setup_relay` sem `config.ini`**

Mova dashboard.py:3887-3916 para `glv/link.py` trocando só a cabeça:

```python
def setup_relay(ip: str, port: int, acc_password: str, logger=None) -> SELClient:
    """Abre o telnet, faz login e autoconfig, e devolve o SELClient pronto.

    Recebe IP, porta e senha explicitos de proposito. Antes lia
    `cfg.get("tcp","ip_address")`, e o loop de sessao ESCREVIA nesse mesmo cfg
    quando o usuario digitava um IP na tela de selecao -- com dois diagramas,
    abrir o segundo apontando pra outro IP reescrevia o IP do primeiro, que
    continuava dizendo na tela que era o rele A e reconectava no rele B.
    O config.ini agora e' so a fonte dos valores padrao, lida uma vez no boot.
    """
    tn = telnetlib.Telnet(ip, port, timeout=10)
    drain_login_banner(tn, logger)
    client = SELClient(tn, autoconfig_now=False, verbose=False)
    client.access_level_1(level_1_pass=acc_password.encode())
    ...  # resto igual
```

- [ ] **Step 2: `RelayLink`**

Escreva a classe com os campos e métodos da seção "Modelo". Pontos que precisam sair certos:

`connect()` faz, nesta ordem: `setup_relay` → decide o modo pelo `relay_model` (`uses_target_region` → `"target_region"`; `digitals_via_tar` → `"tar_digitals"`; senão `"fast_meter_digitals"`) → nos dois modos ASCII monta o `AsciiTargetReader`, carrega o cache por FID e roda a descoberta inicial (o bloco de dashboard.py:4116-4156, com os mesmos `MIN_ROWS_DESIRED = 500` e `TAR_MAX_ROWS = 256`, e o mesmo reporte de `job.stage`) → `_start_polling()` → `ready.set()`. Em qualquer exceção: `self.error = f"sem conexao com {ip}:{port} -- modo desenho"` (ou o motivo real), `ready.set()` no `finally`, e **não** levanta para fora — quem pediu lê `link.error`.

`_start_polling()` escolhe a `poll_loop*` pelo modo, cria `stop_event` novo e sobe a thread daemon `name=f"glv-poll-{self.key}"`. `tar_digitals` usa `max(poll_interval, 1.5)`, como hoje.

`_stop_polling()` seta o event, dá `join(timeout=2.0)` e zera a referência.

`ensure_bits(names)` é o que permite um segundo diagrama entrar num link já vivo com bits que ninguém pediu ainda:

```python
    def ensure_bits(self, names, logger, job=None) -> int:
        """Descobre no rele os bits que ainda nao estao no mapa.

        Precisa parar o polling: o telnet e' um so, e intercalar `TAR <nome>`
        com o pipeline de Fast Meter embaralha as duas respostas. Parar e
        subir de novo custa uma volta de poll e evita mexer nas poll_loop*,
        que foram movidas como estavam.
        """
        with self._lock:
            if self.reader is None:
                return 0
            missing = [b for b in names
                       if b not in self.reader.layout.bit_to_pos
                       and b not in self.reader.layout.not_findable
                       and not b.startswith("VB") and not b.isdigit()]
            if not missing:
                return 0
            self._stop_polling()
            try:
                ... # discover_bits + reindex bit_to_pos + save_cache (4177-4205)
            finally:
                self._start_polling()
            return added
```

`set_wanted_bits(owner, bits)` guarda `self._wanted[owner] = bits` e publica a **união** em `self.state.set_wanted_bits(...)`. É o que impede dois diagramas no mesmo 3xx de apagarem a lista um do outro. `release` do dono tira a entrada dele e republica.

- [ ] **Step 3: `LinkPool`**

```python
    def acquire(self, ip, port, owner):
        key = f"{ip}:{port}"
        with self._lock:
            link = self._links.get(key)
            if link is not None:
                link.owners.add(owner)
                return link, False
            if len(self._links) >= self.max_links:
                raise TooManyLinks(
                    f"limite de {self.max_links} conexoes simultaneas atingido; "
                    "desconecte outro diagrama antes")
            link = RelayLink(ip, port, self.logger)
            link.owners.add(owner)
            self._links[key] = link
            return link, True

    def release(self, link, owner):
        with self._lock:
            link.owners.discard(owner)
            if link.owners:
                self.logger.info("[glv] %s continua com %d diagrama(s)",
                                 link.key, len(link.owners))
                return
            self._links.pop(link.key, None)
        link.set_wanted_bits(owner, set())
        link.close()          # fora do lock: para o poll e fecha o telnet
        self.logger.info("[glv] %s fechado (ultimo diagrama saiu)", link.key)
```

- [ ] **Step 4: smoke sem relé**

```bash
.venv/bin/python - <<'PY'
import logging
from sellib.web.glv.link import LinkPool, TooManyLinks
log = logging.getLogger("t"); logging.basicConfig(level=logging.INFO)
p = LinkPool(log, max_links=2)
a, new_a = p.acquire("10.0.0.1", 23, "d1")
b, new_b = p.acquire("10.0.0.1", 23, "d2")   # mesmo rele: entra no mesmo link
assert a is b and new_a and not new_b and len(a.owners) == 2
p.release(a, "d1"); assert a.owners == {"d2"}
p.acquire("10.0.0.2", 23, "d3")
try:
    p.acquire("10.0.0.3", 23, "d4"); raise SystemExit("deveria ter estourado")
except TooManyLinks as e:
    print("teto ok:", e)
p.release(a, "d2"); p.release(a, "d2")       # duplo release e' inofensivo
print("ok")
PY
```

- [ ] **Step 5: Commit**

```bash
git add sellib/web/glv/link.py sellib/web/dashboard.py
git commit -m "Add RelayLink and LinkPool: one refcounted telnet per relay"
```

---

## Task 8: `GlvDiagram`

**Files:**
- Create: `sellib/web/glv/diagram.py`
- Modify: `sellib/web/dashboard.py:3917-4365` (esvaziar `_glv_session_loop`)

**Interfaces:**
- Consumes: `gle_pages.*`, `notes.NOTES`, `link.LinkPool`, `state.LiveState`, `relay_models.lookup`.
- Produces: `diagram.build_diagram(...) -> GlvDiagram`; `GlvDiagram.connect_async/disconnect/close/values/meta/tab`.

- [ ] **Step 1: `build_diagram`**

```python
def build_diagram(diagram_id, gle_path, relay_name, gle_name, ip, port,
                  relay_model, logger) -> GlvDiagram:
    """Monta um diagrama SEM tocar na rede: parse, render, indices, notas."""
```

Corpo = dashboard.py:4211-4258 (parse, `render_page` por página, `bits_per_page`, `analogs_per_page`, `var_index`, `analog_groups_meta`) + `derived_bits` (4223-4243) para `all_wanted_bits` + `notes = NOTES.get(note_key(relay_name))`. Sem `page_buttons` e sem `html_content`: a faixa de páginas passa a ser montada pelo JS a partir de `/meta`.

- [ ] **Step 2: conectar**

```python
    def connect_async(self, pool, defaults, logger) -> str:
        """Dispara a conexao numa thread e devolve o id do job.

        Nao pode bloquear a resposta: num FID sem cache o AsciiTargetReader
        leva minutos montando o mapa nome -> (linha, bit). Quem acompanha e' a
        barra de progresso, pelo job `glv-connect-<id>`.
        """
        with self._lock:
            if self.link is not None or self.status == "connecting":
                return self.job_id
            self.status = "connecting"
            self.error = ""
            self.idle.clear()
        threading.Thread(target=self._connect, name=f"glv-connect-{self.id}",
                         args=(pool, defaults, logger), daemon=True).start()
        return self.job_id
```

`_connect` (na thread):
1. `job = JobReporter(self.job_id)`; `job.stage("Conectando ao relé...", 8)`.
2. `link, is_new = pool.acquire(self.ip, self.port, self.id)` — `TooManyLinks` → `_fail(str(e))` e volta.
3. `if is_new: link.connect(acc_password=..., relay_model=self.relay_model, poll_interval=..., logger=..., job=job)` senão `link.ready.wait(timeout=180)` com `job.stage("Entrando na conexão existente...", 40)`.
4. `if link.error: pool.release(link, self.id); self._fail(link.error); return`.
5. `job.stage("Localizando bits do diagrama...", 70)`; `link.ensure_bits(self.all_wanted_bits, logger, job)`.
6. `self.notes.adopt_devid(link.devid, logger)` — a adoção, uma vez, na primeira conexão.
7. Sob `self._lock`: `self.link = link; self.status = "live"; self.error = ""`.
8. `job.finish("Conectado")`.

`_fail(reason)`: `self.status = "error"`, `self.error = reason`, `self.idle.error = reason`, `job.fail(reason)`, `logger.warning`. O diagrama **continua aberto e desconectado** — é o comportamento de hoje quando o setup falha, preservado.

- [ ] **Step 3: desconectar e fechar**

```python
    def disconnect(self, pool, logger) -> None:
        with self._lock:
            link, self.link = self.link, None
            self.status = "idle"
            self.error = ""
            self.idle.clear()      # nada na tela pode continuar mostrando a leitura antiga
        if link is not None:
            pool.release(link, self.id)
        REGISTRY.drop(self.job_id)

    def close(self, pool, logger) -> None:
        self.disconnect(pool, logger)
        self.svgs.clear()          # o maior peso em memoria
```

- [ ] **Step 4: `values`, `meta`, `tab`**

`values(page)` = o corpo de `DashboardHandler.do_GET` em `/values` (dashboard.py:2860-2905), lendo `self.state.snapshot()`, `self.bits_per_page`, `self.analogs_per_page`, `self.relay_model` — e, quando `self.link is not None`, chamando `self.link.set_wanted_bits(self.id, wanted)` em vez de `state.set_wanted_bits`.

`meta()` devolve `{id, relay, gle, ip, port, title, status, error, connected, pages: pages_meta, initial, var_index, analog_groups, notes_key}`.

`tab()` devolve o subconjunto que a faixa de abas precisa.

- [ ] **Step 5: smoke sem relé**

```bash
.venv/bin/python - <<'PY'
import logging
from pathlib import Path
from sellib.paths import SAMPLES_DIR
from sellib.web.glv.diagram import build_diagram
log = logging.getLogger("t"); logging.basicConfig(level=logging.INFO)
gle = next(SAMPLES_DIR.glob("*.xml"))
d = build_diagram("s-1", gle, "QPC1_LT1_UPC1", "GL1", "10.0.0.1", 23, None, log)
print(len(d.svgs), "paginas;", len(d.all_wanted_bits), "bits;", d.status)
v = d.values(d.pages_meta[0][1])
assert all(x is None for x in v["digitals"].values())   # desconectado = indeterminado
print("ok")
PY
```

- [ ] **Step 6: Commit**

```bash
git add sellib/web/glv/diagram.py
git commit -m "Add GlvDiagram: an open diagram that connects and disconnects on demand"
```

---

## Task 9: rotas, sessão e `main()`

**Files:**
- Create: `sellib/web/glv/handler.py`
- Modify: `sellib/web/glv/__init__.py`, `sellib/web/dashboard.py` (apagar 2791-3095, 3243-3722 restantes, 3724-4365; ajustar `main()`)

**Interfaces:**
- Consumes: tudo das Tasks 4-8.
- Produces: `glv.build_glv_handler(logger, sessions, defaults) -> type`; `glv.GlvDefaults`.

- [ ] **Step 1: `_GlvSession` e a fábrica**

```python
def build_glv_handler(logger, sessions, defaults: GlvDefaults) -> type:
    pool = LinkPool(logger, max_links=defaults.max_links)

    def _factory():
        st = _GlvSession()
        if defaults.gle_file:          # --gle semeia um diagrama na sessao nova
            ...
        return st

    class Handler(SessionHandler):
        session_key = "glv"
        state_factory = staticmethod(_factory)
        server_sessions = sessions
```

- [ ] **Step 2: rotas**

Implemente a tabela da seção "Rotas". Notas de implementação:
- `_diagram()` resolve `?d=<id>` no `self.sess()` e responde 404 `{"error":"diagrama não encontrado"}` quando não acha; sem `d`, usa `sess.active`.
- `POST /diagrams` valida `_valid_ipv4`, aplica `defaults.max_diagrams` (409 com motivo em português), chama `build_diagram`, insere em `diagrams`/`order`, marca `active` e responde `{"id": ...}`. Quando `defaults.no_relay`, o IP é opcional e o botão Conectar vem desabilitado.
- `POST /connect` responde **202** `{"job": diagram.job_id}` logo depois de disparar a thread; nunca espera a conexão.
- `POST /diagrams/close` chama `diagram.close(pool, logger)` e tira das listas.
- `/rdb-upload` chama `process_upload(data, filename)` — sem `base_dir`, porque agora é o cache por conteúdo. `RDBS_DIR` sai do GLV.
- `GET /` serve `dashboard.html` com `${BOOT_JSON}` = `{"diagrams": [...], "active": ..., "no_relay": ...}`.
  Sem nenhum diagrama aberto, redireciona (302) para `<prefixo>/novo` — a casca com zero abas não tem o que mostrar.

- [ ] **Step 3: fechar diagramas de sessão expirada**

Em `SessionManager._discard_dir` não dá para saber de diagramas. Registre um hook: `sessions.on_expire = lambda sess: _close_session_diagrams(sess, pool, logger)`, chamado dentro de `sweep()` para cada sessão expirada, antes do `rmtree`. Sem isso, a sessão some e a conexão fica pendurada até o processo morrer.

- [ ] **Step 4: `dashboard.py` enxuto**

Apague `DashboardHandler`, `GlvMount`, `_glv_activate`, `LANDING_HTML`, `run_landing_page`, `_glv_session_loop` e os imports que só eles usavam. Em `main()`:

```python
    glv_defaults = glv.GlvDefaults(
        ip=cfg.get("tcp", "ip_address", fallback=""),
        port=cfg.getint("tcp", "port", fallback=23),
        acc_password=cfg.get("auth", "acc_password", fallback="OTTER"),
        poll_interval=args.poll_interval,
        relay_name=cfg.get("relay", "name", fallback="(relé)"),
        gle_file=args.gle,
        no_relay=args.no_relay,
        max_links=cfg.getint("web", "glv_max_links", fallback=4),
        max_diagrams=cfg.getint("web", "glv_max_diagrams", fallback=8),
    )
    ...
        Mount("/glv", glv.build_glv_handler(logger, sessions, glv_defaults),
              "Graphical Logic Viewer"),
```

e apague a thread `glv-session` e a linha de log "O GLV e' sessao unica". Acrescente `[web] glv_max_links = 4` / `glv_max_diagrams = 8` ao `config.ini`, com o comentário explicando que N diagramas conectados são N threads batendo na rede da subestação.

- [ ] **Step 5: verificação**

`python3 app.py --web`. Abra `/glv/` → seletor → suba um RDB → escolha um GLE. O diagrama abre **desconectado**, tudo amarelo hachurado. Abra mais dois. `curl -s localhost:8765/glv/diagrams` mostra os três. Clique em Conectar sem relé na rede: o badge fica vermelho com o motivo e o diagrama continua aberto.

- [ ] **Step 6: Commit**

```bash
git add sellib/web/glv sellib/web/dashboard.py config/config.ini
git commit -m "Serve N GLV diagrams per session from sellib/web/glv/handler.py"
```

---

## Task 10: a tela — faixa de abas e botão Conectar

**Files:**
- Modify: `sellib/web/glv/templates/dashboard.html`

**Interfaces:**
- Consumes: `/diagrams`, `/meta?d=`, `/pages/<safe>?d=`, `/values?d=&page=`, `/connect?d=`, `/disconnect?d=`.

- [ ] **Step 1: faixa de abas**

Nova área de grid `tabs`, entre `header` e `pages`:

```css
  body { grid-template-areas: "header header" "tabs tabs" "pages pages" "viewer panel"; }
  #tabs { grid-area: tabs; display: flex; gap: var(--s1); padding: var(--s1);
          background: var(--surface); border-bottom: 1px solid var(--border-ctl);
          overflow-x: auto; }
  #tabs .tab { display: flex; align-items: center; gap: var(--s1);
               background: var(--surface-2); color: var(--text);
               border: 1px solid var(--border-ctl); border-radius: var(--radius);
               padding: var(--s1) var(--s2); font-size: var(--fs-2); cursor: pointer; }
  #tabs .tab.active { background: var(--accent-strong); border-color: var(--accent-strong);
                      color: var(--accent-fg); }
  #tabs .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text-3); }
  #tabs .tab[data-status="live"] .dot { background: var(--ok); }
  #tabs .tab[data-status="connecting"] .dot { background: var(--warn); }
  #tabs .tab[data-status="error"] .dot { background: var(--err); }
```

Só tokens — nenhuma cor literal nova. O `8px` da bolinha e o `50%` são geometria, não paleta.

- [ ] **Step 2: cabeçalho**

Tire `#back-to-landing` ("Trocar GLE") — abrir outro diagrama tomou o lugar dele. Ponha, ao lado de "Notas":

```html
  <button id="conn-toggle" type="button" title="Conectar ou desconectar este diagrama">Conectar</button>
```

O rótulo alterna com o status (`Conectar` / `Conectando...` desabilitado / `Desconectar`). O `<h1>` passa a mostrar o relé do diagrama ativo, atualizado no `switchDiagram`.

- [ ] **Step 3: JS**

- `PAGES`, `VAR_INDEX`, `analogGroupsMeta` e `currentPage` deixam de ser `const` do template e passam a ser preenchidos por `applyMeta(meta)`.
- `switchDiagram(id)`: `POST /diagrams/activate?d=` → `GET /meta?d=` → `applyMeta` → monta `#pages` → `loadPage(meta.initial)` → `loadGroupState()`, `loadNotes()`, `loadHighlights()` → `zoomCtl.fitWidth()`. **Sem `location.reload()`.**
- `withD(url)`: acrescenta `d=<ativo>` em toda chamada (`/values`, `/pages/`, `/note`, `/group-state`, `/highlights`, `/debug/analogs`).
- `refreshTabs()`: `GET /diagrams` a cada 2 s; redesenha a faixa e o rótulo do botão Conectar. É também o que faz o badge de erro aparecer quando a conexão falha em segundo plano.
- `#conn-toggle`: `SelProgress.post('/connect?d=...')`, `SelProgress.track(job)`; ao terminar, `refreshTabs()`. Desconectar é um POST simples seguido de `refreshTabs()` — os valores voltam a `null` no `/values` seguinte e o `evaluatePage` já pinta indeterminado.
- Aba "+": `location.href = './novo'` (link entre páginas, prefixo à mão via `./`).
- Fechar aba: `POST /diagrams/close?d=` e `switchDiagram` para a vizinha; se não sobrar nenhuma, vai para `./novo`.

- [ ] **Step 4: verificação no navegador**

Percorra o "Pronto quando" 1-6 da spec (menos o que depende de relé). Confira os três temas nas nove telas e rode `grep -c "^\s*--bg:" sellib/web/*.py` (tem que ser 1, em `theme.py`).

- [ ] **Step 5: Commit**

```bash
git add sellib/web/glv/templates/dashboard.html
git commit -m "Add the diagram tab strip and the per-diagram connect button"
```

---

## Task 11: documentação

**Files:**
- Modify: `docs/ENGINEERING-NOTES.md`, `app.py:16`

- [ ] **Step 1: `docs/ENGINEERING-NOTES.md`**

- Em "Project layout", acrescente `sellib/web/glv/` com uma linha por módulo, e diga que `dashboard.py` ficou com a home e o `main()`.
- **Apague** a gotcha "GLV is deliberately NOT per-user" e ponha no lugar: a lista de diagramas é por sessão; a conexão é do processo, chaveada por `ip:porta` e contada por referência; teto de `[web] glv_max_links`.
- Reescreva a gotcha "GLV is the only two-page tool": não há mais `_glv_session_loop` nem `GlvMount.active`; o seletor é a página `/glv/novo`.
- Em "Never write uploads to a shared dir", registre que os RDBs agora vão para `cache/rdb/<sha256>/`, que é compartilhado **de propósito** e read-only para as ferramentas, e que as saídas derivadas continuam no diretório da sessão (é o que o sandbox do `/download` permite).
- Acrescente `RDB_CACHE_DIR` e `GLV_TEMPLATES_DIR` à lista de constantes de `paths.py`.

- [ ] **Step 2: `app.py`**

O desenho de árvore na linha 16 diz `dashboard.py (HOME + LANDING + GLV)`. Corrija para `dashboard.py (HOME + main)` e acrescente `web/glv/`.

- [ ] **Step 3: Commit**

```bash
git add docs/ENGINEERING-NOTES.md app.py
git commit -m "Document the glv package and the RDB content cache"
```

---

## O que não dá para verificar sem relé

Sem hardware, nenhuma destas coisas é exercitável, e o plano não finge que são:

- **O polling de verdade.** Tudo que se vê sem relé é indeterminado; bits em verde/cinza, analógicos com valor e a idade do snapshot no badge dependem de um relé respondendo.
- **A descoberta de bits do `AsciiTargetReader`** (`discover_via_map_bl`, `discover_all_rows`, `discover_bits`) e o `ensure_bits` novo, que para e sobe o polling em volta dela.
- **O compartilhamento real de uma sessão telnet entre dois diagramas.** A contagem de referência é testável em memória (Task 7, Step 4); que o relé aguente as duas leituras pelo mesmo socket, não.
- **O comportamento do relé com várias sessões simultâneas** — quantos telnets um SEL aceita antes de recusar, e o que ele responde ao recusar. O teto de 4 é um palpite defensivo, não uma medição.
- **A adoção dos arquivos de nota pelo DEVID**, que só acontece na primeira conexão bem-sucedida.

---

## O que mudou na execução

Três coisas apareceram ao exercitar o código e não estavam no plano:

1. **Watchdog no setup da conexão** (`[web] glv_setup_timeout`, 60 s). Um IP que
   aceita TCP e nunca responde — port forward morto, switch — deixava o login do
   `selprotopy` lendo para sempre. Antes isso travava o único GLV; agora seguraria
   também uma das quatro vagas do teto. O watchdog fecha o socket, e cobre só o
   setup: a descoberta de bits leva minutos de vontade própria.

2. **`connect()` roda na thread do `RelayLink`, não na de quem pediu.** Fechar o
   socket nem sempre acorda o `selprotopy` (ele engole a exceção e tenta de novo).
   Com a thread de quem pediu presa lá dentro, fechar o diagrama no meio da conexão
   deixava a referência pendurada para sempre. Agora quem trava é uma thread que
   não é dona de nada; os diagramas esperam em `link.ready`, que o watchdog seta,
   e o watchdog também tira o link do pool (`LinkPool.abandon`).

3. **Geração de tentativa no `GlvDiagram`.** Desconectar ou fechar incrementa
   `_gen`, o que invalida a tentativa em voo: ela solta a referência em vez de
   anexá-la a um diagrama que o usuário já mandou parar, e um erro que chegue
   depois não pinta de vermelho um diagrama em repouso. Sem isso, `disconnect()`
   não tinha o que soltar durante a janela em que `self.link` ainda é `None`.

Também: `tools/check_settings_compare.py` passou a procurar extrações no cache por
conteúdo, com `rdbs/` como fallback.
