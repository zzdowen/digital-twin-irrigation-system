// ============================================================
// Unity 3D - 数字孪生灌溉系统 API 客户端
// 成员C（Unity前端负责人）在此基础上开发
// 功能：调用后端 REST API，获取实时数据/历史数据/预测数据
// ============================================================
using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;

[System.Serializable]
public class RealtimeData
{
    public float soil_moisture;
    public float temperature;
    public float humidity;
    public bool pump_status;
    public bool auto_irrigation_enabled;
    public int irrigation_duration;
    public string last_update;
}

[System.Serializable]
public class PredictionData
{
    public float current_moisture;
    public List<float> predicted_moisture;
    public List<float> confidence;
    public List<string> timestamps;
    public bool risk_of_drought;
    public string message;
}

public class IrrigationApiClient : MonoBehaviour
{
    [Header("服务器配置")]
    [Tooltip("后端服务器地址，可切换本地/云端")]
    public string serverUrl = "http://localhost:5000"; // TODO: 部署后替换为云服务器地址

    // 实时数据缓存（供UI和3D场景读取）
    public RealtimeData CurrentRealtime { get; private set; }
    public PredictionData CurrentPrediction { get; private set; }

    // 数据更新事件（UI可订阅）
    public event Action OnRealtimeUpdated;
    public event Action OnPredictionUpdated;

    private float _refreshInterval = 5f;  // 数据刷新周期 5 秒
    private Coroutine _realtimeRoutine;

    void Start()
    {
        StartRealtimeLoop();
    }

    // ============ 实时数据轮询 ============
    public void StartRealtimeLoop()
    {
        if (_realtimeRoutine != null) StopCoroutine(_realtimeRoutine);
        _realtimeRoutine = StartCoroutine(RealtimeLoop());
    }

    IEnumerator RealtimeLoop()
    {
        while (true)
        {
            yield return FetchRealtimeData();
            yield return new WaitForSeconds(_refreshInterval);
        }
    }

    // ============ 获取实时数据 ============
    public IEnumerator FetchRealtimeData()
    {
        string url = serverUrl + "/api/realtime-data";
        using (UnityWebRequest request = UnityWebRequest.Get(url))
        {
            yield return request.SendWebRequest();

            if (request.result == UnityWebRequest.Result.Success)
            {
                CurrentRealtime = JsonUtility.FromJson<RealtimeData>(request.downloadHandler.text);
                OnRealtimeUpdated?.Invoke();
                Debug.Log($"[API] 实时数据: 土壤湿度={CurrentRealtime.soil_moisture:F1}% " +
                          $"温度={CurrentRealtime.temperature:F1}°C 水泵={CurrentRealtime.pump_status}");
            }
            else
            {
                Debug.LogError($"[API] 获取实时数据失败: {request.error}");
            }
        }
    }

    // ============ 获取预测数据 ============
    public IEnumerator FetchPrediction(int minutes = 30)
    {
        string url = $"{serverUrl}/api/prediction?minutes={minutes}";
        using (UnityWebRequest request = UnityWebRequest.Get(url))
        {
            yield return request.SendWebRequest();

            if (request.result == UnityWebRequest.Result.Success)
            {
                CurrentPrediction = JsonUtility.FromJson<PredictionData>(request.downloadHandler.text);
                OnPredictionUpdated?.Invoke();
                Debug.Log($"[API] 预测数据: 30分钟后湿度={CurrentPrediction.predicted_moisture[^1]:F1}% " +
                          $"缺水风险={CurrentPrediction.risk_of_drought}");
            }
            else
            {
                Debug.LogError($"[API] 获取预测数据失败: {request.error}");
            }
        }
    }

    // ============ 水泵控制 ============
    public IEnumerator ControlPump(bool start)
    {
        string url = serverUrl + "/api/control-pump";
        string json = "{\"action\":\"" + (start ? "start" : "stop") + "\"}";

        using (UnityWebRequest request = new UnityWebRequest(url, "POST"))
        {
            byte[] body = System.Text.Encoding.UTF8.GetBytes(json);
            request.uploadHandler = new UploadHandlerRaw(body);
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");

            yield return request.SendWebRequest();

            if (request.result == UnityWebRequest.Result.Success)
            {
                Debug.Log($"[API] 水泵控制成功: {request.downloadHandler.text}");
            }
            else
            {
                Debug.LogError($"[API] 水泵控制失败: {request.error}");
            }
        }
    }

    // ============ UI 按钮回调（拖拽绑定） ============
    public void OnPumpStartButton() => StartCoroutine(ControlPump(true));
    public void OnPumpStopButton() => StartCoroutine(ControlPump(false));
    public void OnRefreshPredictionButton() => StartCoroutine(FetchPrediction(30));
}
