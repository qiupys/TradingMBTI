# -*- coding: utf-8 -*-
"""K线时光机本地服务：
- 静态文件
- /api/analyze    codebuddy CLI · hy3 模型生成炒股 MBTI
- /api/register   注册（用户名 + 密码，PBKDF2 加盐哈希，sqlite3 落盘）
- /api/login      登录（返回内存会话 token）
- /api/save_game  登录用户保存一局体检成绩
- /api/history    拉取最近 20 次历史战绩
"""
import hashlib
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL = "hy3"
# API key 从环境变量读取（勿硬编码提交）；未配置时 /api/analyze 自动走本地规则兜底
CB_KEY = os.environ.get("CODEBUDDY_API_KEY", "")
# 两条通道任选其一：1) LLM_BASE_URL/LLM_API_KEY/LLM_MODEL 指向任意 OpenAI 兼容接口（HTTP 直连，优先）；
#                    2) 服务器安装 @tencent-ai/codebuddy-code 并迁移首次激活设备的 ~/.codebuddy 会话状态
#                       （key 与设备绑定，缺一不可），由 systemd 注入 CODEBUDDY_API_KEY + PATH/HOME
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
PORT = int(os.environ.get("PORT", "8741"))
HOST = os.environ.get("HOST", "127.0.0.1")
# 测试可通过 TT_DB 指定独立数据库，避免碰真实用户数据
DB_PATH = os.environ.get("TT_DB", os.path.join(ROOT, "timetrader.db"))

_db_lock = threading.Lock()
_sessions = {}          # token -> user_id（内存会话；重启后需重新登录）


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db_lock, _conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            pw_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            quiz TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS games(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            score INTEGER, tier TEXT, final_assets REAL, total_ret_pct REAL,
            bench_ret_pct REAL, tax_yuan REAL, mdd_pct REAL, trades INTEGER,
            mbti_self TEXT, mbti_measured TEXT, mbti_name TEXT, detail TEXT, quiz TEXT)""")
        # 旧库平滑迁移：缺列则补
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "quiz" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN quiz TEXT")
        gcols = {r[1] for r in conn.execute("PRAGMA table_info(games)").fetchall()}
        if "quiz" not in gcols:
            conn.execute("ALTER TABLE games ADD COLUMN quiz TEXT")


def _hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes.fromhex(salt), 60000).hex()


def _new_session(user_id):
    token = secrets.token_hex(16)
    _sessions[token] = user_id
    return token


def _user_from_token(token):
    uid = _sessions.get(token or "")
    if not uid:
        return None
    with _db_lock, _conn() as conn:
        row = conn.execute("SELECT id, username FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None


PROMPT_TMPL = """你是行为金融学专家，正在给"K线时光机"游戏的一位玩家做总复盘。
游戏规则：玩家先做 20 道自测题（可跳过任意题目，跳过的维度自评为"?"）得到"自评 MBTI"；随后进入十局制——每一局是一只不同股票的一段真实历史时间窗口（约50个交易日，覆盖低波动白马到创业板/科创板高波动标的），开局公开标的名字与历史资讯，但玩家看不见未来走势；初始资金100万跨局结转；买入最多两档（半仓→满仓），卖出最多两档（卖半→清仓），窗口结束自动按当日收盘价强平结算；跳过=该局无操作。
自创 MBTI 四维度（每个维度一对字母，按实测行为计算）：R=B梭哈型/C稳健型（风险承受）；S=Q快枪型/P蛰伏型（决策频率）；I=N消息型/F信仰型（信息依赖）；P=T拿不住型/H死扛型（盈亏处理）。类型名称由产品内置注册表给出，你不需要另起名字。
玩家数据（JSON，含十局每局的标的与操作、总结果指标、自评与实测 MBTI 及逐维度漂移）：
{payload}

请从跨局行为模式分析（例如：只在某类波动率的标的上重仓、低波局频繁乱动而高波局反而不敢动、逢局必梭哈、卖盈持亏等），重点点评"自评 vs 实测"的认知漂移（哪些维度跑偏了、为什么、说明什么）。所有引用的数字必须直接来自玩家数据，不得编造。用中文输出【严格 JSON】，除 JSON 外不要输出任何其他文字：
{{
  "one_liner": "一句话毒舌但善意的总评，不超过30字，可化用该类型的形象梗",
  "drift_note": "自我认知与实际操作的漂移点评，80字以内，点名跑偏的具体维度",
  "strengths": ["优势1", "优势2"],
  "weaknesses": ["不足1", "不足2"],
  "advice": ["可执行建议1", "可执行建议2", "可执行建议3"],
  "segment_note": "点评最关键的一局（点名第几局、哪只股票，引用该局真实收益数字），说明为什么"
}}"""


def fallback_mbti(data):
    """规则版兜底：按行为标记给一个粗略 MBTI。"""
    f = data.get("flags", {})
    trades_n = data.get("result", {}).get("trades", 0)
    if f.get("chase", 0) >= 2:
        code, name = "FOMO", "热点追逐者"
    elif f.get("panic", 0) >= 1:
        code, name = "PANI", "惊弓之鸟"
    elif not trades_n:
        code, name = "WAIT", "全程围观党"
    elif f.get("sold_fly", 0) >= 1:
        code, name = "SHIP", "拿不住先生"
    else:
        code, name = "CALM", "佛系定力王"
    return {
        "mbti_code": code, "mbti_name": name,
        "one_liner": "本地规则版点评：AI 服务不可用时的兜底结论。",
        "strengths": ["你能坚持完十段，已经赢过一半半途而废的人"],
        "weaknesses": ["样本行为较少，规则版无法展开"],
        "advice": ["联网后可获得混元 hy3 模型的完整分析"],
        "segment_note": "无",
    }


def extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call_model_http(prompt, timeout=120):
    """OpenAI 兼容 HTTP 直连（云服务器用）：LLM_BASE_URL + LLM_API_KEY + LLM_MODEL 三者齐备才启用。"""
    if not (LLM_BASE_URL and LLM_API_KEY and LLM_MODEL):
        raise RuntimeError("LLM HTTP 未配置")
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }).encode("utf-8")
    req = urllib.request.Request(
        LLM_BASE_URL + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + LLM_API_KEY})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    content = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("LLM HTTP 空响应")
    return content


def call_model(prompt, timeout=300):
    """优先 HTTP 直连；失败回退 codebuddy CLI（本机开发）；都不可用由调用方走规则兜底。"""
    try:
        return call_model_http(prompt)
    except Exception as http_err:
        if not CB_KEY:
            raise RuntimeError(f"LLM HTTP 失败且 CODEBUDDY_API_KEY 未配置: {http_err}")
    env = os.environ.copy()
    env["CODEBUDDY_API_KEY"] = CB_KEY
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    last_err = None
    for _ in range(3):                      # 端口竞态等偶发问题最多重试 3 次
        out, err = "", ""
        try:
            r = subprocess.run(
                ["codebuddy", "--port", str(_free_port()), "-p", prompt,
                 "--model", MODEL],
                capture_output=True, text=True, timeout=timeout, cwd=ROOT, env=env,
            )
            out, err = r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired:
            raise
        except Exception as e:
            last_err = e
            continue
        if "EADDRINUSE" in out or "EADDRINUSE" in err:
            last_err = RuntimeError("EADDRINUSE")
            continue
        return out
    raise RuntimeError(f"codebuddy cli failed: {last_err}")


# ---------------- API 处理函数 ----------------

def api_register(data):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not (2 <= len(username) <= 20):
        return {"ok": False, "error": "用户名需 2–20 个字符"}
    if len(password) < 6:
        return {"ok": False, "error": "密码至少 6 位"}
    salt = secrets.token_hex(16)
    pw_hash = _hash_pw(password, salt)
    try:
        with _db_lock, _conn() as conn:
            cur = conn.execute(
                "INSERT INTO users(username, pw_hash, salt) VALUES(?,?,?)",
                (username, pw_hash, salt))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "用户名已被注册，换个名字或直接登录"}
    return {"ok": True, "user_id": uid, "username": username,
            "token": _new_session(uid)}


def api_login(data):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    with _db_lock, _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or _hash_pw(password, row["salt"]) != row["pw_hash"]:
        return {"ok": False, "error": "用户名或密码错误"}
    return {"ok": True, "user_id": row["id"], "username": row["username"],
            "token": _new_session(row["id"]),
            "quiz": json.loads(row["quiz"]) if row["quiz"] else None}


def api_save_quiz(data):
    """开局自测完成后保存答案快照（users 存最新一份用于下次预填）。"""
    user = _user_from_token(data.get("token"))
    if not user:
        return {"ok": False, "error": "登录已过期"}
    answers = data.get("answers")
    if not isinstance(answers, list):
        return {"ok": False, "error": "bad answers"}
    with _db_lock, _conn() as conn:
        conn.execute("UPDATE users SET quiz=? WHERE id=?",
                     (json.dumps(answers, ensure_ascii=False), user["id"]))
    return {"ok": True}


def api_save_game(data):
    user = _user_from_token(data.get("token"))
    if not user:
        return {"ok": False, "error": "登录已过期"}
    s = data.get("summary") or {}
    detail = json.dumps(data.get("detail") or {}, ensure_ascii=False)
    quiz = json.dumps(data.get("quiz"), ensure_ascii=False) if data.get("quiz") else None
    with _db_lock, _conn() as conn:
        cur = conn.execute(
            """INSERT INTO games(user_id, score, tier, final_assets, total_ret_pct,
               bench_ret_pct, tax_yuan, mdd_pct, trades, mbti_self, mbti_measured,
               mbti_name, detail, quiz) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (user["id"], s.get("score"), s.get("tier"), s.get("final_assets"),
             s.get("total_ret_pct"), s.get("bench_ret_pct"), s.get("tax_yuan"),
             s.get("mdd_pct"), s.get("trades"), s.get("mbti_self"),
             s.get("mbti_measured"), s.get("mbti_name"), detail, quiz))
        gid = cur.lastrowid
    return {"ok": True, "id": gid}


def api_save_ai(data):
    """AI 点评异步返回后补写进最近一次体检的 detail（存档先行，AI 后到）。"""
    user = _user_from_token(data.get("token"))
    if not user:
        return {"ok": False, "error": "登录已过期"}
    ai = data.get("ai") or {}
    with _db_lock, _conn() as conn:
        row = conn.execute(
            "SELECT id, detail FROM games WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user["id"],)).fetchone()
        if not row:
            return {"ok": False, "error": "暂无战绩可补写"}
        try:
            det = json.loads(row["detail"]) if row["detail"] else {}
        except Exception:
            det = {}
        det["ai"] = ai
        conn.execute("UPDATE games SET detail=? WHERE id=?",
                     (json.dumps(det, ensure_ascii=False), row["id"]))
    return {"ok": True}


def api_history(data):
    user = _user_from_token(data.get("token"))
    if not user:
        return {"ok": False, "error": "登录已过期"}
    with _db_lock, _conn() as conn:
        rows = conn.execute(
            """SELECT created_at, score, tier, final_assets, total_ret_pct,
               bench_ret_pct, tax_yuan, mdd_pct, trades, mbti_self,
               mbti_measured, mbti_name, detail FROM games WHERE user_id=?
               ORDER BY id DESC LIMIT 20""", (user["id"],)).fetchall()
        qrow = conn.execute("SELECT quiz FROM users WHERE id=?", (user["id"],)).fetchone()
    games = []
    for r in rows:
        g = dict(r)
        try:
            g["detail"] = json.loads(g["detail"]) if g["detail"] else None
        except Exception:
            g["detail"] = None
        games.append(g)
    # 上一次自测答案（供前端预填自测问卷）
    quiz = json.loads(qrow["quiz"]) if qrow and qrow["quiz"] else None
    return {"ok": True, "username": user["username"], "games": games, "quiz": quiz}


APIS = {
    "/api/register": api_register,
    "/api/login": api_login,
    "/api/save_quiz": api_save_quiz,
    "/api/save_game": api_save_game,
    "/api/save_ai": api_save_ai,
    "/api/history": api_history,
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8", ".css": "text/css; charset=utf-8",
            ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}

    def do_GET(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))
        if not fp.startswith(ROOT) or not os.path.isfile(fp):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(fp)[1].lower()
        with open(fp, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", self.MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/analyze":
            return self.handle_analyze()
        fn = APIS.get(self.path)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        try:
            return self._send(200, fn(data))
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)})

    def handle_analyze(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})

        payload = json.dumps(data, ensure_ascii=False)
        prompt = PROMPT_TMPL.replace("{payload}", payload)
        try:
            out = call_model(prompt)
            # 剥掉 CLI banner（"> Name · model" 行）
            lines = [ln for ln in out.splitlines() if not ln.startswith(">")]
            result = extract_json("\n".join(lines))
            if not result:
                raise ValueError("no json in model output: " + out[:200])
            return self._send(200, {"ok": True, "source": "codebuddy/" + MODEL, "mbti": result})
        except Exception as e:
            return self._send(200, {"ok": True, "source": "fallback", "mbti": fallback_mbti(data), "err": str(e)})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    init_db()
    print(f"K线时光机服务: http://{HOST}:{PORT}  (codebuddy CLI · model: {MODEL})")
    print(f"用户数据库: {DB_PATH}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
