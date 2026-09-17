// -----------------------------------------------------------------------------
// PacPage -- the data the server left on this page.
// -----------------------------------------------------------------------------
//
// Um MODULO, e nao mais um IIFE. O arquivo servido continua sendo um unico
// bundle `iife`, entao o escopo do modulo e' privado exatamente como o do IIFE
// era -- nada aqui vaza para o global a nao ser o que este arquivo pendura no
// `window` de proposito. O que a troca compra esta' nas duas ultimas linhas: o
// `export type` deixa o `globals.d.ts` DERIVAR o tipo do global deste codigo,
// em vez de descrever a mao o que este codigo faz. Uma declaracao escrita a
// mao pode dizer menos que a implementacao sem que nada perceba; esta nao
// pode, porque so' existe uma definicao.
var cache: unknown = null;

// The page's server data, as an object. `{}` when the page carries no
// `page-data` block -- which is the normal case: only the screens that have
// something to hand down emit one, and a tool that asks for a key it was
// never given gets `undefined`, not an exception.
//
// A block that IS there and does not parse throws, naming the page. That
// failure is otherwise invisible: the exception aborts the tool's whole
// script and the screen comes up blank with a 200, which is exactly the
// shape the browser check exists to catch.
//
// O retorno e' `unknown`, e isso e' a decisao do B16 continuando a valer: o
// que volta e' o que o servidor pos no bloco, duas telas o emitem e nenhuma
// delas e' TypeScript ainda, entao nao ha aqui nada que confira uma forma
// inventada neste arquivo. Quem ler estreita, e o estreitamento fica escrito
// no ponto de uso em vez de virar um tipo que sete ferramentas herdam sem
// conferencia.
function data(): unknown {
  if (cache) return cache;
  var el = document.getElementById('page-data');
  if (!el) { cache = {}; return cache; }
  try {
    // `String(...)` porque o `textContent` de um Element e' `string | null`
    // para o TypeScript. O `JSON.parse` ja' fazia essa conversao sozinho (ele
    // aplica ToString no argumento), entao explicitar o que o runtime ja'
    // fazia nao muda comportamento nenhum -- e evita uma asercao.
    cache = JSON.parse(String(el.textContent)) || {};
  } catch (e) {
    // Sob `strict`, `e` e' `unknown`. O unico lancador dentro do `try` e' o
    // `JSON.parse`, que lanca `SyntaxError` -- um `Error` --, entao o ramo
    // falso nao e' alcancavel hoje e existe para o tipo ser honesto em vez de
    // para cobrir um caso conhecido.
    var msg = e instanceof Error ? e.message : String(e);
    throw new Error('page-data invalido em ' + location.pathname + ': ' +
                    msg);
  }
  return cache;
}

export const pageApi = {data: data};
export type PacPageApi = typeof pageApi;

// O guarda era `if (window.PacPage) return;` no topo do IIFE. Em escopo de
// modulo ele protege a ATRIBUICAO, que e' o que ele sempre protegeu: uma
// segunda injecao do runtime na mesma pagina nao redefine o objeto.
if (!window.PacPage) window.PacPage = pageApi;
