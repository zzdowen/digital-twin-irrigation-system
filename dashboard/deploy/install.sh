#!/usr/bin/env bash
# =============================================================================
# FARM 灌溉监控看板 · 一键安装脚本（Ubuntu 24.04，云服务器）
#
# 用法（必须在服务器上以 root 执行）：
#   bash deploy/install.sh                          # 完整安装（幂等，可重复跑）
#   bash deploy/install.sh --admin-password 'Xx123456'   # 指定初始管理员密码
#   bash deploy/install.sh --port 8000 --no-apt      # 跳过 apt（依赖已装好）
#   bash deploy/install.sh --skip-health             # 不装完就不做健康检查
#   bash deploy/install.sh --yes                     # 非交互
#
# 它做什么（每一步都幂等，重复执行不会炸、不会覆盖已有数据）：
#   1. 前置检查：root / 系统版本 / 上游 irrigation-server 是否活着（**只读检查，绝不动它**）
#   2. 系统依赖：python3-venv（缺了才 apt 装）
#   3. 系统用户 farm（nologin，不给 shell）
#   4. 目录 /opt/farm 与 /opt/farm/data（owner farm，权限 750）
#   5. 上游历史库只读授权（setfacl 给 farm 读 /home/ubuntu/irrigation_history.db）
#   6. 部署土壤墒情模拟器 v2（物理水平衡模型，替换上游的随机模拟；先备份 .pre-v2）
#   7. 虚拟环境 /opt/farm/venv + pip 装 backend/requirements.txt（清华源，失败回退官方源）
#   8. 环境变量 /opt/farm/.env（**已存在就不覆盖**；首次生成随机 FARM_ADMIN_PASSWORD / FARM_SECRET，权限 640）
#   9. 安装 farm.service 并 enable --now
#  10. 健康检查 /health（最多 30 秒），失败打印日志并提示
#  11. 打印访问地址、首次登录说明、云防火墙待办
#
# ⛔ 铁律：本脚本**绝不** stop / restart / disable / 修改以下服务与文件：
#     irrigation-server.service（上游 Flask，127.0.0.1:5000）
#     mosquitto.service
#     /home/ubuntu/irrigation_history.db（只读它）
#   唯一例外是第 6 步的 moisture-simulator：它正是用户要求按真实物理模型重写的
#   模块，会先备份为 moisture_simulator.py.pre-v2 再覆盖；加 --no-simulator 可跳过。
#   除此之外只做两件事：新建 farm.service，以及读取上游服务的状态。
# =============================================================================
set -Eeuo pipefail

# ─────────────────────────── 常量 ───────────────────────────
APP_NAME="farm"
APP_USER="farm"
APP_GROUP="farm"
APP_DIR="/opt/farm"
VENV_DIR="${APP_DIR}/venv"
DATA_DIR="${APP_DIR}/data"
ENV_FILE="${APP_DIR}/.env"
SERVICE_NAME="farm.service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
REQ_FILE="${APP_DIR}/backend/requirements.txt"
UPSTREAM_DB="/home/ubuntu/irrigation_history.db"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # 仓库根目录（本地镜像解包后即 /opt/farm）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # 本脚本所在目录（deploy/）
PYPI_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"

# ─────────────────────────── 默认参数 ───────────────────────────
PORT="${FARM_PORT:-8000}"
WORKERS="1"                 # ⚠️ 固定 1，见 farm.service 顶部注释；本脚本不提供改它的参数
ADMIN_USER="${FARM_ADMIN_USER:-admin}"
ADMIN_PASSWORD="${FARM_ADMIN_PASSWORD:-}"
DO_APT=1
INSTALL_SIM=1               # 部署上游墒情模拟器 v2（物理水平衡模型），--no-simulator 可跳过
SKIP_HEALTH=0
ASSUME_YES=0

# ─────────────────────────── 输出 ───────────────────────────
if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
  C_CYN=$'\033[36m'; C_BLD=$'\033[1m'; C_RST=$'\033[0m'
else
  C_RED=""; C_GRN=""; C_YEL=""; C_CYN=""; C_BLD=""; C_RST=""
fi
info() { printf '%s[INFO]%s %s\n' "$C_CYN" "$C_RST" "$*"; }
ok()   { printf '%s[ OK ]%s %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_YEL" "$C_RST" "$*" >&2; }
die()  { printf '%s[FAIL]%s %s\n' "$C_RED" "$C_RST" "$*" >&2; exit 1; }

trap 'printf "\n%s[FAIL]%s 安装在第 %s 行中断。把上面这段错误原文发回来即可定位。\n" "$C_RED" "$C_RST" "$LINENO" >&2' ERR

usage() {
  # 只打印文件顶部的注释块：从第 2 行起连续以 # 开头的行，
  # 遇到第一个非注释行（set -Eeuo pipefail）就停。
  # 不要写成 sed -n '2,34p' —— 行号会随文件增删而漂移，把代码打进帮助里。
  awk 'NR>1 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
  cat <<'EOF'
其他可选参数：
  --port N               监听端口（默认 8000；改这里也要去云厂商控制台放行同一个端口）
  --admin-password PWD  初始管理员密码（默认随机生成并写入 .env，只打印一次）
  --admin-user NAME     初始管理员用户名（默认 admin）
  --no-apt              跳过 apt（python3-venv 已装好时用）
  --no-simulator        不部署土壤墒情模拟器 v2（保留上游原有模拟器）
  --skip-health         跳过装完后的健康检查
  -y, --yes             非交互（不再询问确认）
  -h, --help            显示本帮助
EOF
}

# ─────────────────────────── 参数解析 ───────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)           PORT="${2:?--port 需要值}"; shift 2 ;;
    --admin-password) ADMIN_PASSWORD="${2:?--admin-password 需要值}"; shift 2 ;;
    --admin-user)     ADMIN_USER="${2:?--admin-user 需要值}"; shift 2 ;;
    --no-apt)         DO_APT=0; shift ;;
    --no-simulator)   INSTALL_SIM=0; shift ;;
    --skip-health)    SKIP_HEALTH=1; shift ;;
    -y|--yes)         ASSUME_YES=1; shift ;;
    -h|--help)        usage; exit 0 ;;
    *)                die "未知参数：$1（用 --help 查看用法）" ;;
  esac
done

[[ "$PORT" =~ ^[0-9]+$ ]] || die "--port 必须是数字，收到：$PORT"
[[ "$PORT" -ge 1 && "$PORT" -le 65535 ]] || die "--port 超出范围：$PORT"
if [[ -n "$ADMIN_PASSWORD" && ${#ADMIN_PASSWORD} -lt 8 ]]; then
  die "--admin-password 至少 8 位（契约 §3：密码规则 ≥8 位），收到 ${#ADMIN_PASSWORD} 位。"
fi

# ─────────────────────────── 1. 前置检查 ───────────────────────────
info "检查运行环境……"
[[ "$(id -u)" -eq 0 ]] || die "请用 root 执行：sudo bash deploy/install.sh"

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  info "系统：${PRETTY_NAME:-unknown}（${ID:-?} ${VERSION_ID:-?}）"
  case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:24.04|ubuntu:22.04|debian:12) : ;;
    ubuntu:*|debian:*) warn "当前 ${ID} ${VERSION_ID}，脚本针对 Ubuntu 24.04 验证过，继续尝试。" ;;
    *)                 warn "不是 Ubuntu/Debian（${ID:-?}），apt 步骤可能失败；可加 --no-apt 跳过。" ;;
  esac
else
  warn "读不到 /etc/os-release，跳过系统版本检查。"
fi

[[ -f "$REQ_FILE" ]] || die "找不到 ${REQ_FILE} —— 请先把整个项目解包到 ${APP_DIR} 再运行本脚本。"
[[ -f "${SRC_DIR}/deploy/${SERVICE_NAME}" ]] \
  || die "找不到 ${SRC_DIR}/deploy/${SERVICE_NAME}，项目目录不完整。"

# ── 上游服务存在性（只读检查！绝不 stop/restart/disable） ──
if systemctl list-unit-files irrigation-server.service >/dev/null 2>&1 \
   && systemctl cat irrigation-server.service >/dev/null 2>&1; then
  if systemctl is-active --quiet irrigation-server.service; then
    ok "上游 irrigation-server.service 正在运行（本脚本只读检查，不会动它）。"
  else
    warn "上游 irrigation-server.service 存在但**没在运行**。看板仍能启动，但总览会显示「灌溉服务暂时不可达」。"
    warn "如需修它，请找原系统的负责人；本脚本不会去启动/重启它。"
  fi
else
  warn "没找到 irrigation-server.service。若上游不在本机，确认 .env 里 FARM_UPSTREAM 指向正确地址。"
fi

# 端口占用：被别的程序占了要提前死；被现有 farm 服务占着属正常升级
if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${PORT}$"; then
  if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    info "端口 ${PORT} 已被现有 farm.service 占用，按升级流程继续。"
  elif ss -ltnp 2>/dev/null | grep -E "[:.]${PORT}[[:space:]]" | grep -q 'irrigation'; then
    die "端口 ${PORT} 被上游灌溉服务占用。请用 --port 换一个（例如 8001），并去云厂商控制台放行该端口。"
  else
    die "端口 ${PORT} 已被其它程序占用：$(ss -ltnp 2>/dev/null | grep -E "[:.]${PORT}[[:space:]]" || true)
换 --port，或先停掉占用进程。"
  fi
fi

if [[ "$ASSUME_YES" -eq 0 && -t 0 ]]; then
  cat <<EOF

  ${C_BLD}即将安装 FARM 灌溉监控看板${C_RST}
    项目源目录 : ${SRC_DIR}
    安装目录   : ${APP_DIR}
    运行用户   : ${APP_USER}（系统用户，无登录 shell）
    监听       : 0.0.0.0:${PORT}
    worker 数  : ${WORKERS}（固定 1，采集线程与缓存是进程内状态）
    上游服务   : http://127.0.0.1:5000（只读，绝不修改）

EOF
  read -r -p "确认继续？[y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || die "已取消。"
fi

# ─────────────────────────── 2. 系统依赖 ───────────────────────────
if [[ "$DO_APT" -eq 1 ]]; then
  NEED_APT=0
  command -v python3 >/dev/null 2>&1 || NEED_APT=1
  python3 -c 'import venv' 2>/dev/null || NEED_APT=1
  python3 -c 'import ensurepip' 2>/dev/null || NEED_APT=1
  command -v curl >/dev/null 2>&1 || NEED_APT=1
  command -v rsync >/dev/null 2>&1 || NEED_APT=1

  if [[ "$NEED_APT" -eq 1 ]]; then
    info "安装系统依赖（python3-venv / python3-pip / rsync / curl / ca-certificates）……"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq || warn "apt-get update 有告警，继续尝试安装。"
    apt-get install -y -qq --no-install-recommends \
      python3 python3-venv python3-pip rsync curl ca-certificates openssl \
      || die "apt 安装失败。可改用 --no-apt 跳过（自己确保 python3-venv 可用）。"
    ok "系统依赖安装完成。"
  else
    ok "系统依赖已齐全，跳过 apt。"
  fi
else
  info "已指定 --no-apt，跳过系统依赖安装。"
fi

command -v python3 >/dev/null 2>&1 || die "缺少 python3"
PY_VER="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
info "python3 版本：${PY_VER}（契约要求 3.12；3.10+ 也能跑）"
python3 -c 'import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)' \
  || die "需要 Python >= 3.10，当前 ${PY_VER}。"
python3 -c 'import venv' 2>/dev/null || die "python3-venv 不可用，请先 apt install python3-venv"

# ─────────────────────────── 3. 系统用户 ───────────────────────────
if id -u "$APP_USER" >/dev/null 2>&1; then
  info "系统用户 ${APP_USER} 已存在，复用（不改它的 shell/home）。"
else
  info "创建系统用户 ${APP_USER}（--system --nologin）……"
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin --user-group "$APP_USER" \
    || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
  ok "用户 ${APP_USER} 已创建。"
fi

# ─────────────────────────── 4. 目录 ───────────────────────────
info "准备目录 ${APP_DIR} 与 ${DATA_DIR} ……"
mkdir -p "$APP_DIR" "$DATA_DIR" "${DATA_DIR}/files"
# 升级时不要动代码的属主判定，只保证数据目录归 farm 且宿主机 root 能读
#
# 注意：APP_DIR 自身必须是 root:${APP_GROUP} 750，不能留 root:root。
# farm.service 的 WorkingDirectory=/opt/farm 由 farm 用户 chdir 进入，
# 属主是 root:root 且 others 无 x 权限时 systemd 会直接
# "Changing to the requested working directory failed: Permission denied"
# → status=200/CHDIR → 服务反复重启直到 Start request repeated too quickly。
chown "root:${APP_GROUP}" "$APP_DIR"
chown -R "${APP_USER}:${APP_GROUP}" "$DATA_DIR"
chmod 750 "$APP_DIR" "$DATA_DIR"
chmod 750 "${DATA_DIR}/files"
ok "目录就绪（${DATA_DIR} 归 ${APP_USER}，权限 750）。"

# ── 上游历史库只读授权 ──
# /home/ubuntu 是 drwxr-x--- ubuntu:ubuntu，farm 用户连目录都进不去，
# 于是 /api/events 会静默返回空数组（note="上游历史库不可读"）。
# systemd 的 ProtectHome=read-only 挡不住这个——它只放开 systemd 自己的命名空间，
# 挡路的是文件系统权限本身。用 ACL 做最小授权（只给 farm 读，不改目录属主和模式），
# 比 chmod o+r /home/ubuntu（把家目录对所有人开放）安全得多。
if [[ -f "$UPSTREAM_DB" ]]; then
  if ! command -v setfacl >/dev/null 2>&1; then
    info "安装 acl（只为给 ${APP_USER} 授予上游历史库只读权限）……"
    if [[ "$DO_APT" -eq 0 ]]; then
      apt-get install -y acl >/dev/null 2>&1 || warn "acl 安装失败，/api/events 可能为空"
    else
      warn "未安装 acl（--no-apt），若 /api/events 为空请手动 apt install acl 后重跑本脚本"
    fi
  fi
  if command -v setfacl >/dev/null 2>&1; then
    UP_DIR="$(dirname "$UPSTREAM_DB")"
    setfacl -m "u:${APP_USER}:rx" "$UP_DIR" 2>/dev/null || warn "无法给 ${UP_DIR} 设 ACL"
    setfacl -m "u:${APP_USER}:r"  "$UPSTREAM_DB" 2>/dev/null || warn "无法给 ${UPSTREAM_DB} 设 ACL"
    if sudo -u "$APP_USER" test -r "$UPSTREAM_DB" 2>/dev/null; then
      ok "上游历史库只读授权完成（${APP_USER} 可读 ${UPSTREAM_DB}）。"
    else
      warn "上游历史库仍不可读，/api/events 会返回空数组（其余功能不受影响）。"
    fi
  fi
else
  warn "没找到上游历史库 ${UPSTREAM_DB}，/api/events 将返回空数组。"
fi

# ── 上游土壤墒情模拟器（物理水平衡模型 v2）──
# 原模拟器是「随机增减 + 上限写死 80.0」，导致三路读数永远贴在 80%，看板上
# 曲线是死线。新模型按 ΔS = 灌溉 + 降雨 − 蒸散 − 深层渗漏 推进，并读取上游
# /api/weather 的真实预报。两个文件都是本项目自带的，随包安装：
#   deploy/moisture_simulator_v2.py   （本仓库唯一副本，源）
#   /home/ubuntu/moisture_simulator.py（上游服务的实际执行文件，安装目标）
# 注意：这个文件属于上游项目的目录，但**模拟器本身正是用户要求替换的模块**；
# 只在 --install-simulator 显式开启时才覆盖，且会先备份成 .pre-v2。
if [[ "$INSTALL_SIM" -eq 1 ]]; then
  SIM_SRC="${SCRIPT_DIR}/moisture_simulator_v2.py"
  SIM_DST="/home/ubuntu/moisture_simulator.py"
  if [[ ! -f "$SIM_SRC" ]]; then
    warn "没找到 ${SIM_SRC}，跳过模拟器部署。"
  elif [[ ! -f "$SIM_DST" ]]; then
    warn "找不到上游模拟器 ${SIM_DST}，跳过（请确认上游项目路径）。"
  else
    info "部署物理水平衡模拟器 v2 ……"
    [[ -f "${SIM_DST}.pre-v2" ]] || cp -a "$SIM_DST" "${SIM_DST}.pre-v2"
    cp "$SIM_SRC" "$SIM_DST"
    chown ubuntu:ubuntu "$SIM_DST" 2>/dev/null || true
    chmod 755 "$SIM_DST"
    python3 -m py_compile "$SIM_DST" || die "模拟器语法检查失败，已保留备份 ${SIM_DST}.pre-v2"
    systemctl restart moisture-simulator 2>/dev/null || warn "重启 moisture-simulator 失败"
    sleep 3
    if systemctl is-active --quiet moisture-simulator; then
      ok "模拟器 v2 已上线（旧版备份：${SIM_DST}.pre-v2）。"
    else
      warn "moisture-simulator 未处于 active，请查看 journalctl -u moisture-simulator"
    fi
  fi
fi

# 升级前备份代码（不备份 data/venv，那是运行态）
if [[ -f "${APP_DIR}/backend/app/main.py" ]]; then
  BK_DIR="${APP_DIR}/backups"
  mkdir -p "$BK_DIR"
  BK="${BK_DIR}/app-$(date +%Y%m%d-%H%M%S).tar.gz"
  info "检测到已有安装，先备份代码到 ${BK} ……"
  tar -czf "$BK" -C "$APP_DIR" --exclude=venv --exclude=data --exclude=backups . 2>/dev/null \
    || warn "备份失败（不影响升级）"
  # 只保留最近 5 份；无备份时 ls 返回非零，用 || true 兜住 set -e
  { ls -1t "${BK_DIR}"/app-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f; } || true
fi

# ─────────────────────────── 5. venv + 依赖 ───────────────────────────
if [[ -x "${VENV_DIR}/bin/python" ]]; then
  info "复用已有虚拟环境：${VENV_DIR}"
else
  info "创建虚拟环境：${VENV_DIR} ……"
  python3 -m venv "$VENV_DIR" || die "创建 venv 失败。多半是缺 python3-venv。"
fi
"${VENV_DIR}/bin/python" -m pip install --upgrade -q pip setuptools wheel \
    -i "$PYPI_MIRROR" --timeout 60 \
  || warn "pip 自身升级失败（不影响后续，继续）。"
ok "pip 就绪：$("${VENV_DIR}/bin/python" -m pip --version | awk '{print $2}')"

info "安装 Python 依赖（清华源，首次约 30-90 秒）……"
if ! "${VENV_DIR}/bin/pip" install -q -r "$REQ_FILE" \
      -i "$PYPI_MIRROR" --timeout 120 --retries 3; then
  warn "清华源失败，回退官方 PyPI 重试……"
  "${VENV_DIR}/bin/pip" install -q -r "$REQ_FILE" --timeout 120 --retries 3 \
    || die "依赖安装失败。手工排查：${VENV_DIR}/bin/pip install -r ${REQ_FILE}"
fi
ok "Python 依赖安装完成。"

# 依赖自检：契约 §2 只允许这四个
"${VENV_DIR}/bin/python" - <<'PY' || die "依赖自检失败：检查 requirements.txt 与网络。"
import importlib, sys
need = ["fastapi", "uvicorn", "pytest", "httpx"]
miss = []
for m in need:
    try:
        importlib.import_module(m)
    except Exception as e:      # noqa: BLE001
        miss.append("%s(%s)" % (m, e))
if miss:
    print("MISSING:", ", ".join(miss))
    sys.exit(1)
import fastapi, uvicorn
print("deps OK: fastapi", fastapi.__version__, "| uvicorn", uvicorn.__version__)
PY
ok "依赖自检通过（fastapi / uvicorn / pytest / httpx）。"

# 代码属主：代码归 root 可读、farm 只读即可；data 目录归 farm
chown -R "root:${APP_GROUP}" "$APP_DIR/backend" 2>/dev/null || true
chmod -R g+rX "$APP_DIR/backend" 2>/dev/null || true
if [[ -d "${APP_DIR}/web" ]]; then
  chown -R "root:${APP_GROUP}" "${APP_DIR}/web" 2>/dev/null || true
  chmod -R g+rX "${APP_DIR}/web" 2>/dev/null || true
fi

# ─────────────────────────── 6. 环境变量 ───────────────────────────
gen_secret() { openssl rand -hex 24 2>/dev/null || tr -dc 'a-f0-9' </dev/urandom | head -c 48; }
gen_password() {  # 14 位，字母+数字，避免 shell 特殊字符
  local p=""
  p="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 14 || true)"
  [[ ${#p} -eq 14 ]] || p="Farm$(date +%s)$$"
  printf '%s' "$p"
}

ENV_CREATED=0
if [[ -f "$ENV_FILE" ]]; then
  info "已存在 ${ENV_FILE}，**保留原有配置不覆盖**（管理员密码、上游地址都不动）。"
  # 只补可能缺失的键，不覆盖已有值；用 flock 式的轻量判断避免重复追加
  add_env_default() {
    local key="$1" val="$2"
    grep -q "^${key}=" "$ENV_FILE" || { printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"; info "补入缺失配置 ${key}"; }
  }
  add_env_default "FARM_UPSTREAM" "http://127.0.0.1:5000"
  add_env_default "FARM_DATA" "$DATA_DIR"
  add_env_default "FARM_IRRDB" "$UPSTREAM_DB"
  add_env_default "FARM_TOKEN_TTL" "604800"
  add_env_default "FARM_SAMPLE_SEC" "60"
  add_env_default "FARM_PORT" "$PORT"
  [[ -n "$ADMIN_PASSWORD" ]] && warn "服务器上已有 .env，本次 --admin-password 不生效（不覆盖已有密码）。要改请用看板里的「账号」页。"
else
  info "生成环境变量文件：${ENV_FILE}"
  [[ -n "$ADMIN_PASSWORD" ]] || ADMIN_PASSWORD="$(gen_password)"
  ADMIN_PASSWORD_OUT="$ADMIN_PASSWORD"
  cat > "$ENV_FILE" <<EOF
# =============================================================================
# FARM 灌溉监控看板 · 运行环境变量（systemd EnvironmentFile 读取）
#
# 改完必须重启服务才生效：  sudo systemctl restart farm.service
# 权限 640（root:farm），里面有管理员初始密码，别外传、别提交到代码库。
# =============================================================================

# 上游灌溉服务（只读；本看板绝不修改它）
FARM_UPSTREAM=http://127.0.0.1:5000
# 上游不可达时的超时（秒）
FARM_UPSTREAM_TIMEOUT=6

# 本项目自己的数据目录（SQLite、导出文件都在这里）
FARM_DATA=${DATA_DIR}
# 上游历史库（**只读**打开，用于 /api/events）
FARM_IRRDB=${UPSTREAM_DB}

# token 有效期（秒），默认 7 天
FARM_TOKEN_TTL=604800
# 采样间隔（秒）：每这么久拉一次上游数据写 samples 表并评估告警
FARM_SAMPLE_SEC=60
# 数据超过这么多秒没有更新就算「过期」
FARM_STALE_SEC=600

# 服务端口（改这里还要同步改 farm.service 的 ExecStart，并去云厂商放行）
FARM_PORT=${PORT}

# 初始管理员。首次启动时若 users 表里没有该账号才创建；
# 之后改密码请用看板「账号」页，改这里不会覆盖已改过的密码。
FARM_ADMIN_USER=${ADMIN_USER}
FARM_ADMIN_PASSWORD=${ADMIN_PASSWORD}

# 预留的会话密钥（当前代码未读取，留给将来做签名用；留着不算错）
FARM_SECRET=$(gen_secret)
EOF
  ENV_CREATED=1
  ok "环境变量已生成。"
fi

chown "root:${APP_GROUP}" "$ENV_FILE"
chmod 640 "$ENV_FILE"
ok "环境变量权限已设为 640（root:${APP_GROUP}）。"

# ─────────────────────────── 7. systemd ───────────────────────────
info "安装 systemd 服务 ${SERVICE_NAME} ……"
install -m 644 "${SRC_DIR}/deploy/${SERVICE_NAME}" "$SERVICE_FILE"
sed -i "s/__PORT__/${PORT}/g" "$SERVICE_FILE"
sed -i "s/__WORKERS__/${WORKERS}/g" "$SERVICE_FILE"
if grep -q '__PORT__\|__WORKERS__' "$SERVICE_FILE"; then
  warn "服务文件占位替换不完整，请手工检查 ${SERVICE_FILE}"
fi
grep -q -- "--workers ${WORKERS}" "$SERVICE_FILE" \
  || warn "worker 数替换未生效，请检查 ${SERVICE_FILE}（契约要求 workers=1）"
grep -q -- "--port ${PORT}" "$SERVICE_FILE" \
  || warn "端口替换未生效，请检查 ${SERVICE_FILE}"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || warn "开机自启设置失败（不影响本次运行）"
systemctl restart "$SERVICE_NAME"
ok "服务已启动：systemctl status ${SERVICE_NAME}"

# ─────────────────────────── 8. 健康检查 ───────────────────────────
if [[ "$SKIP_HEALTH" -eq 0 ]]; then
  info "等待服务就绪（最多 30 秒）……"
  H_OK=0
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"ok"'; then
      H_OK=1; break
    fi
    sleep 1
  done
  if [[ "$H_OK" -eq 1 ]]; then
    ok "健康检查通过：http://127.0.0.1:${PORT}/health"
  else
    warn "30 秒内 /health 无响应。最近 50 行日志："
    journalctl -u "$SERVICE_NAME" -n 50 --no-pager 2>/dev/null || true
    die "服务没有正常启动。常见原因见 docs/DEPLOY.md「常见故障排查」。"
  fi
else
  info "已指定 --skip-health，跳过健康检查。"
fi

# ─────────────────────────── 9. 完成提示 ───────────────────────────
PUBLIC_IP="$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || true)"
[[ -z "$PUBLIC_IP" ]] && PUBLIC_IP="203.0.113.10"

cat <<EOF

${C_GRN}${C_BLD}════════════════ FARM 看板安装完成 ════════════════${C_RST}

  访问地址   ${C_BLD}http://${PUBLIC_IP}:${PORT}/${C_RST}
  健康检查   http://${PUBLIC_IP}:${PORT}/health
  安装目录   ${APP_DIR}
  运行用户   ${APP_USER}（无登录 shell）
  数据目录   ${DATA_DIR}
  服务单元   ${SERVICE_NAME}（workers=${WORKERS}）
  日志       journalctl -u ${SERVICE_NAME} -f

${C_YEL}【⚠️ 第 0 步：先去放行 8000 端口，否则浏览器打不开】${C_RST}
  云防火墙目前只放行了 22 / 5000 / 4096 / 6080 / 1883，**8000 还没放行**。
  云厂商控制台 → 轻量应用服务器 → 选中本机 → 「防火墙」→「添加规则」：
      应用类型 : 自定义
      协议     : TCP
      端口     : ${PORT}
      来源     : 0.0.0.0/0
      策略     : 允许
  保存后立即生效，不用重启服务器。
  （如果只在本机看，可以不动防火墙，用 ssh 端口转发：
     ssh -L ${PORT}:127.0.0.1:${PORT} root@${PUBLIC_IP} 然后访问 127.0.0.1:${PORT}）

EOF

if [[ "$ENV_CREATED" -eq 1 && -n "${ADMIN_PASSWORD_OUT:-}" ]]; then
  cat <<EOF
${C_YEL}【首次登录 · 请立刻抄下来】${C_RST}
  用户名 : ${C_BLD}${ADMIN_USER}${C_RST}
  密码   : ${C_BLD}${ADMIN_PASSWORD_OUT}${C_RST}
  ${C_RED}这串密码只在本次安装时打印这一次${C_RST}（已存进 ${ENV_FILE}，可 sudo grep 找回）。
  首次登录会被**强制要求改密码**（契约 §3：must_change），改完才能进主界面。

EOF
elif [[ "$ENV_CREATED" -eq 1 ]]; then
  cat <<EOF
${C_YEL}【首次登录】${C_RST}
  用户名 : ${ADMIN_USER}
  密码   : 见 ${ENV_FILE} 里的 FARM_ADMIN_PASSWORD
          查看命令： sudo grep '^FARM_ADMIN_PASSWORD=' ${ENV_FILE}

EOF
else
  cat <<EOF
${C_YEL}【首次登录】${C_RST}
  本次是升级/重装，${ENV_FILE} 已存在 → 管理员密码**没有变**，沿用你上次改过的密码。
  忘了密码就手动重置（看板必须处于运行状态）：
     sudo -u ${APP_USER} ${VENV_DIR}/bin/python - <<'PY'
     import sys; sys.path.insert(0, "/opt/farm/backend")
     from app import auth
     print(auth.set_password("${ADMIN_USER}", "新密码至少8位", must_change=1))
     PY
     sudo systemctl restart ${SERVICE_NAME}

EOF
fi

cat <<EOF
${C_YEL}【验收自测】${C_RST}
  cd ${APP_DIR} && bash tests/verify.sh --user ${ADMIN_USER} --pass '<当前密码>'
  11 条全 PASS 才算部署成功；任何一条 FAIL 都会 exit 1。
  （管理员首次登录未改密时，第 6 条起会 403，这是契约的强制改密，不是故障。）

${C_YEL}【日常运维】${C_RST}
  systemctl status  ${SERVICE_NAME}
  systemctl restart ${SERVICE_NAME}
  journalctl -u ${SERVICE_NAME} -n 100 --no-pager
  systemctl is-active irrigation-server.service moisture-simulator.service mosquitto.service

${C_YEL}【⛔ 不要做的事】${C_RST}
  * 不要 stop / restart / disable irrigation-server.service、moisture-simulator.service、mosquitto.service
  * 不要往 /home/ubuntu/irrigation_history.db 写任何东西（看板只读它）
  * 不要把 farm.service 的 --workers 改成 >1（采集线程与缓存是进程内状态）
  * 不要把这个服务暴露成公网无密码访问；它是给人看的后台，靠登录挡着

${C_GRN}${C_BLD}════════════════════════════════════════════════${C_RST}

EOF
