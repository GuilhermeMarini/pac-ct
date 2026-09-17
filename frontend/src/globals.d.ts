// Os runtimes que o SERVIDOR injeta na pagina, descritos -- nunca importados.
//
// Nenhum destes vem de um `import`, e nao pode vir: `library/client.py` cola o
// `SelLibrary` no fim do `<head>`, e o `inject_progress_runtime` do
// `web/progress.py` enfia o `SelProgress` antes do `</body>`, DEPOIS do script
// da propria pagina. Essa ordem e' carregada de significado (ver
// `docs/ENGINEERING-NOTES.md`, secao "Where the JavaScript lives"): um
// `import` mudaria a forma como a pagina carrega e quebraria as duas pontas.
// Um arquivo de declaracao descreve o que ja esta la; e' a unica ferramenta
// que nao mexe no que e' servido.
//
// Declara-se AQUI so' o que o piloto exerce, e isso e' deliberado:
//
//   - de `SelLibrary`, so' `picker`. O runtime tambem expoe `list`, `fmtSize`
//     e `savedNote`, e nenhum deles tem chamada nesta tela.
//   - de `SelProgress`, so' `post`. O runtime tambem expoe `begin`, `done`,
//     `fail`, `track`, `upload` e `newJobId`.
//   - `PacPage` nao aparece. Tem ZERO chamadas no piloto, e o B17 e' a fase
//     que migra o `lib/file-picker.js` -- o arquivo que o DEFINE. Uma
//     declaracao escrita aqui seria substituida pelo tipo inferido da propria
//     fonte uma fase depois, sem nunca ter sido verificada por uma chamada. E
//     o `PacPage.data()` devolve o que o servidor pos no bloco `page-data`,
//     que esta tela nao tem: tipar essa carga aqui e' escrever um tipo que
//     sete ferramentas herdam sem que nada o tenha conferido.
//
// O `SelLibrary.picker` tambem aceita `multi`, e ai o `onPick` recebe o ARRAY
// das linhas marcadas em vez de uma. Nao esta declarado pelo mesmo motivo: o
// piloto nao usa, entao a forma da sobrecarga seria desenhada sem nenhuma
// chamada para conferi-la. Quem converter uma ferramenta `multi` desenha a
// sobrecarga com um caso de uso na frente.
export {};

declare global {
  // Uma entrada do acervo do projeto, exatamente como o `FileEntry.to_json()`
  // do `library/model.py` a serializa -- nove campos, nenhum opcional.
  // `detail` e `origin` sao `""` quando vazios, nunca `null`.
  interface PacLibraryFile {
    sha256: string;
    short_sha: string;
    kind: string;
    name: string;
    size: number;
    uploaded_at: number;
    detail: string;
    origin: string;
    generated: boolean;
  }

  interface PacPickerOpts {
    kind?: string;
    label?: string;
    selected?: string;
    onPick?: (f: PacLibraryFile) => void;
    annotate?: (f: PacLibraryFile) => string;
  }

  interface PacPickerHandle {
    refresh: () => Promise<void>;
    select: (ref: string) => void;
  }

  const SelLibrary: {
    picker: (el: string | HTMLElement, opts?: PacPickerOpts) => PacPickerHandle;
  };

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
