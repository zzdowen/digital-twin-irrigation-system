"""灌溉计时器：「已完成事件总时长」+「本次已证实运行的时长」。

为什么不能简单用 now - 起泵时刻
------------------------------
那样会在断网时继续往前跑，把一个我们**并不知道**的区间算进累计值。
本模块改用「累计已证实时长」：

  - 每次拿到**新鲜**读数且泵在转，就把「距上次新鲜观测的间隔」加进
    ``credited``，然后把观测时刻前移；
  - 间隔若超过 ``TIMER_GAP_TRUST_SEC``，说明中间有一段时间没观测到，
    这段时间泵到底转没转无法证实 → **整段不补**（宁可少算，不多算）；
  - 读数不新鲜时（上游 ``last_update`` 停了 / 上游不可达）什么都不做，
    ``credited`` 自然冻结，计时停表。

于是 ``credited`` 只增不减，且每一秒都能对应到一次真实观测。

与上游口径一致
--------------
上游 ``/api/irrigation-stats`` 的 ``total_duration_sec`` 是**已完成**
灌溉事件（``irrigation_events`` 表）之和，正在浇的这一段还没落库：

    累计时长 = 上游 total_duration_sec + 本模块 running_sec

``running_sec`` 按通道累加（两路同时浇就是两者之和），与上游按事件
累加的口径一致。观察到泵停时立刻丢弃本通道的 ``credited`` —— 因为上游
随即会把这段写进事件表，再留着就重复计了。

重启保号
--------
``credited`` 与起泵时刻都持久化在 ``meta`` 表；若服务停机超过
``_RESTART_TRUST_SEC`` 秒，停机期间无法证实，``credited`` 直接作废。
"""
import logging
import threading
import time

from . import config, db

log = logging.getLogger("farm.timer")

_CRED = "irr_credited_"      # 已证实时长（秒）
_SINCE = "irr_since_"        # 上次新鲜观测时刻（epoch 秒）
_START = "irr_start_"        # 本次起泵时刻（epoch 秒），仅用于展示

# 服务重启后，停机不足这个秒数才认为 credited 仍可信
_RESTART_TRUST_SEC = 300

_lock = threading.Lock()
# ch -> {"credited": float, "since": float, "start": float}
_running: dict[int, dict] = {}
_loaded = False
_last_obs_ts: float | None = None
_last_data_age = None


# ── meta 持久化 ──


def _get(key: str):
    try:
        row = db.query_one("SELECT v FROM meta WHERE k=?", (key,))
    except Exception:  # noqa: BLE001
        log.exception("读取 meta 失败 k=%s", key)
        return None
    return row["v"] if row else None


def _put(key: str, value) -> None:
    try:
        db.write("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (key, str(value)))
    except Exception:  # noqa: BLE001 —— 持久化失败不影响计时
        log.exception("写入 meta 失败 k=%s", key)


def _del(key: str) -> None:
    try:
        db.write("DELETE FROM meta WHERE k=?", (key,))
    except Exception:  # noqa: BLE001
        log.exception("删除 meta 失败 k=%s", key)


def _save(ch: int, it: dict) -> None:
    _put(_CRED + str(ch), f"{it['credited']:.3f}")
    _put(_SINCE + str(ch), f"{it['since']:.3f}")
    _put(_START + str(ch), f"{it['start']:.3f}")


def _drop(ch: int) -> None:
    for p in (_CRED, _SINCE, _START):
        _del(p + str(ch))


def _load_locked(now: float) -> None:
    """首次调用时把持久化状态读回内存（调用方已持锁）。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    for ch in range(1, config.NUM_CHANNELS + 1):
        since_s = _get(_SINCE + str(ch))
        if since_s in (None, ""):
            continue
        try:
            since = float(since_s)
        except (TypeError, ValueError):
            _drop(ch)
            continue
        if now - since > _RESTART_TRUST_SEC:
            log.info("通道 %s 的计时状态已过期（%.0f 秒未观测），作废", ch, now - since)
            _drop(ch)
            continue
        try:
            cred = float(_get(_CRED + str(ch)) or 0.0)
        except (TypeError, ValueError):
            cred = 0.0
        try:
            start = float(_get(_START + str(ch)) or since)
        except (TypeError, ValueError):
            start = since
        # rebase=True：重启后第一次观测只重设基准，**不补**「停机期间」的时长
        # ——那段没有任何观测，无法证实泵在转。
        _running[ch] = {"credited": max(0.0, cred), "since": since,
                        "start": start, "rebase": True}


# ── 判断可信度 ──


def is_fresh(upstream_ok: bool, data_age_sec) -> bool:
    """读数是否新鲜到足以认账。"""
    if not upstream_ok or data_age_sec is None:
        return False
    try:
        return float(data_age_sec) <= config.TIMER_FRESH_SEC
    except (TypeError, ValueError):
        return False


def reason_of(upstream_ok: bool, data_age_sec) -> str | None:
    """计时为什么不可信（可信时返回 None）。"""
    if not upstream_ok:
        return "灌溉服务暂时不可达，计时已暂停"
    if data_age_sec is None:
        return "暂无有效读数，计时已暂停"
    try:
        age = int(float(data_age_sec))
    except (TypeError, ValueError):
        return "暂无有效读数，计时已暂停"
    if age <= config.TIMER_FRESH_SEC:
        return None
    return f"读数已 {age} 秒未更新，计时已暂停"


# ── 观测 ──


def observe(channels, upstream_ok: bool, data_age_sec,
            now: float | None = None) -> bool:
    """记录一次观测，返回本次读数是否新鲜。

    不新鲜：只冻结，既不清零也不补算，并给在跑的通道打上 ``stalled`` 标记。
    恢复新鲜后的第一次观测看到 ``stalled``，只重设基准、**不补**中断那段
    —— 那段时间没有任何观测，泵到底转没转无法证实。
    """
    global _last_obs_ts, _last_data_age
    if now is None:
        now = time.time()
    fresh = is_fresh(upstream_ok, data_age_sec)
    if not fresh:
        with _lock:
            # 标记「中断中」：恢复后不许把这段补回去
            for it in _running.values():
                it["stalled"] = True
        _last_data_age = data_age_sec
        return False

    with _lock:
        _load_locked(now)
        for c in channels or []:
            try:
                ch = int(c.get("channel") or 0)
            except (TypeError, ValueError):
                continue
            if ch < 1 or ch > config.NUM_CHANNELS:
                continue

            if bool(c.get("pump_status")):
                it = _running.get(ch)
                if it is None:
                    _running[ch] = {"credited": 0.0, "since": now, "start": now}
                    _save(ch, _running[ch])
                    continue
                if it.get("rebase") or it.get("stalled"):
                    # 重启后首见 / 中断恢复首见：确认在转，但那段不补
                    it.pop("rebase", None)
                    it.pop("stalled", None)
                    it["since"] = now
                    _save(ch, it)
                    continue
                gap = now - it["since"]
                if gap <= 0:
                    continue
                if gap <= config.TIMER_GAP_TRUST_SEC:
                    it["credited"] = min(float(config.TIMER_MAX_RUN_SEC),
                                         it["credited"] + gap)
                else:
                    # 中间那段没观测到，无法证实 → 不补
                    log.info("通道 %s 观测中断 %.0f 秒，该区间不计入", ch, gap)
                it["since"] = now
                _save(ch, it)
            elif _running.pop(ch, None) is not None:
                # 上游随即会把这段写进事件表，这里必须丢弃以免重复计
                _drop(ch)
        _last_obs_ts = now
        _last_data_age = data_age_sec
    return True


# ── 快照 ──


def snapshot() -> dict:
    """当前计时快照（纯读，不改状态）。"""
    with _lock:
        chans = []
        running = 0.0
        capped = False
        for ch in sorted(_running):
            it = _running[ch]
            v = max(0.0, float(it["credited"]))
            if v >= float(config.TIMER_MAX_RUN_SEC):
                capped = True
            chans.append({"channel": ch, "run_sec": int(v)})
            running += v
    return {
        "running_channels": chans,
        "running_sec": int(running),
        "counting": bool(chans),
        "capped": capped,
    }


def describe(channels, upstream_ok: bool, data_age_sec,
             completed_sec, now: float | None = None) -> dict:
    """observe + snapshot，并给出与上游同口径的累计秒数。"""
    fresh = observe(channels, upstream_ok, data_age_sec, now=now)
    snap = snapshot()
    snap["fresh"] = fresh
    snap["reason"] = reason_of(upstream_ok, data_age_sec)
    snap["as_of"] = db.now_iso()

    done_known = completed_sec not in (None, "")
    done = 0
    if done_known:
        try:
            done = max(0, int(float(completed_sec)))
        except (TypeError, ValueError):
            done_known = False
    if done_known:
        snap["completed_known"] = True
        snap["completed_sec"] = done
        snap["total_sec"] = done + snap["running_sec"]
    else:
        # 连历史记录都拿不到，那「累计」本身不可信
        snap["completed_known"] = False
        snap["total_sec"] = snap["running_sec"]
        if not snap["reason"]:
            snap["reason"] = "未取到历史灌溉记录，仅显示本次计时"
    return snap


def reset_for_tests(clear_meta: bool = True) -> None:
    """清空内存状态（测试用）。

    ``clear_meta=True`` 时连 meta 表里的持久化状态一起清掉——不这么做的话，
    下一个用例 ``_load_locked`` 会把上一个用例的计时读回来，测出来的
    数字全是串的（这个坑真的踩过）。
    """
    global _loaded, _last_obs_ts, _last_data_age
    with _lock:
        if clear_meta:
            for ch in range(1, config.NUM_CHANNELS + 1):
                for p in (_CRED, _SINCE, _START):
                    try:
                        db.write("DELETE FROM meta WHERE k=?", (p + str(ch),))
                    except Exception:  # noqa: BLE001
                        log.exception("清理 meta 失败 k=%s", p + str(ch))
        _running.clear()
        _loaded = False
        _last_obs_ts = None
        _last_data_age = None
