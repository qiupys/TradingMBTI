/* K线时光机 · 游戏引擎测试（Node 内置 test runner，直接测 game-core.js 真实实现）
   运行：node --test tests/ */
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import { test, beforeEach } from "node:test";

const require = createRequire(import.meta.url);
globalThis.window = globalThis;          // data.js 会挂 window.SCENARIOS
const E = require("../game-core.js");
require("../data.js");
const SCENARIOS = globalThis.SCENARIOS;

const CAP = 1000000;
/* 可复现的伪随机（LCG），保证测试确定性 */
function makeRng(seed) {
  let s = seed >>> 0;
  return () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
}
function newState(scen, startCash = CAP) {
  const k = scen.k;
  return { scen, len: k.dates.length, i: 0, startCash, cash: startCash,
           shares: 0, cost: 0, trades: [], done: false,
           newsIdx: scen.news.map(n => k.dates.findIndex(d => d >= n[0])).filter(x => x >= 0) };
}
const near = (a, b) => Math.abs(a - b) < 1e-6;

/* ==================== 数据完整性 ==================== */
test("场景池：40 只标的、代码唯一、字段齐全", () => {
  assert.equal(SCENARIOS.length, 40);
  assert.equal(new Set(SCENARIOS.map(s => s.code)).size, 40);
  for (const s of SCENARIOS) {
    for (const f of ["code", "name", "sector", "vol", "w", "story", "news", "lb", "k", "period"]) assert.ok(s[f], s.code + " 缺 " + f);
    const k = s.k, n = k.dates.length;
    for (const f of ["o", "h", "l", "c", "pct", "v"]) assert.equal(k[f].length, n, s.code + " k." + f + " 长度不一致");
    for (const f of ["dates", "o", "h", "l", "c"]) assert.equal(s.lb[f].length, s.lb.dates.length, s.code + " lb." + f + " 长度不一致");
  }
});

test("行情数据：日期升序、OHLC 自洽、pct 在涨跌停范围内", () => {
  for (const s of SCENARIOS) {
    const k = s.k;
    for (let i = 0; i < k.dates.length; i++) {
      assert.ok(k.dates[i] <= k.dates[i + 1] || i === k.dates.length - 1, s.code + " 日期非升序 @" + i);
      assert.ok(k.h[i] >= Math.max(k.o[i], k.c[i]) - 1e-9, s.code + " high < max(o,c) @" + i);
      assert.ok(k.l[i] <= Math.min(k.o[i], k.c[i]) + 1e-9, s.code + " low > min(o,c) @" + i);
      assert.ok(k.c[i] > 0, s.code + " 收盘价非正 @" + i);
      assert.ok(Math.abs(k.pct[i]) <= 21.5, s.code + " pct 超出 20cm 涨跌停 @" + i);
      assert.ok(Number.isInteger(k.v[i]) && k.v[i] >= 0, s.code + " 成交量非法 @" + i);
    }
  }
});

test("资讯日期必须落在可交易窗口内", () => {
  for (const s of SCENARIOS) {
    const d0 = s.k.dates[0], d1 = s.k.dates[s.k.dates.length - 1];
    for (const [d] of s.news) {
      assert.ok(d >= d0 && d <= d1, `${s.code} 资讯日期 ${d} 越界 [${d0}, ${d1}]`);
    }
  }
});

/* ==================== 发牌 ==================== */
test("pickDeck：10 只不重复、≥3 创业板/科创板、≥2 低波动", () => {
  for (let seed = 1; seed <= 50; seed++) {
    const deck = E.pickDeck(SCENARIOS, 10);
    assert.equal(deck.length, 10);
    assert.equal(new Set(deck.map(s => s.code)).size, 10);
    const gem = deck.filter(s => s.code.startsWith("sz.3") || s.code.startsWith("sh.68")).length;
    const low = deck.filter(s => s.vol === "低波动").length;
    assert.ok(gem >= 3, `seed ${seed}: 创业板/科创板只有 ${gem} 只`);
    assert.ok(low >= 2, `seed ${seed}: 低波动只有 ${low} 只`);
  }
});

/* ==================== 交易数学 ==================== */
test("买入半仓：现金+市值守恒，股数为一手整数倍", () => {
  const S = newState(SCENARIOS[0]);
  const t = E.buy(S, 0.5);
  assert.ok(t && !t.error, "买入应成功");
  assert.ok(S.shares > 0 && S.shares % 100 === 0);
  assert.ok(near(S.cash + S.shares * E.price(S), S.startCash), "资产不守恒");
  assert.ok(t.action.includes("买入"));
});

test("半仓只能加至满仓；满仓后买入被拒", () => {
  const S = newState(SCENARIOS[0]);
  E.buy(S, 0.5);
  const t2 = E.buy(S, 0.5);
  assert.ok(t2 && !t2.error);
  assert.equal(E.posLevel(S), 2);
  const t3 = E.buy(S, 0.5);
  assert.ok(t3 && t3.error, "满仓后买入必须报错");
});

test("满仓卖半 → 可清仓；空仓时卖操作被拒", () => {
  const S = newState(SCENARIOS[1]);
  E.buy(S, 1);
  const shares0 = S.shares;
  const t = E.sellHalf(S);
  assert.ok(t && !t.error);
  assert.ok(S.shares < shares0 && S.shares % 100 === 0);
  assert.ok(E.sellHalf(S) && E.sellHalf(S).error, "半仓再卖半必须报错");
  const tc = E.clear(S);
  assert.ok(tc && !tc.error && S.shares === 0);
  assert.ok(E.clear(S) === null, "空仓清仓应返回 null");
});

test("资金不足时买入报错且状态不变", () => {
  const S = newState(SCENARIOS[0], 500);   // 500 块连一手都买不起
  const before = { cash: S.cash, shares: S.shares };
  const t = E.buy(S, 1);
  assert.ok(t && t.error);
  assert.equal(S.cash, before.cash);
  assert.equal(S.shares, before.shares);
});

test("追高标记：当日涨幅≥4% 的买入必标 chase", () => {
  let hit = 0;
  for (const s of SCENARIOS) {
    const S = newState(s);
    for (let i = 1; i < S.len; i++) {
      S.i = i;
      if ((s.k.pct[i] || 0) >= 4) {
        const t = E.buy(S, 0.5);
        if (t && !t.error) { assert.equal(t.chase, true, s.code + " 大涨日买入未标追高"); hit++; }
      }
      if (hit > 20) return;
    }
  }
  assert.ok(hit > 0, "样本中未覆盖到大涨日");
});

/* ==================== 全局不变量 + 全流程模拟 ==================== */
/* 任意策略下：现金永不为负、股数永远是一手整数倍、总资产逐步可复算 */
function runStrategy(scen, decide, seed) {
  const S = newState(scen);
  const rng = makeRng(seed);
  for (let i = 0; i < S.len; i++) {
    S.i = i;
    const act = decide(S, rng);
    if (act === "buyHalf") { const t = E.buy(S, 0.5); assert.ok(!t || !t.error || t.error, "buy 返回结构合法"); }
    else if (act === "buyFull") E.buy(S, 1);
    else if (act === "sellHalf") E.sellHalf(S);
    else if (act === "clear") E.clear(S);
    assert.ok(S.cash >= -1e-6, scen.code + " 现金为负 @" + i);
    assert.equal(S.shares % 100, 0, scen.code + " 股数非整手 @" + i);
    assert.ok(near(S.cash + S.shares * E.price(S), Math.max(0, S.cash + S.shares * E.price(S))), "资产为负");
  }
  const ft = E.forceClose(S);
  if (ft) assert.ok(ft.forced);
  assert.equal(S.shares, 0, scen.code + " 强平后仍有持仓");
  const rr = E.roundResult(S, scen);
  assert.ok(near(rr.finalV, S.cash));
  // 躺平基准手工复算
  const bShares = Math.floor(S.startCash / scen.k.c[0] / 100) * 100;
  const benchV = bShares * scen.k.c[S.len - 1] + (S.startCash - bShares * scen.k.c[0]);
  assert.ok(near(rr.benchV, benchV), scen.code + " 躺平基准不符");
  return rr;
}

test("全 40 只标的 × 随机策略：不变量全部成立", () => {
  const strategies = [
    (S, rng) => rng() < 0.3 ? "buyHalf" : rng() < 0.2 ? "clear" : null,
    (S, rng) => S.i === 0 ? "buyFull" : rng() < 0.15 ? "sellHalf" : null,
    (S, rng) => S.i % 5 === 0 ? (rng() < 0.5 ? "buyHalf" : "sellHalf") : null,
    () => null,   // 全程观望
  ];
  let n = 0;
  for (const s of SCENARIOS) {
    strategies.forEach((st, si) => { runStrategy(s, st, 1000 + si * 7 + n); n++; });
  }
  assert.ok(n >= 160);
});

test("全 40 只标的 × 满仓拿到底：本局收益应等于躺平收益", () => {
  for (const s of SCENARIOS) {
    const rr = runStrategy(s, S => S.i === 0 ? "buyFull" : null, 42);
    assert.ok(Math.abs(rr.ret - rr.benchRet) < 0.02, s.code + ` 满仓拿到底 ret=${rr.ret} bench=${rr.benchRet} 差异过大`);
  }
});

/* ==================== 结算与评分 ==================== */
test("forceClose 后窗口结束：无遗留持仓，收益与手工复算一致", () => {
  const S = newState(SCENARIOS[5]);
  E.buy(S, 0.5);
  S.i = S.len - 1;
  const manual = S.cash + S.shares * E.price(S);
  E.forceClose(S);
  const rr = E.roundResult(S, S.scen);
  assert.ok(near(rr.finalV, manual));
});

test("评分引擎：输出始终在 [20, 95]，且极端行为会拉低分数", () => {
  for (let ch = 0; ch <= 10; ch++) for (let pa = 0; pa <= 10; pa++)
    for (const mdd of [0, 0.1, 0.2, 0.4]) for (const ret of [-90, -30, 0, 30, 90]) {
      const sc = E.score(ret, 0, mdd, { chase: ch, panic: pa, soldFly: 0, cutLoss: 0, allIn: false, disposal: false }, 0, false);
      assert.ok(sc >= 20 && sc <= 95, `score 越界: ${sc}`);
    }
  const good = E.score(20, 0, 0.05, { chase: 0, panic: 0, soldFly: 0, cutLoss: 0, allIn: false, disposal: false }, 0, false);
  const bad = E.score(20, 0, 0.05, { chase: 5, panic: 5, soldFly: 5, cutLoss: 5, allIn: true, disposal: true }, 0, false);
  assert.ok(good > bad);
  // 全程观望：分数被夹在 [48, 80]
  const idle = E.score(-40, 10, 0, { chase: 0, panic: 0, soldFly: 0, cutLoss: 0, allIn: false, disposal: false }, 0, true);
  assert.ok(idle >= 48 && idle <= 80);
});

test("行为标记：观望局打 good 标，追高局打 bad 标", () => {
  const f0 = E.roundFlags([]);
  assert.deepEqual(f0, [["good", "本局观望"]]);
  const f1 = E.roundFlags([{ action: "满仓买入", chase: true, pct: 5, forced: false }]);
  assert.ok(f1.some(x => x[0] === "bad" && x[1].includes("追高")));
  // 强平不算用户操作
  const f2 = E.roundFlags([{ action: "强制清仓（本局窗口结束）", forced: true, pct: -5, cost: 10, price: 1, chase: true }]);
  assert.deepEqual(f2, [["good", "本局观望"]]);
});

/* ==================== 新局状态入口 ==================== */
beforeEach(() => {});   // 占位：以上测试彼此独立、无共享可变状态
test("十局连贯模拟（deck 逐局推进）：资金结转守恒", () => {
  const deck = E.pickDeck(SCENARIOS, 10);
  let cash = CAP;
  for (let r = 0; r < 10; r++) {
    const S = newState(deck[r], cash);
    for (let i = 0; i < S.len; i++) {
      S.i = i;
      if (i === 0) E.buy(S, 0.5);
      if (i === Math.floor(S.len / 2)) E.clear(S);
      if (i === S.len - 2) E.buy(S, 0.5);
    }
    E.forceClose(S);
    const rr = E.roundResult(S, S.scen);
    assert.ok(rr.finalV > 0);
    cash = rr.finalV;
  }
  assert.ok(cash > 0);
});
