using UnityEngine;

/// <summary>
/// 数据→场景视觉绑定器：订阅实时数据，驱动程序化模型的动态表现。
/// - 土壤颜色随湿度（>60% 深棕 / 30-60% 棕黄 / <30% 浅黄）
/// - 水泵 LED（运行=蓝亮 / 停止=灰）
/// - 传感器状态灯（正常=绿 / 干燥<30%=红）
/// - 喷灌水流粒子（泵开=播放 / 泵停=停止）
/// </summary>
public class SceneDataBinder : MonoBehaviour
{
    private FarmlandUnit[] _farms;
    private PumpUnit[] _pumps;
    private SensorUnit[] _sensors;
    private ParticleSystem[] _waterFlows;
    private RealtimeDataPoller _poller;

    /// <summary>绑定模型句柄与数据源（由 SceneBuilder 调用）</summary>
    public void Bind(FarmlandUnit[] farms, PumpUnit[] pumps, SensorUnit[] sensors,
                     ParticleSystem[] waterFlows, RealtimeDataPoller poller)
    {
        _farms = farms;
        _pumps = pumps;
        _sensors = sensors;
        _waterFlows = waterFlows;
        _poller = poller;
        if (_poller != null) _poller.OnDataReceived += OnDataReceived;
    }

    private void OnDestroy()
    {
        if (_poller != null) _poller.OnDataReceived -= OnDataReceived;
    }

    private void OnDataReceived(RealtimeData data)
    {
        if (data.channels == null) return;

        for (int i = 0; i < data.channels.Length; i++)
        {
            ChannelData ch = data.channels[i];
            if (ch == null) continue;

            // 1. 土壤颜色
            if (_farms != null && i < _farms.Length && _farms[i] != null && _farms[i].soilRenderer != null)
            {
                _farms[i].soilRenderer.material.color = ProceduralStyle.SoilColorForMoisture(ch.soil_moisture);
            }

            // 2. 水泵 LED
            if (_pumps != null && i < _pumps.Length && _pumps[i] != null && _pumps[i].ledMaterial != null)
            {
                Color led = ch.pump_status ? ProceduralStyle.LedOn : ProceduralStyle.LedOff;
                Material m = _pumps[i].ledMaterial;
                m.color = led;
                if (m.HasProperty("_EmissionColor"))
                {
                    m.SetColor("_EmissionColor", ch.pump_status ? led * 2.2f : Color.black);
                }
            }

            // 3. 传感器状态灯
            if (_sensors != null && i < _sensors.Length && _sensors[i] != null && _sensors[i].ledMaterial != null)
            {
                Color sc = ch.soil_moisture < 30f ? ProceduralStyle.SensorRed : ProceduralStyle.SensorGreen;
                Material m = _sensors[i].ledMaterial;
                m.color = sc;
                if (m.HasProperty("_EmissionColor"))
                {
                    m.SetColor("_EmissionColor", sc * 1.6f);
                }
            }

            // 4. 喷灌水流
            if (_waterFlows != null && i < _waterFlows.Length && _waterFlows[i] != null)
            {
                if (ch.pump_status && !_waterFlows[i].isPlaying)
                {
                    _waterFlows[i].Play();
                }
                else if (!ch.pump_status && _waterFlows[i].isPlaying)
                {
                    _waterFlows[i].Stop();
                }
            }
        }
    }
}
