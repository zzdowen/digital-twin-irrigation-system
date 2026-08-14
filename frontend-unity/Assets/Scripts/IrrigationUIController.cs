using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// 三路 UI 控制器 v3.0.0：把 3 路实时数据绑定到 UI 控件，并通过 IrrigationApiClient 控制各农田水泵。
///
/// 挂载方式：挂到 Canvas 下的空物体，在 Inspector 中把对应控件拖到槽位（留空自动跳过）。
/// 每路控件组（湿度文本/进度条/泵状态/时长/启停按钮）通过 ChannelUIBinding 数组配置（下标 0-2 = 农田 1-3）。
///
/// 说明：
/// 1. 使用 UnityEngine.UI.Text，开箱可编译；换 TextMeshPro 只需改字段类型并加 using TMPro;。
/// 2. 轮询器留空会自动在场景中查找 RealtimeDataPoller。
/// </summary>
[System.Serializable]
public class ChannelUIBinding
{
    [Tooltip("湿度数值文本")]
    public Text soilMoistureText;
    [Tooltip("湿度进度条 Slider（0~1）")]
    public Slider soilMoistureSlider;
    [Tooltip("水泵状态文本")]
    public Text pumpStatusText;
    [Tooltip("灌溉时长文本")]
    public Text irrigationDurationText;
    [Tooltip("启动按钮")]
    public Button startButton;
    [Tooltip("停止按钮")]
    public Button stopButton;
}

public class IrrigationUIController : MonoBehaviour
{
    [Header("数据源")]
    [Tooltip("留空时自动在场景中查找 RealtimeDataPoller")]
    [SerializeField] private RealtimeDataPoller poller;

    [Header("三路 UI（下标 0-2 = 农田 1-3）")]
    [SerializeField] private ChannelUIBinding[] channelUIs = new ChannelUIBinding[3];

    [Header("全局信息")]
    [SerializeField] private Text avgMoistureText;         // 平均湿度
    [SerializeField] private Text activePumpsText;         // 运行水泵数
    [SerializeField] private Text dataSourceText;          // 数据来源
    [SerializeField] private Text autoModeText;            // 自动灌溉状态
    [SerializeField] private Text connectionStatusText;    // 连接状态
    [SerializeField] private Text operationMessageText;    // 操作结果提示

    [Header("全局控制")]
    [SerializeField] private Button startAllButton;        // 全部启动
    [SerializeField] private Button stopAllButton;         // 全部停止
    [SerializeField] private Button autoModeButton;        // 切换自动模式
    [Tooltip("灌溉时长滑块（1~30 分钟）")]
    [SerializeField] private Slider durationSlider;

    [Header("颜色")]
    [SerializeField] private Color pumpRunningColor = new Color(0.20f, 0.80f, 0.20f);
    [SerializeField] private Color pumpStoppedColor = new Color(0.70f, 0.70f, 0.70f);
    [SerializeField] private Color onlineColor     = new Color(0.20f, 0.80f, 0.20f);
    [SerializeField] private Color offlineColor    = new Color(0.90f, 0.20f, 0.20f);
    [SerializeField] private Color connectingColor = new Color(1.00f, 0.80f, 0.20f);

    // ------------------------------------------------------------------
    // 生命周期：订阅 / 退订事件
    // ------------------------------------------------------------------
    private void OnEnable()
    {
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

        SetConnectionText("连接中...", connectingColor);
        SetMessage("等待数据...");
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
    // 数据回调：3 路刷新
    // ------------------------------------------------------------------
    private void OnDataReceived(RealtimeData data)
    {
        if (data.channels != null)
        {
            for (int i = 0; i < channelUIs.Length && i < data.channels.Length; i++)
            {
                ChannelUIBinding ui = channelUIs[i];
                ChannelData ch = data.channels[i];
                if (ui == null || ch == null) continue;

                SetText(ui.soilMoistureText, string.Format("农田{0} 湿度：{1:F1} %", i + 1, ch.soil_moisture));
                if (ui.soilMoistureSlider != null)
                    ui.soilMoistureSlider.value = Mathf.Clamp01(ch.soil_moisture / 100f);

                if (ui.pumpStatusText != null)
                {
                    ui.pumpStatusText.text = ch.pump_status ? "水泵：运行中" : "水泵：已停止";
                    ui.pumpStatusText.color = ch.pump_status ? pumpRunningColor : pumpStoppedColor;
                }
                SetText(ui.irrigationDurationText, string.Format("灌溉时长：{0} 秒", ch.irrigation_duration));

                if (ui.startButton != null) ui.startButton.interactable = !ch.pump_status;
                if (ui.stopButton != null) ui.stopButton.interactable = ch.pump_status;
            }
        }

        SetText(avgMoistureText, string.Format("平均湿度：{0:F1} %", data.soil_moisture_avg));
        SetText(activePumpsText, string.Format("运行水泵：{0} 台", data.active_pumps));
        SetText(dataSourceText, data.data_source == "mqtt" ? "数据来源：真实设备（MQTT）" : "数据来源：模拟数据");
        SetText(autoModeText, data.auto_irrigation_enabled ? "自动灌溉：已开启" : "自动灌溉：已关闭");
    }

    private void OnConnectionChanged(bool online)
    {
        SetConnectionText(online ? "服务器在线" : "服务器离线", online ? onlineColor : offlineColor);
    }

    private void OnPollError(string error)
    {
        Debug.LogWarning("[IrrigationUI] 轮询失败：" + error);
        SetMessage("获取数据失败，请检查网络");
    }

    // ------------------------------------------------------------------
    // 按钮逻辑
    // ------------------------------------------------------------------
    private void WireButtons()
    {
        for (int i = 0; i < channelUIs.Length; i++)
        {
            ChannelUIBinding ui = channelUIs[i];
            if (ui == null) continue;
            int channel = i + 1;   // 闭包捕获固定值
            if (ui.startButton != null)
            {
                ui.startButton.onClick.RemoveAllListeners();
                ui.startButton.onClick.AddListener(() => OnStartPump(channel));
            }
            if (ui.stopButton != null)
            {
                ui.stopButton.onClick.RemoveAllListeners();
                ui.stopButton.onClick.AddListener(() => OnStopPump(channel));
            }
        }

        if (startAllButton != null)
        {
            startAllButton.onClick.RemoveAllListeners();
            startAllButton.onClick.AddListener(() => OnStartPump(0));
        }
        if (stopAllButton != null)
        {
            stopAllButton.onClick.RemoveAllListeners();
            stopAllButton.onClick.AddListener(() => OnStopPump(0));
        }
        if (autoModeButton != null)
        {
            autoModeButton.onClick.RemoveAllListeners();
            autoModeButton.onClick.AddListener(() =>
            {
                SetMessage("正在切换自动模式...");
                IrrigationApiClient.GetOrCreate().ControlPump("auto", 1, 0,
                    resp => SetMessage(resp.message),
                    err => SetMessage("切换失败：" + err));
            });
        }
    }

    private void OnStartPump(int channel)
    {
        int minutes = durationSlider != null ? Mathf.RoundToInt(durationSlider.value) : 5;
        string target = channel == 0 ? "全部农田" : ("农田" + channel);
        SetMessage(string.Format("正在启动灌溉（{0}，{1} 分钟）...", target, minutes));

        IrrigationApiClient.GetOrCreate().ControlPump("start", channel, minutes,
            resp => SetMessage(resp.message),
            err => SetMessage("启动失败：" + err));
    }

    private void OnStopPump(int channel)
    {
        string target = channel == 0 ? "全部农田" : ("农田" + channel);
        SetMessage("正在停止灌溉（" + target + "）...");

        IrrigationApiClient.GetOrCreate().ControlPump("stop", channel, 0,
            resp => SetMessage(resp.message),
            err => SetMessage("停止失败：" + err));
    }

    // ------------------------------------------------------------------
    // 小工具
    // ------------------------------------------------------------------
    private static void SetText(Text text, string content)
    {
        if (text != null) text.text = content;
    }

    private void SetConnectionText(string content, Color color)
    {
        if (connectionStatusText != null)
        {
            connectionStatusText.text = content;
            connectionStatusText.color = color;
        }
    }

    private void SetMessage(string content)
    {
        SetText(operationMessageText, content);
    }
}
