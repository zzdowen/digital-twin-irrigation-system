using UnityEngine;

/// <summary>
/// 场景视觉控制器 v3.0.0：根据 3 路实时数据驱动场景中的 3D 模型表现。
/// - 土壤颜色随湿度变化（>60% 深棕 / 30-60% 棕黄 / <30% 浅黄）
/// - 水泵 LED 指示灯（运行=蓝亮，停止=灰）
/// - 灌溉水流粒子（泵开=播放，泵停=停止）
///
/// 挂载方式：挂到场景中任意物体，在 Inspector 配置 ChannelVisualBinding 数组（下标 0-2 = 农田 1-3）。
/// 前提：各农田土壤材质必须是独立实例（否则改色会影响所有农田），LED 材质同理。
/// </summary>
[System.Serializable]
public class ChannelVisualBinding
{
    [Tooltip("该农田土壤渲染器（材质需独立实例）")]
    public Renderer soilRenderer;
    [Tooltip("水泵运行指示灯渲染器")]
    public Renderer pumpLedRenderer;
    [Tooltip("灌溉水流粒子系统（默认关闭）")]
    public ParticleSystem waterFlow;
}

public class SceneVisualController : MonoBehaviour
{
    [Header("数据源")]
    [Tooltip("留空时自动在场景中查找 RealtimeDataPoller")]
    [SerializeField] private RealtimeDataPoller poller;

    [Header("三路场景绑定（下标 0-2 = 农田 1-3）")]
    [SerializeField] private ChannelVisualBinding[] channelVisuals = new ChannelVisualBinding[3];

    [Header("土壤颜色（按湿度分档）")]
    [SerializeField] private Color wetColor    = new Color(0.36f, 0.25f, 0.18f);  // >60% 深棕（湿润）
    [SerializeField] private Color normalColor = new Color(0.55f, 0.43f, 0.39f);  // 30-60% 棕黄（正常）
    [SerializeField] private Color dryColor    = new Color(0.84f, 0.80f, 0.78f);  // <30% 浅黄（干燥）

    [Header("LED 颜色")]
    [SerializeField] private Color ledOnColor  = new Color(0.20f, 0.60f, 1.00f);  // 运行：蓝
    [SerializeField] private Color ledOffColor = new Color(0.40f, 0.40f, 0.40f);  // 停止：灰

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
        }
    }

    private void OnDisable()
    {
        if (poller != null)
        {
            poller.OnDataReceived -= OnDataReceived;
        }
    }

    private void OnDataReceived(RealtimeData data)
    {
        if (data.channels == null) return;

        for (int i = 0; i < channelVisuals.Length && i < data.channels.Length; i++)
        {
            ChannelVisualBinding v = channelVisuals[i];
            ChannelData ch = data.channels[i];
            if (v == null || ch == null) continue;

            // 1. 土壤颜色
            if (v.soilRenderer != null && v.soilRenderer.material != null)
            {
                Color c = ch.soil_moisture > 60f ? wetColor
                        : (ch.soil_moisture >= 30f ? normalColor : dryColor);
                v.soilRenderer.material.color = c;
            }

            // 2. 水泵 LED
            if (v.pumpLedRenderer != null && v.pumpLedRenderer.material != null)
            {
                Material mat = v.pumpLedRenderer.material;
                Color led = ch.pump_status ? ledOnColor : ledOffColor;
                mat.color = led;
                if (mat.HasProperty("_EmissionColor"))
                {
                    mat.SetColor("_EmissionColor", ch.pump_status ? led * 2.0f : Color.black);
                }
            }

            // 3. 水流粒子
            if (v.waterFlow != null)
            {
                if (ch.pump_status && !v.waterFlow.isPlaying) v.waterFlow.Play();
                else if (!ch.pump_status && v.waterFlow.isPlaying) v.waterFlow.Stop();
            }
        }
    }
}
