using UnityEngine;

/// <summary>单个花盆单元的句柄（供数据驱动层改色/挂粒子）</summary>
public class FarmlandUnit
{
    public GameObject root;              // 花盆根节点
    public Renderer soilRenderer;        // 土壤渲染器（独立材质实例，随湿度改色）
    public Transform soilTop;            // 土壤表面挂点（水流粒子位置）
    public Transform plantAnchor;        // 作物挂点
}

/// <summary>
/// 程序化花盆（3 个）：方形种植箱 + 内部土壤层。
/// 每个花盆的土壤使用独立材质实例，便于数据驱动按湿度实时改色。
/// 命名规范与场景设计文档一致：Planter_1/2/3。
/// </summary>
public class ProceduralFarmland : MonoBehaviour
{
    [Header("花盆尺寸（米）")]
    [SerializeField] private float boxSize = 0.9f;      // 盆体长宽
    [SerializeField] private float boxHeight = 0.38f;   // 盆体高度
    [SerializeField] private float soilInset = 0.1f;    // 土壤相对盆体的缩进
    [SerializeField] private float soilThick = 0.16f;   // 土壤层厚度

    /// <summary>按世界坐标生成 3 个花盆</summary>
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
        unit.root = new GameObject(string.Format("Planter_{0}", index));
        unit.root.transform.SetParent(parent, false);
        unit.root.transform.position = worldPos;

        // 盆体（木色）
        ProceduralStyle.CreatePart("Box", unit.root.transform, PrimitiveType.Cube,
            ProceduralStyle.PlanterBrown,
            new Vector3(0f, boxHeight * 0.5f, 0f),
            new Vector3(boxSize, boxHeight, boxSize));

        // 盆沿（略大的浅色框，简化表现：外圈压扁 Cube）
        ProceduralStyle.CreatePart("Rim", unit.root.transform, PrimitiveType.Cube,
            new Color(0.68f, 0.50f, 0.34f, 1f),
            new Vector3(0f, boxHeight + 0.015f, 0f),
            new Vector3(boxSize + 0.07f, 0.035f, boxSize + 0.07f));

        // 土壤层（独立材质实例，供湿度改色）
        unit.soilRenderer = ProceduralStyle.CreateDynamicPart(
            "Soil", unit.root.transform, PrimitiveType.Cube,
            ProceduralStyle.SoilNormal,
            new Vector3(0f, boxHeight - soilThick * 0.5f + 0.01f, 0f),
            new Vector3(boxSize - soilInset, soilThick, boxSize - soilInset));

        // 土壤表面挂点（水流粒子出生点）
        unit.soilTop = new GameObject("SoilTop").transform;
        unit.soilTop.SetParent(unit.root.transform, false);
        unit.soilTop.localPosition = new Vector3(0f, boxHeight + 0.01f, 0f);

        // 作物挂点
        unit.plantAnchor = new GameObject("PlantAnchor").transform;
        unit.plantAnchor.SetParent(unit.root.transform, false);
        unit.plantAnchor.localPosition = new Vector3(0f, boxHeight - 0.02f, 0f);

        return unit;
    }
}
