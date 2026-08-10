# 数字孪生灌溉系统 - 数据接口规范

## 📡 接口概览

**基础地址**: `http://localhost:5000`  
**数据格式**: JSON  
**编码**: UTF-8  
**API版本**: 1.0.0

---

## 🔄 实时数据接口

### **接口地址**
```
GET /api/realtime-data
```

### **响应数据格式**
```json
{
  "soil_moisture": 45.2,
  "temperature": 23.5, 
  "humidity": 67.8,
  "pump_status": false,
  "irrigation_duration": 0,
  "last_update": "2026-08-09T14:30:25.123456",
  "auto_irrigation_enabled": true
}
```

### **字段说明**
| 字段名 | 数据类型 | 取值范围 | 说明 |
|--------|----------|----------|------|
| `soil_moisture` | float | 0-100 | 土壤湿度百分比，正常范围10-80 |
| `temperature` | float | -10,50 | 环境温度（摄氏度），模拟温室环境 |
| `humidity` | float | 20-95 | 空气相对湿度百分比 |
| `pump_status` | boolean | true/false | 水泵当前运行状态 |
| `irrigation_duration` | integer | 0-600 | 当前灌溉持续时间（秒） |
| `last_update` | string | ISO8601格式 | 数据最后更新时间戳 |
| `auto_irrigation_enabled` | boolean | true/false | 自动灌溉模式开关 |

### **调用示例**
```bash
curl http://localhost:5000/api/realtime-data
```

### **Unity调用示例**
```csharp
using UnityEngine;
using UnityEngine.Networking;
using System.Collections;

public class RealtimeDataFetcher : MonoBehaviour
{
    private string apiUrl = "http://localhost:5000/api/realtime-data";
    
    IEnumerator GetRealtimeData()
    {
        using (UnityWebRequest request = UnityWebRequest.Get(apiUrl))
        {
            yield return request.SendWebRequest();
            
            if (request.result == UnityWebRequest.Result.Success)
            {
                string json = request.downloadHandler.text;
                RealtimeData data = JsonUtility.FromJson<RealtimeData>(json);
                
                // 更新Unity场景中的数据显示
                Debug.Log($"土壤湿度: {data.soil_moisture}%");
                Debug.Log($"温度: {data.temperature}°C");
                Debug.Log($"水泵状态: {(data.pump_status ? "运行中" : "关闭")}");
            }
            else
            {
                Debug.LogError("数据获取失败: " + request.error);
            }
        }
    }
}

[System.Serializable]
public class RealtimeData
{
    public float soil_moisture;
    public float temperature;
    public float humidity;
    public bool pump_status;
    public int irrigation_duration;
    public string last_update;
    public bool auto_irrigation_enabled;
}
```

---

## 📊 历史数据接口

### **接口地址**
```
GET /api/historical-data?hours=24
```

### **请求参数**
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `hours` | integer | 否 | 24 | 查询多少小时内的历史数据 |

### **响应数据格式**
```json
{
  "data_points": [
    {
      "timestamp": "2026-08-09T14:30:25.123456",
      "soil_moisture": 45.2,
      "temperature": 23.5,
      "humidity": 67.8,
      "pump_status": false
    },
    {
      "timestamp": "2026-08-09T14:25:25.123456", 
      "soil_moisture": 44.8,
      "temperature": 23.3,
      "humidity": 68.1,
      "pump_status": false
    }
  ],
  "total_points": 288,
  "query_range_hours": 24
}
```

### **字段说明**
| 字段名 | 数据类型 | 说明 |
|--------|----------|------|
| `data_points` | array | 数据点数组，每5分钟一个点 |
| `timestamp` | string | ISO8601格式的时间戳 |
| `total_points` | integer | 返回的数据点总数 |
| `query_range_hours` | integer | 实际查询的时间范围 |

### **调用示例**
```bash
# 获取最近24小时数据
curl http://localhost:5000/api/historical-data?hours=24

# 获取最近6小时数据  
curl http://localhost:5000/api/historical-data?hours=6
```

---

## 🔮 预测数据接口

### **接口地址**
```
GET /api/prediction?minutes=30
```

### **请求参数**
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `minutes` | integer | 否 | 30 | 预测未来多少分钟内的湿度变化 |

### **响应数据格式**
```json
{
  "predictions": [
    {
      "future_time": "2026-08-09T15:00:25.123456",
      "minutes_ahead": 30,
      "predicted_soil_moisture": 42.1,
      "confidence": 0.85
    },
    {
      "future_time": "2026-08-09T14:45:25.123456",
      "minutes_ahead": 15, 
      "predicted_soil_moisture": 43.7,
      "confidence": 0.92
    }
  ],
  "current_conditions": {
    "soil_moisture": 45.2,
    "temperature": 23.5,
    "humidity": 67.8,
    "pump_status": false,
    "irrigation_duration": 0,
    "last_update": "2026-08-09T14:30:25.123456",
    "auto_irrigation_enabled": true
  },
  "prediction_horizon_minutes": 30,
  "algorithm_info": "基于物理规律的土壤湿度变化预测模型"
}
```

### **字段说明**
| 字段名 | 数据类型 | 说明 |
|--------|----------|------|
| `predictions` | array | 预测数据点数组 |
| `future_time` | string | 预测时间点（ISO8601格式） |
| `minutes_ahead` | integer | 距离当前时间的分钟数 |
| `predicted_soil_moisture` | float | 预测的土壤湿度值 |
| `confidence` | float | 预测置信度（0-1之间） |
| `prediction_horizon_minutes` | integer | 预测时间范围 |
| `algorithm_info` | string | 预测算法描述 |

### **调用示例**
```bash
# 预测30分钟内的湿度变化
curl http://localhost:5000/api/prediction?minutes=30

# 预测2小时内的湿度变化
curl http://localhost:5000/api/prediction?minutes=120
```

---

## 🎮 水泵控制接口

### **接口地址**
```
POST /api/control-pump
```

### **请求数据格式**
```json
{
  "action": "start",
  "duration_minutes": 5
}
```

### **请求参数说明**
| 字段名 | 类型 | 必填 | 可选值 | 说明 |
|--------|------|------|--------|------|
| `action` | string | 是 | "start"/"stop"/"auto" | 控制命令 |
| `duration_minutes` | integer | 否 | 1-30 | 灌溉持续时间（分钟），仅在action="start"时有效 |

### **动作说明**
- `"start"`: 立即启动灌溉，可指定持续时间
- `"stop"`: 立即停止灌溉
- `"auto"`: 切换到自动模式（系统根据湿度阈值自动控制）

### **响应数据格式**
```json
{
  "status": "success",
  "message": "灌溉已启动",
  "pump_status": true,
  "action": "start"
}
```

### **调用示例**
```bash
# 启动灌溉，持续10分钟
curl -X POST http://localhost:5000/api/control-pump \
  -H "Content-Type: application/json" \
  -d '{"action": "start", "duration_minutes": 10}'

# 停止灌溉
curl -X POST http://localhost:5000/api/control-pump \
  -H "Content-Type: application/json" \
  -d '{"action": "stop"}'

# 切换到自动模式
curl -X POST http://localhost:5000/api/control-pump \
  -H "Content-Type: application/json" \
  -d '{"action": "auto"}'
```

### **Unity控制示例**
```csharp
using UnityEngine;
using UnityEngine.Networking;
using System.Collections;

public class IrrigationController : MonoBehaviour
{
    private string controlUrl = "http://localhost:5000/api/control-pump";
    
    // 启动灌溉
    public void StartIrrigation(int durationMinutes = 5)
    {
        StartCoroutine(ControlPump("start", durationMinutes));
    }
    
    // 停止灌溉
    public void StopIrrigation()
    {
        StartCoroutine(ControlPump("stop", 0));
    }
    
    IEnumerator ControlPump(string action, int durationMinutes = 0)
    {
        ControlRequest request = new ControlRequest
        {
            action = action,
            duration_minutes = durationMinutes
        };
        
        string jsonData = JsonUtility.ToJson(request);
        
        using (UnityWebRequest webRequest = new UnityWebRequest(controlUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(jsonData);
            webRequest.uploadHandler = new UploadHandlerRaw(bodyRaw);
            webRequest.downloadHandler = new DownloadHandlerBuffer();
            webRequest.SetRequestHeader("Content-Type", "application/json");
            
            yield return webRequest.SendWebRequest();
            
            if (webRequest.result == UnityWebRequest.Result.Success)
            {
                Debug.Log("灌溉控制成功: " + webRequest.downloadHandler.text);
            }
            else
            {
                Debug.LogError("控制失败: " + webRequest.error);
            }
        }
    }
}

[System.Serializable]
public class ControlRequest
{
    public string action;
    public int duration_minutes;
}
```

---

## 📈 系统状态接口

### **接口地址**
```
GET /api/system-status
```

### **响应数据格式**
```json
{
  "system_running": true,
  "uptime_seconds": 15432,
  "data_points_collected": 145,
  "simulation_active": true,
  "server_time": "2026-08-09T14:35:12.345678",
  "api_version": "1.0.0"
}
```

---

## 🔧 API部署和测试

### **启动模拟服务器**
```bash
# 安装依赖
pip install flask flask-cors

# 启动服务器
python 模拟数据服务器.py
```

### **服务器启动后的输出示例**
```
🌱 数字孪生灌溉系统模拟数据服务器启动中...
📡 API服务地址: http://localhost:5000
📖 API文档: http://localhost:5000/
⚡ 实时数据接口: http://localhost:5000/api/realtime-data
📊 历史数据接口: http://localhost:5000/api/historical-data?hours=24
🔮 预测数据接口: http://localhost:5000/api/prediction?minutes=30
🎮 水泵控制接口: POST http://localhost:5000/api/control-pump

================================================
🚀 服务器已启动，Unity可以通过以上接口进行联调测试！
================================================
```

### **数据模拟逻辑说明**

#### **温度变化模拟**
- 基于正弦函数模拟昼夜温差
- 峰值在下午2-3点，谷值在凌晨4-5点
- 温度范围：14-30°C

#### **湿度变化模拟**
- 与温度呈负相关
- 加上随机波动模拟真实环境
- 湿度范围：20%-90%

#### **土壤湿度模拟**
- **自然蒸发**：每5秒下降0.1-0.4%，受温度和空气湿度影响
- **灌溉时**：每5秒上升2-4%
- **自动触发**：湿度低于30%时自动开始灌溉
- **自动停止**：灌溉10分钟后自动停止
- **湿度范围**：10%-80%

#### **预测算法**
- 基于当前环境条件的物理模型
- 考虑温度、湿度对蒸发速率的影响
- 预测置信度随预测时间递减

---

## 🚀 快速开始指南

### **第一步：启动服务器**
```bash
cd "项目目录"
python 模拟数据服务器.py
```

### **第二步：测试接口**
```bash
# 测试实时数据
curl http://localhost:5000/api/realtime-data

# 测试控制功能
curl -X POST http://localhost:5000/api/control-pump \
  -H "Content-Type: application/json" \
  -d '{"action": "start", "duration_minutes": 3}'
```

### **第三步：Unity集成**
1. 在Unity场景中添加API调用脚本
2. 使用协程定期获取实时数据（建议每5秒）
3. 实现数据可视化（图表、进度条、3D效果等）
4. 添加控制按钮来操作灌溉系统
5. 实现预测数据的图表显示

### **第四步：调试建议**
- 控制台查看服务器日志，确认数据正常更新
- Unity中使用Debug.Log打印接收到的数据
- 测试各种边界情况（湿度极值、控制冲突等）
- 验证预测数据的合理性

---

## ⚠️ 注意事项

1. **跨域问题**：服务器已配置CORS，Unity可直接调用
2. **数据更新频率**：模拟数据每5秒更新一次，符合比赛要求
3. **控制响应时间**：模拟中控制立即生效，实际硬件响应时间≤3秒
4. **预测范围限制**：最大支持180分钟（3小时）预测
5. **错误处理**：Unity代码中需要添加网络错误处理逻辑