using UnityEngine;

/// <summary>单块农田的句柄（供数据驱动层改色/挂粒子）</summary>
public class FarmlandUnit
{
    public GameObject root;              // 农田根节点
    public Renderer soilRenderer;        // 土壤渲染器（独立材质实例，随湿度改色）
    public Transform soilTop;            // 土壤表面挂点（喷灌水流出生点参考）
    public Transform plantAnchor;        // 作物挂点
}

/// <summary>
/// 程序化农田 v4.0.0（3 块）：田埂围边 + 大块可变色土壤层 + 3 条种植垄。
/// 每块农田的土壤使用独立材质实例，便于数据驱动按湿度实时改色。
/// 命名规范：FarmField_1/2/3。
/// </summary>
public class ProceduralFarmland : MonoBehaviour
{
    [Header("农田尺寸（米）")]
    [SerializeField] private float fieldWidth = 3.4f;     // 农田长（x 方向）
    [SerializeField] private float fieldDepth = 2.6f;     // 农田宽（z 方向）
    [SerializeField] private float soilHeight = 0.12f;    // 土壤层厚度
    [SerializeField] private float ridgeHeight = 0.16f;   // 田埂高度
    [SerializeField] private float ridgeWidth = 0.22f;    // 田埂宽度

    [Header("种植垄")]
    [Tooltip("3 条垄的 z 偏移（相对农田中心）")]
    [SerializeField] private float[] rowZs = { -0.6f, 0f, 0.6f };
    [SerializeField] private float rowWidth = 0.5f;       // 垄宽（z）
    [SerializeField] private float rowHeight = 0.04f;     // 垄高（垄顶与田埂齐平）

    /// <summary>按世界坐标生成 3 块农田</summary>
    public FarmlandUnit[] Build(Transform parent, Vector3[] positions)
    {
        FarmlandUnit[] units = new FarmlandUnit[positions.Length];
        for (int i = 0; i < positions.Length; i++)
        {
            units[i] = BuildOne(parent, positions[i], i + 1);
        }
        return units;
    }

    private FarmlandUnit BuildOne(Transform parent, Vector3 worldPos, int index)
    {
        FarmlandUnit unit = new FarmlandUnit();
        unit.root = new GameObject(string.Format("FarmField_{0}", index));
        unit.root.transform.SetParent(parent, false);
        unit.root.transform.position = worldPos;

        // ---- 田埂（四边围框，比土壤略高，形成"一块田"的边界） ----
        // 前后埂（沿 x 方向长条）
        ProceduralStyle.CreatePart("RidgeFront", unit.root.transform, PrimitiveType.Cube,
            ProceduralStyle.RidgeBrown,
            new Vector3(0f, ridgeHeight * 0.5f, fieldDepth * 0.5f),
            new Vector3(fieldWidth + ridgeWidth * 2f, ridgeHeight, ridgeWidth));
        ProceduralStyle.CreatePart("RidgeBack", unit.root.transform, PrimitiveType.Cube,
            ProceduralStyle.RidgeBrown,
            new Vector3(0f, ridgeHeight * 0.5f, -fieldDepth * 0.5f),
            new Vector3(fieldWidth + ridgeWidth * 2f, ridgeHeight, ridgeWidth));
        // 左右埂（沿 z 方向长条）
        ProceduralStyle.CreatePart("RidgeLeft", unit.root.transform, PrimitiveType.Cube,
            ProceduralStyle.RidgeBrown,
            new Vector3(-fieldWidth * 0.5f, ridgeHeight * 0.5f, 0f),
            new Vector3(ridgeWidth, ridgeHeight, fieldDepth));
        ProceduralStyle.CreatePart("RidgeRight", unit.root.transform, PrimitiveType.Cube,
            ProceduralStyle.RidgeBrown,
            new Vector3(fieldWidth * 0.5f, ridgeHeight * 0.5f, 0f),
            new Vector3(ridgeWidth, ridgeHeight, fieldDepth));

        // ---- 土壤层（独立材质实例，供湿度改色） ----
        float soilSize = fieldWidth - ridgeWidth * 0.8f;
        unit.soilRenderer = ProceduralStyle.CreateDynamicPart(
            "Soil", unit.root.transform, PrimitiveType.Cube,
            ProceduralStyle.SoilNormal,
            new Vector3(0f, soilHeight * 0.5f, 0f),
            new Vector3(soilSize, soilHeight, fieldDepth - ridgeWidth * 0.8f));

        // ---- 3 条种植垄（沿 x 方向的隆起土条，作物种在垄上） ----
        for (int r = 0; r < rowZs.Length; r++)
        {
            ProceduralStyle.CreatePart(string.Format("Row_{0}", r + 1), unit.root.transform, PrimitiveType.Cube,
                ProceduralStyle.FurrowDark,
                new Vector3(0f, soilHeight + rowHeight * 0.5f, rowZs[r]),
                new Vector3(soilSize - 0.4f, rowHeight, rowWidth));
        }

        // 土壤表面挂点（喷灌水流出生点参考）
        unit.soilTop = new GameObject("SoilTop").transform;
        unit.soilTop.SetParent(unit.root.transform, false);
        unit.soilTop.localPosition = new Vector3(0f, soilHeight + rowHeight + 0.01f, 0f);

        // 作物挂点
        unit.plantAnchor = new GameObject("PlantAnchor").transform;
        unit.plantAnchor.SetParent(unit.root.transform, false);
        unit.plantAnchor.localPosition = new Vector3(0f, soilHeight + rowHeight, 0f);

        return unit;
    }
}
