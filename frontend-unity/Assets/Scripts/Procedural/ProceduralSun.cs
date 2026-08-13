using UnityEngine;

/// <summary>
/// 程序化太阳：方向光 + 发光日轮。
/// 旋转根节点即可同时改变光照方向和日轮位置（日轮位于根节点 forward 反方向远处）。
/// 天气联动由 WeatherController 调用 SetWeather 调整颜色与强度。
/// </summary>
public class ProceduralSun : MonoBehaviour
{
    [Header("日轮参数")]
    [SerializeField] private float discDistance = 60f;     // 日轮到场景距离
    [SerializeField] private float discSize = 5f;          // 日轮直径
    [SerializeField] private float baseIntensity = 1.05f;  // 基础光强

    public Light SunLight { get; private set; }
    private Renderer _discRenderer;
    private Material _discMaterial;
    private float _targetIntensity;

    /// <summary>生成太阳（默认 45° 高度角，从东南方照射）</summary>
    public void Build(Transform parent)
    {
        Transform root = new GameObject("Sun").transform;
        root.SetParent(parent, false);
        root.localRotation = Quaternion.Euler(45f, -35f, 0f);
        transform.SetParent(root, false);

        // 方向光（挂在根节点，继承旋转）
        GameObject lightGo = new GameObject("SunLight");
        lightGo.transform.SetParent(root, false);
        SunLight = lightGo.AddComponent<Light>();
        SunLight.type = LightType.Directional;
        SunLight.color = new Color(1f, 0.97f, 0.90f, 1f);
        SunLight.intensity = baseIntensity;
        SunLight.shadows = LightShadows.Soft;
        SunLight.shadowStrength = 0.55f;

        // 日轮（发光球体，置于 forward 反方向远处）
        GameObject disc = ProceduralStyle.CreatePart("SunDisc", root, PrimitiveType.Sphere,
            ProceduralStyle.SunYellow,
            new Vector3(0f, 0f, -discDistance),
            new Vector3(discSize, discSize, discSize), glow: true);
        _discRenderer = disc.GetComponent<MeshRenderer>();
        _discMaterial = _discRenderer != null ? _discRenderer.sharedMaterial : null;

        _targetIntensity = baseIntensity;
    }

    /// <summary>按天气/时段调整太阳（weather: 晴/多云/阴/雨 等中文描述）</summary>
    public void SetWeather(string weather)
    {
        bool rainy = weather.Contains("雨");
        bool cloudy = weather.Contains("云") || weather.Contains("阴");
        bool clear = weather.Contains("晴");

        float intensity = rainy ? 0.55f : (cloudy ? 0.8f : 1.05f);
        _targetIntensity = intensity;
        if (SunLight != null)
        {
            SunLight.intensity = intensity;
            SunLight.color = rainy
                ? new Color(0.85f, 0.88f, 0.95f, 1f)
                : (cloudy
                    ? new Color(0.95f, 0.96f, 0.99f, 1f)
                    : new Color(1f, 0.97f, 0.90f, 1f));
            // 雨天无阴影，晴天软阴影
            SunLight.shadows = rainy ? LightShadows.None : LightShadows.Soft;
        }

        if (_discMaterial != null)
        {
            float alpha = rainy ? 0.12f : (cloudy ? 0.35f : 1f);
            Color c = clear ? ProceduralStyle.SunYellow
                     : new Color(0.9f, 0.9f, 0.92f, 1f);
            _discMaterial.color = new Color(c.r, c.g, c.b, alpha);
            if (_discMaterial.HasProperty("_EmissionColor"))
            {
                _discMaterial.SetColor("_EmissionColor", c * (rainy ? 0.2f : (cloudy ? 0.6f : 1.6f)));
            }
        }
    }

    private void Update()
    {
        // 平滑过渡光强（视觉柔和）
        if (SunLight != null && Mathf.Abs(SunLight.intensity - _targetIntensity) > 0.001f)
        {
            SunLight.intensity = Mathf.Lerp(SunLight.intensity, _targetIntensity, Time.deltaTime * 0.5f);
        }
    }
}
