// KaisTrade service worker: only shows push notifications (no caching, no offline mode).
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()))

self.addEventListener('push', event => {
  let data = {}
  try { data = event.data ? event.data.json() : {} } catch (error) { data = {} }
  event.waitUntil(self.registration.showNotification(data.title || 'KaisTrade', {
    body: data.body || '',
    tag: data.tag || 'kaistrade',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    data: {url: typeof data.url === 'string' && data.url.startsWith('/') ? data.url : '/'},
  }))
})

self.addEventListener('notificationclick', event => {
  event.notification.close()
  const url = (event.notification.data && event.notification.data.url) || '/'
  event.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(windows => {
    for (const client of windows) {
      if ('focus' in client) { if ('navigate' in client) client.navigate(url); return client.focus() }
    }
    return self.clients.openWindow(url)
  }))
})
