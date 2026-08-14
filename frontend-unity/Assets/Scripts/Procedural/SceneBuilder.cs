using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

/// <summary>
/// 一键场景生成器 v4.0.0：程序化构建完整的数字孪生灌溉场景。
/// 包含：地面、3 块农田（土壤可变色、成排作物）、设备（水泵/水箱/管道/喷头/传感器）、
/// 喷灌水流粒子、太阳、云、雨、飞鸟、数据轮询、天气联动、玻璃风数据面板 UI、观察相机。
///
/// 使用方式：
/// 1. 场景中挂本脚本（任意物体），勾选 Build On Start，运行即可；
/// 2. 或菜单栏「数字孪生灌溉 → 生成完整场景」在编辑器下直接生成；
/// 3. 数据接入指向 ApiConfig.ServerUrl（默认 localhost:5000）。
/// </summary>
public class SceneBuilder : MonoBehaviour
{
    [Header("生成选项")]
    [Tooltip("运行开始时自动生成场景")]
    [SerializeField] private bool buildOnStart = true;
    [Tooltip("生成程序化数据面板 UI")]
    [SerializeField] private bool buildUI = true;

    [Header("三块农田 x 坐标")]
    [SerializeField] private float[] channelXs = { -3.8f, 0f, 3.8f };

    private void Start()
    {
        if (buildOnStart) BuildScene();
    }

    /// <summary>生成完整场景（幂等：重复调用会先清掉旧场景）</summary>
    public void BuildScene()
    {
        // 清理旧场景
        Transform old = transform.Find("IrrigationScene");
        if (old != null)
        {
            if (Application.isPlaying) Destroy(old.gameObject);
            else DestroyImmediate(old.gameObject);
        }

        Transform root = new GameObject("IrrigationScene").transform;
        root.SetParent(transform, false);

        // 农田世界坐标
        Vector3[] farmPos = new Vector3[channelXs.Length];
        for (int i = 0; i < channelXs.Length; i++)
        {
            farmPos[i] = new Vector3(channelXs[i], 0f, 0f);
        }

        // ---- 1. 地面 ----
        ProceduralGround ground = new GameObject("GroundBuilder").AddComponent<ProceduralGround>();
        ground.Build(root);

        // ---- 2. 农田（3 块） ----
        ProceduralFarmland farmland = new GameObject("FarmlandBuilder").AddComponent<ProceduralFarmland>();
        FarmlandUnit[] farms = farmland.Build(root, farmPos);

        // ---- 3. 作物 ----
        ProceduralPlants plants = new GameObject("PlantsBuilder").AddComponent<ProceduralPlants>();
        for (int i = 0; i < farms.Length; i++)
        {
            plants.Build(farms[i].plantAnchor, i + 1);
        }

        // ---- 4. 设备（水泵/水箱/管道/喷头/传感器） ----
        ProceduralEquipment equipment = new GameObject("EquipmentBuilder").AddComponent<ProceduralEquipment>();
        EquipmentSet eq = equipment.Build(root, channelXs);

        // ---- 5. 喷灌水流粒子（每块农田一个，覆盖整块田，初始关闭） ----
        ParticleSystem[] waterFlows = CreateWaterFlows(root, channelXs);

        // ---- 6. 太阳 ----
        ProceduralSun sun = new GameObject("SunBuilder").AddComponent<ProceduralSun>();
        sun.Build(root);

        // ---- 7. 云 ----
        ProceduralClouds clouds = new GameObject("CloudsBuilder").AddComponent<ProceduralClouds>();
        clouds.Build(root);

        // ---- 8. 雨 ----
        RainEffect rain = new GameObject("RainBuilder").AddComponent<RainEffect>();
        rain.Build(root);

        // ---- 9. 飞鸟 ----
        ProceduralBirds birds = new GameObject("BirdsBuilder").AddComponent<ProceduralBirds>();
        birds.Build(root);

        // ---- 10. 观察相机（先建相机，供天气控制器绑定天空背景） ----
        SetupCamera();

        // ---- 11. 数据层（轮询 + 视觉绑定 + 天气联动） ----
        RealtimeDataPoller poller = new GameObject("DataPoller").AddComponent<RealtimeDataPoller>();

        SceneDataBinder binder = new GameObject("SceneDataBinder").AddComponent<SceneDataBinder>();
        binder.Bind(farms, eq.pumps, eq.sensors, waterFlows, poller);

        WeatherController weather = new GameObject("WeatherController").AddComponent<WeatherController>();
        weather.Configure(Camera.main, sun, clouds, rain, birds);

        // ---- 12. 数据面板 UI ----
        if (buildUI)
        {
            DataPanelUI ui = new GameObject("DataPanelUI").AddComponent<DataPanelUI>();
            ui.Build(poller, weather);
        }

        Debug.Log("[SceneBuilder] 场景生成完成：3 块农田 + 天气联动 + 玻璃风数据面板");
    }

    /// <summary>创建 3 个喷灌水流粒子系统（Box 覆盖整块农田，向下飘洒，默认关闭）</summary>
    private ParticleSystem[] CreateWaterFlows(Transform root, float[] xs)
    {
        ParticleSystem[] flows = new ParticleSystem[xs.Length];
        for (int i = 0; i < xs.Length; i++)
        {
            GameObject go = new GameObject(string.Format("WaterFlow_{0}", i + 1));
            go.transform.SetParent(root, false);
            // 喷头正下方（覆盖对应农田）
            go.transform.localPosition = new Vector3(xs[i], 1.5f, 0.1f);

            ParticleSystem ps = go.AddComponent<ParticleSystem>();
            ParticleSystem.MainModule main = ps.main;
            main.startLifetime = 0.5f;
            main.startSpeed = 0.5f;
            main.startSize = 0.05f;
            main.startColor = new Color(0.42f, 0.72f, 0.96f, 0.85f);
            main.gravityModifier = 1.4f;
            main.simulationSpace = ParticleSystemSimulationSpace.World;
            main.maxParticles = 1000;
            main.loop = true;
            main.playOnAwake = false;

            // 发射：覆盖整块农田的盒状区域（3m × 2.4m），从喷头高度向下飘洒
            ParticleSystem.EmissionModule emission = ps.emission;
            emission.rateOverTime = 500f;

            ParticleSystem.ShapeModule shape = ps.shape;
            shape.shapeType = ParticleSystemShapeType.Box;
            shape.scale = new Vector3(2.9f, 0.06f, 2.3f);

            ParticleSystemRenderer psr = go.GetComponent<ParticleSystemRenderer>();
            psr.material = GetWaterMaterial();

            ps.Stop();
            flows[i] = ps;
        }
        return flows;
    }

    private static Material _waterMaterial;
    private static Material GetWaterMaterial()
    {
        if (_waterMaterial != null) return _waterMaterial;
        Shader s = Shader.Find("Legacy Shaders/Particles/Alpha Blended Premultiply");
        if (s == null) s = Shader.Find("Particles/Standard Unlit");
        if (s == null) s = Shader.Find("Unlit/Color");
        _waterMaterial = new Material(s);
        _waterMaterial.color = new Color(0.42f, 0.72f, 0.96f, 0.9f);
        return _waterMaterial;
    }

    /// <summary>设置主相机（观察相机 + 背景色）</summary>
    private void SetupCamera()
    {
        Camera cam = Camera.main;
        if (cam == null)
        {
            GameObject camGo = new GameObject("Main Camera");
            camGo.tag = "MainCamera";
            cam = camGo.AddComponent<Camera>();
            camGo.AddComponent<AudioListener>();
        }

        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = ProceduralStyle.SkyDayBlue;

        OrbitCamera orbit = cam.GetComponent<OrbitCamera>();
        if (orbit == null) orbit = cam.gameObject.AddComponent<OrbitCamera>();
        cam.transform.position = new Vector3(0f, 6.5f, 11.5f);
        cam.transform.LookAt(new Vector3(0f, 0.5f, 0f));
        orbit.enabled = true;   // 确保启用（Start 时会按当前位姿初始化）
    }
}

#if UNITY_EDITOR
public static class SceneBuilderMenu
{
    [MenuItem("数字孪生灌溉/一键生成完整场景")]
    public static void BuildSceneFromMenu()
    {
        SceneBuilder builder = Object.FindObjectOfType<SceneBuilder>();
        if (builder == null)
        {
            builder = new GameObject("SceneBuilder").AddComponent<SceneBuilder>();
        }
        builder.BuildScene();
        Debug.Log("场景已生成（菜单触发）");
    }
}
#endif
