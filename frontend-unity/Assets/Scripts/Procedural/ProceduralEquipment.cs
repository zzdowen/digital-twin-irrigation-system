using UnityEngine;

/// <summary>水泵单元句柄（LED 独立材质供状态改色）</summary>
public class PumpUnit
{
    public GameObject root;
    public Renderer ledRenderer;
    public Material ledMaterial;
}

/// <summary>传感器单元句柄（状态灯独立材质）</summary>
public class SensorUnit
{
    public GameObject root;
    public Renderer ledRenderer;
    public Material ledMaterial;
}

/// <summary>设备组句柄</summary>
public class EquipmentSet
{
    public Transform root;
    public PumpUnit[] pumps;      // 3 台水泵
    public SensorUnit[] sensors;  // 3 个传感器
    public Transform tank;        // 水箱
}

/// <summary>
/// 程序化设备：3 台水泵 + 1 个水箱 + 主管道与 3 支喷头管 + 3 个土壤湿度传感器探针。
/// 布局与场景设计文档一致：设备区在花盆后方（z = -2.4），花盆在 z = 0。
/// </summary>
public class ProceduralEquipment : MonoBehaviour
{
    [Header("布局参数")]
    [SerializeField] private float pumpSpacing = 2f;      // 水泵间距（与花盆对齐）
    [SerializeField] private float equipmentZ = -2.4f;    // 设备区 z
    [SerializeField] private float pipeHeight = 0.35f;    // 主管高度
    [SerializeField] private float nozzleHeight = 0.95f;  // 喷头管高度
    [SerializeField] private float tankX = 3.6f;          // 水箱 x

    /// <summary>生成全部设备（pumpXs 为 3 台水泵的 x 坐标）</summary>
    public EquipmentSet Build(Transform parent, float[] pumpXs)
    {
        EquipmentSet set = new EquipmentSet();
        set.root = new GameObject("Equipment").transform;
        set.root.SetParent(parent, false);

        // ---- 3 台水泵 ----
        set.pumps = new PumpUnit[pumpXs.Length];
        for (int i = 0; i < pumpXs.Length; i++)
        {
            set.pumps[i] = BuildPump(set.root, pumpXs[i], equipmentZ, i + 1);
        }

        // ---- 水箱 ----
        set.tank = BuildTank(set.root, tankX, equipmentZ);

        // ---- 管道 ----
        BuildPipes(set.root, pumpXs);

        // ---- 3 个传感器探针（插在花盆土壤上，花盆 z=0）----
        set.sensors = new SensorUnit[pumpXs.Length];
        for (int i = 0; i < pumpXs.Length; i++)
        {
            set.sensors[i] = BuildSensor(set.root, pumpXs[i], 0f, i + 1);
        }

        return set;
    }

    private PumpUnit BuildPump(Transform parent, float x, float z, int index)
    {
        PumpUnit unit = new PumpUnit();
        unit.root = new GameObject(string.Format("Pump_{0}", index));
        unit.root.transform.SetParent(parent, false);
        unit.root.transform.localPosition = new Vector3(x, 0f, z);

        // 泵体
        ProceduralStyle.CreatePart("Body", unit.root.transform, PrimitiveType.Cube,
            ProceduralStyle.PumpBlue,
            new Vector3(0f, 0.14f, 0f),
            new Vector3(0.34f, 0.28f, 0.22f));
        // 出水嘴
        ProceduralStyle.CreatePart("Nozzle", unit.root.transform, PrimitiveType.Cylinder,
            ProceduralStyle.MetalDark,
            new Vector3(0f, 0.32f, 0f),
            new Vector3(0.05f, 0.08f, 0.05f));
        // 底座
        ProceduralStyle.CreatePart("Base", unit.root.transform, PrimitiveType.Cube,
            ProceduralStyle.MetalDark,
            new Vector3(0f, 0.03f, 0f),
            new Vector3(0.4f, 0.06f, 0.28f));
        // LED（独立材质实例）
        unit.ledRenderer = ProceduralStyle.CreateDynamicPart("Led", unit.root.transform,
            PrimitiveType.Sphere, ProceduralStyle.LedOff,
            new Vector3(0.12f, 0.14f, 0.11f),
            new Vector3(0.05f, 0.05f, 0.05f));
        unit.ledMaterial = unit.ledRenderer != null ? unit.ledRenderer.sharedMaterial : null;

        return unit;
    }

    private Transform BuildTank(Transform parent, float x, float z)
    {
        Transform tank = new GameObject("WaterTank").transform;
        tank.SetParent(parent, false);
        tank.localPosition = new Vector3(x, 0f, z);

        // 半透明箱体（用独立材质以支持透明）
        GameObject body = ProceduralStyle.CreatePart("Body", tank, PrimitiveType.Cube,
            ProceduralStyle.TankBlue,
            new Vector3(0f, 0.42f, 0f),
            new Vector3(0.9f, 0.84f, 0.6f));
        MeshRenderer mr = body.GetComponent<MeshRenderer>();
        if (mr != null)
        {
            mr.sharedMaterial = ProceduralStyle.CreateMaterial(ProceduralStyle.TankBlue);
            // 开启透明（ToonLit 不透明分支下改用 Standard 半透明更省事）
            Material m = mr.sharedMaterial;
            m.SetFloat("_Mode", 3f);
            m.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
            m.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            m.SetInt("_ZWrite", 0);
            m.DisableKeyword("_ALPHATEST_ON");
            m.EnableKeyword("_ALPHABLEND_ON");
            m.renderQueue = 3000;
        }
        // 箱盖
        ProceduralStyle.CreatePart("Lid", tank, PrimitiveType.Cube,
            ProceduralStyle.MetalDark,
            new Vector3(0f, 0.87f, 0f),
            new Vector3(0.94f, 0.05f, 0.64f));

        return tank;
    }

    private void BuildPipes(Transform parent, float[] pumpXs)
    {
        Transform pipes = new GameObject("Pipes").transform;
        pipes.SetParent(parent, false);

        // 主管：沿 x 轴横放（cylinder 轴向 y → 绕 z 转 90° 变轴向 x）
        float mainLen = 8f;
        GameObject main = ProceduralStyle.CreatePart("PipeMain", pipes, PrimitiveType.Cylinder,
            ProceduralStyle.PipeWhite,
            new Vector3(0f, pipeHeight, equipmentZ),
            new Vector3(0.07f, mainLen * 0.5f, 0.07f));
        main.transform.localRotation = Quaternion.Euler(0f, 0f, 90f);

        for (int i = 0; i < pumpXs.Length; i++)
        {
            float x = pumpXs[i];
            Transform branch = new GameObject(string.Format("PipeBranch_{0}", i + 1)).transform;
            branch.SetParent(pipes, false);

            // 垂直立管：从主管升到喷头高度
            ProceduralStyle.CreatePart("Riser", branch, PrimitiveType.Cylinder,
                ProceduralStyle.PipeWhite,
                new Vector3(x, (pipeHeight + nozzleHeight) * 0.5f, equipmentZ),
                new Vector3(0.05f, (nozzleHeight - pipeHeight) * 0.5f, 0.05f));

            // 水平喷头管：沿 z 伸到花盆上方（cylinder 绕 x 转 90° 变轴向 z）
            float horizLen = Mathf.Abs(equipmentZ) + 0.15f;
            GameObject arm = ProceduralStyle.CreatePart("Arm", branch, PrimitiveType.Cylinder,
                ProceduralStyle.PipeWhite,
                new Vector3(x, nozzleHeight, equipmentZ * 0.5f + 0.05f),
                new Vector3(0.05f, horizLen * 0.5f, 0.05f));
            arm.transform.localRotation = Quaternion.Euler(90f, 0f, 0f);

            // 喷头出水口（小圆筒，向下）
            ProceduralStyle.CreatePart("NozzleTip", branch, PrimitiveType.Cylinder,
                ProceduralStyle.MetalDark,
                new Vector3(x, nozzleHeight - 0.06f, 0.1f),
                new Vector3(0.06f, 0.06f, 0.06f));
        }
    }

    private SensorUnit BuildSensor(Transform parent, float x, float z, int index)
    {
        SensorUnit unit = new SensorUnit();
        unit.root = new GameObject(string.Format("SoilSensor_{0}", index));
        unit.root.transform.SetParent(parent, false);
        // 放在花盆土壤前沿（花盆边内 0.25）
        unit.root.transform.localPosition = new Vector3(x, 0f, z + 0.22f);

        // 主体小方块（土壤表面）
        ProceduralStyle.CreatePart("Body", unit.root.transform, PrimitiveType.Cube,
            ProceduralStyle.SensorGray,
            new Vector3(0f, 0.42f, 0f),
            new Vector3(0.07f, 0.06f, 0.05f));
        // 探针（插入土中，向下）
        ProceduralStyle.CreatePart("Probe", unit.root.transform, PrimitiveType.Cylinder,
            ProceduralStyle.MetalDark,
            new Vector3(0f, 0.33f, 0f),
            new Vector3(0.012f, 0.11f, 0.012f));
        // 状态灯（独立材质实例）
        unit.ledRenderer = ProceduralStyle.CreateDynamicPart("Led", unit.root.transform,
            PrimitiveType.Sphere, ProceduralStyle.SensorGreen,
            new Vector3(0f, 0.5f, 0f),
            new Vector3(0.028f, 0.028f, 0.028f));
        unit.ledMaterial = unit.ledRenderer != null ? unit.ledRenderer.sharedMaterial : null;

        return unit;
    }
}
