using UnityEngine;
using System.Collections.Generic;

/// <summary>
/// 程序化低模云：多组球体组合，随天气改变数量/颜色/高度，并缓慢飘动。
/// WeatherController 调用 SetCloudiness(0~1) 实现晴→阴的过渡。
/// </summary>
public class ProceduralClouds : MonoBehaviour
{
    [Header("云参数")]
    [SerializeField] private int maxClouds = 5;          // 最大云朵数
    [SerializeField] private float cloudHeight = 14f;    // 云层高度
    [SerializeField] private float driftSpeed = 0.35f;   // 飘动速度

    private class Cloud
    {
        public Transform root;
        public List<Renderer> puffRenderers = new List<Renderer>();
        public float speedFactor;
    }

    private readonly List<Cloud> _clouds = new List<Cloud>();
    private float _cloudiness;      // 0~1
    private Transform _root;

    private static System.Random _rng = new System.Random(20260814);

    /// <summary>生成云层</summary>
    public void Build(Transform parent)
    {
        _root = new GameObject("Clouds").transform;
        _root.SetParent(parent, false);

        for (int c = 0; c < maxClouds; c++)
        {
            _clouds.Add(BuildCloud(c));
        }
        SetCloudiness(0.4f);
    }

    private Cloud BuildCloud(int index)
    {
        Cloud cloud = new Cloud();
        cloud.root = new GameObject("Cloud_" + (index + 1)).transform;
        cloud.root.SetParent(_root, false);

        float x = (float)(_rng.NextDouble() * 22 - 11);
        float z = (float)(_rng.NextDouble() * 16 - 8);
        cloud.root.localPosition = new Vector3(x, cloudHeight + (float)_rng.NextDouble() * 3f, z);
        cloud.speedFactor = 0.6f + (float)_rng.NextDouble() * 0.8f;

        // 主云团 + 周边小云球
        int puffs = 3 + _rng.Next(3);
        Vector3 center = Vector3.zero;
        float baseScale = 1.6f + (float)_rng.NextDouble() * 1.6f;
        for (int p = 0; p < puffs; p++)
        {
            Vector3 local = center + new Vector3(
                ((float)_rng.NextDouble() - 0.5f) * baseScale * 1.4f,
                ((float)_rng.NextDouble() - 0.5f) * baseScale * 0.45f,
                ((float)_rng.NextDouble() - 0.5f) * baseScale * 0.9f);
            float s = baseScale * (0.55f + (float)_rng.NextDouble() * 0.5f);
            GameObject puff = ProceduralStyle.CreatePart("Puff", cloud.root, PrimitiveType.Sphere,
                ProceduralStyle.CloudWhite, local, Vector3.one * s, rampSteps: 4f);
            Renderer r = puff.GetComponent<MeshRenderer>();
            if (r != null) cloud.puffRenderers.Add(r);
        }
        return cloud;
    }

    /// <summary>设置云量 0~1（0=万里无云，1=乌云密布）</summary>
    public void SetCloudiness(float amount)
    {
        _cloudiness = Mathf.Clamp01(amount);

        int visibleCount = Mathf.RoundToInt(_cloudiness * _clouds.Count);
        for (int i = 0; i < _clouds.Count; i++)
        {
            bool visible = i < visibleCount;
            Cloud c = _clouds[i];
            c.root.gameObject.SetActive(visible);
            if (!visible) continue;

            Color tint = Color.Lerp(ProceduralStyle.CloudWhite, ProceduralStyle.CloudGray, _cloudiness);
            foreach (Renderer r in c.puffRenderers)
            {
                if (r != null && r.sharedMaterial != null)
                {
                    r.sharedMaterial.color = tint;
                }
            }
        }
    }

    private void Update()
    {
        // 云层缓慢横向飘动，出界后循环
        float t = Time.deltaTime * driftSpeed;
        foreach (Cloud c in _clouds)
        {
            if (!c.root.gameObject.activeSelf) continue;
            Vector3 pos = c.root.localPosition;
            pos.x += t * c.speedFactor;
            if (pos.x > 12f) pos.x = -12f;
            if (pos.x < -12f) pos.x = 12f;
            c.root.localPosition = pos;
        }
    }
}
