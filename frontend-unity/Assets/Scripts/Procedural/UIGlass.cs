using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// 玻璃风 UI 工具库 v4.0.0：运行时生成圆角切片 Sprite，提供半透明玻璃面板、
/// 圆角玻璃按钮、描边、文本与进度条填充等代码化 UI 构建工具。
/// 无任何贴图/字体资源依赖（字体由调用方提供，颜色集中在下方调色板）。
/// </summary>
public static class UIGlass
{
    // ==================== 玻璃调色板 ====================
    public static readonly Color GlassBar      = new Color(0.055f, 0.10f, 0.17f, 0.52f); // 顶栏/底栏玻璃
    public static readonly Color GlassCard     = new Color(0.08f, 0.13f, 0.22f, 0.55f);  // 卡片玻璃
    public static readonly Color GlassDark     = new Color(0.02f, 0.04f, 0.08f, 0.62f);  // 图表底
    public static readonly Color GlassOutline  = new Color(0.36f, 0.58f, 0.92f, 0.38f);  // 玻璃描边
    public static readonly Color ButtonStart   = new Color(0.20f, 0.55f, 0.85f, 0.92f);  // 启动按钮
    public static readonly Color ButtonStop    = new Color(0.72f, 0.32f, 0.32f, 0.92f);  // 停止按钮
    public static readonly Color ButtonAuto    = new Color(0.24f, 0.52f, 0.36f, 0.92f);  // 自动模式
    public static readonly Color ButtonCollapse = new Color(0.12f, 0.20f, 0.30f, 0.85f); // 收起/展开

    // ==================== 圆角切片 Sprite（缓存） ====================
    private static Sprite _glassSprite;

    /// <summary>运行时生成的圆角切片 Sprite（角半径 16px，九宫格边框 20px）</summary>
    public static Sprite GlassSprite
    {
        get
        {
            if (_glassSprite != null) return _glassSprite;

            const int size = 64;
            const float radius = 16f;
            Texture2D tex = new Texture2D(size, size, TextureFormat.RGBA32, false);
            Color32[] px = new Color32[size * size];
            for (int y = 0; y < size; y++)
            {
                for (int x = 0; x < size; x++)
                {
                    bool inside = IsInRoundedRect(x, y, size, radius);
                    px[y * size + x] = inside
                        ? new Color32(255, 255, 255, 255)
                        : new Color32(0, 0, 0, 0);
                }
            }
            tex.SetPixels32(px);
            tex.Apply();
            tex.filterMode = FilterMode.Bilinear;
            tex.wrapMode = TextureWrapMode.Clamp;

            float border = 20f;
            _glassSprite = Sprite.Create(tex, new Rect(0f, 0f, size, size), new Vector2(0.5f, 0.5f),
                100f, 0, SpriteMeshType.FullRect,
                new Vector4(border, border, border, border));
            return _glassSprite;
        }
    }

    private static bool IsInRoundedRect(int x, int y, int size, float radius)
    {
        float cx = x + 0.5f;
        float cy = y + 0.5f;
        float dx = Mathf.Max(Mathf.Abs(cx - size * 0.5f) - (size * 0.5f - radius), 0f);
        float dy = Mathf.Max(Mathf.Abs(cy - size * 0.5f) - (size * 0.5f - radius), 0f);
        return (dx * dx + dy * dy) <= (radius * radius);
    }

    // ==================== 面板 ====================

    /// <summary>创建圆角玻璃面板（Image 九宫格切片 + 淡蓝描边），不拦截点击</summary>
    public static Image CreateGlass(Transform parent, Color color, bool outline = true)
    {
        GameObject go = new GameObject("Glass", typeof(Image));
        go.transform.SetParent(parent, false);
        RectTransform rt = go.GetComponent<RectTransform>();
        rt.anchorMin = Vector2.zero;
        rt.anchorMax = Vector2.one;
        rt.offsetMin = Vector2.zero;
        rt.offsetMax = Vector2.zero;

        Image img = go.GetComponent<Image>();
        img.sprite = GlassSprite;
        img.type = Image.Type.Sliced;
        img.color = color;
        img.raycastTarget = false;

        if (outline) AddOutline(img, GlassOutline, new Vector2(1f, 1f));
        return img;
    }

    /// <summary>给 UI 图形添加描边（半透明细边框）</summary>
    public static Outline AddOutline(Graphic g, Color color, Vector2 distance)
    {
        Outline o = g.gameObject.GetComponent<Outline>();
        if (o == null) o = g.gameObject.AddComponent<Outline>();
        o.effectColor = color;
        o.effectDistance = distance;
        return o;
    }

    // ==================== 文本 ====================

    public static Text CreateText(string name, Transform parent, string content, int fontSize, Color color,
                                  Vector2 anchorMin, Vector2 anchorMax, Vector2 offsetMin, Vector2 offsetMax,
                                  Font font, TextAnchor alignment = TextAnchor.MiddleLeft,
                                  FontStyle style = FontStyle.Normal, bool raycast = false)
    {
        GameObject go = new GameObject(name, typeof(Text));
        go.transform.SetParent(parent, false);
        RectTransform rt = go.GetComponent<RectTransform>();
        rt.anchorMin = anchorMin;
        rt.anchorMax = anchorMax;
        rt.offsetMin = offsetMin;
        rt.offsetMax = offsetMax;
        Text t = go.GetComponent<Text>();
        t.font = font;
        t.text = content;
        t.fontSize = fontSize;
        t.color = color;
        t.alignment = alignment;
        t.fontStyle = style;
        t.raycastTarget = raycast;
        return t;
    }

    // ==================== 按钮 ====================

    public static Button CreateButton(string name, Transform parent, string label, Color color,
                                      Vector2 anchorMin, Vector2 anchorMax, Vector2 offsetMin, Vector2 offsetMax,
                                      Font font, int fontSize, UnityEngine.Events.UnityAction onClick)
    {
        RectTransform rt = CreateRect(name, parent, anchorMin, anchorMax, offsetMin, offsetMax);

        Image bg = rt.gameObject.AddComponent<Image>();
        bg.sprite = GlassSprite;
        bg.type = Image.Type.Sliced;
        bg.color = color;
        bg.raycastTarget = true;

        AddOutline(bg, GlassOutline, new Vector2(1f, 1f));

        Button btn = rt.gameObject.AddComponent<Button>();
        ColorBlock cb = btn.colors;
        cb.normalColor = color;                                  // 关键：否则按钮会被涂成白色
        cb.highlightedColor = color * 1.15f;
        cb.pressedColor = color * 0.8f;
        cb.selectedColor = color;
        cb.disabledColor = new Color(color.r, color.g, color.b, 0.35f);
        cb.colorMultiplier = 1f;
        cb.fadeDuration = 0.1f;
        btn.colors = cb;
        btn.onClick.AddListener(onClick);

        CreateText("Label", rt, label, fontSize, Color.white,
                   Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero,
                   font, TextAnchor.MiddleCenter);
        return btn;
    }

    // ==================== 进度条填充 ====================

    public static Image CreateFill(Transform parent)
    {
        GameObject go = new GameObject("Fill", typeof(Image));
        go.transform.SetParent(parent, false);
        Image img = go.GetComponent<Image>();
        img.color = Color.white;
        img.raycastTarget = false;
        return img;
    }

    public static void SetBarRatio(Image fill, float ratio, Color color)
    {
        if (fill == null) return;
        RectTransform rt = fill.rectTransform;
        rt.anchorMin = Vector2.zero;
        rt.anchorMax = new Vector2(Mathf.Clamp01(ratio), 1f);
        rt.offsetMin = new Vector2(3f, 3f);
        rt.offsetMax = new Vector2(-3f, -3f);
        fill.color = color;
    }

    // ==================== Rect 工具 ====================

    public static RectTransform CreateRect(string name, Transform parent,
                                           Vector2 anchorMin, Vector2 anchorMax,
                                           Vector2 offsetMin, Vector2 offsetMax)
    {
        GameObject go = new GameObject(name, typeof(RectTransform));
        RectTransform rt = go.GetComponent<RectTransform>();
        rt.SetParent(parent, false);
        rt.anchorMin = anchorMin;
        rt.anchorMax = anchorMax;
        rt.offsetMin = offsetMin;
        rt.offsetMax = offsetMax;
        return rt;
    }
}
