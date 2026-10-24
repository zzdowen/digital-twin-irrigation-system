# 🌐 Python 后端（数据服务器 v3.1.0）

数字孪生灌溉系统的后端服务：3 路独立灌溉数据中枢，负责数据接收（MQTT）、处理、预测、控制，并通过 HTTP API 供 Unity 前端调用。

## 模块结构

`irrigation_server.py` 单文件包含全部模块：

| 模块 | 功能 | 状态 |
|------|------|------|
| `SimulationModel` | 3 路物理模拟（蒸发/灌溉/自动触发） | ✅ 完成 |
| `DataHub` | 数据中枢：数据源决策、历史、预测、控制、事件跟踪 | ✅ 完成 |
| `HistoryStore` | SQLite 历史持久化（重启不丢历史曲线） | ✅ 完成 |
| `IrrigationStats` | 灌溉事件统计（次数/时长/用水量/节水率） | ✅ 完成 |
| `PredictionModel` | 加权回归 + 指数平滑 + 留一法自评估 | ✅ 完成 |
| `MQTTBridge` | 订阅 `farm/device01/#`，下发控制指令 | ✅ 完成 |
| `WeatherProvider` | Open-Meteo 获取天气，融合预测 | ✅ 完成（可选） |
| Flask 路由 | 7 个 HTTP API（见《系统接口契约v3.md》） | ✅ 完成 |

## 数据源模式（环境变量 DATA_SOURCE）

| 模式 | 行为 |
|------|------|
| `auto`（默认） | MQTT 收到真实数据用真实值，超时 30 秒回退模拟 |
| `simulated` | 纯内置模拟 |
| `mqtt` | 纯真实传感器 |

## 快速开始

```bash
pip install flask flask-cors paho-mqtt
python irrigation_server.py
```

环境变量（均可选）：

```bash
export DATA_SOURCE=auto              # auto | simulated | mqtt
export MQTT_HOST=127.0.0.1           # MQTT broker 地址
export WEATHER_ENABLED=true          # Open-Meteo 天气感知开关
export WEATHER_CITY=大连             # 天气显示城市
export WEATHER_REFRESH_SEC=7200       # 默认每 2 小时刷新
export LATITUDE=38.9140
export LONGITUDE=121.6147
export HISTORY_DB_PATH=/path/db      # SQLite 数据库路径（默认脚本同目录）
export PUMP_FLOW_RATE_LPM=1.5        # 水泵流量（升/分钟，用水量估算）
export TRADITIONAL_MINUTES_PER_DAY=30  # 传统灌溉基线（分钟/天/路，节水率对比）
```

## API 接口（v3.1.0，3 路）

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/realtime-data` | GET | 3 路实时数据（channels 数组） |
| `/api/historical-data?hours=24` | GET | 3 路历史数据（SQLite 持久化） |
| `/api/prediction?minutes=30` | GET | 3 路预测（加权回归+自评估误差） |
| `/api/control-pump` | POST | 水泵控制（channel 1-3/all） |
| `/api/system-status` | GET | 系统状态（含历史库与统计摘要） |
| `/api/weather` | GET | 天气感知数据（可选） |
| `/api/irrigation-stats` | GET | 灌溉统计（次数/用水量/节水率） |

完整契约见 `docs/系统接口契约v3.md`。

## MQTT 湿度模拟器

运行 `python moisture_simulator.py` 可模拟 3 路土壤湿度数据并发布到 MQTT，主题为
`${MQTT_BASE_TOPIC}/sensors`（默认 `farm/device01`，`deploy/systemd/` 的 drop-in 里
改成 `farm/simulator01`），参数均可通过环境变量配置。

### v2：物理水平衡模型

模拟器已从「随机增减 + 上限写死 80%」升级为按真实农田的**水量平衡**推进：

```
每分钟含水量变化 = 灌溉 + 降雨 − 作物蒸散(ET) − 深层渗漏
```

| 环节 | 怎么算的 |
|------|----------|
| 蒸散 ET | 由太阳高度角驱动（赤纬 + 时角，用 `LATITUDE`/`LONGITUDE` 算），夜里为 0、正午最强；再按气温和云量修正 |
| 深层渗漏 | 超过田间持水量（FC）的部分按土壤质地的时间常数下渗：沙土分钟级、黏土小时级 |
| 灌溉 | 泵开时按 `rise_per_min` 抬升；到 `refill_target` 自动停；连抽超过 `MAX_PUMP_MIN` 强制停（防淹） |
| 降雨 | 读上游 `/api/weather` 的当日降雨概率按天抽签生成阵雨，雨天同时压低蒸散；取不到用季节气候值兜底 |

三块地按土壤质地各配一套参数（沙壤土 / 壤土 / 黏壤土），触发点 `mad = wp + 0.5×(fc−wp)`
和补充目标 `refill_target = fc + 3` 都各不相同，所以三条曲线不会重合成一条线。

**14 天离线自检实测**：沙壤土 38.4~44.0%、壤土 39.8~60.3%、黏壤土 45.0~67.3%；
昼夜节律正确（00:00~07:00 平坦、08:00~17:00 持续下降）。

```bash
python moisture_simulator.py --selftest   # 离线跑 7 天，打印轨迹与结论（不连 MQTT）
```

> **与旧版的不兼容点**：旧版用 `AUTO_THRESHOLD`（30%）统一触发、`AUTO_TARGET`（62%）
> 统一停泵；新版改成按土壤质地各自计算，这两个环境变量不再生效（若仍设置，启动时
> 会打印提示）。MQTT 主题与 payload 格式完全不变。

可调环境变量：`LATITUDE` / `LONGITUDE` / `TZ_OFFSET_HOURS`（决定日照曲线）、
`ET0_PEAK_MM_PER_HOUR`（正午蒸散强度，默认 0.62 mm/h）、`MAX_PUMP_MIN`（默认 90）、
`WEATHER_URL`（默认 `http://127.0.0.1:5000/api/weather`）、`WEATHER_POLL_SEC`（默认 600）。

## 部署状态

- 云端：已部署（systemd 服务 `irrigation-server.service`，地址见内部部署文档）
- 本地：http://localhost:5000
