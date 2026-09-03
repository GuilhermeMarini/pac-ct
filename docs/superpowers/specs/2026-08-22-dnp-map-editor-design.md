# DNP Map Editor — desenho

Data: 2026-08-22 · Branch de partida: `tema-tokens`

Editor de mapas DNP3 de relés SEL: abre um RDB, escolhe o IED, edita os pontos
do mapa numa tabela, e reexporta o RDB com as alterações aplicadas. Sexta
ferramenta do toolkit, montada em `/dnp-map/`.

## 1. O que é um mapa DNP dentro do RDB

Cada relé do RDB tem um ou mais `SET_D<n>.TXT` no seu storage OLE — **um por
sessão DNP**. Um SEL-411L tem D1..D5, um SEL-751 tem D1..D3, um SEL-2440 tem
D1..D3. No RDB de referência (`rdbs/substation_demo.rdb`) as
cinco sessões do `QPC1_LT1_UPC1_SECC_GAMA` são byte-idênticas entre si.

Formato:

```
[INFO]<CR><LF>
RELAYTYPE=SEL-411L-A<CR><LF>
FID=...<CR><LF>
BFID=...<CR><LF>
PARTNO=...<CR><LF>
[D1]<CR><LF>
MINDIST,"1.0"<FS><CR><LF>
BI_1,"PSV22"<FS><CR><LF>
...
```

Onde `<FS>` é `0x1C` (File Separator), presente **dentro** da linha, entre o
valor e o CRLF. As linhas de dados são `CHAVE,"VALOR"`.

Blocos de pontos observados:

| Bloco | Chaves | 411L | 751 | 2440 |
|---|---|---|---|---|
| Binary Input | `BI_n` | 400 | 100 | 200 |
| Binary Output | `BO_n` | 160 | 32 | 64 |
| Analog Input | `AI_n`, `AI_SCAn`, `AI_DBDn` | 200 (+200+200) | 100 | 200 |
| Analog Output | `AO_n` | 100 | 32 | 32 |
| Control | `CO_n`, `CO_DBDn` | 20 (+20) | 32 | 32 |

As colunas extras entre parênteses são as chaves auxiliares (`AI_SCAn`,
`AI_DBDn`, `CO_DBDn`), que só o 411L traz no RDB de referência. Nenhum bloco é
assumido presente: a interface só mostra os que o arquivo tem.

Fora dos blocos: `MINDIST` e `MAXDIST` (só 4xx), que configuram a sessão e não
são pontos do mapa.

Dois detalhes que um parser ingênuo erra:

1. O `0x1C` não é whitespace. O `sellib/parsers/sel_settings.py` atual não o
   trata e deixa o byte colado no valor. Este módulo **não** reaproveita aquele
   parser.
2. O padding do índice varia por família: o 411L escreve `BI_1`, o 751 e o 2440
   escrevem `BI_00`. A chave é preservada literalmente, nunca reconstruída a
   partir do índice.

Valor de slot livre também varia: o 411L usa `""`, o 751 e o 2440 usam `"NA"`.

## 2. Fluxo do usuário

O upload passa por `rdb.process_upload()`, que extrai para o cache de conteúdo
compartilhado `cache/rdb/<sha256>/`. A ferramenta não faz extração própria.

1. Envia um RDB (ou escolhe um já extraído).
2. Escolhe um relé, entre os que têm `SET_D*`.
3. Escolhe a sessão DNP (`D1..Dn`).
4. Edita o mapa numa tabela com abas por bloco (BI / BO / AI / CO).
5. Opcionalmente aplica o mapa editado às demais sessões do mesmo relé.
6. Volta à lista e repete para outros relés — as edições ficam pendentes na
   sessão do usuário.
7. Exporta **um** RDB com todas as alterações acumuladas.

## 3. Arquitetura

Camadas, seguindo o que o projeto já faz. Nada de web dentro de parser, nada
de OLE dentro de handler.

| Arquivo novo | Responsabilidade |
|---|---|
| `sellib/parsers/set_dnp.py` | Parse/serialize de um `SET_D*.TXT`. Puro. |
| `sellib/parsers/ole_rebuild.py` | Escreve um OLE novo a partir de um existente, com streams substituídos. Puro. |
| `sellib/core/wordbits.py` | Registry sobre `data/wordbits/*.json`, espelhando `core/relay_models.py`. |
| `sellib/web/dnp_map/__init__.py` | `load_template()`, como em `glv`. |
| `sellib/web/dnp_map/model.py` | Modelo de edição por sessão de usuário. |
| `sellib/web/dnp_map/export.py` | Orquestra o export híbrido. |
| `sellib/web/dnp_map/handler.py` | Rotas + `build_dnp_map_handler(logger, sessions)`. |
| `sellib/web/dnp_map/templates/landing.html` | Upload + escolha de relé. |
| `sellib/web/dnp_map/templates/editor.html` | A tabela. |
| `data/wordbits/SEL-*.json` | Listas de word bits permitidos. |
| `tools/wordbits_from_glv_cache.py` | Semeia uma dessas listas a partir de um `cache/<FID>.json`. |
| `tools/check_set_dnp_roundtrip.py` | Verificação de round-trip e do rebuild. |

Alterações em arquivo existente, ambas de uma linha:

- `sellib/web/themes/items.py` — `Tool("dnp-map", "/dnp-map/", "Editor de Mapa DNP", "Mapa DNP", "pontos DNP3 do relé", "…", "RDB")`.
- `sellib/web/mount.py` — montar `build_dnp_map_handler` em `/dnp-map/`.

## 4. `parsers/set_dnp.py`

Fidelidade de round-trip é requisito, não conveniência: estes bytes voltam
para o arquivo de ajustes de um relé de proteção.

```python
@dataclass(frozen=True)
class RawLine:
    key: str          # "BI_1", "MINDIST", ou "" para linha sem chave
    value: str        # sem as aspas externas
    quoted: bool      # o valor vinha entre aspas
    terminator: bytes # b"\x1c\r\n" ou b"\r\n"
    raw: bytes        # a linha original, usada quando nada mudou

@dataclass
class SetDnpFile:
    info: dict[str, str]      # RELAYTYPE, FID, BFID, PARTNO
    section: str              # "D1"
    lines: list[RawLine]      # ordem de leitura, tudo preservado

    def points(self) -> list[DnpPoint]: ...
    def set_value(self, key: str, value: str) -> None: ...
    def serialize(self) -> bytes: ...
```

```python
@dataclass
class DnpPoint:
    kind: str          # "BI" | "BO" | "AI" | "AO" | "CO"
    index: int
    key: str           # a chave literal, com o padding original
    value: str
    sca: str | None    # AI_SCAn, só AI
    dbd: str | None    # AI_DBDn / CO_DBDn
```

`AI_SCAn` e `AI_DBDn` **não** são pontos próprios: dobram como colunas da
linha `AI_n`. Idem `CO_DBDn` sobre `CO_n`.

Linhas que não são pontos (`[INFO]`, `[D1]`, `MINDIST`, `MAXDIST`, e qualquer
chave desconhecida) atravessam intactas e aparecem read-only na interface.

Critério de aceite: para todo stream `SET_D*` de todo RDB em `rdbs/`,
`parse(b).serialize() == b`.

## 5. `parsers/ole_rebuild.py`

```python
def rebuild(src: Path, dst: Path, replacements: dict[tuple[str, ...], bytes]) -> None
```

Lê a árvore inteira de `src` com `olefile` e escreve um CFBF v3 novo em `dst`,
com os streams de `replacements` trocados (tamanho livre) e todo o resto
copiado byte a byte.

- Setor 512 B, mini-stream 64 B, FAT / DIFAT / mini-FAT reconstruídos.
- Directory entries na ordem canônica do CFBF: `(len(nome), nome.upper())`.
  Árvore rubro-negra emitida com todos os nós pretos.
- CLSID e timestamps das storages e do root preservados do original.
- Streams copiados um a um. Um RDB de 140 MB nunca entra inteiro em memória.

**Autoverificação obrigatória**, dentro de `rebuild()`: ao terminar, reabre
`dst` com `olefile`, percorre todos os streams e compara com `src`, exceto os
substituídos. Qualquer divergência levanta exceção e apaga `dst`. Um bug no
writer tem que virar "o export falhou", nunca "RDB corrompido em silêncio".

## 6. `web/dnp_map/export.py` — o híbrido

```python
def export(rdb: RdbInfo, edits: dict[str, dict[str, dict[str, str]]],
           out_dir: Path, job) -> ExportResult
```

`edits` é `{relay: {sessao: {chave: valor}}}` — só as diferenças.

1. Para cada `(relay, sessão)` com edições, carrega o stream original do RDB,
   aplica as trocas, serializa.
2. **Se todos os streams novos têm exatamente o tamanho dos originais**:
   `shutil.copyfile` do RDB e `olefile.OleFileIO(write_mode=True)` +
   `write_stream` por stream — o caminho já validado em
   `vb_updater.update_rdb_with_scd_descs_batch`. Saída byte-idêntica fora dos
   streams tocados. Rearranjo puro dentro de um bloco cai sempre aqui: mover
   valores conserva o multiconjunto de bytes.
3. **Senão**: `ole_rebuild.rebuild()` com todos os streams alterados de uma vez.

Não há tentativa de "encher com espaços até caber". A regra é o tamanho exato
ou a reconstrução — nada de apostar no que o QuickSet tolera.

Os `SET_D*.TXT` editados ficam **sempre** disponíveis para download avulso,
como plano B caso o QuickSet recuse o RDB reconstruído.

Estágios do job (`self.job()` / `.stage()`, como as demais ferramentas):
`copiar` → `gravar` ou `reconstruir` → `verificar`.

Saída em `self.sdir("out")`, nome `<nome do RDB>_dnp_updated.rdb`. `/download`
continua restrito aos diretórios da sessão.

### Risco declarado

Só o AcSELerator QuickSet diz se aceita um RDB reconstruído. A autoverificação
prova que o arquivo é um OLE válido e que o conteúdo bate; não prova que o
QuickSet o abre. Precisa de um teste manual. Se recusar, o fallback é o caminho
2 (mesmo tamanho) mais o export dos `.txt`, e perde-se o caso de mapa que
cresce.

## 7. `core/wordbits.py` e `data/wordbits/`

```json
{
  "schema_version": 1,
  "model": "411L",
  "model_aliases": ["411L-A"],
  "source": { "fid": "SEL-411L-A-R133-V2-Z022004-D20251103", "harvested_at": "2026-08-22" },
  "always_valid": ["", "NA", "0", "1"],
  "bits": ["ENABLED", "50P1P", "51T01", "LOP", "PSV01"],
  "patterns": [{ "re": "^ASV[0-9]{3}$", "label": "SELOGIC analógica" }]
}
```

`bits` é a lista primária — enumeração explícita, porque é o que
`tools/wordbits_from_glv_cache.py` produz a partir das chaves de `bit_to_pos`
de um `cache/<FID>.json` (o mapa do Relay Word que o GLV descobre num relé
vivo). `patterns` é a válvula de escape para faixas grandes demais para
enumerar. O script **funde** com o arquivo existente, união de `bits`, para
não atropelar edições à mão.

API:

```python
def lookup(relaytype: str) -> WordbitSet | None
class WordbitSet:
    def check(self, value: str) -> str   # "ok" | "desconhecido"
```

`check()` julga um valor isolado. `duplicado` não é decisão dela: é calculado
pelo handler ao montar o payload de `/map`, varrendo cada bloco em busca do
mesmo bit em mais de um índice.

Resolução por modelo com aliases, como `core/relay_models.lookup()`. **Sem
arquivo para o modelo, a validação é desligada** e a página mostra um aviso
dizendo isso. A validação nunca bloqueia o export — é o requisito central.

Dois tipos de aviso, ambos consultivos:

- `desconhecido` — o valor não está em `bits`, não casa com `patterns`, não
  está em `always_valid`.
- `duplicado` — o mesmo bit em dois índices DNP do mesmo bloco. Legal em DNP,
  quase sempre engano na prática.

Contagens aparecem no cabeçalho da aba (`BI 3⚠`), a linha ganha um marcador, e
a confirmação de export informa o total antes de prosseguir.

## 8. Interface

`/dnp-map/` → landing (upload ou RDB já extraído) → lista de relés com
`SET_D*` → editor.

Editor:

- **Cabeçalho** — relé, `RELAYTYPE`, seletor `D1..Dn` marcando quais sessões
  são byte-idênticas entre si, e o botão *aplicar este mapa às demais sessões*.
  `MINDIST`/`MAXDIST` aqui, read-only.
- **Abas** BI / BO / AI / CO, só os blocos que o arquivo tem.
- **Tabela** — `#` (índice DNP) · campo da variável · colunas `SCA`/`DBD` nas
  AI e `DBD` nas CO · marcador de aviso.
- **Arrastar** para mover uma variável para outro índice, confinado ao bloco.
  **Soltar a linha 5 sobre a 9 troca as duas**, não insere-e-desloca: o índice
  DNP é contrato com o mestre SCADA, e inserir renumeraria todos os pontos
  entre os dois.

Edições são diffs, não documentos: a sessão guarda
`{relay: {sessao: {chave: valor}}}` e o cliente posta a cada mudança
(com debounce). Sair do editor para outro relé e voltar preserva tudo, e o
export em lote já recebe a entrada pronta.

Um painel *alterações pendentes* lista os relés sujos e carrega o botão de
exportar.

### Rotas

Absolutas, como manda a convenção de montagem. `self.mount_prefix` entra à mão
só no `<a href download>`.

```
GET  /                    landing
GET  /relays?rdb=         relés com SET_D
GET  /editor?rdb=&relay=&d=
GET  /map?rdb=&relay=&d=  JSON: pontos + avisos
POST /upload              RDB (via SelProgress.upload)
POST /edit                aplica diffs ao estado da sessão
POST /copy-session        Dn -> demais sessões
POST /export              gera o RDB (job com estágios)
GET  /download?f=         restrito a self.sdir("out")
```

As páginas carregam `<!--NAV:dnp-map-->` para o `mount.py:_resolve_markup()`
renderizar a navegação certa por tema. A ferramenta não define cor, raio,
família de fonte nem padding próprios — só tokens.

O upload passa por `SelProgress.upload()`, nunca `fetch()`: RDBs têm 40–140 MB
e só `XMLHttpRequest.upload.onprogress` reporta progresso.

## 9. Verificação

Não há framework de testes no projeto e o docs/ENGINEERING-NOTES.md pede para não introduzir um
sem combinar. A parte automatizada é um script:

`tools/check_set_dnp_roundtrip.py`

- Modo padrão: percorre todo RDB em `rdbs/`, afirma
  `parse(b).serialize() == b` para todo stream `SET_D*`, imprime relatório.
- `--rebuild <rdb>`: roda `ole_rebuild` sem nenhuma edição e compara todos os
  streams com a origem. Um writer que não reproduz o arquivo byte a byte não
  tem o que fazer escrevendo ajustes.

O resto é manual, como o projeto espera: `python3 app.py --web`, exercitar a
ferramenta no navegador, e uma passagem pelo QuickSet num RDB reconstruído.

## 10. Fora de escopo

- `set_DNPA.txt` / `set_DNPB.txt` — configuração de porta DNP, não o mapa.
- Validar que um bit existe no FID *daquele* relé; a validação é por modelo.
- Editar `MINDIST`/`MAXDIST` ou qualquer ajuste fora dos blocos de pontos.
- Criar sessões DNP novas, ou blocos de pontos que o arquivo não tem.
