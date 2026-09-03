# Prompt — implementar o sistema de temas no app

Cole o bloco abaixo numa sessão nova, na raiz do projeto.

---

Implemente o sistema de temas do SEL Commissioning Toolkit: **um arquivo de tokens
compartilhado + três temas selecionáveis pelo usuário**. As três direções já estão
prototipadas em `mockups/` — use-as como especificação visual, não reinvente.

## Contexto

`mockups/index.html` traz a revisão de design que motivou isso. O achado nº 1: hoje a
mesma paleta está copiada em seis arquivos e já divergiu (`--bg` é `#0e1117` em cinco
tools e `#0d1117` no comparador; `--panel-2` idem), há 7 valores de `border-radius`,
20+ combinações de `padding` e 5 pilhas de fonte, além de `font-family: monospace` puro
em ~30 lugares. Cada tool serve o próprio `<style>`, então não existe onde a consistência
morar. Este trabalho cria esse lugar.

As três direções, cada uma com as nove telas do app e um `theme.css` próprio:

- `mockups/03-regua/` — **Régua de Bornes** (escuro, industrial; cor = cor de fio)
- `mockups/06-folha/` — **Folha de Dados** (claro, denso; coluna de notas na margem)
  — **este é o tema padrão**
- `mockups/10-caderno/` — **Caderno de Campo** (papel quadriculado; carimbos de veredito)

O padrão é a **06 Folha de Dados**: é a direção que sustenta as 659 linhas do comparador
sem cansar e que imprime como relatório de comissionamento. Quem abrir o app pela primeira
vez, sem cookie, tem que cair nela — as outras duas são escolha do usuário.

Leia os três `theme.css` antes de começar: eles já usam quase o mesmo vocabulário de
classes, o que torna a unificação viável.

## O que fazer

### 1. Vocabulário semântico de tokens

Crie `sellib/web/theme.py` com **um** conjunto de nomes semânticos — nunca nomes de
material. Cada tema define os mesmos nomes:

    superfície   --bg  --surface  --surface-2  --border  --border-strong
    texto        --text  --text-2  --text-3
    significado  --accent  --ok  --warn  --err  --vb  --displaced  --equiv
    espaço       --s1..--s5   (uma escala só, sem valores avulsos)
    tipo         --sans  --mono  --fs-1..--fs-5
    forma        --radius  --shadow  (0 e none em 06/10; sutis em 03)

Os valores saem dos `theme.css` dos mockups: `--bake`→`--surface` (03),
`--paper`→`--bg` e `--ink`→`--text` (06/10), e assim por diante.

### 2. Rota compartilhada `/theme.css`

Sirva pelo **dispatcher**, não por cada ferramenta — o mesmo lugar onde `/progress` já é
tratado (`sellib/web/mount.py:_dispatch`, antes do loop de prefixos; aceite tanto
`/theme.css` quanto `<prefixo>/theme.css`). Responda com o CSS do tema ativo,
`Content-Type: text/css` e `Cache-Control: no-store` enquanto estiver iterando.

### 3. Escolha do usuário, persistente

- Cookie `seltheme` (`regua` | `folha` | `caderno`), no mesmo padrão de `selsid`
  (`sellib/web/session.py:build_cookie`). Cookie, e não estado de sessão, porque o GLV é
  deliberadamente compartilhado entre visitantes e mesmo assim cada um deve ver o seu tema.
- Sem cookie, vale o padrão: **`folha` (06 Folha de Dados)**. Deixe configurável em
  `config/config.ini`, seção `[web]`, chave `theme`, para uma equipe padronizar outra —
  mas o valor de fábrica, e o fallback quando a chave estiver ausente ou inválida, é
  `folha`.
- `POST /theme` com `{"theme": "..."}` grava o cookie e responde 204; o cliente recarrega.

### 4. Rota para os estáticos

As fontes precisam de uma rota. Acrescente `STATIC_DIR` a `sellib/paths.py` (ao lado de
`DATA_DIR`, `CACHE_DIR`) apontando para `sellib/web/static/`, e sirva `/static/...` pelo
mesmo dispatcher que serve `/theme.css`:

- `Content-Type` correto (`font/woff2`, `text/css`);
- `Cache-Control: public, max-age=31536000, immutable` nos `.woff2` — eles não mudam;
- sanitize o caminho: nada de `..` escapando de `STATIC_DIR` (o projeto já faz isso em
  `/download` com `is_within()`).

### 5. Injeção nas páginas

Em `inject_prefix_shim()` (`sellib/web/mount.py`) — que já insere logo após `<head>` —
acrescente `<link rel="stylesheet" href="<prefixo>/theme.css">` e escreva
`data-theme="<ativo>"` no `<html>`. Renomeie a função se ela deixar de ser só o shim.
Cuidado: hoje ela é no-op quando o prefixo é vazio (home na raiz) e a home também precisa
do tema.

### 6. Converter as seis ferramentas

`dashboard.py` (home + GLV), `settings_compare.py`, `vb_updater.py`, `vlan_mapper.py`,
`gle_exporter.py`: troque os `:root{...}` locais e os valores literais pelos tokens.
Uma ferramenta por commit, verificando no navegador a cada uma.

### 7. Seletor de tema

Um controle no cabeçalho de todas as páginas (junto do "← Menu"), com os três nomes.
Troca = `POST /theme` + reload. Deve ser alcançável por teclado e ter foco visível.

## Restrições

- **O desenho do GLE não muda.** O SVG vem de `sellib/parsers/gle.py:render_page()` com as
  coordenadas do arquivo e tem folha de estilo própria; o tema muda só a moldura em volta.
  As classes de estado ao vivo (`bit-1` / `bit-0` / `bit-unknown`, `connection active`) e
  suas cores continuam como estão em `dashboard.py`. Confira nos mockups: as três direções
  mostram exatamente o mesmo desenho.
- **As fontes já estão no projeto — sirva de lá, nunca de CDN.** A subestação pode não ter
  internet. Estão em `sellib/web/static/fonts/`: 9 arquivos `.woff2`, 244 KB no total,
  subconjunto latin (cobre os acentos do português), famílias variáveis num arquivo só com
  faixa de peso. `fonts.css` já traz os `@font-face` prontos, com URLs **relativas** — ele
  precisa ser servido do mesmo diretório dos `.woff2`. Licenças (todas OFL 1.1) em
  `licencas/` e a atribuição em `NOTICE.md`; os dois têm que continuar acompanhando os
  arquivos. As páginas de `mockups/` já apontam para esse mesmo bundle, então o caminho
  está provado. Mantenha sempre uma pilha de sistema como fallback em cada token de fonte.
- Strings de interface em **português com acento** — inclusive corrigindo as que hoje estão
  sem ("Compara descricoes", "reles", "Relatorio").
- Rotas absolutas nos handlers; `self.mount_prefix` à mão só em `<a href download>` e links
  entre páginas (ver docs/ENGINEERING-NOTES.md).
- `selprotopy/` é vendorizado: não toque.
- Sem framework, sem build. CSS servido pelo Python, como o resto.

## O que não cabe em CSS

Marcações que exigem HTML diferente por tema estão **fora** deste escopo. Unifique a
marcação e deixe cada tema pintar:

- a navegação é uma lista numerada; a orientação (vertical no 03, faixa no 06/10) sai de
  CSS por `[data-theme]`;
- a coluna de notas existe na marcação de todas as telas: no 06 é a margem com notas
  numeradas, no 10 vira anotação à mão, no 03 vira uma placa lateral;
- veredito é sempre `<span class="j j-ok">`: pastilha no 03, rótulo tipográfico no 06,
  carimbo girado no 10.

Se alguma assinatura de direção não sobreviver a isso, diga qual e por quê — não invente
marcação condicional por tema.

## Pronto quando

1. Navegador limpo, sem cookie e sem chave no `config.ini`, abre em **06 Folha de Dados**.
2. Os três temas trocam sem reload quebrado, em todas as nove telas, e a escolha sobrevive
   a fechar o navegador.
3. `grep -c "^\s*--bg:" sellib/web/*.py` retorna 1 — os tokens moram num lugar só.
4. Nenhum `border-radius` ou `padding` literal novo fora da escala.
5. O GLV renderiza o mesmo SVG nos três temas, com o estado dos bits correto.
6. Com a rede desligada, a tipografia continua correta em todas as telas, e a aba Network
   do navegador não mostra nenhuma requisição a `fonts.googleapis.com` ou `fonts.gstatic.com`
   (`grep -rn "fonts.googleapis\|fonts.gstatic" sellib/` não retorna nada).
7. Verificado no navegador com `python3 app.py --web` — este projeto não tem suíte de teste.
   Diga explicitamente o que não deu para verificar sem relé (polling do GLV, Fast Meter).

Comece lendo `mockups/index.html`, os três `theme.css` e `sellib/web/mount.py`. Antes de
escrever código, me mostre o vocabulário de tokens que você extraiu das três direções e
onde cada uma discorda — é ali que o sistema vai doer.
