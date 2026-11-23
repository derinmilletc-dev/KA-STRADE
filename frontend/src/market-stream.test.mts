// Çalıştırma: node frontend/src/market-stream.test.mts
import assert from 'node:assert'
import {createMarketStream} from '../../market-stream.ts'

class FakeSocket {
  static all: FakeSocket[] = []
  readyState = 0
  sent: any[] = []
  onopen: any = null; onmessage: any = null; onclose: any = null; onerror: any = null
  url: string
  constructor(url: string) { this.url = url; FakeSocket.all.push(this) }
  send(data: string) { this.sent.push(JSON.parse(data)) }
  close() { this.readyState = 3; this.onclose?.() }
  open() { this.readyState = 1; this.onopen?.() }
  emit(stream: string, data: unknown) { this.onmessage?.({data: JSON.stringify({stream, data})}) }
}
let clock = 0
let timerId = 0
const pending = new Map<number, {at: number; cb: () => void}>()
const timers = {
  set: (cb: () => void, ms: number) => { pending.set(++timerId, {at: clock + ms, cb}); return timerId },
  clear: (id: unknown) => { pending.delete(id as number) },
}
const advance = (ms: number) => {
  const target = clock + ms
  for (;;) {
    const next = [...pending.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0]
    if (!next) break
    pending.delete(next[0]); clock = next[1].at; next[1].cb()
  }
  clock = target
}
const make = () => createMarketStream({createSocket: url => new FakeSocket(url) as any, timers, now: () => clock, isHidden: () => false, silenceMs: 8000})

// 1) iki abone tek soket paylaşır, aynı akış bir kez abone olunur
let stream = make()
const a: string[] = [], b: string[] = []
const offA = stream.subscribe(['BTCUSDT@ticker', 'ethusdt@ticker'], (s, d) => a.push(s + ':' + (d as any).c))
const offB = stream.subscribe(['btcusdt@ticker'], (s, d) => b.push(s + ':' + (d as any).c))
assert.equal(FakeSocket.all.length, 1)
const sock = FakeSocket.all[0]
sock.open()
assert.deepEqual(sock.sent[0].params.sort(), ['btcusdt@ticker', 'ethusdt@ticker'])
sock.emit('btcusdt@ticker', {c: '1'})
sock.emit('ethusdt@ticker', {c: '2'})
assert.deepEqual(a, ['btcusdt@ticker:1', 'ethusdt@ticker:2'])
assert.deepEqual(b, ['btcusdt@ticker:1'])

// 2) abone ayrılınca yalnız artık kimsenin istemediği akış bırakılır
offB()
assert.equal(sock.sent.length, 1)
offA()
assert.equal(sock.sent[1].method, 'UNSUBSCRIBE')
assert.deepEqual(sock.sent[1].params.sort(), ['btcusdt@ticker', 'ethusdt@ticker'])
assert.equal(sock.readyState, 3)

// 3) kopunca üstel beklemeyle yeniden bağlanır ve akışları yeniden ister
stream = make()
const statuses: string[] = []
const before = FakeSocket.all.length
stream.subscribe(['solusdt@ticker'], () => {}, s => statuses.push(s))
const s1 = FakeSocket.all[before]
s1.open()
s1.close()
assert.equal(stream.status(), 'offline')
advance(999); assert.equal(FakeSocket.all.length, before + 1)
advance(2); assert.equal(FakeSocket.all.length, before + 2)
const s2 = FakeSocket.all[before + 1]
s2.open()
assert.deepEqual(s2.sent[0].params, ['solusdt@ticker'])
assert.equal(stream.status(), 'live')
assert.deepEqual(statuses, ['connecting', 'live', 'offline', 'live'])

// 4) veri gelmeyen (sessizce ölen) bağlantı kapatılıp yeniden denenir
s2.emit('solusdt@ticker', {c: '3'})
advance(10000)
assert.equal(s2.readyState, 3)
advance(1100)
assert.equal(FakeSocket.all.length, before + 3)

// 5) bozuk mesaj yayını bozmaz
const s3 = FakeSocket.all[before + 2]
s3.open()
s3.onmessage({data: 'not json'})
s3.onmessage({data: JSON.stringify({result: null, id: 1})})
console.log('market-stream: ok')
