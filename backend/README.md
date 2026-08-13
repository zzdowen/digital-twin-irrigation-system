# 🌐 Python 后端（数据服务器 v3.0.0）

数字孪生灌溉系统的后端服务：3 路独立灌溉数据中枢，负责数据接收（MQTT）、处理、预测、控制，并通过 HTTP API 供 Unity 前端调用。

## 模块结构

`irrigation_server.py` 单文件包含全部模块：

| 模块 | 功能 | 状态 |
|------|------|------|
| `SimulationModel` | 3 路物理模拟（蒸发/灌溉/自动触发） | ✅ 完成 |
| `DataHub` | 数据中枢：数据源决策、历史、预测、控制 | ✅ 完成 |
| `MQTTBridge` | 订阅 `farm/device01/#`，下发控制指令 | ✅ 完成 |
| `WeatherProvider` | OpenClaw AI 联网查询天气，融合预测 | ✅ 完成（可选） |
| Flask 路由 | 6 个 HTTP API（见《系统接口契约v3.md》） | ✅ 完成 |

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
export DATA_SOURCE=auto          # auto | simulated | mqtt
export MQTT_HOST=127.0.0.1       # MQTT broker 地址
export WEATHER_ENABLED=true      # OpenClaw 天气感知开关
export WEATHER_CITY=大连         # 天气查询城市
```

## API 接口（v3.0.0，3 路）

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/realtime-data` | GET | 3 路实时数据（channels 数组） |
| `/api/historical-data?hours=24` | GET | 3 路历史数据 |
| `/api/prediction?minutes=30` | GET | 3 路湿度预测（融合天气） |
| `/api/control-pump` | POST | 水泵控制（channel 1-3/all） |
| `/api/system-status` | GET | 系统状态 |
| `/api/weather` | GET | 天气感知数据（可选） |

完整契约见 `docs/系统接口契约v3.md`。

## 部署状态

- 云端：已部署（systemd 服务 `irrigation-server.service`，地址见内部部署文档）
- 本地：http://localhost:5000
