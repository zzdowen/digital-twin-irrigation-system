using UnityEngine;
using System.Collections.Generic;

/// <summary>
/// 程序化飞鸟：低模 V 形剪影（两片薄板成角），在天空盘旋并扇动翅膀。
/// 雨天自动藏起（天气联动，由 WeatherController 调用 SetRaining）。
/// </summary>
public class ProceduralBirds : MonoBehaviour
{
    [Header("鸟群参数")]
    [SerializeField] private int birdCount = 4;          // 鸟数量
    [SerializeField] private float flockHeight = 10f;    // 飞行高度
    [SerializeField] private float circleRadius = 6f;    // 盘旋半径
    [SerializeField] private float flapSpeed = 7f;       // 扇翅速度
    [SerializeField] private float wingSpan = 0.55f;     // 单侧翼展

    private class Bird
    {
        public Transform root;
        public Transform wingL;
        public Transform wingR;
        public float phase;      // 相位（位置角度）
        public float flapPhase;  // 扇翅相位
        public float speedMul;
    }

    private readonly List<Bird> _birds = new List<Bird>();
    private bool _raining;
    private Transform _root;

    /// <summary>生成鸟群</summary>
    public void Build(Transform parent)
    {
        _root = new GameObject("Birds").transform;
        _root.SetParent(parent, false);

        for (int b = 0; b < birdCount; b++)
        {
            Bird bird = new Bird();
            bird.root = new GameObject("Bird_" + (b + 1)).transform;
            bird.root.SetParent(_root, false);
            bird.phase = b * (Mathf.PI * 2f / birdCount);
            bird.flapPhase = b * 1.3f;
            bird.speedMul = 0.8f + b * 0.12f;

            // 两片翅膀（薄 Cube 成 V 形），黑色剪影
            bird.wingL = ProceduralStyle.CreatePart("WingL", bird.root, PrimitiveType.Cube,
                ProceduralStyle.BirdDark,
                new Vector3(-wingSpan * 0.35f, 0f, 0.28f * wingSpan),
                new Vector3(wingSpan, 0.02f, 0.1f)).transform;
            bird.wingR = ProceduralStyle.CreatePart("WingR", bird.root, PrimitiveType.Cube,
                ProceduralStyle.BirdDark,
                new Vector3(wingSpan * 0.35f, 0f, 0.28f * wingSpan),
                new Vector3(wingSpan, 0.02f, 0.1f)).transform;

            // 身体（小球）
            ProceduralStyle.CreatePart("Body", bird.root, PrimitiveType.Sphere,
                ProceduralStyle.BirdDark,
                new Vector3(0f, 0f, 0f),
                new Vector3(0.1f, 0.08f, 0.22f));

            _birds.Add(bird);
        }
    }

    /// <summary>天气联动：雨天鸟群归巢</summary>
    public void SetRaining(bool raining)
    {
        _raining = raining;
        if (_root != null) _root.gameObject.SetActive(!raining);
    }

    private void Update()
    {
        if (_raining) return;

        float t = Time.time;
        foreach (Bird b in _birds)
        {
            if (b.root == null) continue;

            // 盘旋：绕场景中心圆周运动
            float angle = b.phase + t * 0.12f * b.speedMul;
            float x = Mathf.Cos(angle) * circleRadius;
            float z = Mathf.Sin(angle) * circleRadius * 0.6f;
            float y = flockHeight + Mathf.Sin(t * 0.5f + b.phase) * 1.2f;
            b.root.position = new Vector3(x, y, z);

            // 面向运动方向（切向）
            Vector3 tangent = new Vector3(-Mathf.Sin(angle), 0f, Mathf.Cos(angle) * 0.6f).normalized;
            if (tangent.sqrMagnitude > 0.01f)
            {
                b.root.rotation = Quaternion.LookRotation(tangent);
            }

            // 扇翅：绕局部 X 轴摆动
            float flap = Mathf.Sin(t * flapSpeed + b.flapPhase) * 38f;
            if (b.wingL != null) b.wingL.localRotation = Quaternion.Euler(0f, 0f, flap);
            if (b.wingR != null) b.wingR.localRotation = Quaternion.Euler(0f, 0f, -flap);
        }
    }
}
