# GLV — conectores do GLE: ligar o sinal, e mostrar por onde ele passa

*2026-09-01 — design*

## Problema

No GLE, quem desenha usa um **conector** para não puxar uma linha comprida pela
página. Na aba `52 - Cmd. Fechar` do `GL1.gle` do IED `QPC2_TR1_UPC1`
(RDB `SE EXEMPLO III`), o desenho tem dois:

```xml
<element id="723" type="Connector" left="666" top="66"><label>Cont1</label></element>
<element id="206" type="Connector" left="54"  top="324"><label>Cont1</label></element>
```

e duas conexões que os tocam:

```
conn 658:  elemento 667      ->  Connector #723   (Cont1)
conn 134:  Connector #206    ->  elemento 517     (Cont1)
```

**Não existe ligação entre 723 e 206 no XML.** O que junta os dois é o
`<label>` igual — é um *nome de rede*, não uma aresta. O toolkit trata cada
conector como um bloco isolado, então o sinal morre ali e toda linha a jusante
da ponta que emite fica sem cor.

São **duas falhas independentes**, e as duas precisam ser fechadas:

1. **`sellib/parsers/gle.py:render_gate`** desenha o conector como uma caixa com
   `→` e **descarta o `<label>`**. `element_info` lê nome de `<logic_element>`,
   e um Connector não tem `logic_element` nenhum — o `<label>` é filho direto do
   `<element>`. Logo o SVG não carrega chave de pareamento alguma. No desenho,
   pelo mesmo motivo, uma pessoa não consegue distinguir `Cont1` de `CONTADOR`.
2. **`templates/dashboard.html:evaluatePage`** termina o `switch` em
   `default: continue` e não tem `case 'Connector'`. Mesmo um conector *com*
   entrada nunca produz valor.

## Evidência medida

Varredura dos 418 `.gle` em `rdbs/extracted/`.

| | |
|---|---|
| GLEs que usam conector | **107** de 418 |
| elementos `Connector` | **324** |
| labels (redes) | **110** |

### A forma de uma rede é invariante

| receptores | emissores | páginas | ocorrências |
|---|---|---|---|
| 1 | 1 | 1 | 73 |
| 1 | 2 | 1 | 16 |
| 1 | 9 | 1 | 12 |
| 1 | 1 | 2 | 8 |
| 1 | 5 | 2 | 1 |

**Exatamente um receptor por label, 110 de 110.** A direção nunca é ambígua, e
o leque de saída chega a 9 derivações. **Nove labels atravessam página** (todos
`LEDGROUND`: acionado em `SCADA`, derivado em `LEDS`).

### A equação que aciona um conector é pequena e legível

Extraída caminhando de trás do receptor até o primeiro elemento com **bit
nomeado da Relay Word**:

| | |
|---|---|
| equações extraídas | **110 de 110** — 0 falhas, 0 ciclos, 0 truncadas |
| texto | mediana **105** caracteres, máximo 105 |
| bits por equação | mediana **10**, máximo 10 |

A do caso relatado, `Cont1`:

```
(SV02 * LT03 * (PB04_PUL + CC) * SV05)
  + (SV05 * SV02 * LT03 * (RB01 + (VB080 * VB111 * SV16T * LT11)))
```

com 10 bits: `CC LT03 LT11 PB04_PUL RB01 SV02 SV05 SV16T VB080 VB111`.

### O vocabulário dentro das equações é fechado

| elemento | ocorrências | | modificador de porta | ocorrências |
|---|---|---|---|---|
| `SYMBOL` | 1036 | | `NOT` | 148 |
| `AND` | 271 | | `RTRIG` | 15 |
| `OR` | 262 | | `FTRIG` | 12 |
| `PCN` | 12 | | | |

**Nenhum bloco aritmético** (ADD/SUB/MULT/DIV) aparece, então a notação SELOGIC
`*` (E), `+` (OU), `!` (NÃO) não colide com nada — e é a notação que o
engenheiro já lê no AcSELerator.

## Solução

Um conector deixa de ser um bloco isolado e passa a ser uma **rede nomeada com
um acionador e N derivações**, cuja equação o GLV extrai, poleia e mostra.

### Decisões tomadas

| Questão | Decisão |
|---|---|
| Onde a equação é extraída | **Python, em `build_diagram`**, uma vez por diagrama. Só o servidor enxerga todas as páginas; o `evaluatePage` só tem o SVG da página aberta. E é estático: o desenho não muda entre leituras. |
| Onde a equação é **avaliada** | **Só no JS**, com as mesmas primitivas que o `evaluatePage` já tem. Avaliar em Python poria a semântica de NOT/RTRIG/latch em duas linguagens — e a história deste repositório é uma lista de coisas que quebraram quando uma regra teve duas cópias. Python extrai **estrutura**; JS decide **semântica**. |
| Onde a caminhada para | No primeiro elemento com **bit nomeado da Relay Word** — nome de um `SYMBOL`, ou saída derivada de um bloco via `relay_model.derived_bit_for`. É o que faz `PCN` terminar honestamente: o relé publica `PCN01Q` direto, não há o que simular. |
| Travessia de página | **Coberta, e sem caso especial.** Mesma página e página cruzada passam pelo mesmo caminho — os 9 `LEDGROUND` deixam de ser exceção. |
| Bits no polling | `values(page)` soma os bits dos conectores **presentes na página aberta**. ≤10 bits por conector, e eles **já estão em `all_wanted_bits`** (o GLE inteiro), então `ensure_bits` e o mapa MMS não mudam em nada. |
| Notação | SELOGIC: `*`, `+`, `!`, com `↑`/`↓` para RTRIG/FTRIG. Sem colisão (ver acima). |
| Legenda | Seção **Conectores** listando **só os da página aberta** — a legenda é contexto da página, e um label chega a 10 conectores. |
| RTRIG/FTRIG | **Passagem**, marcada no texto. É o que o `evaluatePage` já faz hoje; sem histórico não dá para avaliar borda, e inventar seria pior que mostrar a passagem. |

## Arquitetura

### 1. `sellib/web/glv/connectors.py` (novo)

Módulo com uma responsabilidade: ler um GLE e devolver as redes de conector.

```
ConnectorNet:
    label        str            # "Cont1"
    receiver     str            # id do elemento que RECEBE
    emitters     tuple[str]     # ids que EMITEM
    driver_page  str            # safe_page_id onde está o receptor
    pages        tuple[str]     # safe_page_ids onde há alguma ponta
    tree         dict           # a expressão, em JSON
    bits         frozenset[str] # folhas nomeadas da Relay Word
    equation     str            # o texto SELOGIC, para a legenda

extract(gle_root, relay_model=None) -> dict[str, ConnectorNet]
```

A caminhada tem guarda de ciclo (conjunto de visitados) e **teto de
profundidade de 32 níveis**. O corpus não precisa de nenhum dos dois — 0 ciclos
e 0 truncadas em 110, com a equação mais funda bem abaixo disso — mas
realimentação de latch precisaria, e um extrator que entra em laço trava o
`build_diagram`. Ao estourar o teto, o ramo vira a folha literal `…` e o log
nomeia o label: uma equação visivelmente incompleta é honesta, uma equação
inventada não.

Um label **sem receptor** ou com **mais de um** não vira rede: é registrado no
log e ignorado. Não acontece no corpus (110/110 com exatamente um), e adivinhar
qual ponta aciona pintaria linha por chute.

### 2. Modelo e transporte

`GlvDiagram` ganha `connectors: dict[str, ConnectorNet]`, exposto em `meta()`.
`values(page)` passa a devolver, junto do snapshot, os conectores da página —
nome, equação, árvore e bits — para a legenda e para o avaliador.

### 3. Polling

Em `GlvDiagram.values(page)`:

```
wanted = self.bits_per_page[page] | união(net.bits dos conectores da página)
```

Sem mexer em `ensure_bits`: os bits já entram em `all_wanted_bits` porque estão
no GLE, em alguma página. O que faltava era o filtro por página aberta os
estreitar de volta.

### 4. Renderização

O `<g>` do conector ganha `data-connector="<label>"` e passa a desenhar **o
nome** no lugar do `→`. Ler o label exige `el.findtext("label")` — não há
`logic_element` de onde `element_info` o tiraria.

### 5. Avaliação (JS)

`evaluatePage` ganha uma passada, dentro do laço de convergência que já existe:
avalia a árvore de cada conector com o mesmo `switch`, e semeia `elValue` de
**todo** `<g data-connector>` daquele label com o resultado. Estar dentro do
laço é o que deixa um conector alimentar um bloco que alimenta outro conector e
ainda assim convergir.

### 6. Legenda

Seção **Conectores**, por conector da página aberta: o nome, a equação, e cada
variável colorida pelo estado vivo com as mesmas amostras de cor da legenda
(`#6fdc6f` ativo, `#e8e8e8` inativo, `#fff4c8` indeterminado).

## Testes

**Pytest** (extração e renderização são funções puras do GLE):

- `Cont1` do `QPC2_TR1_UPC1`: os dois ids, o receptor, o emissor, os 10 bits e a
  equação, literais.
- A invariante do corpus: um receptor por label, 110 de 110.
- `LEDGROUND`: rede que atravessa página, com `driver_page` diferente da página
  de uma das pontas.
- Guarda de ciclo: GLE sintético com realimentação, termina e não trava.
- Label sem receptor / com dois receptores: ignorado com log, não vira rede.
- O SVG do conector carrega `data-connector` e o nome.
- `values(page)` inclui os bits do conector no `wanted`.

**Sem infraestrutura de teste neste repositório**: o avaliador JS e a legenda.
Verificação por navegador, sobre este RDB, na aba `52 - Cmd. Fechar` —
declarando o que foi conferido em tela.

**Precisa de relé de bancada**: a leitura ao vivo dos 10 bits e a cor final da
linha com o relé respondendo. Não é verificável sem hardware.

## Fora de escopo

- **Conector em cadeia entre GLEs diferentes.** Não existe no corpus e não há
  semântica definida para isso no GLE.
- **Avaliar RTRIG/FTRIG de verdade.** Exige histórico entre voltas do polling,
  que o GLV não guarda hoje para nenhum bloco.
- **Mudar a notação da equação para a do GLE bruto.** SELOGIC é o que o
  engenheiro lê; o mapeamento é interno.
