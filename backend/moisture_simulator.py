#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三通道土壤墒情模拟器（物理水平衡模型 v2）。

替换原「随机增减 + 恒定 80%」的简化逻辑。新版按真实农田的**水量平衡**推进：

    每分钟含水量变化 = 灌溉 + 降雨 − 作物蒸散(ET) − 深层渗漏

各部分都不是拍脑袋的常数：

1. **蒸散 ET**：由太阳高度角驱动（夜里几乎为 0，正午最强），再按气温和云量修正。
   用大连的经纬度算太阳赤纬与时角，所以曲线会随季节变化，也有昼夜节律。
2. **深层渗漏**：含水量超过田间持水量（FC）后，多余的水按土壤质地的时间常数下渗。
   沙土渗得快（分钟级），黏土渗得慢（小时级）——这是三种地块表现不同的主要原因。
3. **灌溉**：水泵开启时按流量抬升含水量；达到「补充目标」自动停泵（失效保护：
   连续抽水超过上限强制停，防止把地淹了）。
4. **降雨**：从本机上游 `/api/weather` 取真实预报（温度、降雨概率、天气现象），
   按概率生成阵雨，雨天同时压低蒸散。取不到就用季节气候值兜底，不阻塞主循环。

三块地的土壤质地、作物系数、灌溉触发点各不相同，所以三条曲线不会重合成一条直线。

协议与旧版完全兼容：
  订阅 `{BASE_TOPIC}/control`，接受 {"action":"start|stop|auto","channel":1-3|"all","duration_minutes":N}
  发布 `{BASE_TOPIC}/sensors`，内容 {"soil_1..3","pump_1..3","simulated","timestamp"}

与旧版的**唯一不兼容点**：旧版用 AUTO_THRESHOLD（30%）统一触发、AUTO_TARGET（62%）
统一停泵；新版改为每块地按土壤质地算自己的触发点（`Soil.mad`）和补充目标
（`Soil.refill_target`）。设置旧的 AUTO_THRESHOLD / AUTO_TARGET 不再生效（启动时会
打印提示），这样三块地才有不同表现，也才不会都冲到同一个上限。

用法：
    python3 moisture_simulator.py            # 正常运行
    python3 moisture_simulator.py --selftest # 离线跑 7 天，打印轨迹（不连 MQTT）
"""

import json
import math
import os
import random
import signal
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

# ── 连接与发布 ──
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "farm/device01")
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL", "5"))

# ── 地理位置（决定日照曲线）──
# 变量名与 irrigation_server.py 的 LATITUDE / LONGITUDE 保持一致；
# 同时兼容带 FARM_ 前缀的写法，方便与其它服务放在同一个 env 文件里。
def _env2(primary: str, alias: str, default: str) -> str:
    return os.getenv(primary) or os.getenv(alias) or default


LATITUDE = float(_env2("LATITUDE", "FARM_LATITUDE", "38.91"))      # 大连
LONGITUDE = float(_env2("LONGITUDE", "FARM_LONGITUDE", "121.61"))
TZ_OFFSET_HOURS = float(_env2("TZ_OFFSET_HOURS", "FARM_TZ_OFFSET", "8"))

# ── 蒸散参数 ──
# 夏季晴天正午的参考蒸散强度（mm/h）。华北露地作物实测约 0.5~0.8。
ET0_PEAK_MM_PER_HOUR = float(os.getenv("ET0_PEAK_MM_PER_HOUR", "0.62"))
# 1 个含水率百分点对应 1 mm 水（根区 200mm × 持水差 10%vol ≈ 20mm，量级一致）
POINTS_PER_MM = 1.0

# ── 天气 ──
WEATHER_URL = os.getenv("WEATHER_URL", "http://127.0.0.1:5000/api/weather")
WEATHER_POLL_SEC = float(os.getenv("WEATHER_POLL_SEC", "600"))
WEATHER_TIMEOUT = float(os.getenv("WEATHER_TIMEOUT", "4"))

# ── 运行限制 ──
MAX_PUMP_MIN = float(os.getenv("MAX_PUMP_MIN", "90"))   # 单次连续抽水上限，防淹
AUTO_RESUME_SEC = float(os.getenv("AUTO_RESUME_SEC", "60"))  # 手动操作结束后多久恢复自动
SELFTEST_DAYS = float(os.getenv("SELFTEST_DAYS", "7"))

# 天气现象 → 云量（0 晴 1 全阴），用于压低蒸散
CLOUD_BY_TEXT = {
    "晴": 0.08, "多云": 0.40, "阴": 0.68, "雾": 0.60,
    "小雨": 0.80, "中雨": 0.88, "大雨": 0.92, "暴雨": 0.95,
    "阵雨": 0.82, "雷阵雨": 0.86, "雪": 0.85, "小雪": 0.80,
}

# 旧版用这两个环境变量控制自动灌溉，新版改成按土壤质地各自算，留着会让人误以为有效。
_LEGACY_ENV = [k for k in ("AUTO_THRESHOLD", "AUTO_TARGET") if os.getenv(k)]


@dataclass
class Soil:
    """一块地的水文参数。数值取华北平原常见质地。"""

    name: str
    field_capacity: float     # 田间持水量（含水率 %）——超过就往下渗
    wilting_point: float      # 凋萎点（含水率 %）——到这儿作物吸不到水
    sat: float                # 饱和含水率（%）
    drain_tau_min: float      # 深层渗漏时间常数（分钟），沙土小、黏土大
    kc: float                 # 作物系数，决定蒸散强弱
    rise_per_min: float       # 水泵开启时含水率抬升速度（点/分钟）
    mad_frac: float = 0.5     # 允许消耗比例：低于 pwp+frac*(fc-pwp) 就开灌

    @property
    def mad(self) -> float:
        """灌溉触发点（管理允许亏缺下限）。"""
        return self.wilting_point + self.mad_frac * (self.field_capacity - self.wilting_point)

    @property
    def refill_target(self) -> float:
        """灌溉补充目标：略高于田间持水量，多余部分会自然渗走。"""
        return self.field_capacity + 3.0


SOILS = [
    # 1 号地：沙壤土，漏水漏肥，渗得快，蒸发也快，所以要勤浇少量
    Soil("沙壤土", field_capacity=48.0, wilting_point=20.0, sat=100.0,
         drain_tau_min=22.0, kc=0.88, rise_per_min=0.36, mad_frac=0.50),
    # 2 号地：壤土，保水适中，是当地主栽类型
    Soil("壤土", field_capacity=58.0, wilting_point=22.0, sat=100.0,
         drain_tau_min=85.0, kc=1.00, rise_per_min=0.30, mad_frac=0.50),
    # 3 号地：黏壤土，保水强、渗得慢，浇多了容易积水
    Soil("黏壤土", field_capacity=66.0, wilting_point=24.0, sat=100.0,
         drain_tau_min=240.0, kc=1.05, rise_per_min=0.25, mad_frac=0.50),
]

# 初始含水率（各自处在不同消耗阶段，这样三条曲线一开始就不同步）
INIT_MOISTURE = [44.0, 39.8, 47.0]


# ============================================================
# 日照与蒸散
# ============================================================


def solar_factor(ts: float) -> float:
    """太阳高度角的正弦（0~1）。夜里为 0，正午最大。"""
    dt = datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=TZ_OFFSET_HOURS)
    day_of_year = dt.timetuple().tm_yday
    # 太阳赤纬（度）
    decl = 23.45 * math.sin(math.radians(360.0 * (284 + day_of_year) / 365.0))
    # 真太阳时（近似：忽略均时差）
    solar_time = dt.hour + dt.minute / 60.0 + dt.second / 3600.0 + (LONGITUDE - 120.0) / 15.0
    hour_angle = 15.0 * (solar_time - 12.0)
    lat_r = math.radians(LATITUDE)
    decl_r = math.radians(decl)
    ha_r = math.radians(hour_angle)
    sin_elev = (math.sin(lat_r) * math.sin(decl_r)
                + math.cos(lat_r) * math.cos(decl_r) * math.cos(ha_r))
    if sin_elev <= 0:
        return 0.0
    return sin_elev ** 1.2


class Weather:
    """从本机上游取真实天气预报；失败则用季节气候值兜底。"""

    def __init__(self) -> None:
        self.temp_c = 20.0
        self.rain_prob = 10.0
        self.cloud = 0.35
        self.text = "未知"
        self.ok = False
        self.updated_at = 0.0
        self._rain_until = 0.0
        self._rain_rate = 0.0

    def poll(self, now: float) -> None:
        if now - self.updated_at < WEATHER_POLL_SEC:
            return
        self.updated_at = now
        try:
            req = urllib.request.Request(WEATHER_URL, method="GET")
            with urllib.request.urlopen(req, timeout=WEATHER_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            fc = (data.get("forecast") or [{}])[0]
            self.temp_c = float(fc.get("temperature_c", self.temp_c))
            self.rain_prob = float(fc.get("rain_probability_percent", self.rain_prob))
            self.text = str(fc.get("weather") or self.text)
            self.cloud = CLOUD_BY_TEXT.get(self.text, 0.45)
            self.ok = True
        except Exception as exc:  # noqa: BLE001
            # 取不到天气预报不影响模拟，退回季节气候值
            self.ok = False
            month = datetime.now().month
            self.temp_c = {12: -2, 1: -4, 2: 0, 3: 7, 4: 14,
                           5: 20, 6: 24, 7: 27, 8: 26,
                           9: 21, 10: 14, 11: 6}.get(month, 20)
            self.cloud = 0.40
            self.rain_prob = 15.0
            self.text = "气候值"
            if now - getattr(self, "_warn_at", 0.0) > 1800:
                self._warn_at = now
                print(f"[weather] 取天气预报失败（{exc}），使用季节气候值", flush=True)

    def maybe_rain(self, now: float, dt_min: float, wet_bias: float = 1.0) -> float:
        """返回本步降雨量（含水率点数）。

        `rain_probability_percent` 是「今天下不下雨」的概率，不是「这一刻下不下」。
        所以按天抽签：一天抽中一次，就在这一天里随机安排一场雨（0.5~3 小时）。
        直接按步长折算会把概率放大几十倍——实测 7 天能下 44mm，而大连 10 月
        整月才 30mm 左右。

        顺序必须是「**先结算日期 → 再看是否正在下雨**」。反过来写（早期版本就是
        那样）时：昨天那场雨若跨越午夜，今天的抽签会一直被「正在下雨」的提前返回
        挡住，拖到雨停才抽；若抽到的雨窗时刻已经过去，这一天就整天下不了雨——
        于是「降雨概率 100%」的日子里也会出现空白天。实测 9 天里有 2 天为 0。
        """
        local = datetime.fromtimestamp(now, timezone.utc) + timedelta(hours=TZ_OFFSET_HOURS)
        today = local.strftime("%Y%m%d")

        # ① 结算日期：无论此刻是否在下雨，都要先把「今天」的签抽掉
        if today != getattr(self, "_rain_day", ""):
            self._rain_day = today
            self._rain_today = (random.random() < (self.rain_prob / 100.0) * wet_bias)
            self._rain_window = random.uniform(0, 20) * 3600 if self._rain_today else 0.0
            self._rain_started = False

        # ② 正在下雨（可能从昨天延续过来），先把这场下完
        if now < self._rain_until:
            return self._rain_rate * dt_min

        # ③ 今天没雨，或今天那场已经下过了
        if not getattr(self, "_rain_today", False) or getattr(self, "_rain_started", False):
            return 0.0

        # ④ 到点了就开场（0.5~3 小时，雨强 1.5~8 mm/h）
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = now - (day_start - timedelta(hours=TZ_OFFSET_HOURS)).timestamp()
        if elapsed >= self._rain_window:
            self._rain_started = True
            self._rain_until = now + random.uniform(30, 180) * 60
            self._rain_rate = random.uniform(1.5, 8.0) / 60.0 * POINTS_PER_MM
            mm = self._rain_rate * (self._rain_until - now) / 60.0 / POINTS_PER_MM
            print(f"[rain] 开始降雨，预计 {int((self._rain_until - now) / 60)} 分钟，"
                  f"强度 {self._rain_rate * 60:.1f} mm/h，本场约 {mm:.1f} mm", flush=True)
            return self._rain_rate * dt_min
        return 0.0

    @property
    def raining(self) -> bool:
        return time.time() < getattr(self, "_rain_until", 0.0)

    def et_multiplier(self) -> float:
        """气温与云量对蒸散的修正。"""
        t = max(0.5, min(1.7, 1.0 + (self.temp_c - 20.0) * 0.028))
        c = max(0.25, 1.0 - 0.6 * self.cloud)
        if self.raining:
            c *= 0.35
        return t * c


# ============================================================
# 单块地的状态
# ============================================================


@dataclass
class Field:
    soil: Soil
    moisture: float
    pump: bool = False
    auto: bool = True
    manual_until: float = 0.0     # 手动灌溉的截止时间（monotonic 秒）
    auto_block_until: float = 0.0  # 手动结束后暂停自动的截止时间
    pump_started: float = 0.0
    irrigation_count: int = 0
    total_et_mm: float = 0.0
    total_irrig_mm: float = 0.0
    total_rain_mm: float = 0.0
    total_drain_mm: float = 0.0

    def step(self, wall: float, mono: float, dt_min: float,
             wx: Weather, auto_enabled: bool) -> None:
        """推进一步。

        两个时间必须分开，混用会算出荒谬结果：
          wall —— 日历时间（unix 秒），用于日照、降雨这些依赖真实时刻的量；
          mono —— 单调时钟（time.monotonic），用于「泵开了多久」这类持续时间。
        曾经把 monotonic 喂给日照公式，结果昼夜节律是按系统运行时长算的，
        白天蒸散几乎不动、夜里反而下降，完全反了。
        """
        s = self.soil

        # ① 降雨（先加，再算渗漏与蒸散）
        rain = wx.maybe_rain(wall, dt_min)
        if rain > 0:
            self.moisture += rain
            self.total_rain_mm += rain / POINTS_PER_MM

        # ② 灌溉
        if self.pump:
            add = s.rise_per_min * dt_min
            self.moisture += add
            self.total_irrig_mm += add / POINTS_PER_MM

        # ③ 蒸散（日照驱动；超过凋萎点才有效蒸散，干了就几乎不蒸）
        elev = solar_factor(wall)
        if self.moisture > s.wilting_point:
            et = (ET0_PEAK_MM_PER_HOUR * elev * s.kc * wx.et_multiplier()
                  * dt_min / 60.0 * POINTS_PER_MM)
            # 越接近凋萎点，作物越难吸水，蒸散按线性衰减
            avail = (self.moisture - s.wilting_point) / max(1e-6, s.field_capacity - s.wilting_point)
            et *= max(0.15, min(1.0, avail))
            self.moisture -= et
            self.total_et_mm += et / POINTS_PER_MM

        # ④ 深层渗漏：超过田间持水量的部分按土壤时间常数下渗
        if self.moisture > s.field_capacity:
            excess = self.moisture - s.field_capacity
            drained = excess * (1.0 - math.exp(-dt_min / max(1e-6, s.drain_tau_min)))
            self.moisture -= drained
            self.total_drain_mm += drained / POINTS_PER_MM

        # ⑤ 物理边界
        self.moisture = max(0.0, min(s.sat, self.moisture))

        # ⑥ 水泵与灌溉逻辑（这里全用 mono，只关心「过了多久」）
        if self.pump:
            reasons = []
            if self.manual_until and mono >= self.manual_until:
                reasons.append("手动时长到")
            if mono - self.pump_started >= MAX_PUMP_MIN * 60:
                reasons.append("达到单次抽水上限")
            if self.moisture >= s.refill_target:
                reasons.append("已达补充目标")
            if reasons:
                self.pump = False
                self.manual_until = 0.0
                self.auto_block_until = mono + AUTO_RESUME_SEC
                print(f"[irrig] {s.name} 停泵（{reasons[0]}，含水率 {self.moisture:.1f}%）",
                      flush=True)

        if (auto_enabled and self.auto and not self.pump
                and mono >= self.auto_block_until
                and self.moisture <= s.mad):
            self.pump = True
            self.pump_started = mono
            self.irrigation_count += 1
            print(f"[irrig] {s.name} 开泵（含水率 {self.moisture:.1f}% ≤ 触发点 {s.mad:.1f}%）",
                  flush=True)

    def control(self, action: str, duration_min: float, now: float) -> str:
        if action == "start":
            if not self.pump:
                self.pump = True
                self.pump_started = now
                self.irrigation_count += 1
            self.manual_until = (now + duration_min * 60) if duration_min > 0 else 0.0
            self.auto = False
            return "已开泵"
        if action == "stop":
            was = self.pump
            self.pump = False
            self.manual_until = 0.0
            self.auto_block_until = now + AUTO_RESUME_SEC
            return "已停泵" if was else "本来就是停的"
        if action == "auto":
            self.auto = True
            return "已恢复自动"
        return "未知指令"


# ============================================================
# 模拟器主体
# ============================================================


class MoistureSimulator:
    def __init__(self) -> None:
        self.fields = [Field(soil=SOILS[i], moisture=INIT_MOISTURE[i]) for i in range(len(SOILS))]
        self.auto_enabled = True
        self.weather = Weather()
        self.running = True
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id="moisture-simulator")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    # ---------- MQTT ----------
    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            raise RuntimeError(f"MQTT connection rejected: {reason_code}")
        client.subscribe(f"{BASE_TOPIC}/control", qos=1)
        for key in _LEGACY_ENV:
            print(f"[warn] 环境变量 {key} 在新版已不生效（改为按土壤质地各自计算），可以删掉",
                  flush=True)
        print(f"[mqtt] 已连接 {MQTT_HOST}:{MQTT_PORT}，订阅 {BASE_TOPIC}/control", flush=True)

    def on_message(self, client, userdata, message) -> None:
        try:
            cmd = json.loads(message.payload.decode("utf-8", "ignore"))
        except (ValueError, TypeError):
            print("[mqtt] 忽略非 JSON 控制指令", flush=True)
            return
        if not isinstance(cmd, dict):
            return
        action = str(cmd.get("action", "")).lower()
        raw_ch = cmd.get("channel", "all")
        duration = max(0.0, float(cmd.get("duration_minutes") or 0))
        now = time.monotonic()

        if raw_ch in ("all", "0", 0, None):
            targets = range(len(self.fields))
        else:
            try:
                targets = [int(raw_ch) - 1]
            except (TypeError, ValueError):
                print(f"[mqtt] 忽略非法通道 {raw_ch!r}", flush=True)
                return

        if action == "auto":
            self.auto_enabled = True
            for i in range(len(self.fields)):
                self.fields[i].auto = True
            print("[mqtt] 切换到自动灌溉模式", flush=True)
            return

        for i in targets:
            if 0 <= i < len(self.fields):
                msg = self.fields[i].control(action, duration, now)
                print(f"[mqtt] {self.fields[i].soil.name} {action} -> {msg}", flush=True)

    # ---------- 主循环 ----------
    def step_all(self, wall: float, mono: float, dt_min: float) -> None:
        self.weather.poll(wall)
        for f in self.fields:
            f.step(wall, mono, dt_min, self.weather, self.auto_enabled)

    def payload(self) -> dict:
        out = {
            "simulated": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        for i, f in enumerate(self.fields):
            # 真实电容式土壤传感器有 ±0.1~0.3% 的读数抖动，分辨率 0.1%。
            # 只输出 1 位小数且不留噪声，会看起来像卡住不动——照实模拟抖动。
            noisy = f.moisture + random.uniform(-0.06, 0.06)
            out[f"soil_{i + 1}"] = round(max(0.0, noisy), 1)
            out[f"pump_{i + 1}"] = bool(f.pump)
        return out

    def publish(self) -> None:
        body = json.dumps(self.payload(), separators=(",", ":"), ensure_ascii=False)
        info = self.client.publish(f"{BASE_TOPIC}/sensors", body, qos=1)
        info.wait_for_publish(timeout=3)
        print(body, flush=True)

    def stop(self, *_args) -> None:
        self.running = False

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        last = time.monotonic()
        try:
            while self.running:
                time.sleep(PUBLISH_INTERVAL)
                mono = time.monotonic()
                wall = time.time()
                dt_min = max(0.01, min(10.0, (mono - last) / 60.0))
                last = mono
                self.step_all(wall, mono, dt_min)
                self.publish()
        finally:
            self.client.loop_stop()
            self.client.disconnect()


# ============================================================
# 离线自检：不连 MQTT，跑若干天看轨迹是否合理
# ============================================================


def selftest(days: float = SELFTEST_DAYS) -> int:
    sim = MoistureSimulator()
    sim.auto_enabled = True

    step_min = 5.0
    total_steps = int(days * 24 * 60 / step_min)

    # 从今天 00:00 起算，这样「第 N 天」正好对齐自然日，
    # 逐小时的昼夜节律才对得上「几点钟」。
    day0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    wall0 = day0.timestamp()
    mono0 = 1000.0            # 虚拟单调时钟，与墙钟解耦，专门算「泵开了多久」

    sim.weather.poll(wall0)

    print("=" * 74)
    print(f"离线自检：模拟 {days:g} 天，步长 {step_min:g} 分钟（不连 MQTT）")
    print(f"起点：{day0.strftime('%Y-%m-%d %H:%M')}（自然日对齐）")
    print("=" * 74)
    for f in sim.fields:
        print("  %-6s FC=%.0f 凋萎点=%.0f 触发点=%.1f 补充目标=%.1f 渗漏tau=%.0f分  初始=%.1f"
              % (f.soil.name, f.soil.field_capacity, f.soil.wilting_point,
                 f.soil.mad, f.soil.refill_target, f.soil.drain_tau_min, f.moisture))

    hourly = {i: [] for i in range(len(sim.fields))}
    steps_per_hour = int(60 / step_min)
    steps_per_day = int(24 * 60 / step_min)
    print()
    for k in range(total_steps):
        wall = wall0 + k * step_min * 60
        mono = mono0 + k * step_min * 60
        sim.step_all(wall, mono, step_min)
        if k % steps_per_hour == 0:
            for i, f in enumerate(sim.fields):
                hourly[i].append(f.moisture)
        if k and k % steps_per_day == 0:
            d = k // steps_per_day
            print(f"  —— 第 {d} 天结束 ——")
            for f in sim.fields:
                print("     %-6s 含水率 %5.1f%%  泵=%-5s 累计灌溉 %6.1fmm 蒸散 %6.1fmm 降雨 %5.1fmm"
                      % (f.soil.name, f.moisture, "开" if f.pump else "关",
                         f.total_irrig_mm, f.total_et_mm, f.total_rain_mm))

    print()
    print("=" * 74)
    print("结果统计")
    print("=" * 74)
    ok = True
    for i, f in enumerate(sim.fields):
        vals = hourly[i]
        lo, hi = min(vals), max(vals)
        print("  %-6s 范围 %.1f ~ %.1f  （跨度 %.1f）  灌溉次数 %d  蒸散 %.1fmm  灌溉 %.1fmm  渗漏 %.1fmm"
              % (f.soil.name, lo, hi, hi - lo, f.irrigation_count,
                 f.total_et_mm, f.total_irrig_mm, f.total_drain_mm))
        if hi - lo < 3.0:
            print("         ! 曲线几乎不变，模型可能有问题")
            ok = False
        if lo < 25.0:
            print("         ! 低于 25%（会触发上游 30% 阈值的自动灌溉，避免这样）")
            ok = False
        if hi > 99.0:
            print("         ! 达到饱和，灌溉过量")
            ok = False

    # 打印一小时粒度的趋势条，肉眼确认昼夜节律
    print()
    print("=" * 74)
    print("1 号地逐日走势（每小时一个字符，·低 ▁▂▃▄▅▆▇█ 高）")
    print("说明：纵轴用全程统一刻度，这样跨天趋势才看得出来；"
          "每天各自归一化会骗人。")
    print("=" * 74)
    blocks = "·▁▂▃▄▅▆▇█"
    vals = hourly[0]
    g_lo, g_hi = min(vals), max(vals)
    span = max(0.1, g_hi - g_lo)
    for d in range(int(days)):
        chunk = vals[d * 24:(d + 1) * 24]
        if not chunk:
            continue
        line = "".join(blocks[min(8, int((v - g_lo) / span * 8.99))] for v in chunk)
        print("  第%2d天 %s  (%.1f~%.1f%%)" % (d + 1, line, min(chunk), max(chunk)))
    print("  全程 %.1f ~ %.1f%%" % (g_lo, g_hi))

    # 抽一天逐小时打印真实数值，确认昼夜节律方向对
    print()
    print("=" * 74)
    print("第 2 天逐小时真实含水率（确认「白天蒸发下降、夜间平缓」）")
    print("=" * 74)
    day2 = vals[24:48]
    for h, v in enumerate(day2):
        bar = "█" * int((v - g_lo) / span * 40)
        print("   %02d:00  %5.1f%%  %s" % (h, v, bar))


    print()
    print("自检结论：", "通过" if ok else "有异常，见上方 ! 提示")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    MoistureSimulator().run()
