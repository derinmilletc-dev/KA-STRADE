// Bar replay: geçmiş mumları tek tek açarak "o gün ne yapardım" diye çalışmak için. Saf mantık (React yok).
export const REPLAY_SPEEDS = [{id: 1, label: '1x', ms: 1000}, {id: 2, label: '2x', ms: 500}, {id: 4, label: '4x', ms: 250}, {id: 10, label: '10x', ms: 100}] as const
export const REPLAY_MIN = 20
export type ReplayState = {index: number; playing: boolean; speed: number}

export const canReplay = (length: number) => length > REPLAY_MIN + 1
/** Başlangıç: serinin yaklaşık %60'ı; en az REPLAY_MIN mum görünür, en az bir mum ileride kalır. */
export function replayStart(length: number): number {
  if (!canReplay(length)) return Math.max(0, length - 1)
  return Math.min(length - 2, Math.max(REPLAY_MIN, Math.floor(length * 0.6)))
}
/** index = son görünen mumun sırası. */
export const clampReplay = (index: number, length: number) => length <= 0 ? 0 : Math.max(Math.min(REPLAY_MIN, length - 1), Math.min(length - 1, Math.round(Number.isFinite(index) ? index : 0)))
export const stepReplay = (state: ReplayState, delta: number, length: number): ReplayState => {
  const index = clampReplay(state.index + delta, length)
  return {...state, index, playing: state.playing && index < length - 1}
}
export const replaySlice = <T,>(candles: readonly T[], index: number): T[] => candles.slice(0, clampReplay(index, candles.length) + 1)
export const speedMs = (speed: number) => REPLAY_SPEEDS.find(item => item.id === speed)?.ms ?? 1000
export const sanitizeSpeed = (value: unknown) => REPLAY_SPEEDS.some(item => item.id === value) ? value as number : 1
