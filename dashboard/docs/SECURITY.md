# FARM 安全说明（实测版）

> 本文件所有结论都来自在服务器上**实际运行**的探测结果，不是推测。
> 复现方式：把对应的探测脚本放到服务器 `/tmp/`，用
> `/opt/farm/venv/bin/python /tmp/x.py` 跑（脚本内容见各节的「实测验证」代码块）。

## 一、结论速览

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| 用户密码存储 | **安全** | PBKDF2-HMAC-SHA256，260000 轮，每账号独立 salt，库里无明文 |
| 会话 token 存储 | **已加固** | 原来是明文入库，现已改为存 SHA-256 哈希（见 §三.1） |
| 未登录访问 | **安全** | 9 个业务端点全部 401；`/docs`、`/openapi.json` 均已关闭 |
| SQL 注入 | **安全** | 4 类载荷全部被拒；全站参数化查询 |
| 目录穿越 | **安全** | 5 种编码绕过全部 404/401 |
| 登录爆破 | **有防护** | 5 次错密码锁定 10 分钟（实测第 6 次起拒绝） |
| 伪造 token | **安全** | 4 类伪造全部 401 |
| 响应安全头 | **已加固** | nosniff / DENY / CSP / no-store（见 §三.2） |
| 文件权限 | **已加固** | 源码与数据库不再所有人可读（见 §三.3） |
| 上游 5000 端口 | **已封** | iptables DROP，外网实测 `False`（见 §二.1） |
| MQTT 1883 端口 | **已封** | iptables DROP，外网实测 `False`（见 §二.2） |
| **传输加密** | **已启用 HTTPS** | TLSv1.3 + Let's Encrypt IP 证书，`http://` 全部 302 跳转（见 §二.3） |
| token 有效期 | **已缩短** | 7 天 → **12 小时**（`FARM_TOKEN_TTL=43200`） |
| SSH | **偏弱（用户接受）** | 仍允许 root 登录 + 密码登录；已有 314 条自动封禁规则挡爆破（见 §二.4） |

写在前面的实话：**登录、密码、权限、传输加密现在都过关了；唯一剩下的架
是 `:8000` 那个明文备用入口，和 SSH 的密码登录。**

## 二、真实风险（有问题的地方）

> **2026-10-24 更新**：原来这里记的三条高危 —— 上游 5000 零认证、MQTT 匿名、
> 明文 HTTP —— **全部已处理**。保留下来的原始记录是为了让你知道「为什么要这么配」，
> 不要让它们再退回去。

### 2.1 上游 `irrigation-server` 5000 端口：**已封** ✅

**曾经的问题**（从公网、不带任何凭据就能）：

```
GET  http://203.0.113.10:5000/api/realtime-data        -> 200
GET  http://203.0.113.10:5000/api/historical-data      -> 200
POST http://203.0.113.10:5000/api/control-pump
     {"action":"stop","channel":1}
     -> 200 {"status":"success","message":"停止指令已下发（MQTT）"}
     实测泵状态从 true 变 false
```

任何人只要知道这个 IP，就能开关你的灌溉水泵、下载全部历史数据。
网页看板的登录、角色、审计**在这条路径上完全不起作用**。

根因：这个 Flask 服务（`/home/ubuntu/irrigation_server.py`）本来设计成只给内网
用，没有做认证。看板通过 `127.0.0.1:5000` 访问它，根本不需要暴露在公网。

**当前处置（已落地，未改上游一行代码）**：

```bash
# 立即生效
iptables -I INPUT 1 -p tcp --dport 5000 ! -i lo -j DROP
```

外加 `farm-port-guard.service`（`Type=oneshot` + `RemainAfterExit=yes`，已
`enable`）在开机时幂等地补回这条规则，防止服务器重启后失效。

**实测结果**：外网 `Test-NetConnection 203.0.113.10 -Port 5000` → `False`；
`curl http://203.0.113.10:5000/api/realtime-data` → 超时（`000`）；
服务器本机 `curl http://127.0.0.1:5000/api/realtime-data` → `200`。看板功能不受影响。

### 2.2 Mosquitto 1883 端口：**已封** ✅

**曾经的问题**：配置 `/etc/mosquitto/conf.d/Dustin_car_project.conf` 里
`allow_anonymous true`，监听 `0.0.0.0:1883`。

```
匿名 CONNECT  -> 返回码 0（连接成功，无需用户名密码）
匿名订阅 farm/simulator01/sensors -> 收到实时数据
匿名发布 farm/simulator01/control {"action":"stop","channel":1} -> 泵真的停了
```

比 §2.1 更隐蔽——不需要访问网页，一个 MQTT 客户端就能控制设备。
而且 mosquitto 日志里已经看到外部扫描 IP：`（境外扫描源 A）`、`（境外扫描源 B）`、
`（境外扫描源 D）`（CENSYS）、`（境外扫描源 C）` 等，说明**已经被扫到了**。

**当前处置（已落地，未改 mosquitto 配置）**：

```bash
iptables -I INPUT 1 -p tcp --dport 1883 ! -i lo -j DROP
```

同样由 `farm-port-guard.service` 持久化。

**实测结果**：外网 1883 → `False`；服务器本机 `127.0.0.1:1883` 匿名订阅正常；
`ss -tnp | grep 1883` 只剩 `127.0.0.1` 的连接（模拟器与上游服务都走回环）。

> 如果以后要接真实硬件（远程设备需要通过公网连 MQTT），不能只靠这条规则，
> 必须给 mosquitto 配密码认证或 TLS 证书，见官方文档 `mosquitto_passwd`。

### 2.3 传输加密：**已解决**（2026-10-24 上线 HTTPS）

站点现在是 `https://203.0.113.10/chat`，TLSv1.3，证书由 Let's Encrypt 签发
（IP 地址证书，非域名）。

| 项 | 状态 |
| --- | --- |
| 登录密码传输 | **已加密**（TLS 1.3 / TLS_AES_256_GCM_SHA384） |
| 会话 token 传输 | **已加密** |
| 证书链校验 | 外网实测通过，浏览器不报错 |
| HTTP 自动跳转 | `http://` 全部 302 到 `https://` |
| HSTS | `max-age=31536000` |
| token 有效期 | **12 小时**（`FARM_TOKEN_TTL=43200`，已从 7 天压缩） |

**代价（必须知道）**：IP 证书有效期**只有 160 小时（≈6.7 天）**，这是
Let's Encrypt 对 IP 证书的硬规定。靠 `snap.certbot.renew.timer` 每天自动续期，
续期后由 `renew_hook` 自动 reload nginx。

**剩下唯一的明文入口**：`http://203.0.113.10:8000/chat` 仍然开着（用户要求
保留作排障备用）。这个入口直接打到 uvicorn，**绕过 nginx，不走 TLS**。
想彻底关掉只需一行：

```bash
sed -i 's/^FARM_HOST=.*/FARM_HOST=127.0.0.1/' /opt/farm/.env
systemctl restart farm.service
```

**续期失效的风险**：如果云防火墙的 80 端口被关掉，或者定时器被杀掉，证书
会在 6.7 天后过期，浏览器开始报「您的连接不是私密连接」。检查命令：

```bash
openssl x509 -in /etc/letsencrypt/live/203.0.113.10/fullchain.pem -noout -dates
certbot renew --dry-run
```

### 2.4 SSH：允许 root 直接登录 + 允许密码登录

`/etc/ssh/sshd_config` 与 `/etc/ssh/sshd_config.d/50-cloud-init.conf` 实测：

```
PermitRootLogin        yes
PasswordAuthentication yes
```

服务器上已经配好了 2 把 SSH 公钥（`/root/.ssh/authorized_keys` 2 行），
所以关掉密码登录**不会把自己锁在外面**——但这一步有风险，需要在能确认密钥可用的前提下操作。

**当前处置（用户决定暂不改）**：保留密码登录。已有 314 条自动封禁规则
（`YJ-FIREWALL-INPUT` 链）挡爆破，`lastb` 为空。

```bash
# 先另开一个窗口确认密钥能登录，再执行
sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' \
    /etc/ssh/sshd_config.d/50-cloud-init.conf
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload ssh
```

另外服务器上已有 314 条针对爆破 IP 的 22 端口封禁规则（`YJ-FIREWALL-INPUT` 链），
说明**已经在被持续扫描爆破**，只是目前还没成功。

## 三、已经修好的问题（本次实测发现并改掉）

### 3.1 token 原来是明文入库 → 已改为哈希存储

**原问题**：`tokens` 表里存的是 43 字符的原始 token。一旦 `farm.db` 被下载（备份泄漏、
误传、任何读文件漏洞），表里所有未过期 token 可以**直接拿去冒用**，不需要密码。

**修改**：`backend/app/auth.py` 新增 `_token_id()`，
`issue_token` / `user_by_token` / `revoke` 三处统一改为存/查 `sha256(token)`。

实测验证（方法：登录后对比库里 `tokens.token` 与真实 token 的 SHA-256）：

```
登录 token_len=43
库里存储值长度 = 64           <- SHA-256 十六进制
库里值 == sha256(真实token)？ 是 OK
用库里存储值当 token 请求 -> 401   <- 库泄漏也无法直接冒用
真实 token 仍能正常用 -> /api/me 200, /api/overview 200
登出后复用 -> 401（已失效）
```

### 3.2 响应缺少安全头 → 已补

新增 `security_headers` 中间件（`backend/app/main.py`），每个响应都带：

```
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: no-referrer
x-permitted-cross-domain-policies: none
content-security-policy: default-src 'self'; script-src 'self' 'unsafe-inline'; ...
cache-control: no-store
```

作用：防 MIME 类型嗅探、防被别的网站嵌套（点击劫持）、限制页面只能加载本站资源。

### 3.3 文件权限过宽 → 已收紧

实测发现 `farm.db` 是 `-rw-r--r--`（**所有人可读**，意味着服务器上任何账号都能下载整个用户库），
源码 `.py` 甚至是 `-rw-rw-rw-`（所有人可写）——那是严重的提权隐患。

已改为：

```
/opt/farm/backend/app/*.py   -> -rw-r-----  (640)
/opt/farm/data/farm.db       -> -rw-r-----  (640)
/opt/farm/data/farm.db-wal   -> -rw-r-----  (640)
/opt/farm/.env               -> -rw-r-----  (640)
/opt/farm/data               -> drwxr-x---  (750)
```

### 3.4 顺带修掉的显示 bug：历史曲线时间跨度不够

用户选「近 24 小时」时，原本因为本地采样表只有 31 条就不再回落上游，
导致图上只画了最近 31 分钟的数据，**看着像 24 小时其实是半小时**——属于会误导判断的显示问题。

已改为按「时间跨度是否够」判断，并把上游长历史与本地细粒度采样合并（`_merge_recent`）。

实测：`/api/history?hours=24&points=100` → `source=upstream+samples pts=[100,100,100]`，
7 天图也是 `[200,200,200]`。

## 四、密码到底是怎么保护的（技术细节）

登录时发生的事：

1. 用户输入密码 `xxx`
2. 取该账号的 salt（32 字符随机十六进制，每账号独立）
3. 计算 `PBKDF2-HMAC-SHA256(xxx, salt, 260000 轮)` → 64 字符十六进制
4. 用 `hmac.compare_digest` 与库里的值比对（**防时序攻击**的等时比较）

关键点：

- **库里存的不是密码**，是 260000 轮哈希后的结果。从哈希**反推不回**原始密码。
- 260000 轮是刻意设计的**慢哈希**：正常登录感觉不到，但攻击者暴力猜测时每猜一次都要付这个代价。
  按单核每秒几千次估算，猜一个 8 位复杂密码需要**几十年**量级。
- 每个账号独立 salt → 攻击者不能用彩虹表批量破解，也不能发现"两个用户密码相同"。
- 实测反证：用错误密码复算，结果不匹配；用正确密码复算，结果匹配。

**什么情况下密码会泄露**：

| 场景 | 会不会拿到密码 |
| --- | --- |
| 数据库文件被下载 | 拿不到明文，只能拿到慢哈希（要破解成本极高） |
| 网络被嗅探（现在走 HTTPS） | **不会**——TLSv1.3 加密，只有 `:8000` 备用入口仍明文 |
| 服务器被 root 提权 | 会（可以直接改代码记录密码） |
| 前端被 XSS | 不会自动泄露已有密码，但能偷当前会话 token |

## 五、现在还剩什么没解决

按优先级：

1. ~~**（最高）** 5000 与 1883 端口对公网开放~~ → **已封**（iptables DROP +
   `farm-port-guard.service` 持久化，外网实测两个端口都 `False`）
2. ~~**（高）** 明文 HTTP~~ → **已启用 HTTPS**（Let's Encrypt IP 证书，TLSv1.3）
   - 遗留：`:8000` 明文备用入口还开着（用户要求保留），一行命令可关
   - 新增运维负担：IP 证书只活 6.7 天，续期链断了就全站报错
3. **（中）** SSH root + 密码登录 —— 确认密钥可用后可关掉密码登录（用户暂不改）
4. **（低）** 没有 fail2ban —— 目前靠 iptables 手工封 IP，可考虑装 fail2ban 自动封
5. **（低）** 端口 6080（browser-vnc）、4096（opencode）也对公网开放 ——
   如果不用，建议一并封掉

## 六、一句话总结

**这套看板本身：密码是慢哈希、库里没明文、登录有限速、越权有拦截、注入穿透过不去、
传输已加密——这几项我逐条实测过，是过关的。**

**原来最危险的两个老服务（5000 / 1883）已经不经过公网了，iptables 挡住并做了
开机自动恢复。现在最需要盯的不是「会不会被攻破」，而是「6.7 天后证书会不会过期」——
去跑一次 `certbot renew --dry-run`，比什么都实在。**
