using UnityEngine;
using UnityEngine.UI;
using System.Collections;

/// <summary>
/// 程序化数据面板 v3.0.0：代码创建 Canvas UI，无需手工搭建。
/// 面板内容：
///  - 顶部状态条：数据源 / 连接状态 / 平均湿度 / 天气摘要
///  - 左侧：3 路湿度卡片（数值 + 进度条 + 泵状态 + 启停按钮）
///  - 右侧：3 路灌溉预测曲线（未来 60 分钟）+ 天气详情
///  - 底部：全局控制（全部启动 / 全部停止 / 自动模式）+ 操作提示
///
/// 中文字体运行时从操作系统加载（微软雅黑等），无字体资源依赖。
/// </summary>
public class DataPanelUI : MonoBehaviour
{
    [Header("默认灌溉时长（分钟）")]
    [SerializeField] private int defaultDurationMinutes = 5;
    [Header("预测时长（分钟）")]
    [SerializeField] private int predictionMinutes = 60;

    private RealtimeDataPoller _poller;
    private WeatherController _weather;

    // UI 元素
    private Text _statusText;                 // 数据源/连接
    private Text _avgText;                    // 平均湿度
    private Text _weatherText;                // 天气摘要
    private Text[] _channelMoistureTexts = new Text[3];
    private Image[] _channelBarFills = new Image[3];
    private Text[] _channelPumpTexts = new Text[3];
    private UILineChart _chart;
    private Text _chartLegend;
    private Text _statsText;       // 灌溉统计（次数/用水量/节水率）
    private Text _weatherDetail;
    private Text _msgText;

    private static Font _font;

    // ==================== 构建入口 ====================
    public void Build(RealtimeDataPoller poller, WeatherController weather)
    {
        _poller = poller;
        _weather = weather;

        BuildCanvas();

        if (_poller != null)
        {
            _poller.OnDataReceived += OnDataReceived;
            _poller.OnConnectionChanged += OnConnectionChanged;
            _poller.OnPollError += OnPollError;
        }

        StartCoroutine(ChartLoop());
        StartCoroutine(WeatherTextLoop());
        StartCoroutine(StatsLoop());
    }

    private void OnDestroy()
    {
        if (_poller != null)
        {
            _poller.OnDataReceived -= OnDataReceived;
            _poller.OnConnectionChanged -= OnConnectionChanged;
            _poller.OnPollError -= OnPollError;
        }
    }

    // ==================== 数据回调 ====================
    private void OnDataReceived(RealtimeData data)
    {
        if (data.channels != null)
        {
            for (int i = 0; i < 3 && i < data.channels.Length; i++)
            {
                ChannelData ch = data.channels[i];
                if (_channelMoistureTexts[i] != null)
                {
                    _channelMoistureTexts[i].text = string.Format("湿度 {0:F1}%", ch.soil_moisture);
                    _channelMoistureTexts[i].color = MoistureColor(ch.soil_moisture);
                }
                SetBarRatio(_channelBarFills[i], ch.soil_moisture / 100f, MoistureColor(ch.soil_moisture));
                if (_channelPumpTexts[i] != null)
                {
                    _channelPumpTexts[i].text = ch.pump_status
                        ? string.Format("💧 灌溉中 · {0}s", ch.irrigation_duration)
                        : "⏸ 未灌溉";
                    _channelPumpTexts[i].color = ch.pump_status
                        ? new Color(0.35f, 0.85f, 1f)
                        : new Color(0.75f, 0.75f, 0.75f);
                }
            }
        }

        if (_avgText != null)
        {
            _avgText.text = string.Format("平均湿度 {0:F1}%", data.soil_moisture_avg);
        }
        if (_statusText != null)
        {
            _statusText.text = string.Format("数据源：{0}",
                data.data_source == "mqtt" ? "真实设备 (MQTT)" : "模拟数据");
        }
    }

    private void OnConnectionChanged(bool online)
    {
        if (_statusText != null)
        {
            _statusText.text += online ? " ｜ 服务器在线" : " ｜ 服务器离线";
        }
    }

    private void OnPollError(string error)
    {
        SetMessage("数据获取失败，请检查网络");
    }

    // ==================== 循环任务 ====================
    private IEnumerator ChartLoop()
    {
        yield return new WaitForSeconds(3f);
        while (true)
        {
            FetchPrediction();
            yield return new WaitForSeconds(30f);
        }
    }

    private void FetchPrediction()
    {
        IrrigationApiClient.GetOrCreate().GetPrediction(predictionMinutes, data =>
        {
            if (data == null || data.predictions == null || data.predictions.Length < 3) return;
            // 取每路预测值序列
            float[][] series = new float[data.predictions.Length][];
            for (int s = 0; s < data.predictions.Length; s++)
            {
                PredictionPoint[] pts = data.predictions[s];
                series[s] = new float[pts != null ? pts.Length : 0];
                if (pts == null) continue;
                for (int i = 0; i < pts.Length; i++)
                {
                    series[s][i] = pts[i].predicted_soil_moisture;
                }
            }
            if (_chart != null) _chart.SetData(series, 0f, 100f);
            if (_chartLegend != null)
            {
                _chartLegend.text = data.algorithm_info != null
                    ? string.Format("●花盆1  ●花盆2  ●花盆3　|　{0}", data.algorithm_info)
                    : "●花盆1  ●花盆2  ●花盆3";
            }
        }, err => Debug.LogWarning("[UI] 预测获取失败: " + err));
    }

    private IEnumerator WeatherTextLoop()
    {
        yield return new WaitForSeconds(4f);
        while (true)
        {
            RefreshWeatherText();
            yield return new WaitForSeconds(5f);
        }
    }

    private IEnumerator StatsLoop()
    {
        yield return new WaitForSeconds(3f);
        while (true)
        {
            FetchStats();
            yield return new WaitForSeconds(60f);
        }
    }

    private void FetchStats()
    {
        IrrigationApiClient.GetOrCreate().GetIrrigationStats(stats =>
        {
            if (stats == null || _statsText == null) return;
            _statsText.text = string.Format(
                "💧 累计灌溉 {0} 次 ｜ 用水 {1:F1} 升 ｜ 节水率 {2:F1}%（对比传统 {3:F0} 分钟/天）",
                stats.total_irrigations,
                stats.water_usage_liters,
                stats.water_saving_percent,
                stats.baseline_minutes_per_day);
        }, err => Debug.LogWarning("[UI] 灌溉统计获取失败: " + err));
    }

    private void RefreshWeatherText()
    {
        if (_weather == null) return;
        WeatherEntry w = _weather.CurrentWeather;
        if (w == null)
        {
            SetTextSafe(_weatherText, "天气：加载中...");
            SetTextSafe(_weatherDetail, "天气数据不可用（服务器未开启 OpenClaw 天气感知）");
            return;
        }
        SetTextSafe(_weatherText, string.Format("天气：{0} {1}℃ 降雨{2:F0}%",
            w.weather, w.temperature_c, w.rain_probability_percent));
        SetTextSafe(_weatherDetail, string.Format("🌤 {0} ｜ 温度 {1:F1}℃ ｜ 降雨概率 {2:F0}%",
            w.weather, w.temperature_c, w.rain_probability_percent));
    }

    // ==================== 按钮 ====================
    private void StartPump(int channel)
    {
        string target = channel == 0 ? "全部花盆" : string.Format("花盆{0}", channel);
        SetMessage(string.Format("正在启动灌溉（{0}，{1} 分钟）...", target, defaultDurationMinutes));
        IrrigationApiClient.GetOrCreate().ControlPump("start", channel, defaultDurationMinutes,
            resp => SetMessage(resp.message ?? "指令已发送"),
            err => SetMessage("启动失败：" + err));
    }

    private void StopPump(int channel)
    {
        string target = channel == 0 ? "全部花盆" : string.Format("花盆{0}", channel);
        SetMessage("正在停止灌溉（" + target + "）...");
        IrrigationApiClient.GetOrCreate().ControlPump("stop", channel, 0,
            resp => SetMessage(resp.message ?? "指令已发送"),
            err => SetMessage("停止失败：" + err));
    }

    private void AutoMode()
    {
        SetMessage("正在切换自动灌溉模式...");
        IrrigationApiClient.GetOrCreate().ControlPump("auto", 1, 0,
            resp => SetMessage(resp.message ?? "已切换"),
            err => SetMessage("切换失败：" + err));
    }

    private void SetMessage(string msg)
    {
        SetTextSafe(_msgText, msg);
    }

    // ==================== UI 构建 ====================
    private void BuildCanvas()
    {
        // Canvas
        GameObject canvasGo = new GameObject("IrrigationCanvas", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
        canvasGo.transform.SetParent(transform, false);
        Canvas canvas = canvasGo.GetComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceOverlay;
        CanvasScaler scaler = canvasGo.GetComponent<CanvasScaler>();
        scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        scaler.referenceResolution = new Vector2(1920f, 1080f);
        scaler.matchWidthOrHeight = 0.5f;

        RectTransform cv = canvasGo.GetComponent<RectTransform>();

        // ---- 顶部状态条 ----
        RectTransform topBar = CreateRect("TopBar", cv, new Vector2(0f, 1f), new Vector2(1f, 1f),
                                          new Vector2(0f, -64f), new Vector2(0f, 0f));
        CreatePanel(topBar, new Color(0.10f, 0.13f, 0.18f, 0.85f));
        _statusText = CreateText("StatusText", topBar, "数据源：连接中...", 22, Color.white,
                                 new Vector2(0f, 0f), new Vector2(0.55f, 1f), new Vector2(24f, 0f), new Vector2(0f, 0f));
        _avgText = CreateText("AvgText", topBar, "平均湿度 --%", 22, new Color(0.4f, 0.85f, 1f),
                              new Vector2(0.55f, 0f), new Vector2(0.78f, 1f), Vector2.zero, Vector2.zero);
        _weatherText = CreateText("WeatherText", topBar, "天气：加载中...", 22, Color.white,
                                  new Vector2(0.78f, 0f), new Vector2(1f, 1f), new Vector2(0f, 0f), new Vector2(-24f, 0f));

        // ---- 左侧三路卡片 ----
        RectTransform leftPanel = CreateRect("ChannelPanel", cv, new Vector2(0f, 0.18f), new Vector2(0.34f, 0.94f),
                                             new Vector2(20f, 0f), new Vector2(0f, -20f));
        CreatePanel(leftPanel, new Color(0.10f, 0.13f, 0.18f, 0.78f));
        BuildChannelCards(leftPanel);

        // ---- 右侧图表 ----
        RectTransform rightPanel = CreateRect("ChartPanel", cv, new Vector2(0.66f, 0.18f), new Vector2(1f, 0.94f),
                                              new Vector2(0f, 0f), new Vector2(-20f, -20f));
        CreatePanel(rightPanel, new Color(0.10f, 0.13f, 0.18f, 0.78f));
        BuildChartPanel(rightPanel);

        // ---- 底部控制栏 ----
        RectTransform bottomBar = CreateRect("BottomBar", cv, new Vector2(0f, 0f), new Vector2(1f, 0.18f),
                                             new Vector2(20f, 20f), new Vector2(-20f, 0f));
        CreatePanel(bottomBar, new Color(0.10f, 0.13f, 0.18f, 0.85f));
        BuildBottomBar(bottomBar);
    }

    private void BuildChannelCards(RectTransform panel)
    {
        string[] names = { "花盆1", "花盆2", "花盆3" };
        for (int i = 0; i < 3; i++)
        {
            // 卡片纵向三等分
            float yTop = 1f - i * (1f / 3f);
            float yBottom = 1f - (i + 1) * (1f / 3f);
            RectTransform card = CreateRect("Card" + (i + 1), panel,
                new Vector2(0f, yBottom), new Vector2(1f, yTop),
                new Vector2(16f, 8f), new Vector2(-16f, -8f));
            CreatePanel(card, new Color(0.16f, 0.20f, 0.27f, 0.9f));

            int index = i;
            // 标题 + 泵状态
            CreateText("Title", card, names[i], 26, Color.white,
                       new Vector2(0f, 0.62f), new Vector2(1f, 1f), new Vector2(20f, 0f), new Vector2(-20f, 0f));

            _channelPumpTexts[i] = CreateText("PumpState", card, "⏸ 未灌溉", 22, new Color(0.75f, 0.75f, 0.75f),
                                              new Vector2(0f, 0.40f), new Vector2(1f, 0.62f), new Vector2(20f, 0f), new Vector2(-20f, 0f));

            // 湿度条（背景 + 锚点拉伸填充）
            RectTransform bar = CreateRect("Bar", card, new Vector2(0f, 0.16f), new Vector2(0.62f, 0.40f),
                                           new Vector2(20f, 0f), new Vector2(0f, -4f));
            CreatePanel(bar, new Color(0.08f, 0.09f, 0.12f, 1f));
            Image fill = CreateFill(bar);
            _channelBarFills[i] = fill;
            SetBarRatio(fill, 0f, ProceduralStyle.SoilNormal);

            // 湿度数值
            _channelMoistureTexts[i] = CreateText("Moisture", card, "湿度 --%", 24, Color.white,
                                                  new Vector2(0.64f, 0.16f), new Vector2(1f, 0.40f), Vector2.zero, Vector2.zero);

            // 启动/停止按钮
            CreateButton("StartBtn", card, "启动灌溉", new Vector2(0f, 0f), new Vector2(0.49f, 0.15f),
                         new Color(0.20f, 0.55f, 0.85f),
                         () => StartPump(index + 1));
            CreateButton("StopBtn", card, "停止", new Vector2(0.51f, 0f), new Vector2(1f, 0.15f),
                         new Color(0.65f, 0.30f, 0.30f),
                         () => StopPump(index + 1));
        }
    }

    private void BuildChartPanel(RectTransform panel)
    {
        CreateText("ChartTitle", panel, string.Format("灌溉预测 · 未来 {0} 分钟", predictionMinutes), 26, Color.white,
                   new Vector2(0f, 0.90f), new Vector2(1f, 1f), new Vector2(24f, 0f), new Vector2(-24f, 0f));

        _chartLegend = CreateText("Legend", panel, "●花盆1  ●花盆2  ●花盆3", 20, new Color(0.85f, 0.85f, 0.85f),
                                  new Vector2(0f, 0.83f), new Vector2(1f, 0.90f), new Vector2(24f, 0f), new Vector2(-24f, 0f));

        // 图表区
        RectTransform chartArea = CreateRect("Chart", panel, new Vector2(0f, 0.42f), new Vector2(1f, 0.83f),
                                             new Vector2(24f, 0f), new Vector2(-24f, 0f));
        CreatePanel(chartArea, new Color(0.07f, 0.08f, 0.11f, 0.9f));
        _chart = chartArea.gameObject.AddComponent<UILineChart>();
        _chart.SetData(new[] { new[] { 50f, 50f, 50f, 50f } }, 0f, 100f);

        // 灌溉统计（节水数据，比赛工程效果）
        _statsText = CreateText("IrrigationStats", panel, "💧 灌溉统计加载中...", 22, new Color(0.55f, 0.9f, 0.65f),
                                new Vector2(0f, 0.17f), new Vector2(1f, 0.40f), new Vector2(24f, 0f), new Vector2(-24f, 0f));

        // 天气详情
        _weatherDetail = CreateText("WeatherDetail", panel, "天气数据加载中...", 22, Color.white,
                                    new Vector2(0f, 0f), new Vector2(1f, 0.15f), new Vector2(24f, 0f), new Vector2(-24f, 0f));
    }

    private void BuildBottomBar(RectTransform bar)
    {
        _msgText = CreateText("Message", bar, "等待数据...", 22, new Color(0.9f, 0.9f, 0.9f),
                              new Vector2(0f, 0f), new Vector2(0.62f, 1f), new Vector2(24f, 0f), Vector2.zero);

        CreateButton("StartAll", bar, "全部启动", new Vector2(0.63f, 0.12f), new Vector2(0.74f, 0.88f),
                     new Color(0.20f, 0.55f, 0.85f), () => StartPump(0));
        CreateButton("StopAll", bar, "全部停止", new Vector2(0.75f, 0.12f), new Vector2(0.86f, 0.88f),
                     new Color(0.65f, 0.30f, 0.30f), () => StopPump(0));
        CreateButton("AutoMode", bar, "自动模式", new Vector2(0.87f, 0.12f), new Vector2(0.99f, 0.88f),
                     new Color(0.25f, 0.50f, 0.35f), AutoMode);
    }

    // ==================== UI 工具 ====================
    private static Font GetFont()
    {
        if (_font != null) return _font;
        string[] candidates = { "Microsoft YaHei", "微软雅黑", "SimHei", "PingFang SC", "Noto Sans CJK SC", "Arial" };
        foreach (string name in candidates)
        {
            try
            {
                Font f = Font.CreateDynamicFontFromOSFont(name, 20);
                if (f != null) { _font = f; return f; }
            }
            catch (System.Exception) { /* 尝试下一个 */ }
        }
        _font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        return _font;
    }

    private static RectTransform CreateRect(string name, Transform parent,
                                            Vector2 anchorMin, Vector2 anchorMax,
                                            Vector2 offsetMin, Vector2 offsetMax)
    {
        GameObject go = new GameObject(name, typeof(RectTransform));
        RectTransform rt = go.GetComponent<RectTransform>();
        rt.SetParent(parent, false);
        rt.anchorMin = anchorMin;
        rt.anchorMax = anchorMax;
        rt.offsetMin = offsetMin;
        rt.offsetMax = offsetMax;
        return rt;
    }

    private static Image CreatePanel(Transform parent, Color color)
    {
        GameObject go = new GameObject("Panel", typeof(Image));
        go.transform.SetParent(parent, false);
        RectTransform rt = go.GetComponent<RectTransform>();
        rt.anchorMin = Vector2.zero;
        rt.anchorMax = Vector2.one;
        rt.offsetMin = Vector2.zero;
        rt.offsetMax = Vector2.zero;
        Image img = go.GetComponent<Image>();
        img.color = color;
        img.raycastTarget = false;
        return img;
    }

    private static Text CreateText(string name, Transform parent, string content, int fontSize, Color color,
                                   Vector2 anchorMin, Vector2 anchorMax, Vector2 offsetMin, Vector2 offsetMax)
    {
        GameObject go = new GameObject(name, typeof(Text));
        go.transform.SetParent(parent, false);
        RectTransform rt = go.GetComponent<RectTransform>();
        rt.anchorMin = anchorMin;
        rt.anchorMax = anchorMax;
        rt.offsetMin = offsetMin;
        rt.offsetMax = offsetMax;
        Text t = go.GetComponent<Text>();
        t.font = GetFont();
        t.text = content;
        t.fontSize = fontSize;
        t.color = color;
        t.alignment = TextAnchor.MiddleLeft;
        t.raycastTarget = false;
        return t;
    }

    private static Image CreateFill(Transform parent)
    {
        GameObject go = new GameObject("Fill", typeof(Image));
        go.transform.SetParent(parent, false);
        Image img = go.GetComponent<Image>();
        img.color = Color.white;
        img.raycastTarget = false;
        return img;
    }

    private static void SetBarRatio(Image fill, float ratio, Color color)
    {
        if (fill == null) return;
        RectTransform rt = fill.rectTransform;
        rt.anchorMin = Vector2.zero;
        rt.anchorMax = new Vector2(Mathf.Clamp01(ratio), 1f);
        rt.offsetMin = new Vector2(3f, 3f);
        rt.offsetMax = new Vector2(-3f, -3f);
        fill.color = color;
    }

    private static void CreateButton(string name, Transform parent, string label,
                                     Vector2 anchorMin, Vector2 anchorMax, Color color,
                                     UnityEngine.Events.UnityAction onClick)
    {
        RectTransform rt = CreateRect(name, parent, anchorMin, anchorMax, Vector2.zero, Vector2.zero);

        Image bg = rt.gameObject.AddComponent<Image>();
        bg.color = color;

        Button btn = rt.gameObject.AddComponent<Button>();
        ColorBlock cb = btn.colors;
        cb.highlightedColor = color * 1.15f;
        cb.pressedColor = color * 0.8f;
        btn.colors = cb;
        btn.onClick.AddListener(onClick);

        Text labelText = CreateText("Label", rt, label, 24, Color.white,
                                    Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
        labelText.alignment = TextAnchor.MiddleCenter;
    }

    private static void SetTextSafe(Text t, string content)
    {
        if (t != null) t.text = content;
    }

    private static Color MoistureColor(float moisture)
    {
        if (moisture > 60f) return new Color(0.35f, 0.75f, 1.00f);   // 湿：蓝
        if (moisture >= 30f) return new Color(0.45f, 0.85f, 0.45f);  // 正常：绿
        return new Color(0.95f, 0.45f, 0.35f);                       // 干：红
    }
}
