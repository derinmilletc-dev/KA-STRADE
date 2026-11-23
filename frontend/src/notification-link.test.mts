import assert from 'node:assert/strict'
import test from 'node:test'
import {parseNotificationLink} from './notification-link.ts'

test('parse link', () => {
  assert.deepEqual(parseNotificationLink('?tab=canli&ex=coinbase&alert=AVP-20DEC30-CDE'), {exchange: 'coinbase', alert: 'AVP-20DEC30-CDE'})
  assert.deepEqual(parseNotificationLink('?ex=binance&alert=BTCUSDT'), {exchange: 'binance', alert: 'BTCUSDT'})
  assert.deepEqual(parseNotificationLink('?ex=evil&alert=<script>'), {exchange: null, alert: null})
  assert.deepEqual(parseNotificationLink(''), {exchange: null, alert: null})
})
