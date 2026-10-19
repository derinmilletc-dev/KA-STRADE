export const MODERATOR_PERMISSIONS = [
  'customers.view', 'subscriptions.view', 'payments.view', 'support.view',
  'support.manage', 'events.view', 'approvals.create',
  'campaigns.manage',
] as const
export type ModeratorPermission = typeof MODERATOR_PERMISSIONS[number]
export const permissionLabels: Record<ModeratorPermission, string> = {
  'customers.view': 'Müşterileri görüntüle',
  'subscriptions.view': 'Abonelikleri görüntüle',
  'payments.view': 'Ödemeleri görüntüle',
  'support.view': 'Destek taleplerini görüntüle',
  'support.manage': 'Destek taleplerini yönet',
  'events.view': 'Etkinliği görüntüle',
  'approvals.create': 'Onay talebi oluştur',
  'campaigns.manage': 'Bilgilendirme duyurularını yönet',
}

export const moderatorSections = [
  {id: 'overview', label: 'Genel Bakış', permission: null},
  {id: 'customers', label: 'Müşteriler', permission: 'customers.view'},
  {id: 'subscriptions', label: 'Abonelikler', permission: 'subscriptions.view'},
  {id: 'payments', label: 'Ödemeler', permission: 'payments.view'},
  {id: 'payment-requests', label: 'Ödeme Talepleri', permission: 'payments.view'},
  {id: 'support', label: 'Destek Talepleri', permission: 'support.view'},
  {id: 'activity', label: 'Etkinlik', permission: 'events.view'},
  {id: 'approvals', label: 'Onay Talepleri', permission: 'approvals.create'},
  {id: 'campaigns', label: 'Duyurular', permission: 'campaigns.manage'},
] as const
export type ModeratorSection = typeof moderatorSections[number]['id']
export function visibleModeratorSections(permissions: readonly ModeratorPermission[]) {
  return moderatorSections.filter(section => section.permission === null || permissions.includes(section.permission))
}

export type ModeratorMe = {role: 'OWNER' | 'MODERATOR'; permissions: ModeratorPermission[]}
export type CustomerProfile = {
  user_id: string; email_masked: string; role: 'CUSTOMER'; active: boolean;
  created_at: string | null; email_verified: boolean; mfa_enabled: boolean;
}
export type CustomerPage = {items: CustomerProfile[]; total: number; limit: number; offset: number}
const plans = ['TRIAL', 'MASTER_MODE'] as const
const statuses = ['TRIALING', 'ACTIVE', 'PAST_DUE', 'UNPAID', 'CANCELLED', 'EXPIRED'] as const
export type CustomerSubscription = {
  user_id: string; plan: typeof plans[number] | null; subscription_status: typeof statuses[number] | null;
  current_period_end: string | null; cancel_at_period_end: boolean | null;
}
export type CustomerPayments = {user_id: string; payment_status: 'PAID' | 'FAILED' | 'veri yok'; last_failed_payment_at: string | null}
export type CustomerResource =
  | {kind: 'profile'; data: CustomerProfile}
  | {kind: 'subscription'; data: CustomerSubscription}
  | {kind: 'payments'; data: CustomerPayments}

export class ModeratorRequestError extends Error {
  readonly status: number
  readonly code: string
  constructor(status: number, code = '') {
    super(moderatorErrorMessage(status, code))
    this.status = status
    this.code = code
  }
}
export function moderatorErrorMessage(status: number, code = ''): string {
  if (code === 'mfa_required') return 'Devam etmek için profilinden iki adımlı doğrulamayı aç.'
  if (status === 429) return 'İstek sınırına ulaşıldı. Bir dakika bekleyip yeniden deneyin.'
  if (status === 404) return 'Bu müşteri bulunamadı. Kullanıcı numarasını kontrol edin.'
  if (status === 409) return 'Bu talep başka biri tarafından güncellendi, yenile.'
  if (status === 403) return 'Bu bölüm için izniniz yok. Yöneticinizden erişim isteyin.'
  if (status === 401) return 'Oturumunuz sona erdi. Yeniden giriş yapın.'
  return 'Bilgiler alınamadı. Lütfen biraz sonra yeniden deneyin.'
}
export function moderatorDate(value: string | null): string {
  if (!value) return 'veri yok'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'veri yok' : date.toLocaleString('tr-TR', {dateStyle: 'medium', timeStyle: 'short'})
}
export const subscriptionLabels: Record<NonNullable<CustomerSubscription['subscription_status']>, string> = {
  TRIALING: 'Deneme', ACTIVE: 'Aktif', PAST_DUE: 'Ödeme gecikmiş', UNPAID: 'Ödenmemiş', CANCELLED: 'İptal edilmiş', EXPIRED: 'Süresi dolmuş',
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function fail(): never {throw new ModeratorRequestError(502)}
function opaqueId(value: unknown): value is string {return typeof value === 'string' && /^[A-Za-z0-9_-]{1,160}$/.test(value)}
function nullableDate(value: unknown): value is string | null {
  return value === null || (typeof value === 'string' && Number.isFinite(Date.parse(value)))
}
export function parseModeratorMe(value: unknown): ModeratorMe {
  if (!record(value) || (value.role !== 'OWNER' && value.role !== 'MODERATOR') || !Array.isArray(value.permissions)) return fail()
  const permissions: ModeratorPermission[] = []
  for (const permission of value.permissions) {
    const allowed = MODERATOR_PERMISSIONS.find(item => item === permission)
    if (!allowed) return fail()
    permissions.push(allowed)
  }
  return {role: value.role, permissions}
}
export function parseCustomerProfile(value: unknown): CustomerProfile {
  if (!record(value) || !opaqueId(value.user_id) || typeof value.email_masked !== 'string' ||
    !/^(?:\*{3}|[A-Za-z0-9*]\*{3}@[A-Za-z0-9.-]+)$/.test(value.email_masked) || value.role !== 'CUSTOMER' ||
    typeof value.active !== 'boolean' || typeof value.email_verified !== 'boolean' ||
    typeof value.mfa_enabled !== 'boolean' || !nullableDate(value.created_at)) return fail()
  return {user_id: value.user_id, email_masked: value.email_masked, role: value.role, active: value.active,
    email_verified: value.email_verified, mfa_enabled: value.mfa_enabled, created_at: value.created_at}
}
export function parseCustomerPage(value: unknown): CustomerPage {
  if (!record(value) || !Array.isArray(value.items) || value.items.length > 50 ||
    typeof value.total !== 'number' || !Number.isSafeInteger(value.total) || value.total < 0 ||
    typeof value.limit !== 'number' || !Number.isSafeInteger(value.limit) || value.limit < 1 || value.limit > 50 ||
    typeof value.offset !== 'number' || !Number.isSafeInteger(value.offset) || value.offset < 0) return fail()
  return {items: value.items.map(parseCustomerProfile), total: value.total, limit: value.limit, offset: value.offset}
}
export function parseCustomerSubscription(value: unknown): CustomerSubscription {
  if (!record(value) || !opaqueId(value.user_id) || !nullableDate(value.current_period_end) ||
    (value.cancel_at_period_end !== null && typeof value.cancel_at_period_end !== 'boolean')) return fail()
  const plan = value.plan === null ? null : plans.find(item => item === value.plan)
  const status = value.subscription_status === null ? null : statuses.find(item => item === value.subscription_status)
  if (plan === undefined || status === undefined) return fail()
  return {user_id: value.user_id, plan, subscription_status: status, current_period_end: value.current_period_end, cancel_at_period_end: value.cancel_at_period_end}
}
export function parseCustomerPayments(value: unknown): CustomerPayments {
  if (!record(value) || !opaqueId(value.user_id) || !nullableDate(value.last_failed_payment_at) ||
    (value.payment_status !== 'PAID' && value.payment_status !== 'FAILED' && value.payment_status !== 'veri yok')) return fail()
  return {user_id: value.user_id, payment_status: value.payment_status, last_failed_payment_at: value.last_failed_payment_at}
}

export type PaymentQueueStatus = 'created' | 'pending' | 'approved' | 'rejected' | 'expired'
const queueStatuses: readonly PaymentQueueStatus[] = ['created', 'pending', 'approved', 'rejected', 'expired']
export type PaymentQueueCustomer = {
  user_id: string; email_masked: string; active: boolean; email_verified: boolean; mfa_enabled: boolean;
  registered_at: string | null; plan: CustomerSubscription['plan']; subscription_status: CustomerSubscription['subscription_status'];
  plan_period_end: string | null; membership_end: string | null;
  approved_count: number; rejected_count: number; expired_count: number;
}
export type PaymentQueueItem = {
  id: string; reference_code: string; amount: string; currency: 'USDT'; status: PaymentQueueStatus;
  transaction_hint: string | null; created_at: string; submitted_at: string | null; decided_at: string | null;
  decision_note: string | null; recommended_at: string | null; customer: PaymentQueueCustomer;
}
export type PaymentQueuePage = {
  items: PaymentQueueItem[]; total: number; limit: number; offset: number;
  summary: {pending: number; approved: number; rejected: number};
}
export const paymentQueueStatusLabels: Record<PaymentQueueStatus, string> = {
  created: 'Başlatıldı', pending: 'İnceleme bekliyor', approved: 'Onaylandı', rejected: 'Reddedildi', expired: 'Süresi doldu',
}
function count(value: unknown): value is number {return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0}
function nullableText(value: unknown): value is string | null {return value === null || typeof value === 'string'}
function parseQueueCustomer(value: unknown): PaymentQueueCustomer {
  if (!record(value) || !opaqueId(value.user_id) || typeof value.email_masked !== 'string' ||
    !/^(?:\*{3}|[A-Za-z0-9*]\*{3}@[A-Za-z0-9.-]+)$/.test(value.email_masked) ||
    typeof value.active !== 'boolean' || typeof value.email_verified !== 'boolean' || typeof value.mfa_enabled !== 'boolean' ||
    !nullableDate(value.registered_at) || !nullableDate(value.plan_period_end) || !nullableDate(value.membership_end) ||
    !count(value.approved_count) || !count(value.rejected_count) || !count(value.expired_count)) return fail()
  const plan = value.plan === null ? null : plans.find(item => item === value.plan)
  const status = value.subscription_status === null ? null : statuses.find(item => item === value.subscription_status)
  if (plan === undefined || status === undefined) return fail()
  return {user_id: value.user_id, email_masked: value.email_masked, active: value.active, email_verified: value.email_verified,
    mfa_enabled: value.mfa_enabled, registered_at: value.registered_at, plan, subscription_status: status,
    plan_period_end: value.plan_period_end, membership_end: value.membership_end,
    approved_count: value.approved_count, rejected_count: value.rejected_count, expired_count: value.expired_count}
}
function parseQueueItem(value: unknown): PaymentQueueItem {
  const status = record(value) ? queueStatuses.find(item => item === value.status) : undefined
  if (!record(value) || typeof value.id !== 'string' || !/^[a-f0-9]{32}$/.test(value.id) ||
    typeof value.reference_code !== 'string' || !/^KT-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$/.test(value.reference_code) ||
    typeof value.amount !== 'string' || !/^\d+\.\d{2}$/.test(value.amount) || value.currency !== 'USDT' || status === undefined ||
    !nullableText(value.transaction_hint) || typeof value.created_at !== 'string' || !Number.isFinite(Date.parse(value.created_at)) ||
    !nullableDate(value.submitted_at) || !nullableDate(value.decided_at) || !nullableText(value.decision_note) ||
    !nullableDate(value.recommended_at)) return fail()
  return {id: value.id, reference_code: value.reference_code, amount: value.amount, currency: value.currency, status,
    transaction_hint: value.transaction_hint, created_at: value.created_at, submitted_at: value.submitted_at,
    decided_at: value.decided_at, decision_note: value.decision_note, recommended_at: value.recommended_at,
    customer: parseQueueCustomer(value.customer)}
}
export function parsePaymentQueuePage(value: unknown): PaymentQueuePage {
  if (!record(value) || !Array.isArray(value.items) || value.items.length > 50 || !count(value.total) ||
    !count(value.limit) || value.limit < 1 || value.limit > 50 || !count(value.offset) || !record(value.summary) ||
    !count(value.summary.pending) || !count(value.summary.approved) || !count(value.summary.rejected)) return fail()
  return {items: value.items.map(parseQueueItem), total: value.total, limit: value.limit, offset: value.offset,
    summary: {pending: value.summary.pending, approved: value.summary.approved, rejected: value.summary.rejected}}
}
export type PaymentRecommendation = {id: string; recommended_at: string}
export function parsePaymentRecommendation(value: unknown): PaymentRecommendation {
  if (!record(value) || typeof value.id !== 'string' || !/^[a-f0-9]{32}$/.test(value.id) ||
    typeof value.recommended_at !== 'string' || !Number.isFinite(Date.parse(value.recommended_at))) return fail()
  return {id: value.id, recommended_at: value.recommended_at}
}
