/*
 * 数字孪生灌溉系统 - ESP32 边缘主控固件 v3.0.0
 * =================================================
 * 功能：
 *   1. 采集 3 路电容式土壤湿度（GPIO34/35/32，ADC1）
 *   2. 控制 3 路水泵（GPIO25/26/27，继电器低电平触发）
 *   3. 通过 MQTT 每 5 秒上报 3 路数据到服务器
 *   4. 订阅 MQTT 控制指令，远程启动/停止灌溉
 *   5. 本地自动灌溉兜底：湿度低于阈值自动开泵，到时自动关泵
 *
 * 依赖库（Arduino IDE 库管理器安装）：
 *   - PubSubClient by Nick O'Leary
 *
 * 硬件接线见《硬件物料清单与接线手册.md》
 */

#include <WiFi.h>
#include <PubSubClient.h>

// ===================== WiFi 配置 =====================
// TODO: 烧录前改为现场 WiFi（勿提交真实密码到公开仓库）
const char* WIFI_SSID = "你的WiFi名称";
const char* WIFI_PASSWORD = "你的WiFi密码";

// ===================== MQTT 配置 =====================
// TODO: 部署后替换为服务器 IP（真实 IP 见内部部署文档，勿提交）
const char* MQTT_SERVER = "你的服务器IP";
const int   MQTT_PORT = 1883;
const char* MQTT_CLIENT_ID = "esp32_irrigation_01";

// 主题（与《系统接口契约v3.md》一致）
const char* TOPIC_SENSORS = "farm/device01/sensors";   // 数据上报
const char* TOPIC_STATUS  = "farm/device01/status";    // 心跳
const char* TOPIC_CONTROL = "farm/device01/control";   // 控制指令

// ===================== 引脚定义 =====================
const int PUMP_PINS[3] = {25, 26, 27};   // 继电器 IN1/IN2/IN3
const int SOIL_PINS[3] = {34, 35, 32};   // 湿度传感器 AO（ADC1，输入专用）

// 继电器触发电平（低电平触发模块）
#define PUMP_ON   LOW    // 输出低电平 = 继电器吸合 = 水泵开
#define PUMP_OFF  HIGH   // 输出高电平 = 继电器断开 = 水泵关

// ===================== 土壤湿度标定 =====================
// 电容式传感器输出 0~3.3V 模拟电压，ESP32 读到 0~4095。
// 标定方法（务必实测，不同传感器/批次差异大）：
//   1. 传感器悬空（最干燥）：看串口打印的原始值 → 填入 DRY_ADC
//   2. 传感器插入水中（最湿）：看串口打印的原始值 → 填入 WET_ADC
// 说明：多数电容式传感器"越湿读数越大"；若你的传感器相反，交换两个值即可，
//       map() 会自动按方向正确映射，无需改代码逻辑。
const int DRY_ADC = 2500;   // TODO: 干燥时实测值
const int WET_ADC = 1200;   // TODO: 浸水时实测值

// ===================== 灌溉逻辑参数 =====================
const float AUTO_IRRIGATE_THRESHOLD = 30.0;                    // 湿度低于此值自动灌溉（%）
const unsigned long DEFAULT_IRRIGATE_MS = 10UL * 60UL * 1000UL; // 默认灌溉 10 分钟
const unsigned long SEND_INTERVAL_MS = 5000;                    // 上报间隔 5 秒
const unsigned long STATUS_INTERVAL_MS = 30000;                 // 心跳间隔 30 秒

// ===================== 运行状态 =====================
WiFiClient    wifiClient;
PubSubClient  mqtt(wifiClient);

bool         pumpState[3]       = {false, false, false};  // 各泵当前状态
unsigned long pumpStartMs[3]    = {0, 0, 0};              // 各泵启动时刻
unsigned long pumpDurationMs[3] = {0, 0, 0};              // 各泵目标时长（0=手动持续）

unsigned long lastSendMs   = 0;
unsigned long lastStatusMs = 0;
int rssi = 0;   // WiFi 信号强度

// ===================== 函数声明 =====================
void setupWiFi();
void reconnectMQTT();
void mqttCallback(char* topic, byte* payload, unsigned int length);
float readSoilMoisture(int index);
void setPump(int index, bool on, unsigned long durationMs = 0);
void checkAutoIrrigation();
void reportSensors();
void publishStatus();

// ===================== 初始化 =====================
void setup() {
  Serial.begin(115200);
  delay(200);

  // 水泵引脚：输出，初始关闭
  for (int i = 0; i < 3; i++) {
    pinMode(PUMP_PINS[i], OUTPUT);
    digitalWrite(PUMP_PINS[i], PUMP_OFF);
  }

  // 湿度传感器引脚：ADC1，0~3.3V 满量程
  for (int i = 0; i < 3; i++) {
    pinMode(SOIL_PINS[i], INPUT);
    analogSetPinAttenuation(SOIL_PINS[i], ADC_11db);
  }

  // WiFi + MQTT
  setupWiFi();
  mqtt.setServer(MQTT_SERVER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);

  Serial.println("\n[系统] ESP32 灌溉主控初始化完成（3路采集 + 3路控制）");
}

// ===================== 主循环 =====================
void loop() {
  if (!mqtt.connected()) {
    reconnectMQTT();
  }
  mqtt.loop();

  unsigned long now = millis();

  // 周期上报
  if (now - lastSendMs >= SEND_INTERVAL_MS) {
    lastSendMs = now;
    checkAutoIrrigation();   // 先决策，再采集上报（让上报的泵状态反映最新决策）
    reportSensors();
  }

  // 周期心跳
  if (now - lastStatusMs >= STATUS_INTERVAL_MS) {
    lastStatusMs = now;
    publishStatus();
  }

  // 检查各泵是否到时自动关闭
  for (int i = 0; i < 3; i++) {
    if (pumpState[i] && pumpDurationMs[i] > 0 && (now - pumpStartMs[i] >= pumpDurationMs[i])) {
      Serial.printf("[灌溉] 花盆%d 灌溉到时，自动停止\n", i + 1);
      setPump(i, false);
    }
  }
}

// ===================== WiFi 连接 =====================
void setupWiFi() {
  Serial.printf("连接 WiFi: %s ...", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 40) {  // 最多约 20 秒
    delay(500);
    Serial.print(".");
    tries++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    rssi = WiFi.RSSI();
    Serial.printf("\nWiFi 已连接! IP=%s RSSI=%d dBm\n", WiFi.localIP().toString().c_str(), rssi);
  } else {
    Serial.println("\nWiFi 连接失败，稍后重试（建议检查账号密码/信号）");
  }
}

// ===================== MQTT 重连 =====================
void reconnectMQTT() {
  while (!mqtt.connected()) {
    Serial.print("连接 MQTT 服务器...");
    if (mqtt.connect(MQTT_CLIENT_ID)) {
      Serial.println(" 已连接!");
      mqtt.subscribe(TOPIC_CONTROL);
      Serial.printf("已订阅控制主题: %s\n", TOPIC_CONTROL);
    } else {
      Serial.printf(" 失败(状态码%d)，5秒后重试\n", mqtt.state());
      delay(5000);
    }
  }
}

// ===================== MQTT 控制指令回调 =====================
// 指令格式（见契约）：{"channel":2,"action":"start","duration_minutes":5}
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  Serial.println("[MQTT] 收到控制指令: " + msg);

  // 解析 channel（默认 1；"all" = 全部）
  int channel = 1;
  int chPos = msg.indexOf("channel");
  if (chPos >= 0) {
    int colon = msg.indexOf(':', chPos);
    if (colon >= 0) {
      String val = msg.substring(colon + 1);
      val.trim();
      if (val.indexOf("all") >= 0) {
        channel = 0;  // 全部
      } else {
        int c = val.toInt();
        if (c >= 1 && c <= 3) channel = c;
      }
    }
  }

  // 解析 action
  bool isStart = msg.indexOf("start") >= 0;
  bool isStop  = msg.indexOf("stop") >= 0;

  // 解析 duration_minutes（仅 start 有效）
  int durMin = 0;
  int durPos = msg.indexOf("duration_minutes");
  if (durPos >= 0) {
    int colon = msg.indexOf(':', durPos);
    if (colon >= 0) {
      durMin = msg.substring(colon + 1).toInt();
      if (durMin < 0) durMin = 0;
    }
  }

  if (isStop) {
    if (channel == 0) { for (int i = 0; i < 3; i++) setPump(i, false); }
    else setPump(channel - 1, false);
  } else if (isStart) {
    unsigned long durMs = durMin > 0 ? (unsigned long)durMin * 60UL * 1000UL : DEFAULT_IRRIGATE_MS;
    if (channel == 0) { for (int i = 0; i < 3; i++) setPump(i, true, durMs); }
    else setPump(channel - 1, true, durMs);
  } else {
    Serial.println("[MQTT] 无法识别的 action（应为 start/stop）");
  }
}

// ===================== 土壤湿度采集（多次采样取平均） =====================
float readSoilMoisture(int index) {
  const int SAMPLES = 10;
  long sum = 0;
  for (int i = 0; i < SAMPLES; i++) {
    sum += analogRead(SOIL_PINS[index]);
    delay(5);
  }
  int adc = sum / SAMPLES;
  // 线性映射到 0~100%，map() 支持反向（DRY_ADC/WET_ADC 大小任意）
  int pct = constrain(map(adc, DRY_ADC, WET_ADC, 0, 100), 0, 100);
  return (float)pct;
}

// ===================== 水泵控制 =====================
void setPump(int index, bool on, unsigned long durationMs) {
  if (index < 0 || index > 2) return;
  pumpState[index] = on;
  digitalWrite(PUMP_PINS[index], on ? PUMP_ON : PUMP_OFF);
  if (on) {
    pumpStartMs[index] = millis();
    pumpDurationMs[index] = durationMs;
    Serial.printf("[水泵] 花盆%d 开启（时长 %lu 秒）\n", index + 1, durationMs / 1000UL);
  } else {
    pumpDurationMs[index] = 0;
    Serial.printf("[水泵] 花盆%d 关闭\n", index + 1);
  }
}

// ===================== 自动灌溉决策（本地兜底） =====================
void checkAutoIrrigation() {
  for (int i = 0; i < 3; i++) {
    float sm = readSoilMoisture(i);
    if (!pumpState[i] && sm < AUTO_IRRIGATE_THRESHOLD) {
      Serial.printf("[自动灌溉] 花盆%d 湿度 %.1f%% 低于阈值 %.0f%%，自动开泵\n",
                    i + 1, sm, AUTO_IRRIGATE_THRESHOLD);
      setPump(i, true, DEFAULT_IRRIGATE_MS);
    }
  }
}

// ===================== 数据上报 =====================
void reportSensors() {
  float sm[3];
  for (int i = 0; i < 3; i++) sm[i] = readSoilMoisture(i);

  // 手写 JSON（避免额外依赖 ArduinoJson）
  String payload = "{";
  payload += "\"soil_1\":" + String(sm[0], 1) + ",";
  payload += "\"soil_2\":" + String(sm[1], 1) + ",";
  payload += "\"soil_3\":" + String(sm[2], 1) + ",";
  payload += "\"pump_1\":" + String(pumpState[0] ? "true" : "false") + ",";
  payload += "\"pump_2\":" + String(pumpState[1] ? "true" : "false") + ",";
  payload += "\"pump_3\":" + String(pumpState[2] ? "true" : "false");
  payload += "}";

  if (mqtt.connected()) {
    mqtt.publish(TOPIC_SENSORS, payload.c_str());
    Serial.printf("[上报] %s\n", payload.c_str());
  } else {
    Serial.println("[上报] MQTT 未连接，跳过本次上报");
  }
}

// ===================== 心跳上报 =====================
void publishStatus() {
  rssi = WiFi.RSSI();
  String payload = "{\"online\":true,\"rssi\":" + String(rssi) + "}";
  if (mqtt.connected()) {
    mqtt.publish(TOPIC_STATUS, payload.c_str());
    Serial.printf("[心跳] %s\n", payload.c_str());
  }
}
