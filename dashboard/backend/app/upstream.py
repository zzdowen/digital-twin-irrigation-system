"""上游灌溉服务客户端（只读为主，control 是唯一写操作）。

绝不修改上游服务、绝不写上游数据库。所有异常都转成中性错误，不向上抛。
"""
import datetime as dt
import logging
import urllib.error
import urllib.request
import json

from . import config

log = logging.getLogger("farm.upstream")


class UpstreamError(Exception):
    pass


def _get(path: str, timeout: float | None = None) -> dict:
    url = config.UPSTREAM + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.UPSTREAM_TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as exc:
        raise UpstreamError(f"上游返回 {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError(f"上游不可达：{exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("上游返回的不是 JSON") from exc


def _post(path: str, body: dict) -> dict:
    url = config.UPSTREAM + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=config.UPSTREAM_TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            detail = {}
        msg = detail.get("error") or detail.get("message") or f"上游返回 {exc.code}"
        raise UpstreamError(msg) from exc
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError(f"上游不可达：{exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("上游返回的不是 JSON") from exc


def realtime() -> dict:
    return _get("/api/realtime-data")


def status() -> dict:
    return _get("/api/system-status")


def weather() -> dict:
    return _get("/api/weather")


def stats() -> dict:
    return _get("/api/irrigation-stats")


def historical(hours: int = 24) -> dict:
    return _get(f"/api/historical-data?hours={int(hours)}")


def prediction(minutes: int = 30) -> dict:
    return _get(f"/api/prediction?minutes={int(minutes)}")


def control(action: str, channel=None, duration_minutes=None) -> dict:
    body: dict = {"action": action}
    if channel not in (None, ""):
        body["channel"] = channel
    if duration_minutes not in (None, ""):
        body["duration_minutes"] = duration_minutes
    return _post("/api/control-pump", body)


# ── 上游响应归一化 ──


def parse_iso_ts(s) -> int | None:
    """把上游的 ISO 时间串转 Unix 秒。带不带时区都吃。"""
    if not s:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    txt = str(s).strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(txt)
    except Exception:  # noqa: BLE001
        return None
    if d.tzinfo is None:
        # 上游的 last_update 是本地（北京）时间
        d = d.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return int(d.timestamp())


def normalize_channels(rt: dict) -> list[dict]:
    """把 realtime-data 的 channels 归一成统一结构。"""
    out = []
    for ch in rt.get("channels") or []:
        try:
            num = int(ch.get("channel"))
        except (TypeError, ValueError):
            continue
        try:
            moist = float(ch.get("soil_moisture"))
        except (TypeError, ValueError):
            moist = None
        out.append({
            "channel": num,
            "soil_moisture": None if moist is None else round(moist, 1),
            "pump_status": bool(ch.get("pump_status")),
            "irrigation_duration": int(ch.get("irrigation_duration") or 0),
        })
    out.sort(key=lambda c: c["channel"])
    return out


def channel_status(moisture, stale: bool) -> str:
    if stale:
        return "offline"
    if moisture is None:
        return "offline"
    if moisture < config.DRY_PCT:
        return "dry"
    if moisture > config.WET_PCT:
        return "wet"
    return "normal"


def normalize_forecast(w: dict) -> dict:
    fc = []
    for item in (w.get("forecast") or []):
        fc.append({
            "date": item.get("date"),
            "weather": item.get("weather"),
            "temperature_c": item.get("temperature_c"),
            "rain_probability_percent": item.get("rain_probability_percent"),
        })
    return {
        "city": w.get("city"),
        "enabled": bool(w.get("enabled")),
        "updated_at": w.get("updated_at"),
        "last_error": w.get("last_error"),
        "forecast": fc,
    }


def normalize_prediction(p: dict, ch: int = 1) -> dict:
    """上游 predictions 可能是 list[dict] / dict / list[[ts,v]] / list[list[dict]]，
    全部归一成 [[ts,v]]。"""
    raw = p.get("predictions")

    # 真实形态（上游 irrigation-server）：predictions 是「每个通道一个数组」的数组，
    #   predictions = [[{confidence, future_time, minutes_ahead, predicted_soil_moisture}, ...] × 3]
    # 取第 ch 个通道（1 基）。若外层每项是 dict 则不是这种形态，跳过。
    if (isinstance(raw, list) and raw and isinstance(raw[0], list)
            and raw[0] and isinstance(raw[0][0], dict)):
        idx = ch - 1 if 0 <= ch - 1 < len(raw) else 0
        raw = raw[idx]

    series: list[list] = []
    if isinstance(raw, dict):
        # {"1": [...]} 或 {"timestamps":[...], "values":[...]}
        pick = raw.get(str(ch)) if str(ch) in raw else raw.get(ch)
        if pick is None:
            for v in raw.values():
                if isinstance(v, list):
                    pick = v
                    break
        raw = pick if pick is not None else []
    if isinstance(raw, list):
        for item in raw:
            ts = val = None
            if isinstance(item, dict):
                ts = parse_iso_ts(
                    item.get("future_time") or item.get("timestamp")
                    or item.get("time") or item.get("ts"))
                val = item.get("predicted_soil_moisture")
                if val is None:
                    val = item.get("soil_moisture", item.get("value", item.get("v")))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                ts = parse_iso_ts(item[0])
                val = item[1]
            if ts is None or val is None:
                continue
            try:
                series.append([int(ts), round(float(val), 1)])
            except (TypeError, ValueError):
                continue
    acc = {}
    for k, v in (p.get("accuracy_by_channel") or {}).items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        acc[str(k)] = round(f / 100.0 if f > 1.0 else f, 4)
    return {
        "horizon_minutes": p.get("prediction_horizon_minutes") or p.get("horizon_minutes"),
        "algorithm_info": p.get("algorithm_info"),
        "predictions": series,
        "accuracy_by_channel": acc,
    }
