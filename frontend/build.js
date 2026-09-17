// O inventario do que este repositorio constroi, e o laco que constroi.
//
// Ele existe porque o contrato servido e o formato `iife`, e o Vite recusa
// mais de uma entrada nesse formato -- medido, nao suposto:
//
//     Multiple entry points are not supported when output formats include
//     "umd" or "iife".
//
// E o `iife` nao e' preferencia: e' o que faz a saida ser um script classico,
// executado onde o parser o encontra, que e' o que o `inject_progress_runtime`
// e o `inject_library_runtime` dependem (ver `vite.config.js`). Entao a build
// roda UMA VEZ POR ARQUIVO, e a lista de arquivos precisava morar em algum
// lugar.
//
// Ela mora em `bundles.json`, e em JSON por um motivo que nao e' estetico: o
// `tests/test_frontend_build.py` LE essa tabela. Um `&&` encadeado no
// `package.json` seria mais curto e ilegivel para a suite -- ninguem alem de
// um humano saberia dizer que ferramenta esta' construida, qual fonte nao tem
// saida e qual saida nao tem fonte. Com a tabela como DADO, isso vira teste,
// que e' a mesma razao pela qual as 88 rotas vivem em `tests/declared_routes.py`
// em vez de so' no documento.
//
// Cada linha tem tres campos e nenhum e' opcional:
//
//   entry  - a fonte, relativa a `frontend/`.
//   out    - a saida, relativa ao `static/` do pacote. E' esse caminho que o
//            `mount.py` serve em todos os nove prefixos e que o template
//            escreve a mao, entao ele e' fixo e sem hash.
//   global - o nome que o `iife` exige. Nenhum arquivo aqui exporta nada em
//            tempo de execucao, entao o nome nunca chega a ser atribuido.
import { readFileSync } from 'node:fs'
import { build } from 'vite'

const BUNDLES = JSON.parse(readFileSync(new URL('./bundles.json', import.meta.url), 'utf8'))

// `../src/pacct/web/static/` visto de `frontend/`.
const STATIC = '../src/pacct/web/static'

for (const b of BUNDLES) {
  const slash = b.out.lastIndexOf('/')
  await build({
    // O resto da configuracao -- `formats: ['iife']`, sem minificacao, sem
    // source map, `target: 'esnext'` -- vem do `vite.config.js` e vale para
    // todos. Aqui vai so' o que muda de um arquivo para o outro.
    build: {
      lib: {
        entry: b.entry,
        name: b.global,
        fileName: () => b.out.slice(slash + 1),
      },
      outDir: `${STATIC}/${b.out.slice(0, slash)}`,
      rollupOptions: {
        output: {
          banner: `// Gerado por \`npm run build\` a partir de frontend/${b.entry} -- nao editar aqui.`,
        },
      },
    },
  })
}
