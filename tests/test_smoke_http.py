# -*- coding: utf-8 -*-
"""HTTP 集成冒烟测试：真实启动 server.py 子进程，走通 静态页 + 注册/登录/存档/历史/AI兜底 全链路。"""
import json
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    proc = subprocess.Popen(
        [PY, str(ROOT / "server.py")],
        env={"PATH": "/usr/bin:/bin", "PORT": str(port), "HOST": "127.0.0.1",
             "TT_DB": str(ROOT / "tests" / ".smoke.db"), "CODEBUDDY_API_KEY": ""},
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    # 轮询直到服务就绪（最多 5s）
    import time
    for _ in range(50):
        try:
            urllib.request.urlopen(url + "/api/health" if False else url + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("server 未能在 5s 内就绪")
    yield url
    proc.terminate()
    proc.wait(timeout=5)
    (ROOT / "tests" / ".smoke.db").unlink(missing_ok=True)


def _post(url, path, body):
    req = urllib.request.Request(url + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _get(url, path):
    """注意：必须在 with 块内读完响应体——urlopen 的 with 退出即关闭连接，出了块 read() 只会得到 b''。"""
    with urllib.request.urlopen(url + path, timeout=10) as r:
        return r.status, r.read()


# ---------------- 静态资源 ----------------
def test_index_page(base_url):
    status, body = _get(base_url, "/")
    body = body.decode()
    assert status == 200
    assert "K线时光机" in body and "game-core.js" in body


def test_static_assets(base_url):
    for path, marker in [("/data.js", b"SCENARIOS"), ("/game-core.js", b"Engine")]:
        status, body = _get(base_url, path)
        assert status == 200 and marker in body


def test_path_traversal_blocked(base_url):
    try:
        _get(base_url, "/../server.py")
        assert False, "路径穿越未被拦截"
    except Exception:
        pass  # 404/400/连接层异常都算拦截成功


def test_missing_page_404(base_url):
    try:
        _get(base_url, "/nope.js")
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 404


# ---------------- API 全链路 ----------------
def test_full_user_flow(base_url):
    reg = _post(base_url, "/api/register", {"username": "smoke_user", "password": "smoke123"})
    assert reg["ok"] and reg["token"]
    token = reg["token"]

    # 登录
    login = _post(base_url, "/api/login", {"username": "smoke_user", "password": "smoke123"})
    assert login["ok"] and login["token"]

    # 错误密码
    bad = _post(base_url, "/api/login", {"username": "smoke_user", "password": "wrong!"})
    assert not bad["ok"]

    # 自测快照
    assert _post(base_url, "/api/save_quiz", {"token": token, "answers": [0.5, -1]})["ok"]

    # 存一局战绩
    summary = {"score": 66, "tier": "平平无奇", "final_assets": 1088000, "total_ret_pct": 8.8,
               "bench_ret_pct": 5.0, "tax_yuan": -12000, "mdd_pct": 9.0, "trades": 11,
               "mbti_self": "CPNH", "mbti_measured": "CPNH", "mbti_name": "定投苦行僧"}
    saved = _post(base_url, "/api/save_game", {"token": token, "summary": summary,
                                               "detail": {"rounds": [], "flags": {}, "summary": "ok"},
                                               "quiz": [0.5, -1]})
    assert saved["ok"]

    # AI 兜底（无 API key → 必须走本地规则版，不能 500）
    ai = _post(base_url, "/api/analyze", {"result": {"trades": 0}, "flags": {}})
    assert ai["ok"] and ai["source"] == "fallback" and ai["mbti"]["mbti_code"]

    # 历史回读
    h = _post(base_url, "/api/history", {"token": token})
    assert h["ok"] and h["username"] == "smoke_user" and len(h["games"]) == 1
    assert h["games"][0]["score"] == 66
    assert h["quiz"] == [0.5, -1]

    # AI 补写
    assert _post(base_url, "/api/save_ai", {"token": token, "ai": {"one_liner": "测试"}})["ok"]


def test_bad_json_400(base_url):
    req = urllib.request.Request(base_url + "/api/login", data=b"not json",
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_unknown_api_404(base_url):
    try:
        _post(base_url, "/api/nothing", {})
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 404
