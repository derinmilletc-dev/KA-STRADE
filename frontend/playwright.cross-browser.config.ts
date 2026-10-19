import {defineConfig, devices} from '@playwright/test'

/** Çapraz tarayıcı duman testi: aynı sayfalar Chrome, Firefox ve Safari motorlarında, masaüstü ve telefon ekranlarında açılır.
 *  Sunucu çağrıları taklit edilir (helpers/assistant-api); gerçek hesap, borsa veya ağ kullanılmaz.
 *  Not: WebKit (Linux) gerçek iPhone Safari'nin birebir aynısı değildir; gerçek cihaz denemesinin yerini tutmaz. */
export default defineConfig({
  testDir: './tests',
  testMatch: /cross-browser-smoke\.spec\.ts/,
  workers: 1,
  timeout: 90000,
  reporter: [['list'], ['json', {outputFile: 'cross-browser-results.json'}]],
  use: {baseURL: 'http://127.0.0.1:4174', locale: 'tr-TR', timezoneId: 'Europe/Istanbul', screenshot: 'only-on-failure'},
  webServer: {
    command: 'npm --prefix .. run dev -- --mode test --host 127.0.0.1 --port 4174 --strictPort',
    url: 'http://127.0.0.1:4174',
    reuseExistingServer: false,
    timeout: 120000,
  },
  projects: [
    {name: 'chrome-masaustu', use: {...devices['Desktop Chrome']}},
    {name: 'firefox-masaustu', use: {...devices['Desktop Firefox']}},
    {name: 'safari-masaustu', use: {...devices['Desktop Safari']}},
    {name: 'iphone-safari', use: {...devices['iPhone 13']}},
    {name: 'android-chrome', use: {...devices['Pixel 7']}},
  ],
})
