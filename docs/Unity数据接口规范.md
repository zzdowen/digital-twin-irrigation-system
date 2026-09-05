# 数字孪生灌溉系统 - Unity 数据接口规范 v3.0.0

## 📡 接口概览

**基础地址**: `http://localhost:5000`（本地默认；部署后改为服务器地址，真实 IP 见内部部署文档，勿提交公开仓库）  
**数据格式**: JSON  
**编码**: UTF-8  
**API版本**: 3.0.0  
**通道模型**: 3 路独立灌溉（农田 1/2/3），无温湿度字段

> 完整契约见《系统接口契约v3.md》。所有数组下标 0/1/2 分别对应农田 1/2/3。

---

## 🔄 实时数据接口

```
GET /api/realtime-data
```

```json
{
  "channels": [
    {"channel": 1, "soil_moisture": 45.2, "pump_status": false, "irrigation_duration": 0},
    {"channel": 2, "soil_moisture": 50.1, "pump_status": true,  "irrigation_duration": 120},
    {"channel": 3, "soil_moisture": 38.7, "pump_status": false, "irrigation_duration": 0}
  ],
  "soil_moisture_avg": 44.7,
  "active_pumps": 1,
  "data_source": "simulated",
  "auto_irrigation_enabled": true,
  "last_update": "2026-08-13T23:00:00"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `channels` | array | 3 路数据，下标 0-2 = 农田 1-3 |
| `channels[].channel` | int | 农田编号 1/2/3 |
| `channels[].soil_moisture` | float | 土壤湿度 %（10-80 正常范围） |
| `channels[].pump_status` | bool | 水泵是否运行 |
| `channels[].irrigation_duration` | int | 灌溉已持续秒数 |
| `soil_moisture_avg` | float | 3 路平均湿度 |
| `active_pumps` | int | 运行中的水泵数量 |
| `data_source` | string | `simulated` 模拟 / `mqtt` 真实设备 |
| `auto_irrigation_enabled` | bool | 自动灌溉模式开关 |

**Unity 轮询建议**：每 5 秒一次（`RealtimeDataPoller` 已实现）。

---

## 📊 历史数据接口

```
GET /api/historical-data?hours=24
```

```json
{
  "channels": [
    [{"timestamp": "...", "soil_moisture": 45.2, "pump_status": false}],
    [{"timestamp": "...", "soil_moisture": 50.1, "pump_status": true}],
    [{"timestamp": "...", "soil_moisture": 38.7, "pump_status": false}]
  ],
  "total_points": 288,
  "query_range_hours": 24
}
```

- `channels` 为嵌套数组：`channels[0]` = 农田1 的时间序列（每 5 分钟一个点）
- Unity 端对应 `HistoricalDataPoint[][] channels`

---

## 🔮 预测数据接口

```
GET /api/prediction?minutes=30
```

```json
{
  "predictions": [
    [{"future_time": "...", "minutes_ahead": 5, "predicted_soil_moisture": 44.0, "confidence": 0.95}],
    [...],
    [...]
  ],
  "current_conditions": { "channels": [...], "soil_moisture_avg": 44.7, "data_source": "..." },
  "prediction_horizon_minutes": 30,
  "algorithm_info": "基于历史趋势的土壤湿度线性回归预测模型"
}
```

- `predictions` 为嵌套数组：下标 0-2 = 农田 1-3，每项为预测点数组（每 5 分钟一个点）
- 预测范围限制：5~180 分钟
- `confidence` 随预测时间递减（1.0 → 0.7）

---

## 🎮 水泵控制接口（3 路）

```
POST /api/control-pump
```

### 请求
```json
{"channel": 2, "action": "start", "duration_minutes": 5}
```

| 字段 | 必填 | 取值 | 说明 |
|------|------|------|------|
| `channel` | 否 | 1/2/3/0/"all" | 目标农田；缺省默认 1；0 或 "all" = 全部 |
| `action` | 是 | start/stop/auto | 控制命令 |
| `duration_minutes` | 否 | 1-30 | 仅 start 有效 |

### 响应（单路）
```json
{"status": "success", "message": "灌溉已启动（模拟）", "channel": 2, "channels": [2], "pump_status": [true]}
```

### 调用示例
```bash
# 启动农田2 灌溉 5 分钟
curl -X POST http://localhost:5000/api/control-pump \
  -H "Content-Type: application/json" \
  -d '{"channel": 2, "action": "start", "duration_minutes": 5}'

# 停止全部
curl -X POST http://localhost:5000/api/control-pump \
  -H "Content-Type: application/json" \
  -d '{"channel": "all", "action": "stop"}'
```

---

## 📈 系统状态接口

```
GET /api/system-status
```

```json
{
  "system_running": true,
  "uptime_seconds": 15432,
  "data_points_collected": 145,
  "channel_count": 3,
  "server_time": "...",
  "api_version": "3.0.0",
  "data_source_mode": "auto",
  "data_source": "simulated"
}
```

## 🌤️ 天气感知接口（可选，Open-Meteo）

```
GET /api/weather
```

```json
{
  "enabled": true,
  "city": "大连",
  "forecast": [
    {"date": "2026-08-14", "weather": "阵雨", "temperature_c": 26.6, "rain_probability_percent": 82}
  ],
  "updated_at": "...",
  "last_error": null
}
```

- 服务端启动时立即获取天气，之后默认每 2 小时刷新缓存
- Open-Meteo 请求无需 API Key，经纬度由服务端环境变量配置
- `enabled=false` 或 `forecast=null` 表示不可用（前端隐藏天气显示即可）
- 天气数据同时出现在 `/api/prediction` 的 `weather_forecast` 字段，并参与预测修正（降雨概率高 → 蒸发减缓）
- Unity 数据模型：`WeatherStatus` / `WeatherEntry`（见 DataModels.cs）

---

## 🔧 Unity 对接脚本（已提供，见 `frontend-unity/Assets/Scripts/`）

| 脚本 | 用途 |
|------|------|
| `ApiConfig.cs` | 服务器地址与接口路径配置 |
| `DataModels.cs` | v3 数据模型（`ChannelData[]` 等），字段名与 JSON 完全一致 |
| `IrrigationApiClient.cs` | API 客户端单例，`ControlPump(action, channel, minutes, ...)` |
| `RealtimeDataPoller.cs` | 每 5 秒轮询 + 事件回调 |
| `IrrigationUIController.cs` | 3 路 UI 绑定与控制 |
| `SceneVisualController.cs` | 驱动 3D 模型（土壤颜色/LED/水流粒子） |

---

## ⚠️ 注意事项

1. **数据源切换**：`data_source` 字段区分模拟/真实设备；MQTT 断线 30 秒后自动回退模拟（`auto` 模式）
2. **控制响应**：真实模式下控制指令经 MQTT 下发，响应中 message 带"（MQTT）"标识
3. **错误处理**：Unity 代码需处理网络错误/超时（脚本已内置 10 秒超时与重连提示）
4. **隐私**：服务器真实 IP 属敏感信息，勿提交公开仓库
