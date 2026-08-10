#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数字孪生灌溉系统 - 模拟数据服务器
为Unity开发提供测试用的模拟数据接口
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import time
import threading
import random
import json
from datetime import datetime, timedelta
import math

app = Flask(__name__)
CORS(app)  # 允许跨域请求，方便Unity调用

# 模拟数据和状态
class DataSimulator:
    def __init__(self):
        # 基础环境参数
        self.base_temperature = 22.0  # 基础温度
        self.base_humidity = 65.0      # 基础湿度
        self.base_soil_moisture = 50.0  # 基础土壤湿度
        
        # 动态参数
        self.soil_moisture = 45.2      # 当前土壤湿度
        self.temperature = 23.5        # 当前温度
        self.humidity = 67.8            # 当前空气湿度
        self.pump_status = False        # 水泵状态
        self.irrigation_duration = 0    # 灌溉持续时间
        
        # 模拟时间相关
        self.time_factor = 1.0          # 时间流逝速度（1.0 = 实时）
        self.last_update = datetime.now()
        
        # 历史数据存储（最多保存48小时）
        self.historical_data = []
        self.max_history_points = 576   # 48小时 * 每5分钟一个点
        
        # 启动后台数据更新线程
        self.running = True
        self.update_thread = threading.Thread(target=self._update_loop)
        self.update_thread.daemon = True
        self.update_thread.start()
    
    def _update_loop(self):
        """后台循环更新模拟数据"""
        while self.running:
            # 更新环境数据
            self._update_environment()
            
            # 保存历史数据点的逻辑
            # 保存历史数据点
            if len(self.historical_data) == 0 or \
                (datetime.now() - datetime.fromisoformat(self.historical_data[-1]["timestamp"])).seconds >= 300:
                data_point = {
                    "timestamp": datetime.now().isoformat(),
                    "soil_moisture": self.soil_moisture,
                    "temperature": self.temperature,
                    "humidity": self.humidity,
                    "pump_status": self.pump_status
                }
                
                # 控制历史数据长度，防止数据过载
                # 控制历史数据长度
                self.historical_data.append(data_point)
                if len(self.historical_data) > self.max_history_points:
                    self.historical_data = self.historical_data[-self.max_history_points:]
            
            time.sleep(5)  # 每5秒更新一次
    
    def _update_environment(self):
        """更新模拟的环境数据"""
        current_time = datetime.now()
        hour = current_time.hour + current_time.minute / 60.0
        
        # 温度变化模拟（白天高，夜晚低）
        temp_variation = 8 * math.sin((hour - 6) * math.pi / 12)
        self.temperature = self.base_temperature + temp_variation + random.uniform(-1, 1)
        
        # 空气湿度变化模拟（与温度负相关，加上随机变化）
        humidity_variation = -15 * math.sin((hour - 6) * math.pi / 12)
        self.humidity = self.base_humidity + humidity_variation + random.uniform(-5, 5)
        self.humidity = max(20, min(90, self.humidity))  # 限制在合理范围内
        
        # 土壤湿度变化模拟
        if self.pump_status:
            # 灌溉时湿度快速上升
            self.soil_moisture += random.uniform(2, 4)
            self.irrigation_duration += 5
            # 灌溉10分钟后自动停止
            if self.irrigation_duration >= 600:
                self.pump_status = False
                self.irrigation_duration = 0
        else:
            # 自然蒸发，湿度缓慢下降
            evaporation_rate = 0.1 + 0.05 * (self.temperature - 20) / 10
            if self.humidity < 50:  # 空气干燥时蒸发更快
                evaporation_rate *= 1.5
            self.soil_moisture -= evaporation_rate + random.uniform(-0.1, 0.3)
        
        # 限制土壤湿度范围
        self.soil_moisture = max(10, min(80, self.soil_moisture))
        
        # 智能灌溉触发逻辑（土壤湿度低于30%时自动触发）
        if self.soil_moisture < 30 and not self.pump_status:
            self.trigger_irrigation()
        
        self.last_update = current_time
    
    def trigger_irrigation(self):
        """
        触发灌溉系统。通过设置flag和重置duration，启动自动灌溉逻辑。
        系统会定期检查灌溉状态，并在满足条件时开始灌溉。
        """
        self.pump_status = True
        self.irrigation_duration = 0
    
    def get_realtime_data(self):
        """获取当前实时数据"""
        return {
            "soil_moisture": round(self.soil_moisture, 1),
            "temperature": round(self.temperature, 1),
            "humidity": round(self.humidity, 1),
            "pump_status": self.pump_status,
            "irrigation_duration": self.irrigation_duration,
            "last_update": datetime.now().isoformat(),
            "auto_irrigation_enabled": True
        }
    
    def get_historical_data(self, hours=24):
        """获取历史数据"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        filtered_data = [
            point for point in self.historical_data
            if datetime.fromisoformat(point["timestamp"]) >= cutoff_time
        ]
        return {
            "data_points": filtered_data,
            "total_points": len(filtered_data),
            "query_range_hours": hours
        }
    
    def get_prediction_data(self, minutes=30):
        """获取预测数据（基于当前趋势和物理模型）"""
        predictions = []
        current_moisture = self.soil_moisture
        current_temp = self.temperature
        current_humidity = self.humidity
        
        for i in range(0, minutes + 1, 5):  # 每5分钟预测一个点
            future_time = datetime.now() + timedelta(minutes=i)
            
            # 简单的预测模型
            if self.pump_status and i < 120:  # 如果正在灌溉，预测前2小时湿度会上升
                moisture_change = 2.0 * (1 - i / 120)  # 递减效果
            else:
                # 自然蒸发预测
                evap_rate = 0.1 + 0.05 * (current_temp - 20) / 10
                if current_humidity < 50:
                    evap_rate *= 1.5
                moisture_change = -evap_rate * (i / 5)  # 每5分钟一个点的累积效果
            
            predicted_moisture = current_moisture + moisture_change * (i / 60)
            predicted_moisture = max(10, min(80, predicted_moisture))  # 限制范围
            
            predictions.append({
                "future_time": (datetime.now() + timedelta(minutes=i)).isoformat(),
                "minutes_ahead": i,
                "predicted_soil_moisture": round(predicted_moisture, 1),
                "confidence": max(0.7, 1.0 - (i / minutes) * 0.3)  # 随时间递减的置信度
            })
        
        return {
            "predictions": predictions,
            "current_conditions": self.get_realtime_data(),
            "prediction_horizon_minutes": minutes,
            "algorithm_info": "基于物理规律的土壤湿度变化预测模型"
        }
    
    def control_pump(self, action, duration_minutes=None):
        """控制水泵"""
        if action == "start":
            self.pump_status = True
            self.irrigation_duration = 0
            if duration_minutes:
                self.irrigation_duration = duration_minutes * 60
            return {"status": "success", "message": "灌溉已启动", "pump_status": self.pump_status}
        elif action == "stop":
            self.pump_status = False
            self.irrigation_duration = 0
            return {"status": "success", "message": "灌溉已停止", "pump_status": self.pump_status}
        elif action == "auto":
            return {"status": "success", "message": "切换到自动模式", "auto_mode": True}
        else:
            return {"status": "error", "message": "无效的控制命令"}

# 全局数据模拟器实例
simulator = DataSimulator()

@app.route('/api/realtime-data', methods=['GET'])
def get_realtime_data():
    """实时数据接口"""
    try:
        data = simulator.get_realtime_data()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/historical-data', methods=['GET'])
def get_historical_data():
    """历史数据接口"""
    try:
        hours = request.args.get('hours', 24, type=int)
        data = simulator.get_historical_data(hours)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/prediction', methods=['GET'])
def get_prediction_data():
    """预测数据接口"""
    try:
        minutes = request.args.get('minutes', 30, type=int)
        minutes = max(5, min(180, minutes))  # 限制预测范围在5分钟到3小时
        data = simulator.get_prediction_data(minutes)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/control-pump', methods=['POST'])
def control_pump():
    """水泵控制接口"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "缺少请求参数"}), 400
        
        action = data.get('action')
        duration_minutes = data.get('duration_minutes')
        
        if not action:
            return jsonify({"error": "缺少action参数"}), 400
        
        result = simulator.control_pump(action, duration_minutes)
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/system-status', methods=['GET'])
def get_system_status():
    """系统状态接口"""
    try:
        status = {
            "system_running": True,
            "uptime_seconds": time.time(),
            "data_points_collected": len(simulator.historical_data),
            "simulation_active": simulator.running,
            "server_time": datetime.now().isoformat(),
            "api_version": "1.0.0"
        }
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', methods=['GET'])
def api_info():
    """API信息接口"""
    return jsonify({
        "name": "数字孪生灌溉系统 - 模拟数据API",
        "version": "1.0.0",
        "endpoints": {
            "GET /api/realtime-data": "获取实时传感器数据",
            "GET /api/historical-data?hours=24": "获取历史数据",
            "GET /api/prediction?minutes=30": "获取土壤湿度预测数据",
            "POST /api/control-pump": "控制水泵灌溉",
            "GET /api/system-status": "获取系统运行状态"
        },
        "data_format": "JSON",
        "test_mode": "模拟数据生成中"
    })

if __name__ == '__main__':
    print("🌱 数字孪生灌溉系统模拟数据服务器启动中...")
    print("📡 API服务地址: http://localhost:5000")
    print("📖 API文档: http://localhost:5000/")
    print("⚡ 实时数据接口: http://localhost:5000/api/realtime-data")
    print("📊 历史数据接口: http://localhost:5000/api/historical-data?hours=24")
    print("🔮 预测数据接口: http://localhost:5000/api/prediction?minutes=30")
    print("🎮 水泵控制接口: POST http://localhost:5000/api/control-pump")
    print("\n" + "="*60)
    print("🚀 服务器已启动，Unity可以通过以上接口进行联调测试！")
    print("="*60)
    
    # 启动Flask服务器
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)