// O que o navegador ganha em TODA pagina, injetado no fim do <head> por
// `pacct/library/client.py:inject_library_runtime`.
//
// Dois runtimes moram aqui:
//
//   PacPage     -- le o bloco <script type="application/json" id="page-data">,
//                  que e' como o servidor entrega dados a um arquivo .js
//                  externo. Substituir `${...}` no corpo de um <script> deixou
//                  de ser possivel quando o corpo saiu do .html.
//   SelLibrary  -- o seletor sobre o acervo de arquivos do projeto. Seis
//                  ferramentas precisam do mesmo seletor sobre a mesma lista,
//                  e o estado vazio, que tem que linkar a aba com um href
//                  RELATIVO (um link entre paginas e' uma das duas coisas que
//                  o shim de prefixo nao alcanca), e' escrito uma vez so.
//
// O prefixo `Sel` e' historico: veio de quando a aplicacao so' lia arquivos
// SEL. Ela le SCD (IEC 61850) ha' tempos, e `SelLibrary`/`SelProgress` sao os
// dois nomes que sobraram -- renomea-los mexe no topo do script de sete
// ferramentas e nao cabe nesta fase. `PacPage` ja' nasce com o prefixo que os
// substitui.

// Este arquivo e' a ENTRADA do bundle e nao contem logica: ele so' diz quais
// modulos entram e em que ordem. O `bundles.json` aponta para ca', e a saida
// continua sendo o mesmo `/static/js/lib/file-picker.js` de sempre -- mesmo
// caminho absoluto, mesma tag classica, mesmo lugar no fim do `<head>`.
//
// A ordem importa menos do que parece (nenhum dos dois toca o DOM ao ser
// definido, e nenhum chama o outro), mas e' a ordem em que os dois runtimes
// sempre apareceram no arquivo servido, e mante-la deixa o diff da saida
// legivel.
import './page';
import './library';
