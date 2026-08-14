# 🎮 Unity 3D 前端（v4.0.0）

数字孪生灌溉系统的可视化层：**程序化一键场景**（动漫低模风格，3 块农田）+ 玻璃风数据面板 + 天气联动 + 灌溉控制。

## 模块清单（全部完成）

### 数据对接（`Assets/Scripts/`）

| 文件 | 功能 |
|------|------|
| `ApiConfig.cs` | 服务器地址与全部接口路径（改地址只改这一处） |
| `DataModels.cs` | 数据模型（3 路 + 天气 + 灌溉统计），字段与 JSON 一致 |
| `IrrigationApiClient.cs` | API 客户端单例（8 个接口，超时保护，防过期回调） |
| `RealtimeDataPoller.cs` | 每 5 秒轮询实时数据 + 事件回调 |
| `IrrigationUIController.cs` | 3 路 UI 控制器（手工搭建 UI 时用） |
| `SceneVisualController.cs` | 场景视觉驱动（手工建模场景时用） |

### 程序化场景（`Assets/Scripts/Procedural/` + `Assets/Shaders/`）

| 文件 | 功能 |
|------|------|
| `ToonLit.shader` | 卡通着色器（阶梯光照 + 描边） |
| `ProceduralStyle.cs` | 动漫调色板 + 材质/部件工厂 |
| `ProceduralGround/Farmland/Plants/Sun/Clouds/Birds/Equipment.cs` | 程序化模型：地面/3 块农田（田埂+垄+成排作物）/太阳/云/飞鸟/水泵水箱管道喷头传感器 |
| `RainEffect.cs` | 程序化雨效 |
| `WeatherController.cs` | 天气联动（天空/光照/云/雨/鸟 随 API 天气变化） |
| `SceneDataBinder.cs` | 数据→视觉绑定（土壤变色/LED/喷灌水流/传感器灯） |
| `OrbitCamera.cs` | 观察相机（左键旋转/右键平移/滚轮缩放） |
| `UILineChart.cs` | 轻量折线图组件（多序列） |
| `UIGlass.cs` | 玻璃风 UI 工具（运行时生成圆角切片/半透明面板/按钮） |
| `DataPanelUI.cs` | 程序化数据面板（玻璃风·紧凑版：3 张农田卡片/预测曲线/灌溉统计/天气/控制按钮，可收起） |
| `SceneBuilder.cs` | 一键场景生成器（含编辑器菜单入口） |

## 快速开始

1. 改 `Assets/Scripts/ApiConfig.cs` 的 `ServerUrl`（默认 `http://localhost:5000`，真实地址见内部部署文档）
2. 空场景挂 `SceneBuilder` 点 Play，或菜单栏 **「数字孪生灌溉 → 一键生成完整场景」**
3. 等待 2~5 秒数据接入，左键旋转/滚轮缩放查看，面板按钮控制灌溉

详见 `程序化场景使用说明.md`；建模需求（若改手工模型）见 `Unity场景设计文档.md`。
