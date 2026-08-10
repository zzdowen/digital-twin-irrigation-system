# 🌐 Python 后端

数字孪生灌溉系统的后端服务，负责数据接收、处理、预测和控制。

## 模块规划

| 文件 | 功能 | 状态 |
|------|------|------|
| `模拟数据服务器.py` | Flask 模拟数据服务（已部署） | ✅ 完成 |
| `digital_twin_model.py` | 数字孪生预测模型 | 📝 待开发 |
| `mqtt_client.py` | MQTT 客户端（接收ESP32数据） | 📝 待开发 |
| `control_algorithm.py` | 智能灌溉控制算法 | 📝 待开发 |

## 快速开始

```bash
pip install -r requirements.txt
python 模拟数据服务器.py
```

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/realtime-data` | GET | 实时环境数据 |
| `/api/historical-data?hours=24` | GET | 历史数据 |
| `/api/prediction?minutes=30` | GET | 湿度预测 |
| `/api/control-pump` | POST | 水泵控制 |

## 部署状态

- 云端: 已部署 (systemd 服务，地址见内部部署文档)
- 本地: http://localhost:5000

## 待办
- [ ] MQTT 客户端对接真实硬件数据
- [ ] 数字孪生预测模型（物理模型 + 数据校准）
- [ ] 数据库存储（SQLite/InfluxDB）
- [ ] 控制算法参数优化
