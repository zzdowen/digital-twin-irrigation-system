using UnityEngine;

/// <summary>
/// 程序化作物 v4.0.0（小白菜风格低模）：中心茎 + 多层展开叶片 + 嫩芯。
/// 农田模式：沿 3 条种植垄按 3 行 × 4 列成排种植（每块农田 12 株），
/// 株间带随机抖动，形成整齐又不呆板的田间效果。无贴图依赖，纯几何体组合。
/// </summary>
public class ProceduralPlants : MonoBehaviour
{
    [Header("每块农田的行列布局")]
    [SerializeField] private int rows = 3;           // 行数（沿 z 方向，对应 3 条垄）
    [SerializeField] private int cols = 4;           // 列数（沿 x 方向）
    [SerializeField] private float rowSpacing = 0.6f;   // 行距（z）
    [SerializeField] private float colSpacing = 0.75f;  // 株距（x）
    [SerializeField] private float jitter = 0.1f;       // 随机抖动幅度

    [Header("植株尺寸（米）")]
    [SerializeField] private float plantHeight = 0.32f;

    private static System.Random _rng = new System.Random(20260814);

    /// <summary>在挂点下按 3 行 × 4 列生成一田作物</summary>
    public void Build(Transform plantAnchor, int fieldIndex)
    {
        if (plantAnchor == null) return;

        int p = 0;
        for (int r = 0; r < rows; r++)
        {
            float z = (r - (rows - 1) * 0.5f) * rowSpacing;   // 居中对称
            for (int c = 0; c < cols; c++)
            {
                float x = (c - (cols - 1) * 0.5f) * colSpacing;
                float jx = (float)(_rng.NextDouble() * 2.0 - 1.0) * jitter;
                float jz = (float)(_rng.NextDouble() * 2.0 - 1.0) * jitter;
                Vector3 localPos = new Vector3(x + jx, 0f, z + jz);
                BuildOne(plantAnchor, localPos, fieldIndex, p + 1);
                p++;
            }
        }
    }

    private void BuildOne(Transform anchor, Vector3 localPos, int fieldIndex, int index)
    {
        Transform plant = new GameObject(string.Format("Crop_{0}_{1}", fieldIndex, index)).transform;
        plant.SetParent(anchor, false);
        plant.localPosition = localPos;

        float stemH = plantHeight * 0.4f;
        float stemW = 0.04f;

        // 茎
        ProceduralStyle.CreatePart("Stem", plant, PrimitiveType.Cylinder,
            ProceduralStyle.StemGreen,
            new Vector3(0f, stemH * 0.5f, 0f),
            new Vector3(stemW, stemH * 0.5f, stemW));

        // 外层大叶（4 片，贴地展开）
        BuildLeafLayer(plant, stemH * 0.55f, plantHeight * 0.42f, 4, 0f, ProceduralStyle.LeafGreen);
        // 内层小叶（5 片，略高）
        BuildLeafLayer(plant, stemH * 0.95f, plantHeight * 0.3f, 5, 36f, ProceduralStyle.LeafGreen);
        // 嫩芯
        ProceduralStyle.CreatePart("Core", plant, PrimitiveType.Sphere,
            ProceduralStyle.LeafLight,
            new Vector3(0f, stemH + plantHeight * 0.12f, 0f),
            new Vector3(plantHeight * 0.32f, plantHeight * 0.2f, plantHeight * 0.32f));
    }

    /// <summary>生成一层叶片（压扁球围绕中心均匀分布）</summary>
    private void BuildLeafLayer(Transform plant, float height, float leafSize, int count,
                                float angleOffset, Color color)
    {
        for (int lf = 0; lf < count; lf++)
        {
            float a = (lf * (360f / count) + angleOffset) * Mathf.Deg2Rad;
            // 叶片略向外探出
            float reach = leafSize * 0.55f;
            Vector3 pos = new Vector3(Mathf.Cos(a) * reach, height, Mathf.Sin(a) * reach);
            // 压扁球：宽（径向）× 薄（高）× 长（切向）
            ProceduralStyle.CreatePart("Leaf", plant, PrimitiveType.Sphere, color, pos,
                new Vector3(leafSize, leafSize * 0.18f, leafSize * 0.7f));
        }
    }
}
