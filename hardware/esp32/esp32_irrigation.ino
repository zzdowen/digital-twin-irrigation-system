# ESP32 数字孪生灌溉系统 - Arduino 固件模板
# 负责：传感器数据采集 + MQTT 通信 + 水泵控制
# 成员A（硬件与通信负责人）在此基础上开发

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// ========== WiFi 配置 ==========
const char* WIFI_SSID = "你的WiFi名称";
const char* WIFI_PASSWORD = "你的WiFi密码";

// ========== MQTT 配置 ==========
const char* MQTT_SERVER = "你的服务器IP";  // TODO: 部署后替换为云服务器IP（勿提交真实IP）
const int MQTT_PORT = 1883;
const char* MQTT_TOPIC_SENSOR = "farm/device01/sensor";   // 传感器数据上报
const char* MQTT_TOPIC_CONTROL = "farm/device01/control"; // 控制指令下发

// ========== 引脚定义 ==========
#define DHTPIN 4          // DHT11/DHT22 数据引脚
#define DHTTYPE DHT22     // 传感器型号
#define SOIL_PIN 34       // 土壤湿度传感器 (ADC)
#define RELAY_PIN 25      // 继电器控制引脚 (水泵)

DHT dht(DHTPIN, DHTTYPE);
WiFiClient espClient;
PubSubClient mqttClient(espClient);

// ========== 定时器 ==========
unsigned long lastSendTime = 0;
const unsigned long SEND_INTERVAL = 5000;  // 数据采集周期 ≤ 5秒

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);
  dht.begin();

  connectWiFi();
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setCallback(callback);
}

void loop() {
  if (!mqttClient.connected()) {
    reconnectMQTT();
  }
  mqttClient.loop();

  // 每 5 秒采集一次数据
  if (millis() - lastSendTime >= SEND_INTERVAL) {
    sendSensorData();
    lastSendTime = millis();
  }
}

// ========== WiFi 连接 ==========
void connectWiFi() {
  Serial.print("连接WiFi中...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi已连接! IP: " + WiFi.localIP().toString());
}

// ========== MQTT 回调 ==========
void callback(char* topic, byte* payload, unsigned int length) {
  String message;
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.printf("收到控制指令 [%s]: %s\n", topic, message.c_str());

  // 解析控制指令
  if (message.indexOf("\"action\":\"start\"") >= 0) {
    digitalWrite(RELAY_PIN, HIGH);  // 启动水泵
    Serial.println(">>> 水泵启动");
  } else if (message.indexOf("\"action\":\"stop\"") >= 0) {
    digitalWrite(RELAY_PIN, LOW);   // 停止水泵
    Serial.println(">>> 水泵停止");
  }
}

// ========== MQTT 重连 ==========
void reconnectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("连接MQTT服务器...");
    if (mqttClient.connect("esp32_device01")) {
      Serial.println("已连接!");
      mqttClient.subscribe(MQTT_TOPIC_CONTROL);
    } else {
      Serial.printf("失败, 状态码=%d, 5秒后重试\n", mqttClient.state());
      delay(5000);
    }
  }
}

// ========== 传感器数据采集与上报 ==========
void sendSensorData() {
  float temperature = dht.readTemperature();
  float humidity = dht.readHumidity();
  int soilRaw = analogRead(SOIL_PIN);
  // 土壤湿度: ADC值 → 百分比 (根据传感器校准)
  float soilMoisture = map(soilRaw, 4095, 1500, 0, 100);
  soilMoisture = constrain(soilMoisture, 0, 100);

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("DHT传感器读取失败!");
    return;
  }

  // 构造 JSON 数据
  String payload = String("{\"soil_moisture\":") + soilMoisture +
                   ",\"temperature\":" + temperature +
                   ",\"humidity\":" + humidity +
                   ",\"pump_status\":" + (digitalRead(RELAY_PIN) == HIGH ? "true" : "false") + "}";

  Serial.println("上报数据: " + payload);
  mqttClient.publish(MQTT_TOPIC_SENSOR, payload.c_str());
}
