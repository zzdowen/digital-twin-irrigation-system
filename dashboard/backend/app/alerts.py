"""告警规则引擎（本项目自己算，结果写 alerts 表）。

CONTRACT §5.4 五条规则：
  dry             任一通道含水量 < 30                      warning
  wet             任一通道含水量 > 95 持续 >= 3 次采样       info
  long_irrigation 同一通道连续开泵 >= 60 分钟                warning
  no_data         data_age_sec > 600                       critical
  upstream_down   连续 3 次采样上游不可达                    critical

约定：
  - 同一 (rule, channel) 未恢复时不重复插入，只更新 ts / level / message；
  - 条件消失时自动写 resolved_ts；
  - 所有函数都不向上抛异常（采集线程被它拖死会连累主进程）。
"""
import logging

from . import config, db

log = logging.getLogger("farm.alerts")

RULES = ("dry", "wet", "long_irrigation", "no_data", "upstream_down")

LEVELS = {
    "dry": "warning",
    "wet": "info",
    "long_irrigation": "warning",
    "no_data": "critical",
    "upstream_down": "critical",
}

# 采集线程与 API 共享的运行态（湿度连续次数、开泵起点、上游连续失败次数）
STATE = {"wet": {}, "pump_since": {}, "down_streak": 0, "upstream_ok": None}


def new_state() -> dict:
    """一份干净的评估状态（测试用）。"""
    return {"wet": {}, "pump_since": {}, "down_streak": 0, "upstream_ok": None}


def reset_state() -> dict:
    STATE["wet"] = {}
    STATE["pump_since"] = {}
    STATE["down_streak"] = 0
    STATE["upstream_ok"] = None
    return STATE


# ── 小工具 ──


def _num(value):
    """尽量转成 float；转不了返回 None（上游字段可能是 null 或字符串）。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def channel_label(channel) -> str:
    """1 -> '1 号地块'；None -> '全部地块'。"""
    if channel is None:
        return "全部地块"
    try:
        n = int(channel)
    except (TypeError, ValueError):
        return "全部地块"
    return config.CHANNEL_LABELS.get(n, f"{n} 号地块")


# ── alerts 表读写 ──


def open_alert_row(rule: str, channel):
    """查该 (rule, channel) 当前未恢复的那一条。"""
    if channel is None:
        return db.query_one(
            "SELECT * FROM alerts WHERE rule = ? AND channel IS NULL"
            " AND resolved_ts IS NULL ORDER BY id DESC LIMIT 1", (rule,))
    return db.query_one(
        "SELECT * FROM alerts WHERE rule = ? AND channel = ?"
        " AND resolved_ts IS NULL ORDER BY id DESC LIMIT 1", (rule, channel))


def raise_alert(rule: str, channel, message: str, ts: int | None = None) -> dict:
    """触发告警：未有未恢复记录则插入，否则只更新 ts / level / message。"""
    ts = int(ts if ts is not None else db.now_ts())
    level = LEVELS.get(rule, "warning")
    try:
        row = open_alert_row(rule, channel)
        if row is None:
            db.write(
                "INSERT INTO alerts(ts, rule, level, channel, message, resolved_ts)"
                " VALUES(?,?,?,?,?,NULL)",
                (ts, rule, level, channel, message))
        else:
            db.write("UPDATE alerts SET ts = ?, level = ?, message = ? WHERE id = ?",
                     (ts, level, message, int(row["id"])))
    except Exception:  # noqa: BLE001 —— 告警写失败不能影响采集
        log.exception("写告警失败：rule=%s channel=%s", rule, channel)
    return {"ts": ts, "rule": rule, "level": level, "channel": channel,
            "message": message, "resolved": False}


def resolve_if_ok(rule: str, channel, ts: int | None = None) -> int:
    """条件消失：把未恢复的记录标记为已恢复。返回受影响行数。"""
    ts = int(ts if ts is not None else db.now_ts())
    try:
        conn = db.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            if channel is None:
                cur = conn.execute(
                    "UPDATE alerts SET resolved_ts = ? WHERE rule = ?"
                    " AND channel IS NULL AND resolved_ts IS NULL", (ts, rule))
            else:
                cur = conn.execute(
                    "UPDATE alerts SET resolved_ts = ? WHERE rule = ?"
                    " AND channel = ? AND resolved_ts IS NULL", (ts, rule, channel))
            conn.execute("COMMIT")
            return int(cur.rowcount or 0)
        except Exception:
            conn.execute("ROLLBACK")
            raise
    except Exception:  # noqa: BLE001
        log.exception("恢复告警失败：rule=%s channel=%s", rule, channel)
        return 0


# 语义别名（老调用方用 resolve()）
resolve = resolve_if_ok


def _row_to_dict(row) -> dict:
    ts = int(row["ts"] or 0)
    resolved_ts = row["resolved_ts"]
    return {
        "id": int(row["id"]),
        "ts": ts,
        "ts_text": _fmt_ts(ts),
        "rule": row["rule"],
        "level": row["level"],
        "channel": row["channel"],
        "channel_label": channel_label(row["channel"]),
        "message": row["message"],
        "resolved": resolved_ts is not None,
        "resolved_ts": None if resolved_ts is None else int(resolved_ts),
        "resolved_text": None if resolved_ts is None else _fmt_ts(int(resolved_ts)),
    }


def _fmt_ts(ts: int) -> str:
    from datetime import datetime

    try:
        return datetime.fromtimestamp(int(ts), db.CST).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:  # noqa: BLE001
        return ""


def open_alerts(limit: int = 50) -> list[dict]:
    """告警列表：未恢复的置顶，其次按时间倒序。"""
    try:
        limit = max(1, min(int(limit or 50), 500))
    except (TypeError, ValueError):
        limit = 50
    rows = db.query(
        "SELECT * FROM alerts ORDER BY (resolved_ts IS NULL) DESC, ts DESC, id DESC"
        " LIMIT ?", (limit,))
    return [_row_to_dict(r) for r in rows]


def badge_count() -> int:
    """未恢复告警数（前端角标）。"""
    try:
        row = db.query_one("SELECT COUNT(*) AS n FROM alerts WHERE resolved_ts IS NULL")
        return int(row["n"]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


# ── 评估 ──


def evaluate(sample: dict, state: dict | None = None) -> list[dict]:
    """评估一次采样，写入/恢复告警，返回本次**仍然成立**的告警列表。

    sample 结构::

        {"ts": int, "ch1": 22.4, "ch2": 80.0, "ch3": None,
         "pump1": True, "pump2": False, "pump3": True,
         "upstream_ok": True, "data_age_sec": 12,
         "irrigation_duration": {1: 0, 2: 0, 3: 0}}

    state 省略时用模块级共享 STATE（采集线程用默认即可）。
    """
    st = STATE if state is None else state
    for key, default in (("wet", {}), ("pump_since", {}), ("down_streak", 0)):
        if key not in st:
            st[key] = default
    ts = int(sample.get("ts") or db.now_ts())
    triggered: list[dict] = []

    upstream_ok = sample.get("upstream_ok")
    upstream_ok = True if upstream_ok is None else bool(upstream_ok)
    st["upstream_ok"] = upstream_ok

    # ── upstream_down：连续 N 次不可达 ──
    if not upstream_ok:
        st["down_streak"] = int(st.get("down_streak") or 0) + 1
        if st["down_streak"] >= config.UPSTREAM_DOWN_STREAK:
            triggered.append(raise_alert(
                "upstream_down", None,
                f"连续 {st['down_streak']} 次采集失败，灌溉服务不可达，请检查上游服务是否运行。",
                ts))
        return triggered
    st["down_streak"] = 0
    resolve_if_ok("upstream_down", None, ts)

    # ── no_data：数据超过 10 分钟没更新 ──
    age = _num(sample.get("data_age_sec"))
    if age is not None:
        if age > config.STALE_AFTER_SEC:
            triggered.append(raise_alert(
                "no_data", None,
                f"数据已 {int(age)} 秒未更新（超过 {config.STALE_AFTER_SEC} 秒），请检查采集链路。",
                ts))
        else:
            resolve_if_ok("no_data", None, ts)

    # ── 逐通道规则 ──
    wet = st["wet"]
    pump_since = st["pump_since"]
    irr = sample.get("irrigation_duration") or {}
    if not isinstance(irr, dict):
        irr = {}

    for ch in range(1, config.NUM_CHANNELS + 1):
        moist = _num(sample.get(f"ch{ch}"))
        pump = bool(sample.get(f"pump{ch}"))

        # dry
        if moist is not None and moist < config.DRY_PCT:
            triggered.append(raise_alert(
                "dry", ch,
                f"{channel_label(ch)}含水量 {moist:.1f}%，低于 {config.DRY_PCT:.0f}%，建议灌溉。",
                ts))
        else:
            resolve_if_ok("dry", ch, ts)

        # wet（连续 >= 3 次）
        if moist is not None and moist > config.WET_PCT:
            wet[ch] = int(wet.get(ch) or 0) + 1
            if wet[ch] >= config.WET_STREAK:
                triggered.append(raise_alert(
                    "wet", ch,
                    f"{channel_label(ch)}含水量 {moist:.1f}%，高于 {config.WET_PCT:.0f}%，"
                    f"已持续 {wet[ch]} 次采样。",
                    ts))
        else:
            wet[ch] = 0
            resolve_if_ok("wet", ch, ts)

        # long_irrigation（连续开泵 >= 60 分钟）
        if pump:
            if pump_since.get(ch) is None:
                pump_since[ch] = ts
            cont = int(ts) - int(pump_since.get(ch) or ts)
            upstream_dur = _num(irr.get(ch) if ch in irr else irr.get(str(ch)))
            if upstream_dur is not None and upstream_dur > cont:
                cont = int(upstream_dur)
            if cont >= config.LONG_IRRIGATION_SEC:
                triggered.append(raise_alert(
                    "long_irrigation", ch,
                    f"{channel_label(ch)}已连续灌溉 {cont // 60} 分钟，"
                    f"超过 {config.LONG_IRRIGATION_SEC // 60} 分钟，请确认是否需要停泵。",
                    ts))
            else:
                resolve_if_ok("long_irrigation", ch, ts)
        else:
            pump_since[ch] = None
            resolve_if_ok("long_irrigation", ch, ts)

    return triggered
