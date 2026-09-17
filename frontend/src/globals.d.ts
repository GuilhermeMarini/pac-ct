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
// **O B17b fechou a ultima assimetria.** Ate' ele, o `SelProgress` continuava
// descrito a mao aqui, porque o runtime dele morava dentro de uma string do
// `web/progress.py` e nao havia implementacao de onde derivar. Agora ha
// (`lib/progress.ts`), e nao sobrou nenhuma declaracao escrita a mao neste
// arquivo: os tres globais derivam do codigo que os define.
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
    SelProgress: import('./lib/progress').SelProgressApi;
  }

  // Os tipos da barra vem de onde ela e' implementada.
  type PacApiError = import('./lib/progress').PacApiError;
  type PacPostResult<T> = import('./lib/progress').PacPostResult<T>;

  const SelProgress: import('./lib/progress').SelProgressApi;
}
