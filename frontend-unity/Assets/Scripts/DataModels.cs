using System;

/// <summary>
/// 数据模型定义：与 Flask 服务器返回的 JSON 字段一一对应。
///
/// 重要说明（Unity JsonUtility 规则）：
/// 1. 所有数据类必须标记 [Serializable]；
/// 2. 所有字段必须为 public（或 [SerializeField]）；
/// 3. 字段名必须与 JSON 键名完全一致（snake_case 原样保留，不要改成驼峰）；
/// 4. JsonUtility 不支持顶层数组/字典，但支持对象内嵌数组（如 data_points[]）。
/// </summary>

/// <summary>实时数据（GET /api/realtime-data 响应体）</summary>
[Serializable]
public class RealtimeData
{
    public float  soil_moisture;           // 土壤湿度（%）
    public float  temperature;             // 温度（℃）
    public float  humidity;                // 空气湿度（%）
    public bool   pump_status;             // 水泵是否运行
    public int    irrigation_duration;     // 灌溉已持续秒数
    public string last_update;             // 最后更新时间（ISO 8601 字符串）
    public bool   auto_irrigation_enabled; // 自动灌溉是否开启
    public string data_source;             // 数据来源："simulated" 或 "mqtt"
}

/// <summary>历史数据点（/api/historical-data 中 data_points 数组的单个元素）</summary>
[Serializable]
public class HistoricalDataPoint
{
    public string timestamp;       // 时间戳
    public float  soil_moisture;   // 土壤湿度（%）
    public float  temperature;     // 温度（℃）
    public float  humidity;        // 空气湿度（%）
    public bool   pump_status;     // 水泵状态
}

/// <summary>历史数据（GET /api/historical-data 响应体）</summary>
[Serializable]
public class HistoricalData
{
    public HistoricalDataPoint[] data_points;    // 历史数据点数组
    public int total_points;                     // 数据点总数
    public int query_range_hours;                // 查询的时间范围（小时）
}

/// <summary>预测数据点（/api/prediction 中 predictions 数组的单个元素）</summary>
[Serializable]
public class PredictionPoint
{
    public string future_time;                 // 预测的时间点
    public int    minutes_ahead;               // 距当前时间多少分钟
    public float  predicted_soil_moisture;     // 预测的土壤湿度（%）
    public float  confidence;                  // 置信度（0~1）
}

/// <summary>预测数据（GET /api/prediction 响应体）</summary>
[Serializable]
public class PredictionData
{
    public PredictionPoint[] predictions;             // 预测点数组
    public RealtimeData      current_conditions;      // 当前工况（结构同 RealtimeData）
    public int               prediction_horizon_minutes; // 预测总时长（分钟）
    public string            algorithm_info;          // 预测算法说明
}

/// <summary>系统状态（GET /api/system-status 响应体）</summary>
[Serializable]
public class SystemStatus
{
    public bool   system_running;           // 系统是否运行
    public int    uptime_seconds;           // 系统已运行秒数
    public int    data_points_collected;    // 已采集数据点数
    public bool   simulation_active;        // 模拟器是否激活
    public string server_time;              // 服务器时间
    public string api_version;              // API 版本号
    public string data_source;              // 数据来源："simulated" 或 "mqtt"
}

/// <summary>水泵控制请求（POST /api/control-pump 请求体）</summary>
[Serializable]
public class ControlRequest
{
    public string action;            // 动作："start" 启动 / "stop" 停止 / "auto" 切换自动模式
    public int    duration_minutes;  // 启动灌溉时的时长（分钟，1~30；stop/auto 时为 0）
}

/// <summary>水泵控制响应（POST /api/control-pump 响应体）</summary>
[Serializable]
public class ControlResponse
{
    public string status;       // 状态："success" 或 "error"
    public string message;      // 服务器提示信息（中文）
    public bool   pump_status;  // 操作后的水泵状态
}
