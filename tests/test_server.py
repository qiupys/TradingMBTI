# -*- coding: utf-8 -*-
"""server.py 单元测试：直接调用 API 处理函数（内存级，不启端口）。"""
import importlib
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
server = importlib.import_module("server")


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    """独立临时数据库 + 清空会话，测试互不污染。"""
    monkeypatch.setattr(server, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(server, "_sessions", {})
    server.init_db()
    return server


def _user(srv, name="testuser", pw="secret123"):
    d = srv.api_register({"username": name, "password": pw})
    assert d["ok"], d
    return d


# ---------------- 注册 ----------------
def test_register_ok(srv):
    d = _user(srv, "alice")
    assert d["username"] == "alice"
    assert len(d["token"]) == 32


@pytest.mark.parametrize("name", ["a", "x" * 21, "", "  "])
def test_register_bad_username(srv, name):
    assert not srv.api_register({"username": name, "password": "secret123"})["ok"]


@pytest.mark.parametrize("pw", ["", "12345", None])
def test_register_bad_password(srv, pw):
    assert not srv.api_register({"username": "bob", "password": pw})["ok"]


def test_register_duplicate(srv):
    _user(srv, "dup")
    d = srv.api_register({"username": "dup", "password": "secret123"})
    assert not d["ok"] and "已被注册" in d["error"]


# ---------------- 登录 ----------------
def test_login_ok_and_wrong_pw(srv):
    _user(srv, "carol", "right123")
    ok = srv.api_login({"username": "carol", "password": "right123"})
    assert ok["ok"] and ok["token"]
    bad = srv.api_login({"username": "carol", "password": "wrong!!"})
    assert not bad["ok"] and "错误" in bad["error"]
    ghost = srv.api_login({"username": "nobody", "password": "whatever"})
    assert not ghost["ok"]


def test_password_hashed_not_stored(srv):
    import sqlite3
    _user(srv, "dave", "plainpw")
    with sqlite3.connect(srv.DB_PATH) as conn:
        row = conn.execute("SELECT pw_hash, salt FROM users WHERE username='dave'").fetchone()
    assert row and "plainpw" not in row[0] and len(row[0]) == 64   # sha256 hex


# ---------------- 会话 ----------------
def test_expired_token(srv):
    for fn in (srv.api_save_quiz, srv.api_save_game, srv.api_save_ai, srv.api_history):
        d = fn({"token": "deadbeef"})
        assert not d["ok"] and "过期" in d["error"]


def test_quiz_roundtrip(srv):
    u = _user(srv, "erin")
    answers = [0.5, -1, 0.1] + [None] * 17
    assert srv.api_save_quiz({"token": u["token"], "answers": answers})["ok"]
    h = srv.api_history({"token": u["token"]})
    assert h["ok"] and h["quiz"] == answers
    assert h["username"] == "erin" and h["games"] == []


# ---------------- 存档 / 补写 AI / 历史 ----------------
SUMMARY = dict(score=77, tier="老手风范", final_assets=1234567.0, total_ret_pct=23.46,
               bench_ret_pct=10.0, tax_yuan=-50000, mdd_pct=12.5, trades=18,
               mbti_self="CQNH", mbti_measured="BQNT", mbti_name="打板冲锋号")


def test_save_game_and_history(srv):
    u = _user(srv, "frank")
    detail = {"rounds": [{"r": 1, "name": "长江电力", "ret_pct": 3.2, "bench_pct": 1.1, "ops": 2}],
              "flags": {"chase": 1, "panic": 0, "cutLoss": 0, "soldFly": 2}, "summary": "逐段复盘文本"}
    d = srv.api_save_game({"token": u["token"], "summary": SUMMARY, "detail": detail, "quiz": [0.5, -1]})
    assert d["ok"] and d["id"] == 1
    h = srv.api_history({"token": u["token"]})
    assert len(h["games"]) == 1
    g = h["games"][0]
    assert g["score"] == 77 and g["tier"] == "老手风范"
    assert g["detail"]["rounds"][0]["name"] == "长江电力"
    assert g["detail"]["flags"]["soldFly"] == 2


def test_save_ai_merges_into_latest_game(srv):
    u = _user(srv, "grace")
    srv.api_save_game({"token": u["token"], "summary": SUMMARY, "detail": {"rounds": []}})
    ai = {"code": "BQNT", "one_liner": "满仓梭哈王"}
    assert srv.api_save_ai({"token": u["token"], "ai": ai})["ok"]
    g = srv.api_history({"token": u["token"]})["games"][0]
    assert g["detail"]["ai"]["one_liner"] == "满仓梭哈王"


def test_save_ai_without_game(srv):
    u = _user(srv, "heidi")
    d = srv.api_save_ai({"token": u["token"], "ai": {"code": "X"}})
    assert not d["ok"]


def test_history_limit_20_and_order(srv):
    u = _user(srv, "ivan")
    for i in range(25):
        s = dict(SUMMARY, score=i)
        srv.api_save_game({"token": u["token"], "summary": s, "detail": {}})
    games = srv.api_history({"token": u["token"]})["games"]
    assert len(games) == 20
    assert games[0]["score"] == 24 and games[-1]["score"] == 5   # 最新在前


# ---------------- 纯函数 ----------------
def test_extract_json():
    assert server.extract_json('前言 {"a": 1} 后记') == {"a": 1}
    assert server.extract_json('no json here') is None


def test_fallback_mbti_paths():
    base = {"result": {"trades": 0}, "flags": {}}
    assert server.fallback_mbti(base)["mbti_code"] == "WAIT"
    assert server.fallback_mbti({**base, "flags": {"chase": 2}})["mbti_code"] == "FOMO"
    assert server.fallback_mbti({**base, "result": {"trades": 5}, "flags": {"panic": 1}})["mbti_code"] == "PANI"
    assert server.fallback_mbti({**base, "result": {"trades": 5}, "flags": {"sold_fly": 1}})["mbti_code"] == "SHIP"
    calm = server.fallback_mbti({**base, "result": {"trades": 5}, "flags": {}})
    assert calm["mbti_code"] == "CALM"
    for out in (calm,):
        assert set(out) >= {"mbti_code", "mbti_name", "one_liner", "strengths", "weaknesses", "advice", "segment_note"}


def test_prompt_contains_payload():
    p = server.PROMPT_TMPL.replace("{payload}", '{"k": 1}')
    assert '{"k": 1}' in p and "严格 JSON" in p
