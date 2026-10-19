import {defineConfig, devices} from '@playwright/test'
export default defineConfig({
  testDir: '.', workers: 1, reporter: 'list', timeout: 60000,
  use: {baseURL: 'http://127.0.0.1:4176', locale: 'tr-TR'},
  webServer: {command: 'npm --prefix ../.. run preview -- --host 127.0.0.1 --port 4176 --strictPort', url: 'http://127.0.0.1:4176', reuseExistingServer: false},
  projects: [{name: 'desktop', use: {...devices['Desktop Chrome'], viewport: {width: 1440, height: 900}}}, {name: 'laptop', use: {...devices['Desktop Chrome'], viewport: {width: 1280, height: 720}}}, {name: 'phone', use: {...devices['iPhone 13']}}],
})
