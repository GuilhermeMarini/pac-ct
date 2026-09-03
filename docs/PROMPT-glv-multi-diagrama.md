# Prompt — GLV multi-diagrama, com conectar/desconectar

Cole o bloco abaixo numa sessão nova, na raiz do projeto.
Ponto de partida: branch `tema-tokens`, árvore limpa (`567c439 V1.0.0`).

---

Reestruture o Graphical Logic Viewer para **abrir vários diagramas ao mesmo tempo**,
cada um começando **desconectado**, com **botão de conectar/desconectar por diagrama**.
No caminho, extraia o GLV de `dashboard.py` para um pacote próprio.

O desenho já foi decidido numa sessão de brainstorming anterior. As decisões abaixo
não estão em aberto — não as reabra, implemente-as. O que está em aberto é *como*,
e é isso que você vai me mostrar antes de escrever código.

## O que o GLV é hoje

- `pacct/web/dashboard.py` tem 4.462 linhas. A home são 147 (`HOME_HTML` +
  `build_home_handler`) e o `main()` são 97. **Todo o resto, ~4.150 linhas, é GLV.**
- `DashboardHandler` guarda o diagrama inteiro em **atributos de classe**: `state`,
  `pages_meta`, `svgs`, `html`, `bits_per_page`, `analogs_per_page`, `relay_model`,
  `analog_groups_meta`, `group_state_devid`, `group_state`, `note_relay`, `note_pages`,
  `highlights`, `return_event`. Um diagrama por processo.
- `_glv_session_loop()` é uma thread que **bloqueia**: roda a landing (que por sua vez
  bloqueia num `threading.Event` até o usuário escolher o GLE), **conecta ao relé**, faz a
  descoberta de bits, renderiza os SVGs, sobe a thread de polling, publica o dashboard e
  espera o "Trocar GLE" para derrubar tudo.
- `GlvMount.active` alterna a classe do handler entre a landing e o dashboard.
- A conexão acontece no *setup*, antes de a tela existir. O único jeito de ver um diagrama
  sem conectar é o `--no-relay`.
- Rotas do dashboard: `/`, `/debug/analogs`, `/values`, `/pages/<safeId>`, `/group-state`,
  `/note`, `/highlights`. Da landing: `/landing-state`, `/rdb-upload`, `/rdb-select`.
- `app.py` faz `from pacct.web.dashboard import main as web_main` — esse ponto de entrada
  tem que continuar existindo.

## Decisões já tomadas

1. **N diagramas, N relés, N conexões.** Cada diagrama aberto é um par (GLE + relé)
   independente, com telnet e polling próprios. Dá para acompanhar três relés de baias
   diferentes ao mesmo tempo.
2. **Lista de diagramas por visitante; conexão compartilhada por relé.** A lista vive na
   sessão (cookie `selsid`, como as outras quatro ferramentas). A conexão com um relé é
   única no processo e **contada por referência**: o segundo diagrama que pedir o mesmo
   `ip:porta` entra na conexão existente, e ela só cai quando o último solta. Isso protege
   o equipamento — relé SEL aceita poucas sessões simultâneas.
3. **Faixa de abas dentro da página.** Um `/glv/` só, com uma faixa de abas no topo (uma
   por diagrama aberto) acima da faixa de páginas do GLE. Trocar de diagrama não recarrega
   a página: busca os metadados do diagrama e re-renderiza a faixa de páginas e o viewer.
4. **Notas, marca-texto e checkboxes de grupo passam a ser chaveados pelo nome do relé no
   RDB**, não pelo devid. Na primeira conexão, se existir arquivo pelo devid e não pelo
   nome, ele é **adotado uma vez**. A chave nunca troca no meio da sessão e nada que já foi
   escrito se perde.
5. **Desconectar devolve tudo a indeterminado.** Bits voltam ao amarelo hachurado e
   analógicos a "-", como um diagrama recém-aberto. A tela nunca mostra um valor que não
   está sendo lido agora. `LiveState` não tem método para isso hoje — precisa de um.
6. **O GLV sai de `dashboard.py` para o pacote `pacct/web/glv/`.**
7. **Os templates viram arquivos `.html` de verdade**, lidos no import. São 2.539 das 4.150
   linhas, e ~1.400 delas são JavaScript (zoom, pan, marca-texto, busca de variável,
   notepad) que hoje nenhum editor colore e nenhum linter olha. A mecânica não muda —
   continuam os mesmos `.replace("${PAGES_JSON}", ...)`. Entra um `GLV_TEMPLATES_DIR` no
   `paths.py`, porque o `ENGINEERING-NOTES.md` proíbe `Path(__file__).parent` solto.

## Parte 1 — cache de RDB por sha256 (faça primeiro, commits próprios)

Hoje `rdb.process_upload(data, filename, base_dir)` grava em `<base_dir>/<nome>.rdb` e
extrai em `<base_dir>/extracted/<stem>/`, e só reaproveita se **nome e sha256** baterem
naquele `base_dir`. Como cada ferramenta passa o próprio `self.sdir("rdbs")`, cada sessão
guarda a própria cópia de 40–140 MB e reextrai tudo.

Passe a extrair num cache de processo chaveado pelo conteúdo: `cache/rdb/<sha256>/`.
Dois arquivos iguais *são* o mesmo, então colisão por nome deixa de existir e o
reaproveitamento passa a valer entre sessões e entre reinícios.

Quatro consequências que **têm que entrar no mesmo trabalho**, senão isso quebra coisa:

- **`info.rdb_path.name` é o nome que aparece na tela** em `gle_exporter.py:661,1217`,
  `settings_compare.py:129,519,1707`, `vb_updater.py:893,2288` e `dashboard.py:3759,3816`.
  Guardando por hash, todos passariam a mostrar o nome de quem subiu primeiro. Acrescente
  `display_name` ao `RdbInfo` e use nesses pontos.
- **VB Updater e Exportador gravam os derivados ao lado do RDB de origem**
  (`_with_suffix_before_ext(rdb.rdb_path, "_comments_updated")` em `vb_updater.py:2005,
  2019,2095` e `gle_exporter.py:1284,1344`). Com o RDB num cache compartilhado, esses
  arquivos gerados cairiam lá dentro — e o `/download` é sandboxado à sessão
  (`is_within(target, (self.sdir("rdbs"), self.sdir("xlsx")))`), então ou o download quebra,
  ou abrir o cache no sandbox deixa um visitante baixar o arquivo gerado por outro.
  As saídas derivadas passam a ir para o diretório da sessão.
- **Nada varre esse cache.** Os diretórios de sessão têm TTL; um cache por hash não tem
  dono e cresce para sempre. Precisa de política própria — proponha uma e me diga qual
  (sugestão: teto de tamanho ou idade, chave em `[web]` no `config.ini`, varrido pelo mesmo
  sweeper das sessões).
- `pacct/matchers/relay_scd.py:339` chama `process_upload` fora da web, com `base_dir`
  próprio, e tem um comentário que descreve o layout antigo. Mantenha funcionando.

Verificação: subir o mesmo RDB em duas sessões diferentes e ver a segunda reaproveitar;
gerar um RDB com comentários atualizados no VB Updater e baixá-lo.

## Parte 2 — extrair `pacct/web/glv/`

`dashboard.py` fica com a home e o `main()` (~250 linhas) e continua sendo o ponto de
entrada de `app.py`. Corte sugerido, ajuste se enxergar melhor:

```
pacct/web/glv/
  __init__.py      build_glv_handler() + o que o main() precisa
  state.py         LiveState + clear()
  poll.py          poll_loop, poll_loop_fastmeter, poll_loop_tar,
                   _read_fast_meter_analogs        (hoje dashboard.py:106-481)
  gle_pages.py     list_pages, collect_bit_names,
                   collect_analog_symbols_per_page,
                   collect_bits_per_page           (hoje dashboard.py:482-598)
  notes.py         notas, marca-texto e grupos em cache/ (hoje 2678-2790)
  link.py          RelayLink + LinkPool + setup_relay          (novo)
  diagram.py       GlvDiagram: montagem, conectar, desconectar (novo)
  handler.py       as rotas (era DashboardHandler + LandingHandler + GlvMount)
  templates/
    dashboard.html  era HTML_TEMPLATE   (hoje dashboard.py:599-2677)
    landing.html    era LANDING_HTML    (hoje dashboard.py:3243-3705)
```

As três `poll_loop*` já recebem `(client, [reader,] state, interval, logger, stop_event)` —
movem como estão, sem mudança de assinatura.

## Parte 3 — o modelo

- **`RelayLink`** — uma conexão telnet com um relé, chaveada por `ip:porta`. Dona do
  `SELClient`, do `AsciiTargetReader`, da thread de polling e do stop event. `acquire()` /
  `release()` com contagem de referência; ao chegar a zero, para o polling e fecha o telnet.
- **`LinkPool`** — mapa `ip:porta -> RelayLink` de processo, com lock.
- **`GlvDiagram`** — um diagrama aberto: caminho do GLE, nome do relé, `relay_model`,
  `pages_meta`, `svgs`, `bits_per_page`, `analogs_per_page`, `analog_groups_meta`,
  `LiveState`, notas/marcações, e um `RelayLink` ou `None`. É o que hoje são os atributos
  de classe do `DashboardHandler`.
- **Estado de sessão do GLV** — `dict[id, GlvDiagram]` + qual está ativo, via
  `SessionHandler.sess()`, como as outras ferramentas.

**`setup_relay()` deixa de ler o `config.ini`.** Hoje ela faz `cfg.get("tcp","ip_address")`
e o loop de sessão *escreve* nesse mesmo `cfg` quando o usuário digita um IP na landing
(`cfg.set("tcp", "ip_address", ip_override)`). Com um relé é invisível; com dois diagramas,
abrir o segundo apontando para outro IP reescreve o IP do primeiro — o diagrama 1 continua
dizendo na tela que é o relé A e reconecta no relé B. Passe `(ip, port, auth, logger)`
explícitos; o `config.ini` vira só a fonte dos valores padrão, lida uma vez no boot.

## Parte 4 — o ciclo de conexão

`POST /connect?d=<id>` e `POST /disconnect?d=<id>`, respondendo rápido:

- **Conectar não pode bloquear a resposta.** Para 4xx e 3xx o `AsciiTargetReader` monta um
  mapa nome→(linha, bit) que, num FID novo sem cache, leva muito tempo (a primeira execução
  custa ~10 s por bit; com cache é instantâneo). Dispare em thread e reporte pela barra de
  progresso, que já existe (`self.job()` / `SelProgress`).
- **`GLV_SETUP_JOB = "glv-session"` é um id fixo**, com o comentário "existe uma sessão GLV
  só". Vira um job por diagrama.
- **Falha de conexão não derruba o diagrama.** Hoje o setup cai para modo desenho e escreve
  o motivo em `state.error`, que o badge mostra em vermelho. Mantenha esse comportamento:
  falhou, o diagrama continua aberto e desconectado, com o motivo visível.
- **Desconectar** solta o `RelayLink` e chama o `clear()` novo do `LiveState`.
- Decida e me diga se há **teto de conexões simultâneas**. N diagramas conectados são N
  threads de polling batendo na rede da subestação; sem limite, isso é um pé no acelerador
  sem freio.

## Parte 5 — a tela

- Faixa de abas dos diagramas acima da faixa de páginas do GLE, uma aba por diagrama, com o
  estado da conexão visível na própria aba. Uma aba "+" abre o seletor (o que hoje é a
  landing) para montar mais um.
- Botão **Conectar / Desconectar** no cabeçalho, ao lado do que já existe. O cabeçalho do
  GLV já está cheio (busca, ferramentas, zoom, Menu, Trocar GLE, Notas, Ocultar painel) —
  reveja o conjunto; "Trocar GLE" provavelmente deixa de fazer sentido, já que agora se abre
  outro diagrama em vez de trocar o único que existe.
- Fechar um diagrama solta a conexão dele.
- Use os tokens de `pacct/web/theme.py`. Nenhuma cor, raio, pilha de fonte ou padding
  literal novo — os três temas (`folha`, `regua`, `caderno`) têm que continuar corretos.

## Parte 6 — notas por nome de relé

`_group_state_path`, `_note_path` e `_highlights_path` recebem hoje um `devid` que é
`client.devid` quando conectado e o nome do relé sanitizado em modo desenho. Passe a usar
sempre o nome do relé no RDB. Na primeira conexão de um diagrama, se existir arquivo pelo
devid e não existir pelo nome, adote-o uma vez (renomeie/copie) e registre no log.

## Restrições

- **O desenho do GLE não muda.** O SVG sai de `parsers/gle.py:render_page()` com as
  coordenadas do arquivo e tem folha de estilo própria. As classes de estado ao vivo
  (`bit-1`, `bit-0`, `bit-unknown`, `polyline.connection`) e as cores do marca-texto e da
  busca continuam **literais** onde estão — elas pintam por cima de um desenho técnico
  claro e significam alguma coisa. Só a moldura em volta é do tema (`--viewer-bg`).
- **Strings de interface em português com acento.**
- **`selprotopy/` é vendorizado**: não toque (um hook bloqueia).
- Rotas absolutas nos handlers; `self.mount_prefix` à mão só em `<a href download>` e links
  entre páginas (ver `ENGINEERING-NOTES.md`).
- Sem framework, sem build. CSS e HTML servidos pelo Python, como o resto.
- Uploads no diretório da sessão; nada de diretório compartilhado (a landing do GLV grava
  hoje em `RDBS_DIR`, que é compartilhado — a Parte 1 resolve isso).
- Atualize o `ENGINEERING-NOTES.md`: a gotcha "GLV is deliberately NOT per-user" deixa de valer, e o
  layout do projeto ganha `pacct/web/glv/`.

## Pronto quando

1. Abro três diagramas de relés diferentes, todos desconectados, e circulo entre eles pela
   faixa de abas sem recarregar a página.
2. Conecto um; só ele passa a mostrar valores. Conecto o segundo; os dois convivem, cada um
   com o seu relé.
3. Dois diagramas apontando para o mesmo IP compartilham **uma** conexão telnet; fechar o
   primeiro não derruba o segundo; fechar os dois fecha a conexão.
4. Desconectar devolve o diagrama a indeterminado, e nada na tela continua mostrando a
   leitura antiga.
5. Conexão que falha deixa o diagrama aberto e desconectado, com o motivo no badge.
6. Nota escrita antes de conectar continua lá depois de conectar.
7. O mesmo RDB subido em duas sessões extrai uma vez só.
8. Os três temas continuam corretos nas nove telas, e `grep -c "^\s*--bg:" pacct/web/*.py`
   continua devolvendo 1 só em `theme.py`.
9. Verificado no navegador com `python3 app.py --web` — este projeto não tem suíte de teste.

## O que dizer explicitamente ao final

O que não deu para verificar sem relé. Sem hardware não dá para exercitar: o polling de
verdade (bits em verde/cinza em vez de indeterminado), a descoberta de bits do
`AsciiTargetReader`, o compartilhamento real de uma sessão telnet entre dois diagramas, e o
comportamento do relé com várias sessões simultâneas. Diga isso sem enfeitar.

## Antes de escrever código

Me mostre duas coisas:

1. **As interfaces de `RelayLink`, `LinkPool` e `GlvDiagram`** — métodos, o que cada um
   possui, quem trava o quê. É onde mora o risco: refcount errado fecha o telnet debaixo de
   um diagrama vivo, ou deixa conexão pendurada para sempre.
2. **O plano de corte de `dashboard.py`**, dizendo o que vai para cada arquivo novo e o que
   fica. São 4.150 linhas se movendo junto com uma mudança de comportamento; quero ver o
   corte antes.

Comece lendo `pacct/web/dashboard.py`, `pacct/parsers/rdb.py`, `pacct/web/session.py` e
o `ENGINEERING-NOTES.md`.
