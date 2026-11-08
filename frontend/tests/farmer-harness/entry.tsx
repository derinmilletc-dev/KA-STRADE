import React from 'react'
import {createRoot} from 'react-dom/client'
import FarmerPanel from '../../src/FarmerPanel'

const rec = {min_score: 92, margin_usdt: 50, leverage: 3, target_usdt: 1, max_loss_usdt: 1.5, dead_score: 75, dead_confirmations: 2, max_positions: 5, total_margin_cap: 100}
const limits = {margin_options: [25, 50, 75, 100]}
const prev = (over: any = {}) => ({margin_usdt: 50, leverage: 3, notional_usdt: 150, target_usdt: 1, max_loss_usdt: 1.5, est_cost_usdt: 0.2, target_pct: 0.8, stop_pct: 0.87, cost_over_target: 0.2, effective_capacity: 2, max_positions: 5, total_margin_cap: 100, verdict: 'GOOD', warnings: ['MARGIN_CAP_LIMITS'], ...over})
const pos = (o: any = {}) => ({grid_id: 'g1', symbol: 'BTCUSDT', side: 'LONG', engine_status: 'RUNNING', slot_state: 'OPEN', age_s: 1080, score: 96, target_usdt: 1, max_loss_usdt: 1.5, dead_score: 75, dead_consecutive: 0, margin_usdt: 50, notional_usdt: 150, est_cost_usdt: 0.2, target_pct: 0.8, stop_pct: 0.87, target_over_atr: 0.6, cost_over_target: 0.2, close_reason: 'OPEN', pnl_state: 'PENDING', verified_net_pnl: null, entry_price: 64231.5, tp_price: 64745.2, stop_price: 63690.1, mark_price: 64320.4, unrealized_net_usdt: 0.18, ...o})
const base = (o: any = {}) => ({ok: true, name: 'KAIS FARMER', available: true, blockers: [], state: 'RUNNING', configured: true, config: {...rec}, recommended: rec, provisional: true, limits, preview: prev(), arm: {active: true, expires_at: Date.now() / 1000 + 5 * 3600 + 1500},
  positions: [], scanner: [], slots: {capacity: 2, held: 0, open: 0, closing: 0, free: 2}, effective_capacity: 2, max_positions: 5, snapshot_ok: true, recovered: true,
  today: {basis: 'UTC_DAY', verified_net_pnl: 3.42, verified_count: 3, pending_count: 0, unverified_count: 0}, why: {code: 'NO_CANDIDATES', details: []}, hard_cap_usdt: 100, margin_used_usdt: 0, available_margin_usdt: 100, ...o})
const two = [pos(), pos({grid_id: 'g2', symbol: 'ETHUSDT', side: 'SHORT', age_s: 320, entry_price: 3120.45, tp_price: 3105.2, stop_price: 3138.9, mark_price: 3118.0, unrealized_net_usdt: -0.05})]
const scan = [{symbol: 'BTCUSDT', direction: 'LONG', score: 96, status: 'QUALIFIED', reason: null}, {symbol: 'SOLUSDT', direction: 'LONG', score: 93, status: 'WAITING_CONFIRMATION', reason: null}, {symbol: 'XRPUSDT', direction: 'SHORT', score: 88, status: 'REJECTED', reason: 'SCORE_TOO_LOW'}, {symbol: 'DOGEUSDT', direction: 'LONG', score: 94, status: 'COOLING_DOWN', reason: 'ENTRY_LOCKED'}]
export const SCENARIOS: Record<string, any> = {
  loading: 'loading', down: 'down', account: 'account',
  disabled: base({available: false, state: 'OFF', configured: false, config: null, blockers: ['LIVE_FLAG_OFF'], why: {code: 'UNAVAILABLE', details: []}, slots: null, effective_capacity: null}),
  onboarding: base({state: 'OFF', configured: false, config: null, arm: {active: false, expires_at: null}, why: {code: 'OFF', details: []}}),
  off: base({state: 'OFF', why: {code: 'OFF', details: []}}),
  running_empty: base({scanner: [{symbol: 'XRPUSDT', direction: 'SHORT', score: 88, status: 'REJECTED', reason: 'SCORE_TOO_LOW'}], why: {code: 'SCORE_TOO_LOW', details: [{code: 'SCORE_TOO_LOW', count: 1}, {code: 'MTF_NOT_CONFIRMED', count: 2}]}}),
  running_none: base({}),
  running_two: base({positions: two, scanner: scan, slots: {capacity: 2, held: 0, open: 2, closing: 0, free: 0}, margin_used_usdt: 100, available_margin_usdt: 0, why: {code: 'CAPACITY_FULL', details: []}}),
  running_one: base({positions: [pos()], scanner: scan, slots: {capacity: 2, held: 0, open: 1, closing: 0, free: 1}, margin_used_usdt: 50, available_margin_usdt: 50, why: {code: 'SCORE_TOO_LOW', details: [{code: 'SCORE_TOO_LOW', count: 2}]}}),
  five: base({positions: [pos()], config: {...rec, margin_usdt: 25}, slots: {capacity: 4, held: 0, open: 1, closing: 0, free: 3}, effective_capacity: 4, max_positions: 5, margin_used_usdt: 25, available_margin_usdt: 75}),
  zero_cap: base({positions: [], slots: {capacity: 0, held: 0, open: 0, closing: 0, free: 0}, effective_capacity: 0, why: {code: 'CAPACITY_ZERO', details: []}}),
  arm_expired: base({state: 'ENTRY_PAUSED_ARM', arm: {active: false, expires_at: null}, positions: [pos()], scanner: scan, slots: {capacity: 2, held: 0, open: 1, closing: 0, free: 1}, margin_used_usdt: 50, available_margin_usdt: 50, why: {code: 'ARM_EXPIRED', details: []}}),
  safety: base({state: 'ENTRY_PAUSED_SAFETY', why: {code: 'DAILY_LOSS', details: [{code: 'DAILY_LOSS_LIMIT', count: 1}]}}),
  stopping: base({state: 'STOPPING', positions: [pos()], slots: {capacity: 2, held: 0, open: 1, closing: 0, free: 1}, why: {code: 'STOP_BOT', details: []}, margin_used_usdt: 50, available_margin_usdt: 50}),
  intervention: base({state: 'INTERVENTION_REQUIRED', positions: [pos({engine_status: 'BLOCKED'})], slots: {capacity: 2, held: 0, open: 1, closing: 0, free: 1}, why: {code: 'INTERVENTION', details: []}, margin_used_usdt: 50, available_margin_usdt: 50}),
  pnl: base({positions: [pos({grid_id: 'a', symbol: 'ADAUSDT', slot_state: 'CLOSING', pnl_state: 'PENDING'}), pos({grid_id: 'b', symbol: 'LTCUSDT', slot_state: 'OPEN', pnl_state: 'UNVERIFIED'}), pos({grid_id: 'c', symbol: 'AVAXUSDT', pnl_state: 'VERIFIED', verified_net_pnl: 1.02})], slots: {capacity: 5, held: 0, open: 2, closing: 1, free: 2}, effective_capacity: 5, today: {basis: 'UTC_DAY', verified_net_pnl: null, verified_count: 0, pending_count: 1, unverified_count: 1}}),
}
const params = new URLSearchParams(location.search)
const name = params.get('s') || 'running_two'
const emergency = params.get('em') || ''
const sc = name === 'account' ? SCENARIOS.off : SCENARIOS[name]
const calls: string[] = ((window as any).__calls = [])
const get = async (path: string) => { calls.push('GET ' + path); if (sc === 'loading') return new Promise(() => {}); if (sc === 'down') throw new Error('x'); return sc }
const post = async (path: string, body?: any) => { calls.push('POST ' + path + (body ? ' ' + JSON.stringify(body) : '')); if (path === '/farmer/preview') return {preview: prev({verdict: body?.target_usdt < 0.4 ? 'TIGHT' : 'GOOD', warnings: body?.target_usdt < 0.4 ? ['TARGET_TOO_SMALL'] : []})}
  if (path === '/farmer/emergency-close') { if (emergency === 'fail') return {complete: false, closed: 0, failed: 1}; if (emergency === 'partial') return {complete: false, closed: 1, failed: 1}; return {complete: true, closed: 2, failed: 0} }
  if (path === '/farmer/start') { if (new URLSearchParams(location.search).get('start') === 'reject') throw new Error('{"code":"FARMER_NOT_STARTED"}'); return {ok: true, state: 'RUNNING'} }
  return {ok: true} }
createRoot(document.getElementById('root')!).render(<FarmerPanel get={get} post={post} accountConnected={name !== 'account'} onAsk={q => calls.push('ASK ' + q)} onConnect={() => calls.push('CONNECT')} nowSeconds={() => Date.now() / 1000}/>)
