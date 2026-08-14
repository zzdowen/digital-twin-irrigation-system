using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// 灌溉系统 API 客户端（单例 MonoBehaviour）。
/// 封装与 Flask 服务器的全部 HTTP 接口，基于 UnityWebRequest + 协程实现，
/// 不依赖任何第三方库。
///
/// 使用方式：
///   IrrigationApiClient.GetOrCreate().GetRealtimeData(
///       data => { ... },                 // 成功回调
///       error => { ... });               // 失败回调（含超时/网络错误）
///
/// 所有回调都在主线程触发，可直接操作 UI。
///
/// 说明：
/// - 兼容 Unity 2020.1+（使用 UnityWebRequest.Result 判断结果）；
///   若项目是 2019 及更早版本，请把 result != Success 的判断替换为
///   isNetworkError || isHttpError。
/// - StopAll() 用于场景切换/退出时终止所有在途请求。
/// </summary>
public class IrrigationApiClient : MonoBehaviour
{
    // ------------------------------------------------------------------
    // 单例
    // ------------------------------------------------------------------
    public static IrrigationApiClient Instance { get; private set; }

    [Header("请求设置")]
    [Tooltip("单个请求的超时时间（秒）")]
    [SerializeField] private int timeoutSeconds = ApiConfig.DefaultTimeoutSeconds;

    /// <summary>请求超时时间（秒），供其他脚本（如轮询器）参考</summary>
    public int TimeoutSeconds => timeoutSeconds;

    /// <summary>当前是否有请求在途（可用于 UI 显示加载状态）</summary>
    public bool IsBusy { get; private set; }

    private int _activeRequests; // 在途请求计数
    private int _generation;     // 世代号：StopAll 后递增，使所有旧回调失效

    // ------------------------------------------------------------------
    // 生命周期
    // ------------------------------------------------------------------

    private void Awake()
    {
        if (Instance != null && Instance != this)
        {
            Destroy(gameObject); // 场景中已有实例，销毁重复挂载的物体
            return;
        }
        Instance = this;
        DontDestroyOnLoad(gameObject); // 跨场景常驻
    }

    private void OnDestroy()
    {
        if (Instance == this) Instance = null;
    }

    /// <summary>
    /// 获取单例；若场景中不存在则自动创建一个常驻 GameObject（懒加载）。
    /// 推荐所有调用方统一使用本方法，保证任何时刻客户端都可用。
    /// </summary>
    public static IrrigationApiClient GetOrCreate()
    {
        if (Instance == null)
        {
            var go = new GameObject("[IrrigationApiClient]");
            Instance = go.AddComponent<IrrigationApiClient>();
            DontDestroyOnLoad(go);
        }
        return Instance;
    }

    /// <summary>
    /// 停止所有在途请求（例如场景切换、退出登录、销毁时调用）。
    /// 停止后，之前发起的请求即使返回，其回调也不会再被触发。
    /// </summary>
    public void StopAll()
    {
        _generation++;          // 使旧回调全部失效
        StopAllCoroutines();    // 停止所有协程
        _activeRequests = 0;
        IsBusy = false;
    }

    // ------------------------------------------------------------------
    // 对外 API（全部为异步，回调式）
    // ------------------------------------------------------------------

    /// <summary>获取实时数据（GET /api/realtime-data）</summary>
    public void GetRealtimeData(Action<RealtimeData> onSuccess, Action<string> onError)
    {
        StartCoroutine(RequestJson<RealtimeData>(ApiConfig.RealtimeDataUrl, onSuccess, onError));
    }

    /// <summary>获取历史数据（GET /api/historical-data?hours=N，N 为 1~168 小时内的小时数）</summary>
    public void GetHistoricalData(int hours, Action<HistoricalData> onSuccess, Action<string> onError)
    {
        string url = ApiConfig.WithQuery(ApiConfig.HistoricalDataUrl, "hours=" + Mathf.Max(1, hours));
        StartCoroutine(RequestJson<HistoricalData>(url, onSuccess, onError));
    }

    /// <summary>获取预测数据（GET /api/prediction?minutes=N，预测未来 N 分钟）</summary>
    public void GetPrediction(int minutes, Action<PredictionData> onSuccess, Action<string> onError)
    {
        string url = ApiConfig.WithQuery(ApiConfig.PredictionUrl, "minutes=" + Mathf.Max(1, minutes));
        StartCoroutine(RequestJson<PredictionData>(url, onSuccess, onError));
    }

    /// <summary>获取系统状态（GET /api/system-status）</summary>
    public void GetSystemStatus(Action<SystemStatus> onSuccess, Action<string> onError)
    {
        StartCoroutine(RequestJson<SystemStatus>(ApiConfig.SystemStatusUrl, onSuccess, onError));
    }

    /// <summary>获取天气感知数据（GET /api/weather，OpenClaw AI 查询，可能返回 enabled=false）</summary>
    public void GetWeather(Action<WeatherStatus> onSuccess, Action<string> onError)
    {
        StartCoroutine(RequestJson<WeatherStatus>(ApiConfig.WeatherUrl, onSuccess, onError));
    }

    /// <summary>获取灌溉统计（GET /api/irrigation-stats，次数/用水量/节水率）</summary>
    public void GetIrrigationStats(Action<IrrigationStats> onSuccess, Action<string> onError)
    {
        StartCoroutine(RequestJson<IrrigationStats>(ApiConfig.IrrigationStatsUrl, onSuccess, onError));
    }

    /// <summary>
    /// 控制水泵（POST /api/control-pump，v3.0.0 三路）。
    /// </summary>
    /// <param name="action">动作："start" 启动灌溉 / "stop" 停止 / "auto" 切换自动模式</param>
    /// <param name="channel">目标花盆 1/2/3；0 = 全部</param>
    /// <param name="durationMinutes">启动灌溉时的时长（分钟），会被自动限制在 1~30；stop/auto 传 0 即可</param>
    public void ControlPump(string action, int channel, int durationMinutes, Action<ControlResponse> onSuccess, Action<string> onError)
    {
        // 参数校验与归一化
        action = string.IsNullOrEmpty(action) ? "" : action.Trim().ToLowerInvariant();
        if (action != "start" && action != "stop" && action != "auto")
        {
            onError?.Invoke("非法操作：action 只能是 start / stop / auto");
            return;
        }

        int duration = action == "start"
            ? Mathf.Clamp(durationMinutes, ApiConfig.MinIrrigationMinutes, ApiConfig.MaxIrrigationMinutes)
            : 0;

        // 使用 JsonUtility 序列化请求体，字段名与服务器要求一致（channel=0 时后端按 all 处理）
        string body = JsonUtility.ToJson(new ControlRequest
        {
            action = action,
            channel = Mathf.Clamp(channel, 0, 3),
            duration_minutes = duration
        });

        StartCoroutine(PostJson(ApiConfig.ControlPumpUrl, body, onSuccess, onError));
    }

    // ------------------------------------------------------------------
    // 内部实现：GET 请求 + JSON 解析（泛型）
    // ------------------------------------------------------------------

    private IEnumerator RequestJson<T>(string url, Action<T> onSuccess, Action<string> onError) where T : class
    {
        int gen = _generation;
        BeginRequest();

        using (UnityWebRequest request = UnityWebRequest.Get(url))
        {
            bool timedOut = false;
            yield return SendWithTimeout(request, timeoutSeconds, v => timedOut = v);

            // 已被 StopAll 终止：静默退出，不再回调
            if (!IsCurrent(gen)) yield break;

            if (timedOut)
            {
                FinishRequest();
                onError?.Invoke(string.Format("请求超时（{0} 秒无响应）：{1}", timeoutSeconds, url));
                yield break;
            }

            if (request.result != UnityWebRequest.Result.Success)
            {
                FinishRequest();
                onError?.Invoke(string.Format("请求失败：{0}（HTTP {1}）", request.error, (int)request.responseCode));
                yield break;
            }

            string text = request.downloadHandler != null ? request.downloadHandler.text : null;
            T data = ParseJson<T>(text, out string parseError);
            FinishRequest();

            if (data != null)
                onSuccess?.Invoke(data);
            else
                onError?.Invoke(parseError ?? "数据解析失败");
        }
    }

    // ------------------------------------------------------------------
    // 内部实现：POST 请求（JSON 请求体）+ 解析
    // ------------------------------------------------------------------

    private IEnumerator PostJson<T>(string url, string jsonBody, Action<T> onSuccess, Action<string> onError) where T : class
    {
        int gen = _generation;
        BeginRequest();

        using (UnityWebRequest request = new UnityWebRequest(url, UnityWebRequest.kHttpVerbPOST))
        {
            // 手动构造 JSON 请求体（UnityWebRequest.Post 默认是表单格式，不适用）
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(jsonBody));
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");

            bool timedOut = false;
            yield return SendWithTimeout(request, timeoutSeconds, v => timedOut = v);

            if (!IsCurrent(gen)) yield break;

            if (timedOut)
            {
                FinishRequest();
                onError?.Invoke(string.Format("请求超时（{0} 秒无响应）：{1}", timeoutSeconds, url));
                yield break;
            }

            if (request.result != UnityWebRequest.Result.Success)
            {
                FinishRequest();
                onError?.Invoke(string.Format("请求失败：{0}（HTTP {1}）", request.error, (int)request.responseCode));
                yield break;
            }

            string text = request.downloadHandler != null ? request.downloadHandler.text : null;
            T data = ParseJson<T>(text, out string parseError);
            FinishRequest();

            if (data != null)
                onSuccess?.Invoke(data);
            else
                onError?.Invoke(parseError ?? "数据解析失败");
        }
    }

    // ------------------------------------------------------------------
    // 工具方法
    // ------------------------------------------------------------------

    /// <summary>
    /// 发送请求并做超时保护。
    /// 使用帧时间累计（unscaledDeltaTime），即使游戏暂停（Time.timeScale=0）超时依然生效。
    /// 超时时主动 Abort 释放连接，并通过 onTimeout 回调告知调用方。
    /// </summary>
    private IEnumerator SendWithTimeout(UnityWebRequest request, int seconds, Action<bool> onTimeout)
    {
        UnityWebRequestAsyncOperation operation = request.SendWebRequest();
        float elapsed = 0f;

        while (!operation.isDone)
        {
            if (elapsed >= seconds)
            {
                onTimeout?.Invoke(true);
                request.Abort();
                yield break;
            }
            elapsed += Time.unscaledDeltaTime;
            yield return null;
        }

        onTimeout?.Invoke(false);
    }

    /// <summary>解析 JSON 字符串为指定类型；失败时返回 null 并给出错误信息</summary>
    private T ParseJson<T>(string json, out string error) where T : class
    {
        error = null;
        if (string.IsNullOrEmpty(json))
        {
            error = "服务器返回了空数据";
            return null;
        }
        try
        {
            return JsonUtility.FromJson<T>(json);
        }
        catch (Exception e)
        {
            error = "JSON 解析失败：" + e.Message;
            return null;
        }
    }

    private void BeginRequest()
    {
        _activeRequests++;
        IsBusy = true;
    }

    private void FinishRequest()
    {
        _activeRequests = Mathf.Max(0, _activeRequests - 1);
        IsBusy = _activeRequests > 0;
    }

    /// <summary>判断回调所属的请求是否仍属于当前世代（防止 StopAll 后旧回调触发）</summary>
    private bool IsCurrent(int generation)
    {
        return generation == _generation;
    }

    // ------------------------------------------------------------------
    // 使用示例（取消注释即可试用）
    // ------------------------------------------------------------------
    /*
    private void ExampleUsage()
    {
        // 获取历史数据
        IrrigationApiClient.GetOrCreate().GetHistoricalData(24,
            data => Debug.Log("历史点数：" + data.total_points),
            err => Debug.LogError(err));

        // 获取未来 30 分钟土壤湿度预测
        IrrigationApiClient.GetOrCreate().GetPrediction(30,
            data => Debug.Log("预测算法：" + data.algorithm_info),
            err => Debug.LogError(err));

        // 启动灌溉 5 分钟
        IrrigationApiClient.GetOrCreate().ControlPump("start", 5,
            resp => Debug.Log(resp.message),
            err => Debug.LogError(err));

        // 查询系统状态
        IrrigationApiClient.GetOrCreate().GetSystemStatus(
            status => Debug.Log("API 版本：" + status.api_version),
            err => Debug.LogError(err));
    }
    */
}
