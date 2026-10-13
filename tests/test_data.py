# -*- coding: utf-8 -*-
"""数据完整性测试：kline_raw2.json（原始行情）与 data.js（构建产物）交叉校验。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def raw():
    return json.loads((ROOT / "data" / "kline_raw2.json").read_text())


@pytest.fixture(scope="module")
def scen():
    text = (ROOT / "data.js").read_text()
    assert text.startswith("window.SCENARIOS = ")
    payload = text[len("window.SCENARIOS = "):].rstrip().rstrip(";")
    return json.loads(payload)


# ---------------- 原始数据 ----------------
def test_raw_pool_size(raw):
    assert len(raw) == 40
    assert all(rows for rows in raw.values())


def test_raw_rows_consistent(raw):
    for code, rows in raw.items():
        dates = [r[0] for r in rows]
        assert dates == sorted(dates), f"{code} 日期非升序"
        assert len(dates) == len(set(dates)), f"{code} 日期重复"
        for r in rows:
            d, o, h, l, c, v, pct = r
            o, h, l, c = float(o), float(h), float(l), float(c)
            assert h >= max(o, c) - 1e-6, f"{code} {d} high<max(o,c)"
            assert l <= min(o, c) + 1e-6, f"{code} {d} low>min(o,c)"
            assert c > 0 and float(v or 0) >= 0
            if pct:
                # 上市首日（如中芯国际 +202%）没有涨跌停约束，其余 obey 板块限值
                limit = 250 if d == dates[0] else 21.5
                assert abs(float(pct)) <= limit, f"{code} {d} pct={pct} 超限"


# ---------------- 构建产物 ----------------
def test_scen_count_and_fields(scen):
    assert len(scen) == 40
    vols = set()
    for s in scen:
        assert s["code"] and s["name"] and s["sector"] and s["vol"]
        vols.add(s["vol"])
        assert s["k"]["dates"] and len(s["k"]["c"]) == len(s["k"]["dates"])
        assert set(s["k"]) == {"dates", "o", "h", "l", "c", "pct", "v"}
        assert len(s["lb"]["dates"]) <= 22
    assert vols <= {"低波动", "中低波动", "中波动", "中高波动", "高波动", "超高波动"}


def test_scen_news_in_window(scen):
    for s in scen:
        d0, d1 = s["k"]["dates"][0], s["k"]["dates"][-1]
        for d, text in s["news"]:
            assert d0 <= d <= d1, f"{s['code']} 资讯 {d} 越界 [{d0},{d1}]"
            assert text.strip(), f"{s['code']} 有空资讯"


def test_scen_pct_no_mechanical_gap(scen):
    """除权机械缺口回归测试：隆基 2021-06-23 曾因东财不复权 pctChg=-27.27% 污染价格序列。"""
    for s in scen:
        limit = 21.5 if s["code"].startswith(("sz.3", "sh.68")) else 11.5
        for i, p in enumerate(s["k"]["pct"]):
            assert abs(p) <= limit, f"{s['code']} k.pct[{i}]={p} 超过板块限值（疑似除权缺口）"


def test_scen_price_series_matches_pct_chain(scen):
    """锚定重建自洽性：相邻收盘价比 ≈ 1+pct/100（定点复权口径）。"""
    for s in scen:
        k = s["k"]
        for i in range(1, len(k["c"])):
            implied = (k["c"][i] / k["c"][i - 1] - 1) * 100
            assert abs(implied - k["pct"][i]) < 0.6, f"{s['code']} @{k['dates'][i]} pct链不符: {implied:.2f} vs {k['pct'][i]}"
