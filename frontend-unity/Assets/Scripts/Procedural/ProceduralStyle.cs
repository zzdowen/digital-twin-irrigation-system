using UnityEngine;

/// <summary>
/// 程序化场景风格库 v3.0.0：动漫低模调色板 + 材质工厂 + 通用模型部件工具。
/// 所有程序化生成的模型统一走本类，保证画面色调统一、性能可控。
///
/// 说明：
/// 1. 统一使用 Custom/ToonLit 卡通着色器（找不到时回退 Standard，避免报错）；
/// 2. 创建部件默认移除 Collider（纯展示场景无物理，省性能）；
/// 3. 共享材质用 sharedMaterial；需要独立变色的部件（土壤/LED）请用 material 属性。
/// </summary>
public static class ProceduralStyle
{
    // ==================== 动漫调色板 ====================
    // 地面与天空
    public static readonly Color GrassGreen   = new Color(0.55f, 0.78f, 0.45f, 1f);   // 草地
    public static readonly Color PathGray     = new Color(0.78f, 0.76f, 0.70f, 1f);   // 石板路
    public static readonly Color SkyDayBlue   = new Color(0.42f, 0.68f, 0.95f, 1f);   // 晴天天空
    public static readonly Color SkyCloudy    = new Color(0.62f, 0.66f, 0.72f, 1f);   // 阴天天空
    public static readonly Color SkyRainy     = new Color(0.38f, 0.42f, 0.50f, 1f);   // 雨天天空

    // 农田与土壤
    public static readonly Color PlanterBrown = new Color(0.60f, 0.42f, 0.28f, 1f);   // 田埂木色（保留兼容）
    public static readonly Color RidgeBrown   = new Color(0.46f, 0.32f, 0.20f, 1f);   // 田埂土色
    public static readonly Color FurrowDark   = new Color(0.40f, 0.28f, 0.20f, 1f);   // 垄沟深土色
    public static readonly Color WalkwayGray  = new Color(0.83f, 0.81f, 0.76f, 1f);   // 田间步道
    public static readonly Color SoilWet      = new Color(0.33f, 0.23f, 0.15f, 1f);   // 土壤湿润（>60%）
    public static readonly Color SoilNormal   = new Color(0.52f, 0.40f, 0.30f, 1f);   // 土壤正常（30-60%）
    public static readonly Color SoilDry      = new Color(0.78f, 0.70f, 0.58f, 1f);   // 土壤干燥（<30%）

    // 作物
    public static readonly Color LeafGreen    = new Color(0.32f, 0.68f, 0.32f, 1f);   // 叶绿
    public static readonly Color StemGreen    = new Color(0.45f, 0.72f, 0.40f, 1f);   // 茎绿
    public static readonly Color LeafLight    = new Color(0.48f, 0.80f, 0.42f, 1f);   // 嫩叶

    // 设备
    public static readonly Color PumpBlue     = new Color(0.30f, 0.52f, 0.80f, 1f);   // 水泵主体
    public static readonly Color PipeWhite    = new Color(0.88f, 0.88f, 0.85f, 1f);   // PVC 管道
    public static readonly Color TankBlue     = new Color(0.45f, 0.68f, 0.90f, 0.75f); // 水箱半透明
    public static readonly Color SensorGray   = new Color(0.35f, 0.38f, 0.42f, 1f);   // 传感器主体
    public static readonly Color MetalDark    = new Color(0.30f, 0.32f, 0.36f, 1f);   // 金属深色

    // 状态灯
    public static readonly Color LedOn        = new Color(0.25f, 0.70f, 1.00f, 1f);   // 水泵运行（蓝）
    public static readonly Color LedOff       = new Color(0.42f, 0.42f, 0.42f, 1f);   // 水泵停止（灰）
    public static readonly Color SensorGreen  = new Color(0.30f, 0.85f, 0.40f, 1f);   // 传感器正常
    public static readonly Color SensorRed    = new Color(0.90f, 0.30f, 0.25f, 1f);   // 传感器干燥告警

    // 天空物件
    public static readonly Color SunYellow    = new Color(1.00f, 0.88f, 0.55f, 1f);   // 太阳
    public static readonly Color CloudWhite   = new Color(0.96f, 0.96f, 0.97f, 0.92f); // 白云
    public static readonly Color CloudGray    = new Color(0.62f, 0.64f, 0.68f, 0.95f); // 阴云
    public static readonly Color BirdDark     = new Color(0.22f, 0.24f, 0.28f, 1f);   // 飞鸟剪影

    // ==================== 着色器缓存 ====================
    private static Shader _toonShader;
    private static bool _toonSearched;

    /// <summary>卡通着色器（缺失时回退 Standard）</summary>
    public static Shader ToonShader
    {
        get
        {
            if (!_toonSearched)
            {
                _toonShader = Shader.Find("Custom/ToonLit");
                _toonSearched = true;
            }
            return _toonShader != null ? _toonShader : Shader.Find("Standard");
        }
    }

    // ==================== 材质工厂 ====================
    /// <summary>创建统一风格的卡通材质</summary>
    public static Material CreateMaterial(Color color, float rampSteps = 3f)
    {
        Material m = new Material(ToonShader);
        m.color = color;
        if (m.HasProperty("_RampSteps")) m.SetFloat("_RampSteps", rampSteps);
        return m;
    }

    /// <summary>创建自发光材质（太阳、LED 灯等）</summary>
    public static Material CreateGlowMaterial(Color color, float intensity = 1.6f)
    {
        Material m = CreateMaterial(color, 5f);
        m.EnableKeyword("_EMISSION");
        if (m.HasProperty("_EmissionColor"))
        {
            m.SetColor("_EmissionColor", color * intensity);
        }
        return m;
    }

    // ==================== 部件工厂 ====================
    /// <summary>
    /// 创建一个基础模型部件并套用卡通材质。
    /// 返回 GameObject（已挂 MeshRenderer，无 Collider）。
    /// </summary>
    public static GameObject CreatePart(string name, Transform parent, PrimitiveType type,
                                        Color color, Vector3 localPos, Vector3 localScale,
                                        float rampSteps = 3f, bool glow = false)
    {
        GameObject go = GameObject.CreatePrimitive(type);
        go.name = name;
        if (parent != null) go.transform.SetParent(parent, false);
        go.transform.localPosition = localPos;
        go.transform.localScale = localScale;

        // 纯展示：移除碰撞体（编辑器下用 DestroyImmediate）
        Collider col = go.GetComponent<Collider>();
        if (col != null)
        {
            if (Application.isPlaying) Object.Destroy(col);
            else Object.DestroyImmediate(col);
        }

        MeshRenderer mr = go.GetComponent<MeshRenderer>();
        if (mr != null)
        {
            mr.sharedMaterial = glow
                ? CreateGlowMaterial(color)
                : CreateMaterial(color, rampSteps);
        }
        return go;
    }

    /// <summary>
    /// 创建一个基础模型部件并套用卡通材质（可指定局部旋转，用于横放圆柱等）。
    /// 返回 GameObject（已挂 MeshRenderer，无 Collider）。
    /// </summary>
    public static GameObject CreatePart(string name, Transform parent, PrimitiveType type,
                                        Color color, Vector3 localPos, Vector3 localScale,
                                        Quaternion localRotation, float rampSteps = 3f, bool glow = false)
    {
        GameObject go = CreatePart(name, parent, type, color, localPos, localScale, rampSteps, glow);
        go.transform.localRotation = localRotation;
        return go;
    }

    /// <summary>
    /// 创建带独立材质实例的部件（供运行时改色，如土壤湿度变色、LED 状态灯）。
    /// 返回渲染器，调用方通过 renderer.material 改色。
    /// </summary>
    public static Renderer CreateDynamicPart(string name, Transform parent, PrimitiveType type,
                                             Color color, Vector3 localPos, Vector3 localScale)
    {
        GameObject go = CreatePart(name, parent, type, color, localPos, localScale);
        MeshRenderer mr = go.GetComponent<MeshRenderer>();
        if (mr != null)
        {
            // 断开共享，生成独立材质实例
            mr.sharedMaterial = CreateMaterial(color);
        }
        return mr;
    }

    /// <summary>根据湿度返回土壤颜色（与场景设计文档一致）</summary>
    public static Color SoilColorForMoisture(float moisturePercent)
    {
        if (moisturePercent > 60f) return SoilWet;
        if (moisturePercent >= 30f) return SoilNormal;
        return SoilDry;
    }
}
