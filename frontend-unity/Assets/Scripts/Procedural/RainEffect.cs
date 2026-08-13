using UnityEngine;

/// <summary>
/// 程序化雨效：代码创建粒子系统，雨丝用拉伸渲染。
/// 由 WeatherController 根据 API 降雨概率调用 SetRain(0~100)。
/// </summary>
public class RainEffect : MonoBehaviour
{
    [Header("雨区范围")]
    [SerializeField] private Vector3 rainArea = new Vector3(14f, 0.1f, 10f);  // 长×高×宽
    [SerializeField] private float rainHeight = 9f;                            // 雨层高度

    public ParticleSystem RainParticles { get; private set; }

    private static Material _rainMaterial;

    /// <summary>创建雨粒子系统（初始关闭）</summary>
    public void Build(Transform parent)
    {
        GameObject go = new GameObject("Rain");
        go.transform.SetParent(parent, false);
        go.transform.localPosition = new Vector3(0f, rainHeight, 0f);
        transform.SetParent(go.transform, false);

        RainParticles = go.AddComponent<ParticleSystem>();

        // 主模块
        ParticleSystem.MainModule main = RainParticles.main;
        main.startLifetime = 0.55f;
        main.startSpeed = 11f;
        main.startSize = 0.06f;
        main.startColor = new Color(0.72f, 0.80f, 0.90f, 0.55f);
        main.gravityModifier = 0.9f;
        main.simulationSpace = ParticleSystemSimulationSpace.World;
        main.maxParticles = 2500;
        main.loop = true;
        main.playOnAwake = true;

        // 发射：默认关闭
        ParticleSystem.EmissionModule emission = RainParticles.emission;
        emission.rateOverTime = 0f;

        // 形状：覆盖场景的盒状区域
        ParticleSystem.ShapeModule shape = RainParticles.shape;
        shape.shapeType = ParticleSystemShapeType.Box;
        shape.scale = rainArea;

        // 渲染：拉伸成雨丝
        ParticleSystemRenderer psr = go.GetComponent<ParticleSystemRenderer>();
        if (psr != null)
        {
            psr.renderMode = ParticleSystemRenderMode.Stretch;
            psr.velocityScale = 0.1f;
            psr.lengthScale = 0.7f;
            psr.material = GetRainMaterial();
        }

        RainParticles.Stop();
    }

    private static Material GetRainMaterial()
    {
        if (_rainMaterial != null) return _rainMaterial;
        Shader s = Shader.Find("Legacy Shaders/Particles/Alpha Blended Premultiply");
        if (s == null) s = Shader.Find("Particles/Standard Unlit");
        if (s == null) s = Shader.Find("Unlit/Color");
        _rainMaterial = new Material(s);
        _rainMaterial.color = new Color(0.72f, 0.80f, 0.90f, 0.55f);
        return _rainMaterial;
    }

    /// <summary>按降雨概率（0~100）设置雨量；≤50 停雨，>50 随概率增大</summary>
    public void SetRain(float probabilityPercent)
    {
        if (RainParticles == null) return;

        ParticleSystem.EmissionModule emission = RainParticles.emission;
        if (probabilityPercent <= 50f)
        {
            emission.rateOverTime = 0f;
            if (RainParticles.isPlaying) RainParticles.Stop();
        }
        else
        {
            // 50%→300 粒子/秒，100%→1800 粒子/秒
            float rate = 300f + (probabilityPercent - 50f) / 50f * 1500f;
            emission.rateOverTime = rate;
            if (!RainParticles.isPlaying) RainParticles.Play();
        }
    }
}
