# 展示页公开 API v2（`/api/showcase*`）

> 实现文件：`backend/app/main.py`、`backend/app/config.py`
> 契约依据：`showcase/3d/CONTRACT_V2.md` §2
> 全部端点**公开只读**，无需 token；列入 `config.PUBLIC_PATHS`（精确匹配，无通配）。
> 单端口部署，经 nginx 转发，故对外 URL 前缀为 `https://<host>/`，下文写相对路径。

## 通用约定

| 项 | 说明 |
| --- | --- |
| 方法 | 全部 `GET` |
| 鉴权 | 不需要。无需 `Authorization`、无需 `?token=` |
| 状态码 | **恒为 200**（含上游不可达、参数非法）。前端据 `status` 字段区分 |
| `status` | `"ok"` / `"error"`，`error` 时页面显示「未连接」，不显示假数据 |
| 参数非法 | **不报 4xx**，一律夹到合法区间或回落默认值 |
| 缓存 | 响应头 `Cache-Control: no-store`（全局安全头中间件统一加） |
| 编码 | UTF-8 JSON，中文不转义 |

参数夹取区间（超出即取边界，非数字即取默认值）：

| 端点 | 参数 | 默认 | 区间 |
| --- | --- | --- | --- |
| `/api/showcase/history` | `hours` | 6 | 1 ~ 72 |
| `/api/showcase/history` | `points` | 72 | 12 ~ 500 |
| `/api/showcase/prediction` | `minutes` | 30 | 30 ~ 180 |
| `/api/showcase/alerts` | `limit` | 20 | 1 ~ 100 |

> `history` 返回的 `points` 是**实际**点数（`points` 只是上限；库里只有 120 条样本时不会硬造 500 个点）。

> ⚠️ **与 `CONTRACT_V2.md` §2.2 的数值差异（需 Lead 确认）**
> 契约正文写 `hours` 夹 1~720、`points` 夹 10~2000；本实现按 Lead 的任务指令取
> `hours` 1~72、`points` 12~500。二者不一致，以**本实现（任务指令）**为准；
> 若最终要按契约正文放宽，只需改 `main.py` 里
> `SHOWCASE_HISTORY_HOURS` / `SHOWCASE_HISTORY_POINTS` 两个常量即可，无需动 handler。

---

## 1. `GET /api/showcase`

3D 场景与展示页的主数据源。v1 字段**全部保留**，v2 只新增。

```jsonc
{
  "status": "ok",
  "server_time": "2026-10-24T21:20:36",
  "upstream_ok": true,
  "data_source": "simulator",
  "stale": false,
  "data_age_sec": 0,
  "uptime_seconds": 51234,
  "collector": {
    "samples": 812,
    "interval_sec": 60,
    "errors": 0,
    "last_ok_ts": 1792853536
  },
  "channels": [
    {
      "channel": 1,
      "soil_moisture": 44.2,
      "pump_status": true,
      "status": "normal",
      "irrigation_duration": 300,
      "label": "1 号地块",
      "soil_type": "沙壤土",
      "field_capacity": 48.0,
      "wilting_point": 20.0,
      "target": 51.0
    },
    {
      "channel": 2, "soil_moisture": 40.1, "pump_status": false, "status": "normal",
      "irrigation_duration": 0, "label": "2 号地块",
      "soil_type": "壤土", "field_capacity": 58.0,
      "wilting_point": 22.0, "target": 61.0
    },
    {
      "channel": 3, "soil_moisture": 47.0, "pump_status": false, "status": "normal",
      "irrigation_duration": 0, "label": "3 号地块",
      "soil_type": "黏壤土", "field_capacity": 66.0,
      "wilting_point": 24.0, "target": 69.0
    }
  ],
  "soil_moisture_avg": 43.8,
  "active_pumps": 1,
  "auto_irrigation_enabled": true,
  "weather": {
    "city": "上海",
    "enabled": true,
    "forecast": [
      { "date": "2026-10-25", "weather": "晴",
        "temperature_c": 22, "rain_probability_percent": 5 }
    ]
  },
  "stats": {
    "total_irrigations": 1233,
    "water_usage_liters": 249158.1,
    "water_saving_percent": 12.5,
    "last_event": "ch1 stop"
  }
}
```

**v2 新增字段说明**

| 字段 | 类型 | 含义 / 来源 |
| --- | --- | --- |
| `uptime_seconds` | int | 后端进程运行秒数，与 `/health` 同源同逻辑 |
| `collector.samples` | int | 采集线程成功采样次数 |
| `collector.interval_sec` | int | 采样间隔（`config.SAMPLE_SEC`，60） |
| `collector.errors` | int | 采集失败次数 |
| `collector.last_ok_ts` | int | 最近一次成功采样的 Unix 秒，`0` 表示启动后还没成功过 |
| `channels[].irrigation_duration` | int | 本次灌溉已运行秒数（0 表示没在浇） |
| `channels[].label` | string | `1 号地块` / `2 号地块` / `3 号地块` |
| `channels[].soil_type` | string | `沙壤土` / `壤土` / `黏壤土` |
| `channels[].field_capacity` | float | 田间持水量 % |
| `channels[].wilting_point` | float | 凋萎点 % |
| `channels[].target` | float | 灌溉补充目标 %（= 田间持水量 + 3.0） |

三块地土壤质地不同（这是三路湿度本来就不该相同的原因），参数表硬编码在
`main.CHANNEL_SOILS`，取值与模拟器 `moisture_simulator.py` 的 `SOILS` 一致：

| 通道 | 土壤 | `field_capacity` | `wilting_point` | `target` |
| --- | --- | --- | --- | --- |
| 1 | 沙壤土 | 48.0 | 20.0 | 51.0 |
| 2 | 壤土 | 58.0 | 22.0 | 61.0 |
| 3 | 黏壤土 | 66.0 | 24.0 | 69.0 |

**降级行为**：上游不可达时 `upstream_ok:false`、`channels[].soil_moisture:null`，
`collector` / `uptime_seconds` / `soil_type` 等**仍照常返回**（这些不依赖上游）。
`status` 可能是 `"error"`，但 HTTP 仍是 200。

---

## 2. `GET /api/showcase/history?hours=6&points=72`

历史回放。复用既有 `build_history(hours, points)`，数据源与 `/api/history` 完全一致
（本地 `samples` 表，跨度不够时回落上游历史库并合并）。

```jsonc
{
  "status": "ok",
  "hours": 6,
  "points": 72,
  "source": "samples",          // samples | upstream | upstream+samples
  "series": [
    {
      "channel": 1,
      "label": "1 号地块",
      "moisture": [[1792844400, 40.0], [1792844460, 41.0]],
      "pump":     [[1792844400, 0],    [1792844460, 1]]
    },
    { "channel": 2, "label": "2 号地块", "moisture": [], "pump": [] },
    { "channel": 3, "label": "3 号地块", "moisture": [], "pump": [] }
  ]
}
```

- `series` 恒为 3 项（通道 1/2/3 顺序固定），无数据的通道给空数组而不是省略。
- `moisture[i]` = `[unix_ts, 含水量%]`；`pump[i]` = `[unix_ts, 0|1]`。
- `source` 说明历史从哪来，前端可据此标注数据来源。
- 构建失败时返回 `status:"ok"` + 空 `series` + `note`（不 500，页面照常渲染）。

---

## 3. `GET /api/showcase/prediction?minutes=30`

未来预测。复用 `upstream.prediction()` + `upstream.normalize_prediction()`。

```jsonc
{
  "status": "ok",
  "horizon_minutes": 30,        // 回填中夹取后的实际请求值
  "algorithm_info": "LSTM v2",
  "predictions": [[1792848066, 41.0], [1792848126, 40.2], [1792848186, 39.5]],
  "accuracy_by_channel": { "1": 0.0, "2": 0.0232, "3": 0.0 }
}
```

- `predictions[i]` = `[unix_ts, 预测含水量%]`，按时间升序。
- `accuracy_by_channel` 已归一为 **0~1** 的误差率（上游给的百分数除以 100）。

**上游不可达**（HTTP 仍 200，这是展示页不崩的关键）：

```jsonc
{
  "status": "error",
  "horizon_minutes": 30,
  "algorithm_info": null,
  "predictions": [],
  "accuracy_by_channel": {},
  "message": "灌溉服务暂时不可达，请稍后重试"
}
```

**上游活着但格式不认识**：`status:"ok"` + `predictions:[]` + `note:"上游预测格式未识别"`
（这不是故障，是「暂无预测」，不应让前端报错）。

---

## 4. `GET /api/showcase/alerts?limit=20`

告警列表。字段**严格白名单**，只有下面 6 个。

```jsonc
{
  "status": "ok",
  "badge": 1,                   // 未恢复告警数（前端角标）
  "count": 2,                   // 本次返回条数
  "alerts": [
    {
      "rule": "dry",
      "channel": 1,
      "level": "warning",
      "message": "1 号地块含水量 28.4%，低于 30%，建议灌溉。",
      "ts": "2026-10-24T21:10:36",
      "resolved_ts": null
    },
    {
      "rule": "wet",
      "channel": 2,
      "level": "info",
      "message": "2 号地块含水量 96.1%，高于 95%。",
      "ts": "2026-10-24T21:05:36",
      "resolved_ts": "2026-10-24T21:15:36"
    }
  ]
}
```

- 排序：未恢复（`resolved_ts == null`）置顶，其次按 `ts` 倒序。
- `level`：`info` / `warning` / `critical`。
- `rule`：`dry` / `wet` / `long_irrigation` / `no_data` / `upstream_down`。
- `resolved_ts` 为 `null` 表示告警仍在持续。
- 读库失败时返回 `badge:0, count:0, alerts:[]`（不 500）。

---

## 为什么这几个字段公开是安全的

四个端点的输出**全部是环境传感量 + 领域规则**，不含任何主体身份信息。逐项说明：

### 1. 不含任何身份凭证或账号信息

- `channels[]` 只有传感器读数（含水量、泵开关、灌溉时长）与**地块静态属性**（土壤质地、田间持水量、凋萎点、补充目标）。这些是物理常量，公开无风险。
- `label` / `soil_type` 是地块名称与土类，属于厂区公开信息，不是用户名。
- 白名单端点（`alerts`）**逐字段重建**：`main.showcase_alert_row()` 只从库里取
  `rule/channel/level/message/ts/resolved_ts` 六个字段，再用常量
  `SHOWCASE_ALERT_FIELDS` 做最后一道过滤。库行里的 `id`（后端主键）也不外泄。
- 绝不返回：`username`、`pwd_hash`、`salt`、`token`、`role`、`ip`、`detail`、`ok`、审计记录。
  这些只存在于 `/api/users`、`/api/audit`，那两个端点**要求 admin token**，未列入 `PUBLIC_PATHS`。

### 2. 不能用来横向越权

公开端点全是 `GET`，**没有任何写能力**：不能开水泵、不能改密码、不能建账号。
`/api/control` 仍要求 `Bearer <token>` 且角色为 `operator`/`admin`，
`/app/` 看板逻辑与 `/api/overview` 的既有字段均未改动（只新增字段，没删没改语义）。
即使有人把全站公开端点扫一遍，也拿不到一张能写操作的 token。

### 3. 不泄露内部拓扑与异常细节

- `collector` 只给计数（`samples`/`errors`/`interval_sec`/`last_ok_ts`），
  **刻意不带** `collector.status()` 里的 `last_error` / `down_streak`：前者可能含上游
  URL 和异常栈，后者是运维内部指标。
- `prediction` 上游失败时只回固定的 `message`（`MSG_UPSTREAM`），
  **不回传异常原文**——异常字符串里可能带 `http://127.0.0.1:5000/...` 这类内网地址。
- 告警 `message` 是规则引擎生成的固定模板句（「X 号地块含水量 N%，低于 M%」），
  内容只由阈值与读数决定，不含操作者、IP 或请求上下文。

### 4. 数据本身不是秘密，但完整性仍有保障

这些数字（土壤湿度、泵状态、灌溉统计）本就挂在公开展示页上供人观看，属「可公开的
运行观测值」。风险不在「被看到」，而在「被篡改」——而公开端点只读，篡改无从谈起。
参数一律 clamp 且非法值不报错，避免公开端点被用来探测后端内部边界或触发 500。

### 5. 通配符是刻意避免的

`config.PUBLIC_PATHS` 里三个新路径**逐个精确列出**，没写 `/api/showcase/*`。
若用通配，日后任何人往 `/api/showcase/` 下加一个写接口（例如
`/api/showcase/irrigate`），都会**默认变成公开**——这是最容易被忽视的提权路径。
`main.is_public()` 仍是精确匹配集合，未改成前缀匹配。

---

## 自检与验证边界

本机（开发机）没有 fastapi，也没有运行中的上游服务，因此：

- ✅ 已验证：`ast.parse` 通过；`config.PUBLIC_PATHS` 内容与数量正确；
  `is_public()` 对新增/相邻路径（`/api/showcase/foo`、`/api/showcase/history/x`）
  判定为**非公开**；四个 handler 用 fastapi 替身 + 合成数据实际跑通，断言覆盖
  字段结构、clamp 边界、白名单、上游不可达时的降级、既有端点未被破坏。
- ⚠️ 未验证：真实 HTTP 层（路由注册、中间件放行、JSON 序列化）。需部署到服务器后
  按 `CONTRACT_V2.md` §6 重启并 `curl` 复验。
