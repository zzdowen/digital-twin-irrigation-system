using UnityEngine;
using UnityEngine.UI;
using System.Collections;

/// <summary>
/// 程序化数据面板 v4.0.0（玻璃风·紧凑版）：代码创建 Canvas UI，无需手工搭建。
/// 相比 v3 大幅缩小占用面积并提高透明度，把屏幕中央留给 3D 场景：
///  - 顶部细状态条：数据源 / 连接状态 / 平均湿度 / 天气摘要 / 收起按钮
///  - 左上：3 张农田卡片（数值 + 进度条 + 泵状态 + 启停按钮），300px 宽
///  - 右上：3 路灌溉预测曲线（未来 60 分钟）+ 灌溉统计 + 天气详情
///  - 底部细控制栏：全部灌溉 / 全部停止 / 自动模式 + 操作提示
///  - 「收起 ▲」按钮可一键隐藏/展开除顶栏外的全部面板（淡入淡出）
///
/// 面板为运行时生成的圆角玻璃材质（半透明 + 淡蓝描边），中文字体从系统加载。
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
    private Text[] _channelTitleTexts = new Text[3];
    private Text[] _channelMoistureTexts = new Text[3];
    private Image[] _channelBarFills = new Image[3];
    private Text[] _channelPumpTexts = new Text[3];
    private UILineChart _chart;
    private Text _chartLegend;
    private Text _statsText;       // 灌溉统计（次数/用水量/节水率）
    private Text _weatherDetail;
    private Text _msgText;

    private CanvasGroup _contentGroup;   // 可收起的内容容器（卡片/图表/底栏）
    private Text _collapseLabel;
    private bool _contentVisible = true;
    private Coroutine _fadeRoutine;

    private string _lastDataSource = "模拟数据";
    private bool _isOnline;

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
                Color mc = MoistureColor(ch.soil_moisture);

                if (_channelTitleTexts[i] != null)
                {
                    _channelTitleTexts[i].text = string.Format("● 农田 {0}", i + 1);
                    _channelTitleTexts[i].color = mc;
                }
                if (_channelMoistureTexts[i] != null)
                {
                    _channelMoistureTexts[i].text = string.Format("{0:F1}%", ch.soil_moisture);
                    _channelMoistureTexts[i].color = mc;
                }
                UIGlass.SetBarRatio(_channelBarFills[i], ch.soil_moisture / 100f, mc);
                if (_channelPumpTexts[i] != null)
                {
                    _channelPumpTexts[i].text = ch.pump_status
                        ? string.Format("💧 灌溉中 {0}s", ch.irrigation_duration)
                        : "⏸ 待机";
                    _channelPumpTexts[i].color = ch.pump_status
                        ? new Color(0.40f, 0.85f, 1f)
                        : new Color(0.72f, 0.74f, 0.78f);
                }
            }
        }

        if (_avgText != null)
        {
            _avgText.text = string.Format("平均湿度 {0:F1}%", data.soil_moisture_avg);
        }
        _lastDataSource = data.data_source == "mqtt" ? "真实设备 (MQTT)" : "模拟数据";
        RefreshStatus();
    }

    private void OnConnectionChanged(bool online)
    {
        _isOnline = online;
        RefreshStatus();
    }

    private void RefreshStatus()
    {
        if (_statusText != null)
        {
            _statusText.text = string.Format("数据源：{0} ｜ {1}", _lastDataSource,
                _isOnline ? "服务器在线" : "服务器离线");
            _statusText.color = _isOnline ? new Color(0.55f, 0.9f, 0.62f) : new Color(0.95f, 0.55f, 0.5f);
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
                    ? string.Format("●农田1  ●农田2  ●农田3　|　{0}", data.algorithm_info)
                    : "●农田1  ●农田2  ●农田3";
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
                "💧 累计灌溉 {0} 次 ｜ 用水 {1:F1} 升 ｜ 节水率 {2:F1}%（基线 {3:F0} 分钟/天）",
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
            SetTextSafe(_weatherDetail, "天气数据不可用（服务器未开启天气感知）");
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
        string target = channel == 0 ? "全部农田" : string.Format("农田{0}", channel);
        SetMessage(string.Format("正在启动灌溉（{0}，{1} 分钟）...", target, defaultDurationMinutes));
        IrrigationApiClient.GetOrCreate().ControlPump("start", channel, defaultDurationMinutes,
            resp => SetMessage(resp.message ?? "指令已发送"),
            err => SetMessage("启动失败：" + err));
    }

    private void StopPump(int channel)
    {
        string target = channel == 0 ? "全部农田" : string.Format("农田{0}", channel);
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

    // ==================== 收起 / 展开 ====================
    private void ToggleCollapse()
    {
        SetContentVisible(!_contentVisible);
    }

    private void SetContentVisible(bool visible)
    {
        _contentVisible = visible;
        if (_collapseLabel != null)
        {
            _collapseLabel.text = visible ? "收起 ▲" : "展开 ▼";
        }
        if (_fadeRoutine != null) StopCoroutine(_fadeRoutine);
        _fadeRoutine = StartCoroutine(FadeContent(visible));
    }

    private IEnumerator FadeContent(bool show)
    {
        if (_contentGroup == null) yield break;
        _contentGroup.blocksRaycasts = show;
        _contentGroup.interactable = show;
        float from = _contentGroup.alpha;
        float to = show ? 1f : 0f;
        float t = 0f;
        while (t < 0.22f)
        {
            t += Time.unscaledDeltaTime;
            _contentGroup.alpha = Mathf.Lerp(from, to, Mathf.Clamp01(t / 0.22f));
            yield return null;
        }
        _contentGroup.alpha = to;
        _fadeRoutine = null;
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

        // ---- 顶部细状态条（常驻，不随收起隐藏） ----
        RectTransform topBar = UIGlass.CreateRect("TopBar", cv, new Vector2(0f, 1f), new Vector2(1f, 1f),
                                                  new Vector2(12f, -56f), new Vector2(-12f, 0f));
        UIGlass.CreateGlass(topBar, UIGlass.GlassBar);

        _statusText = UIGlass.CreateText("StatusText", topBar, "数据源：连接中...", 16, Color.white,
            new Vector2(0f, 0f), new Vector2(0.28f, 1f), new Vector2(18f, 0f), new Vector2(-4f, 0f), GetFont());
        _avgText = UIGlass.CreateText("AvgText", topBar, "平均湿度 --%", 16, new Color(0.42f, 0.85f, 1f),
            new Vector2(0.28f, 0f), new Vector2(0.46f, 1f), new Vector2(4f, 0f), new Vector2(-4f, 0f), GetFont());
        _weatherText = UIGlass.CreateText("WeatherText", topBar, "天气：加载中...", 16, Color.white,
            new Vector2(0.46f, 0f), new Vector2(0.90f, 1f), new Vector2(4f, 0f), new Vector2(-8f, 0f), GetFont());

        // 收起按钮（顶栏右侧）
        Button collapseBtn = UIGlass.CreateButton("CollapseBtn", topBar, "收起 ▲", UIGlass.ButtonCollapse,
            new Vector2(0.93f, 0f), new Vector2(1f, 1f), new Vector2(0f, 8f), new Vector2(-8f, -6f),
            GetFont(), 15, ToggleCollapse);
        Text collapseText = collapseBtn.GetComponentInChildren<Text>();
        if (collapseText != null) _collapseLabel = collapseText;

        // ---- 可收起内容容器 ----
        RectTransform content = UIGlass.CreateRect("Content", cv, Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
        _contentGroup = content.gameObject.AddComponent<CanvasGroup>();
        _contentGroup.alpha = 1f;
        _contentGroup.blocksRaycasts = true;
        _contentGroup.interactable = true;

        // ---- 左上：3 张农田卡片（300 × 96，间距 10） ----
        RectTransform cards = UIGlass.CreateRect("FieldCards", content, new Vector2(0f, 1f), new Vector2(0f, 1f),
                                                 new Vector2(12f, -376f), new Vector2(312f, -68f));
        BuildFieldCards(cards);

        // ---- 右上：预测图表（636 × 368） ----
        RectTransform chartPanel = UIGlass.CreateRect("ChartPanel", content, new Vector2(1f, 1f), new Vector2(1f, 1f),
                                                      new Vector2(-648f, -436f), new Vector2(-12f, -68f));
        UIGlass.CreateGlass(chartPanel, UIGlass.GlassCard);
        BuildChartPanel(chartPanel);

        // ---- 底部细控制栏 ----
        RectTransform bottomBar = UIGlass.CreateRect("BottomBar", content, new Vector2(0f, 0f), new Vector2(1f, 0f),
                                                     new Vector2(12f, 12f), new Vector2(-12f, 64f));
        UIGlass.CreateGlass(bottomBar, UIGlass.GlassBar);
        BuildBottomBar(bottomBar);
    }

    private void BuildFieldCards(RectTransform cards)
    {
        for (int i = 0; i < 3; i++)
        {
            // 卡片自上而下排列
            float top = i * (96f + 10f);
            RectTransform card = UIGlass.CreateRect("Card" + (i + 1), cards,
                new Vector2(0f, 1f), new Vector2(1f, 1f),
                new Vector2(0f, -(top + 96f)), new Vector2(0f, -top));
            UIGlass.CreateGlass(card, UIGlass.GlassCard);

            // 标题（含湿度色圆点）
            _channelTitleTexts[i] = UIGlass.CreateText("Title", card, string.Format("● 农田 {0}", i + 1),
                17, Color.white,
                new Vector2(0f, 0.64f), new Vector2(0.56f, 1f), new Vector2(14f, -2f), new Vector2(-2f, -2f),
                GetFont(), TextAnchor.MiddleLeft, FontStyle.Bold);

            // 泵状态（右上角）
            _channelPumpTexts[i] = UIGlass.CreateText("PumpState", card, "⏸ 待机", 14, new Color(0.72f, 0.74f, 0.78f),
                new Vector2(0.56f, 0.64f), new Vector2(1f, 1f), new Vector2(2f, -2f), new Vector2(-10f, -2f),
                GetFont(), TextAnchor.MiddleRight);

            // 湿度进度条（背景 + 锚点拉伸填充）
            RectTransform bar = UIGlass.CreateRect("Bar", card, new Vector2(0f, 0.30f), new Vector2(0.74f, 0.60f),
                                                   new Vector2(14f, 0f), new Vector2(-6f, -4f));
            UIGlass.CreateGlass(bar, new Color(0f, 0f, 0f, 0.35f), outline: false);
            Image fill = UIGlass.CreateFill(bar);
            _channelBarFills[i] = fill;
            UIGlass.SetBarRatio(fill, 0f, ProceduralStyle.SoilNormal);

            // 湿度数值（进度条右侧）
            _channelMoistureTexts[i] = UIGlass.CreateText("Moisture", card, "--%", 19, Color.white,
                new Vector2(0.76f, 0.30f), new Vector2(1f, 0.60f), new Vector2(2f, 0f), new Vector2(-10f, -4f),
                GetFont(), TextAnchor.MiddleRight, FontStyle.Bold);

            // 灌溉 / 停止按钮
            int index = i;
            UIGlass.CreateButton("StartBtn", card, "灌溉", UIGlass.ButtonStart,
                new Vector2(0f, 0f), new Vector2(0.47f, 0.28f), new Vector2(14f, 4f), new Vector2(-4f, -2f),
                GetFont(), 15, () => StartPump(index + 1));
            UIGlass.CreateButton("StopBtn", card, "停止", UIGlass.ButtonStop,
                new Vector2(0.53f, 0f), new Vector2(1f, 0.28f), new Vector2(4f, 4f), new Vector2(-14f, -2f),
                GetFont(), 15, () => StopPump(index + 1));
        }
    }

    private void BuildChartPanel(RectTransform panel)
    {
        UIGlass.CreateText("ChartTitle", panel, string.Format("湿度预测 · 未来 {0} 分钟", predictionMinutes),
            16, Color.white,
            new Vector2(0f, 0.90f), new Vector2(1f, 1f), new Vector2(18f, 0f), new Vector2(-18f, 0f),
            GetFont(), TextAnchor.MiddleLeft, FontStyle.Bold);

        _chartLegend = UIGlass.CreateText("Legend", panel, "●农田1  ●农田2  ●农田3", 13, new Color(0.82f, 0.84f, 0.88f),
            new Vector2(0f, 0.82f), new Vector2(1f, 0.90f), new Vector2(18f, 0f), new Vector2(-18f, 0f), GetFont());

        // 图表区
        RectTransform chartArea = UIGlass.CreateRect("Chart", panel, new Vector2(0f, 0.30f), new Vector2(1f, 0.82f),
                                                     new Vector2(18f, 0f), new Vector2(-18f, -2f));
        UIGlass.CreateGlass(chartArea, UIGlass.GlassDark, outline: false);
        _chart = chartArea.gameObject.AddComponent<UILineChart>();
        _chart.SetData(new[] { new[] { 50f, 50f, 50f, 50f } }, 0f, 100f);

        // 灌溉统计（节水数据，比赛工程效果）
        _statsText = UIGlass.CreateText("IrrigationStats", panel, "💧 灌溉统计加载中...", 13, new Color(0.55f, 0.9f, 0.65f),
            new Vector2(0f, 0.16f), new Vector2(1f, 0.29f), new Vector2(18f, 0f), new Vector2(-18f, 0f), GetFont());

        // 天气详情
        _weatherDetail = UIGlass.CreateText("WeatherDetail", panel, "天气数据加载中...", 13, Color.white,
            new Vector2(0f, 0.03f), new Vector2(1f, 0.16f), new Vector2(18f, 0f), new Vector2(-18f, 0f), GetFont());
    }

    private void BuildBottomBar(RectTransform bar)
    {
        _msgText = UIGlass.CreateText("Message", bar, "等待数据...", 15, new Color(0.88f, 0.9f, 0.92f),
            new Vector2(0f, 0f), new Vector2(0.52f, 1f), new Vector2(16f, 0f), Vector2.zero, GetFont());

        UIGlass.CreateButton("StartAll", bar, "全部灌溉", UIGlass.ButtonStart,
            new Vector2(0.53f, 0f), new Vector2(0.68f, 1f), new Vector2(0f, 10f), new Vector2(0f, -10f),
            GetFont(), 16, () => StartPump(0));
        UIGlass.CreateButton("StopAll", bar, "全部停止", UIGlass.ButtonStop,
            new Vector2(0.69f, 0f), new Vector2(0.80f, 1f), new Vector2(0f, 10f), new Vector2(0f, -10f),
            GetFont(), 16, () => StopPump(0));
        UIGlass.CreateButton("AutoMode", bar, "自动模式", UIGlass.ButtonAuto,
            new Vector2(0.81f, 0f), new Vector2(0.99f, 1f), new Vector2(0f, 10f), new Vector2(0f, -10f),
            GetFont(), 16, AutoMode);
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
