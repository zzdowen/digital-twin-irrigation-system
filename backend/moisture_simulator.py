#!/usr/bin/env python3
"""Three-channel irrigation sensor simulator for the digital-twin demo."""

import json
import math
import os
import random
import signal
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "farm/device01")
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL", "5"))
AUTO_THRESHOLD = float(os.getenv("AUTO_THRESHOLD", "30"))
AUTO_TARGET = float(os.getenv("AUTO_TARGET", "62"))


class MoistureSimulator:
    def __init__(self) -> None:
        self.moisture = [46.0, 54.0, 62.0]
        self.pumps = [False, False, False]
        self.auto_enabled = True
        self.running = True
        self.started_at = time.monotonic()
        self.stop_at = [0.0, 0.0, 0.0]
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="moisture-simulator")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            raise RuntimeError(f"MQTT connection rejected: {reason_code}")
        client.subscribe(f"{BASE_TOPIC}/control", qos=1)
        print(f"connected to {MQTT_HOST}:{MQTT_PORT}; topic={BASE_TOPIC}", flush=True)

    def on_message(self, client, userdata, message) -> None:
        try:
            command = json.loads(message.payload.decode("utf-8"))
            action = str(command.get("action", "")).lower()
            raw_channel = command.get("channel", "all")
            targets = range(3) if raw_channel in (0, "0", "all", None) else [int(raw_channel) - 1]
            duration = max(0, int(command.get("duration_minutes", 0)))

            if action == "auto":
                self.auto_enabled = True
            elif action in ("start", "stop"):
                for index in targets:
                    if 0 <= index < 3:
                        self.pumps[index] = action == "start"
                        self.stop_at[index] = time.monotonic() + duration * 60 if action == "start" and duration else 0.0
                if action == "start":
                    self.auto_enabled = False
            print(f"control: {command}", flush=True)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"ignored invalid control payload: {exc}", flush=True)

    def update(self) -> None:
        now = time.monotonic()
        phase = now - self.started_at
        for index in range(3):
            if self.stop_at[index] and now >= self.stop_at[index]:
                self.pumps[index] = False
                self.stop_at[index] = 0.0

            if self.auto_enabled:
                if self.moisture[index] < AUTO_THRESHOLD:
                    self.pumps[index] = True
                elif self.moisture[index] >= AUTO_TARGET:
                    self.pumps[index] = False

            change = random.uniform(0.35, 0.65) if self.pumps[index] else -random.uniform(0.08, 0.20)
            wave = math.sin(phase / 25.0 + index * 1.7) * 0.025
            self.moisture[index] = min(80.0, max(10.0, self.moisture[index] + change + wave))

    def publish(self) -> None:
        payload = {
            "soil_1": round(self.moisture[0], 1),
            "soil_2": round(self.moisture[1], 1),
            "soil_3": round(self.moisture[2], 1),
            "pump_1": self.pumps[0],
            "pump_2": self.pumps[1],
            "pump_3": self.pumps[2],
            "simulated": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        info = self.client.publish(f"{BASE_TOPIC}/sensors", json.dumps(payload, separators=(",", ":")), qos=1)
        info.wait_for_publish(timeout=3)
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    def stop(self, *_args) -> None:
        self.running = False

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        try:
            while self.running:
                self.update()
                self.publish()
                time.sleep(PUBLISH_INTERVAL)
        finally:
            self.client.loop_stop()
            self.client.disconnect()


if __name__ == "__main__":
    MoistureSimulator().run()
