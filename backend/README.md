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
| `WeatherProvider` | OpenClaw AI 联网查询天气，融合预测 | ✅ 完成（可选） |
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
export WEATHER_ENABLED=true          # OpenClaw 天气感知开关
export WEATHER_CITY=大连             # 天气查询城市
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

## 部署状态

- 云端：已部署（systemd 服务 `irrigation-server.service`，地址见内部部署文档）
- 本地：http://localhost:5000
