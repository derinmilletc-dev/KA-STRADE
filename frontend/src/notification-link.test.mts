import assert from 'node:assert/strict'
import test from 'node:test'
import {parseNotificationLink} from './notification-link.ts'

test('parse link', () => {
  assert.deepEqual(parseNotificationLink('?tab=canli&ex=coinbase&alert=AVP-20DEC30-CDE'), {exchange: 'coinbase', alert: 'AVP-20DEC30-CDE'})
  assert.deepEqual(parseNotificationLink('?ex=binance&alert=BTCUSDT'), {exchange: 'binance', alert: 'BTCUSDT'})
  assert.deepEqual(parseNotificationLink('?ex=evil&alert=<script>'), {exchange: null, alert: null})
  assert.deepEqual(parseNotificationLink(''), {exchange: null, alert: null})
})

import {readLinkTarget} from './notification-link.ts'
test('read link target respects exchange and symbol rules', () => {
  assert.equal(readLinkTarget('binance', '?tab=pozisyonlar&ex=binance&read=BTCUSDT'), 'BTCUSDT')
  assert.equal(readLinkTarget('coinbase', '?tab=pozisyonlar&ex=coinbase&read=BIP-20DEC30-CDE'), 'BIP-20DEC30-CDE')
  assert.equal(readLinkTarget('binance', '?ex=coinbase&read=BTCUSDT'), null)       // başka borsa
  assert.equal(readLinkTarget('binance', '?read=BTCUSDT'), 'BTCUSDT')             // borsa yazılmamışsa kabul
  assert.equal(readLinkTarget('binance', '?read=%22%3E%3Cx'), null)               // geçersiz sembol
  assert.equal(readLinkTarget('binance', '?tab=canli'), null)
})
