import assert from 'node:assert/strict'
import {existsSync, readFileSync} from 'node:fs'
import {test} from 'node:test'

const read = path => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'))
const deployments = {'vercel.json': read('../vercel.json'), 'frontend/vercel.json': read('../frontend/vercel.json')}

// Vercel `:path(<regex>)` parametresi ilk "/" sonrasını yakalar; testte aynı şekilde modellenir.
function fallbackMatches(source, pathname) {
  const match = /^\/:path\((.*)\)$/.exec(source)
  assert.ok(match, `unexpected fallback source: ${source}`)
  return new RegExp(`^/${match[1]}$`).test(pathname)
}

function resolve(config, pathname) {
  for (const rule of config.rewrites) {
    if (rule.has) continue // admin.kaistrade.com ana alan adı dışındaki kural
    if (rule.source.startsWith('/:path(')) { if (fallbackMatches(rule.source, pathname)) return rule.destination }
    else if (rule.source === '/api/:path*') { if (pathname.startsWith('/api/')) return rule.destination.replace(':path*', pathname.slice('/api/'.length)) }
    else if (rule.source === pathname) return rule.destination
  }
  return null
}

for (const [name, config] of Object.entries(deployments)) {
  test(`${name}: assetlinks.json and .well-known are never swallowed by the SPA fallback`, () => {
    for (const path of ['/.well-known/assetlinks.json', '/.well-known/other.json', '/.well-known/a/b']) {
      assert.equal(resolve(config, path), null, path)
    }
    // Normal rotalar hâlâ SPA'ya düşer; kapsam genişlemedi.
    for (const path of ['/', '/profile', '/verify-email', '/master-trade', '/well-known/x', '/x.well-known/y']) {
      assert.equal(resolve(config, path), '/index.html', path)
    }
  })

  test(`${name}: assetlinks.json is served as JSON without cache overrides`, () => {
    const rule = config.headers.find(item => item.source === '/.well-known/assetlinks.json')
    assert.deepEqual(rule?.headers, [{key: 'Content-Type', value: 'application/json; charset=utf-8'}])
  })

  test(`${name}: /hesap-silme is a static page with and without trailing slash`, () => {
    for (const path of ['/hesap-silme', '/hesap-silme/']) assert.equal(resolve(config, path), '/hesap-silme.html')
    // Mevcut politika sayfaları değişmedi.
    for (const page of ['privacy', 'terms', 'risk']) assert.equal(resolve(config, `/${page}`), `/${page}.html`)
  })
}

test('root config keeps API/OAuth routing and the SEO exclusions', () => {
  const root = deployments['vercel.json']
  assert.equal(resolve(root, '/api/v22/auth/google/callback'), 'https://protrebot-rkpt.onrender.com/api/v22/auth/google/callback')
  for (const path of ['/sitemap.xml', '/robots.txt', '/sw.js']) assert.equal(resolve(root, path), null, path)
})

test('a published assetlinks.json must be real: valid JSON, our package, no placeholder fingerprint', () => {
  const file = new URL('../frontend/public/.well-known/assetlinks.json', import.meta.url)
  if (!existsSync(file)) return // Play parmak izi sahibinden gelene kadar dosya bilerek yok
  const data = JSON.parse(readFileSync(file, 'utf8'))
  assert.ok(Array.isArray(data) && data.length > 0)
  for (const statement of data) {
    assert.deepEqual(statement.relation, ['delegate_permission/common.handle_all_urls'])
    assert.equal(statement.target.namespace, 'android_app')
    assert.equal(statement.target.package_name, 'com.kaistrade.app')
    assert.ok(statement.target.sha256_cert_fingerprints.length > 0)
    for (const fingerprint of statement.target.sha256_cert_fingerprints) {
      assert.match(fingerprint, /^([0-9A-F]{2}:){31}[0-9A-F]{2}$/, 'SHA-256 parmak izi AA:BB:... biçiminde olmalı (yer tutucu olamaz)')
    }
  }
})
