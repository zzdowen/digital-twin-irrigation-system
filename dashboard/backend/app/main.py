"""FARM 监控看板 · FastAPI 应用。

只读上游灌溉服务 + 本项目自己的 SQLite，绝不修改上游。
路由清单与响应结构严格对齐 CONTRACT.md §4 / §5。
"""
import csv
import io
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import alerts, auth, collector, config, db, timer, upstream

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("farm.main")

__version__ = "1.0.0"

MSG_401 = "未登录或登录已过期"
MSG_403_ROLE = "当前账号没有该操作权限"
MSG_403_CHANGE = "请先修改初始密码"
MSG_UPSTREAM = "灌溉服务暂时不可达，请稍后重试"

# must_change=1 时仍然放行的路径（除 PUBLIC_PATHS 外）
CHANGE_EXEMPT = {"/api/me", "/api/password", "/api/logout",
                 "/api/login", "/health", "/", "/chat"}

# 公开路径（无需 token），与 config.PUBLIC_PATHS 合并
# 契约 §4 只放行这四个；/favicon.ico 为浏览器默认请求，放行避免日志噪音。
PUBLIC = set(config.PUBLIC_PATHS) | {"/favicon.ico"}

STARTED_TS = db.now_ts()

# ── overview 服务端缓存（契约要求 <= 3 秒） ──
_CACHE_TTL = 3.0
_cache: dict = {"ts": 0.0, "data": None, "lock": threading.Lock()}


# ── 小工具 ──


def err(message: str, code: int = 400, **extra) -> JSONResponse:
    payload = {"status": "error", "message": message}
    payload.update(extra)
    return JSONResponse(payload, status_code=code)


def ok(payload: dict) -> dict:
    out = {"status": "ok"}
    out.update(payload)
    return out


async def body_of(request: Request) -> dict:
    """读 JSON body，坏 body 一律当空 dict（不 500）。"""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        try:
            raw = await request.body()
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001
            data = {}
    return data if isinstance(data, dict) else {}


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def web_index_path() -> Path:
    return Path(__file__).resolve().parents[2] / "web" / "index.html"


FALLBACK_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>FARM · 智能灌溉监控看板</title>
<style>body{font-family:system-ui,sans-serif;background:#f5f7fa;color:#333;
margin:0;padding:48px}h1{color:#2c7fb8;font-size:22px}
.box{background:#fff;border-radius:10px;padding:24px;max-width:640px;
box-shadow:0 1px 3px rgba(0,0,0,.08)}code{background:#f0f2f5;padding:2px 6px;
border-radius:4px;font-family:ui-monospace,monospace}</style></head>
<body><h1>FARM · 智能灌溉监控看板</h1><div class="box">
<p>前端页面 <code>web/index.html</code> 尚未部署，或当前镜像里缺少该文件。</p>
<p>后端 API 已就绪：请访问 <code>/health</code> 检查服务状态，
使用 <code>POST /api/login</code> 获取 token 后调用其它接口。</p>
</div></body></html>"""


def serve_index() -> HTMLResponse:
    p = web_index_path()
    try:
        if p.is_file():
            return HTMLResponse(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        log.exception("读取前端页面失败")
    return HTMLResponse(FALLBACK_HTML, status_code=200)


# ── 鉴权 ──


def token_from(request: Request) -> str:
    hdr = request.headers.get("authorization") or ""
    if hdr.lower().startswith("bearer "):
        return hdr[7:].strip()
    if not hdr.lower().startswith("bearer"):
        # 有些客户端直接塞裸 token，也认
        pass
    q = request.query_params.get("token")
    if q:
        return q.strip()
    return ""


def is_public(path: str) -> bool:
    if path in PUBLIC:
        return True
    if path.startswith("/files/") and path != "/files/":
        return False  # /files 需要 token（可走 ?token=）
    return False


def authorize(path: str, token: str) -> tuple[object, dict | None]:
    """鉴权核心（同步、纯函数式，便于单测）。

    返回 (user_row, None) 表示放行；(None, 错误体) 表示拒绝。
    错误体已含 status/message/code，调用方据 code 返回 HTTP 状态码。
    """
    user, msg = auth.user_by_token(token)
    if user is None:
        if msg == "账号已停用":
            return None, {"status": "error", "message": "账号已停用", "code": 403}
        return None, {"status": "error", "message": MSG_401, "code": 401}
    if int(user["must_change"]) and path not in CHANGE_EXEMPT:
        return None, {"status": "error", "message": MSG_403_CHANGE, "code": 403}
    return user, None


# ── 生命周期 ──


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    try:
        info = auth.ensure_admin()
        if info.get("created"):
            log.info("已创建初始管理员 %s", info.get("username"))
    except Exception:  # noqa: BLE001
        log.exception("初始化管理员失败")
    try:
        collector.start()
    except Exception:  # noqa: BLE001
        log.exception("启动采集线程失败")
    log.info("FARM 后端已启动：%s", config.APP_TITLE)
    try:
        yield
    finally:
        try:
            collector.stop()
        except Exception:  # noqa: BLE001
            log.exception("停止采集线程失败")
        log.info("FARM 后端已关闭")


app = FastAPI(title=config.APP_TITLE, version=__version__, lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    request.state.user = None
    request.state.token = ""

    if request.method == "OPTIONS":
        return Response(status_code=204)
    if is_public(path):
        return await call_next(request)

    token = token_from(request)
    user, denial = authorize(path, token)
    if user is None:
        return JSONResponse({"status": "error", "message": denial["message"]},
                            status_code=int(denial["code"]))

    request.state.user = user
    request.state.token = token
    return await call_next(request)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Permitted-Cross-Domain-Policies": "none",
    # 单文件前端：脚本与样式都是内联的，所以必须放行 unsafe-inline；
    # 但把外链来源全部关死（default-src/connect-src/img-src 只允自己）。
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "Cache-Control": "no-store",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """给每个响应补安全头（防 MIME 嗅探、防点击劫持、防外链注入）。"""
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        # 静态图片缓存用 no-store 也没问题（都是私有数据），统一加
        resp.headers.setdefault(k, v)
    return resp


def need_role(request: Request, roles) -> JSONResponse | None:
    """角色校验；不合规返回 403 响应，合规返回 None。"""
    user = getattr(request.state, "user", None)
    if user is None:
        return err(MSG_401, 401)
    if user["role"] not in roles:
        auth.audit(user["username"], "越权访问", f"path={request.url.path}",
                   client_ip(request), ok=False)
        return err(MSG_403_ROLE, 403)
    return None


def current_user(request: Request):
    return getattr(request.state, "user", None)


# ── 公开端点 ──


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "app": config.APP_NAME,
        "version": __version__,
        "server_time": db.now_iso(),
        "uptime_seconds": max(0, db.now_ts() - STARTED_TS),
        "collector": collector.status(),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    return serve_index()


@app.get("/chat", response_class=HTMLResponse)
async def chat():
    return serve_index()


@app.post("/api/login")
async def api_login(request: Request):
    data = await body_of(request)
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    ip = client_ip(request)
    if not username or not password:
        return err("用户名或密码不正确", 401)
    result = auth.login(username, password)
    if result.get("status") != "ok":
        auth.audit(username, "登录失败", str(result.get("message") or ""), ip, ok=False)
        return err(result.get("message") or "用户名或密码不正确",
                   403 if "停用" in str(result.get("message")) else 401)
    auth.audit(username, "登录成功", f"role={result.get('role')}", ip, ok=True)
    return {
        "status": "ok",
        "token": result["token"],
        "username": result["username"],
        "role": result["role"],
        "must_change": bool(result.get("must_change")),
    }


# ── 账号 ──


@app.get("/api/me")
async def api_me(request: Request):
    u = current_user(request)
    return ok({"username": u["username"], "role": u["role"],
               "must_change": bool(u["must_change"]),
               "plot_style": _get_meta("plot_style", "soft")})


@app.post("/api/logout")
async def api_logout(request: Request):
    u = current_user(request)
    auth.revoke(request.state.token)
    auth.audit(u["username"], "退出登录", "", client_ip(request))
    return ok({"message": "已退出登录"})


@app.post("/api/password")
async def api_password(request: Request):
    u = current_user(request)
    if u is None:
        return err(MSG_401, 401)
    data = await body_of(request)
    old = str(data.get("old_password") or "")
    new = str(data.get("new_password") or "")
    if not auth.verify_password(old, u["pwd_hash"], u["salt"]):
        return err("原密码不正确", 400)
    if len(new) < 8:
        return err("密码至少 8 位", 400)
    if new == old:
        return err("新密码不能与原密码相同", 400)
    out = auth.set_password(u["username"], new, must_change=0)
    if out.get("status") != "ok":
        return err(out.get("message") or "修改密码失败", 400)
    # 改密会吊销全部旧 token（契约 §9 要求旧 token 失效），这里发一张新的
    token = auth.issue_token(u["username"])
    auth.audit(u["username"], "修改密码", "", client_ip(request))
    return ok({"message": "密码已更新，请使用新密码登录", "token": token,
               "must_change": False})


# ── 总览 ──


def _get_meta(key: str, default: str = "") -> str:
    try:
        row = db.query_one("SELECT v FROM meta WHERE k = ?", (key,))
        return row["v"] if row else default
    except Exception:  # noqa: BLE001
        return default


def _build_overview() -> dict:
    """合并 realtime + status + weather + stats（失败逐项降级）。"""
    now = db.now_ts()
    upstream_ok = True
    rt: dict = {}
    st: dict = {}
    w: dict = {}
    stats: dict = {}

    try:
        rt = upstream.realtime()
    except Exception as exc:  # noqa: BLE001
        log.warning("overview：realtime 拉取失败 %s", exc)
        upstream_ok = False
        rt = {}
    for name, fn, slot in (("status", upstream.status, "st"),
                           ("weather", upstream.weather, "w"),
                           ("stats", upstream.stats, "stats")):
        try:
            val = fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("overview：%s 拉取失败 %s", name, exc)
            upstream_ok = False
            continue
        if slot == "st":
            st = val or {}
        elif slot == "w":
            w = val or {}
        else:
            stats = val or {}

    chans = upstream.normalize_channels(rt)
    last_update_raw = rt.get("last_update") or st.get("last_update")
    last_ts = upstream.parse_iso_ts(last_update_raw)
    if last_ts is None:
        # 兜底：用最近一次采样时间
        row = db.query_one("SELECT MAX(ts) AS t FROM samples")
        last_ts = int(row["t"]) if row and row["t"] else None

    data_age_sec = None if last_ts is None else max(0, now - int(last_ts))
    stale = bool(data_age_sec is not None and data_age_sec > config.STALE_AFTER_SEC)
    if last_ts is None:
        stale = True

    channels = []
    moist_values = []
    for c in chans:
        m = c.get("soil_moisture")
        channels.append({
            "channel": c["channel"],
            "soil_moisture": m,
            "pump_status": bool(c.get("pump_status")),
            "irrigation_duration": int(c.get("irrigation_duration") or 0),
            "status": upstream.channel_status(m, stale),
        })
        if m is not None:
            moist_values.append(float(m))

    avg = rt.get("soil_moisture_avg")
    try:
        avg = round(float(avg), 1) if avg is not None else None
    except (TypeError, ValueError):
        avg = None
    if avg is None and moist_values:
        avg = round(sum(moist_values) / len(moist_values), 1)

    active = rt.get("active_pumps")
    try:
        active = int(active) if active is not None else sum(
            1 for c in channels if c["pump_status"])
    except (TypeError, ValueError):
        active = sum(1 for c in channels if c["pump_status"])

    weather = upstream.normalize_forecast(w) if w else {
        "city": None, "enabled": False, "updated_at": None,
        "last_error": None, "forecast": []}

    stats_out = {
        "total_irrigations": stats.get("total_irrigations"),
        "total_duration_sec": stats.get("total_duration_sec"),
        "total_duration_minutes": _round(stats.get("total_duration_minutes"), 1),
        "water_usage_liters": _round(stats.get("water_usage_liters"), 1),
        "water_saving_percent": _round(stats.get("water_saving_percent"), 1),
        "baseline_minutes_per_day": _round(stats.get("baseline_minutes_per_day"), 1),
        "last_event": stats.get("last_event"),
        "by_channel": stats.get("by_channel") or {},
    }

    # 计时器：把「已完成事件总时长」和「本次已证实运行时长」分开给前端。
    # 断流/读数过期时 running_sec 会自然冻结（不补算），并用 reason 说明原因。
    try:
        snap = timer.describe(channels, upstream_ok, data_age_sec,
                              stats.get("total_duration_sec"), now=float(now))
    except Exception:  # noqa: BLE001 —— 计时器故障不该让看板 500
        log.exception("计时器计算失败")
        snap = {"running_channels": [], "running_sec": 0, "counting": False,
                "capped": False, "fresh": False, "completed_known": False,
                "total_sec": 0, "reason": "计时器暂不可用", "as_of": db.now_iso()}
    stats_out["duration"] = snap

    return {
        "status": "ok",
        "upstream_ok": upstream_ok,
        "server_time": db.now_iso(),
        "data_source": rt.get("data_source") or st.get("data_source"),
        "uptime_seconds": int(st.get("uptime_seconds") or
                              max(0, db.now_ts() - STARTED_TS)),
        "channels": channels,
        "soil_moisture_avg": avg,
        "active_pumps": active,
        "auto_irrigation_enabled": rt.get("auto_irrigation_enabled"),
        "last_update": last_update_raw,
        "data_age_sec": data_age_sec,
        "stale": stale,
        "weather": weather,
        "stats": stats_out,
        "alert_badge": alerts.badge_count(),
    }


def _round(value, digits: int = 1):
    try:
        return round(float(value), digits) if value is not None else None
    except (TypeError, ValueError):
        return None


def _overview_cached() -> dict:
    """带 3 秒缓存的 overview 构建（失败返回降级体，绝不抛）。"""
    now = time.time()
    cached = _cache.get("data")
    with _cache["lock"]:
        if cached is not None and (now - float(_cache["ts"])) <= _CACHE_TTL:
            return cached
    try:
        data = _build_overview()
    except Exception:  # noqa: BLE001 —— 绝不让看板 500
        log.exception("overview 构建失败")
        return {
            "status": "error", "message": MSG_UPSTREAM, "stale": True,
            "upstream_ok": False, "server_time": db.now_iso(),
            "channels": [], "soil_moisture_avg": None, "active_pumps": 0,
            "alert_badge": alerts.badge_count(),
        }
    with _cache["lock"]:
        _cache["ts"] = now
        _cache["data"] = data
    return data


@app.get("/api/overview")
async def api_overview():
    return _overview_cached()


# ── 地块水文参数（公开常量，非实时数据） ──
# 与模拟器 moisture_simulator.py 的 SOILS 保持一致：三块地土壤质地不同，
# 所以「湿度多少算干/该补到多少」三块地本来就不一样，展示页要能解释这一点。
# 这里硬编码而不 import 模拟器：模拟器是独立服务、不在本进程里，跨服务 import
# 会让后端因模拟器缺失/改路径而启动失败。
# refill_target = fc + 3.0（略高于田间持水量，补水补到刚好不产生深层渗漏）。
CHANNEL_SOILS = {
    1: {"soil_type": "沙壤土", "field_capacity": 48.0,
        "wilting_point": 20.0, "target": 51.0},
    2: {"soil_type": "壤土", "field_capacity": 58.0,
        "wilting_point": 22.0, "target": 61.0},
    3: {"soil_type": "黏壤土", "field_capacity": 66.0,
        "wilting_point": 24.0, "target": 69.0},
}

# 展示页允许的查询范围（与 CONTRACT_V2 §2.2/2.3/2.4 一致）。
# hours 上限 720 = 30 天；points 上限 2000 是前端 canvas 能舒服画完的量级。
SHOWCASE_HISTORY_HOURS = (1, 720)
SHOWCASE_HISTORY_POINTS = (10, 2000)
SHOWCASE_PREDICTION_MINUTES = (30, 180)
SHOWCASE_ALERTS_LIMIT = (1, 100)


def clamp_int(raw, default: int, low: int, high: int) -> int:
    """把 query 参数夹到 [low, high]。非法/越界一律用 default 或边界，绝不报错。

    公开展示端点不能因为一个手抖的 URL 就 400 —— 前端拿到 400 只会显示空白。
    """
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return default
    return min(max(val, low), high)


def soil_of(channel) -> dict:
    """通道号 → 水文参数；未知通道返回空 dict（不让展示页因脏数据崩）。"""
    try:
        return CHANNEL_SOILS.get(int(channel), {})
    except (TypeError, ValueError):
        return {}


@app.get("/api/showcase")
async def api_showcase():
    """公开只读快照：只给 3D 展示页用的传感器聚合量。

    白名单字段，绝不包含用户名 / token / 审计 / 告警明细。
    数据不可用时 status=error，由前端显示「未连接」，绝不编造读数。
    """
    ov = _overview_cached()
    chans = []
    for c in (ov.get("channels") or []):
        soil = soil_of(c.get("channel"))
        chans.append({
            "channel": c.get("channel"),
            "soil_moisture": c.get("soil_moisture"),
            "pump_status": bool(c.get("pump_status")),
            "status": c.get("status"),
            # 灌溉时长给 3D 页驱动泵体动画强度（已有字段，v1 漏了，v2 补上）
            "irrigation_duration": int(c.get("irrigation_duration") or 0),
            "label": config.CHANNEL_LABELS.get(c.get("channel"),
                                               f"{c.get('channel')} 号地块"),
            "soil_type": soil.get("soil_type"),
            "field_capacity": soil.get("field_capacity"),
            "wilting_point": soil.get("wilting_point"),
            "target": soil.get("target"),
        })
    # 采集线程的运行态：只取计数类字段，last_error 也不出去（可能含上游 URL）。
    cst = collector.status()
    w = ov.get("weather") or {}
    fc = []
    for item in (w.get("forecast") or [])[:3]:
        fc.append({
            "date": item.get("date"),
            "weather": item.get("weather"),
            "temperature_c": item.get("temperature_c"),
            "rain_probability_percent": item.get("rain_probability_percent"),
        })
    s = ov.get("stats") or {}
    return {
        "status": ov.get("status") or "ok",
        "server_time": ov.get("server_time"),
        "upstream_ok": bool(ov.get("upstream_ok")),
        "data_source": ov.get("data_source"),
        "stale": bool(ov.get("stale")),
        "data_age_sec": ov.get("data_age_sec"),
        # 进程运行时长：与 /health 同一套逻辑（/health 优先用上游自报的 uptime，
        # 这里沿用同名字段，前端两处显示的数字才不会互相打架）。
        "uptime_seconds": int(ov.get("uptime_seconds")
                              or max(0, db.now_ts() - STARTED_TS)),
        "collector": {
            "samples": int(cst.get("samples") or 0),
            "interval_sec": int(cst.get("interval_sec") or config.SAMPLE_SEC),
            "errors": int(cst.get("errors") or 0),
            "last_ok_ts": int(cst.get("last_ok_ts") or 0),
        },
        "channels": chans,
        "soil_moisture_avg": ov.get("soil_moisture_avg"),
        "active_pumps": ov.get("active_pumps"),
        "auto_irrigation_enabled": ov.get("auto_irrigation_enabled"),
        "weather": {
            "city": w.get("city"),
            "enabled": bool(w.get("enabled")),
            "forecast": fc,
        },
        "stats": {
            "total_irrigations": s.get("total_irrigations"),
            "water_usage_liters": s.get("water_usage_liters"),
            "water_saving_percent": s.get("water_saving_percent"),
            "last_event": s.get("last_event"),
        },
    }


# ── 展示页专用只读端点（CONTRACT_V2 §2.2 / §2.3 / §2.4） ──
# 三个端点都在 config.PUBLIC_PATHS 里逐个列出，无 token 可访问。
# 共同约束：只返回传感器/规则的聚合量；参数非法一律 clamp 而不是 4xx，
# 上游挂了也要 200 + status=error，因为展示页宁可显示「未连接」也不能白屏。


@app.get("/api/showcase/history")
async def api_showcase_history(hours: str = "6", points: str = "72"):
    """历史回放数据：{status, hours, points, source, series[]}。

    series 里每项只带 channel / label / moisture / pump，标签取自
    config.CHANNEL_LABELS（公开常量），不含任何用户字段。
    """
    h = clamp_int(hours, 6, *SHOWCASE_HISTORY_HOURS)
    p = clamp_int(points, 72, *SHOWCASE_HISTORY_POINTS)
    try:
        out = build_history(h, p)
    except Exception:  # noqa: BLE001 —— 历史拉不到也不能让展示页 500
        log.exception("showcase/history 构建失败")
        return ok({"hours": h, "points": 0, "source": None, "series": [],
                   "note": "历史数据暂时不可用"})
    # 二次白名单：build_history 未来若加了字段，也不会顺着这个公开端点漏出去。
    series = []
    for item in (out.get("series") or []):
        ch = item.get("channel")
        series.append({
            "channel": ch,
            "label": item.get("label") or config.CHANNEL_LABELS.get(
                ch, f"{ch} 号地块"),
            "moisture": item.get("moisture") or [],
            "pump": item.get("pump") or [],
        })
    return ok({"hours": out.get("hours", h),
               "points": out.get("points", 0),
               "source": out.get("source"),
               "series": series})


@app.get("/api/showcase/prediction")
async def api_showcase_prediction(minutes: str = "30"):
    """未来预测（三通道）：{status, horizon_minutes, algorithm_info, predictions, accuracy_by_channel}。

    上游不可达时 status=error + predictions=[]，HTTP 仍是 200。
    """
    m = clamp_int(minutes, 30, *SHOWCASE_PREDICTION_MINUTES)
    try:
        raw = upstream.prediction(m)
    except Exception as exc:  # noqa: BLE001
        log.warning("showcase/prediction：上游拉取失败 %s", exc)
        # 注意：这里不能带 exc 原文，异常里可能含上游地址等内部信息。
        return ok({"horizon_minutes": m, "algorithm_info": None,
                   "predictions": [], "accuracy_by_channel": {},
                   "message": MSG_UPSTREAM, "status": "error"})
    try:
        norm = upstream.normalize_prediction(raw, ch=1)
    except Exception:  # noqa: BLE001
        log.exception("showcase/prediction 归一化失败")
        return ok({"horizon_minutes": m, "algorithm_info": None,
                   "predictions": [], "accuracy_by_channel": {},
                   "note": "上游预测格式未识别"})
    preds = norm.get("predictions") or []
    if not preds:
        # 上游活着但没给可解析的点：这不是故障，是「暂无预测」，仍算 ok。
        return ok({"horizon_minutes": norm.get("horizon_minutes") or m,
                   "algorithm_info": norm.get("algorithm_info"),
                   "predictions": [], "accuracy_by_channel": {},
                   "note": "上游预测格式未识别"})
    return ok({"horizon_minutes": norm.get("horizon_minutes") or m,
               "algorithm_info": norm.get("algorithm_info"),
               "predictions": preds,
               "accuracy_by_channel": norm.get("accuracy_by_channel") or {}})


# 告警白名单：这条列表是公开端点的全部输出字段。
# alerts._row_to_dict() 还会给 id / channel_label / resolved / ts_text 等，
# 那些够不上「公开」的必要性，也不再暴露出后端主键，所以逐字段重建。
SHOWCASE_ALERT_FIELDS = ("rule", "channel", "level", "message", "ts", "resolved_ts")


def showcase_alert_row(row: dict) -> dict:
    """把库里的告警行压成公开白名单结构（ts 用 ISO 文本，与契约示例一致）。"""
    resolved = row.get("resolved_ts")
    built = {
        "rule": row.get("rule"),
        "channel": row.get("channel"),
        "level": row.get("level"),
        "message": row.get("message"),
        "ts": alerts._fmt_ts(row.get("ts")) if row.get("ts") is not None else None,
        "resolved_ts": (alerts._fmt_ts(resolved)
                        if resolved is not None else None),
    }
    # 用白名单常量做最后一道过滤：将来若有人往 built 里加了字段，
    # 只要忘了同步 SHOWCASE_ALERT_FIELDS，这里就会把它挡在公网之外。
    return {k: built.get(k) for k in SHOWCASE_ALERT_FIELDS}


@app.get("/api/showcase/alerts")
async def api_showcase_alerts(limit: str = "20"):
    """公开告警列表：字段严格白名单，绝不含用户名 / token / IP / 审计明细。"""
    lim = clamp_int(limit, 20, *SHOWCASE_ALERTS_LIMIT)
    try:
        rows = alerts.open_alerts(lim)
    except Exception:  # noqa: BLE001
        log.exception("showcase/alerts 读取失败")
        return ok({"badge": 0, "count": 0, "alerts": []})
    items = [showcase_alert_row(r) for r in rows]
    return ok({"badge": alerts.badge_count(),
               "count": len(items),
               "alerts": items})


# ── 历史 / 降采样 ──


def downsample(points: list, limit: int) -> list:
    """按时间桶等间隔降采样，桶内取均值（剔除 None）。

    输入 [[ts, value], ...]（可含 None 值），输出同样结构，长度 <= limit。
    """
    pts = [p for p in points if p]
    if limit <= 0 or len(pts) <= limit:
        return [list(p) for p in pts]
    ts_list = [int(p[0]) for p in pts]
    t0, t1 = min(ts_list), max(ts_list)
    span = max(1, t1 - t0)
    buckets: dict = {}
    for p in pts:
        ts = int(p[0])
        idx = int((ts - t0) * limit / span)
        if idx >= limit:
            idx = limit - 1
        b = buckets.setdefault(idx, {"vals": [], "pumps": [], "ts": []})
        b["ts"].append(ts)
        val = p[1] if len(p) > 1 else None
        if val is None:
            continue
        try:
            b["vals"].append(float(val))
        except (TypeError, ValueError):
            continue
        if len(p) > 2 and p[2] is not None:
            b["pumps"].append(int(p[2]))
    out = []
    for idx in sorted(buckets):
        b = buckets[idx]
        mean_ts = int(sum(b["ts"]) / len(b["ts"]))
        if b["vals"]:
            out.append([mean_ts, round(sum(b["vals"]) / len(b["vals"]), 1)])
        elif b.get("pumps"):
            out.append([mean_ts, None])
    return out


def _samples_series(hours: int) -> dict[int, dict]:
    """本项目 samples 表 → {ch: {"moisture": [...], "pump": [...]}}。"""
    cutoff = db.now_ts() - int(hours) * 3600
    rows = db.query(
        "SELECT ts, ch1, ch2, ch3, pump1, pump2, pump3, avg FROM samples"
        " WHERE ts >= ? AND upstream_ok = 1 ORDER BY ts ASC", (cutoff,))
    out: dict[int, dict] = {}
    for r in rows:
        ts = int(r["ts"])
        for ch in range(1, config.NUM_CHANNELS + 1):
            slot = out.setdefault(ch, {"moisture": [], "pump": []})
            m = r[f"ch{ch}"]
            if m is not None:
                slot["moisture"].append([ts, round(float(m), 1)])
            slot["pump"].append([ts, 1 if int(r[f"pump{ch}"] or 0) else 0])
    return out


def _upstream_series(hours: int) -> dict[int, dict]:
    """上游 historical-data → {ch: {...}}。结构不认识就返回空 dict。"""
    try:
        raw = upstream.historical(hours)
    except Exception as exc:  # noqa: BLE001
        log.warning("history：上游拉取失败 %s", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[int, dict] = {}
    for ch in range(1, config.NUM_CHANNELS + 1):
        out[ch] = {"moisture": [], "pump": []}

    def add(ch: int, ts_raw, moist, pump) -> None:
        if ch not in out:
            return
        ts = upstream.parse_iso_ts(ts_raw)
        if ts is None:
            return
        if moist is not None:
            try:
                out[ch]["moisture"].append([int(ts), round(float(moist), 1)])
            except (TypeError, ValueError):
                pass
        if pump is not None:
            try:
                out[ch]["pump"].append([int(ts), 1 if int(float(pump)) else 0])
            except (TypeError, ValueError):
                pass

    # 形态 A0（上游 irrigation-server 的真实形态）：
    #   {"channels": [[{"timestamp","soil_moisture","pump_status"}, ...] × 3],
    #    "query_range_hours": 24, "total_points": 288}
    # 即 channels 是「按通道顺序排列的数组的数组」，下标 +1 就是通道号。
    chans = raw.get("channels")
    if isinstance(chans, list) and chans and isinstance(chans[0], list):
        for idx, series in enumerate(chans):
            ch = idx + 1
            if ch not in out:
                continue
            for p in series if isinstance(series, list) else []:
                if isinstance(p, dict):
                    add(ch,
                        p.get("timestamp") or p.get("time") or p.get("ts"),
                        p.get("soil_moisture", p.get("value")),
                        p.get("pump_status", p.get("pump")))
        return out

    # 形态 A：{"channels": [{"channel":1, "data":[...], "pump":[...]}, ...]}
    if isinstance(chans, list) and chans and isinstance(chans[0], dict):
        for item in chans:
            try:
                ch = int(item.get("channel"))
            except (TypeError, ValueError):
                continue
            series = (item.get("data") or item.get("series") or
                      item.get("values") or item.get("points") or [])
            pump_series = item.get("pump") or item.get("pump_status") or []
            pump_map = {}
            for pp in pump_series if isinstance(pump_series, list) else []:
                if isinstance(pp, (list, tuple)) and len(pp) >= 2:
                    pts = upstream.parse_iso_ts(pp[0])
                    if pts is not None:
                        pump_map[int(pts)] = pp[1]
            for p in series if isinstance(series, list) else []:
                if isinstance(p, dict):
                    add(ch, p.get("timestamp") or p.get("time") or p.get("ts"),
                        p.get("soil_moisture", p.get("value")), None)
                elif isinstance(p, (list, tuple)) and len(p) >= 2:
                    pts = upstream.parse_iso_ts(p[0])
                    add(ch, p[0], p[1], pump_map.get(int(pts)) if pts else None)
        return out

    # 形态 B：{"records"/"data"/"rows": [{"timestamp":..,"ch1_moisture":..}, ...]}
    rows = raw.get("records") or raw.get("data") or raw.get("rows") or raw.get("history")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        for r in rows:
            ts_raw = r.get("timestamp") or r.get("time") or r.get("ts")
            for ch in range(1, config.NUM_CHANNELS + 1):
                add(ch, ts_raw, r.get(f"ch{ch}_moisture"), r.get(f"ch{ch}_pump"))
        return out

    # 形态 C：{"timestamps":[...], "ch1_moisture":[...], "ch1_pump":[...]}
    tss = raw.get("timestamps") or raw.get("ts")
    if isinstance(tss, list):
        for ch in range(1, config.NUM_CHANNELS + 1):
            ms = raw.get(f"ch{ch}_moisture") or raw.get(f"ch{ch}")
            ps = raw.get(f"ch{ch}_pump")
            for i, ts_raw in enumerate(tss):
                add(ch, ts_raw,
                    ms[i] if isinstance(ms, list) and i < len(ms) else None,
                    ps[i] if isinstance(ps, list) and i < len(ps) else None)
        return out

    return out


def _merge_recent(local: dict[int, dict], remote: dict[int, dict]) -> dict[int, dict]:
    """以上游为长历史打底，把本地更新的点补在后面（本地 1 分钟粒度更新更细）。"""
    out: dict[int, dict] = {}
    for ch in range(1, config.NUM_CHANNELS + 1):
        r = remote.get(ch) or {"moisture": [], "pump": []}
        l = local.get(ch) or {"moisture": [], "pump": []}
        r_m = list(r["moisture"])
        r_p = list(r["pump"])
        last_ts = max([p[0] for p in r_m], default=0)
        # 只补上游还没有的「更新」的点，避免同一时刻两条重复
        extra_m = [p for p in l["moisture"] if p[0] > last_ts]
        extra_p = [p for p in l["pump"] if p[0] > last_ts]
        out[ch] = {"moisture": r_m + extra_m, "pump": r_p + extra_p}
    return out


def build_history(hours: int, points: int) -> dict:
    """返回 {hours, points, series, source}。"""
    hours = int(hours)
    points = int(points)
    local = _samples_series(hours)
    total = sum(len(v["moisture"]) for v in local.values())
    source = "samples"
    data = local

    # 本地 samples 表只有服务启动后才开始采集（每分钟一条）。判断要不要回落上游，
    # 不能只看「点够不够」，必须看「时间跨度够不够」——刚部署 1 小时时本地有 60 个点，
    # 看着不少，但选「近 24 小时」实际只覆盖最近 1 小时，图会严重误导。
    want_span = hours * 3600
    stamps = [p[0] for v in local.values() for p in v["moisture"]]
    local_span = (max(stamps) - min(stamps)) if len(stamps) >= 2 else 0
    need_remote = (local_span < want_span * 0.6) or total < config.NUM_CHANNELS * 2
    if need_remote:
        remote = _upstream_series(hours)
        remote_total = sum(len(v["moisture"]) for v in remote.values())
        if remote_total > 0:
            # 上游 5 分钟一条、可回溯很久；本地 1 分钟一条、更新。两者合并取并集：
            # 上游负责「长历史」，本地负责「最近的细粒度」。
            if remote_total > total:
                data = _merge_recent(local, remote)
                source = "upstream" if total < 2 else "upstream+samples"

    series = []
    max_points = 0
    for ch in range(1, config.NUM_CHANNELS + 1):
        slot = data.get(ch) or {"moisture": [], "pump": []}
        moist = downsample(slot["moisture"], points)
        pump_raw = downsample([p + [None] for p in slot["pump"]], points)
        pump = [[p[0], 1 if (len(p) > 1 and p[1]) else 0] for p in pump_raw]
        max_points = max(max_points, len(moist))
        series.append({
            "channel": ch,
            "label": config.CHANNEL_LABELS.get(ch, f"{ch} 号地块"),
            "moisture": moist,
            "pump": pump,
        })
    return {"hours": hours, "points": max_points, "series": series, "source": source}


@app.get("/api/history")
async def api_history(hours: str = "24", points: str = "600"):
    try:
        h = int(float(hours))
    except (TypeError, ValueError):
        return err("hours 参数无效")
    try:
        p = int(float(points))
    except (TypeError, ValueError):
        return err("points 参数无效")
    h = min(max(h, 1), 168 * 4)
    p = min(max(p, 10), 5000)
    try:
        out = build_history(h, p)
    except Exception:  # noqa: BLE001
        log.exception("history 构建失败")
        return ok({"hours": h, "points": 0, "series": [], "note": "历史数据暂时不可用"})
    return ok(out)


@app.get("/api/prediction")
async def api_prediction(minutes: str = "30"):
    try:
        m = int(float(minutes))
    except (TypeError, ValueError):
        return err("minutes 参数无效")
    m = min(max(m, 5), 24 * 60)
    try:
        raw = upstream.prediction(m)
    except Exception as exc:  # noqa: BLE001
        log.warning("prediction：上游拉取失败 %s", exc)
        return JSONResponse({"status": "error", "message": MSG_UPSTREAM,
                             "stale": True, "predictions": []}, status_code=200)
    try:
        norm = upstream.normalize_prediction(raw, ch=1)
    except Exception:  # noqa: BLE001
        log.exception("prediction 归一化失败")
        return ok({"horizon_minutes": m, "algorithm_info": None,
                   "predictions": [], "accuracy_by_channel": {},
                   "note": "上游预测格式未识别"})
    if not norm.get("predictions"):
        return ok({"horizon_minutes": norm.get("horizon_minutes") or m,
                   "algorithm_info": norm.get("algorithm_info"),
                   "predictions": [],
                   "accuracy_by_channel": norm.get("accuracy_by_channel") or {},
                   "note": "上游预测格式未识别"})
    return ok({
        "horizon_minutes": norm.get("horizon_minutes") or m,
        "algorithm_info": norm.get("algorithm_info"),
        "predictions": norm["predictions"],
        "accuracy_by_channel": norm.get("accuracy_by_channel") or {},
    })


# ── 事件（只读上游 SQLite） ──


def _open_irrdb():
    """以只读 URI 打开上游库；不存在时返回 None。"""
    path = str(config.IRRDB_PATH)
    if not os.path.exists(path):
        return None
    uri = "file:" + urllib.parse.quote(path) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=3.0)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/api/events")
async def api_events(days: str = "7"):
    try:
        d = int(float(days))
    except (TypeError, ValueError):
        return err("days 参数无效")
    d = min(max(d, 1), 3650)
    import datetime as _dt

    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%S")
    rows_out: list[dict] = []
    conn = None
    try:
        conn = _open_irrdb()
        if conn is None:
            return ok({"days": d, "events": [], "note": "上游历史库不可读"})
        rows = conn.execute(
            "SELECT id, channel, start_time, end_time, duration_sec"
            " FROM irrigation_events WHERE start_time >= ?"
            " ORDER BY start_time DESC LIMIT 2000", (cutoff,)).fetchall()
        for r in rows:
            rows_out.append({
                "id": int(r["id"]),
                "channel": int(r["channel"]) if r["channel"] is not None else None,
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "duration_sec": int(r["duration_sec"] or 0),
                "duration_minutes": round(float(r["duration_sec"] or 0) / 60.0, 1),
            })
    except Exception as exc:  # noqa: BLE001 —— 只读失败不 500
        log.warning("events：读取上游库失败 %s", exc)
        return ok({"days": d, "events": [],
                   "note": "上游历史库不可读，请稍后重试"})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return ok({"days": d, "events": rows_out, "count": len(rows_out)})


# ── 告警 ──


@app.get("/api/alerts")
async def api_alerts(limit: str = "50"):
    try:
        lim = int(float(limit))
    except (TypeError, ValueError):
        lim = 50
    items = alerts.open_alerts(lim)
    return ok({"alerts": items, "count": len(items),
               "badge": alerts.badge_count()})


# ── 控制 ──


@app.post("/api/control")
async def api_control(request: Request):
    denied = need_role(request, config.WRITE_ROLES)
    if denied is not None:
        return denied
    u = current_user(request)
    data = await body_of(request)
    ip = client_ip(request)

    action = str(data.get("action") or "").strip().lower()
    if action not in ("start", "stop"):
        auth.audit(u["username"], "灌溉控制", f"非法 action={action}", ip, ok=False)
        return err("action 只能是 start 或 stop")

    channel = data.get("channel")
    if isinstance(channel, str):
        ch_txt = channel.strip().lower()
        if ch_txt in ("all", "全部"):
            channel = "all"
        else:
            try:
                channel = int(ch_txt)
            except (TypeError, ValueError):
                auth.audit(u["username"], "灌溉控制", f"非法 channel={channel}", ip, ok=False)
                return err("channel 只能是 1、2、3 或 all")
    if channel == "all":
        pass
    else:
        try:
            channel = int(channel)
        except (TypeError, ValueError):
            auth.audit(u["username"], "灌溉控制", f"非法 channel={channel}", ip, ok=False)
            return err("channel 只能是 1、2、3 或 all")
        if channel not in range(1, config.NUM_CHANNELS + 1):
            auth.audit(u["username"], "灌溉控制", f"非法 channel={channel}", ip, ok=False)
            return err("channel 只能是 1、2、3 或 all")

    duration = data.get("duration_minutes")
    if duration in (None, ""):
        duration = None
    else:
        try:
            duration = int(float(duration))
        except (TypeError, ValueError):
            auth.audit(u["username"], "灌溉控制", f"非法 duration={duration}", ip, ok=False)
            return err("duration_minutes 必须是 1-120 的整数")
        if not (1 <= duration <= 120):
            auth.audit(u["username"], "灌溉控制", f"越界 duration={duration}", ip, ok=False)
            return err("duration_minutes 必须是 1-120 的整数")

    detail_base = f"action={action} channel={channel} duration_minutes={duration}"
    try:
        result = upstream.control(action, channel, duration)
    except Exception as exc:  # noqa: BLE001 —— 上游拒绝/不可达都不是 500
        reason = str(exc) or "未知原因"
        if "不可达" in reason:
            auth.audit(u["username"], "灌溉控制", f"{detail_base} 上游不可达：{reason}",
                       ip, ok=False)
            return JSONResponse({"status": "error", "message": MSG_UPSTREAM,
                                 "stale": True}, status_code=200)
        auth.audit(u["username"], "灌溉控制", f"{detail_base} 上游拒绝：{reason}",
                   ip, ok=False)
        return JSONResponse({"status": "error",
                             "message": f"上游拒绝了该操作：{reason}",
                             "stale": False}, status_code=200)

    auth.audit(u["username"], "灌溉控制",
               f"{detail_base} 上游返回={json.dumps(result, ensure_ascii=False)[:400]}",
               ip, ok=True)
    label = "全部地块" if channel == "all" else config.CHANNEL_LABELS.get(
        int(channel), f"{channel} 号地块")
    verb = "开始灌溉" if action == "start" else "停止灌溉"
    tail = f"，时长 {duration} 分钟" if (action == "start" and duration) else ""
    return ok({"message": f"{label}已{verb}{tail}",
               "action": action, "channel": channel,
               "duration_minutes": duration, "upstream": result})


# ── 审计 / 用户 ──


@app.get("/api/audit")
async def api_audit(request: Request, limit: str = "100"):
    denied = need_role(request, (config.ADMIN_ROLE,))
    if denied is not None:
        return denied
    try:
        lim = int(float(limit))
    except (TypeError, ValueError):
        lim = 100
    lim = min(max(lim, 1), 1000)
    rows = db.query(
        "SELECT id, ts, username, action, detail, ip, ok FROM audit"
        " ORDER BY id DESC LIMIT ?", (lim,))
    items = [{
        "id": int(r["id"]),
        "ts": int(r["ts"]),
        "time": alerts._fmt_ts(int(r["ts"])),
        "username": r["username"],
        "action": r["action"],
        "detail": r["detail"] or "",
        "ip": r["ip"] or "",
        "ok": bool(r["ok"]),
    } for r in rows]
    return ok({"audit": items, "count": len(items)})


@app.get("/api/users")
async def api_users(request: Request):
    denied = need_role(request, (config.ADMIN_ROLE,))
    if denied is not None:
        return denied
    items = auth.list_users()  # 已排除 pwd_hash / salt
    safe = [{"username": u["username"], "role": u["role"],
             "created": u["created"], "disabled": bool(u["disabled"]),
             "must_change": bool(u["must_change"])} for u in items]
    return ok({"users": safe, "count": len(safe)})


@app.post("/api/users")
async def api_users_create(request: Request):
    denied = need_role(request, (config.ADMIN_ROLE,))
    if denied is not None:
        return denied
    actor = current_user(request)
    data = await body_of(request)
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    role = str(data.get("role") or "viewer").strip() or "viewer"
    must_change = 1 if data.get("must_change") else 0
    out = auth.create_user(username, password, role, must_change=must_change)
    ok_flag = out.get("status") == "ok"
    auth.audit(actor["username"], "新建账号",
               f"username={username} role={role} -> {out.get('message')}",
               client_ip(request), ok=ok_flag)
    if not ok_flag:
        return err(out.get("message") or "创建账号失败")
    return ok({"message": out.get("message"), "username": username})


@app.patch("/api/users/{username}")
async def api_users_patch(username: str, request: Request):
    denied = need_role(request, (config.ADMIN_ROLE,))
    if denied is not None:
        return denied
    actor = current_user(request)
    data = await body_of(request)
    target = auth.get_user(username)
    if target is None:
        return err("账号不存在", 404)
    messages = []
    if "role" in data and data.get("role") is not None:
        out = auth.set_role(username, str(data.get("role")))
        if out.get("status") != "ok":
            auth.audit(actor["username"], "修改角色",
                       f"username={username} -> {out.get('message')}",
                       client_ip(request), ok=False)
            return err(out.get("message") or "修改角色失败")
        messages.append(out.get("message"))
    if "disabled" in data and data.get("disabled") is not None:
        disabled = bool(data.get("disabled"))
        if disabled and username == actor["username"]:
            return err("不能停用自己的账号")
        out = auth.set_disabled(username, disabled)
        if out.get("status") != "ok":
            return err(out.get("message") or "修改状态失败")
        messages.append(out.get("message"))
    if "password" in data and data.get("password"):
        pwd = str(data.get("password"))
        if len(pwd) < 8:
            return err("密码至少 8 位")
        out = auth.set_password(username, pwd, must_change=1)
        if out.get("status") != "ok":
            return err(out.get("message") or "重置密码失败")
        messages.append("密码已重置")
    if not messages:
        return err("没有需要修改的字段")
    auth.audit(actor["username"], "修改账号",
               f"username={username} fields={sorted(data.keys())}", client_ip(request))
    return ok({"message": "；".join(messages), "username": username})


# ── 导出 CSV ──


CSV_HEADER = ["时间", "1号地块含水量", "1号泵", "2号地块含水量", "2号泵",
              "3号地块含水量", "3号泵", "平均含水量"]


def build_csv(hours: int) -> bytes:
    """导出历史 CSV。

    数据来源与 /api/history 完全一致（本地 samples 与上游历史库二选一），
    否则刚部署时导出只有几行，而页面上图表却有 288 点，看起来像 bug。
    """
    import datetime as _dt

    hist = build_history(int(hours), 5000)
    series = hist.get("series") or []

    # 每通道：{ts: value} / {ts: pump}
    moist_map: dict = {}
    pump_map: dict = {}
    for s in series:
        ch = s.get("channel")
        for pair in s.get("moisture") or []:
            moist_map.setdefault(int(pair[0]), {})[ch] = pair[1]
        for pair in s.get("pump") or []:
            pump_map.setdefault(int(pair[0]), {})[ch] = pair[1]

    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADER)

    def f1(v):
        return "" if v is None else f"{float(v):.1f}"

    def p(v):
        return "" if v is None else ("1" if int(v or 0) else "0")

    for ts in sorted(moist_map):
        try:
            stamp = _dt.datetime.fromtimestamp(ts, db.CST).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            stamp = str(ts)
        m = moist_map.get(ts) or {}
        pm = pump_map.get(ts) or {}
        vals = [m.get(1), m.get(2), m.get(3)]
        known = [v for v in vals if v is not None]
        avg = (sum(float(v) for v in known) / len(known)) if known else None
        writer.writerow([stamp, f1(m.get(1)), p(pm.get(1)),
                         f1(m.get(2)), p(pm.get(2)),
                         f1(m.get(3)), p(pm.get(3)), f1(avg)])
    text = buf.getvalue()
    return b"\xef\xbb\xbf" + text.encode("utf-8")


@app.get("/api/export.csv")
async def api_export_csv(request: Request, hours: str = "24"):
    try:
        h = int(float(hours))
    except (TypeError, ValueError):
        return err("hours 参数无效")
    h = min(max(h, 1), 168 * 4)
    payload = build_csv(h)
    rows_count = max(0, payload.count(b"\n") - 1)
    auth.audit(current_user(request)["username"], "导出 CSV",
               f"hours={h} rows={rows_count}", client_ip(request))
    stamp = db.now_iso().replace(":", "").replace("-", "")
    headers = {
        "Content-Disposition": 'attachment; filename="farm_history.csv"',
        "X-Farm-Export-Time": stamp,
        "Cache-Control": "no-store",
    }
    return Response(content=payload, media_type="text/csv; charset=utf-8",
                    headers=headers)


# ── 静态文件（防目录穿越） ──


@app.get("/files/{name}")
async def api_file(name: str, request: Request):
    target, problem = safe_upload_path(name)
    if problem is not None:
        return problem
    if not target.is_file():
        return err("文件不存在", 404)
    return FileResponse(str(target), filename=name)


def safe_upload_path(name: str):
    """把 /files/{name} 解析成 UPLOAD_DIR 内的真实路径。

    返回 (Path, None) 或 (None, 错误响应)。防目录穿越：
    拒绝空名、含 / \\ .. 的名字、以 . 开头的隐藏名、含 NUL 的名字，
    并把最终路径 resolve 后校验仍在 UPLOAD_DIR 之内。
    """
    if (not name or "/" in name or "\\" in name or ".." in name
            or name.startswith(".") or "\x00" in name):
        return None, err("文件名不合法", 400)
    base = Path(config.UPLOAD_DIR).resolve()
    target = (base / name).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None, err("文件名不合法", 400)
    return target, None
