# Fontes embarcadas

Estes arquivos ficam no projeto de propósito: a interface tem que abrir com a tipografia
certa num notebook de subestação **sem internet**. Nenhuma página deve pedir fonte a CDN.

Todas as seis famílias estão sob a **SIL Open Font License 1.1**, que permite uso,
redistribuição e embutimento desde que a licença e o aviso de copyright acompanhem os
arquivos — é o que está em `licencas/`.

| Família | Peso(s) | Arquivo | Tema | Copyright |
|---|---|---|---|---|
| IBM Plex Sans | 400–600 (variável) | `ibm-plex-sans-var.woff2` | Folha de Dados (padrão) | © 2017 IBM Corp. |
| IBM Plex Mono | 400, 500, 600 | `ibm-plex-mono-{400,500,600}.woff2` | Folha de Dados (padrão) | © 2017 IBM Corp. |
| Roboto | 400–500 (variável) | `roboto-var.woff2` | Régua de Bornes | © 2011 The Roboto Project Authors |
| Roboto Condensed | 500–700 (variável) | `roboto-condensed-var.woff2` | Régua de Bornes | © 2011 The Roboto Project Authors |
| Public Sans | 400–700 (variável) | `public-sans-var.woff2` | Caderno de Campo | © 2015 The Public Sans Project Authors |
| Courier Prime | 400, 700 | `courier-prime-{400,700}.woff2` | Caderno de Campo | © 2015 The Courier Prime Project Authors |

Total: 9 arquivos, 244 KB.

## Como foram gerados

Baixados de `fonts.googleapis.com` com User-Agent de Chrome (que devolve `woff2`, e não
`ttf`), **subconjunto `latin`** — cobre todos os acentos do português, que vivem em
U+00C0–U+00FF. Famílias variáveis vêm num arquivo só, declarado com faixa de peso
(`font-weight: 400 600`), em vez de um arquivo por peso.

Para atualizar ou acrescentar um peso, refaça o mesmo caminho: pegue o CSS de
`https://fonts.googleapis.com/css2?family=<Familia>:wght@<pesos>&display=swap`, extraia
as URLs `.woff2` do bloco marcado `/* latin */` e regrave `fonts.css`.

## Uso

`fonts.css` declara os `@font-face` com URLs **relativas**, então precisa ser servido do
mesmo diretório dos `.woff2`. Cada tema referencia as famílias pelo nome; sempre deixe uma
pilha de sistema como fallback, para o caso de um arquivo faltar:

    --sans: 'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
    --mono: 'IBM Plex Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace;
