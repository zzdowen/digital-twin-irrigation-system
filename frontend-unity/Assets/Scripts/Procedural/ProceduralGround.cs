using UnityEngine;

/// <summary>
/// 程序化地面：温室平台（草地）+ 花盆区石板 + 设备区平台。
/// 尺寸按场景设计文档：整体约 12m × 9m，纯展示无碰撞体。
/// </summary>
public class ProceduralGround : MonoBehaviour
{
    /// <summary>生成地面，返回根节点</summary>
    public Transform Build(Transform parent)
    {
        Transform root = new GameObject("Ground").transform;
        root.SetParent(parent, false);

        // 主地面（Plane 原生 10×10 单位，缩放到 12m × 9m）
        ProceduralStyle.CreatePart("GroundPlane", root, PrimitiveType.Plane,
            ProceduralStyle.GrassGreen,
            new Vector3(0f, 0f, 0f),
            new Vector3(1.2f, 1f, 0.9f));

        // 花盆区石板（3 个花盆正下方，微微高出地面）
        ProceduralStyle.CreatePart("PlanterPad", root, PrimitiveType.Cube,
            ProceduralStyle.PathGray,
            new Vector3(0f, 0.015f, 0f),
            new Vector3(7.4f, 0.03f, 1.6f));

        // 设备区平台（后方：水泵/水箱/管道）
        ProceduralStyle.CreatePart("EquipmentPad", root, PrimitiveType.Cube,
            ProceduralStyle.PathGray,
            new Vector3(0f, 0.015f, -2.4f),
            new Vector3(7.4f, 0.03f, 1.8f));

        // 两侧装饰草地（低模小土丘，增加画面层次）
        ProceduralStyle.CreatePart("HillL", root, PrimitiveType.Sphere,
            new Color(0.62f, 0.82f, 0.50f, 1f),
            new Vector3(-5.2f, -0.6f, -1.2f),
            new Vector3(2.4f, 0.9f, 1.6f));
        ProceduralStyle.CreatePart("HillR", root, PrimitiveType.Sphere,
            new Color(0.58f, 0.80f, 0.48f, 1f),
            new Vector3(5.2f, -0.6f, 0.5f),
            new Vector3(2.6f, 0.8f, 1.8f));

        return root;
    }
}
