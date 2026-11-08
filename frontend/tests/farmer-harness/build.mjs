// KAIS FARMER bileşen koşum düzeneği: FarmerPanel'i sahte API ile tarayıcıda çalıştırır (uygulamanın geri kalanına bağlı DEĞİL).
// Kullanım: ESBUILD=/yol/esbuild/lib/main.js MODULES=/yol/node_modules node build.mjs && PLAYWRIGHT=/yol/playwright/index.mjs node farmer-harness.test.mjs
import {pathToFileURL} from 'node:url'
const {build} = await import(pathToFileURL(process.env.ESBUILD || 'esbuild').href.replace('file:///esbuild', 'esbuild'))
const modules = process.env.MODULES || new URL('../../node_modules', import.meta.url).pathname
await build({entryPoints: ['entry.tsx'], bundle: true, outfile: 'out.js', jsx: 'automatic', format: 'iife', loader: {'.css': 'css'},
  alias: {'lucide-react': './lucide-shim.tsx', react: `${modules}/react`, 'react-dom': `${modules}/react-dom`}, nodePaths: [modules], logLevel: 'warning', define: {'process.env.NODE_ENV': '"production"'}})
