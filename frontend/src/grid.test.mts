// Çalıştırma: node frontend/src/grid.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert'
import * as g from './gridMath.ts'
import * as p from './gridPortfolio.ts'
import * as a from './gridAlerts.ts'

const lim:any={maxPositions:2,maxLossPerTrade:30,maxTotalExposure:350,currentExposure:0,openPositions:0,dailyLossBudgetLeft:40,openPositionRisk:0};
const F:any={tickSize:0.1,stepSize:0.01,minNotional:5};
const base:any={side:'LONG',price:100,lower:90,upper:98,count:5,totalMargin:50,leverage:2,stop:88,filters:F};
// ---- portfolio risk helper
const pos=(o?:any)=>({symbol:'ETHUSDT',quantity:1,mark_price:100,direction:'LONG',...o});
const stop=(o?:any)=>({symbol:'ETHUSDT',side:'SELL',type:'STOP_MARKET',trigger_price:95,...o});
let r:any=p.openPositionsRiskToStop([pos()],[stop()]);assert.strictEqual(r.total,5);
r=p.openPositionsRiskToStop([pos({direction:'SHORT',quantity:-2})],[stop({side:'BUY',trigger_price:103})]);assert.strictEqual(r.total,6);
assert.strictEqual(p.openPositionsRiskToStop([pos()],[]).total,null,'stop yok → bilinmiyor');
assert.strictEqual(p.openPositionsRiskToStop([pos()],[stop({type:'TAKE_PROFIT_MARKET'})]).total,null,'TP stop sayılmaz');
assert.strictEqual(p.openPositionsRiskToStop([pos()],[stop({side:'BUY'})]).total,null,'yanlış yön');
assert.strictEqual(p.openPositionsRiskToStop([pos({mark_price:undefined})],[stop()]).total,null);
assert.strictEqual(p.openPositionsRiskToStop([pos(),pos({symbol:'SOLUSDT'})],[stop()]).total,null,'biri bilinmiyorsa toplam bilinmez');
assert.strictEqual(p.openPositionsRiskToStop([pos(),pos({symbol:'SOLUSDT'})],[stop(),stop({symbol:'SOLUSDT',trigger_price:90})]).total,15);
assert.strictEqual(p.openPositionsRiskToStop([],[]).total,0);
assert.strictEqual(p.openPositionsRiskToStop([pos()],[stop({trigger_price:98}),stop({trigger_price:94})]).total,6,'en uzak stop');
assert.strictEqual(p.openPositionsRiskToStop([pos({quantity:0})],[]).total,0,'boş satır');
// ---- portfolio check in grid
r=g.computeGrid(base,{...lim,openPositionRisk:0});const w=r.worstLoss;assert(r.checks.find((c:any)=>c.id==='portfolio_risk').ok);
r=g.computeGrid(base,{...lim,dailyLossBudgetLeft:w+3,openPositionRisk:5});assert(!r.checks.find((c:any)=>c.id==='portfolio_risk').ok,'5+worst > hak');
assert(r.checks.find((c:any)=>c.id==='daily_loss').ok,'tek başına grid hâlâ sığıyor; toplam sığmıyor');
r=g.computeGrid(base,{...lim,openPositionRisk:null});assert(!r.checks.find((c:any)=>c.id==='portfolio_risk').ok&&!r.allOk);
r=g.computeGrid(base,{...lim,dailyLossBudgetLeft:null,openPositionRisk:1});assert(!r.checks.find((c:any)=>c.id==='portfolio_risk').ok);
r=g.computeGrid(base,lim);assert(r.checks.find((c:any)=>c.id==='exchange_filters').ok);
// ---- alerts
let al:any=a.buildGridAlerts({result:g.computeGrid({...base,filters:undefined},lim),filters:'error',filtersError:'503',openRisk:0,hasOpenPositions:false});
assert(al[0].level==='uyari'&&/Bağlantı/.test(al[0].text));assert(!al.some((x:any)=>/Borsa fiyat\/miktar/.test(x.text)),'çift uyarı yok');
al=a.buildGridAlerts({result:g.computeGrid(base,{...lim,dailyLossBudgetLeft:1}),filters:'ok',openRisk:0,hasOpenPositions:false});
assert(al.some((x:any)=>x.level==='kritik'&&/Risk aşımı/.test(x.text)),'risk aşımı kritik');
al=a.buildGridAlerts({result:g.computeGrid(base,{...lim,openPositionRisk:null}),filters:'ok',openRisk:null,hasOpenPositions:true});
assert(al.some((x:any)=>/stopu okunamadı/.test(x.text)));
al=a.buildGridAlerts({result:g.computeGrid(base,lim),filters:'ok',openRisk:0,hasOpenPositions:false});assert.deepStrictEqual(al,[],'sorun yoksa uyarı yok');
assert.strictEqual(a.engineStatusAlert('INTERVENTION_REQUIRED').level,'kritik');
assert(/stoplar yerinde/i.test(a.engineStatusAlert('INTERVENTION_REQUIRED','x').text));
assert.strictEqual(a.engineStatusAlert('BLOCKED','uyuşmuyor').level,'uyari');
assert.strictEqual(a.engineStatusAlert('EMERGENCY').level,'kritik');
assert.strictEqual(a.engineStatusAlert('RUNNING'),null);
assert(a.engineStatusAlert('EMERGENCY_CLOSED').level==='bilgi');
// ---- önceki testler (id, çift filtre, likidasyon)
const id=g.gridClientOrderId;assert.strictEqual(id('abc-123-XYZ','LONG',3,'E'),'kg-abc123XYZ-L03E0');
r=g.computeGrid({...base,lower:97.95,upper:98,filters:{...F,tickSize:1}},lim);assert(!r.checks.find((c:any)=>c.id==='exchange_filters').ok);
assert(!g.computeGrid({...base,filters:undefined},lim).checks.find((c:any)=>c.id==='exchange_filters').ok);
r=g.computeGrid({...base,leverage:10,stop:80},lim);assert(!r.checks.find((c:any)=>c.id==='liquidation').ok);
assert(!g.computeGrid({...base,count:6},lim).valid,'6 kademe geçersiz');
console.log('OK');
// ---- çok küçük emir: ideal değerler gösterilir (sıfır değil), kontrol başarısız
{
  const small: any = g.computeGrid({...base, filters: {tickSize: 0.1, stepSize: 0.001, minNotional: 100}, price: 87000, lower: 82000, upper: 86000, stop: 80000, totalMargin: 50}, lim)
  assert(!small.checks.find((c: any) => c.id === 'exchange_filters').ok)
  assert(small.worstLoss > 0 && small.totalNotional > 0, 'özet sıfır görünmemeli')
  const okc: any = g.computeGrid({...base, totalMargin: 5000, filters: F}, {...lim, maxLossPerTrade: 1e9, maxTotalExposure: 1e9, dailyLossBudgetLeft: 1e9})
  assert(okc.checks.find((c: any) => c.id === 'exchange_filters').ok)
  for (const l of okc.levels) { assert(Math.abs(l.qty / 0.01 - Math.round(l.qty / 0.01)) < 1e-6, 'adım'); assert(Math.abs(l.entry / 0.1 - Math.round(l.entry / 0.1)) < 1e-6) }
}
console.log('OK-2')
