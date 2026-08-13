using UnityEngine;

/// <summary>
/// 程序化作物（小白菜风格低模）：中心茎 + 多层展开叶片 + 嫩芯。
/// 无贴图依赖，纯几何体组合，配合 ToonLit 卡通着色器呈现动漫效果。
/// </summary>
public class ProceduralPlants : MonoBehaviour
{
    [Header("每盆作物数量")]
    [SerializeField] private int plantCount = 4;
    [Header("植株高度（米）")]
    [SerializeField] private float plantHeight = 0.3f;
    [Header("随机偏移半径")]
    [SerializeField] private float scatterRadius = 0.24f;

    private static System.Random _rng = new System.Random(20260814);

    /// <summary>在挂点下生成一盆作物</summary>
    public void Build(Transform plantAnchor, int planterIndex)
    {
        if (plantAnchor == null) return;

        for (int p = 0; p < plantCount; p++)
        {
            BuildOne(plantAnchor, p, planterIndex);
        }
    }

    private void BuildOne(Transform anchor, int index, int planterIndex)
    {
        // 随机散布位置（用固定种子保证每次生成一致，便于调试）
        float angle = (float)_rng.NextDouble() * Mathf.PI * 2f;
        float dist = (float)_rng.NextDouble() * scatterRadius;
        Vector3 localPos = new Vector3(
            Mathf.Cos(angle) * dist,
            0f,
            Mathf.Sin(angle) * dist);

        Transform plant = new GameObject(string.Format("Crop_{0}_{1}", planterIndex, index + 1)).transform;
        plant.SetParent(anchor, false);
        plant.localPosition = localPos;

        float stemH = plantHeight * 0.4f;
        float stemW = 0.035f;

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
