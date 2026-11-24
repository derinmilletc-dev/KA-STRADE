import {defineConfig, devices} from '@playwright/test'
import {randomUUID} from 'node:crypto'
import {tmpdir} from 'node:os'
import {dirname, resolve} from 'node:path'
import {fileURLToPath} from 'node:url'

// Ana sayfa düzen testi: yalnız bu dosyayı, derlenmiş uygulamaya karşı çalıştırır (büyük e2e takımından bağımsız).
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
export default defineConfig({
  testDir: resolve(root, 'frontend', 'tests'),
  testMatch: ['home-layout.spec.ts'],
  outputDir: process.env.HOME_LAYOUT_RESULTS ?? resolve(tmpdir(), `kais-home-${randomUUID()}`),
  workers: 1, reporter: 'line', timeout: 60000,
  use: {baseURL: 'http://127.0.0.1:4177', trace: 'retain-on-failure'},
  webServer: {command: 'npm run preview -- --host 127.0.0.1 --port 4177 --strictPort', cwd: root, url: 'http://127.0.0.1:4177', reuseExistingServer: false},
  projects: [{name: 'chromium-phone', use: {...devices['Desktop Chrome']}}],
})
