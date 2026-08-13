#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数字孪生灌溉系统 - 数据服务器 v2.0
====================================
为 Unity 数字孪生前端提供数据接口，支持三种数据源模式（通过环境变量 DATA_SOURCE 配置）：

  simulated  → 纯模拟数据（演示 / 联调用，内置物理模型自动生成）
  mqtt       → 纯真实传感器数据（ESP32 通过 MQTT 上报）
  auto       → 自动切换（默认）：MQTT 收到真实数据就用真实值，
               超过 MQTT_STALE_TIMEOUT 秒未收到则自动回退到模拟数据。

这样后续 ESP32 湿度传感器真正接入后，只需让设备向 MQTT 上报数据，
服务器无需改代码即可平滑切换到真实数据源；传感器掉线时自动降级为模拟数据，
比赛现场也不至于黑屏。

对外 HTTP API 与 v1.x 完全兼容，Unity 端无需改动（新增了 data_source 字段用于前端提示）。
"""

import os
import json
import math
import time
import random
import threading
from datetime import datetime, timedelta

from flask import Flask, jsonify, request
from flask_cors import CORS

# 可选依赖：paho-mqtt（未安装时自动降级为纯模拟模式）
try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
    _MQTT_CB_API = mqtt.CallbackAPIVersion.VERSION2
except ImportError:
    HAS_MQTT = False
    _MQTT_CB_API = None


# ============================================================
# 配置区（均可用环境变量覆盖，方便 systemd 里配置）
# ============================================================
DATA_SOURCE = os.environ.get("DATA_SOURCE", "auto")            # auto | simulated | mqtt
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")           # 本机 mosquitto
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_STALE_TIMEOUT = int(os.environ.get("MQTT_STALE_TIMEOUT", "30"))  # auto 模式判定 MQTT 超时的秒数
MQTT_BASE_TOPIC = os.environ.get("MQTT_BASE_TOPIC", "farm/device01")

HISTORY_INTERVAL_SEC = 300        # 历史数据每 5 分钟存一个点
MAX_HISTORY_POINTS = 576          # 最多保留 48 小时（576 个 5 分钟点）
AUTO_IRRIGATION_THRESHOLD = 30.0  # 土壤湿度低于此值自动触发灌溉（%）
MAX_IRRIGATION_SEC = 600          # 自动灌溉最长 10 分钟后停止
SOIL_MOISTURE_MIN, SOIL_MOISTURE_MAX = 10.0, 80.0
HUMIDITY_MIN, HUMIDITY_MAX = 20.0, 90.0

# 模拟物理模型参数
BASE_TEMPERATURE = 22.0
BASE_HUMIDITY = 65.0

START_TIME = time.time()          # 用于计算真实运行时长（修复 uptime bug）


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ============================================================
# 物理模拟模型（纯函数：根据当前状态推演下一步）
# ============================================================
class SimulationModel:
    """基于物理规律的模拟器：昼夜温度正弦、湿度负相关、土壤蒸发/灌溉平衡。"""

    def step(self, state):
        now = datetime.now()
        hour = now.hour + now.minute / 60.0

        # 温度：正弦昼夜变化，峰值在下午 2-3 点
        temperature = BASE_TEMPERATURE + 8 * math.sin((hour - 6) * math.pi / 12) + random.uniform(-1, 1)

        # 空气湿度：与温度负相关
        humidity = BASE_HUMIDITY - 15 * math.sin((hour - 6) * math.pi / 12) + random.uniform(-5, 5)
        humidity = _clamp(humidity, HUMIDITY_MIN, HUMIDITY_MAX)

        soil = state["soil_moisture"]
        pump = state["pump_status"]
        duration = state["irrigation_duration"]

        if pump:
            # 灌溉时湿度快速上升
            soil += random.uniform(2, 4)
            duration += 5
            if duration >= MAX_IRRIGATION_SEC:
                pump = False
                duration = 0
        else:
            # 自然蒸发
            evap = 0.1 + 0.05 * (temperature - 20) / 10
            if humidity < 50:
                evap *= 1.5
            soil -= evap + random.uniform(-0.1, 0.3)

        soil = _clamp(soil, SOIL_MOISTURE_MIN, SOIL_MOISTURE_MAX)

        # 自动灌溉：湿度低于阈值且开启自动模式时触发
        if soil < AUTO_IRRIGATION_THRESHOLD and not pump and state.get("auto_irrigation_enabled", True):
            pump = True
            duration = 0

        return {
            "soil_moisture": soil,
            "temperature": temperature,
            "humidity": humidity,
            "pump_status": pump,
            "irrigation_duration": duration,
        }


# ============================================================
# 数据中枢：唯一状态持有者 + 数据源决策 + 历史 + 预测 + 控制
# ============================================================
class DataHub:
    """聚合模拟器与 MQTT 两路数据，决定对外暴露哪一路，并维护历史与预测。"""

    def __init__(self):
        self._lock = threading.RLock()
        self.model = SimulationModel()
        self.mqtt_bridge = None            # 由外部注入
        self.running = True

        # 当前对外状态（真实与模拟共用同一份，切换时平滑续接）
        self.state = {
            "soil_moisture": 50.0,
            "temperature": 22.0,
            "humidity": 65.0,
            "pump_status": False,
            "irrigation_duration": 0,
            "auto_irrigation_enabled": True,
            "data_source": "simulated",
            "last_update": datetime.now().isoformat(),
        }
        self._last_mqtt_seen = 0.0         # epoch 初值 → auto 模式下先以模拟起步
        self._last_history_ts = None
        self.history = []

        # 模拟器后台线程
        self._sim_thread = threading.Thread(target=self._simulate_loop, daemon=True)
        self._sim_thread.start()

    # ---------- 数据源决策 ----------
    def _should_use_simulated(self):
        if DATA_SOURCE == "simulated":
            return True
        if DATA_SOURCE == "mqtt":
            return False
        # auto：MQTT 在超时窗口内有过数据 → 用真实；否则用模拟
        return (time.time() - self._last_mqtt_seen) > MQTT_STALE_TIMEOUT

    # ---------- 模拟器线程 ----------
    def _simulate_loop(self):
        while self.running:
            self.simulate_step()
            time.sleep(5)

    def simulate_step(self):
        """推进一步模拟。仅在模拟数据生效时写状态。"""
        if not self._should_use_simulated():
            return
        with self._lock:
            new = self.model.step(self.state)
            self.state.update(new)
            self.state["data_source"] = "simulated"
            self.state["last_update"] = datetime.now().isoformat()
            self._maybe_append_history()

    # ---------- MQTT 数据写入 ----------
    def mqtt_update(self, **fields):
        """MQTT 线程收到真实数据时调用，写入状态并标记新鲜。"""
        with self._lock:
            self.state.update(fields)
            self._last_mqtt_seen = time.time()
            self.state["data_source"] = "mqtt"
            self.state["last_update"] = datetime.now().isoformat()
            self._maybe_append_history()
            # 真实模式下，服务器侧自动灌溉决策
            self._maybe_auto_irrigate_real()

    # ---------- 历史数据 ----------
    def _maybe_append_history(self):
        now = datetime.now()
        if self._last_history_ts is not None and (now - self._last_history_ts).total_seconds() < HISTORY_INTERVAL_SEC:
            return
        self._last_history_ts = now
        self.history.append({
            "timestamp": now.isoformat(),
            "soil_moisture": round(self.state["soil_moisture"], 1),
            "temperature": round(self.state["temperature"], 1),
            "humidity": round(self.state["humidity"], 1),
            "pump_status": self.state["pump_status"],
        })
        if len(self.history) > MAX_HISTORY_POINTS:
            self.history = self.history[-MAX_HISTORY_POINTS:]

    def get_history(self, hours=24):
        cutoff = datetime.now() - timedelta(hours=hours)
        with self._lock:
            points = [p for p in self.history if datetime.fromisoformat(p["timestamp"]) >= cutoff]
        return {
            "data_points": points,
            "total_points": len(points),
            "query_range_hours": hours,
        }

    # ---------- 实时快照 ----------
    def snapshot(self):
        with self._lock:
            return dict(self.state)

    def get_realtime_data(self):
        s = self.snapshot()
        return {
            "soil_moisture": round(s["soil_moisture"], 1),
            "temperature": round(s["temperature"], 1),
            "humidity": round(s["humidity"], 1),
            "pump_status": s["pump_status"],
            "irrigation_duration": s["irrigation_duration"],
            "last_update": s["last_update"],
            "auto_irrigation_enabled": s["auto_irrigation_enabled"],
            "data_source": s["data_source"],
        }

    # ---------- 预测 ----------
    def get_prediction(self, minutes=30):
        s = self.snapshot()
        current_moisture = s["soil_moisture"]
        current_temp = s["temperature"]
        current_humidity = s["humidity"]
        pumping = s["pump_status"]

        predictions = []
        for i in range(0, minutes + 1, 5):
            if pumping and i < 120:
                moisture_change = 2.0 * (1 - i / 120)
            else:
                evap = 0.1 + 0.05 * (current_temp - 20) / 10
                if current_humidity < 50:
                    evap *= 1.5
                moisture_change = -evap * (i / 5)

            predicted = _clamp(current_moisture + moisture_change * (i / 60), SOIL_MOISTURE_MIN, SOIL_MOISTURE_MAX)
            predictions.append({
                "future_time": (datetime.now() + timedelta(minutes=i)).isoformat(),
                "minutes_ahead": i,
                "predicted_soil_moisture": round(predicted, 1),
                "confidence": max(0.7, 1.0 - (i / max(minutes, 1)) * 0.3),
            })

        return {
            "predictions": predictions,
            "current_conditions": self.get_realtime_data(),
            "prediction_horizon_minutes": minutes,
            "algorithm_info": "基于物理规律的土壤湿度变化预测模型",
        }

    # ---------- 控制 ----------
    def control_pump(self, action, duration_minutes=None):
        action = (action or "").lower()

        if action == "auto":
            with self._lock:
                self.state["auto_irrigation_enabled"] = True
            return {"status": "success", "message": "已切换到自动灌溉模式", "pump_status": self.state["pump_status"]}

        if action not in ("start", "stop"):
            return {"status": "error", "message": "无效的控制命令，action 应为 start/stop/auto"}

        if self._should_use_simulated():
            # 模拟生效：直接改本地状态
            with self._lock:
                if action == "start":
                    self.state["pump_status"] = True
                    self.state["irrigation_duration"] = (duration_minutes or 0) * 60
                    msg = "灌溉已启动（模拟）"
                else:
                    self.state["pump_status"] = False
                    self.state["irrigation_duration"] = 0
                    msg = "灌溉已停止（模拟）"
                self.state["last_update"] = datetime.now().isoformat()
            return {"status": "success", "message": msg, "pump_status": self.state["pump_status"]}
        else:
            # 真实生效：通过 MQTT 下发控制指令，并乐观更新本地状态（等待 ESP32 上报校正）
            if self.mqtt_bridge is not None:
                self.mqtt_bridge.publish_control(action, duration_minutes)
            with self._lock:
                if action == "start":
                    self.state["pump_status"] = True
                    self.state["irrigation_duration"] = (duration_minutes or 0) * 60
                    msg = "灌溉指令已下发（MQTT）"
                else:
                    self.state["pump_status"] = False
                    self.state["irrigation_duration"] = 0
                    msg = "停止指令已下发（MQTT）"
                self.state["last_update"] = datetime.now().isoformat()
            return {"status": "success", "message": msg, "pump_status": self.state["pump_status"]}

    # ---------- 真实模式自动灌溉决策 ----------
    def _maybe_auto_irrigate_real(self):
        s = self.state
        if not s.get("auto_irrigation_enabled", True):
            return
        if s["pump_status"]:
            return
        if s["soil_moisture"] < AUTO_IRRIGATION_THRESHOLD:
            if self.mqtt_bridge is not None:
                self.mqtt_bridge.publish_control("start", None)
            s["pump_status"] = True
            s["irrigation_duration"] = 0
            s["last_update"] = datetime.now().isoformat()


# ============================================================
# MQTT 桥接：订阅传感器上报，下发控制指令
# ============================================================
class MQTTBridge:
    """订阅 farm/device01/# ，把真实传感器数据喂给 DataHub；发布控制指令。"""

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
        data = self._parse(payload)

        if topic.endswith("/status"):
            # 心跳 / 状态主题，可能携带泵状态
            if isinstance(data, dict):
                if "pump_status" in data:
                    self.hub.mqtt_update(pump_status=bool(data["pump_status"]))
            return

        fields = {}
        if isinstance(data, dict):
            # 聚合 JSON 包：{"soil_moisture":35, "temperature":28, ...}
            alias = {
                "soil_moisture": "soil_moisture",
                "soil": "soil_moisture",
                "temperature": "temperature",
                "temp": "temperature",
                "humidity": "humidity",
            }
            for k, v in data.items():
                target = alias.get(k)
                if target and isinstance(v, (int, float)):
                    fields[target] = float(v)
        else:
            # 单值主题
            t = topic.split("/")[-1]
            mapping = {
                "soil_moisture": "soil_moisture",
                "soil": "soil_moisture",
                "temperature": "temperature",
                "temp": "temperature",
                "humidity": "humidity",
            }
            if t in mapping and isinstance(data, (int, float)):
                fields[mapping[t]] = float(data)

        if fields:
            self.hub.mqtt_update(**fields)

    @staticmethod
    def _parse(payload):
        """宽容解析：尝试 JSON，再尝试纯数字，否则返回原字符串。"""
        try:
            return json.loads(payload)
        except (ValueError, TypeError):
            try:
                return float(payload)
            except (ValueError, TypeError):
                return payload

    def publish_control(self, action, duration_minutes=None):
        if self.client is None:
            return
        topic = f"{MQTT_BASE_TOPIC}/control/irrigation"
        payload = {"action": action}
        if duration_minutes:
            payload["duration_minutes"] = duration_minutes
        try:
            self.client.publish(topic, json.dumps(payload), qos=0)
            print(f"[MQTT] 控制指令下发 {topic} <- {payload}")
        except Exception as e:
            print(f"[MQTT] 控制指令发布失败: {e}")


# ============================================================
# Flask 应用与路由（对外接口保持 v1.x 兼容）
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
        duration_minutes = data.get('duration_minutes')
        if not action:
            return jsonify({"error": "缺少action参数"}), 400
        result = hub.control_pump(action, duration_minutes)
        code = 200 if result.get("status") == "success" else 400
        return jsonify(result), code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/system-status', methods=['GET'])
def get_system_status():
    try:
        return jsonify({
            "system_running": True,
            "uptime_seconds": int(time.time() - START_TIME),   # 修复：真实运行时长
            "data_points_collected": len(hub.history),
            "simulation_active": hub.running,
            "server_time": datetime.now().isoformat(),
            "api_version": "2.0.0",
            "data_source_mode": DATA_SOURCE,
            "data_source": hub.snapshot().get("data_source"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/', methods=['GET'])
def api_info():
    return jsonify({
        "name": "数字孪生灌溉系统 - 数据API",
        "version": "2.0.0",
        "endpoints": {
            "GET /api/realtime-data": "获取实时传感器数据",
            "GET /api/historical-data?hours=24": "获取历史数据",
            "GET /api/prediction?minutes=30": "获取土壤湿度预测数据",
            "POST /api/control-pump": "控制水泵灌溉",
            "GET /api/system-status": "获取系统运行状态",
        },
        "data_format": "JSON",
        "data_source_mode": DATA_SOURCE,
        "mqtt_broker": f"{MQTT_HOST}:{MQTT_PORT}" if HAS_MQTT else "未安装 paho-mqtt",
    })


if __name__ == '__main__':
    print("🌱 数字孪生灌溉系统数据服务器 v2.0 启动中...")
    print(f"   数据源模式: DATA_SOURCE={DATA_SOURCE}")
    print(f"   MQTT: {MQTT_HOST}:{MQTT_PORT} (基础主题 {MQTT_BASE_TOPIC})")
    print("📡 API服务地址: http://0.0.0.0:5000")
    print("📖 API文档: http://0.0.0.0:5000/")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
