import {defineConfig, devices} from '@playwright/test'

export default defineConfig({
  testDir: './frontend/tests/shots',
  workers: 1,
  reporter: 'line',
  use: {...devices['Desktop Chrome']},
  webServer: [{command: 'npm run dev -- --mode test --host 127.0.0.1 --port 4174 --strictPort', url: 'http://127.0.0.1:4174', reuseExistingServer: false, timeout: 180000}],
})
