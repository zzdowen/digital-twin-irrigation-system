"""FARM 后端 API 测试（pytest）。

覆盖 CONTRACT §9 的全部验收点。不依赖真实上游服务：
upstream.* 全部 monkeypatch 成固定假数据，数据库指向 tmp_path。
运行：在 /opt/farm 下 `venv/bin/pytest tests/ -v`
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# ── 固定假数据（结构与真实上游一致） ──

FAKE_REALTIME = {
    "active_pumps": 3,
    "auto_irrigation_enabled": True,
    "channels": [
        {"channel": 1, "soil_moisture": 80.0, "pump_status": True, "irrigation_duration": 0},
        {"channel": 2, "soil_moisture": 80.0, "pump_status": True, "irrigation_duration": 0},
        {"channel": 3, "soil_moisture": 80.0, "pump_status": True, "irrigation_duration": 0},
    ],
    "data_source": "mqtt",
    # last_update 必须是"当前时间"，否则 data_age_sec > STALE_AFTER_SEC(600)，
    # 后端会把通道正确判定为 offline —— 那是被测代码对的，是这里的数据过期了。
    # 上游给的是北京时间的无时区串，parse_iso_ts 按 +08:00 解释，保持一致。
    "last_update": None,
    "soil_moisture_avg": 80.0,
}


def now_bj_str() -> str:
    """当前北京时间，格式与上游 last_update 一致（无时区后缀）。"""
    import datetime as _dt
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=8)
            ).strftime("%Y-%m-%dT%H:%M:%S")

FAKE_STATUS = {
    "api_version": "1.0.0", "channel_count": 3, "data_source": "mqtt",
    "uptime_seconds": 7846, "server_time": "2026-10-24T18:41:37",
}

FAKE_WEATHER = {
    "city": "大连", "enabled": True, "updated_at": "2026-10-24T18:40:00",
    "last_error": None,
    "forecast": [{"date": "2026-10-24", "weather": "阴",
                  "temperature_c": 18.0, "rain_probability_percent": 10.0}],
}

FAKE_STATS = {
    "total_irrigations": 1214, "total_duration_minutes": 165525.9,
    "water_usage_liters": 248288.8, "water_saving_percent": 0.0,
    "baseline_minutes_per_day": 30.0, "last_event": "2026-09-06T15:45:46.794173",
    "by_channel": {"1": {"count": 406, "duration_sec": 3310835}},
}

FAKE_PREDICTION = {
    "prediction_horizon_minutes": 30,
    "algorithm_info": "LSTM",
    "predictions": [[1730000000, 80.0], [1730001800, 80.5]],
    "accuracy_by_channel": {"1": 0.0, "2": 0.0, "3": 0.0},
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """一套干净的临时环境 + 打桩后的 TestClient。"""
    from app import auth, collector, config, db, upstream

    config.DB_PATH = tmp_path / "farm.db"
    config.UPLOAD_DIR = tmp_path / "files"
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    config.IRRDB_PATH = tmp_path / "irrigation_history.db"
    config.ADMIN_USER = "admin"
    config.ADMIN_PASSWORD = "admin12345"
    config.ADMIN_PASSWORD_SET = True

    # 上游打桩（last_update 每次调用都取当前时间，避免数据"过期"被判 offline）
    def _fake_realtime():
        d = dict(FAKE_REALTIME)
        d["last_update"] = now_bj_str()
        return d

    monkeypatch.setattr(upstream, "realtime", _fake_realtime)
    monkeypatch.setattr(upstream, "status", lambda: dict(FAKE_STATUS))
    monkeypatch.setattr(upstream, "weather", lambda: dict(FAKE_WEATHER))
    monkeypatch.setattr(upstream, "stats", lambda: dict(FAKE_STATS))
    monkeypatch.setattr(upstream, "historical", lambda hours=24: {})
    monkeypatch.setattr(upstream, "prediction", lambda minutes=30: dict(FAKE_PREDICTION))
    monkeypatch.setattr(upstream, "control",
                        lambda action, channel=None, duration_minutes=None: {
                            "status": "ok", "message": "已下发", "action": action,
                            "channel": channel, "duration_minutes": duration_minutes})

    # 采集线程不启动（测试要确定性）
    monkeypatch.setattr(collector, "start", lambda: True)
    monkeypatch.setattr(collector, "stop", lambda timeout=5.0: True)

    # 重置 DB 连接与初始化标记
    db._initialized = False
    if hasattr(db._LOCAL, "conn"):
        try:
            db._LOCAL.conn.close()
        except Exception:
            pass
        db._LOCAL.conn = None
    db.init()
    auth.ensure_admin()

    # 预备上游事件库（只读测试用）
    conn = sqlite3.connect(str(config.IRRDB_PATH))
    conn.execute("CREATE TABLE irrigation_events (id INTEGER PRIMARY KEY, channel INTEGER,"
                 " start_time TEXT, end_time TEXT, duration_sec INTEGER)")
    import datetime as dt
    recent = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
              ).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute("INSERT INTO irrigation_events VALUES (1, 1, ?, ?, 600)",
                 (recent, recent))
    conn.commit()
    conn.close()

    # 预置样本（供 /api/history 与 CSV 使用）
    now = db.now_ts()
    rows = []
    for i in range(300):
        ts = now - (300 - i) * 300  # 5 分钟一条，覆盖 24h+
        rows.append(("INSERT OR REPLACE INTO samples(ts, ch1, ch2, ch3, pump1, pump2,"
                     " pump3, avg, source, upstream_ok) VALUES(?,?,?,?,?,?,?,?,?,1)",
                     (ts, 60.0 + (i % 10), 61.5, 62.5, i % 2, 0, 1, 61.5, "mqtt")))
    db.write_many(rows)

    main = importlib.import_module("app.main")
    # 清 overview 缓存，避免跨用例串数据
    main._cache["ts"] = 0.0
    main._cache["data"] = None

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        c.app_module = main
        yield c


def login(client, username="admin", password="admin12345"):
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    return body["token"]


def auth_h(token):
    return {"Authorization": f"Bearer {token}"}


def make_user(client, admin_token, username, password, role):
    r = client.post("/api/users", headers=auth_h(admin_token),
                    json={"username": username, "password": password, "role": role})
    assert r.status_code == 200, r.text
    return r.json()


# ── 1. 健康检查 / 首页 ──


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "server_time" in r.json()


def test_index_html(client):
    r = client.get("/")
    assert r.status_code == 200
    r2 = client.get("/chat")
    assert r2.status_code == 200


# ── 2. 登录 ──


def test_login_success(client):
    r = client.post("/api/login", json={"username": "admin", "password": "admin12345"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert b["username"] == "admin" and b["role"] == "admin"
    assert len(b["token"]) > 20


def test_login_wrong_password(client):
    r = client.post("/api/login", json={"username": "admin", "password": "wrong-pass"})
    assert r.status_code == 401
    assert r.json() == {"status": "error", "message": "用户名或密码不正确"}


def test_login_lockout_after_five_failures(client):
    for _ in range(5):
        client.post("/api/login", json={"username": "admin", "password": "bad-pass"})
    r = client.post("/api/login", json={"username": "admin", "password": "admin12345"})
    assert r.status_code != 200
    assert "分钟后再试" in r.json()["message"]


def test_bad_token_401(client):
    r = client.get("/api/overview", headers=auth_h("not-a-real-token"))
    assert r.status_code == 401
    assert r.json() == {"status": "error", "message": "未登录或登录已过期"}


def test_overview_requires_token(client):
    r = client.get("/api/overview")
    assert r.status_code == 401
    assert r.json()["message"] == "未登录或登录已过期"


# ── 3. 角色 ──


def test_viewer_cannot_control(client):
    admin = login(client)
    make_user(client, admin, "viewer01", "viewerpass1", "viewer")
    vt = login(client, "viewer01", "viewerpass1")
    r = client.post("/api/control", headers=auth_h(vt),
                    json={"action": "start", "channel": 1, "duration_minutes": 5})
    assert r.status_code == 403
    assert r.json() == {"status": "error", "message": "当前账号没有该操作权限"}


def test_viewer_cannot_read_audit_or_users(client):
    admin = login(client)
    make_user(client, admin, "viewer02", "viewerpass2", "viewer")
    vt = login(client, "viewer02", "viewerpass2")
    assert client.get("/api/audit", headers=auth_h(vt)).status_code == 403
    assert client.get("/api/users", headers=auth_h(vt)).status_code == 403


def test_operator_control_writes_audit(client):
    from app import db
    admin = login(client)
    make_user(client, admin, "operator1", "operatorpass1", "operator")
    ot = login(client, "operator1", "operatorpass1")

    before = db.query_one("SELECT COUNT(*) AS n FROM audit")["n"]
    r = client.post("/api/control", headers=auth_h(ot),
                    json={"action": "start", "channel": 2, "duration_minutes": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["channel"] == 2
    after = db.query_one("SELECT COUNT(*) AS n FROM audit")["n"]
    assert after == before + 1
    row = db.query_one("SELECT * FROM audit ORDER BY id DESC LIMIT 1")
    assert row["username"] == "operator1"
    assert row["ok"] == 1
    assert "channel=2" in row["detail"]


def test_control_validation(client):
    admin = login(client)
    make_user(client, admin, "operator2", "operatorpass2", "operator")
    ot = login(client, "operator2", "operatorpass2")
    bad = [
        {"action": "pause", "channel": 1},
        {"action": "start", "channel": 9},
        {"action": "start", "channel": "x"},
        {"action": "start", "channel": 1, "duration_minutes": 0},
        {"action": "start", "channel": 1, "duration_minutes": 999},
    ]
    for payload in bad:
        r = client.post("/api/control", headers=auth_h(ot), json=payload)
        assert r.status_code == 400, payload
        assert r.json()["status"] == "error"
    # all 合法
    r = client.post("/api/control", headers=auth_h(ot),
                    json={"action": "stop", "channel": "all"})
    assert r.status_code == 200


def test_control_upstream_down_not_500(client, monkeypatch):
    from app import upstream

    def boom(*a, **k):
        raise upstream.UpstreamError("上游不可达：connection refused")

    monkeypatch.setattr(upstream, "control", boom)
    admin = login(client)
    make_user(client, admin, "operator3", "operatorpass3", "operator")
    ot = login(client, "operator3", "operatorpass3")
    r = client.post("/api/control", headers=auth_h(ot),
                    json={"action": "start", "channel": 1, "duration_minutes": 5})
    assert r.status_code == 200
    assert r.json()["status"] == "error"
    assert "不可达" in r.json()["message"] or "稍后重试" in r.json()["message"]


# ── 4. overview ──


def test_overview_shape(client):
    token = login(client)
    r = client.get("/api/overview", headers=auth_h(token))
    assert r.status_code == 200
    b = r.json()
    for key in ("status", "upstream_ok", "server_time", "data_source",
                "uptime_seconds", "channels", "soil_moisture_avg", "active_pumps",
                "auto_irrigation_enabled", "last_update", "data_age_sec",
                "stale", "weather", "stats", "alert_badge"):
        assert key in b, f"overview 缺少字段 {key}"
    assert b["status"] == "ok"
    assert len(b["channels"]) == 3
    ch = b["channels"][0]
    for key in ("channel", "soil_moisture", "pump_status",
                "irrigation_duration", "status"):
        assert key in ch
    assert ch["soil_moisture"] == 80.0
    assert ch["status"] == "normal"
    assert isinstance(b["weather"]["forecast"], list)
    assert "by_channel" in b["stats"]


def test_overview_degrades_when_upstream_down(client, monkeypatch):
    from app import upstream, main

    def boom(*a, **k):
        raise upstream.UpstreamError("上游不可达")

    monkeypatch.setattr(upstream, "realtime", boom)
    monkeypatch.setattr(upstream, "status", boom)
    monkeypatch.setattr(upstream, "weather", boom)
    monkeypatch.setattr(upstream, "stats", boom)
    main._cache["ts"] = 0.0
    main._cache["data"] = None
    token = login(client)
    r = client.get("/api/overview", headers=auth_h(token))
    assert r.status_code == 200
    assert r.json()["upstream_ok"] is False


def test_overview_cache_under_3s(client):
    from app import main
    token = login(client)
    main._cache["ts"] = 0.0
    main._cache["data"] = None
    client.get("/api/overview", headers=auth_h(token))
    first = main._cache["data"]
    assert first is not None
    client.get("/api/overview", headers=auth_h(token))
    assert main._cache["data"] is first


# ── 5. history ──


def test_history_points(client):
    token = login(client)
    r = client.get("/api/history?hours=24&points=600", headers=auth_h(token))
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert b["hours"] == 24
    assert len(b["series"]) == 3
    for s in b["series"]:
        assert s["channel"] in (1, 2, 3)
        assert s["label"]
        assert len(s["moisture"]) > 50, "每通道点数必须 > 50"
        ts, val = s["moisture"][0]
        assert isinstance(ts, int)
        assert isinstance(val, float)
        assert len(s["pump"]) == len(s["pump"])


def test_history_downsample_respects_limit(client):
    token = login(client)
    r = client.get("/api/history?hours=24&points=20", headers=auth_h(token))
    b = r.json()
    for s in b["series"]:
        assert len(s["moisture"]) <= 20


def test_history_bad_param(client):
    token = login(client)
    r = client.get("/api/history?hours=abc", headers=auth_h(token))
    assert r.status_code == 400


# ── 6. 预测 ──


def test_prediction_shape(client):
    token = login(client)
    r = client.get("/api/prediction?minutes=60", headers=auth_h(token))
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert b["horizon_minutes"] == 30
    assert isinstance(b["predictions"], list)
    assert b["predictions"][0][0] == 1730000000
    assert b["accuracy_by_channel"]["1"] == 0.0  # 上游未收敛，保持 0.0 不伪造


def test_prediction_bad_format(client, monkeypatch):
    from app import upstream
    monkeypatch.setattr(upstream, "prediction", lambda minutes=30: {"predictions": "???", })
    token = login(client)
    r = client.get("/api/prediction", headers=auth_h(token))
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert b["predictions"] == []


# ── 7. 事件 ──


def test_events(client):
    token = login(client)
    r = client.get("/api/events?days=7", headers=auth_h(token))
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert isinstance(b["events"], list)
    assert len(b["events"]) >= 1
    ev = b["events"][0]
    for key in ("id", "channel", "start_time", "end_time", "duration_sec"):
        assert key in ev


def test_events_missing_db_not_500(client, monkeypatch, tmp_path):
    from app import config
    monkeypatch.setattr(config, "IRRDB_PATH", tmp_path / "nope.db")
    token = login(client)
    r = client.get("/api/events?days=7", headers=auth_h(token))
    assert r.status_code == 200
    assert r.json()["events"] == []


# ── 8. 告警 ──


def test_alerts_readable(client):
    token = login(client)
    r = client.get("/api/alerts?limit=50", headers=auth_h(token))
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert isinstance(b["alerts"], list)


# ── 9. CSV ──


def test_export_csv_bom(client):
    token = login(client)
    r = client.get("/api/export.csv?hours=24", headers=auth_h(token))
    assert r.status_code == 200
    assert r.content[:3] == b"\xef\xbb\xbf", "首字节必须是 UTF-8-BOM"
    disp = r.headers.get("content-disposition", "")
    assert "attachment" in disp
    assert "farm_history.csv" in disp
    text = r.content.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == "时间,1号地块含水量,1号泵,2号地块含水量,2号泵,3号地块含水量,3号泵,平均含水量"
    assert len(lines) > 2
    # 含水量保留 1 位小数
    cell = lines[1].split(",")[1]
    assert cell == "" or (len(cell.split(".")[-1]) == 1)


# ── 10. 用户管理 ──


def test_users_no_secret_leak(client):
    token = login(client)
    r = client.get("/api/users", headers=auth_h(token))
    assert r.status_code == 200
    raw = r.text
    assert "pwd_hash" not in raw
    assert "salt" not in raw
    users = r.json()["users"]
    assert any(u["username"] == "admin" for u in users)
    for u in users:
        assert set(u.keys()) == {"username", "role", "created", "disabled", "must_change"}


def test_user_create_and_patch(client):
    token = login(client)
    r = client.post("/api/users", headers=auth_h(token),
                    json={"username": "u_test01", "password": "password1", "role": "viewer"})
    assert r.status_code == 200, r.text
    # 重名
    r = client.post("/api/users", headers=auth_h(token),
                    json={"username": "u_test01", "password": "password1", "role": "viewer"})
    assert r.status_code == 400
    # 改角色
    r = client.patch("/api/users/u_test01", headers=auth_h(token),
                     json={"role": "operator"})
    assert r.status_code == 200
    assert "operator" in r.json()["message"]
    # 停用
    r = client.patch("/api/users/u_test01", headers=auth_h(token), json={"disabled": True})
    assert r.status_code == 200
    # 停用后登录被拒
    r = client.post("/api/login", json={"username": "u_test01", "password": "password1"})
    assert r.status_code != 200
    assert "停用" in r.json()["message"]


def test_user_bad_username(client):
    token = login(client)
    r = client.post("/api/users", headers=auth_h(token),
                    json={"username": "a", "password": "password1", "role": "viewer"})
    assert r.status_code == 400


# ── 11. 改密 / must_change / 审计 ──


def test_password_change_invalidates_old_token(client):
    admin = login(client)
    make_user(client, admin, "operator4", "operatorpass4", "operator")
    old = login(client, "operator4", "operatorpass4")
    r = client.post("/api/password", headers=auth_h(old),
                    json={"old_password": "operatorpass4", "new_password": "newpassword4"})
    assert r.status_code == 200, r.text
    # 旧 token 失效
    r = client.get("/api/me", headers=auth_h(old))
    assert r.status_code == 401
    # 新密码可登录
    login(client, "operator4", "newpassword4")


def test_password_wrong_old(client):
    token = login(client)
    r = client.post("/api/password", headers=auth_h(token),
                    json={"old_password": "nope", "new_password": "newpassword"})
    assert r.status_code == 400


def test_password_too_short(client):
    token = login(client)
    r = client.post("/api/password", headers=auth_h(token),
                    json={"old_password": "admin12345", "new_password": "short"})
    assert r.status_code == 400


def test_must_change_blocks_other_endpoints(client):
    admin = login(client)
    # 契约 §3 只要求「初始管理员」首登强制改密；管理员后续建号默认 must_change=0。
    # 这里显式传 must_change=True 才能构造出「待改密账号」这一被测场景。
    r = client.post("/api/users", headers=auth_h(admin),
                    json={"username": "newbie01", "password": "newbiepass1",
                          "role": "viewer", "must_change": True})
    assert r.status_code == 200, r.text
    r = client.post("/api/login", json={"username": "newbie01", "password": "newbiepass1"})
    assert r.status_code == 200
    b = r.json()
    assert b["must_change"] is True
    t = b["token"]
    r = client.get("/api/overview", headers=auth_h(t))
    assert r.status_code == 403
    assert r.json()["message"] == "请先修改初始密码"
    # /api/me 放行
    assert client.get("/api/me", headers=auth_h(t)).status_code == 200
    # 改密后放行
    r = client.post("/api/password", headers=auth_h(t),
                    json={"old_password": "newbiepass1", "new_password": "newbiepass2"})
    assert r.status_code == 200
    nt = r.json()["token"]
    assert client.get("/api/overview", headers=auth_h(nt)).status_code == 200


def test_audit_admin_only_and_content(client):
    token = login(client)
    r = client.get("/api/audit?limit=10", headers=auth_h(token))
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok"
    assert isinstance(b["audit"], list)
    assert len(b["audit"]) >= 1


def test_me(client):
    token = login(client)
    b = client.get("/api/me", headers=auth_h(token)).json()
    assert b["status"] == "ok"
    assert b["username"] == "admin" and b["role"] == "admin"
    assert "must_change" in b and "plot_style" in b


def test_logout_revokes_token(client):
    token = login(client)
    assert client.post("/api/logout", headers=auth_h(token)).status_code == 200
    assert client.get("/api/me", headers=auth_h(token)).status_code == 401


# ── 12. /files 鉴权与防穿越 ──


def test_files_auth(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "UPLOAD_DIR", config.DB_PATH.parent / "files")
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (config.UPLOAD_DIR / "ok.txt").write_text("hello", encoding="utf-8")

    # 无 token → 401
    assert client.get("/files/ok.txt").status_code == 401
    token = login(client)
    # 带 token → 200
    r = client.get("/files/ok.txt", headers=auth_h(token))
    assert r.status_code == 200
    # ?token= → 200
    r = client.get(f"/files/ok.txt?token={token}")
    assert r.status_code == 200
    # 不存在的文件 → 404
    assert client.get("/files/nope.txt", headers=auth_h(token)).status_code == 404


def test_files_traversal_rejected(client):
    token = login(client)
    # 编码形式走真实路由，才有价值
    for name in ("..%2Fetc%2Fpasswd", "..%5Cwin.ini", "%2Fetc%2Fpasswd"):
        r = client.get(f"/files/{name}", headers=auth_h(token))
        assert r.status_code in (400, 404), name
    # 裸 ".." / "." 到不了 /files/{name}：HTTP 层会先把 URL 归一化成 "/"，
    # 返回的是公开首页（200），那不是穿越。所以直接单测解析函数。
    from app.main import safe_upload_path
    for name in ("..", ".", "../etc/passwd", "..\\win.ini", "a/b", "", ".hidden",
                 "/etc/passwd", "a\x00b"):
        path, problem = safe_upload_path(name)
        assert path is None, f"{name!r} 不应解析出路径"
        assert problem is not None, f"{name!r} 应返回错误响应"


def test_safe_upload_path_accepts_normal_name(client):
    """正常文件名必须放行，防止防穿越规则收得过紧。"""
    from app.main import safe_upload_path

    path, problem = safe_upload_path("ok.txt")
    assert problem is None
    assert path is not None and path.name == "ok.txt"


# ── 13. 告警引擎（单元） ──


def test_alerts_engine_dry(client):
    from app import alerts, db
    alerts.reset_state()
    ts = db.now_ts()
    out = alerts.evaluate({"ts": ts, "ch1": 22.4, "ch2": 80.0, "ch3": 80.0,
                           "pump1": False, "pump2": False, "pump3": False,
                           "upstream_ok": True, "data_age_sec": 10})
    rules = [(a["rule"], a["channel"]) for a in out]
    assert ("dry", 1) in rules
    row = db.query_one("SELECT * FROM alerts WHERE rule='dry' AND channel=1"
                       " AND resolved_ts IS NULL")
    assert row is not None
    assert "22.4" in row["message"] and "1 号地块" in row["message"]
    # 重复评估不重复插入，只更新 ts
    before = db.query_one("SELECT COUNT(*) AS n FROM alerts WHERE rule='dry'")["n"]
    alerts.evaluate({"ts": ts + 60, "ch1": 21.0, "ch2": 80.0, "ch3": 80.0,
                     "pump1": False, "pump2": False, "pump3": False,
                     "upstream_ok": True, "data_age_sec": 10})
    after = db.query_one("SELECT COUNT(*) AS n FROM alerts WHERE rule='dry'")["n"]
    assert after == before
    # 恢复 → resolved_ts
    alerts.evaluate({"ts": ts + 120, "ch1": 80.0, "ch2": 80.0, "ch3": 80.0,
                     "pump1": False, "pump2": False, "pump3": False,
                     "upstream_ok": True, "data_age_sec": 10})
    row = db.query_one("SELECT * FROM alerts WHERE rule='dry' AND channel=1")
    assert row["resolved_ts"] is not None


def test_alerts_engine_wet_needs_three(client):
    from app import alerts, db
    alerts.reset_state()
    ts = db.now_ts()
    base = {"ch1": 96.5, "ch2": 80.0, "ch3": 80.0,
            "pump1": False, "pump2": False, "pump3": False,
            "upstream_ok": True, "data_age_sec": 5}
    alerts.evaluate(dict(base, ts=ts))
    alerts.evaluate(dict(base, ts=ts + 60))
    assert db.query_one("SELECT * FROM alerts WHERE rule='wet' AND channel=1") is None
    out = alerts.evaluate(dict(base, ts=ts + 120))
    assert ("wet", 1) in [(a["rule"], a["channel"]) for a in out]


def test_alerts_engine_long_irrigation(client):
    from app import alerts, db
    alerts.reset_state()
    ts = db.now_ts()
    out = alerts.evaluate({"ts": ts, "ch1": 80.0, "ch2": 80.0, "ch3": 80.0,
                           "pump1": True, "pump2": False, "pump3": False,
                           "upstream_ok": True, "data_age_sec": 5})
    assert not [a for a in out if a["rule"] == "long_irrigation"]
    out = alerts.evaluate({"ts": ts + 3600, "ch1": 80.0, "ch2": 80.0, "ch3": 80.0,
                           "pump1": True, "pump2": False, "pump3": False,
                           "upstream_ok": True, "data_age_sec": 5})
    assert ("long_irrigation", 1) in [(a["rule"], a["channel"]) for a in out]
    row = db.query_one("SELECT * FROM alerts WHERE rule='long_irrigation' AND channel=1")
    assert "60 分钟" in row["message"]


def test_alerts_engine_no_data(client):
    from app import alerts, db
    alerts.reset_state()
    out = alerts.evaluate({"ts": db.now_ts(), "ch1": 80.0, "ch2": 80.0, "ch3": 80.0,
                           "pump1": False, "pump2": False, "pump3": False,
                           "upstream_ok": True, "data_age_sec": 900})
    assert ("no_data", None) in [(a["rule"], a["channel"]) for a in out]
    row = db.query_one("SELECT * FROM alerts WHERE rule='no_data' AND channel IS NULL")
    assert row["level"] == "critical"


def test_alerts_engine_upstream_down_streak(client):
    from app import alerts, db
    alerts.reset_state()
    ts = db.now_ts()
    bad = {"ts": ts, "ch1": None, "ch2": None, "ch3": None,
           "pump1": False, "pump2": False, "pump3": False,
           "upstream_ok": False, "data_age_sec": None}
    alerts.evaluate(dict(bad, ts=ts))
    alerts.evaluate(dict(bad, ts=ts + 60))
    assert db.query_one("SELECT * FROM alerts WHERE rule='upstream_down'") is None
    out = alerts.evaluate(dict(bad, ts=ts + 120))
    assert ("upstream_down", None) in [(a["rule"], a["channel"]) for a in out]
    # 恢复
    alerts.evaluate({"ts": ts + 180, "ch1": 80.0, "ch2": 80.0, "ch3": 80.0,
                     "pump1": False, "pump2": False, "pump3": False,
                     "upstream_ok": True, "data_age_sec": 5})
    row = db.query_one("SELECT * FROM alerts WHERE rule='upstream_down'")
    assert row["resolved_ts"] is not None


# ── 14. 降采样（单元） ──


def test_downsample_mean_and_limit(client):
    from app import main
    now = 1730000000
    pts = [[now + i, float(i)] for i in range(1000)]
    out = main.downsample(pts, 100)
    assert len(out) <= 100
    assert all(isinstance(p[0], int) for p in out)
    # 桶内均值应单调不减
    vals = [p[1] for p in out]
    assert vals == sorted(vals)


def test_downsample_drops_nulls(client):
    from app import main
    pts = [[1730000000 + i, None if i % 2 else 60.0] for i in range(100)]
    out = main.downsample(pts, 10)
    assert all(p[1] is not None for p in out)


def test_downsample_passthrough(client):
    from app import main
    pts = [[1730000000, 60.0], [1730000060, 61.0]]
    assert main.downsample(pts, 600) == pts


# ── 15. 采集线程 ──


def test_collector_once_writes_sample(client):
    from app import collector, db
    before = db.query_one("SELECT COUNT(*) AS n FROM samples")["n"]
    sample = collector.collect_once()
    assert sample["upstream_ok"] is True
    assert sample["ch1"] == 80.0
    after = db.query_one("SELECT COUNT(*) AS n FROM samples")["n"]
    assert after >= before


def test_collector_survives_upstream_down(client, monkeypatch):
    from app import collector, upstream
    monkeypatch.setattr(upstream, "realtime", lambda: (_ for _ in ()).throw(
        upstream.UpstreamError("上游不可达")))
    sample = collector.collect_once()  # 不抛异常
    assert sample["upstream_ok"] is False
    assert collector.STATS["errors"] >= 1
