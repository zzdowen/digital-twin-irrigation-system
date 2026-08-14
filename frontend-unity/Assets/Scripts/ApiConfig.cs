using UnityEngine;

/// <summary>
/// API 配置类：集中管理数字孪生灌溉系统的服务器地址与所有接口路径。
/// 修改服务器地址时只需改动 ServerUrl 一处，其余代码无需调整。
///
/// 注意：
/// 1. 服务器为 http 明文地址。在 Unity 编辑器中可直接访问；
///    打包到 Android 真机时需要在 Player Settings → Publishing Settings 勾选
///    "Allow downloads over HTTP"（或在 AndroidManifest 中为 application 添加
///    android:usesCleartextTraffic="true"）；iOS 需要配置 ATS 允许明文流量。
/// 2. 所有路径与后端 Flask 路由保持一致，不要改动（字段名也必须一致）。
/// </summary>
public static class ApiConfig
{
    // ------------------------------------------------------------------
    // 服务器地址（默认本地；部署后改为服务器地址）
    // 注意：真实服务器 IP 属敏感信息，勿提交到公开仓库（见内部部署文档）
    // ------------------------------------------------------------------
    public const string ServerUrl = "http://localhost:5000";

    // ------------------------------------------------------------------
    // 接口路径常量（与服务器路由一一对应）
    // ------------------------------------------------------------------
    public const string RealtimeDataPath  = "/api/realtime-data";    // 实时数据 GET
    public const string HistoricalDataPath = "/api/historical-data"; // 历史数据 GET
    public const string PredictionPath     = "/api/prediction";      // 预测数据 GET
    public const string ControlPumpPath    = "/api/control-pump";    // 水泵控制 POST
    public const string SystemStatusPath   = "/api/system-status";   // 系统状态 GET
    public const string WeatherPath        = "/api/weather";         // 天气感知 GET（可选）
    public const string IrrigationStatsPath = "/api/irrigation-stats"; // 灌溉统计 GET

    // ------------------------------------------------------------------
    // 默认请求参数
    // ------------------------------------------------------------------
    public const int   DefaultTimeoutSeconds        = 10;  // 默认请求超时（秒）
    public const float DefaultPollIntervalSeconds   = 5f;  // 默认轮询间隔（秒）
    public const int   MinIrrigationMinutes         = 1;   // 灌溉时长下限（分钟）
    public const int   MaxIrrigationMinutes         = 30;  // 灌溉时长上限（分钟）

    // ------------------------------------------------------------------
    // 完整请求地址（组合 BaseUrl + Path）
    // ------------------------------------------------------------------
    public static string RealtimeDataUrl  => ServerUrl + RealtimeDataPath;
    public static string HistoricalDataUrl => ServerUrl + HistoricalDataPath;
    public static string PredictionUrl     => ServerUrl + PredictionPath;
    public static string ControlPumpUrl    => ServerUrl + ControlPumpPath;
    public static string SystemStatusUrl   => ServerUrl + SystemStatusPath;
    public static string WeatherUrl        => ServerUrl + WeatherPath;
    public static string IrrigationStatsUrl => ServerUrl + IrrigationStatsPath;

    /// <summary>拼接 GET 查询参数，例如 WithQuery(HistoricalDataUrl, "hours=24")</summary>
    public static string WithQuery(string url, string query)
    {
        return url + "?" + query;
    }
}
