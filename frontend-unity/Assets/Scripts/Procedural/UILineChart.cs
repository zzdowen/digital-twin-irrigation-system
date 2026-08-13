using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// 轻量折线图组件（MaskableGraphic）：纯代码绘制，无贴图依赖。
/// 支持多条数据序列（如 3 路湿度预测曲线），带面积填充、数据点与图例色。
/// 用法：挂到 UI 物体上（有 RectTransform），调用 SetData(float[][], min, max) 刷新。
/// </summary>
public class UILineChart : MaskableGraphic
{
    [Header("绘制参数")]
    [SerializeField] private float lineWidth = 2.5f;
    [SerializeField] private bool showFill = true;
    [SerializeField] private bool showPoints = true;
    [SerializeField] private float pointSize = 2.6f;

    [Header("数据")]
    [SerializeField] private float[] singleValues;    // 编辑器预览用单序列
    [SerializeField] private Color previewColor = new Color(0.3f, 0.7f, 1f, 1f);

    [Header("三路序列颜色（花盆1/2/3）")]
    [SerializeField] private Color series1Color = new Color(0.30f, 0.70f, 1.00f, 1f);
    [SerializeField] private Color series2Color = new Color(0.35f, 0.85f, 0.45f, 1f);
    [SerializeField] private Color series3Color = new Color(1.00f, 0.70f, 0.30f, 1f);

    private float[][] _series;
    private float _minValue;
    private float _maxValue;

    protected override void Awake()
    {
        base.Awake();
        // 编辑器预览：把单序列提升为多序列
        if (singleValues != null && singleValues.Length > 1)
        {
            _series = new[] { singleValues };
            _minValue = 0f;
            _maxValue = 100f;
        }
    }

    /// <summary>设置多序列数据（每序列一条折线）</summary>
    public void SetData(float[][] series, float minValue, float maxValue)
    {
        _series = series;
        _minValue = minValue;
        _maxValue = maxValue;
        SetVerticesDirty();
    }

    /// <summary>设置单序列数据（便捷重载）</summary>
    public void SetData(float[] series, float minValue, float maxValue)
    {
        SetData(new[] { series }, minValue, maxValue);
    }

    protected override void OnPopulateMesh(VertexHelper vh)
    {
        vh.Clear();
        if (_series == null || _series.Length == 0) return;

        Rect rect = GetPixelAdjustedRect();
        float pad = 8f;
        Rect inner = new Rect(rect.x + pad, rect.y + pad, rect.width - pad * 2f, rect.height - pad * 2f);
        if (inner.width < 4f || inner.height < 4f) return;

        // 底部基线（0 值线）
        AddLineSegment(vh, new Vector2(inner.xMin, inner.yMin), new Vector2(inner.xMax, inner.yMin),
                       1.5f, new Color(1f, 1f, 1f, 0.25f));

        for (int s = 0; s < _series.Length; s++)
        {
            float[] values = _series[s];
            if (values == null || values.Length < 2) continue;

            Color color = GetSeriesColor(s);
            Color fill = new Color(color.r, color.g, color.b, 0.14f);

            // 数据点 → 局部坐标
            Vector2[] pts = new Vector2[values.Length];
            for (int i = 0; i < values.Length; i++)
            {
                float x = inner.xMin + inner.width * (float)i / (values.Length - 1);
                float t = Mathf.InverseLerp(_minValue, _maxValue, values[i]);
                float y = inner.yMin + inner.height * Mathf.Clamp01(t);
                pts[i] = new Vector2(x, y);
            }

            // 面积填充
            if (showFill)
            {
                for (int i = 0; i < pts.Length - 1; i++)
                {
                    int b = vh.currentVertCount;
                    vh.AddVert(new Vector3(pts[i].x, inner.yMin), fill, Vector2.zero);
                    vh.AddVert(new Vector3(pts[i].x, pts[i].y), fill, Vector2.zero);
                    vh.AddVert(new Vector3(pts[i + 1].x, pts[i + 1].y), fill, Vector2.zero);
                    vh.AddVert(new Vector3(pts[i + 1].x, inner.yMin), fill, Vector2.zero);
                    vh.AddTriangle(b, b + 1, b + 2);
                    vh.AddTriangle(b, b + 2, b + 3);
                }
            }

            // 折线
            for (int i = 0; i < pts.Length - 1; i++)
            {
                AddLineSegment(vh, pts[i], pts[i + 1], lineWidth, color);
            }

            // 数据点
            if (showPoints)
            {
                foreach (Vector2 p in pts)
                {
                    AddQuad(vh, new Vector2(p.x - pointSize, p.y - pointSize),
                            new Vector2(p.x + pointSize, p.y + pointSize), color);
                }
            }
        }
    }

    private Color GetSeriesColor(int index)
    {
        switch (index % 3)
        {
            case 0: return series1Color;
            case 1: return series2Color;
            default: return series3Color;
        }
    }

    private void AddLineSegment(VertexHelper vh, Vector2 a, Vector2 b, float width, Color color)
    {
        Vector2 dir = (b - a).normalized;
        Vector2 normal = new Vector2(-dir.y, dir.x) * (width * 0.5f);
        vh.AddVert(new Vector3(a.x + normal.x, a.y + normal.y), color, Vector2.zero);
        vh.AddVert(new Vector3(a.x - normal.x, a.y - normal.y), color, Vector2.zero);
        vh.AddVert(new Vector3(b.x - normal.x, b.y - normal.y), color, Vector2.zero);
        vh.AddVert(new Vector3(b.x + normal.x, b.y + normal.y), color, Vector2.zero);
        int idx = vh.currentVertCount - 4;
        vh.AddTriangle(idx, idx + 1, idx + 2);
        vh.AddTriangle(idx, idx + 2, idx + 3);
    }

    private void AddQuad(VertexHelper vh, Vector2 min, Vector2 max, Color color)
    {
        vh.AddVert(new Vector3(min.x, min.y), color, Vector2.zero);
        vh.AddVert(new Vector3(max.x, min.y), color, Vector2.zero);
        vh.AddVert(new Vector3(max.x, max.y), color, Vector2.zero);
        vh.AddVert(new Vector3(min.x, max.y), color, Vector2.zero);
        int idx = vh.currentVertCount - 4;
        vh.AddTriangle(idx, idx + 1, idx + 2);
        vh.AddTriangle(idx, idx + 2, idx + 3);
    }
}
