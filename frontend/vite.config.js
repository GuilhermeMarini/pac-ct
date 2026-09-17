// A build do frontend do PAC CT. Hoje ela produz UM arquivo: o script da tela
// do Mapeador de VLAN. Tudo aqui existe para que a saida caiba no contrato que
// as paginas ja cumprem -- descrito em `docs/ENGINEERING-NOTES.md`, secao
// "Where the JavaScript lives" -- e nao para seguir o default do Vite.
//
// Desde o B16 a fonte e' TypeScript, e vale dizer o que esta build NAO faz: o
// Vite nao confere tipos. O esbuild apaga as anotacoes sem ler nenhuma, entao
// um `npm run build` verde -- e um job `frontend` verde -- nao prova nada
// sobre corretude de tipos. Quem prova e' o `npm run typecheck`
// (`tsc --noEmit`), que o CI roda ANTES deste build, no mesmo job.
//
// Os quatro pontos em que o default do Vite briga com esse contrato:
//
//   1. `type="module"` e' adiado por definicao, e este projeto proibe
//      `defer`/`async`: o `inject_progress_runtime` enfia o `SelProgress`
//      antes do `</body>` DEPOIS do script da pagina, e o `SelLibrary` vem no
//      fim do `<head>` antes dele. Por isso `formats: ['iife']` -- script
//      classico, executado na hora em que o parser o encontra.
//   2. O Vite poe hash no nome e junta tudo em `assets/`. O caminho
//      `/static/js/vlan_mapper/landing.js` esta escrito no template e e' o que
//      o `mount.py` serve em todos os nove prefixos, entao o nome e' fixo e o
//      diretorio de saida e' o que ja existe.
//   3. `.map` nao esta no `_STATIC_TYPES` do `mount.py`: um source map seria
//      servido como `application/octet-stream` e recusado com `nosniff`. O B14
//      adiou esta decisao ate aqui POR ESCRITO, e a resposta do B16 e' nao:
//      ligar o mapa e' mexer no `_STATIC_TYPES`, isto e', mover a superficie
//      SERVIDA na fase que prometeu nao mover nenhuma, e ainda despejar `.map`
//      dentro do pacote offline -- bytes que nenhuma subestacao vai abrir. A
//      saida transpilada continua linha a linha com a fonte, entao o mapa
//      fecharia uma distancia que nao existe.
//   4. Minificacao continua desligada, mas o argumento do B14 ("a saida e' a
//      fonte com um envelope em volta") MORREU, e vale medir o quanto: das 65
//      linhas de comentario da fonte, a saida transpilada guarda 1 -- o
//      banner. O esbuild tambem normaliza aspas e tira parenteses redundantes,
//      entao a saida nao e' mais a fonte em sentido nenhum, e o teste do B14
//      que exigia isso saiu junto.
//
//      O que sustenta a decisao e' o resto dela, que a transpilacao nao toca:
//      esta saida e' VERSIONADA, aparece em diff, e a checagem de defasagem do
//      B15 imprime esse diff para um humano ler. Sao 282 linhas legiveis.
//      Minificada, seria uma linha so', e a checagem passaria a dizer apenas
//      "mudou" -- que e' precisamente o que ela nao pode dizer.
//
// `target: 'esnext'` pelo mesmo motivo: sem rebaixamento, a saida difere da
// fonte apenas pelo envelope IIFE, e essa diferenca cabe numa frase.
import { defineConfig } from 'vite'

export default defineConfig({
  build: {
    lib: {
      entry: 'src/vlan_mapper/landing.ts',
      formats: ['iife'],
      // O modo `lib` exige um nome para `iife`. Este arquivo nao exporta nada,
      // entao o nome nunca chega a ser atribuido a lugar nenhum.
      name: 'PacVlanMapper',
      fileName: () => 'landing.js',
    },
    outDir: '../src/pacct/web/static/js/vlan_mapper',
    // O diretorio de saida e' versionado e tem dono: nunca esvazia-lo.
    emptyOutDir: false,
    sourcemap: false,
    minify: false,
    target: 'esnext',
    rollupOptions: {
      output: {
        banner: '// Gerado por `npm run build` a partir de frontend/src/vlan_mapper/landing.ts -- nao editar aqui.',
      },
    },
  },
})
