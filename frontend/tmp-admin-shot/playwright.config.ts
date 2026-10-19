import {defineConfig, devices} from '@playwright/test'
const phone = (width: number, height: number) => ({...devices['Pixel 7'], viewport: {width, height}, screen: {width, height}})
export default defineConfig({
  testDir: '.', workers: 1, reporter: 'list', timeout: 60000,
  use: {baseURL: 'http://127.0.0.1:4176', locale: 'tr-TR'},
  webServer: {command: 'npm --prefix ../.. run preview -- --host 127.0.0.1 --port 4176 --strictPort', url: 'http://127.0.0.1:4176', reuseExistingServer: false},
  projects: [
    {name: 'desktop', use: {...devices['Desktop Chrome'], viewport: {width: 1440, height: 900}}},
    {name: 'laptop', use: {...devices['Desktop Chrome'], viewport: {width: 1366, height: 768}}},
    {name: 'phone-360', use: phone(360, 800)},
    {name: 'tablet-768', use: {...devices['Desktop Chrome'], viewport: {width: 768, height: 1024}}},
    {name: 'phone-320', use: phone(320, 568)},
    {name: 'phone-375', use: phone(375, 667)},
    {name: 'phone-430', use: phone(430, 932)},
    {name: 'iphone-webkit', use: {...devices['iPhone 13']}},
  ],
})
