using System;
using System.Collections;
using UnityEngine;

/// <summary>
/// 实时数据轮询器（MonoBehaviour）。
/// 每 5 秒（可通过 Inspector 调整间隔）向服务器请求一次实时数据，
/// 并通过 C# 事件把结果推送给 UI 等订阅方。
///
/// 订阅方式（在 UI 脚本的 OnEnable/OnDisable 中订阅/退订）：
///   poller.OnDataReceived      += OnDataReceived;      // 成功收到实时数据
///   poller.OnPollError         += OnPollError;         // 单次轮询失败
///   poller.OnConnectionChanged += OnConnectionChanged;  // 连接状态变化（true=在线 / false=离线）
///
/// 特性：
/// - 自带 isPolling 开关（StartPolling / StopPolling）；
/// - 上一次请求未返回时不会重复发起请求（避免请求堆积）；
/// - 停止轮询后，在途请求的过期回调会被丢弃，不会误触发事件；
/// - 轮询失败自动把连接状态置为离线，供 UI 显示"服务器离线"。
/// </summary>
public class RealtimeDataPoller : MonoBehaviour
{
    [Header("轮询设置")]
    [Tooltip("轮询间隔（秒）")]
    [SerializeField] private float pollIntervalSeconds = ApiConfig.DefaultPollIntervalSeconds;

    [Tooltip("脚本启用时自动开始轮询")]
    [SerializeField] private bool startPollingOnEnable = true;

    // ------------------------------------------------------------------
    // 公开状态
    // ------------------------------------------------------------------

    /// <summary>是否正在轮询</summary>
    public bool IsPolling { get; private set; }

    /// <summary>最近一次成功获取的实时数据（其他脚本可直接读取）</summary>
    public RealtimeData LatestData { get; private set; }

    /// <summary>当前是否与服务器连通（true=在线，false=离线/超时）</summary>
    public bool IsConnected { get; private set; }

    // ------------------------------------------------------------------
    // 事件（UI 订阅入口）
    // ------------------------------------------------------------------
    public event Action<RealtimeData> OnDataReceived;   // 成功收到实时数据
    public event Action<string> OnPollError;            // 单次轮询失败（含错误信息）
    public event Action<bool> OnConnectionChanged;      // 连接状态变化（true=在线 / false=离线）

    // ------------------------------------------------------------------
    // 内部状态
    // ------------------------------------------------------------------
    private Coroutine _pollCoroutine;   // 轮询循环协程
    private bool _requestInFlight;      // 当前是否有请求在途
    private int _requestToken;          // 递增令牌：防止停止轮询后过期回调再次触发事件

    // ------------------------------------------------------------------
    // 生命周期
    // ------------------------------------------------------------------

    private void OnEnable()
    {
        if (startPollingOnEnable) StartPolling();
    }

    private void OnDisable()
    {
        StopPolling();
    }

    // ------------------------------------------------------------------
    // 对外控制接口
    // ------------------------------------------------------------------

    /// <summary>开始轮询（幂等，重复调用无副作用）</summary>
    public void StartPolling()
    {
        if (IsPolling) return;
        IsPolling = true;
        _pollCoroutine = StartCoroutine(PollLoop());
    }

    /// <summary>停止轮询（幂等；同时使在途请求的过期回调失效）</summary>
    public void StopPolling()
    {
        if (!IsPolling) return;
        IsPolling = false;
        _requestToken++; // 作废在途回调
        if (_pollCoroutine != null)
        {
            StopCoroutine(_pollCoroutine);
            _pollCoroutine = null;
        }
        _requestInFlight = false;
    }

    /// <summary>立即请求一次（可用于"刷新"按钮）；已有请求在途时忽略本次调用</summary>
    public void PollNow()
    {
        if (_requestInFlight) return;
        StartCoroutine(PollOnce());
    }

    // ------------------------------------------------------------------
    // 轮询实现
    // ------------------------------------------------------------------

    /// <summary>轮询主循环：请求一次 → 等待间隔 → 再请求，循环往复</summary>
    private IEnumerator PollLoop()
    {
        while (IsPolling)
        {
            if (!_requestInFlight)
                yield return PollOnce();

            yield return new WaitForSeconds(pollIntervalSeconds);
        }
    }

    /// <summary>发起一次实时数据请求并派发结果事件</summary>
    private IEnumerator PollOnce()
    {
        _requestInFlight = true;
        int token = ++_requestToken; // 本次请求的令牌

        bool finished = false;
        RealtimeData data = null;
        string error = null;

        IrrigationApiClient.GetOrCreate().GetRealtimeData(
            d => { data = d; finished = true; },   // 成功回调（主线程触发）
            e => { error = e; finished = true; }); // 失败回调（主线程触发）

        // 等待请求结束；最多等 客户端超时 + 5 秒，防止客户端被销毁后永久挂起
        int maxWaitSeconds = IrrigationApiClient.GetOrCreate().TimeoutSeconds + 5;
        float elapsed = 0f;
        while (!finished && elapsed < maxWaitSeconds)
        {
            elapsed += Time.unscaledDeltaTime;
            yield return null;
        }

        // 令牌不匹配 = 轮询已被停止/重新开始，丢弃过期结果，不再触发任何事件
        if (token != _requestToken)
        {
            _requestInFlight = false;
            yield break;
        }

        if (!finished)
        {
            // 等待超时（正常情况下客户端会自行报告超时，这里兜底）
            _requestInFlight = false;
            SetConnected(false);
            OnPollError?.Invoke("等待服务器响应超时");
            yield break;
        }

        if (data != null)
        {
            // 成功：更新缓存、标记在线、派发数据事件
            LatestData = data;
            SetConnected(true);
            OnDataReceived?.Invoke(data);
        }
        else
        {
            // 失败：标记离线、派发错误事件
            SetConnected(false);
            OnPollError?.Invoke(string.IsNullOrEmpty(error) ? "获取实时数据失败" : error);
        }

        _requestInFlight = false;
    }

    /// <summary>仅在连接状态发生改变时派发 OnConnectionChanged 事件</summary>
    private void SetConnected(bool online)
    {
        if (IsConnected == online) return;
        IsConnected = online;
        OnConnectionChanged?.Invoke(online);
    }
}
