// A build do frontend do PAC CT. Hoje ela produz UM arquivo: o script da tela
// do Mapeador de VLAN. Tudo aqui existe para que a saida caiba no contrato que
// as paginas ja cumprem -- descrito em `docs/ENGINEERING-NOTES.md`, secao
// "Where the JavaScript lives" -- e nao para seguir o default do Vite.
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
//      servido como `application/octet-stream` e recusado com `nosniff`.
//      Source map fica desligado ate o B16, que e' quando ele passa a valer o
//      preco de mexer no `mount.py`.
//   4. Minificacao apagaria os comentarios, que neste projeto sao a
//      documentacao, e transformaria a saida versionada numa linha so -- o que
//      tornaria ilegivel a checagem de defasagem que o B15 vai montar.
//
// `target: 'esnext'` pelo mesmo motivo: sem rebaixamento, a saida difere da
// fonte apenas pelo envelope IIFE, e essa diferenca cabe numa frase.
import { defineConfig } from 'vite'

export default defineConfig({
  build: {
    lib: {
      entry: 'src/vlan_mapper/landing.js',
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
        banner: '// Gerado por `npm run build` a partir de frontend/src/vlan_mapper/landing.js -- nao editar aqui.',
      },
    },
  },
})
