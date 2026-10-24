"""后台采集线程：定时拉上游 → 写 samples 表 → 评估告警。

设计要点（CONTRACT §8）：
  - FARM_SAMPLE_SEC 默认 60 秒一次；
  - 采集失败**绝不让主进程退出**，只记日志 + 计数 + 写告警；
  - 线程 daemon=True，名字 farm-collector，start()/stop() 幂等。
"""
import logging
import threading
import time

from . import alerts, config, db, upstream

log = logging.getLogger("farm.collector")

_thread: threading.Thread | None = None
_stop = threading.Event()
_lock = threading.Lock()

# 采集统计（/health 与 overview 会读）
STATS = {"samples": 0, "errors": 0, "last_ok_ts": 0, "last_error": None,
         "started_ts": 0}


def _sample_from_realtime(rt: dict, ts: int) -> dict:
    """把上游 realtime-data 归一成内部采样结构。"""
    chans = upstream.normalize_channels(rt)
    by_ch = {c["channel"]: c for c in chans}
    sample: dict = {"ts": ts, "upstream_ok": True,
                    "source": rt.get("data_source") or "unknown",
                    "irrigation_duration": {}}
    moist_values = []
    for ch in range(1, config.NUM_CHANNELS + 1):
        c = by_ch.get(ch) or {}
        moist = c.get("soil_moisture")
        sample[f"ch{ch}"] = moist
        sample[f"pump{ch}"] = bool(c.get("pump_status"))
        sample["irrigation_duration"][ch] = int(c.get("irrigation_duration") or 0)
        if moist is not None:
            moist_values.append(float(moist))
    sample["avg"] = round(sum(moist_values) / len(moist_values), 1) if moist_values else None

    # data_age_sec 由 last_update 推
    last_ts = upstream.parse_iso_ts(rt.get("last_update"))
    sample["last_update_ts"] = last_ts
    sample["data_age_sec"] = None if last_ts is None else max(0, ts - last_ts)
    raw_avg = rt.get("soil_moisture_avg")
    if raw_avg is not None:
        try:
            sample["avg"] = round(float(raw_avg), 1)
        except (TypeError, ValueError):
            pass
    return sample


def _write_sample(sample: dict) -> None:
    """写 samples 表（ts 主键，重复采样直接覆盖）。"""
    db.write(
        "INSERT OR REPLACE INTO samples(ts, ch1, ch2, ch3, pump1, pump2, pump3,"
        " avg, source, upstream_ok) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (int(sample["ts"]), sample.get("ch1"), sample.get("ch2"), sample.get("ch3"),
         1 if sample.get("pump1") else 0, 1 if sample.get("pump2") else 0,
         1 if sample.get("pump3") else 0, sample.get("avg"),
         sample.get("source"), 1 if sample.get("upstream_ok") else 0))


def collect_once() -> dict:
    """执行一次采集。返回采样 dict（失败时 upstream_ok=False）。永不抛异常。"""
    ts = db.now_ts()
    try:
        rt = upstream.realtime()
    except Exception as exc:  # noqa: BLE001 —— 上游挂了也必须活着
        STATS["errors"] = int(STATS["errors"]) + 1
        STATS["last_error"] = str(exc)[:200]
        log.warning("采集失败：%s", exc)
        sample = {"ts": ts, "upstream_ok": False, "source": "unavailable",
                  "ch1": None, "ch2": None, "ch3": None,
                  "pump1": False, "pump2": False, "pump3": False,
                  "avg": None, "data_age_sec": None,
                  "irrigation_duration": {}, "last_update_ts": None}
        try:
            alerts.evaluate(sample)
        except Exception:  # noqa: BLE001
            log.exception("告警评估失败（采集失败分支）")
        return sample

    try:
        sample = _sample_from_realtime(rt, ts)
    except Exception as exc:  # noqa: BLE001
        STATS["errors"] = int(STATS["errors"]) + 1
        STATS["last_error"] = f"上游响应无法解析：{exc}"[:200]
        log.warning("上游响应无法解析：%s", exc)
        sample = {"ts": ts, "upstream_ok": False, "source": "unavailable",
                  "ch1": None, "ch2": None, "ch3": None,
                  "pump1": False, "pump2": False, "pump3": False,
                  "avg": None, "data_age_sec": None,
                  "irrigation_duration": {}, "last_update_ts": None}
        try:
            alerts.evaluate(sample)
        except Exception:  # noqa: BLE001
            log.exception("告警评估失败（解析失败分支）")
        return sample

    try:
        _write_sample(sample)
        STATS["samples"] = int(STATS["samples"]) + 1
        STATS["last_ok_ts"] = ts
        STATS["last_error"] = None
    except Exception:  # noqa: BLE001 —— 写库失败不能退出
        STATS["errors"] = int(STATS["errors"]) + 1
        log.exception("写 samples 失败")

    try:
        alerts.evaluate(sample)
    except Exception:  # noqa: BLE001
        log.exception("告警评估失败")

    return sample


def _loop() -> None:
    log.info("采集线程启动，间隔 %s 秒", config.SAMPLE_SEC)
    # 启动后先立刻采一次，别让看板空等一分钟
    while not _stop.is_set():
        try:
            collect_once()
        except Exception:  # noqa: BLE001 —— 双保险
            log.exception("采集循环出现未捕获异常")
        if _stop.wait(max(5, config.SAMPLE_SEC)):
            break
    log.info("采集线程已退出")


def start() -> bool:
    """启动采集线程。幂等：已在跑返回 False。"""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()
        STATS["started_ts"] = db.now_ts()
        _thread = threading.Thread(target=_loop, name="farm-collector", daemon=True)
        _thread.start()
        return True


def stop(timeout: float = 5.0) -> bool:
    """停止采集线程。幂等：没在跑返回 False。"""
    global _thread
    with _lock:
        th = _thread
        if th is None or not th.is_alive():
            _thread = None
            return False
        _stop.set()
        th.join(timeout)
        _thread = None
        return True


def running() -> bool:
    th = _thread
    return bool(th is not None and th.is_alive())


def status() -> dict:
    return {
        "running": running(),
        "interval_sec": config.SAMPLE_SEC,
        "samples": int(STATS["samples"]),
        "errors": int(STATS["errors"]),
        "last_ok_ts": int(STATS["last_ok_ts"] or 0),
        "last_error": STATS["last_error"],
        "down_streak": int(alerts.STATE.get("down_streak") or 0),
    }
