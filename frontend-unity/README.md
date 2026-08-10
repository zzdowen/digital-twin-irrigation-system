# 🎮 Unity 3D 前端

数字孪生灌溉系统的可视化层，使用 Unity 3D 构建虚拟温室场景。

## 模块规划

| 文件 | 功能 | 状态 |
|------|------|------|
| `IrrigationApiClient.cs` | API 客户端（已提供） | ✅ 完成 |
| `SceneManager3D.cs` | 3D 场景管理 | 📝 待开发 |
| `DataVisualization.cs` | 数据可视化（曲线图等） | 📝 待开发 |
| `UIController.cs` | UI 控制面板 | 📝 待开发 |

## 场景元素
- 透明温室（玻璃材质）
- 植物区域（3D 作物模型）
- 地下土壤层（分层可视化）
- 灌溉管路 + 水箱
- 水泵状态指示灯

## 数据接口
详见 `docs/Unity数据接口规范.md`，本地开发默认 `http://localhost:5000`，云端地址见内部部署文档。

## 开发指南
1. 创建 Unity 项目（推荐 Unity 2022 LTS 或更新）
2. 将 `Assets/Scripts/` 下的脚本放入项目
3. 创建空物体挂载 `IrrigationApiClient` 脚本
4. 在 Inspector 中配置服务器地址
5. 运行测试数据是否正常接收

## 待办
- [ ] 3D 场景搭建（温室、作物、土壤）
- [ ] 实时数据 UI 显示
- [ ] 预测曲线可视化
- [ ] 水泵控制交互
- [ ] 与后端联调
