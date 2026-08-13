using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// 示例 UI 控制器：演示如何把实时数据绑定到 UI 控件，
/// 以及通过 IrrigationApiClient 调用水泵控制接口。
///
/// 挂载方式：把本脚本挂到场景中任意 UI 物体（如 Canvas 下的空物体）上，
/// 然后在 Inspector 中把对应的 Text / Slider / Image / Button 拖到各槽位。
/// 留空的槽位会被跳过（不会报错）。
///
/// 说明：
/// 1. 本示例使用 UnityEngine.UI.Text（无需导入任何包即可编译）。
///    若项目使用 TextMeshPro，把字段类型 Text 换成 TextMeshProUGUI，
///    并在文件顶部加上 using TMPro; 即可（仅需替换类型，逻辑不变）。
/// 2. 土壤湿度 Image 进度条需要把 Image 的 Image Type 设置为 Filled。
/// 3. 轮询器（RealtimeDataPoller）若未手动指定，本脚本会自动在场景中查找。
/// </summary>
public class IrrigationUIController : MonoBehaviour
{
    [Header("数据源")]
    [Tooltip("留空时自动在场景中查找 RealtimeDataPoller")]
    [SerializeField] private RealtimeDataPoller poller;

    [Header("数值文本（Text）")]
    [SerializeField] private Text soilMoistureText;        // 土壤湿度
    [SerializeField] private Text temperatureText;         // 温度
    [SerializeField] private Text humidityText;            // 空气湿度
    [SerializeField] private Text pumpStatusText;          // 水泵状态
    [SerializeField] private Text irrigationDurationText;  // 灌溉已持续时长
    [SerializeField] private Text dataSourceText;          // 数据来源
    [SerializeField] private Text lastUpdateText;          // 最后更新时间
    [SerializeField] private Text connectionStatusText;    // 连接状态（连接中/在线/离线）
    [SerializeField] private Text operationMessageText;    // 操作结果提示

    [Header("土壤湿度可视化")]
    [Tooltip("Slider 进度条（0~1）")]
    [SerializeField] private Slider soilMoistureSlider;
    [Tooltip("Image 填充图（需设置 Image Type = Filled）")]
    [SerializeField] private Image soilMoistureBar;

    [Header("控制按钮")]
    [SerializeField] private Button startPumpButton;  // 启动灌溉
    [SerializeField] private Button stopPumpButton;   // 停止灌溉
    [SerializeField] private Button autoModeButton;   // 切换自动模式

    [Header("控制参数")]
    [Tooltip("点击“启动”时默认的灌溉时长（分钟），服务器会限制在 1~30")]
    [SerializeField] private float defaultIrrigationMinutes = 5f;

    [Header("颜色")]
    [SerializeField] private Color pumpRunningColor  = new Color(0.20f, 0.80f, 0.20f); // 水泵运行
    [SerializeField] private Color pumpStoppedColor  = new Color(0.70f, 0.70f, 0.70f); // 水泵停止
    [SerializeField] private Color onlineColor       = new Color(0.20f, 0.80f, 0.20f); // 服务器在线
    [SerializeField] private Color offlineColor      = new Color(0.90f, 0.20f, 0.20f); // 服务器离线
    [SerializeField] private Color connectingColor   = new Color(1.00f, 0.80f, 0.20f); // 连接中

    // ------------------------------------------------------------------
    // 生命周期：订阅 / 退订事件
    // ------------------------------------------------------------------

    private void OnEnable()
    {
        // 未手动指定轮询器时，自动在场景中查找
        if (poller == null)
        {
#if UNITY_2023_1_OR_NEWER
            poller = FindFirstObjectByType<RealtimeDataPoller>();
#else
            poller = FindObjectOfType<RealtimeDataPoller>();
#endif
        }

        if (poller != null)
        {
            poller.OnDataReceived += OnDataReceived;
            poller.OnPollError += OnPollError;
            poller.OnConnectionChanged += OnConnectionChanged;
        }

        // 初始显示"连接中"（在收到第一次数据或错误前保持该状态）
        SetConnectionText("连接中...", connectingColor);
        SetMessage("等待数据...");

        // 绑定按钮点击事件（幂等：先清空再绑定）
        WireButtons();
    }

    private void OnDisable()
    {
        if (poller != null)
        {
            poller.OnDataReceived -= OnDataReceived;
            poller.OnPollError -= OnPollError;
            poller.OnConnectionChanged -= OnConnectionChanged;
        }
    }

    // ------------------------------------------------------------------
    // 事件回调：把实时数据刷新到 UI
    // ------------------------------------------------------------------

    private void OnDataReceived(RealtimeData data)
    {
        // 数值文本
        SetText(soilMoistureText, string.Format("土壤湿度：{0:F1} %", data.soil_moisture));
        SetText(temperatureText, string.Format("温度：{0:F1} ℃", data.temperature));
        SetText(humidityText, string.Format("空气湿度：{0:F1} %", data.humidity));
        SetText(irrigationDurationText, string.Format("灌溉时长：{0} 秒", data.irrigation_duration));
        SetText(lastUpdateText, "更新时间：" + data.last_update);
        SetText(dataSourceText, data.data_source == "mqtt" ? "数据来源：真实设备（MQTT）" : "数据来源：模拟数据");

        // 水泵状态文本 + 颜色
        if (pumpStatusText != null)
        {
            pumpStatusText.text = data.pump_status ? "水泵：运行中" : "水泵：已停止";
            pumpStatusText.color = data.pump_status ? pumpRunningColor : pumpStoppedColor;
        }

        // 土壤湿度可视化：0~100 映射到 0~1
        float ratio = Mathf.Clamp01(data.soil_moisture / 100f);
        if (soilMoistureSlider != null) soilMoistureSlider.value = ratio;
        if (soilMoistureBar != null) soilMoistureBar.fillAmount = ratio;

        // 根据水泵状态更新按钮可用性（运行中禁止再启动）
        if (startPumpButton != null) startPumpButton.interactable = !data.pump_status;
        if (stopPumpButton != null) stopPumpButton.interactable = data.pump_status;
    }

    /// <summary>连接状态变化：true=在线 / false=离线</summary>
    private void OnConnectionChanged(bool online)
    {
        SetConnectionText(online ? "服务器在线" : "服务器离线", online ? onlineColor : offlineColor);
    }

    /// <summary>单次轮询失败（网络错误/超时等）</summary>
    private void OnPollError(string error)
    {
        Debug.LogWarning("[IrrigationUI] 轮询失败：" + error);
        SetMessage("获取数据失败，请检查网络");
    }

    // ------------------------------------------------------------------
    // 按钮点击处理：调用水泵控制接口
    // ------------------------------------------------------------------

    private void WireButtons()
    {
        if (startPumpButton != null)
        {
            startPumpButton.onClick.RemoveAllListeners();
            startPumpButton.onClick.AddListener(OnStartPumpClicked);
        }
        if (stopPumpButton != null)
        {
            stopPumpButton.onClick.RemoveAllListeners();
            stopPumpButton.onClick.AddListener(OnStopPumpClicked);
        }
        if (autoModeButton != null)
        {
            autoModeButton.onClick.RemoveAllListeners();
            autoModeButton.onClick.AddListener(OnAutoModeClicked);
        }
    }

    private void OnStartPumpClicked()
    {
        int minutes = Mathf.RoundToInt(defaultIrrigationMinutes);
        SetMessage(string.Format("正在启动灌溉（{0} 分钟）...", minutes));

        IrrigationApiClient.GetOrCreate().ControlPump("start", minutes,
            resp => { SetMessage(resp.message); RefreshPumpButtons(resp.pump_status); },
            err => SetMessage("启动失败：" + err));
    }

    private void OnStopPumpClicked()
    {
        SetMessage("正在停止灌溉...");

        IrrigationApiClient.GetOrCreate().ControlPump("stop", 0,
            resp => { SetMessage(resp.message); RefreshPumpButtons(resp.pump_status); },
            err => SetMessage("停止失败：" + err));
    }

    private void OnAutoModeClicked()
    {
        SetMessage("正在切换自动模式...");

        IrrigationApiClient.GetOrCreate().ControlPump("auto", 0,
            resp => SetMessage(resp.message),
            err => SetMessage("切换失败：" + err));
    }

    /// <summary>根据服务器返回的水泵状态刷新按钮可用性</summary>
    private void RefreshPumpButtons(bool pumpRunning)
    {
        if (startPumpButton != null) startPumpButton.interactable = !pumpRunning;
        if (stopPumpButton != null) stopPumpButton.interactable = pumpRunning;
    }

    // ------------------------------------------------------------------
    // 小工具
    // ------------------------------------------------------------------

    /// <summary>安全地给 Text 赋值（控件为空时跳过）</summary>
    private static void SetText(Text text, string content)
    {
        if (text != null) text.text = content;
    }

    /// <summary>更新连接状态文本与颜色</summary>
    private void SetConnectionText(string content, Color color)
    {
        if (connectionStatusText != null)
        {
            connectionStatusText.text = content;
            connectionStatusText.color = color;
        }
    }

    /// <summary>更新操作提示文本</summary>
    private void SetMessage(string content)
    {
        SetText(operationMessageText, content);
    }
}
