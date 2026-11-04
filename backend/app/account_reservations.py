"""Hesap seviyesinde ortak risk ve coin rezervasyonu — KALICI (SQLite), yalnız LIVE.

Manuel, otomatik (V25 / Original V2 / KAIS) ve Grid girişleri aynı defteri kullanır. Kontrol + kayıt tek SQLite
transaction'ında (`BEGIN IMMEDIATE`) yapılır: yazma kilidi okumadan önce alınır, bu yüzden "kontrol et sonra ekle" yarışı
süreç içinde de, yanlışlıkla ikinci bir süreç açılsa da kapalıdır. Bu çoklu-worker desteği DEĞİLDİR: durum sözlüğü, kullanıcı
kilidi ve Grid motorları hâlâ tek süreç varsayar (deploy/vps/kaistrade.service).

Yaşam döngüsü (her geçiş `UPDATE … WHERE state=beklenen`, rowcount==1; aynı rezervasyon iki kez kullanılamaz):

    RESERVED ──(emirden ÖNCE)──► SUBMITTED ──► CONSUMED   (pozisyon oluştu; risk artık açık pozisyon olarak görülür)
       │ TTL                          │  └────► RELEASED   (kesin red / iptal / borsada yok)
       └────► RELEASED                └ süreyle düşmez; yalnız plan durumu ya da Binance mutabakatıyla kapanır

`SUBMITTED` emir gitmeden ÖNCE yazıldığı için "emir gitti ama kayıt güncellenemedi" çökmesinde satır durur. Yeniden başlatmadan
sonra başka süreçten kalan `SUBMITTED` satırlar Binance ile mutabakat edilene kadar o kullanıcının yeni girişini engeller
(fail-closed). Demo bu modülü kullanmaz; dosya yalnız LIVE içindir.
"""
from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable, Iterator

ACCOUNT = "LIVE"
RESERVED, SUBMITTED, CONSUMED, RELEASED = "RESERVED", "SUBMITTED", "CONSUMED", "RELEASED"
ACTIVE = (RESERVED, SUBMITTED)
RESERVE_TTL = 120.0       # RESERVED emir gitmeden önceki kısa aşamadır; sonra düşer
GRID_TTL = 300.0
PENDING_PLAN_STATUS = "DOLUM BEKLİYOR"
CANCELLED_PLAN_STATUS = "İPTAL"
EPOCH = uuid.uuid4().hex   # bu sürecin kimliği; başka EPOCH'tan kalan SUBMITTED satırlar mutabakat bekler

SCHEMA = """
CREATE TABLE IF NOT EXISTS reservations(
  token TEXT PRIMARY KEY, owner TEXT NOT NULL, account TEXT NOT NULL DEFAULT 'LIVE', kind TEXT NOT NULL,
  symbol TEXT NOT NULL, worst_loss REAL NOT NULL DEFAULT 0, state TEXT NOT NULL, client_order_id TEXT NOT NULL DEFAULT '',
  epoch TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL,
  note TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS reservations_one_active_symbol
  ON reservations(owner, account, symbol) WHERE state IN ('RESERVED','SUBMITTED');
CREATE INDEX IF NOT EXISTS reservations_owner_state ON reservations(owner, state);
"""


class ReservationConflict(RuntimeError):
    """Aynı coin başka bir giriş için ayrılmış, ortak risk bütçesi yetmiyor ya da mutabakat bekleniyor."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind          # "symbol" | "risk" | "reconcile"


class ReservationUnavailable(RuntimeError):
    """Rezervasyon defteri okunamadı/yazılamadı: yeni giriş reddedilir (fail-closed)."""


class ReservationStore:
    def __init__(self, path: str = ":memory:") -> None:
        # isolation_level=None: otomatik transaction yok; BEGIN IMMEDIATE / COMMIT açıkça yönetilir.
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.executescript(SCHEMA)

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            else:
                self.db.execute("COMMIT")


_store: ReservationStore | None = None
_store_lock = threading.Lock()


def use_store(store: ReservationStore | None) -> None:
    """Testler ve açılış için: kullanılacak defteri belirler (None → bir sonraki çağrıda varsayılan dosya açılır)."""
    global _store
    with _store_lock:
        _store = store


def get_store() -> ReservationStore:
    global _store
    with _store_lock:
        if _store is None:
            try:
                from .local_storage import DATA_DIR
                _store = ReservationStore(str(DATA_DIR / "reservations_live.sqlite3"))
            except Exception as exc:  # noqa: BLE001 - dosya/izin/sqlite hatası → fail-closed
                raise ReservationUnavailable("Rezervasyon defteri açılamadı.") from exc
        return _store


def _guard(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except (ReservationConflict, ReservationUnavailable):
        raise
    except sqlite3.Error as exc:
        raise ReservationUnavailable("Rezervasyon defteri yazılamadı.") from exc


def _expire(db: sqlite3.Connection, now: float) -> None:
    """Emir gönderilmemiş (RESERVED) satırlardan süresi dolanlar düşer: emir hiç gitmemiştir. Başka süreçten kalan RESERVED satır
    da yalnız süreyle düşer (hemen silmek, aynı dosyayı kullanan canlı bir ikinci sürecin taze rezervasyonunu silerdi)."""
    db.execute("UPDATE reservations SET state=?, updated_at=?, note='EXPIRED' WHERE state=? AND expires_at<?", (RELEASED, now, RESERVED, now))


def reserve(owner: str, kind: str, symbol: str, worst_loss: float = 0.0, *, budget_left: float | None = None,
            committed: float = 0.0, ttl: float = RESERVE_TTL) -> str:
    """Coini ve (varsa) en kötü zararı ayırır. budget_left: bugün kalan günlük zarar bütçesi; committed: çalışan Grid'lerin zaten
    ayırdığı en kötü zarar. Başkasının ayırdığı (eşzamanlı ya da Grid) risk varsa bütçe kontrolü yapılır; yoksa tek işlemin mevcut
    kapıları aynen geçerlidir."""
    owner, symbol = str(owner or ""), str(symbol or "").upper()
    worst = max(0.0, float(worst_loss or 0.0))

    def run() -> str:
        store, now = get_store(), time.time()
        with store.tx() as db:
            _expire(db, now)
            stale = db.execute("SELECT symbol FROM reservations WHERE owner=? AND account=? AND state=? AND epoch<>?",
                               (owner, ACCOUNT, SUBMITTED, EPOCH)).fetchall()
            if stale:
                raise ReservationConflict(
                    "Yeniden başlatma öncesi gönderilmiş giriş emri(leri) Binance ile doğrulanmadı "
                    f"({', '.join(sorted({r['symbol'] for r in stale}))}); mutabakat bitene kadar yeni giriş yok.", kind="reconcile")
            if symbol:
                clash = db.execute("SELECT kind FROM reservations WHERE owner=? AND account=? AND symbol=? AND state IN (?,?)",
                                   (owner, ACCOUNT, symbol, RESERVED, SUBMITTED)).fetchone()
                if clash is not None:
                    raise ReservationConflict(f"{symbol} için aynı anda başka bir {clash['kind']} girişi hazırlanıyor; çift giriş engellendi.",
                                              kind="symbol")
            others = float(db.execute("SELECT COALESCE(SUM(worst_loss),0) FROM reservations WHERE owner=? AND account=? AND state IN (?,?)",
                                      (owner, ACCOUNT, RESERVED, SUBMITTED)).fetchone()[0]) + max(0.0, float(committed or 0.0))
            if budget_left is not None and others > 0 and worst > float(budget_left) - others + 1e-9:
                raise ReservationConflict(
                    f"Ortak günlük risk bütçesi yetmiyor: bu giriş {worst:.2f} USDT, kalan {max(float(budget_left) - others, 0):.2f} USDT "
                    f"(eşzamanlı girişler {others:.2f} USDT ayırdı).", kind="risk")
            token = uuid.uuid4().hex[:16]
            db.execute("INSERT INTO reservations(token,owner,account,kind,symbol,worst_loss,state,epoch,created_at,updated_at,expires_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (token, owner, ACCOUNT, kind, symbol, worst, RESERVED, EPOCH, now, now, now + ttl))
            return token
    return _guard(run)


def _transition(token: str | None, allowed: tuple[str, ...], new: str, note: str = "", **fields: Any) -> bool:
    if not token:
        return False
    sets = ["state=?", "updated_at=?", "note=?"] + [f"{k}=?" for k in fields]
    args = [new, time.time(), note, *fields.values(), token, *allowed]

    def run() -> bool:
        with get_store().tx() as db:
            cur = db.execute(f"UPDATE reservations SET {', '.join(sets)} WHERE token=? AND state IN ({','.join('?' * len(allowed))})", args)
            return cur.rowcount == 1
    return bool(_guard(run))


def mark_submitted(token: str | None, client_order_id: str) -> bool:
    """Emir Binance'e gitmeden HEMEN ÖNCE yazılır (tekrar çağrı güvenlidir). False: rezervasyon artık geçerli değil (süresi dolmuş/serbest) → emir gönderilmemeli."""
    return _transition(token, (RESERVED, SUBMITTED), SUBMITTED, "SUBMITTED", client_order_id=str(client_order_id or ""), epoch=EPOCH)


def consume(token: str | None, note: str = "") -> bool:
    return _transition(token, (RESERVED, SUBMITTED), CONSUMED, note)


def release(token: str | None, note: str = "") -> bool:
    return _transition(token, (RESERVED, SUBMITTED), RELEASED, note)


def verify(token: str | None) -> bool:
    """Mutabakat: bu süreçte doğrulandı (hâlâ bekleyen LIMIT vb.), engel kalkar."""
    return _transition(token, (SUBMITTED,), SUBMITTED, "VERIFIED", epoch=EPOCH)


# Planın girişi dolmuş / korumaya geçmiş / kapanmış demektir: rezervasyonun işi bitti, risk artık açık pozisyon olarak görülür.
FILLED_PLAN_STATUSES = frozenset({
    "KORUMA AKTİF", "KAPANDI", "CLOSED", "STOP AKTİF · TP KORUMASIZ (İZLEME)", "KAPATMA EMRİ GÖNDERİLDİ", "ACİL DURDURULDU",
    "YÖN UYUŞMAZLIĞI", "STOP BAŞARISIZ · KAPATILIYOR", "GÜVENLİK KAPATMASI · DOĞRULAMA BEKLİYOR", "KURTARILDI · KORUMA KONTROLÜ",
    "PNL DOĞRULANIYOR", "CLOSED_FOR_SECURITY", "FILLED"})
CANCELLED_PLAN_STATUSES = frozenset({CANCELLED_PLAN_STATUS, "CANCELLED"})


def settle_by_plan_status(token: str | None, plan_status: str | None) -> str:
    """Plan durumuna göre kapanış: bekleyen LIMIT → SUBMITTED kalır; iptal → RELEASED; bilinen dolum/koruma/kapanış durumları → CONSUMED.
    Plan yoksa ya da durum TANINMIYORSA SUBMITTED kalır: emir gitmiş olabilir, yanlışlıkla serbest bırakmak çift kullanıma yol açar."""
    if not token:
        return "NONE"
    row = get(token)
    if row is None:
        return "NONE"
    if row["state"] == RESERVED:
        release(token, "NOT_SUBMITTED")
        return RELEASED
    if row["state"] != SUBMITTED or plan_status is None or plan_status == PENDING_PLAN_STATUS:
        return row["state"]
    if plan_status in CANCELLED_PLAN_STATUSES:
        release(token, "PLAN_CANCELLED")
        return RELEASED
    if plan_status in FILLED_PLAN_STATUSES:
        consume(token, f"PLAN:{plan_status}")
        return CONSUMED
    return SUBMITTED


def get(token: str) -> dict[str, Any] | None:
    def run() -> dict[str, Any] | None:
        store = get_store()
        with store.lock:
            row = store.db.execute("SELECT * FROM reservations WHERE token=?", (token,)).fetchone()
            return dict(row) if row else None
    return _guard(run)


def rows(owner: str, *, states: tuple[str, ...] = ACTIVE) -> list[dict[str, Any]]:
    def run() -> list[dict[str, Any]]:
        store = get_store()
        with store.lock:
            return [dict(r) for r in store.db.execute(
                f"SELECT * FROM reservations WHERE owner=? AND account=? AND state IN ({','.join('?' * len(states))}) ORDER BY created_at",
                (str(owner or ""), ACCOUNT, *states))]
    return _guard(run)


def snapshot(owner: str) -> list[dict[str, Any]]:
    return rows(owner)


def unreconciled(owner: str) -> list[dict[str, Any]]:
    """Başka süreçten kalmış, Binance ile henüz doğrulanmamış SUBMITTED satırlar."""
    return [r for r in rows(owner, states=(SUBMITTED,)) if r["epoch"] != EPOCH]


def others_worst_loss(owner: str, exclude: str | None = None) -> float:
    return round(sum(r["worst_loss"] for r in rows(owner) if r["token"] != exclude), 8)


def update(token: str | None, worst_loss: float) -> bool:
    """Aktif rezervasyonun ayrılan en kötü zararını günceller (Grid planı hesaplanınca)."""
    if not token:
        return False

    def run() -> bool:
        with get_store().tx() as db:
            cur = db.execute("UPDATE reservations SET worst_loss=?, updated_at=? WHERE token=? AND state IN (?,?)",
                             (max(0.0, float(worst_loss or 0.0)), time.time(), token, RESERVED, SUBMITTED))
            return cur.rowcount == 1
    return bool(_guard(run))


def sweep(owner: str, plan_status_of: Callable[[str], str | None]) -> list[dict[str, Any]]:
    """Bu süreçte gönderilmiş SUBMITTED satırları plan durumuna göre kapatır. Döndürülen liste çözülemeyenlerdir
    (planı bulunamayan satırlar); bunlar için Binance'e sorulur. `plan_status_of(client_order_id)` plan durumunu ya da None verir."""
    open_rows: list[dict[str, Any]] = []
    for row in rows(owner, states=(SUBMITTED,)):
        status = plan_status_of(row["client_order_id"]) if row["client_order_id"] else None
        if status is None:
            open_rows.append(row)
            continue
        if settle_by_plan_status(row["token"], status) == SUBMITTED and row["epoch"] != EPOCH:
            verify(row["token"])           # plan var ve emir hâlâ bekliyor: mutabakat tamam
    return [r for r in open_rows]


def resolve(token: str, outcome: str) -> str:
    """Binance sorgusunun sonucu: PENDING (emir hâlâ bekliyor) → doğrulandı; FILLED → CONSUMED; GONE (borsada yok/iptal, dolum yok) → RELEASED."""
    if outcome == "PENDING":
        verify(token)
        return SUBMITTED
    if outcome == "FILLED":
        consume(token, "EXCHANGE:FILLED")
        return CONSUMED
    if outcome == "GONE":
        release(token, "EXCHANGE:GONE")
        return RELEASED
    return SUBMITTED


@contextlib.contextmanager
def hold(owner: str, kind: str, symbol: str, worst_loss: float = 0.0, *, budget_left: float | None = None,
         committed: float = 0.0) -> Iterator[str]:
    token = reserve(owner, kind, symbol, worst_loss, budget_left=budget_left, committed=committed)
    try:
        yield token
    finally:
        release(token, "HOLD_EXIT")
