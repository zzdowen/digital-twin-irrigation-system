using UnityEngine;

/// <summary>
/// 程序化地面 v4.0.0：温室平台（草地）+ 农田区石板 + 田间步道 + 设备区平台。
/// 尺寸适配三块农田布局：整体约 17m × 13m，纯展示无碰撞体。
/// </summary>
public class ProceduralGround : MonoBehaviour
{
    /// <summary>生成地面，返回根节点</summary>
    public Transform Build(Transform parent)
    {
        Transform root = new GameObject("Ground").transform;
        root.SetParent(parent, false);

        // 主地面（Plane 原生 10×10 单位，缩放到 17m × 13m）
        ProceduralStyle.CreatePart("GroundPlane", root, PrimitiveType.Plane,
            ProceduralStyle.GrassGreen,
            new Vector3(0f, 0f, 0f),
            new Vector3(1.7f, 1f, 1.3f));

        // 农田区石板（三块农田正下方，微微高出草地）
        ProceduralStyle.CreatePart("FarmlandPad", root, PrimitiveType.Cube,
            ProceduralStyle.PathGray,
            new Vector3(0f, 0.017f, 0f),
            new Vector3(11.6f, 0.035f, 3.2f));

        // 田间步道（农田前方，供观察与通行）
        ProceduralStyle.CreatePart("Walkway", root, PrimitiveType.Cube,
            ProceduralStyle.WalkwayGray,
            new Vector3(0f, 0.014f, 2.7f),
            new Vector3(11.6f, 0.03f, 1.2f));

        // 设备区平台（后方：水泵/水箱/管道）
        ProceduralStyle.CreatePart("EquipmentPad", root, PrimitiveType.Cube,
            ProceduralStyle.PathGray,
            new Vector3(0f, 0.017f, -3.2f),
            new Vector3(11.6f, 0.035f, 2.4f));

        // 两侧装饰草地（低模小土丘，增加画面层次）
        ProceduralStyle.CreatePart("HillL", root, PrimitiveType.Sphere,
            new Color(0.62f, 0.82f, 0.50f, 1f),
            new Vector3(-7.4f, -0.6f, -1.8f),
            new Vector3(2.6f, 0.9f, 1.8f));
        ProceduralStyle.CreatePart("HillR", root, PrimitiveType.Sphere,
            new Color(0.58f, 0.80f, 0.48f, 1f),
            new Vector3(7.4f, -0.6f, 0.6f),
            new Vector3(2.8f, 0.8f, 2.0f));

        return root;
    }
}
