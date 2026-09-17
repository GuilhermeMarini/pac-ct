// Os runtimes que o SERVIDOR injeta na pagina, descritos -- nunca importados.
//
// Nenhum destes vem de um `import`, e nao pode vir: `library/client.py` cola o
// `SelLibrary` no fim do `<head>`, e o `inject_progress_runtime` do
// `web/progress.py` enfia o `SelProgress` antes do `</body>`, DEPOIS do script
// da propria pagina. Essa ordem e' carregada de significado (ver
// `docs/ENGINEERING-NOTES.md`, secao "Where the JavaScript lives"): um
// `import` mudaria a forma como a pagina carrega e quebraria as duas pontas.
// Um arquivo de declaracao descreve o que ja esta la'; e' a unica ferramenta
// que nao mexe no que e' servido.
//
// **O que mudou no B17.** Ate' aqui este arquivo DESCREVIA o `SelLibrary` e o
// `PacPage` a mao, e uma descricao escrita a mao pode dizer menos que a
// implementacao sem que nada perceba. Agora ela nao descreve: ela DERIVA, com
// `import('...')` em posicao de tipo -- que nao gera import nenhum em tempo de
// execucao -- do `lib/library.ts` e do `lib/page.ts`, que sao o codigo que
// define esses globais. Existe uma definicao de cada forma, e ela e' a
// implementacao.
//
// O `SelProgress` continua descrito a mao aqui embaixo, e essa e' exatamente a
// assimetria que o B17b existe para fechar: o runtime dele ainda mora dentro
// de uma string do `web/progress.py`, entao nao ha implementacao em TypeScript
// de onde derivar. Enquanto isso, o que esta escrito aqui e' uma promessa que
// nada confere.
export {};

declare global {
  // Os tipos do acervo vem de onde o picker os cumpre.
  type PacLibraryFile = import('./lib/library').PacLibraryFile;
  type PacSavedFile = import('./lib/library').PacSavedFile;
  type PacPickerOpts = import('./lib/library').PacPickerOpts;
  type PacPickerHandle = import('./lib/library').PacPickerHandle;

  const SelLibrary: import('./lib/library').SelLibraryApi;
  const PacPage: import('./lib/page').PacPageApi;

  // O `window` precisa conhecer os dois porque e' onde o proprio runtime os
  // pendura (`window.SelLibrary = libraryApi`), e porque o guarda de dupla
  // injecao os le' antes de existirem.
  interface Window {
    SelLibrary: import('./lib/library').SelLibraryApi;
    PacPage: import('./lib/page').PacPageApi;
  }

  // -- SelProgress: ainda descrito a mao, ver B17b ---------------------------

  // O corpo que uma rota devolve quando falha. Nao e' uma invencao deste
  // arquivo: o proprio `progress.py` le `d.error` do corpo para montar a
  // mensagem (`API.fail((d && d.error) || ('Falhou: HTTP ' + r.status))`), e o
  // caminho de erro de rede resolve com `{error: String(e)}`. As rotas em
  // Python escrevem a mesma forma -- `self._send_json(404, {"error": ...})`.
  // `error` e' opcional porque o runtime ja aceita nao o encontrar.
  interface PacApiError {
    error?: string;
  }

  // O que o `post` resolve, como uniao discriminada por `ok` -- e a
  // discriminacao e' o que faz a conversao caber sem uma unica asercao no
  // ponto de chamada: no ramo `!r.ok` o corpo e' o erro, depois dele e' o
  // sucesso, e o TypeScript estreita sozinho exatamente onde o codigo ja
  // estreitava sozinho.
  //
  // `data` e' `T | null` nos dois ramos: o `progress.py` faz
  // `r.json().catch(function () { return null; })`, entao um corpo que nao e'
  // JSON chega como `null` com qualquer status.
  type PacPostResult<T> =
    | {ok: true; status: number; data: T | null}
    | {ok: false; status: number; data: PacApiError | null};

  interface PacPostOpts {
    label?: string;
    doneLabel?: string;
    jobId?: string;
    headers?: Record<string, string>;
  }

  const SelProgress: {
    post: <T = unknown>(
      url: string,
      payload?: unknown,
      opts?: PacPostOpts,
    ) => Promise<PacPostResult<T>>;
  };
}
