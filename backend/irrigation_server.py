#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数字孪生灌溉系统 - 数据服务器 v3.1.0
====================================
v3.0.0 变化：
  - 升级为 3 路独立灌溉（3 个农田），每路独立土壤湿度 + 水泵
  - 移除温湿度字段（实物无温湿度传感器）
  - MQTT 契约：《系统接口契约v3.md》（farm/device01/sensors 上报 JSON 包）

v3.1.0 变化：
  - 自动灌溉改为"浇到目标湿度（70%）自动停泵"，替换原固定 10 分钟（智能停泵，修节水率指标）
  - 放缓模拟蒸发速率，使"按需灌溉 vs 传统灌溉"的节水对比回归正常正数

数据源模式（环境变量 DATA_SOURCE）：
  simulated  → 纯内置模拟（3 路物理模型）
  mqtt       → 纯真实传感器（ESP32 通过 MQTT 上报）
  auto       → 自动切换（默认）：MQTT 有数据用真实，超时 30 秒回退模拟

对外 HTTP API 见《系统接口契约v3.md》，Unity 端按契约对接。
"""

import os
import json
import math
import re
import sqlite3
import time
import random
import subprocess
import threading
from datetime import datetime, timedelta

from flask import Flask, jsonify, request
from flask_cors import CORS

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
    _MQTT_CB_API = mqtt.CallbackAPIVersion.VERSION2
except ImportError:
    HAS_MQTT = False
    _MQTT_CB_API = None


# ============================================================
# 配置区（可用环境变量覆盖）
# ============================================================
DATA_SOURCE = os.environ.get("DATA_SOURCE", "auto")            # auto | simulated | mqtt
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_STALE_TIMEOUT = int(os.environ.get("MQTT_STALE_TIMEOUT", "30"))
MQTT_BASE_TOPIC = os.environ.get("MQTT_BASE_TOPIC", "farm/device01")

NUM_CHANNELS = 3                          # 3 个农田
HISTORY_INTERVAL_SEC = 300                # 历史数据每 5 分钟一个点
MAX_HISTORY_POINTS = 576                  # 最多 48 小时
AUTO_IRRIGATION_THRESHOLD = 30.0          # 湿度低于此值自动灌溉（%）
AUTO_IRRIGATION_TARGET = 70.0             # 自动灌溉浇到此湿度即停（%）——智能停泵，避免过量灌溉
MAX_IRRIGATION_SEC = 600                  # 自动灌溉兜底上限（正常情况浇到目标就停，不会用满）
SOIL_MOISTURE_MIN, SOIL_MOISTURE_MAX = 10.0, 80.0

# 历史数据持久化（SQLite，重启不丢）
HISTORY_DB_PATH = os.environ.get(
    "HISTORY_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "irrigation_history.db"))

# 灌溉统计与节水基线
PUMP_FLOW_RATE_LPM = float(os.environ.get("PUMP_FLOW_RATE_LPM", "1.5"))          # 水泵流量（升/分钟）
TRADITIONAL_MINUTES_PER_DAY = float(os.environ.get("TRADITIONAL_MINUTES_PER_DAY", "30"))  # 传统经验灌溉基线（每路每天分钟数）

# ---------- 天气感知（OpenClaw AI 智能查询，可关闭） ----------
WEATHER_ENABLED = os.environ.get("WEATHER_ENABLED", "true").lower() in ("1", "true", "yes")
WEATHER_CITY = os.environ.get("WEATHER_CITY", "大连")
WEATHER_REFRESH_SEC = int(os.environ.get("WEATHER_REFRESH_SEC", "1800"))  # 每 30 分钟刷新
WEATHER_AGENT_ID = os.environ.get("WEATHER_AGENT_ID", "main")
# OpenClaw CLI 位于 root 目录，Flask 以 ubuntu 用户运行时用 sudo 执行
OPENCLAW_NODE = "/root/.nvm/versions/node/v22.22.1/bin/node"
OPENCLAW_CLI = "/root/.local/share/pnpm/global/5/.pnpm/openclaw@2026.6.11/node_modules/openclaw/dist/index.js"

START_TIME = time.time()


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ============================================================
# SQLite 存储（历史数据 + 灌溉事件，重启不丢）
# ============================================================
class HistoryStore:
    """历史数据存储：内存镜像（供预测）+ SQLite 持久化（重启恢复/大范围查询）。"""

    def __init__(self):
        self._conn = sqlite3.connect(HISTORY_DB_PATH, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                timestamp TEXT PRIMARY KEY,
                ch1_moisture REAL, ch1_pump INTEGER,
                ch2_moisture REAL, ch2_pump INTEGER,
                ch3_moisture REAL, ch3_pump INTEGER
            )
        """)
        self._conn.commit()
        self.points = self._load_recent(MAX_HISTORY_POINTS)
        print(f"[存储] SQLite 就绪: {HISTORY_DB_PATH}（启动时载入 {len(self.points)} 个历史点）")

    @property
    def conn(self):
        return self._conn

    def _load_recent(self, limit):
        rows = self._conn.execute(
            "SELECT timestamp, ch1_moisture, ch1_pump, ch2_moisture, ch2_pump, ch3_moisture, ch3_pump "
            "FROM history ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        rows.reverse()
        return [self._row_to_point(r) for r in rows]

    @staticmethod
    def _row_to_point(row):
        return {
            "timestamp": row[0],
            "channels": {
                1: {"soil_moisture": row[1], "pump_status": bool(row[2])},
                2: {"soil_moisture": row[3], "pump_status": bool(row[4])},
                3: {"soil_moisture": row[5], "pump_status": bool(row[6])},
            },
        }

    def append(self, point):
        """写入 DB 并更新内存镜像。"""
        ts = point["timestamp"]
        ch = point["channels"]
        self._conn.execute(
            "INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?,?)",
            (ts,
             ch[1]["soil_moisture"], int(ch[1]["pump_status"]),
             ch[2]["soil_moisture"], int(ch[2]["pump_status"]),
             ch[3]["soil_moisture"], int(ch[3]["pump_status"])))
        self._conn.commit()
        self.points.append(point)
        if len(self.points) > MAX_HISTORY_POINTS:
            self.points = self.points[-MAX_HISTORY_POINTS:]

    def query_since(self, cutoff_iso):
        """从 DB 查询 cutoff 之后的全部点（内存镜像不足时使用，如重启后查大范围）。"""
        rows = self._conn.execute(
            "SELECT timestamp, ch1_moisture, ch1_pump, ch2_moisture, ch2_pump, ch3_moisture, ch3_pump "
            "FROM history WHERE timestamp >= ? ORDER BY timestamp ASC", (cutoff_iso,)).fetchall()
        return [self._row_to_point(r) for r in rows]


class IrrigationStats:
    """灌溉事件统计：次数/时长/用水量/节水率（事件持久化到 SQLite）。"""

    def __init__(self, db_conn):
        self._conn = db_conn
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS irrigation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel INTEGER,
                start_time TEXT,
                end_time TEXT,
                duration_sec INTEGER
            )
        """)
        self._conn.commit()

    def record_event(self, channel, start_iso, end_iso, duration_sec):
        self._conn.execute(
            "INSERT INTO irrigation_events (channel, start_time, end_time, duration_sec) VALUES (?,?,?,?)",
            (channel, start_iso, end_iso, duration_sec))
        self._conn.commit()

    def get_stats(self, uptime_seconds):
        rows = self._conn.execute(
            "SELECT channel, duration_sec, start_time FROM irrigation_events").fetchall()

        total_events = len(rows)
        total_dur = sum(r[1] for r in rows)
        by_channel = {i: {"count": 0, "duration_sec": 0} for i in range(1, NUM_CHANNELS + 1)}
        for ch, dur, _st in rows:
            if ch in by_channel:
                by_channel[ch]["count"] += 1
                by_channel[ch]["duration_sec"] += dur

        # 运行天数（用服务运行时长，至少 1 小时计）
        days = max(uptime_seconds / 86400.0, 1.0 / 24.0)

        # 用水量估算（微型潜水泵典型 1~2 L/min）
        water_liters = round(total_dur / 60.0 * PUMP_FLOW_RATE_LPM, 2)

        # 日均每路灌溉分钟数（总时长 ÷ 路数 ÷ 天数）
        daily_avg_min_per_channel = (total_dur / 60.0) / NUM_CHANNELS / days

        # 节水率：对比传统经验灌溉基线（每路每天固定时长）
        baseline = TRADITIONAL_MINUTES_PER_DAY
        saving = max(0.0, min(100.0, (baseline - daily_avg_min_per_channel) / baseline * 100.0))

        return {
            "total_irrigations": total_events,
            "total_duration_sec": total_dur,
            "total_duration_minutes": round(total_dur / 60.0, 1),
            "by_channel": by_channel,
            "water_usage_liters": water_liters,
            "pump_flow_rate_lpm": PUMP_FLOW_RATE_LPM,
            "days_running": round(days, 2),
            "daily_avg_minutes_per_channel": round(daily_avg_min_per_channel, 2),
            "baseline_minutes_per_day": baseline,
            "water_saving_percent": round(saving, 1),
            "last_event": rows[-1][2] if rows else None,
        }


# ============================================================
# 预测模型（加权线性回归 + 指数平滑 + 精度自评估）
# ============================================================
class PredictionModel:
    """土壤湿度预测模型 v2：
    - 指数衰减加权回归：近期数据权重更高，捕捉真实趋势（数据点间隔 5 分钟）；
    - 指数平滑混合：当前值与回归线按比例混合，抑制单点噪声；
    - 留一法自评估：用历史数据回测，输出平均绝对误差（%），量化模型精度。
    """

    DECAY = 0.35         # 权重衰减系数（越大越看重近期）
    SMOOTH_ALPHA = 0.6   # 回归线与当前值的混合系数

    @classmethod
    def weighted_fit(cls, values):
        """加权最小二乘拟合，返回 (slope, intercept)。slope 单位为 湿度%/点（每点5分钟）。"""
        n = len(values)
        if n < 2:
            return 0.0, (values[0] if values else 50.0)
        w = [math.exp(-cls.DECAY * (n - 1 - i)) for i in range(n)]
        wsum = sum(w)
        xm = sum(w[i] * i for i in range(n)) / wsum
        ym = sum(w[i] * values[i] for i in range(n)) / wsum
        num = sum(w[i] * (i - xm) * (values[i] - ym) for i in range(n))
        den = sum(w[i] * (i - xm) ** 2 for i in range(n))
        slope = num / den if den else 0.0
        intercept = ym - slope * xm
        return slope, intercept

    @classmethod
    def predict_at(cls, values, steps_ahead):
        """预测 steps_ahead 个点（每点 5 分钟）之后的湿度：加权回归 + 指数平滑混合。"""
        n = len(values)
        if n == 0:
            return 50.0
        slope, intercept = cls.weighted_fit(values)
        x_next = (n - 1) + steps_ahead
        regression = intercept + slope * x_next
        smoothed = cls.SMOOTH_ALPHA * regression + (1.0 - cls.SMOOTH_ALPHA) * values[-1]
        return smoothed

    @classmethod
    def self_assess(cls, values):
        """留一法回测：依次用前 k 个点预测第 k 个点，返回平均绝对误差（%）。
        数据点不足 5 个时返回 None（无法可靠评估）。"""
        n = len(values)
        if n < 5:
            return None
        errors = []
        for k in range(4, n):
            pred = cls.predict_at(values[:k], 1)
            errors.append(abs(pred - values[k]))
        return sum(errors) / len(errors)


# ============================================================
# 物理模拟模型（3 路独立）
# ============================================================
class SimulationModel:
    """每路独立模拟：自然蒸发下降 + 灌溉上升 + 低湿度自动灌溉、浇到目标湿度自动停泵。"""

    def step(self, state):
        for i in range(1, NUM_CHANNELS + 1):
            ch = state["channels"][i]
            if ch["pump_status"]:
                # 灌溉时湿度上升
                ch["soil_moisture"] += random.uniform(2, 4)
                ch["irrigation_duration"] += 5
                # 智能停泵：浇到目标湿度即停（兜底：最长 MAX_IRRIGATION_SEC 强制停）
                if (ch["soil_moisture"] >= AUTO_IRRIGATION_TARGET
                        or ch["irrigation_duration"] >= MAX_IRRIGATION_SEC):
                    ch["pump_status"] = False
                    ch["irrigation_duration"] = 0
            else:
                # 自然蒸发（速率贴近真实土壤，保证"按需灌溉"确实比传统省水）
                evap = 0.03 + random.uniform(-0.01, 0.03)
                ch["soil_moisture"] -= evap

            ch["soil_moisture"] = _clamp(ch["soil_moisture"], SOIL_MOISTURE_MIN, SOIL_MOISTURE_MAX)

            if (ch["soil_moisture"] < AUTO_IRRIGATION_THRESHOLD and not ch["pump_status"]
                    and state.get("auto_irrigation_enabled", True)):
                ch["pump_status"] = True
                ch["irrigation_duration"] = 0
        return state


# ============================================================
# 数据中枢（3 路）
# ============================================================
class DataHub:
    def __init__(self):
        self._lock = threading.RLock()
        self.model = SimulationModel()
        self.mqtt_bridge = None
        self.running = True

        self.state = {
            "channels": {
                i: {"soil_moisture": 50.0, "pump_status": False, "irrigation_duration": 0}
                for i in range(1, NUM_CHANNELS + 1)
            },
            "auto_irrigation_enabled": True,
            "data_source": "simulated",
            "last_update": datetime.now().isoformat(),
        }
        self._last_mqtt_seen = 0.0
        self._last_history_ts = None
        self.history = HistoryStore()      # 历史数据（内存镜像 + SQLite 持久化）
        self.stats = IrrigationStats(self.history.conn)   # 灌溉事件统计
        self._pump_sessions = {}           # 泵运行会话：{channel: start_iso}
        self.weather = WeatherProvider()   # 天气感知（可选，失败自动降级）

        # 启动时若模拟器泵在运行（状态恢复），以启动时刻作为会话起点
        for i in range(1, NUM_CHANNELS + 1):
            if self.state["channels"][i]["pump_status"]:
                self._pump_sessions[i] = datetime.now().isoformat()

        self._sim_thread = threading.Thread(target=self._simulate_loop, daemon=True)
        self._sim_thread.start()

    # ---------- 数据源决策 ----------
    def _should_use_simulated(self):
        if DATA_SOURCE == "simulated":
            return True
        if DATA_SOURCE == "mqtt":
            return False
        return (time.time() - self._last_mqtt_seen) > MQTT_STALE_TIMEOUT

    # ---------- 模拟器线程 ----------
    def _simulate_loop(self):
        while self.running:
            self.simulate_step()
            time.sleep(5)

    def simulate_step(self):
        if not self._should_use_simulated():
            return
        with self._lock:
            self.model.step(self.state)
            self.state["data_source"] = "simulated"
            self.state["last_update"] = datetime.now().isoformat()
            self._sync_pump_tracking()
            self._maybe_append_history()

    # ---------- 灌溉事件跟踪（统计用） ----------
    def _sync_pump_tracking(self):
        """泵状态变化时记录灌溉事件（off→on 记开始，on→off 记时长入库）。"""
        for i in range(1, NUM_CHANNELS + 1):
            ch = self.state["channels"][i]
            if ch["pump_status"] and i not in self._pump_sessions:
                self._pump_sessions[i] = datetime.now().isoformat()
            elif not ch["pump_status"] and i in self._pump_sessions:
                start_iso = self._pump_sessions.pop(i)
                start = datetime.fromisoformat(start_iso)
                duration = int((datetime.now() - start).total_seconds())
                if duration >= 5:   # 忽略瞬时抖动
                    self.stats.record_event(i, start_iso, datetime.now().isoformat(), duration)
                    print(f"[统计] 农田{i} 灌溉结束：{duration} 秒")

    # ---------- MQTT 数据写入 ----------
    def mqtt_update_channels(self, updates):
        """updates: {channel: {"soil_moisture": v, "pump_status": b}}"""
        with self._lock:
            for ch_idx, fields in updates.items():
                if ch_idx in self.state["channels"]:
                    self.state["channels"][ch_idx].update(fields)
            self._last_mqtt_seen = time.time()
            self.state["data_source"] = "mqtt"
            self.state["last_update"] = datetime.now().isoformat()
            self._sync_pump_tracking()
            self._maybe_append_history()
            self._maybe_auto_irrigate_real()

    def mqtt_touch(self):
        """心跳等消息：仅刷新 MQTT 新鲜度。"""
        self._last_mqtt_seen = time.time()

    # ---------- 历史数据 ----------
    def _maybe_append_history(self):
        now = datetime.now()
        if self._last_history_ts is not None and (now - self._last_history_ts).total_seconds() < HISTORY_INTERVAL_SEC:
            return
        self._last_history_ts = now
        self.history.append({
            "timestamp": now.isoformat(),
            "channels": {
                i: {
                    "soil_moisture": round(self.state["channels"][i]["soil_moisture"], 1),
                    "pump_status": self.state["channels"][i]["pump_status"],
                }
                for i in range(1, NUM_CHANNELS + 1)
            },
        })

    def get_history(self, hours=24):
        cutoff = datetime.now() - timedelta(hours=hours)
        with self._lock:
            mem = self.history.points
            if mem and datetime.fromisoformat(mem[0]["timestamp"]) <= cutoff:
                # 内存镜像覆盖查询范围
                points = [p for p in mem if datetime.fromisoformat(p["timestamp"]) >= cutoff]
            else:
                # 重启后内存不足，直接从 SQLite 查
                points = self.history.query_since(cutoff.isoformat())
        per_channel = []
        for i in range(1, NUM_CHANNELS + 1):
            per_channel.append([
                {
                    "timestamp": p["timestamp"],
                    "soil_moisture": p["channels"][i]["soil_moisture"],
                    "pump_status": p["channels"][i]["pump_status"],
                }
                for p in points
            ])
        return {
            "channels": per_channel,   # 数组，下标 0-2 对应农田 1-3（Unity JsonUtility 兼容）
            "total_points": len(points),
            "query_range_hours": hours,
        }

    # ---------- 实时快照 ----------
    def get_realtime_data(self):
        with self._lock:
            channels = [
                {
                    "channel": i,
                    "soil_moisture": round(self.state["channels"][i]["soil_moisture"], 1),
                    "pump_status": self.state["channels"][i]["pump_status"],
                    "irrigation_duration": self.state["channels"][i]["irrigation_duration"],
                }
                for i in range(1, NUM_CHANNELS + 1)
            ]
            avg = round(sum(c["soil_moisture"] for c in channels) / NUM_CHANNELS, 1)
            active = sum(1 for c in channels if c["pump_status"])
            return {
                "channels": channels,
                "soil_moisture_avg": avg,
                "active_pumps": active,
                "data_source": self.state["data_source"],
                "auto_irrigation_enabled": self.state["auto_irrigation_enabled"],
                "last_update": self.state["last_update"],
            }

    # ---------- 预测（3 路：加权回归 + 指数平滑 + 天气修正 + 自评估） ----------
    def get_prediction(self, minutes=30):
        with self._lock:
            predictions = []   # 数组，下标 0-2 对应农田 1-3（Unity JsonUtility 兼容）
            accuracy_by_channel = {}

            for i in range(1, NUM_CHANNELS + 1):
                series = [p["channels"][i]["soil_moisture"] for p in self.history.points[-12:]]
                current = self.state["channels"][i]["soil_moisture"]
                pumping = self.state["channels"][i]["pump_status"]

                ch_preds = []
                for m in range(0, minutes + 1, 5):
                    steps = max(1, int(round(m / 5.0)))

                    # 1) 趋势预测：加权回归 + 指数平滑
                    trend = PredictionModel.predict_at(series, steps)

                    # 2) 天气修正：降雨概率高 → 蒸发减缓（下降量打折，最多 60%）
                    rain_factor = 1.0
                    if self.weather is not None and self.weather.enabled:
                        p = self.weather.rain_probability_at(m)
                        if p is not None and p > 0:
                            rain_factor = max(0.4, 1.0 - 0.6 * p / 100.0)

                    # 3) 灌溉修正：泵运行中叠加上升效应（前 2 小时递减）
                    if pumping and m < 120:
                        predicted = trend + 2.0 * (1 - m / 120) * (m / 60)
                    else:
                        delta = trend - current
                        if delta < 0:
                            delta *= rain_factor          # 下降趋势按降雨打折
                        predicted = current + delta

                    predicted = _clamp(predicted, SOIL_MOISTURE_MIN, SOIL_MOISTURE_MAX)
                    ch_preds.append({
                        "future_time": (datetime.now() + timedelta(minutes=m)).isoformat(),
                        "minutes_ahead": m,
                        "predicted_soil_moisture": round(predicted, 1),
                        "confidence": max(0.7, 1.0 - (m / max(minutes, 1)) * 0.3),
                    })
                predictions.append(ch_preds)

                # 4) 精度自评估（留一法回测）
                mae = PredictionModel.self_assess(series)
                accuracy_by_channel[str(i)] = round(mae, 2) if mae is not None else None

            weather_forecast = None
            if self.weather is not None and self.weather.enabled:
                weather_forecast = self.weather.get_forecast()

            # 整体自评估（三路有效值的平均）
            valid_maes = [v for v in accuracy_by_channel.values() if v is not None]
            overall_mae = round(sum(valid_maes) / len(valid_maes), 2) if valid_maes else None

            algo = "加权线性回归+指数平滑预测模型"
            if overall_mae is not None:
                algo += f"（自评估误差 ±{overall_mae}%）"
            if weather_forecast:
                algo += " + OpenClaw 天气感知"

            return {
                "predictions": predictions,
                "current_conditions": self.get_realtime_data(),
                "prediction_horizon_minutes": minutes,
                "algorithm_info": algo,
                "prediction_accuracy_mae_percent": overall_mae,
                "accuracy_by_channel": accuracy_by_channel,
                "weather_forecast": weather_forecast,
            }

    # ---------- 控制 ----------
    def control_pump(self, action, channel=None, duration_minutes=None):
        action = (action or "").lower()

        if action == "auto":
            with self._lock:
                self.state["auto_irrigation_enabled"] = True
            return {"status": "success", "message": "已切换到自动灌溉模式", "auto_mode": True}

        if action not in ("start", "stop"):
            return {"status": "error", "message": "无效的控制命令，action 应为 start/stop/auto"}

        # 解析目标农田：1-3 或 "all"
        if channel in ("all", 0, "0"):
            targets = list(range(1, NUM_CHANNELS + 1))
        else:
            try:
                c = int(channel) if channel is not None else 1
            except (TypeError, ValueError):
                c = 1
            targets = [c] if 1 <= c <= NUM_CHANNELS else [1]

        if self._should_use_simulated():
            with self._lock:
                for i in targets:
                    ch = self.state["channels"][i]
                    if action == "start":
                        ch["pump_status"] = True
                        ch["irrigation_duration"] = (duration_minutes or 0) * 60
                    else:
                        ch["pump_status"] = False
                        ch["irrigation_duration"] = 0
                self.state["last_update"] = datetime.now().isoformat()
                self._sync_pump_tracking()
            msg = ("灌溉已启动（模拟）" if action == "start" else "灌溉已停止（模拟）")
        else:
            # 真实模式：MQTT 下发 + 乐观更新
            if self.mqtt_bridge is not None:
                for i in targets:
                    self.mqtt_bridge.publish_control(i, action, duration_minutes)
            with self._lock:
                for i in targets:
                    ch = self.state["channels"][i]
                    if action == "start":
                        ch["pump_status"] = True
                        ch["irrigation_duration"] = (duration_minutes or 0) * 60
                    else:
                        ch["pump_status"] = False
                        ch["irrigation_duration"] = 0
                self.state["last_update"] = datetime.now().isoformat()
                self._sync_pump_tracking()
            msg = ("灌溉指令已下发（MQTT）" if action == "start" else "停止指令已下发（MQTT）")

        result = {
            "status": "success",
            "message": msg,
            "channels": targets,
            "pump_status": [self.state["channels"][i]["pump_status"] for i in targets],
        }
        if len(targets) == 1:
            result["channel"] = targets[0]
        return result

    # ---------- 真实模式自动灌溉决策 ----------
    def _maybe_auto_irrigate_real(self):
        if not self.state.get("auto_irrigation_enabled", True):
            return
        for i in range(1, NUM_CHANNELS + 1):
            ch = self.state["channels"][i]
            if ch["soil_moisture"] < AUTO_IRRIGATION_THRESHOLD and not ch["pump_status"]:
                if self.mqtt_bridge is not None:
                    self.mqtt_bridge.publish_control(i, "start", None)
                ch["pump_status"] = True
                ch["irrigation_duration"] = 0
                self.state["last_update"] = datetime.now().isoformat()
                self._sync_pump_tracking()

    # ---------- 灌溉统计 ----------
    def get_irrigation_stats(self):
        return self.stats.get_stats(int(time.time() - START_TIME))


# ============================================================
# 天气感知（OpenClaw AI 联网查询，可选，失败自动降级）
# ============================================================
class WeatherProvider:
    """通过 OpenClaw AI agent 查询城市天气预报，缓存结果供预测融合。

    调用方式：sudo 执行 root 目录下的 openclaw CLI（Flask 以 ubuntu 用户运行）。
    失败时不影响基本功能（预测自动回退到纯历史趋势）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._forecast = None
        self._updated_at = 0.0
        self._last_error = None
        # 注意：Flask 以 ubuntu 用户运行，无法直接 stat /root 下文件（os.path.exists 恒 False），
        # 因此不预检查，直接尝试 sudo 执行，失败由 _loop 捕获并降级。
        self.enabled = WEATHER_ENABLED
        if not self.enabled:
            print("[天气] 天气感知已禁用（WEATHER_ENABLED=false）")
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.refresh()
            except Exception as e:
                self._last_error = str(e)[:300]
                print(f"[天气] 查询失败: {self._last_error}")
            time.sleep(WEATHER_REFRESH_SEC)

    def refresh(self):
        prompt = (
            f"请查询{WEATHER_CITY}未来48小时天气。只输出一个JSON数组，不要任何其他文字。"
            f"数组每个元素包含字段：date（YYYY-MM-DD字符串）、weather（中文描述）、"
            f"temperature_c（数字）、rain_probability_percent（0到100的数字）。"
        )
        cmd = ["sudo", OPENCLAW_NODE, OPENCLAW_CLI, "agent", "--agent", WEATHER_AGENT_ID,
               "-m", prompt, "--json", "--timeout", "90"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
        answer = self._extract_answer(proc.stdout)
        forecast = self._parse_forecast(answer)
        with self._lock:
            self._forecast = forecast
            self._updated_at = time.time()
        if forecast:
            first = forecast[0]
            print(f"[天气] 已更新 {len(forecast)} 条预报，首条: {first.get('date')} {first.get('weather')}")
        else:
            print(f"[天气] 解析失败（回答前200字）: {str(answer)[:200]}")

    @staticmethod
    def _extract_answer(stdout):
        """从 openclaw --json 输出中递归提取 finalAssistantVisibleText。"""
        try:
            data = json.loads(stdout)
        except Exception:
            return stdout

        def find(obj):
            if isinstance(obj, dict):
                if "finalAssistantVisibleText" in obj:
                    return obj["finalAssistantVisibleText"]
                for v in obj.values():
                    r = find(v)
                    if r:
                        return r
            elif isinstance(obj, list):
                for v in obj:
                    r = find(v)
                    if r:
                        return r
            return None

        return find(data) or stdout

    @staticmethod
    def _parse_forecast(text):
        """从回答文本中提取 JSON 数组（支持 ```json 代码块或裸数组）。"""
        if not text:
            return None
        candidates = []
        m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
        if m:
            candidates.append(m.group(1))
        m2 = re.search(r"\[\s*\{.*?\}\s*(?:,\s*\{.*?\}\s*)*\]", text, re.S)
        if m2:
            candidates.append(m2.group(0))
        for cand in candidates:
            try:
                data = json.loads(cand)
                if isinstance(data, list):
                    return data
            except Exception:
                continue
        return None

    def get_forecast(self):
        with self._lock:
            return list(self._forecast) if self._forecast else None

    def get_status(self):
        with self._lock:
            return {
                "enabled": self.enabled,
                "city": WEATHER_CITY,
                "forecast": list(self._forecast) if self._forecast else None,
                "updated_at": datetime.fromtimestamp(self._updated_at).isoformat() if self._updated_at else None,
                "last_error": self._last_error,
            }

    def rain_probability_at(self, minutes_ahead):
        """minutes_ahead 分钟后所在日期当天最大降雨概率（0-100，无数据返回 None）。

        取当天最大值作为保守估计：当天任一时段可能下雨，灌溉都应酌情减少。
        """
        forecast = self.get_forecast()
        if not forecast:
            return None
        target_date = (datetime.now() + timedelta(minutes=minutes_ahead)).date()
        day_rain = []
        for entry in forecast:
            date_str = str(entry.get("date", ""))[:10]
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d == target_date:
                p = entry.get("rain_probability_percent")
                if isinstance(p, (int, float)):
                    day_rain.append(float(p))
        return max(day_rain) if day_rain else None


# ============================================================
# MQTT 桥接
# ============================================================
class MQTTBridge:
    def __init__(self, hub):
        self.hub = hub
        self.client = None
        if not HAS_MQTT:
            print("[MQTT] paho-mqtt 未安装，本次运行将始终使用模拟数据源")
            return
        self.client = mqtt.Client(callback_api_version=_MQTT_CB_API)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def start(self):
        if self.client is None:
            return
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            self.client.loop_start()
            print(f"[MQTT] 已连接 broker {MQTT_HOST}:{MQTT_PORT}")
        except Exception as e:
            print(f"[MQTT] 连接失败: {e}（回退为模拟数据源）")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if getattr(reason_code, "is_failure", False):
            print(f"[MQTT] 连接被拒绝: {reason_code}")
            return
        topic = f"{MQTT_BASE_TOPIC}/#"
        client.subscribe(topic, qos=0)
        print(f"[MQTT] 已订阅主题: {topic}")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        print(f"[MQTT] 连接断开: {reason_code}（auto 模式将在超时后回退模拟数据）")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode("utf-8", "ignore").strip()
        if not payload:
            return
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            data = payload

        # 心跳
        if topic.endswith("/status"):
            self.hub.mqtt_touch()
            return

        # v3 契约：sensors 主题 JSON 包 {"soil_1":..,"soil_2":..,"soil_3":..,"pump_1":..,...}
        if topic.endswith("/sensors") and isinstance(data, dict):
            updates = {}
            for i in range(1, NUM_CHANNELS + 1):
                fields = {}
                if f"soil_{i}" in data and isinstance(data[f"soil_{i}"], (int, float)):
                    fields["soil_moisture"] = float(data[f"soil_{i}"])
                if f"pump_{i}" in data:
                    fields["pump_status"] = bool(data[f"pump_{i}"])
                if fields:
                    updates[i] = fields
            if updates:
                self.hub.mqtt_update_channels(updates)
            else:
                self.hub.mqtt_touch()
            return

        # 兼容旧格式（v2 时代单路主题，如 sensors/soil_moisture）：按 channel 1 处理
        if isinstance(data, (int, float)) and any(k in topic for k in ("soil", "moisture")):
            self.hub.mqtt_update_channels({1: {"soil_moisture": float(data)}})
            return

        # 其他消息至少刷新心跳
        self.hub.mqtt_touch()

    def publish_control(self, channel, action, duration_minutes=None):
        if self.client is None:
            return
        topic = f"{MQTT_BASE_TOPIC}/control"
        payload = {"channel": channel, "action": action}
        if duration_minutes:
            payload["duration_minutes"] = duration_minutes
        try:
            self.client.publish(topic, json.dumps(payload), qos=0)
            print(f"[MQTT] 控制指令下发 {topic} <- {payload}")
        except Exception as e:
            print(f"[MQTT] 控制指令发布失败: {e}")


# ============================================================
# Flask 应用与路由（v3 契约）
# ============================================================
app = Flask(__name__)
CORS(app)

hub = DataHub()
mqtt_bridge = MQTTBridge(hub)
hub.mqtt_bridge = mqtt_bridge
mqtt_bridge.start()


@app.route('/api/realtime-data', methods=['GET'])
def get_realtime_data():
    try:
        return jsonify(hub.get_realtime_data())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/historical-data', methods=['GET'])
def get_historical_data():
    try:
        hours = request.args.get('hours', 24, type=int)
        return jsonify(hub.get_history(hours))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/prediction', methods=['GET'])
def get_prediction_data():
    try:
        minutes = request.args.get('minutes', 30, type=int)
        minutes = max(5, min(180, minutes))
        return jsonify(hub.get_prediction(minutes))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/control-pump', methods=['POST'])
def control_pump():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "缺少请求参数"}), 400
        action = data.get('action')
        channel = data.get('channel')
        duration_minutes = data.get('duration_minutes')
        if not action:
            return jsonify({"error": "缺少action参数"}), 400
        result = hub.control_pump(action, channel, duration_minutes)
        code = 200 if result.get("status") == "success" else 400
        return jsonify(result), code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/system-status', methods=['GET'])
def get_system_status():
    try:
        return jsonify({
            "system_running": True,
            "uptime_seconds": int(time.time() - START_TIME),
            "data_points_collected": len(hub.history.points),
            "history_db": HISTORY_DB_PATH,
            "irrigation_stats": hub.get_irrigation_stats(),
            "channel_count": NUM_CHANNELS,
            "server_time": datetime.now().isoformat(),
            "api_version": "3.1.0",
            "data_source_mode": DATA_SOURCE,
            "data_source": hub.get_realtime_data()["data_source"],
            "weather": hub.weather.get_status() if hub.weather else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/weather', methods=['GET'])
def get_weather():
    """天气感知数据接口（OpenClaw AI 查询，可能为 None 表示不可用）。"""
    try:
        if hub.weather is None:
            return jsonify({"enabled": False, "forecast": None})
        status = hub.weather.get_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/irrigation-stats', methods=['GET'])
def get_irrigation_stats():
    """灌溉统计接口：次数/时长/用水量/节水率（比赛工程效果数据）。"""
    try:
        return jsonify(hub.get_irrigation_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/', methods=['GET'])
def api_info():
    return jsonify({
        "name": "数字孪生灌溉系统 - 数据API",
        "version": "3.1.0",
        "endpoints": {
            "GET /api/realtime-data": "获取3路实时传感器数据",
            "GET /api/historical-data?hours=24": "获取3路历史数据（SQLite持久化）",
            "GET /api/prediction?minutes=30": "获取3路土壤湿度预测（加权回归+自评估）",
            "POST /api/control-pump": "控制水泵灌溉（channel:1-3/all）",
            "GET /api/system-status": "获取系统运行状态",
            "GET /api/weather": "天气感知（OpenClaw AI）",
            "GET /api/irrigation-stats": "灌溉统计（次数/用水量/节水率）",
        },
        "data_format": "JSON",
        "data_source_mode": DATA_SOURCE,
        "channels": NUM_CHANNELS,
        "mqtt_broker": f"{MQTT_HOST}:{MQTT_PORT}" if HAS_MQTT else "未安装 paho-mqtt",
    })


if __name__ == '__main__':
    print("🌱 数字孪生灌溉系统数据服务器 v3.1.0 启动中...")
    print(f"   数据源模式: DATA_SOURCE={DATA_SOURCE}（{NUM_CHANNELS} 路独立灌溉）")
    print(f"   MQTT: {MQTT_HOST}:{MQTT_PORT} (基础主题 {MQTT_BASE_TOPIC})")
    print("📡 API服务地址: http://0.0.0.0:5000")
    print("📖 API文档: http://0.0.0.0:5000/")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
