/* ============================================================================
 * K线时光机 · 纯游戏引擎（无 DOM 依赖）
 * 浏览器挂到 window.Engine；Node 测试通过 module.exports 引用同一份实现。
 * 状态对象 S 约定：{ scen, len, i, startCash, cash, shares, cost, trades,
 *                    done, newsIdx }   （scen.k 字段：dates,o,h,l,c,pct,v；scen.lb：dates,o,h,l,c）
 * ========================================================================== */
(function (root) {
  "use strict";
  const E = {};

  /* ---- 行情与账户 ---- */
  E.price = S => S.scen.k.c[S.i];
  E.total = S => S.cash + S.shares * E.price(S);
  E.posLevel = S => {
    if (S.shares === 0) return 0;
    // 剩余现金买不起一手(100股)就视为满仓——高价股加仓后的常见状态
    return S.cash < E.price(S) * 100 ? 2 : 1;   // 0=空仓 1=半仓 2=满仓
  };

  /* ---- 交易记录公共字段（含 nearNews：当日大涨或处于资讯后3日内） ---- */
  function baseTrade(S) {
    const pct = S.scen.k.pct[S.i] || 0;
    return { i: S.i, date: S.scen.k.dates[S.i], pct,
             nearNews: pct >= 3.5 || S.newsIdx.some(ni => S.i >= ni && S.i <= ni + 2) };
  }

  /* 追高判定：当日涨幅≥4%，或买价 ≥ 近20日最高价(含入场前lookback)的97% */
  function chaseFlag(S, t) {
    const k = S.scen.k;
    const all = S.scen.lb.c.concat(k.c.slice(0, S.i + 1));
    const hi20 = Math.max(...all.slice(-20));
    t.chase = k.pct[S.i] >= 4 || t.price >= hi20 * 0.97;
  }

  /* ---- 买入：空仓可买半仓/满仓，半仓只能加至满仓；返回 trade 或 {error} ---- */
  E.buy = (S, fraction) => {
    if (S.done) return null;
    const p = E.price(S);
    const level = E.posLevel(S);
    if (level === 2) return { error: "已满仓——先卖出一半，才能再买回满仓" };
    if (level === 1) fraction = 1;
    // 空仓时全部资产就是现金，按当前现金的 fraction 计算（用期初资金会买超、现金变负）
    const budget = S.cash * fraction;
    const lots = Math.floor(budget / p / 100) * 100;
    if (lots <= 0) return { error: "资金不足，无法成交" };
    S.cost = (S.cost * S.shares + lots * p) / (S.shares + lots);
    S.shares += lots;
    S.cash -= lots * p;
    const allIn = level === 0 && fraction === 1;
    const t = baseTrade(S);
    t.action = level === 0 ? (allIn ? "满仓买入" : "买入半仓") : "加仓至满仓";
    t.price = p; t.shares = lots; t.allIn = allIn;
    chaseFlag(S, t);
    return t;
  };

  /* ---- 卖出半仓：仅满仓可用；至少保留一手规则通过 max(100,...) 保证 ---- */
  E.sellHalf = S => {
    if (S.done) return null;
    if (E.posLevel(S) !== 2) return { error: "半仓状态下没有再卖一半的操作，只能清仓" };
    const p = E.price(S);
    const lots = Math.max(100, Math.floor(S.shares / 2 / 100) * 100);
    const t = baseTrade(S);
    t.action = "卖出半仓"; t.price = p; t.shares = lots;
    t.pnl = (p - S.cost) * lots; t.cost = S.cost;
    S.cash += lots * p; S.shares -= lots;
    return t;
  };

  /* ---- 清仓 ---- */
  E.clear = S => {
    if (S.done || S.shares === 0) return null;
    const p = E.price(S), sh = S.shares;
    const t = baseTrade(S);
    t.action = "清仓"; t.price = p; t.shares = sh;
    t.pnl = (p - S.cost) * sh; t.cost = S.cost;
    S.cash += sh * p; S.shares = 0;
    return t;
  };

  /* ---- 强制平仓（窗口结束） ---- */
  E.forceClose = S => {
    if (S.shares === 0) return null;
    const p = E.price(S), sh = S.shares;
    const t = baseTrade(S);
    t.action = "强制清仓（本局窗口结束）"; t.price = p; t.shares = sh;
    t.pnl = (p - S.cost) * sh; t.cost = S.cost; t.forced = true;
    S.cash += sh * p; S.shares = 0;
    return t;
  };

  /* ---- 局结算：最终资产、本局收益、满仓躺平基准（整手取整，零钱留在账户） ---- */
  E.roundResult = (S, scen) => {
    const k = scen.k;
    const finalV = S.cash;
    const ret = finalV / S.startCash - 1;
    const bShares = Math.floor(S.startCash / k.c[0] / 100) * 100;
    const benchV = bShares * k.c[S.len - 1] + (S.startCash - bShares * k.c[0]);
    return { finalV, ret, benchV, benchRet: benchV / S.startCash - 1 };
  };

  /* ---- 行为标记（仅由可计算的交易数据得出） ---- */
  E.roundFlags = trades => {
    const F = [];
    const userT = trades.filter(t => !t.forced);
    const buys = userT.filter(t => t.action.includes("买入") || t.action.includes("加仓"));
    const sells = userT.filter(t => t.action.includes("卖出") || t.action.includes("清仓"));
    const chase = buys.filter(t => t.chase);
    const panic = sells.filter(t => t.pct <= -3.5);
    const cutLoss = sells.filter(t => t.cost && t.price < t.cost * 0.95);
    if (userT.length === 0) F.push(["good", "本局观望"]);
    if (chase.length) F.push(["bad", `追高买入 ×${chase.length}`]);
    if (panic.length) F.push(["bad", `恐慌日卖出 ×${panic.length}`]);
    if (cutLoss.length) F.push(["bad", `割肉离场 ×${cutLoss.length}`]);
    if (buys.some(t => t.allIn)) F.push(["bad", "一把梭"]);
    if (!F.length) F.push(["good", "无典型偏差"]);
    return F;
  };

  /* ---- 综合评分：62 基础分 ± 收益差(±20) − 行为偏差(封顶-12) ± 回撤/观望修正，限 [20,95] ----
     userRet/benchRet 传入百分数（如 12.3），mdd 传入小数（如 0.18） */
  E.score = (userRet, benchRet, mdd, flags, skippedCount, noTrade) => {
    let score = 62;
    score += Math.max(-20, Math.min(20, (userRet - benchRet) * 0.4));
    const flagPenalty = flags.chase * 2.5 + flags.panic * 2.5 + flags.soldFly * 2
                      + flags.cutLoss * 1.5 + (flags.allIn ? 2 : 0) + (flags.disposal ? 2 : 0);
    score -= Math.min(12, flagPenalty);
    if (mdd > 0.15) score -= 4;
    if (mdd > 0.30) score -= 4;
    if (skippedCount >= 4) score += 2;
    if (noTrade) score = Math.max(48, Math.min(80, score + 8));
    score = Math.max(20, Math.min(95, Math.round(score)));
    return Number.isFinite(score) ? score : 50;
  };

  /* ---- 每局抽 10 只：≥3 只创业板/科创板、≥2 只低波动，剩余从全池随机补齐 ---- */
  E.pickDeck = (pool, rounds) => {
    const sh = a => { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
    const p = sh([...pool]);
    const deck = [];
    const take = f => { for (const s of p) if (!deck.includes(s) && f(s)) { deck.push(s); return true; } return false; };
    let gemN = 0, lowN = 0;
    while (gemN < 3 && take(s => s.code.startsWith("sz.3") || s.code.startsWith("sh.68"))) gemN++;
    while (lowN < 2 && take(s => s.vol === "低波动" && !deck.includes(s))) lowN++;
    while (deck.length < rounds && take(() => true)) {}
    return sh(deck);
  };

  root.Engine = E;
  if (typeof module !== "undefined" && module.exports) module.exports = E;
})(typeof window !== "undefined" ? window : globalThis);
