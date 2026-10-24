# 灌溉监控看板（dashboard）

给 `digital-twin-irrigation-system` 加的一层**只读监控 + 有限控制**的 Web 看板。
后端是 FastAPI，前端是单文件零依赖 ES5 页面，数据源是同一台机器上的
Flask 数字孪生层（`:5000`）与 MQTT（`:1883`）。

> 为什么单独一层？数字孪生层（Flask）只管「传感器在说什么」，
> 没有登录、没有角色、没有审计、手机上看不了。这一层补的就是这些，
> **不改变上游任何行为** —— 它只调上游 API、只听 MQTT，从不写上游数据库。

---

## 它解决什么

| 痛点 | 这一层的做法 |
| --- | --- |
| 上游 `/api/realtime-data` 谁都能看 | 全部端点要登录；未登录一律 401 |
| 没有角色概念 | 三级角色 `viewer` / `operator` / `admin`，写操作按角色拦 |
| 谁开了水泵查不到 | 所有登录、控制、导出、改密都进 `audit` 表，带 IP 与时间 |
| 手机上看不了、图表要自己画 | 单文件响应式页面，5 秒轮询，纯 CSS 画图 |
| 断网时数字还在变（假的） | 读数超过 `FARM_TIMER_FRESH_SEC` 未更新就**停表并写明原因** |
| 历史只有 24 小时 | 本地 `samples` 表 + 上游长历史按时间拼接 |

---

## 目录结构

```
dashboard/
├── backend/
│   ├── app/
│   │   ├── config.py       环境变量、常量、角色表、通道标签
│   │   ├── db.py           SQLite（WAL）+ 7 张表 + 线程本地连接
│   │   ├── auth.py         PBKDF2 密码、token（存哈希）、角色、审计
│   │   ├── upstream.py     调 Flask 上游，全部异常转 UpstreamError
│   │   ├── collector.py    后台线程，每分钟写入一条 samples
│   │   ├── alerts.py       规则式告警（缺水/过湿/长时间灌溉/上游断流）
│   │   ├── timer.py        累计灌溉时长的「只认可观测时间」计时器
│   │   └── main.py         全部路由 + 安全响应头中间件
│   └── requirements.txt
├── web/
│   ├── index.html          单文件前端（零外链、ES5、内联 CSS）
│   └── _check_frontend.js  前端自检（129 条断言，node 直接跑）
├── deploy/
│   ├── install.sh          一键安装（幂等，11 步）
│   ├── update.sh           本地 rsync 推送 + 远程 install + 健康检查
│   ├── deploy.ps1          Windows 版（tar + scp + ssh）
│   ├── farm.service        systemd 单元模板
│   ├── setup-https.sh      一条命令配好 HTTPS（IP 证书）
│   ├── nginx-dashboard-http.conf   80 端口模板
│   ├── nginx-dashboard-https.conf  443 端口模板
│   └── moisture_simulator_v2.py    湿度模拟器 v2（物理水平衡）
├── docs/
│   ├── DEPLOY.md           部署与排障（15 章）
│   ├── USER_GUIDE.md       使用说明（面向非技术用户）
│   ├── SECURITY.md         安全审计（实测版）
│   └── SHOWCASE_API_V2.md  对外展示用的 API 说明
└── tests/
    ├── verify.sh           11 条端到端验收
    ├── test_api.py         pytest（47 passed）
    └── make_sample.py      造测试 CSV
```

---

## 快速开始

### 1. 装（在服务器上，root）

```bash
# 代码传到 /opt/farm 之后
cd /opt/farm
bash deploy/install.sh
```

`install.sh` 会做 11 件事：建用户与目录 → 建 venv → 装依赖 → 写 `.env` →
装 systemd 单元 → 装湿度模拟器 v2 → 给 `farm` 用户授权读上游历史库 →
启服务 → 健康检查。**幂等，可重复跑。**

可选参数：

| 参数 | 作用 |
| --- | --- |
| `--admin-password 'xxx'` | 指定初始管理员密码（不指定则随机生成并打印） |
| `--port 8000` | 监听端口 |
| `--no-apt` | 跳过 apt（依赖已装好时更快） |
| `--no-simulator` | 不替换湿度模拟器 |
| `--skip-health` | 装完不做健康检查 |

### 2. 配 HTTPS（可选但强烈建议）

```bash
bash deploy/setup-https.sh --staging   # 先用测试环境试签
bash deploy/setup-https.sh             # 正式签发 + 配好 443 + 装续期钩子
```

**不需要域名、不需要 ICP 备案**，用 Let's Encrypt 的 IP 证书。
代价是**有效期只有 160 小时（约 6.7 天）**，必须靠
`snap.certbot.renew.timer` + `renew_hook` 自动续期。
**关掉 80 端口 = 续期失败 = 6.7 天后全站报错。**

细节与人踩过的坑全在 [`docs/DEPLOY.md`](docs/DEPLOY.md) 第 1 节。

### 3. 验

```bash
bash tests/verify.sh --url http://127.0.0.1:8000 \
  --user admin --pass '你设的密码'
```

11 条全 PASS 才 `exit 0`。首次装完管理员是**强制改密**状态，
此时第 6~11 条会 403 —— 那是契约行为，不是故障。

---

## 角色与权限

| 能力 | viewer | operator | admin |
| --- | :---: | :---: | :---: |
| 看实时读数、历史、事件、告警 | ✅ | ✅ | ✅ |
| 导出 CSV | ✅ | ✅ | ✅ |
| 开/关水泵 | ❌ | ✅ | ✅ |
| 看审计日志 | ❌ | ❌ | ✅ |
| 管用户（建/停用/改角色/改密） | ❌ | ❌ | ✅ |

**保底规则**：不允许停用最后一个 admin，也不允许把最后一个 admin 降级。

---

## 数据来源与「不美化」原则

| 字段 | 从哪来 | 如果上游挂了 |
| --- | --- | --- |
| 三通道含水量、泵状态 | MQTT `.../sensors` → 上游 `/api/realtime-data` | 显示「上游不可达」，**不编造数字** |
| 累计灌溉时长 | 上游 `irrigation-stats.total_duration_sec` + 本地计时器 | 只显示本地已证实部分，并写明原因 |
| 历史曲线 | 本地 `samples` 表，跨度不够时拼上游 `historical-data` | 只剩本地部分，`source` 字段会说明 |
| 灌溉事件 | 上游 `irrigation_history.db`（**只读**） | 空数组 + `note` |
| 预测 | 上游 `/api/prediction` | 空 + `note` |
| 天气 | 上游 `/api/weather` | 空 + `note` |

**绝不外推**是这个模块的核心约束。看板上的每个数字都必须能对应一次真实观测，
否则就显示「不知道」并说明为什么。

---

## 安全措施（已落地）

- 密码：**PBKDF2-HMAC-SHA256 / 260000 轮**，每账号独立随机 salt，库里无明文
- 会话 token：**库里只存 SHA-256 哈希**，库泄漏也不能直接冒用
- 登录爆破：同一用户名连错 5 次锁 10 分钟
- SQL：全站参数化查询
- 路径穿越：上传文件名经 `safe_upload_path` 白名单校验
- 安全响应头：`nosniff` / `X-Frame-Options: DENY` / `Referrer-Policy` / CSP / `no-store`
- 审计：登录、控制、导出、改密全部落库带 IP
- 系统层：`farm` 用户无 shell、`ProtectSystem=full`、目录 750 / 文件 640

完整实测过程与剩余风险见 [`docs/SECURITY.md`](docs/SECURITY.md)。

---

## 环境变量

写进 `/opt/farm/.env`，由 systemd 的 `EnvironmentFile` 加载。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `FARM_PORT` | `8000` | 监听端口 |
| `FARM_DATA` | `/opt/farm/data` | 数据库与导出文件目录 |
| `FARM_UPSTREAM` | `http://127.0.0.1:5000` | Flask 数字孪生层地址 |
| `FARM_IRRDB` | `/home/ubuntu/irrigation_history.db` | 上游历史库（**只读**） |
| `FARM_TOKEN_TTL` | `43200` | token 有效期（秒），默认 12 小时 |
| `FARM_SAMPLE_SEC` | `60` | 本地采样间隔 |
| `FARM_STALE_SEC` | `600` | 读数超过此秒数算「过期」 |
| `FARM_TIMER_FRESH_SEC` | `10` | 计时器的「读数新鲜」阈值，超过即停表 |
| `FARM_TIMER_GAP_SEC` | `120` | 单次补记上限，超过的区间不补 |
| `FARM_TIMER_MAX_RUN_SEC` | `14400` | 单次运行计时上限（4 小时） |
| `FARM_ADMIN_USER` / `FARM_ADMIN_PASSWORD` | `admin` / 随机 | 初始管理员 |
| `FARM_SECRET` | 随机 | 内部签名用 |

---

## 测试

```bash
# 后端接口（需要 fastapi/httpx/pytest，建议在服务器 venv 里跑）
cd backend && PYTHONPATH=. python -m pytest ../tests/test_api.py -q

# 端到端验收（真起服务、真登录、真出图）
bash tests/verify.sh --url http://127.0.0.1:8000 --user admin --pass 'xxx'

# 前端自检（129 条断言，只需 node）
node web/_check_frontend.js web/index.html
```

---

## 已知限制

- **单进程**：`--workers 1` 写死。计时器、告警状态、轮询缓存都在进程内存里，
  多 worker 会各自为政。要横向扩展得先把这些状态挪到 Redis/DB。
- **SQLite**：够用（几千条/天），但不适合高并发写。
- **IP 证书 6.7 天**：见上文，续期链路是单点。
- **无 2FA**、**无密码找回**（忘了只能上服务器改库）。
- **告警只落库**，不发邮件/短信/Webhook。

---

## 上游项目

本体（Unity 场景、ESP32 固件、Flask 数字孪生层）见仓库根目录的
[`README.md`](../README.md) 与 [`docs/系统接口契约v3.md`](../docs/系统接口契约v3.md)。
本模块**不修改**上游任何文件，只新增目录。
