#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数字孪生灌溉系统 - 数据服务器 v3.0.0
====================================
v3.0.0 变化：
  - 升级为 3 路独立灌溉（3 个花盆），每路独立土壤湿度 + 水泵
  - 移除温湿度字段（实物无温湿度传感器）
  - MQTT 契约：《系统接口契约v3.md》（farm/device01/sensors 上报 JSON 包）

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

NUM_CHANNELS = 3                          # 3 个花盆
HISTORY_INTERVAL_SEC = 300                # 历史数据每 5 分钟一个点
MAX_HISTORY_POINTS = 576                  # 最多 48 小时
AUTO_IRRIGATION_THRESHOLD = 30.0          # 湿度低于此值自动灌溉（%）
MAX_IRRIGATION_SEC = 600                  # 自动灌溉最长 10 分钟
SOIL_MOISTURE_MIN, SOIL_MOISTURE_MAX = 10.0, 80.0

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
# 物理模拟模型（3 路独立）
# ============================================================
class SimulationModel:
    """每路独立模拟：自然蒸发下降 + 灌溉上升 + 低湿度自动灌溉。"""

    def step(self, state):
        for i in range(1, NUM_CHANNELS + 1):
            ch = state["channels"][i]
            if ch["pump_status"]:
                ch["soil_moisture"] += random.uniform(2, 4)
                ch["irrigation_duration"] += 5
                if ch["irrigation_duration"] >= MAX_IRRIGATION_SEC:
                    ch["pump_status"] = False
                    ch["irrigation_duration"] = 0
            else:
                evap = 0.1 + random.uniform(-0.1, 0.3)
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
        self.history = []   # 统一时间点存储：[{timestamp, channels: {1:{...},2:{...},3:{...}}}]
        self.weather = WeatherProvider()   # 天气感知（可选，失败自动降级）

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
            self._maybe_append_history()

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
        if len(self.history) > MAX_HISTORY_POINTS:
            self.history = self.history[-MAX_HISTORY_POINTS:]

    def get_history(self, hours=24):
        cutoff = datetime.now() - timedelta(hours=hours)
        with self._lock:
            points = [p for p in self.history if datetime.fromisoformat(p["timestamp"]) >= cutoff]
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
            "channels": per_channel,   # 数组，下标 0-2 对应花盆 1-3（Unity JsonUtility 兼容）
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

    # ---------- 预测（3 路，基于历史线性趋势外推） ----------
    @staticmethod
    def _linear_slope(values):
        """values: 按时间顺序的数值序列；返回最小二乘斜率（每点间隔 5 分钟）。"""
        n = len(values)
        if n < 2:
            return 0.0
        xm = (n - 1) / 2.0
        ym = sum(values) / n
        num = sum((i - xm) * (values[i] - ym) for i in range(n))
        den = sum((i - xm) ** 2 for i in range(n))
        return num / den if den else 0.0

    def get_prediction(self, minutes=30):
        with self._lock:
            predictions = []   # 数组，下标 0-2 对应花盆 1-3（Unity JsonUtility 兼容）
            for i in range(1, NUM_CHANNELS + 1):
                series = [p["channels"][i]["soil_moisture"] for p in self.history[-12:]]
                slope_per_point = self._linear_slope(series)      # 每 5 分钟变化量
                per_minute = slope_per_point / 5.0
                current = self.state["channels"][i]["soil_moisture"]
                pumping = self.state["channels"][i]["pump_status"]

                ch_preds = []
                for m in range(0, minutes + 1, 5):
                    # 天气修正：降雨概率高 → 蒸发减缓（空气湿度大），最多减缓 60%
                    rain_factor = 1.0
                    if self.weather is not None and self.weather.enabled:
                        p = self.weather.rain_probability_at(m)
                        if p is not None and p > 0:
                            rain_factor = max(0.4, 1.0 - 0.6 * p / 100.0)

                    if pumping and m < 120:
                        delta = per_minute * m + 2.0 * (1 - m / 120) * (m / 60)
                    else:
                        delta = per_minute * rain_factor * m
                    predicted = _clamp(current + delta, SOIL_MOISTURE_MIN, SOIL_MOISTURE_MAX)
                    ch_preds.append({
                        "future_time": (datetime.now() + timedelta(minutes=m)).isoformat(),
                        "minutes_ahead": m,
                        "predicted_soil_moisture": round(predicted, 1),
                        "confidence": max(0.7, 1.0 - (m / max(minutes, 1)) * 0.3),
                    })
                predictions.append(ch_preds)

            weather_forecast = None
            if self.weather is not None and self.weather.enabled:
                weather_forecast = self.weather.get_forecast()

            return {
                "predictions": predictions,
                "current_conditions": self.get_realtime_data(),
                "prediction_horizon_minutes": minutes,
                "algorithm_info": "基于历史趋势的土壤湿度线性回归预测模型"
                                  + ("（融合 OpenClaw 天气感知）" if weather_forecast else ""),
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

        # 解析目标花盆：1-3 或 "all"
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
            "data_points_collected": len(hub.history),
            "channel_count": NUM_CHANNELS,
            "server_time": datetime.now().isoformat(),
            "api_version": "3.0.0",
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


@app.route('/', methods=['GET'])
def api_info():
    return jsonify({
        "name": "数字孪生灌溉系统 - 数据API",
        "version": "3.0.0",
        "endpoints": {
            "GET /api/realtime-data": "获取3路实时传感器数据",
            "GET /api/historical-data?hours=24": "获取3路历史数据",
            "GET /api/prediction?minutes=30": "获取3路土壤湿度预测",
            "POST /api/control-pump": "控制水泵灌溉（channel:1-3/all）",
            "GET /api/system-status": "获取系统运行状态",
        },
        "data_format": "JSON",
        "data_source_mode": DATA_SOURCE,
        "channels": NUM_CHANNELS,
        "mqtt_broker": f"{MQTT_HOST}:{MQTT_PORT}" if HAS_MQTT else "未安装 paho-mqtt",
    })


if __name__ == '__main__':
    print("🌱 数字孪生灌溉系统数据服务器 v3.0.0 启动中...")
    print(f"   数据源模式: DATA_SOURCE={DATA_SOURCE}（{NUM_CHANNELS} 路独立灌溉）")
    print(f"   MQTT: {MQTT_HOST}:{MQTT_PORT} (基础主题 {MQTT_BASE_TOPIC})")
    print("📡 API服务地址: http://0.0.0.0:5000")
    print("📖 API文档: http://0.0.0.0:5000/")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
