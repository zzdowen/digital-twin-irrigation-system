#!/usr/bin/env bash
# =============================================================================
# FARM 看板 · 代码更新脚本（在**本地电脑**跑：Linux / macOS / WSL / Git Bash）
#
# 作用：备份服务器上的 data → 把本地项目目录用 rsync 推到 /opt/farm →
#       依赖有变化才 pip install → systemctl restart farm.service →
#       等 /health 最多 20 秒 → 失败时给出回滚命令。
#       **绝不覆盖**服务器上的 data/、venv/、.env。
#       **绝不碰** irrigation-server / moisture-simulator / mosquitto 三个上游服务。
#
# 用法：
#   # 日常更新（推代码 + 重启 + 健康检查）
#   bash deploy/update.sh --server 203.0.113.10 --key ~/.ssh/id_rsa
#
#   # 首次部署（服务器上还没有 /opt/farm）：推代码 + 远程跑 install.sh
#   bash deploy/update.sh --server 203.0.113.10 --key ~/.ssh/id_rsa --init
#
#   # 只推代码不重启
#   bash deploy/update.sh --server 203.0.113.10 --key ~/.ssh/id_rsa --no-restart
#
# 参数：
#   --server IP     服务器公网 IP（必填）
#   --key PATH      SSH 私钥路径（用密码登录请改用 deploy/deploy.ps1，或手工 scp）
#   --user NAME     SSH 用户（默认 root）
#   --port N        看板端口（默认 8000）
#   --app-dir DIR   远程安装目录（默认 /opt/farm）
#   --init          首次部署：远程执行 deploy/install.sh
#   --no-restart    只推代码，不重启服务
#   --skip-health   跳过健康检查
#   -y, --yes       非交互
#   -h, --help      显示帮助
#
# 退出码：0 成功；1 失败（失败时终端会打印回滚步骤）。
# =============================================================================
set -Eeuo pipefail

SERVER=""
KEY_PATH=""
SSH_USER="root"
PORT="${FARM_PORT:-8000}"
APP_DIR="/opt/farm"
DO_INIT=0
DO_RESTART=1
SKIP_HEALTH=0
ASSUME_YES=0
LOCAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

trap 'printf "\n%s[FAIL]%s 更新在第 %s 行中断。服务器上服务状态未变（除非已执行 restart），可重跑本脚本。\n" "$C_RED" "$C_RST" "$LINENO" >&2' ERR

usage() { sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# ─────────────────────────── 参数解析 ───────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server)      SERVER="${2:?--server 需要 IP}"; shift 2 ;;
    --key)         KEY_PATH="${2:?--key 需要路径}"; shift 2 ;;
    --user)        SSH_USER="${2:?--user 需要用户名}"; shift 2 ;;
    --port)        PORT="${2:?--port 需要值}"; shift 2 ;;
    --app-dir)     APP_DIR="${2:?--app-dir 需要路径}"; shift 2 ;;
    --init)        DO_INIT=1; shift ;;
    --no-restart)  DO_RESTART=0; shift ;;
    --skip-health) SKIP_HEALTH=1; shift ;;
    -y|--yes)      ASSUME_YES=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "未知参数：$1（用 --help 查看用法）" ;;
  esac
done

[[ -n "$SERVER" ]]   || { usage; die "必须指定 --server <服务器IP>"; }
[[ -n "$KEY_PATH" ]] || die "必须指定 --key <SSH私钥路径>（密码登录请用 deploy/deploy.ps1）"
[[ -f "$KEY_PATH" ]] || die "私钥不存在：$KEY_PATH"
[[ "$PORT" =~ ^[0-9]+$ ]] || die "--port 必须是数字：$PORT"
[[ -f "${LOCAL_ROOT}/backend/requirements.txt" ]] \
  || die "本地目录不像 FARM 项目根：${LOCAL_ROOT}（找不到 backend/requirements.txt）"
[[ -f "${LOCAL_ROOT}/deploy/farm.service" ]] \
  || die "本地缺少 deploy/farm.service，项目不完整。"

command -v rsync >/dev/null 2>&1 || die "本地缺少 rsync。Windows 请用 Git Bash 并安装 rsync，或改用 deploy/deploy.ps1。"
command -v ssh   >/dev/null 2>&1 || die "本地缺少 ssh 客户端。"

# 私钥权限：OpenSSH 拒绝过宽权限
if stat -c '%a' "$KEY_PATH" >/dev/null 2>&1; then
  perm="$(stat -c '%a' "$KEY_PATH")"
elif stat -f '%Lp' "$KEY_PATH" >/dev/null 2>&1; then
  perm="$(stat -f '%Lp' "$KEY_PATH")"
else
  perm="600"
fi
if [[ "$perm" != "600" && "$perm" != "400" ]]; then
  warn "私钥权限是 ${perm}，尝试 chmod 600（OpenSSH 可能拒绝过宽权限）"
  chmod 600 "$KEY_PATH" 2>/dev/null || warn "chmod 失败；若 SSH 报 permissions too open 请手工处理"
fi

SSH_OPTS=(-i "$KEY_PATH" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15
          -o BatchMode=yes -o ServerAliveInterval=15)
TARGET="${SSH_USER}@${SERVER}"
SSH=(ssh "${SSH_OPTS[@]}" "$TARGET")

# ─────────────────────────── 1. 连通性 ───────────────────────────
info "测试 SSH 连通性：${TARGET} ……"
"${SSH[@]}" 'true' >/dev/null 2>&1 || die \
  "SSH 连不上 ${TARGET}。依次检查：① IP 对不对 ② 云厂商防火墙是否放行 22/tcp ③ 私钥是否匹配 ④ 用户是否为 ${SSH_USER}"
ok "SSH 已连通。"

# ─────────────────────────── 2. 首次 vs 更新 ───────────────────────────
if "${SSH[@]}" "test -d ${APP_DIR}/backend/app" >/dev/null 2>&1; then
  REMOTE_INSTALLED=1
  info "服务器上已有安装：${APP_DIR}（按「更新」流程）"
else
  REMOTE_INSTALLED=0
  info "服务器上还没有 ${APP_DIR}/backend/app（按「首次部署」流程）"
  DO_INIT=1
fi

if [[ "$DO_INIT" -eq 1 && "$REMOTE_INSTALLED" -eq 0 ]]; then
  info "准备服务器基础依赖（python3-venv / rsync / curl）……"
  # shellcheck disable=SC2029
  "${SSH[@]}" "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq --no-install-recommends python3 python3-venv python3-pip rsync curl ca-certificates openssl" \
    || die "服务器 apt 安装失败（可能不是 Ubuntu/Debian，或没有外网）。"
  "${SSH[@]}" "mkdir -p ${APP_DIR}" || die "创建 ${APP_DIR} 失败。"
  ok "服务器基础依赖就绪。"
fi

# ─────────────────────────── 3. 备份数据（只备份运行态，不备份代码） ───────────────────────────
if [[ "$REMOTE_INSTALLED" -eq 1 ]]; then
  info "备份服务器数据（farm.db + 导出文件，最多保留 10 份）……"
  # shellcheck disable=SC2029
  "${SSH[@]}" "set -e
mkdir -p ${APP_DIR}/backups
if [ -d ${APP_DIR}/data ] && [ -n \"\$(ls -A ${APP_DIR}/data 2>/dev/null)\" ]; then
  B=\${APP_DIR}/backups/data-\$(date +%Y%m%d-%H%M%S).tar.gz
  tar -czf \"\$B\" -C ${APP_DIR} data 2>/dev/null && echo \"data backup: \$B\"
  ls -1t ${APP_DIR}/backups/data-*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f || true
else
  echo 'no data to backup (fresh install)'
fi" || warn "备份数据失败（继续，但本次更新就没有数据兜底了）"
  ok "数据备份完成（详见 ${APP_DIR}/backups/）。"
else
  info "首次部署，无数据可备份，跳过。"
fi

# ─────────────────────────── 4. rsync 推代码 ───────────────────────────
# 排除服务器侧运行态与本地垃圾：data/、venv/、.env、backups/、缓存、探针脚本
RSYNC_EXCLUDES=(
  --exclude 'venv/' --exclude 'data/' --exclude '.env' --exclude 'backups/'
  --exclude '.git/' --exclude '__pycache__/' --exclude '*.pyc' --exclude '*.pyo'
  --exclude '.pytest_cache/' --exclude '.mypy_cache/' --exclude '.ruff_cache/'
  --exclude 'node_modules/' --exclude '.DS_Store' --exclude '.venv/'
  --exclude '.idea/' --exclude '.vscode/'
  --exclude '.pytest-tmp-*/' --exclude 'pytest-cache-files-*/'
  --exclude 'probe-*/' --exclude 'probe_*.py' --exclude 'pm-*/' --exclude '.tmp*/'
)

info "上传代码 ${LOCAL_ROOT} → ${TARGET}:${APP_DIR} ……"
# shellcheck disable=SC2086
rsync -az --delete \
  -e "ssh ${SSH_OPTS[*]}" \
  "${RSYNC_EXCLUDES[@]}" \
  --stats \
  "${LOCAL_ROOT}/" "${TARGET}:${APP_DIR}/" \
  || die "rsync 上传失败。可加 -v 重跑看细节，或检查服务器磁盘空间（df -h）。"
ok "代码上传完成。"

# 属主/权限：代码 root:farm 只读，数据目录归 farm
# shellcheck disable=SC2029
"${SSH[@]}" "set -e
chown -R root:farm ${APP_DIR} 2>/dev/null || true
chmod -R g+rX ${APP_DIR} 2>/dev/null || true
chown -R farm:farm ${APP_DIR}/data 2>/dev/null || true
chmod 750 ${APP_DIR} ${APP_DIR}/data 2>/dev/null || true
[ -f ${APP_DIR}/.env ] && chown root:farm ${APP_DIR}/.env && chmod 640 ${APP_DIR}/.env || true" \
  2>/dev/null || warn "设置属主/权限时告警（首次部署时 farm 用户可能还不存在，属正常）。"

# ─────────────────────────── 5. 首次安装 ───────────────────────────
if [[ "$DO_INIT" -eq 1 ]]; then
  info "远程执行 deploy/install.sh（幂等，可重复跑）……"
  # shellcheck disable=SC2029
  "${SSH[@]}" "cd ${APP_DIR} && bash deploy/install.sh --yes --port ${PORT}" \
    || die "install.sh 执行失败。登录服务器排查：bash ${APP_DIR}/deploy/install.sh（不带 --yes 看交互信息）"
  ok "install.sh 执行完成。"
fi

# ─────────────────────────── 6. 依赖 + 重启 ───────────────────────────
REQUIREMENTS_CHANGED=0
if [[ "$DO_RESTART" -eq 1 ]]; then
  info "检查依赖是否有变化……"
  # 用 sha256 指纹比对，避免每次白装一遍
  NEW_SUM="$(sha256sum "${LOCAL_ROOT}/backend/requirements.txt" | awk '{print $1}')"
  OLD_SUM="$("${SSH[@]}" "cat ${APP_DIR}/.requirements.sha256 2>/dev/null || echo none" 2>/dev/null || echo none)"
  if [[ "$NEW_SUM" != "$OLD_SUM" ]]; then
    REQUIREMENTS_CHANGED=1
    info "依赖有变化，安装中（清华源，失败回退官方源）……"
    # shellcheck disable=SC2029
    "${SSH[@]}" "set -e
cd ${APP_DIR}
${APP_DIR}/venv/bin/pip install -q -r ${APP_DIR}/backend/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 --retries 3 \
  || ${APP_DIR}/venv/bin/pip install -q -r ${APP_DIR}/backend/requirements.txt --timeout 120 --retries 3
echo '${NEW_SUM}' > ${APP_DIR}/.requirements.sha256
chown root:farm ${APP_DIR}/.requirements.sha256 2>/dev/null || true" \
      || die "依赖安装失败。手工：ssh ${TARGET} '${APP_DIR}/venv/bin/pip install -r ${APP_DIR}/backend/requirements.txt'"
    ok "依赖已更新。"
  else
    info "依赖未变化（sha256 一致），跳过 pip。"
  fi

  info "重新加载 systemd 并重启 farm.service ……"
  # shellcheck disable=SC2029
  "${SSH[@]}" "set -e
systemctl daemon-reload
systemctl restart farm.service
echo restarted" || die "重启失败。日志：ssh ${TARGET} 'journalctl -u farm.service -n 80 --no-pager'"
  ok "farm.service 已重启。"
else
  info "已指定 --no-restart，跳过依赖安装与重启（改动的代码下次重启才生效）。"
fi

# ─────────────────────────── 7. 健康检查（最多 20 秒） ───────────────────────────
if [[ "$DO_RESTART" -eq 1 && "$SKIP_HEALTH" -eq 0 ]]; then
  info "健康检查 http://127.0.0.1:${PORT}/health（最多 20 秒）……"
  # shellcheck disable=SC2029
  if "${SSH[@]}" "for i in \$(seq 1 20); do curl -fsS --max-time 2 http://127.0.0.1:${PORT}/health 2>/dev/null | grep -q '\"ok\"' && exit 0; sleep 1; done; exit 1"; then
    ok "健康检查通过。"
  else
    warn "20 秒内 /health 没有返回 ok。最近 60 行日志："
    "${SSH[@]}" "journalctl -u farm.service -n 60 --no-pager" 2>/dev/null || true
    warn "上游服务状态（只读）："
    "${SSH[@]}" "systemctl is-active irrigation-server.service moisture-simulator.service mosquitto.service 2>&1 || true" 2>/dev/null || true
    cat <<EOF

${C_YEL}【回滚建议】${C_RST}
  1) 先看日志里第一段 Traceback，多半是 import 错误或语法问题。
  2) 代码回滚（用本次更新自动打的备份）：
       ssh ${TARGET}
       ls -1t ${APP_DIR}/backups/app-*.tar.gz | head -3
       tar -xzf ${APP_DIR}/backups/app-<选中的时间戳>.tar.gz -C ${APP_DIR}
       systemctl restart farm.service
  3) 数据回滚（仅当你怀疑 farm.db 被写坏；正常情况**不需要**）：
       tar -xzf ${APP_DIR}/backups/data-<时间戳>.tar.gz -C ${APP_DIR}
       chown -R farm:farm ${APP_DIR}/data
       systemctl restart farm.service
  4) 注意：回滚 farm 不会影响上游 irrigation-server，那个服务全程没被碰过。

EOF
    die "服务未正常启动，更新失败。"
  fi
fi

# ─────────────────────────── 8. 结果 ───────────────────────────
PUBLIC_IP="$("${SSH[@]}" "curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || echo ''" 2>/dev/null || true)"
[[ -z "$PUBLIC_IP" ]] && PUBLIC_IP="$SERVER"

cat <<EOF

${C_GRN}${C_BLD}════════════ FARM 看板更新完成 ════════════${C_RST}

  访问地址 : http://${PUBLIC_IP}:${PORT}/
  健康检查 : http://${PUBLIC_IP}:${PORT}/health
  服务器   : ${TARGET}:${APP_DIR}
  依赖     : $([[ "$REQUIREMENTS_CHANGED" -eq 1 ]] && echo "本次有更新" || echo "未变化")
  日志     : ssh -i ${KEY_PATH} ${TARGET} 'journalctl -u farm.service -f'

${C_YEL}【提示】${C_RST}
  * ${APP_DIR}/data 与 .env **未被覆盖**；管理员密码、历史采样、告警都还在。
  * 本次更新前已自动备份数据到 ${APP_DIR}/backups/data-*.tar.gz。
  * 上游三个服务（irrigation-server / moisture-simulator / mosquitto）全程未被触碰。
  * 验收：ssh 上去执行  bash ${APP_DIR}/tests/verify.sh --user admin --pass '<当前密码>'

EOF
