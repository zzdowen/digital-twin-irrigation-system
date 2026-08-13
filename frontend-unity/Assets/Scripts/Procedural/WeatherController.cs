using UnityEngine;
using System.Collections;

/// <summary>
/// 天气联动控制器 v3.0.0：
/// - 定时（60 秒）通过 IrrigationApiClient.GetWeather 拉取 OpenClaw 天气数据；
/// - 根据天气驱动天空色 / 环境光 / 太阳 / 云量 / 雨效 / 飞鸟；
/// - 无天气数据时保持晴天默认，界面显示"天气数据不可用"。
/// </summary>
public class WeatherController : MonoBehaviour
{
    [Header("拉取间隔（秒）")]
    [SerializeField] private float refreshInterval = 60f;

    [Header("被驱动对象")]
    [SerializeField] private Camera sceneCamera;
    [SerializeField] private ProceduralSun sun;
    [SerializeField] private ProceduralClouds clouds;
    [SerializeField] private RainEffect rain;
    [SerializeField] private ProceduralBirds birds;

    [Header("默认晴天环境")]
    [SerializeField] private Color ambientDay = new Color(0.52f, 0.58f, 0.66f, 1f);

    /// <summary>最新天气（供 UI 显示，可能为 null）</summary>
    public WeatherEntry CurrentWeather { get; private set; }

    private Coroutine _routine;

    /// <summary>由 SceneBuilder 注入被驱动对象</summary>
    public void Configure(Camera cam, ProceduralSun s, ProceduralClouds c, RainEffect r, ProceduralBirds b)
    {
        sceneCamera = cam;
        sun = s;
        clouds = c;
        rain = r;
        birds = b;
    }

    private void OnEnable()
    {
        _routine = StartCoroutine(PollLoop());
    }

    private void OnDisable()
    {
        if (_routine != null) StopCoroutine(_routine);
    }

    private IEnumerator PollLoop()
    {
        yield return new WaitForSeconds(2f);   // 启动稍候再拉（让 API 客户端就绪）
        while (true)
        {
            yield return FetchWeather();
            yield return new WaitForSeconds(refreshInterval);
        }
    }

    private IEnumerator FetchWeather()
    {
        bool done = false;
        WeatherStatus status = null;
        IrrigationApiClient.GetOrCreate().GetWeather(
            s => { status = s; done = true; },
            e => { Debug.LogWarning("[天气] 获取失败: " + e); done = true; });

        float wait = 0f;
        while (!done && wait < 15f)
        {
            wait += Time.unscaledDeltaTime;
            yield return null;
        }

        if (status != null && status.enabled && status.forecast != null && status.forecast.Length > 0)
        {
            // 取预报首条作为当前天气
            CurrentWeather = status.forecast[0];
            ApplyWeather(CurrentWeather);
        }
        else
        {
            CurrentWeather = null;
            ApplyDefaultSunny();
        }
    }

    /// <summary>把一条天气预报应用到场景</summary>
    public void ApplyWeather(WeatherEntry entry)
    {
        if (entry == null) { ApplyDefaultSunny(); return; }

        string weather = entry.weather ?? "";
        float rainProb = entry.rain_probability_percent;
        bool rainy = rainProb > 50f || weather.Contains("雨");
        bool cloudy = weather.Contains("云") || weather.Contains("阴");

        // 天空色
        Color sky = rainy ? ProceduralStyle.SkyRainy : (cloudy ? ProceduralStyle.SkyCloudy : ProceduralStyle.SkyDayBlue);
        SetSky(sky);

        // 环境光
        RenderSettings.ambientLight = rainy ? ambientDay * 0.7f : (cloudy ? ambientDay * 0.9f : ambientDay);

        // 太阳
        if (sun != null) sun.SetWeather(weather);

        // 云量（雨天/阴天拉满，多云一半）
        float cloudiness = rainy ? 1f : (cloudy ? 0.8f : 0.35f);
        if (clouds != null) clouds.SetCloudiness(cloudiness);

        // 雨
        if (rain != null) rain.SetRain(rainProb);

        // 飞鸟（雨天归巢）
        if (birds != null) birds.SetRaining(rainy);
    }

    private void ApplyDefaultSunny()
    {
        SetSky(ProceduralStyle.SkyDayBlue);
        RenderSettings.ambientLight = ambientDay;
        if (sun != null) sun.SetWeather("晴");
        if (clouds != null) clouds.SetCloudiness(0.35f);
        if (rain != null) rain.SetRain(0f);
        if (birds != null) birds.SetRaining(false);
    }

    private void SetSky(Color c)
    {
        if (sceneCamera != null) sceneCamera.backgroundColor = c;
        RenderSettings.fog = true;
        RenderSettings.fogColor = c;
        RenderSettings.fogMode = FogMode.Linear;
        RenderSettings.fogStartDistance = 35f;
        RenderSettings.fogEndDistance = 90f;
    }
}
