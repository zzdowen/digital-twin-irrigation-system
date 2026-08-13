using System;

/// <summary>
/// 数据模型定义 v3.0.0：3 路独立灌溉，与《系统接口契约v3.md》完全一致。
///
/// Unity JsonUtility 规则：
/// 1. 所有数据类必须标记 [Serializable]；
/// 2. 所有字段必须为 public（或 [SerializeField]）；
/// 3. 字段名必须与 JSON 键名完全一致（snake_case 原样保留）；
/// 4. 数组字段直接对应 JSON 数组；嵌套数组（如 channels[][]）Unity 2018.3+ 支持；
///    若遇到解析问题，可替换为 Newtonsoft.Json 的 JArray 手动解析。
/// </summary>

/// <summary>单路数据（一个花盆）</summary>
[Serializable]
public class ChannelData
{
    public int   channel;              // 花盆编号 1/2/3
    public float soil_moisture;        // 土壤湿度（%）
    public bool  pump_status;          // 水泵是否运行
    public int   irrigation_duration;  // 灌溉已持续秒数
}

/// <summary>实时数据（GET /api/realtime-data 响应体，v3.0.0）</summary>
[Serializable]
public class RealtimeData
{
    public ChannelData[] channels;             // 3 路，下标 0-2 对应花盆 1-3
    public float  soil_moisture_avg;           // 3 路平均湿度（%）
    public int    active_pumps;                // 运行中的水泵数量
    public string data_source;                 // "simulated" 或 "mqtt"
    public bool   auto_irrigation_enabled;     // 自动灌溉是否开启
    public string last_update;                 // 最后更新时间
}

/// <summary>历史数据点（/api/historical-data 中每路的单个元素）</summary>
[Serializable]
public class HistoricalDataPoint
{
    public string timestamp;        // 时间戳
    public float  soil_moisture;    // 土壤湿度（%）
    public bool   pump_status;      // 水泵状态
}

/// <summary>历史数据（GET /api/historical-data 响应体，v3.0.0）</summary>
[Serializable]
public class HistoricalData
{
    public HistoricalDataPoint[][] channels;  // 嵌套数组：下标 0-2 = 花盆 1-3，每项为该路的时间序列
    public int total_points;                  // 每路数据点总数
    public int query_range_hours;             // 查询时间范围（小时）
}

/// <summary>预测数据点（/api/prediction 中每路的单个元素）</summary>
[Serializable]
public class PredictionPoint
{
    public string future_time;               // 预测时间点
    public int    minutes_ahead;             // 距当前多少分钟
    public float  predicted_soil_moisture;   // 预测土壤湿度（%）
    public float  confidence;                // 置信度 0~1
}

/// <summary>天气预报条目（OpenClaw AI 查询，可选）</summary>
[Serializable]
public class WeatherEntry
{
    public string date;                     // 日期 YYYY-MM-DD
    public string weather;                  // 天气描述（中文）
    public float  temperature_c;            // 温度 ℃
    public float  rain_probability_percent; // 降雨概率 0~100
}

/// <summary>预测数据（GET /api/prediction 响应体，v3.0.0）</summary>
[Serializable]
public class PredictionData
{
    public PredictionPoint[][] predictions;  // 嵌套数组：下标 0-2 = 花盆 1-3
    public RealtimeData      current_conditions;          // 当前工况
    public int               prediction_horizon_minutes;  // 预测总时长
    public string            algorithm_info;              // 预测算法说明
    public WeatherEntry[]    weather_forecast;            // 天气预测（可能为 null）
}

/// <summary>天气状态（GET /api/weather 与 system-status.weather 同构）</summary>
[Serializable]
public class WeatherStatus
{
    public bool          enabled;       // 天气感知是否可用
    public string        city;          // 城市
    public WeatherEntry[] forecast;     // 天气预报数组（可能为 null）
    public string        updated_at;    // 更新时间
    public string        last_error;    // 最近错误（无则 null）
}

/// <summary>系统状态（GET /api/system-status 响应体，v3.0.0）</summary>
[Serializable]
public class SystemStatus
{
    public bool   system_running;          // 系统是否运行
    public int    uptime_seconds;          // 已运行秒数
    public int    data_points_collected;   // 已采集历史点数
    public int    channel_count;           // 通道数（3）
    public string server_time;             // 服务器时间
    public string api_version;             // API 版本
    public string data_source_mode;        // 数据源模式：auto/simulated/mqtt
    public string data_source;             // 当前数据来源
    public WeatherStatus weather;          // 天气状态（可能为 null）
}

/// <summary>水泵控制请求（POST /api/control-pump 请求体，v3.0.0）</summary>
[Serializable]
public class ControlRequest
{
    public string action;            // "start" / "stop" / "auto"
    public int    channel;           // 1/2/3；0 = 全部（对应后端 "all"）
    public int    duration_minutes;  // 灌溉时长（分钟，仅 start 有效）
}

/// <summary>水泵控制响应（POST /api/control-pump 响应体）</summary>
[Serializable]
public class ControlResponse
{
    public string status;        // "success" / "error"
    public string message;       // 服务器提示信息（中文）
    public int    channel;       // 单路控制时返回的目标花盆
    public int[]  channels;      // 全部控制时返回的目标花盆列表
    public bool[] pump_status;   // 操作后的水泵状态数组
    public bool   auto_mode;     // auto 命令时返回
}
