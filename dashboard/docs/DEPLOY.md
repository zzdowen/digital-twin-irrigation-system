# FARM 灌溉监控看板 · 部署文档

> 目标读者：**完全没接触过这个项目**、但会用 ssh 的人。
> 照着从头做完，大约 20 分钟（其中 5 分钟是等 pip 装包）。
>
> 全程只有一件事需要你去**云厂商网页控制台**点鼠标：放行 `22` / `80` / `443`
> 三个端口（`8000` 可选，见第 1 节）。其余都在命令行里完成。
>
> **HTTPS 已经配好了**，用的是 Let's Encrypt 的 IP 证书，不用买域名、不用备案。
> 详见第 1 节。

---

## 0. 先搞清楚现状（很重要，读完再动手）

服务器是一台**已经在跑别的系统**的机器，不是空的：

| 已有的东西 | 说明 | 我们怎么对待 |
| --- | --- | --- |
| `irrigation-server.service` | Flask，监听 `0.0.0.0:5000`，是**上游**灌溉系统 | **只读它，绝不动它** |
| `moisture-simulator.service` | 土壤墒情模拟器（**已被本项目替换为 v2 物理模型**，见第 9 节） | 由 `install.sh` 部署，可 `--no-simulator` 跳过 |
| `mosquitto.service` | MQTT broker，1883 | 不碰 |
| browser-vnc | 远程桌面，6080 | 不碰 |
| `/home/ubuntu/irrigation_history.db` | 上游历史库（20,466 行，5 分钟一条） | **只读打开**，绝不写入 |

我们这次要**新装**的是一个看板 + 一个模拟器：

```
farm.service          →  /opt/farm  →  监听 0.0.0.0:8000
moisture-simulator    →  /home/ubuntu/moisture_simulator.py（v2 物理模型，替换旧版）
```

它做的事：读取上游的 HTTP 接口和历史库，汇总成一个带登录的中文看板给人看。
它**不会**反向控制上游，也**不会**修改上游的任何数据。

> ⛔ **铁律**：部署过程中如果发现某个命令要去 `stop` / `restart` / `disable`
> `irrigation-server.service`、`mosquitto.service`，那是错的，立刻停下来。
> 唯一例外是 `moisture-simulator`：用户明确要求按真实物理模型重写它，
> `install.sh` 会先备份成 `moisture_simulator.py.pre-v2` 再覆盖并重启；
> 不想要这个替换就加 `--no-simulator`。
> 除此之外，本项目所有脚本对这几个服务只做 `is-active` 只读检查。

### 服务器信息

| 项 | 值 |
| --- | --- |
| 云厂商 | 云服务器 |
| 系统 | Ubuntu 24.04 |
| 配置 | 4 核 4G |
| 公网 IP | `203.0.113.10` |
| 登录方式 | `root` + 密码/密钥 |
| 部署根 | `/opt/farm` |
| venv | `/opt/farm/venv` |
| 数据 | `/opt/farm/data/farm.db` |
| 服务 | `farm.service`（`User=farm`，`workers=1`） |
| 对外入口 | `https://203.0.113.10/chat`（nginx 443 → 127.0.0.1:8000，见第 1 节） |
| 证书 | Let's Encrypt **IP 证书**，有效期 160 小时（约 6.7 天），自动续期 |

---

## 1. HTTPS 与端口（现状已配好，这里讲怎么复现和怎么维护）

> **先说结论：不用买域名、不用 ICP 备案。** 已用 Let's Encrypt 的
> **IP 地址证书**给 `203.0.113.10` 签了证书，浏览器地址栏是绿色小锁。
> 依据见本节末尾「为什么 IP 也能签证书」。

### 1.1 现在的访问链路

```
浏览器 ──HTTPS:443──> nginx ──HTTP──> 127.0.0.1:8000 (farm.service)
          │
          └─ 80 端口：只放行 ACME 验证路径，其余全部 302 跳 HTTPS
```

| 入口 | 状态 | 说明 |
| --- | --- | --- |
| `https://203.0.113.10/chat` | ✅ **推荐** | 正式入口，全程加密 |
| `http://203.0.113.10/chat` | ↪ 302 | 自动跳转到 HTTPS |
| `http://203.0.113.10:8000/chat` | ⚠️ 仍可用 | 直连 uvicorn，**明文**，仅作排障备用 |

> 8000 端口**没有关**。原因：nginx 反代挂了或证书出问题时，还能用
> `:8000` 直接进去救火。代价是这个入口仍然明文传输密码和 token。
> **想彻底关掉**（推荐在稳定后做）：
> ```
> # 让 uvicorn 只监听回环，物理上断掉外网直连
> sed -i 's/^FARM_HOST=.*/FARM_HOST=127.0.0.1/' /opt/farm/.env
> systemctl restart farm.service
> ```
> 之后 `:8000` 从外网就彻底不可达，只有 nginx 能访问它。

### 1.2 云防火墙要放行哪些端口

| 端口 | 必须 | 用途 |
| --- | --- | --- |
| `22` | ✅ | ssh 登录（建议改成只允许你自己的 IP） |
| `80` | ✅ | **ACME 验证 + 自动跳 HTTPS**。关掉的话证书无法续期 |
| `443` | ✅ | 正式访问入口 |
| `8000` | 可选 | 排障备用，稳定后可关 |

操作：登录云厂商控制台 → 找到这台服务器 → 「**防火墙**」/「安全组」→
「添加规则」→ 应用类型 **自定义**、协议 **TCP**、端口填上表里的值、来源
**0.0.0.0/0**、策略 **允许**。保存立即生效，不用重启。

> 各家叫法不同：有的叫「防火墙」（Firewall），有的叫「安全组」（Security Group）。
> 位置都在「机器详情」页里，找不到就在控制台搜索这两个词。

> ⚠️ **`80` 断了 = 证书续不了 = 6.7 天后全站报错。** 这是 IP 证书方案最大的
> 单点，务必保留 80 放行。

### 1.3 从零复现（换机器或证书彻底废了时）

**一条命令搞定**（脚本把下面 7 步全做了，幂等可重复跑）：

```bash
bash deploy/setup-https.sh --staging      # ① 先用 staging 试签（不占正式额度）
bash deploy/setup-https.sh                # ② 确认没问题后签正式证书 + 配好 443
```

不带参数时会自动 `curl https://api.ipify.org` 探测公网 IP；探测不到就 `--ip <公网IP>` 手动指定。

**手动分步做**（想看清楚每一步，或者脚本某步失败时按需重来）：

```bash
# ① 装 certbot（snap 版，才有 IP 证书支持，需要 ≥ 5.3）
snap install --classic certbot
ln -sf /snap/bin/certbot /usr/bin/certbot
certbot --version                     # 实测 5.8.0
certbot --help all | grep -E 'ip-address|preferred-profile'   # 两个参数都要在

# ② 建 ACME 验证目录
mkdir -p /var/www/letsencrypt

# ③ 放 80 站点（只服务 ACME，其余跳 HTTPS）
cp deploy/nginx-dashboard-http.conf /etc/nginx/sites-available/dashboard-http.conf
ln -sf /etc/nginx/sites-available/dashboard-http.conf /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default     # 它占着 80 default_server，会抢走 ACME 请求
nginx -t && systemctl reload nginx

# ④ 签证书（先用 --staging 试，避免把正式额度耗光）
certbot certonly --webroot --webroot-path /var/www/letsencrypt \
  --ip-address 203.0.113.10 --preferred-profile shortlived \
  --cert-name 203.0.113.10 \
  --non-interactive --agree-tos --register-unsafely-without-email \
  --staging

# ⑤ staging 通过后删掉重签正式证书
certbot delete --cert-name 203.0.113.10
# 去掉 --staging，其余参数完全一样，再跑一次

# ⑥ 放 443 站点（模板里的 __CERT_NAME__ / __APP_PORT__ 要替换成实际值）
sed -e 's|__CERT_NAME__|203.0.113.10|g' -e 's|__APP_PORT__|8000|g' \
  deploy/nginx-dashboard-https.conf > /etc/nginx/sites-available/dashboard-https.conf
ln -sf /etc/nginx/sites-available/dashboard-https.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# ⑦ 装续期后的 reload 钩子（不装的话续期成功但 nginx 还用旧证书）
#    setup-https.sh 第 5 步就是干这个的，也可以手动抄它那 20 行
```

**关键参数说明**：

| 参数 | 为什么必须有 |
| --- | --- |
| `--ip-address` | 证明「我在给 IP 签，不是给域名签」 |
| `--preferred-profile shortlived` | IP 证书**强制**用这个 profile，不写会失败 |
| `--webroot` | 用 80 端口的静态文件验证，不用停服务 |
| `--register-unsafely-without-email` | 不绑定邮箱，避免到期提醒依赖邮箱 |

### 1.4 续期机制（**必须理解，否则 6.7 天后炸**）

IP 证书**只有 160 小时（≈ 6.7 天）有效期**，这是 Let's Encrypt 的硬规定，
不是配置错了。所以续期不是「锦上添花」，是**每天都必须正常工作的东西**。

| 组件 | 现状 | 查法 |
| --- | --- | --- |
| 系统定时器 | `snap.certbot.renew.timer`，`enabled` + `active`，每天 2 次 | `systemctl list-timers snap.certbot.renew.timer` |
| 续期配置 | `/etc/letsencrypt/renewal/203.0.113.10.conf` | `grep preferred_profile` 应为 `shortlived` |
| 续期后动作 | `renew_hook = /etc/letsencrypt/hooks/reload-nginx.sh` | `grep renew_hook` |
| 钩子日志 | `/var/log/letsencrypt/reload-nginx.log` | `tail -20` |

**为什么一定要 `renew_hook`**：certbot 续期只负责**换文件**，nginx 已经把旧证书
读进内存了，不 reload 就继续用旧的 —— 到第 7 天照样报「证书已过期」。

**三个必查命令**：

```bash
# ① 证书还剩几天（低于 2 天就要人工介入）
openssl x509 -in /etc/letsencrypt/live/203.0.113.10/fullchain.pem \
  -noout -dates

# ② 续期能不能成功（不动真证书，跑 2~3 分钟）
certbot renew --dry-run

# ③ 续期后 nginx 会不会自动 reload（--run-deploy-hooks 才会真的试钩子）
certbot renew --dry-run --run-deploy-hooks
```

两条 dry-run 都出现 `Congratulations, all simulated renewals succeeded` 就是好的。

**手动强制续期**（证书快过期又等不及定时器时）：

```bash
certbot renew --force-renewal && systemctl reload nginx
```

**如果 80 端口断了导致续期失败**：certbot 会重试，但连续失败 30 天后
Let's Encrypt 会发邮件警告（如果绑了邮箱）。**没有邮箱就没有警告**，
所以要么定期跑上面第 ① 条，要么配一个监控。

### 1.5 为什么 IP 也能签证书（不用域名、不用备案）

- **Let's Encrypt 从 2026-01-15 起正式支持 IP 地址证书**
  （[公告](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)）。
  代价是**强制 160 小时短有效期**，官方就是要你自动化续期。
- **Certbot 从 5.3 起支持 `--ip-address`**，5.4 起 `webroot` 模式可用
  （[公告](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)）。
  这也是为什么必须用 snap 装，`apt` 源里的版本太旧。
- **备案与端口无关，只与域名有关**。云厂商的通用口径是：
  「若域名不解析到境内云资源、**仅通过服务器公网 IP 直接访问测试，
  可暂不备案**」。所以用 IP + HTTPS 访问，全程不需要域名、不需要备案。
  （该口径在中国大陆各云厂商一致，具体条文以你所用云厂商的 ICP 说明为准。）

**什么时候必须回到域名方案**：想给多人用、想要 `farm.example.com` 这种地址、
或者想要 90 天证书少折腾 —— 那就买域名 + 备案 + `certbot --nginx -d 域名`。

### 1.6 排障：HTTPS 打不开

**按顺序查，别跳步**：

```bash
# ① nginx 活着吗、在听 80/443 吗
systemctl is-active nginx
ss -lntp | grep -E ':80 |:443 '

# ② 本机经 nginx 能通吗（这一步通 = 服务和证书都没问题）
curl -sk https://127.0.0.1/chat -o /dev/null -w '%{http_code}\n'   # 期望 200
curl -s  http://127.0.0.1/chat  -o /dev/null -w '%{http_code}\n'   # 期望 302

# ③ 证书本身对吗
openssl x509 -in /etc/letsencrypt/live/203.0.113.10/fullchain.pem \
  -noout -dates -ext subjectAltName
```

- ① 里 **443 没有监听** → 443 站点没启用，回到 1.3 第 ⑥ 步。
- ② 通过但外网不通 → **云防火墙没放行 443**，回到 1.2。
- ② 就不通 → `nginx -t` 看配置、`journalctl -u nginx -n 30` 看日志。

> **实测教训：端口不通先查本地有没有监听，再怀疑云防火墙。**
> 配这套时外网 80/443 一直不通，以为防火墙没放行，实际是**服务器上 nginx
> 只监听 8080，80 端口根本没有进程**。cloud 防火墙是通的。
> 判断方法就一句：`ss -lntp | grep ':80 '` 有没有输出。

**怎么确认端口真的放行了**：从你本地机器测
`nc -zv 203.0.113.10 443`（Windows 用 `Test-NetConnection 203.0.113.10 -Port 443`）。
在服务还没装好之前，这个测试大概率是 `Connection refused` —— 那是「端口通了但
没程序在听」，和「防火墙拦了导致超时」是两回事。两者都出现时，先装完服务再回来看。

> **不想对公网开放 8000 端口**？可以改用 ssh 端口转发，只有你自己能访问：
> ```
> ssh -L 8000:127.0.0.1:8000 root@203.0.113.10
> ```
> 然后本地浏览器打开 `http://127.0.0.1:8000`。

---

## 2. 选择一种部署方式

三种方式效果完全一样，选一个就行：

| 方式 | 在哪跑 | 适合谁 | 脚本 |
| --- | --- | --- | --- |
| **A. update.sh** | 你的 Linux / macOS / WSL | 有 rsync、喜欢命令行 | `deploy/update.sh` |
| **B. deploy.ps1** | 你的 Windows（PowerShell） | 在 Windows 上干活 | `deploy/deploy.ps1` |
| **C. 手动** | 服务器上 | 想一步步看清楚在干嘛 / 前两种都失败了 | `deploy/install.sh` |

> 三种方式底层都是同一个 `deploy/install.sh`（幂等）。
> 区别只是「怎么把代码传上去」。

---

## 3. 方式 A：`deploy/update.sh`（Linux / macOS / WSL）

### 3.1 准备

本地需要：`ssh`、`rsync`、`sha256sum`、`bash`。

Windows 上如果没有 WSL，请用方式 B。
（Git Bash **不太行**：它默认没有 `rsync`。）

### 3.2 运行

```bash
cd /path/to/farm
bash deploy/update.sh \
  --server 203.0.113.10 \
  --key ~/.ssh/id_rsa
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--server IP` | **必填**，服务器公网 IP |
| `--key PATH` | **必填**，SSH 私钥路径 |
| `--user NAME` | SSH 用户，默认 `root` |
| `--port N` | 看板端口，默认 `8000` |
| `--app-dir PATH` | 远程安装目录，默认 `/opt/farm` |
| `--init` | 强制走「首次安装」流程（正常会自动判断） |
| `--no-restart` | 只传代码，不装依赖、不重启（改的代码下次重启才生效） |
| `--skip-health` | 跳过健康检查 |

### 3.3 它做什么

1. 测 SSH 连通性（连不上会告诉你查 IP / 防火墙 / 私钥）
2. 判断是**首次部署**还是**更新**（看服务器上有没有 `/opt/farm/backend/app`）
3. **更新时**先备份 `data/` 到 `/opt/farm/backups/data-<时间戳>.tar.gz`，保留最近 10 份
4. `rsync` 推代码（排除 `venv/`、`data/`、`.env`、`backups/`、`__pycache__` 等）
5. 首次部署时远程执行 `install.sh`
6. `requirements.txt` 的 sha256 变了才 `pip install`，没变就跳过（省时间）
7. `systemctl daemon-reload && systemctl restart farm.service`
8. 健康检查 `/health`，最多等 20 秒；失败会打印日志 + 回滚步骤

---

## 4. 方式 B：`deploy/deploy.ps1`（Windows）

### 4.1 准备

Windows 10 1809+ 自带 `ssh.exe` / `scp.exe` / `tar.exe`，通常不用装东西。
如果提示找不到，去「设置 → 应用 → 可选功能 → 添加功能」里装 **OpenSSH 客户端**。

### 4.2 运行

在 PowerShell 里（`E:\Workplace\deepseek_harness_Desktop` 下）：

```powershell
powershell -ExecutionPolicy Bypass -File .\farm\deploy\deploy.ps1 `
  -Server 203.0.113.10 `
  -KeyPath $env:USERPROFILE\.ssh\id_rsa
```

用密码登录（不传 `-KeyPath`，会提示输密码）：

```powershell
.\farm\deploy\deploy.ps1 -Server 203.0.113.10
```

常用参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `-Server` | **必填** | 公网 IP |
| `-KeyPath` | 空 | 私钥路径；不填用默认密钥 / 密码 |
| `-User` | `root` | SSH 用户 |
| `-Port` | `8000` | 看板端口 |
| `-AppDir` | `/opt/farm` | 远程目录 |
| `-NoRestart` | 关 | 只上传解包，不安装不重启 |
| `-SkipHealth` | 关 | 跳过健康检查 |
| `-SkipPackage` | 关 | 复用本地已有的 `farm-latest.tar.gz`（调试用） |

> **这个脚本是 UTF-8 with BOM 保存的**。别用记事本以外的编辑器「另存为 UTF-8」，
> 去掉 BOM 后 PowerShell 5.1 会把中文注释解析成乱码，然后报
> 「缺少右 `}`」这种莫名其妙的错。

### 4.3 它做什么

1. 用 `tar.exe` 打包 `backend/ web/ deploy/ tests/ docs/ CONTRACT.md`
   （**显式列白名单**，不会把 `data/`、`farm.db`、探针脚本带上去）
2. `scp` 上传到 `/tmp/farm-latest.tar.gz`
3. `ssh` 解包到 `/opt/farm`（归档里没有 `data/` 和 `.env`，所以不会覆盖数据库和密码）
4. 远程执行 `install.sh`
5. 健康检查

---

## 5. 方式 C：手动（最透明，也最适合排查）

### 5.1 传代码上去

在你的机器上：

```bash
# 只传需要的东西，不要传 data/ .env venv/
cd /path/to/farm
tar -czf farm.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='data' --exclude='.env' \
  backend web deploy tests docs CONTRACT.md
scp farm.tar.gz root@203.0.113.10:/tmp/
```

Windows 上可以把上面 `tar` 换成：

```powershell
tar.exe -czf farm.tar.gz --exclude "__pycache__" --exclude "*.pyc" backend web deploy tests docs CONTRACT.md
scp.exe farm.tar.gz root@203.0.113.10:/tmp/
```

### 5.2 在服务器上解包并安装

```bash
ssh root@203.0.113.10

mkdir -p /opt/farm
tar -xzf /tmp/farm.tar.gz -C /opt/farm
cd /opt/farm
bash deploy/install.sh
```

想看它准备干什么、不想被问 y/N：

```bash
bash deploy/install.sh --yes
```

---

## 6. `install.sh` 到底做了什么（9 步，每步都幂等）

「幂等」= 重复跑不会炸、不会覆盖已有数据。跑第二遍、第三遍都安全。
（编号与脚本里的 `# ─── N. xxx ───` 注释一一对应。）

| 步 | 干什么 | 幂等怎么保证 |
| --- | --- | --- |
| 1 | **前置检查** | root 才行；检查系统版本；**只读**检查 `irrigation-server` 是否在跑；检查端口占用 |
| 2 | 系统依赖 | 先探测 `python3-venv`/`rsync`/`curl` 是否都在，缺了才 `apt install` |
| 3 | 建系统用户 `farm` | `id -u farm` 存在就复用，不改它的 shell/home |
| 4 | 建目录 + 上游历史库只读授权 + 部署模拟器 v2 | `mkdir -p`；`setfacl` 重复设同样 ACL 无副作用；`.pre-v2` 备份只在不存在时做一次 |
| 5 | 建 venv + 装依赖 | venv 存在就复用；`pip install -r requirements.txt`（清华源，失败自动回退官方 PyPI） |
| 6 | 生成 `/opt/farm/.env` | **已存在就完全不覆盖**，只补缺失的键；首次才随机生成密码 |
| 7 | 装 `farm.service` | `sed` 替换 `__PORT__`/`__WORKERS__` → `daemon-reload` → `enable` → `restart` |
| 8 | 健康检查 | 轮询 `/health` 最多 30 秒；失败打印 `journalctl` 并退出 1 |
| 9 | 打印提示 | 访问地址、首次密码、**云防火墙待办**、⛔ 不要做的事 |

> 第 4 步里有两件看起来「越界」的事，都是必要的：
> - **ACL 授权**：`/home/ubuntu` 是 `drwxr-x--- ubuntu:ubuntu`，`farm` 用户连目录都进不去，
>   不授权则 `/api/events` 永远空。用 `setfacl` 只给 `farm` 读，不动属主和模式。
> - **模拟器 v2**：用户要求按真实物理模型重写，会先备份成
>   `/home/ubuntu/moisture_simulator.py.pre-v2` 再覆盖。不想要就加 `--no-simulator`。

### 参数

```bash
bash deploy/install.sh [选项]

  --port N               监听端口（默认 8000；改了也要去云厂商放行同一个端口）
  --admin-password PWD   初始管理员密码（默认随机生成；**已存在 .env 时此参数无效**）
  --admin-user NAME      初始管理员用户名（默认 admin）
  --no-apt               跳过 apt（python3-venv 已装好时用）
  --no-simulator         不部署模拟器 v2（保留 /home/ubuntu 上现有的那一份）
  --skip-health          跳过装完后的健康检查
  -y, --yes              非交互
  -h, --help             显示帮助
```

### 关于「workers 必须是 1」

`farm.service` 里写死了 `--workers 1`。**别改**，原因：

后端有两处「进程内状态」：
1. `collector.py` 的采集线程（每 60 秒拉一次上游、写 `samples` 表、评估告警）
2. `/api/overview` 的服务端缓存（≤3 秒）

开 N 个 worker 就是 N 个独立进程：N 个采集线程同时写库会互相撞锁，
缓存各存各的，登录失败计数看起来会「跳变」。
4 核 4G 跑一个看板 + 60 秒一次采集完全够用。

---

## 7. 首次登录

安装成功后，`install.sh` 会打印：

```
【首次登录 · 请立刻抄下来】
  用户名 : admin
  密码   : <14 位随机串>
```

- 这串密码**只在安装时打印一次**（同时也写进了 `/opt/farm/.env` 的
  `FARM_ADMIN_PASSWORD=`，忘了可以 `sudo grep '^FARM_ADMIN_PASSWORD=' /opt/farm/.env` 找回）。
- 首次登录会被**强制要求改密码**（契约 §3 的 `must_change`）。
  改完之前，除了 `/api/me` 和 `/api/password`，其它接口一律 403 `请先修改初始密码`。
  **这是设计，不是 bug。**
- 想自己指定初始密码：`bash deploy/install.sh --admin-password '你的密码'`（≥8 位）。
  注意 `.env` 已存在时这个参数**不生效**（不覆盖已有密码）。

---

## 8. 验收：`tests/verify.sh`（11 条全绿才算成功）

### 8.1 怎么跑

在**服务器上**跑（推荐，绕开防火墙变量）：

```bash
cd /opt/farm
bash tests/verify.sh --user admin --pass '你改过的密码'
```

打公网地址（可以在你本地跑，能顺带验证防火墙）：

```bash
bash tests/verify.sh --url http://203.0.113.10:8000 --user admin --pass '密码'
```

### 8.2 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--url URL` | `http://127.0.0.1:8000` | 目标地址 |
| `--user NAME` | `admin` | 登录用户名 |
| `--pass PWD` | 无 | 登录密码（`--password`、`--admin-password` 是等价别名） |
| `--viewer-user NAME` | 自动造 | 第 11 条用的 viewer 账号 |
| `--viewer-password PWD` | 自动造 | viewer 账号密码 |
| `-v, --verbose` | 关 | 打印原始响应体 |
| `-h, --help` | — | 显示帮助 |

### 8.3 11 条都验什么

| # | 验的东西 | 通过条件 |
| --- | --- | --- |
| 1 | `GET /health` | HTTP 200，`status=ok` |
| 2 | `GET /chat` | HTTP 200，返回的是 HTML（`<html` 或 `<!doctype`） |
| 3 | 未登录 `GET /api/overview` | HTTP 401，`message=未登录或登录已过期` |
| 4 | `POST /api/login` 错密码 | `status=error`，**不发 token** |
| 5 | `POST /api/login` 对密码 | HTTP 200，`status=ok`，拿到 token |
| 6 | `GET /api/overview`（带 token） | `channels` 长度 = 3，有 `data_age_sec`，首通道有 `channel`/`soil_moisture`/`pump_status` |
| 7 | `GET /api/history?hours=24&points=100` | `series` 长度 = 3，每通道点数 > 50，时间戳是 Unix 秒整数（9~11 位） |
| 8 | `GET /api/events?days=30` | HTTP 200，`events` 是数组（**空数组也算通过**） |
| 9 | `GET /api/alerts?limit=10` | HTTP 200，`alerts` 是数组 |
| 10 | `GET /api/export.csv?hours=24` | HTTP 200，**首 3 字节 = `EF BB BF`（UTF-8 BOM）**，`Content-Disposition` 含 `attachment` |
| 11 | viewer 账号 `POST /api/control` | HTTP 403，`message=当前账号没有该操作权限` |

### 8.4 退出码

| 码 | 含义 |
| --- | --- |
| `0` | 11 条全部 PASS |
| `1` | 有 FAIL（看输出里哪条 ✗，文末有按编号的排障建议） |
| `2` | 环境问题：连不上服务、缺 curl |

### 8.5 关于第 11 条

需要一个 **viewer** 账号。你不给 `--viewer-password` 时，脚本会用 admin 权限
**自动建一个临时 viewer**（用户名形如 `vfy_view_123456`），建完**不删除**——
擅自删数据库记录比留个测试账号风险更大。

想用自己的：`--viewer-user bob --viewer-password 'bob的密码'`。

### 8.6 常见的「看着像 FAIL 其实是正常」

- **admin 还没改初始密码** → 第 6~11 条全 403。
  这是契约强制的首次改密，**不是故障**。先去浏览器改密码，再重跑。
- **第 4 条**：HTTP 状态可能是 200 也可能是 401，只要 `status=error` 且没 token 就算过。
- **第 8 / 9 条**：`events`/`alerts` 是空数组 `[]` 也算过。
  真没灌过水时空是正常的；**但如果后端在 `note` 里写着「上游历史库不可读」，
  那是权限没配好（见 11.4），不是数据为空。**

---

## 9. 三通道的读数现在会真实变化了（模拟器 v2）

> **本节的旧内容已作废。** 早期版本这里写的是「三个通道恒为 80.0，是模拟器饱和，
> 不是 bug」——那是当时的实况，但用户随后要求把模拟逻辑换掉，已经处理完毕。

### 9.1 现在是什么状态

`moisture-simulator.service` 跑的是**物理水平衡模型 v2**，按真实农田的水量平衡推进：

```
每分钟含水量变化 = 灌溉 + 降雨 − 作物蒸散(ET) − 深层渗漏
```

- **蒸散**由太阳高度角驱动（用大连经纬度算赤纬与时角），夜里为 0、正午最强，
  再按气温和云量修正 → 有昼夜节律，也会随季节变化。
- **深层渗漏**按土壤质地的时间常数下渗：沙土分钟级、黏土小时级。
- **降雨**读取上游 `/api/weather` 的真实预报（`WEATHER_CITY=大连`），
  按「今日降雨概率」逐日抽签生成阵雨，雨天同时压低蒸散。
- **三块地参数不同**（沙壤土 / 壤土 / 黏壤土，各自的田间持水量、凋萎点、作物系数、
  灌溉触发点），所以三条曲线不会重合成一条线。

实测（14 天离线自检）：沙壤土 36.5~44.0%、壤土 39.8~60.3%、黏壤土 45.0~67.3%，
灌溉在触发点自动开泵、到补充目标自动停泵。

### 9.2 怎么确认它在正常工作

```bash
# 看模拟器日志里的开泵/停泵/降雨事件
journalctl -u moisture-simulator -n 40 --no-pager | grep -E 'irrig|rain|mqtt'

# 看实时读数（每 5 秒推一次 MQTT，上游缓存成 realtime-data）
curl -sS http://127.0.0.1:5000/api/realtime-data | head -c 400
```

正常情况下 `soil_N` 会缓慢变化（分钟级 0.1~0.5 个百分点），泵会在含水率跌到
「触发点」时自动开。

### 9.3 想改回原来的模拟器

```bash
# 旧版完整备份就在旁边
sudo cp /home/ubuntu/moisture_simulator.py.pre-v2 /home/ubuntu/moisture_simulator.py
sudo systemctl restart moisture-simulator
```

或者重装时加 `--no-simulator` 就不会覆盖（但已覆盖过的不会被自动还原，
请用上面的 `cp` 命令）。

### 9.4 想调参

模拟器全部参数都走环境变量，改 `/etc/systemd/system/moisture-simulator.service`
的 `Environment=` 行即可（改完 `systemctl daemon-reload && systemctl restart moisture-simulator`）：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `FARM_LATITUDE` / `FARM_LONGITUDE` | `38.91` / `121.61` | 决定日照曲线，换地方要改 |
| `ET0_PEAK_MM_PER_HOUR` | `0.62` | 晴天正午蒸散强度（mm/h） |
| `MAX_PUMP_MIN` | `90` | 单次连续抽水上限，防淹 |
| `WEATHER_URL` | `http://127.0.0.1:5000/api/weather` | 天气预报来源 |
| `PUBLISH_INTERVAL` | `5` | 推送间隔（秒） |

源文件在仓库 `deploy/moisture_simulator_v2.py`，改完重新部署用
`bash deploy/install.sh --yes`（会自动覆盖并重启）。


---

## 10. 日常运维

```bash
# 服务状态
systemctl status farm.service

# 重启（改 .env 后必须重启才生效）
systemctl restart farm.service

# 实时日志
journalctl -u farm.service -f

# 最近 100 行
journalctl -u farm.service -n 100 --no-pager

# 确认上游服务与模拟器都活着
systemctl is-active irrigation-server.service moisture-simulator.service mosquitto.service

# 模拟器最近的开泵/停泵/降雨事件（v2 才有这些日志）
journalctl -u moisture-simulator -n 40 --no-pager
```

### 改配置

```bash
sudo nano /opt/farm/.env       # 改完保存
sudo systemctl restart farm.service
```

`/opt/farm/.env` 里的键：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `FARM_UPSTREAM` | `http://127.0.0.1:5000` | 上游灌溉 API 地址 |
| `FARM_UPSTREAM_TIMEOUT` | `6` | 上游超时（秒） |
| `FARM_DATA` | `/opt/farm/data` | 本项目数据目录 |
| `FARM_IRRDB` | `/home/ubuntu/irrigation_history.db` | 上游历史库（**只读**） |
| `FARM_TOKEN_TTL` | `43200` | token 有效期（秒，**12 小时**）。别设太长：明文 HTTP 下 token 被嗅探到就能直接冒用 |
| `FARM_SAMPLE_SEC` | `60` | 采样间隔（秒） |
| `FARM_STALE_SEC` | `600` | 超过这么久没新数据就标「已过期」 |
| `FARM_TIMER_FRESH_SEC` | `10` | 读数超过这么多秒就当「不新鲜」，累计时长计时器**立刻停表**。上游 5 秒推一次，= 漏 2 次心跳就停 |
| `FARM_TIMER_GAP_SEC` | `120` | 两次新鲜观测间隔超过这么多秒，整段不补进计时（防睡死/长断流后凭空跳涨） |
| `FARM_TIMER_MAX_RUN_SEC` | `14400` | 单次连续运行上限（秒，4 小时）。超过就停止累加，防逻辑卡死无限涨 |
| `FARM_PORT` | `8000` | 端口（改了要同步改 `farm.service` + 云厂商放行） |
| `FARM_ADMIN_USER` | `admin` | 初始管理员用户名 |
| `FARM_ADMIN_PASSWORD` | 随机 | 初始密码（改密码请用看板「账号」页，改这里无效） |

### 备份

```bash
# 手动备份本项目数据（farm.db + 导出文件）
sudo tar -czf /opt/farm/backups/data-$(date +%Y%m%d-%H%M%S).tar.gz -C /opt/farm data

# 看有哪些备份
ls -1t /opt/farm/backups/
```

`update.sh` 每次更新前会自动做一次，保留最近 10 份。

---

## 11. 故障排查

### 11.1 浏览器打不开 / 一直转圈

**先确认你用的是哪个地址**：现在正式入口是 `https://203.0.113.10/chat`
（经 nginx），不是 `:8000`。两者的排查路径不同。

```bash
# ① 服务起来了吗
systemctl status farm.service

# ② 后端端口在听吗
ss -ltnp | grep 8000

# ③ 后端本机直连通吗（这一步通过说明 farm.service 没问题）
curl -v http://127.0.0.1:8000/health

# ④ nginx 在听 80/443 吗
ss -ltnp | grep -E ':80 |:443 '

# ⑤ 经 nginx 通吗（这一步通过说明反代和证书都没问题）
curl -sk https://127.0.0.1/chat -o /dev/null -w '%{http_code}\n'   # 期望 200
curl -s  http://127.0.0.1/chat  -o /dev/null -w '%{http_code}\n'   # 期望 302

# ⑥ 日志
journalctl -u farm.service -n 80 --no-pager
journalctl -u nginx -n 40 --no-pager
```

| 卡在哪 | 结论 |
| --- | --- |
| ③ 就通不过 | `farm.service` 有问题，看 11.2 |
| ③ 通、④ 里 443 没监听 | 443 站点没启用，看 1.3 第 ⑥ 步 |
| ③④⑤ 都通，浏览器还是不通 | **云防火墙没放行 443**，回到第 1.2 节 |
| 提示「您的连接不是私密连接」 | 证书过期或没续上，看 1.4 节（IP 证书只活 6.7 天） |

### 11.2 服务起不来

```bash
journalctl -u farm.service -n 100 --no-pager
```

找第一段 `Traceback`，常见原因：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'fastapi'` | venv 里没装依赖 | `sudo /opt/farm/venv/bin/pip install -r /opt/farm/backend/requirements.txt` |
| `Address already in use` | 8000 被别的程序占了 | `ss -ltnp \| grep 8000` 看是谁；换端口 `--port 8001` |
| `unable to open database file` | `/opt/farm/data` 权限不对 | `chown -R farm:farm /opt/farm/data && chmod 750 /opt/farm/data` |
| `Permission denied: /home/ubuntu/irrigation_history.db` | `ProtectHome` 设置错了 | 确认 `farm.service` 里是 `ProtectHome=read-only`，**不是** `true` |

### 11.3 总览显示「灌溉服务暂时不可达」

这是**上游**不通，不是看板坏了。看板会正常返回（带 `upstream_ok: false`），不会 500。

```bash
# 上游活着吗
systemctl is-active irrigation-server.service
curl -sS http://127.0.0.1:5000/api/realtime-data | head -c 300
```

上游挂了**不要自己去重启它** —— 那不是本项目的服务，找原系统的负责人。

### 11.4 `/api/events` 一直是空的

两种情况：

1. **正常为空**：上游 `irrigation_events` 表里最近确实没有灌溉记录。空数组是正确结果，不是 bug。
2. **从不空却一直空 → 权限问题**（部署时最容易踩的坑）：
   `/home/ubuntu` 是 `drwxr-x--- ubuntu:ubuntu`，`farm` 用户连目录都进不去，
   于是后端读上游历史库失败，只能返回空数组并在 `note` 里写「上游历史库不可读」。

   自检一条命令：

   ```bash
   sudo -u farm test -r /home/ubuntu/irrigation_history.db && echo 可读 || echo 不可读
   ```

   显示「不可读」就补 ACL（`install.sh` 已自动做，手工补如下）：

   ```bash
   apt-get install -y acl
   setfacl -m u:farm:rx /home/ubuntu
   setfacl -m u:farm:r  /home/ubuntu/irrigation_history.db
   ```

   > 注意：`farm.service` 里的 `ProtectHome=read-only` **不能**解决这个问题——
   > 它只放开 systemd 自己的命名空间，挡路的是文件系统权限本身。
   > 也**不要**用 `chmod o+rx /home/ubuntu` 把家目录对所有人开放。

   修完 `curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/events?days=30`
   应能看到 `events` 数组里出现记录。

### 11.5 CSV 导出乱码

`/api/export.csv` 带 UTF-8 BOM（`EF BB BF`），Excel 双击打开不会乱码。
如果乱码，是别的工具吃掉了 BOM。

### 11.6 忘了管理员密码

在服务器上执行（服务需处于运行状态）：

```bash
sudo -u farm /opt/farm/venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "/opt/farm/backend")
from app import auth
print(auth.set_password("admin", "新密码至少8位", must_change=1))
PY

sudo systemctl restart farm.service
```

这样会把密码重置成新的，并把 `must_change` 设成 1（下次登录强制改密码）。

---

## 12. 更新与回滚

### 12.1 更新

```bash
# Linux / macOS / WSL
bash deploy/update.sh --server 203.0.113.10 --key ~/.ssh/id_rsa

# Windows
.\farm\deploy\deploy.ps1 -Server 203.0.113.10 -KeyPath $env:USERPROFILE\.ssh\id_rsa
```

更新**不会**覆盖：
- `/opt/farm/data/`（数据库、采样、告警、审计）
- `/opt/farm/.env`（管理员密码、上游地址）

### 12.2 回滚代码

每次安装/更新前会自动把代码打到 `/opt/farm/backups/app-<时间戳>.tar.gz`：

```bash
ssh root@203.0.113.10
ls -1t /opt/farm/backups/app-*.tar.gz | head -3        # 挑一个时间点
tar -xzf /opt/farm/backups/app-20261024-190000.tar.gz -C /opt/farm
systemctl restart farm.service
```

### 12.3 回滚数据

⚠️ **正常情况不需要**。只有当你确认 `farm.db` 被写坏了才做：

```bash
tar -xzf /opt/farm/backups/data-20261024-190000.tar.gz -C /opt/farm
chown -R farm:farm /opt/farm/data
systemctl restart farm.service
```

### 12.4 完全卸载

```bash
systemctl stop farm.service
systemctl disable farm.service
rm -f /etc/systemd/system/farm.service
systemctl daemon-reload
rm -rf /opt/farm
userdel farm
```

> 卸载 **只删自己**。`irrigation-server`、`mosquitto`、
> `irrigation_history.db` 一个都不许动。
>
> `moisture-simulator` 是**唯一**被本项目改过文件的：`install.sh` 把
> `/home/ubuntu/moisture_simulator.py` 换成了 v2。卸载时如果想还原，
> 单独执行（这一步是可选的，不还原也能继续跑）：
>
> ```bash
> sudo cp /home/ubuntu/moisture_simulator.py.pre-v2 /home/ubuntu/moisture_simulator.py
> sudo systemctl restart moisture-simulator
> ```

---

## 13. 文件清单（`deploy/` 与 `tests/`）

| 文件 | 在哪跑 | 作用 |
| --- | --- | --- |
| `deploy/install.sh` | 服务器（root） | 一键安装，9 步，幂等 |
| `deploy/update.sh` | 本地 Linux/macOS/WSL | rsync 推代码 + 远程 install + 重启 + 健康检查 |
| `deploy/deploy.ps1` | 本地 Windows | tar+scp+ssh 版的 update.sh（UTF-8 BOM 保存） |
| `deploy/farm.service` | — | systemd 单元模板（`__PORT__`/`__WORKERS__` 由 install.sh 替换） |
| `deploy/setup-https.sh` | 服务器（root） | **一条命令配好 HTTPS**：装 certbot → 签 IP 证书 → 配 80/443 → 装续期钩子 → 自检 |
| `deploy/nginx-dashboard-http.conf` | 服务器 | 80 端口站点模板：只服务 ACME 验证，其余 302 跳 HTTPS |
| `deploy/nginx-dashboard-https.conf` | 服务器 | 443 端口站点模板：反代到 127.0.0.1:8000 + HSTS（`__CERT_NAME__`/`__APP_PORT__` 占位） |
| `deploy/moisture_simulator_v2.py` | 服务器 | 湿度模拟器 v2（物理水平衡模型），`install.sh` 会推到 `/home/ubuntu/` |
| `tests/verify.sh` | 服务器或本地 | 11 条验收，全 PASS 才 exit 0 |
| `tests/test_api.py` | 开发机 | pytest 单元/接口测试 |
| `tests/make_sample.py` | 开发机 | 造本地测试 CSV（**不写任何数据库**） |

---

## 14. 一页速查（贴在手边）

```bash
# 装
ssh root@203.0.113.10
cd /opt/farm && bash deploy/install.sh

# 验
cd /opt/farm && bash tests/verify.sh --user admin --pass '密码'

# 看
systemctl status farm.service
journalctl -u farm.service -f

# 重启
systemctl restart farm.service

# 忘密码
sudo -u farm /opt/farm/venv/bin/python -c \
  'import sys;sys.path.insert(0,"/opt/farm/backend");from app import auth;print(auth.set_password("admin","新密码123",must_change=1))'
sudo systemctl restart farm.service
```

### HTTPS 速查（IP 证书只活 6.7 天，这条最重要）

```bash
# 证书还剩几天（< 2 天必须人工介入）
openssl x509 -in /etc/letsencrypt/live/203.0.113.10/fullchain.pem -noout -dates

# 续期能不能成（不动真证书，约 2~3 分钟）
certbot renew --dry-run

# 续期后 nginx 会不会自动 reload
certbot renew --dry-run --run-deploy-hooks

# 定时器活着吗
systemctl list-timers snap.certbot.renew.timer

# 钩子最近干了什么
tail -20 /var/log/letsencrypt/reload-nginx.log

# 手动强续 + 重载
certbot renew --force-renewal && systemctl reload nginx
```

**永远不要做：**
- `systemctl stop/restart/disable irrigation-server.service mosquitto.service`
  （`moisture-simulator` 例外：`install.sh` 部署 v2 时会重启它，这是设计内的）
- 往 `/home/ubuntu/irrigation_history.db` 写东西
- 把 `farm.service` 的 `--workers` 改成大于 1
- 删除 `/opt/farm/.env`（管理员密码在里面）
- **关掉云防火墙的 80 端口**（证书续期靠它，关了就 6.7 天后全站报错）
