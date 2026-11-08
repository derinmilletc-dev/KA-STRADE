# KAIS FARMER — Faz B: Sözleşmeler, Şema ve Prototip Sonuçları

Durum: **TASARIM + KÜÇÜK PROTOTİP.** Depoda üretim koduna dokunulmadı; canlı, Grid, DCA ve Original V2 davranışı değişmedi.
Kapsam: Faz A onay mesajındaki 13. madde (kesin modül sözleşmeleri, şema, durum geçişleri, test mimarisi, 4 prototip).
Kullanıcıya görünen ad her yerde yalnızca **KAIS FARMER**.

> Not: "Faz A raporu" bu oturumda depoda/ekte yoktu; yalnızca onay kararları (1–13) elimde. Aşağıdaki sözleşmeler bu kararlara ve mevcut koda dayanır.
> Faz A'da tanımlanmış olup bende olmayan ayrıntılar (özellikle **giriş onayı kuralları**) §14'te "sahibinden teyit" olarak ayrıldı; uydurulmadı.

---

## 0. Özet

| # | Prototip | Sonuç | Kanıt |
|---|---|---|---|
| 1 | Farmer = bir GridEngine satırı | **EVET, tek küçük değişiklikle** | Stok `GridEngine` 1 kademeyi reddediyor (`2 <= len(levels)`). `MIN_LEVELS = 2` sınıf özelliği (varsayılan aynı) ile `DcaEngine` alt sınıfı olarak tam yaşam döngüsü çalıştı: giriş → stop → hedef → hedef/stop/piyasa kapanışı, yeniden başlatma kurtarması, yanıt kaybı, acil durdurma, 5 bağımsız satır. 15/15 test |
| 2 | TAKE_PROFIT_MARKET güvenle eklenebilir | **EVET (sahte borsa + adaptör parametreleri); GERÇEK Binance'te DOĞRULANMADI** | Adaptör `algoOrder` ile `TAKE_PROFIT_MARKET + reduceOnly + tam miktar` gönderiyor. Grid/DCA hedefleri (`kg-`/`kd-` + `T`) normal uçtan gitmeye devam ediyor. Mevcut Grid/DCA/nötr/filtre/testnet testleri yamalı kopyada **114/114** geçti |
| 3 | Plan `verified_plan_pnl`'e mevcut sözleşmeyle verilebilir | **EVET, koşulla** | Gerçek fonksiyon, Farmer planıyla çalıştı. Plan, hedef emrin `algoId`'sini `protection_ids` içinde (veya kapanış `orderId`'sini `exchange_order_ids` içinde) taşımazsa kapanış kanıtlanamaz ve fonksiyon hata verir (güvenli taraf). `funding_included` her zaman `False` → funding ayrı eklenmeli |
| 4 | 5 motor + orkestratör kabul edilebilir yük | **EVET, toplu okuma şartıyla** | Mevcut tur davranışı doğrusal: 5 motor = **30 REST/tur** (360/dk, 5 sn turda). Orkestratör turda tek toplu `positionRisk` + sembol başına tek `openOrders` ile **11 REST/tur (132/dk, %63 az)**. CPU ihmal edilebilir (≈1,5–2 ms/tur, 5 motor) |

**Büyük çaplı implementation'a geçiş önerisi: koşullu EVET.** Önce §13'teki 5 doğrulanmamış varsayım Testnet'te kapatılmalı (özellikle TAKE_PROFIT_MARKET'in algo ucunda kabulü).

Prototip dosyaları (atılabilir, depoya girmedi): `proto3_pnl.py`, `pkg/test_proto12.py`, `pkg/proto4_load.py`, `pkg/farmer_proto.py`, `pkg/patches.diff` (scratchpad).

---

## 1. Mimari ve modül haritası

```
KAIS FARMER orchestrator (tek, hesap başına)         ← slot, cooldown, refill, aday önbelleği, bot durumu, global güvenlik, marjin tavanı
   │  her pozisyon için bir FarmerEngine satırı (grid tablosu, spec.meta.kind="FARMER", grid_id = farmer-<kısa kimlik>)
   ├── FarmerFeed        → ortak kline akışı (BinanceKlineStream) talep birleşimi
   ├── CandidateCache    → kapanmış mumdan gelen adaylar
   ├── ConfirmationGate  → giriş onayı + risk/maliyet/ulaşılabilirlik
   ├── MarginGuard       → toplam marjin tavanı + pozisyon sayısı (atomik rezervasyon)
   ├── EntryAttemptLedger→ aynı mum + aynı sembol = en fazla 1 gerçek giriş denemesi (DB UNIQUE)
   ├── SnapshotPort      → motorlara tur başına tek toplu okuma
   └── PnlVerifier       → verified_plan_pnl (yeniden kullanım) + funding (ayrı income sorgusu)
```

Önerilen dosyalar (yeni; mevcutlara minimum dokunuş):

| Dosya | İçerik |
|---|---|
| `backend/app/farmer_engine/engine.py` | `FarmerEngine(DcaEngine)`, `farmer_client_order_id`, `farmer_spec` |
| `backend/app/farmer_engine/orchestrator.py` | `FarmerOrchestrator`, durum makinesi |
| `backend/app/farmer_engine/feed.py` | `FarmerFeed`, `CandidateCache` |
| `backend/app/farmer_engine/confirm.py` | `ConfirmationGate`, `Feasibility`, `DeadDetector` |
| `backend/app/farmer_engine/margin.py` | `MarginGuard`, `EntryAttemptLedger` |
| `backend/app/farmer_engine/pnl.py` | `FarmerPnlVerifier` |
| `backend/app/farmer_engine/snapshot.py` | `SnapshotPort` |
| `backend/app/farmer_live.py` | API uçları + canlı çalıştırıcı (`grid_live.py` / `dca_live.py` kalıbı) |
| `backend/tests/test_farmer_*.py` | §12 |

**Yeniden kullanılan (değiştirilmeyen) davranış:** emir önce deftere yazılır, kimlik önceden bellidir; yanıt kaybında önce sorulur; defter↔borsa uyuşmazlığında dur; stop dolu miktarın tamamını kapsar ve yeni stop doğrulanmadan eskisi iptal edilmez; acil durdurma kapanışı borsadan doğrular (`GridEngine`/`DcaEngine`).

---

## 2. Mevcut koda yapılacak değişiklikler (Faz C'de; hepsi mevcut davranışı korur)

Prototipte 14 küçük yama kopya paket üzerinde uygulandı (29 satır eklendi, 10 mevcut satır değişti; çoğu import ve koşul satırı) ve mevcut testler geçti. Repoya girecek olanlar:

1. `grid_engine/ports.py` — `TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"` sabiti.
2. `grid_engine/engine.py` — `MIN_LEVELS = 2` sınıf özelliği; kademe doğrulaması bunu kullanır (varsayılan = bugünkü 2).
3. `grid_engine/fake_exchange.py` — TAKE_PROFIT_MARKET: lehte tetik (SELL: fiyat ≥ tetik; BUY: fiyat ≤ tetik), piyasa dolumu, `tp_slippage` ayarı, fiyat zaten geçmişse `-2021`.
4. `grid_engine/live_adapter.py` —
   - `is_algo_order(client_id)`: rol `S` her zaman; rol `T` yalnız `kf-` önekliyse algo ucundan yönetilir. `kg-`/`kd-` hedefleri LIMIT'tir ve normal uçta KALIR.
   - `place_order`: `STOP_MARKET` ve `TAKE_PROFIT_MARKET` aynı algo gövdesiyle (`reduceOnly=true`, `quantity`, `triggerPrice`; `closePosition` KULLANILMAZ).
   - `_algo_open_info`: tür, satırdaki `orderType`/`type` alanından okunur (yoksa `STOP_MARKET`).
5. `original_v2_live.shared_original_symbols` — Farmer talebini her zaman birleşime katar (§7). Farmer kapalıyken davranış birebir aynı.
6. `grid_live.GRID_CLIENT_ID`'e dokunulmaz; Farmer kendi `FARMER_CLIENT_ID = ^kf-…` düzenini ve kendi akış eşleştirmesini taşır.

Hedef emri rolü `T` kalır: `grid_net()`, `stats`, `recover`, `emergency_stop` değişmeden çalışır.

---

## 3. Veri şeması (SQLite `GridStore` ile aynı dosya; `CREATE TABLE IF NOT EXISTS` + mevcut `MIGRATIONS` kalıbı)

Motor satırları mevcut `grid` / `orders` / `alerts` tablolarını kullanır. Yeni tablolar:

```sql
-- Orkestratör durumu (hesap başına tek satır)
CREATE TABLE IF NOT EXISTS farmer_bot(
  account_key TEXT PRIMARY KEY,              -- kullanıcı + hesap
  state TEXT NOT NULL,                       -- §4.1
  settings TEXT NOT NULL,                    -- FarmerSettings JSON (kayıt anında doğrulanmış)
  pause_reason TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL, created_at REAL NOT NULL);

-- Aynı mum + aynı sembol = en fazla bir giriş emri denemesi (ATOMİK KİLİT)
CREATE TABLE IF NOT EXISTS farmer_entry_attempt(
  account_key TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL, candle_open_ms INTEGER NOT NULL,
  grid_id TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'SENT',   -- SENT | REJECTED_TEMP | FILLED | ...
  at REAL NOT NULL,
  PRIMARY KEY(account_key, symbol, interval, candle_open_ms));

-- Toplam marjin rezervasyonu (giriş denemesiyle AYNI işlemde yazılır)
CREATE TABLE IF NOT EXISTS farmer_margin_hold(
  grid_id TEXT PRIMARY KEY, account_key TEXT NOT NULL, margin_usdt REAL NOT NULL,
  state TEXT NOT NULL,                       -- HELD | OPEN | RELEASED
  at REAL NOT NULL, released_at REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS farmer_hold_acct ON farmer_margin_hold(account_key, state);

-- Aday önbelleği (son kapanmış mum başına)
CREATE TABLE IF NOT EXISTS farmer_candidate(
  account_key TEXT NOT NULL, symbol TEXT NOT NULL, candle_open_ms INTEGER NOT NULL,
  direction TEXT NOT NULL, score REAL NOT NULL, snapshot TEXT NOT NULL,   -- analysis/mtf alanları (JSON)
  status TEXT NOT NULL DEFAULT 'NEW',        -- NEW | CONFIRMING | REJECTED | ENTERED | EXPIRED
  reject_reason TEXT NOT NULL DEFAULT '', at REAL NOT NULL,
  PRIMARY KEY(account_key, symbol, candle_open_ms));

-- Pozisyon sonucu: HEDEF ile DOĞRULANMIŞ ayrı sütunlar
CREATE TABLE IF NOT EXISTS farmer_result(
  grid_id TEXT PRIMARY KEY, account_key TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
  opened_at REAL NOT NULL, closed_at REAL NOT NULL DEFAULT 0,
  target_usdt REAL NOT NULL, max_loss_usdt REAL NOT NULL, est_cost_usdt REAL NOT NULL,
  close_reason TEXT NOT NULL DEFAULT 'OPEN',          -- TARGET | STOP | MARKET_CLOSE | DEAD_CLOSE | EMERGENCY
  pnl_state TEXT NOT NULL DEFAULT 'PENDING',          -- PENDING | VERIFIED | UNVERIFIED
  gross_pnl REAL, commission REAL, funding REAL, verified_net_pnl REAL,   -- NULL = henüz doğrulanmadı
  verify_attempts INTEGER NOT NULL DEFAULT 0, verify_error TEXT NOT NULL DEFAULT '');

-- Ölü pozisyon sayacı (art arda KAPANMIŞ mum)
CREATE TABLE IF NOT EXISTS farmer_dead_watch(
  grid_id TEXT PRIMARY KEY, last_candle_ms INTEGER NOT NULL DEFAULT 0, consecutive INTEGER NOT NULL DEFAULT 0);
```

Kurallar: (a) `farmer_entry_attempt` satırı **giriş emri gönderilmeden ÖNCE** `INSERT OR IGNORE` ile yazılır; `rowcount != 1` ise emir GÖNDERİLMEZ. Geçici ret sonrası satır silinmez → aynı mumda yeniden denenmez. (b) Rezervasyon (`farmer_margin_hold`) ile deneme kaydı **tek işlemde** (`BEGIN IMMEDIATE`) yazılır. (c) Migration: yeni tablolar için geri alma = `DROP TABLE farmer_*`; mevcut tablolara sütun eklenmez.

---

## 4. Durum geçişleri

### 4.1 Orkestratör (`farmer_bot.state`)

| Durum | Anlam | Yeni giriş | Açık pozisyon |
|---|---|---|---|
| `OFF` | Kapalı | hayır | — |
| `RUNNING` | ARM geçerli, güvenlik tamam | **evet** | yönetilir |
| `ENTRY_PAUSED_ARM` | ARM süresi doldu | **hayır** | **dokunulmaz**; TP/STOP sürer; acil kapanış YOK |
| `ENTRY_PAUSED_SAFETY` | Veri bayat / akış kopuk / günlük zarar / doğrulanamayan PnL eşiği / borsa hatası | hayır | yönetilir (koruma sürer) |
| `STOPPING` | Kullanıcı durdurdu | hayır | kullanıcı seçimi: bırak (korumalı) veya acil kapat |
| `INTERVENTION_REQUIRED` | Motor müdahale istedi (stop kurulamadı vb.) | hayır | koruma iptal EDİLMEZ |

Geçişler: `OFF→RUNNING` (kullanıcı başlatır + ARM geçerli + kapılar) · `RUNNING→ENTRY_PAUSED_ARM` (ARM biter) · `ENTRY_PAUSED_ARM→RUNNING` (ARM yeniden geçerli; **otomatik**, kullanıcı "devam" demek zorunda değil) · `RUNNING→ENTRY_PAUSED_SAFETY` (güvenlik tetiği) · `ENTRY_PAUSED_SAFETY→RUNNING` (tetik temizlendi) · herhangi → `INTERVENTION_REQUIRED` (bir motor bildirirse) · `*→STOPPING→OFF`.

### 4.2 Motor (mevcut durumlar aynen): `CREATED → RUNNING → {COMPLETED | STOPPED | EMERGENCY_CLOSED}`; `BLOCKED` / `INTERVENTION_REQUIRED` mevcut anlamıyla. Farmer'a özel ayrım `close_reason` ile (`TARGET | STOP | MARKET_CLOSE | DEAD_CLOSE | EMERGENCY`).

### 4.3 Slot yaşam döngüsü
`FREE → HELD (rezervasyon + deneme kaydı) → OPEN (giriş doldu, koruma doğrulandı) → CLOSING → FLAT (borsada pozisyon 0 doğrulandı) → COOLDOWN → FREE`. Slot **borsa "flat" doğrulanınca** boşalır; PnL doğrulaması **eşzamansız** sürer (§9).

---

## 5. Motor ↔ orkestratör iletişimi

Senkron motor, orkestratör iş parçacığı/olay döngüsü kalıbı `grid_live.py` ile aynı (motor ayrı iş parçacığında, adaptör olay döngüsüne köprü). Motor orkestratörü **çağırmaz**; orkestratör motor durumunu okur ve olay kuyruğuyla bilgilenir:

```python
class FarmerEngine(DcaEngine):
    MIN_LEVELS = 1
    def __init__(self, exchange: ExchangePort, store: GridStore, spec: GridSpec, *, on_event: Callable[[FarmerEvent], None] | None = None, **kw): ...
    def tick(self) -> None: ...                      # mevcut (DcaEngine) – önce koruma, sonra gönderim
    def protect_now(self) -> bool: ...               # akış bildirimi: giriş AÇMAZ
    def recover(self) -> tuple[bool, str]: ...       # yeniden başlatma
    def emergency_stop(self) -> str: ...             # borsadan doğrulama zorunlu
    def close_reason(self) -> str: ...               # OPEN|TARGET|STOP|MARKET_CLOSE|DEAD_CLOSE|EMERGENCY
    def estimate(self) -> dict: ...                  # TAHMİN (defter); doğrulanmış değil
    def reduce_if_dead_profit(self, safety_margin_usdt: float) -> bool: ...   # §6.4: yalnız kârdaysa reduce-only kapanış
@dataclass(frozen=True) class FarmerEvent: grid_id: str; kind: Literal["FILLED","PROTECTED","CLOSED","BLOCKED","INTERVENTION"]; at: float; detail: dict
```

Orkestratör ana turu (5 sn):

```python
class FarmerOrchestrator:
    def start(self, settings: FarmerSettings) -> FarmerState: ...
    def stop(self, *, close_positions: bool) -> FarmerState: ...
    def tick(self) -> None:
        """1) ARM/güvenlik kapıları → durum  2) snapshot.begin_tick(semboller)  3) her motor: tick()  4) kapanan motorlar: slot FLAT, doğrulama kuyruğuna
           5) DEAD denetimi (yeni kapanmış mumda)  6) RUNNING ise: aday önbelleğinden refill (boş slot kadar)"""
    def on_stream_event(self, payload: dict) -> None: ...        # kullanıcı akışı: ilgili motorda protect_now()
    def snapshot(self) -> FarmerView: ...                        # panel: hedef/doğrulanmış AYRI, yaş, maliyet/hedef
```

Refill: boş slot sayısı = `min(max_positions, floor(margin_cap / position_margin)) − (HELD+OPEN+CLOSING)`; adaylar skora göre; her biri için **§6 onay → §3 kilit → emir**.

---

## 6. Giriş, onay, ulaşılabilirlik, ölü pozisyon

### 6.1 Ayarlar (`FarmerSettings`, doğrulama kaydedilmeden önce)

```python
@dataclass(frozen=True)
class FarmerSettings:
    position_margin_usdt: float       # canlı izinli: 25 | 50 | 75 | 100  (150 yalnız araştırma — §13)
    leverage: int = 3
    profit_target_usdt: float = 1.0   # NET hedef
    max_loss_usdt: float = 1.5        # NET
    min_score: int = 92
    dead_score: int | None = 75       # None = KAPALI
    max_positions: int = 5
    total_margin_cap_usdt: float = 100.0   # SERT; ayardan yükseltilemez (kodda tavan)
    variant: Literal["CONFIDENCE", "STRATEGY_QUALITY"] = "CONFIDENCE"   # backtest sonrası belirlenir
```
Etkin pozisyon sayısı = `min(max_positions, floor(total_margin_cap / position_margin))`. **Toplam marjin tavanı her zaman üstün sınırdır.**

### 6.2 Yeni indicator yok — mevcut alanlar (kod okunarak)

| Gereksinim | Mevcut alan |
|---|---|
| Yön | `analysis["direction"]` (`LONG`/`SHORT`/`BEKLE`) ve MTF `dir15m/dir1h/dir4h` (`mtf.alignment`, `mtf.entry_permission`) |
| Momentum | `analysis["momentum"]` (`Pozitif`/`Negatif`/`Nötr`; MACD histogramı + RSI'dan türer) |
| Skor | `analysis["confidence"]` |
| Tuzak / kırılım | `analysis["radar"]["trap_score"]`, `["breakout_quality"]` |
| SHORT filtresi | `mtf["short_filter_v2"]["accepted"]` |
| ATR | `analysis["atr"]` |

Hiçbir yeni EMA/MACD/RSI kurulmaz. Onay yeterli alan bulamazsa (örn. tek zaman diliminde momentum yok) **kapanmış mumdan asgari** doğrulama üretilir; bu bir istisna kaydıdır (§14-3).

### 6.3 Onay sözleşmesi

```python
@dataclass(frozen=True)
class Feasibility:
    notional: float; target_pct: float; stop_pct: float; est_round_trip_cost_usdt: float
    cost_over_target: float; atr_pct: float | None; target_over_atr: float | None
    verdict: Literal["FEASIBLE","MARGINAL","UNLIKELY","UNKNOWN"]      # UNKNOWN = veri yok → GİRİŞ YOK
    message_tr: str       # "Bu ayarla +1 USDT net hedef için yaklaşık %0,80 fiyat hareketi gerekiyor."

class ConfirmationGate:
    def evaluate(self, c: FarmerCandidate, s: FarmerSettings, now_ms: int) -> ConfirmationResult: ...
@dataclass(frozen=True)
class ConfirmationResult: passed: bool; reasons: tuple[str, ...]; feasibility: Feasibility; checks: dict[str, Any]
```

- Varyant **A (CONFIDENCE)**: `confidence ≥ min_score` + Farmer giriş onayı + Farmer risk/maliyet kontrolleri.
- Varyant **B (STRATEGY_QUALITY)**: A + `trap_score` + `breakout_quality` + MTF + (SHORT ise) `short_filter_v2`.
- **Ulaşılabilirlik gerçek kapıdır:** `verdict ∈ {UNLIKELY, UNKNOWN}` → giriş yok. (Eşikler Faz C'de backtest verisiyle belirlenir: `target_over_atr` ve `cost_over_target`.)
- Hesap (prototipte doğrulandı): `maliyet = notional·0,0005·2 + tampon`, `hedef% = (hedef + maliyet)/notional`, `stop% = (maks_zarar − maliyet)/notional`. Kaydetmeden önce ekranda **aynı fonksiyon** gösterir.

### 6.4 DEAD

`dead_score is not None` iken, **art arda 2 KAPANMIŞ mumda**: `confidence < dead_score` **VE** yön ters **VE** momentum ters. "Ters" = pozisyon LONG ise `direction=="SHORT"`/`momentum=="Negatif"`; SHORT ise tersi. `BEKLE`/`Nötr` ters sayılmaz. Sayaç `farmer_dead_watch`'ta, **yalnız yeni kapanmış mum açılış zamanı değişince** artar (aynı mum iki kez sayılmaz).
- DEAD + **kârda** (tahmini net > maliyet + güvenlik payı): `reduce_if_dead_profit()` → reduce-only MARKET kapanış (`role C`, borsadan doğrulanır).
- DEAD + **zararda**: **kapatma YOK**; borsadaki STOP yönetir. Zaman aşımı yok. Pozisyon yaşı panelde zorunlu.

### 6.5 Aynı mum kuralı
Akış: aday → onay → **`INSERT OR IGNORE farmer_entry_attempt`** → kazandıysa emir. Onay aynı mumda tekrar çalışabilir (slot boşaldı); kayıt varsa emir yok. Prototip: 64 eşzamanlı denemede 1 kazanan.

---

## 7. Tarayıcı ↔ ortak kline akışı

Bulgu: `BinanceKlineStream.configure(symbols, intervals)` sembol kümesini **değiştirir** (tek sahip varsayımı) ve en çok **100** sembol kabul eder. Mevcut çok-kullanıcı yolunda `shared_original_symbols` talep birleşimi (`application.state.original_symbol_demands`) kuruyor, ama yalnız `live_registry.enabled()` iken ve fazlasını `union[:100]` ile **sessizce kesiyor**.

Sözleşme:
```python
class FarmerFeed:
    async def set_demand(self, symbols: list[str]) -> None:
        """Farmer talebini 'farmer' anahtarıyla demands'a yazar; birleşimi TEK configure çağrısıyla uygular (aralıklar: 15m/30m/1h/4h)."""
    def closed_candles(self, symbol: str, interval: str, n: int) -> list[Candle] | None:
        """Yalnız KAPANMIŞ mumlar; abone değil / bayat / eksik ise None (→ 'veri yok', giriş yok)."""
```
- Farmer talebi **her zaman** birleşime girer (live_registry bayrağından bağımsız); Farmer kapalıyken Original V2 davranışı aynı.
- Birleşim 100'ü aşarsa Farmer sembolleri **kesilmez**; Farmer evreni, Original V2 evreniyle birleşince ≤100 kalacak şekilde sınırlandırılır ve her taramada `stream.is_subscribed()` doğrulanır. Abone olmayan sembol = veri yok.
- **Veri paylaşımı ≠ durum paylaşımı:** Farmer kendi `grid`/`orders`/`farmer_*` kayıtlarını tutar; Original V2 plan/ledger'ına yazmaz, okumaz.
- Hata mesajlarındaki "Original V2" metni dahili; kullanıcıya görünmez.

---

## 8. TP / STOP adapter sözleşmesi

| Emir | Tür | Yön | Miktar | Flag | Uç | Kimlik rolü |
|---|---|---|---|---|---|---|
| Giriş | `MARKET` | giriş yönü | notional/fiyat, adım aşağı yuvarlanır | — | `/fapi/v1/order` | `E` |
| Stop | `STOP_MARKET` | çıkış | **gerçek dolum miktarı** | `reduceOnly` | `/fapi/v1/algoOrder` | `S` |
| Hedef | **`TAKE_PROFIT_MARKET`** | çıkış | **gerçek pozisyon miktarı (TAM)** | `reduceOnly` | `/fapi/v1/algoOrder` | `T` |
| Piyasa kapanış | `MARKET` | çıkış | kalan | `reduceOnly` | `/fapi/v1/order` | `C` |

Kurallar: önce stop, sonra hedef (mevcut sıra). Hedef koruyucu **değildir**: iptal/yenilemede önce eski hedef iptal edilir, sonra yenisi konur (aynı miktarı iki emir kapatmasın). Fiyat hedefi zaten geçmişse (`-2021`): kâr beklemeden **reduce-only piyasa kapanışı** (prototipte doğrulandı). Hedef fiyatı **gerçek ortalama girişten** (`avg_price`) yeniden hesaplanır. `LIMIT` hedef kullanılmaz. `closePosition` kullanılmaz (karar: `reduceOnly` + tam miktar).

Kimlik: `kf-<grid>-<L|S><kademe 01><rol><rev>` (≤36). `kg-`/`kd-` ile çakışmaz; `ROLE_PATTERN` aynen çalışır.

---

## 9. PnL doğrulama sözleşmesi

```python
class FarmerPnlVerifier:
    async def verify(self, client: BinanceLiveClient, plan: dict[str, Any]) -> FarmerVerifiedPnl: ...
@dataclass(frozen=True)
class FarmerVerifiedPnl:
    gross: float; commission: float; funding: float; net: float      # net = gross − commission + funding
    exit_price: float | None; opened_at: str | None; attribution: str; funding_included: Literal[True]
```
1. **Yeniden kullanım:** `v25_execution.verified_plan_pnl(client, plan)` aynen çağrılır (değiştirilmez). Farmer planı şunları taşımalı: `symbol, direction, created_at, intent_id, entry_order_id/exchange_order_ids, entry_client_order_id, protection_ids` (**hedef ve stop algoId'leri**), `close_client_order_ids` (varsa DEAD/acil kapanış).
2. **Funding (ayrı):** `GET /fapi/v1/income?incomeType=FUNDING_FEE&symbol=…&startTime=opened_at&endTime=closed_at+tampon` (`original_profit_guard._funding_paid` kalıbı; **endTime eklenir**, 1000 satırı aşarsa sayfalanır). İşaret: `income` ödenen negatiftir → `net += Σ income`. Okunamazsa `UNVERIFIED` (varsayılan 0 UYDURULMAZ).
3. **Hedef ≠ sonuç:** panel iki ayrı alan gösterir — `Hedef: +1,00 USDT` ve `Doğrulanmış net: +0,97 USDT`. Prototipte kayma 0,10 iken tahmini net 0,8995 çıktı.
4. **Başarısızlık:** `LiveExchangeError` → `pnl_state=UNVERIFIED`, `verify_attempts++`, üstel bekleme ile yeniden. **Slotu bloklamaz**; ardışık N doğrulanamayan sonuç → `ENTRY_PAUSED_SAFETY` (N Faz C'de ayarlanır). Farmer, V25'in genel "kesin PnL doğrulanana kadar yeni girişler kilitli" kapısını **tetiklemez** (ayrı defter).

---

## 10. Marjin tavanı

```python
class MarginGuard:
    def try_reserve(self, account_key: str, grid_id: str, margin_usdt: float, cap_usdt: float, max_positions: int) -> bool:
        """Tek işlemde: Σ(HELD+OPEN) + margin ≤ cap  VE  sayı < max_positions  → farmer_margin_hold yaz. Aksi halde False."""
    def release(self, grid_id: str) -> None: ...          # borsada FLAT doğrulanınca
```
- 100 USDT sert tavan; ayrı bağımsız kontrol, **aynı işlemde** deneme kaydıyla (yarış yok). 25→4, 50→2, 75→1, 100→1 pozisyon. **150 → 0 (canlıda seçilemez).**
- Başlatma anında ve her girişte yeniden doğrulanır; borsadaki gerçek izole marjin ile defter marjini uyuşmazsa `ENTRY_PAUSED_SAFETY`.
- Hesap genel risk katmanları (`account_reservations`, `account_exposure`, kullanıcı risk ayarı) ek olarak geçerlidir; Farmer onları atlamaz (Faz C'de entegrasyon kontrolü — §14-5).

---

## 11. ARM davranışı

```
ENTRY GATE (rezervasyon anında YENİDEN okunur):
  ARM geçerli  → yeni giriş serbest
  ARM doldu    → yeni giriş DURUR (ENTRY_PAUSED_ARM)
                 açık pozisyonlara DOKUNULMAZ; TP/STOP sürer; acil kapanış tetiklenmez
  ARM yeniden geçerli → otomatik RUNNING
```
Fark: `grid_live._pause_for_arm` motoru `PAUSED` yapıp bekleyen girişleri iptal eder; Farmer girişleri anlık MARKET olduğu için bekleyen giriş yoktur ve **motorlar PAUSED yapılmaz** (korumalar ve hedef zaten motor turuyla yönetilir). Yalnız orkestratör girişi durdurur. Güvenlik tetikleri (akış kopması, bayat veri) de **yalnız yeni girişi** durdurur.

---

## 12. Sahte borsa test mimarisi

Katmanlar (ağ yok, gerçek anahtar yok):
1. `FakeExchange` (genişletilmiş): TAKE_PROFIT_MARKET, `tp_slippage`, `income` (FUNDING_FEE) satırları, `userTrades/allOrders/allAlgoOrders` taklidi (prototip 3'teki `FakeClient`'ın genelleşmişi).
2. `FakeKlineFeed` + `FakeClock`: kapanmış mum akışı, bayat/eksik/abone-değil senaryoları.
3. `FakeArm`: geçerli/doldu/yenilendi.
4. Orkestratör testleri gerçek `GridStore` (bellek içi) üzerinde.

Zorunlu test matrisi (biri kırılırsa ilerleme yok):

| Alan | Senaryo |
|---|---|
| Motor | giriş→stop→hedef doğru sıra; hedef dolumu; stop dolumu; fiyat hedefi zaten geçmiş (piyasa kapanış); yanıt kaybı (giriş/stop/hedef); yeniden başlatma kurtarması; acil durdurma doğrulaması; çift emir yok |
| Kısmi | giriş kısmi dolumunda stop+hedef = gerçek miktar, hiçbir an korumasız yok |
| Aynı mum | 64 eşzamanlı deneme → 1 emir; geçici ret sonrası aynı mumda yeniden emir YOK; yeni mumda serbest |
| Marjin | 25/50/75/100 → 4/2/1/1; 150 → 0; yarış (iki aday aynı anda) tavanı aşamaz; `max_positions=5` ama tavan küçük |
| ARM | doldu→giriş durur, pozisyon/TP/STOP aynen; emergency tetiklenmez; yenilendi→giriş sürer |
| DEAD | tek mum yetmez; aynı mum iki kez sayılmaz; kârda → reduce-only kapanış; zararda → kapanış YOK |
| PnL | hedef≠doğrulanmış gösterimi; funding işareti/pencere; kimlik eksik→UNVERIFIED; slot bloklanmaz |
| Akış paylaşımı | Farmer + Original V2 birleşimi; Farmer kapalıyken Original V2 davranışı birebir; 100 sınırı; abone olmayan sembol → giriş yok |
| Yük | 5 motor × N tur: REST sayısı ve commit sayısı tavanı (regresyon eşiği) |
| **Regresyon** | Mevcut Grid/DCA/nötr/filtre/testnet testleri (yamalı kopyada 114/114) + `test_grid_live`, `test_dca_live_runner` (fastapi gerektirir; bu ortamda koşulamadı → CI'da) |

### Backtest planı (karar 3 ve 6)
Mevcut `backtest_execution.py` / `backtest_data.py` / `backtest_baseline.py` altyapısı üzerine **aynı veri + aynı yürütme modeli** ile A/B varyantı ve ızgara: marjin {25,50,75,100,150} × kaldıraç 3 × hedef {0,5/1/2} × maks. zarar {1/1,5/2} × min skor {90/92/94/96} × dead {70/75/80/KAPALI} (= 5·3·3·4·4 = **720 kombinasyon × 2 varyant**). Karar kriterleri kararda listelenen 13 metrik; seçim "en yüksek win rate" değil, **yeterli işlem örneğiyle risk-ayarlı net kârlılık**. Hesap hedefi: aynı kombinasyon için gerçek TAKE_PROFIT_MARKET kayması modellenir (sabit + ATR oranlı iki senaryo).

Ön ulaşılabilirlik tablosu (3x, taker 0,05%/yön + 0,05 USDT tampon; `Feasibility` formülüyle):

| Marjin | Notional | Maliyet | Hedef % (0,5 / 1 / 2) | Stop % (zarar 1 / 1,5 / 2) | 100 cap'te poz. |
|---|---|---|---|---|---|
| 25 | 75 | 0,125 | 0,83 / 1,50 / 2,83 | 1,17 / 1,83 / 2,50 | 4 |
| 50 | 150 | 0,200 | 0,47 / **0,80** / 1,47 | 0,53 / **0,87** / 1,20 | 2 |
| 75 | 225 | 0,275 | 0,34 / 0,57 / 1,01 | 0,32 / 0,54 / 0,77 | 1 |
| 100 | 300 | 0,350 | 0,28 / 0,45 / 0,78 | 0,22 / 0,38 / 0,55 | 1 |
| 150 | 450 | 0,500 | 0,22 / 0,33 / 0,56 | **0,11** / 0,22 / 0,33 | **0** |

Yorum: referans (50 × 3x × +1 × −1,5) için yaklaşık **%0,80** hedef / **%0,87** stop mesafesi gerekiyor. Büyük marjinde stop mesafesi (örn. 150 ve zarar 1,0 → %0,11) gürültü seviyesine iner; ulaşılabilirlik kapısı bu uçları ATR'ye göre eleyecek.

---

## 13. Yük ölçümü (prototip 4) ve doğrulanmamış varsayımlar

Ölçüm (5 pozisyon açık, korumalı, bekleme durumunda; her port çağrısı canlı adaptördeki gerçek REST sayısına çevrildi):

| | REST/tur | REST/dk (5 sn tur) | CPU/tur | DB commit/tur |
|---|---|---|---|---|
| Mevcut tur davranışı | 30,0 | 360 | 1,55–1,95 ms | 20 |
| + tur başına toplu okuma | 11,0 | 132 | 1,47–2,16 ms | 20 |

Ek iyileştirmeler (Faz C): (i) kararlı durumda yalnız değişen satıra yazma (`_apply`'da değişmediyse commit yok — 20 commit/tur gereksiz); (ii) kullanıcı veri akışı olayı `protect_now`'u tetikler, REST uzlaştırması 5 sn yerine 15–30 sn'ye çıkarılır (≈ 22–44 REST/dk); (iii) kline verisi REST'ten değil ortak akıştan.

**DOĞRULANMAMIŞ varsayımlar (Testnet/canlıda doğrulanmadan büyük implementation yok):**
1. `TAKE_PROFIT_MARKET`'in `/fapi/v1/algoOrder` ucunda `algoType=CONDITIONAL`, `quantity` + `reduceOnly` ile kabul edildiği ve alan adları. (Mevcut Grid README'sinde stop için bile "henüz gerçek borsada denenmedi" notu var.)
2. `workingType=MARK_PRICE` ile hedef tetiğinin beklenen davranışı; `priceProtect` etkisi.
3. Tetiklenen hedefin `allAlgoOrders`'ta `actualOrderId` ile gerçek dolum emrine bağlanması (prototip 3 bunu **sahte** veriyle gösterdi).
4. `-2021` benzeri "hemen tetiklenir" kodunun TAKE_PROFIT_MARKET için de aynı olması.
5. Binance REST **ağırlıkları** (bu ortamda ölçülemedi; tablodaki "varsayımsal ağırlık" yalnızca yaklaşıktır, REST **sayıları** ölçüldü).
6. Bu ortamda `fastapi`/`httpx`/`websockets` yok: `test_grid_live`, `test_dca_live_runner`, `test_grid_arm_expiry`, `test_grid_recovery` koşulamadı; `v25_execution` prototipte eksik paketler taklit edilerek içe aktarıldı (yalnız ölçüm düzeneği). Bunlar CI'da çalıştırılmalı.

---

## 14. Sahibinden teyit gereken noktalar

1. **Giriş onayı kuralları (Faz A):** `ConfirmationGate` yalnız sözleşme olarak tanımlandı; "Farmer entry confirmation" içeriği Faz A raporunda; bende yok. Lütfen raporu ekle ya da kuralları yaz.
2. **150 USDT marjin noktası** canlı toplam tavanla (100) çelişir (0 pozisyon). Yalnız backtest/araştırma noktası olarak mı kalsın? (Önerim: evet; canlıda seçilemez.)
3. **Momentum onayı için asgari mum doğrulaması** gerekirse sınırları (hangi alan eksikse) — mevcut alanlar yeterli görünüyor, istisna çıkarsa tek tek onay isterim.
4. **Slot, borsada flat olunca mı boşalsın (önerim), PnL doğrulanınca mı?** Önerim: flat; PnL eşzamansız; ardışık N doğrulanamayan sonuçta giriş duraklar.
5. **Hesap geneli risk katmanları** (`account_reservations`, `account_exposure`, günlük zarar defteri): Farmer bunlara tabi olacak mı, yoksa kendi 100 USDT tavanı yeterli mi? (Önerim: tabi; atlamak yeni risk.)
6. **Ortak akış talebi:** Farmer + Original V2 evreni ≤100. Farmer evreni Original V2'nin günlük evreninin alt kümesi mi olsun, yoksa ayrı liste mi?

---

## 15. Sonraki adım (onayına bağlı)

Faz C önerisi: önce Testnet'te §13 madde 1–4; ardından §2'deki 6 küçük değişiklik + `FarmerEngine` + testler; backtest ızgarası paralel. Her adımda mevcut Grid/DCA testleri yeşil kalacak.
