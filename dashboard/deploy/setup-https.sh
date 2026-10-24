#!/usr/bin/env bash
# =============================================================================
# 给看板配 HTTPS（Let's Encrypt **IP 证书**，不需要域名，不需要 ICP 备案）
#
# 为什么能用 IP 签证书：
#   Let's Encrypt 自 2026-01 起支持 IP 地址证书，代价是**强制 160 小时有效期**
#   （约 6.7 天），官方就是要你自动化续期。Certbot 5.3+ 支持 --ip-address。
#
# 用法（在服务器上以 root 执行）：
#   bash deploy/setup-https.sh                      # 自动探测本机公网 IP
#   bash deploy/setup-https.sh --ip 203.0.113.10    # 显式指定
#   bash deploy/setup-https.sh --staging            # 用测试环境签（不占正式额度，强烈建议先跑一次）
#   bash deploy/setup-https.sh --skip-renew-test    # 不跑 dry-run（省 2 分钟）
#
# 前置条件：
#   1. 云防火墙已放行 22 / 80 / 443
#   2. 看板本身已装好（bash deploy/install.sh）并监听 127.0.0.1:8000 或 0.0.0.0:8000
#
# 这一步会做 7 件事（全部幂等，可重复跑）：
#   1) 用 snap 装 certbot（apt 源版本太旧，不支持 IP 证书）
#   2) 建 ACME 验证目录 /var/www/letsencrypt
#   3) 装 80 端口的 nginx 站点（只服务 ACME，其余 302 跳 HTTPS）
#   4) 签 IP 证书（默认先 staging 探测）
#   5) 装 443 端口的 nginx 站点（反代到 127.0.0.1:8000）
#   6) 装续期后自动 reload nginx 的钩子（不装的话续期成功也白搭）
#   7) 跑 certbot renew --dry-run --run-deploy-hooks 自检
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBROOT="/var/www/letsencrypt"
APP_PORT="${APP_PORT:-8000}"

PUBLIC_IP=""
USE_STAGING=0
SKIP_RENEW_TEST=0

C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
C_BLD=$'\033[1m';  C_RST=$'\033[0m'

info() { printf '%s[ info ]%s %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '%s[ warn ]%s %s\n' "$C_YEL" "$C_RST" "$*"; }
die()  { printf '%s[ fail ]%s %s\n' "$C_RED" "$C_RST" "$*" >&2; exit 1; }
step() { printf '\n%s%s── %s ──%s\n' "$C_BLD" "$C_GRN" "$*" "$C_RST"; }

usage() {
    awk 'NR>1 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ip)              PUBLIC_IP="${2:-}"; shift 2 ;;
        --ip=*)            PUBLIC_IP="${1#*=}"; shift ;;
        --port)            APP_PORT="${2:-}"; shift 2 ;;
        --staging)         USE_STAGING=1; shift ;;
        --skip-renew-test) SKIP_RENEW_TEST=1; shift ;;
        -h|--help)         usage; exit 0 ;;
        *)                 die "未知参数：$1（用 --help 看用法）" ;;
    esac
done

[[ "$(id -u)" -eq 0 ]] || die "必须用 root 执行（sudo bash deploy/setup-https.sh）"

# ─────────────────────────── 0. 探测公网 IP ───────────────────────────
step "0. 探测公网 IP"
if [[ -z "$PUBLIC_IP" ]]; then
    PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
fi
[[ -n "$PUBLIC_IP" ]] || die "探测不到公网 IP。请用 --ip 指定，例如 --ip 203.0.113.10"
info "公网 IP = ${C_BLD}${PUBLIC_IP}${C_RST}"
CERT_NAME="$PUBLIC_IP"
LIVE_DIR="/etc/letsencrypt/live/${CERT_NAME}"

# ─────────────────────────── 1. 装 certbot ───────────────────────────
step "1. 安装 certbot（snap 版）"
if command -v certbot >/dev/null 2>&1; then
    info "certbot 已存在：$(certbot --version 2>&1 | head -1)"
else
    info "通过 snap 安装（apt 源版本不支持 --ip-address）"
    snap install --classic certbot
    ln -sf /snap/bin/certbot /usr/bin/certbot
fi
# 能力自检：这两个参数缺一个就走不下去
if ! certbot --help all 2>/dev/null | grep -q -- '--ip-address'; then
    die "当前 certbot 不支持 --ip-address（需要 ≥ 5.3）。请确认装的是 snap 版。"
fi
if ! certbot --help all 2>/dev/null | grep -q -- '--preferred-profile'; then
    die "当前 certbot 不支持 --preferred-profile（需要 ≥ 5.3）。"
fi
info "certbot 支持 IP 证书 ✓"

# ─────────────────────────── 2. ACME 目录 + 80 站点 ───────────────────────────
step "2. 配置 80 端口（ACME 验证）"
mkdir -p "$WEBROOT"
chmod 755 "$WEBROOT"

install -m 644 "${SCRIPT_DIR}/nginx-dashboard-http.conf" \
    /etc/nginx/sites-available/dashboard-http.conf
ln -sf /etc/nginx/sites-available/dashboard-http.conf \
    /etc/nginx/sites-enabled/dashboard-http.conf

# 拿掉 nginx 默认站点（它占着 80 的 default_server，会抢走 ACME 请求）
if [[ -e /etc/nginx/sites-enabled/default ]]; then
    rm -f /etc/nginx/sites-enabled/default
    info "已移除 nginx 默认站点（它占着 80 default_server）"
fi

nginx -t || die "nginx 配置检查失败，请看上面的报错"
systemctl reload nginx
info "80 端口已就绪"

# 本机自测：ACME 路径必须能读到文件
echo "test-$(date +%s)" > "${WEBROOT}/.selftest"
if curl -fsS --max-time 5 "http://127.0.0.1/.well-known/acme-challenge/.selftest" >/dev/null; then
    info "ACME 路径本机可达 ✓"
else
    warn "ACME 路径本机不可达，签发大概率会失败。检查 nginx 80 站点。"
fi
rm -f "${WEBROOT}/.selftest"

# ─────────────────────────── 3. 签证书 ───────────────────────────
step "3. 申请 IP 证书"
CERTBOT_ARGS=(
    certonly
    --webroot --webroot-path "$WEBROOT"
    --ip-address "$PUBLIC_IP"
    --preferred-profile shortlived      # IP 证书强制要求，不写会失败
    --cert-name "$CERT_NAME"
    --non-interactive
    --agree-tos
    --register-unsafely-without-email   # 不绑邮箱，避免续期失败时无提醒 → 见文档
)

if [[ "$USE_STAGING" -eq 1 ]]; then
    info "使用 staging 环境（不会占用正式额度，但证书不被浏览器信任）"
    certbot "${CERTBOT_ARGS[@]}" --staging
    warn "staging 证书已签出。确认流程没问题后，重跑本脚本时**不要**加 --staging。"
    exit 0
fi

if [[ -f "${LIVE_DIR}/fullchain.pem" ]]; then
    info "证书已存在：${LIVE_DIR}/fullchain.pem"
    openssl x509 -in "${LIVE_DIR}/fullchain.pem" -noout -dates -ext subjectAltName || true
else
    certbot "${CERTBOT_ARGS[@]}"
    info "证书已签发"
    openssl x509 -in "${LIVE_DIR}/fullchain.pem" -noout -dates -ext subjectAltName
fi

# ─────────────────────────── 4. 443 站点 ───────────────────────────
step "4. 配置 443 端口（HTTPS 反代）"
# 把模板里的占位符换成实际值
sed -e "s|__CERT_NAME__|${CERT_NAME}|g" \
    -e "s|__APP_PORT__|${APP_PORT}|g" \
    "${SCRIPT_DIR}/nginx-dashboard-https.conf" \
    > /etc/nginx/sites-available/dashboard-https.conf
chmod 644 /etc/nginx/sites-available/dashboard-https.conf
ln -sf /etc/nginx/sites-available/dashboard-https.conf \
    /etc/nginx/sites-enabled/dashboard-https.conf

nginx -t || die "443 站点配置检查失败"
systemctl reload nginx
info "443 端口已就绪"

# ─────────────────────────── 5. 续期钩子 ───────────────────────────
step "5. 安装续期后 reload 钩子"
# 关键：certbot 续期只换文件，nginx 内存里还是旧证书。不 reload 就等于没续。
mkdir -p /etc/letsencrypt/hooks
cat > /etc/letsencrypt/hooks/reload-nginx.sh <<HOOK
#!/usr/bin/env bash
# 由 certbot 在每次成功续期后调用。失败不能静默，必须进日志。
set -u
LOG=/var/log/letsencrypt/reload-nginx.log
{
    echo "===== \$(date -Is) 续期后 reload ====="
    if nginx -t 2>&1; then
        systemctl reload nginx && echo "reload OK"
        openssl x509 -in ${LIVE_DIR}/fullchain.pem -noout -dates 2>&1
    else
        echo "ERROR: nginx 配置检查失败，跳过 reload"
        exit 1
    fi
} >> "\$LOG" 2>&1
HOOK
chmod 700 /etc/letsencrypt/hooks/reload-nginx.sh

RENEW_CONF="/etc/letsencrypt/renewal/${CERT_NAME}.conf"
if [[ -f "$RENEW_CONF" ]]; then
    if grep -q '^renew_hook' "$RENEW_CONF"; then
        sed -i 's|^renew_hook.*|renew_hook = /etc/letsencrypt/hooks/reload-nginx.sh|' "$RENEW_CONF"
    else
        sed -i '/^\[\[webroot_map\]\]/i renew_hook = /etc/letsencrypt/hooks/reload-nginx.sh' "$RENEW_CONF"
    fi
    grep -n 'renew_hook' "$RENEW_CONF"
    info "renew_hook 已写入 $RENEW_CONF"
else
    warn "找不到 $RENEW_CONF，跳过硬编码钩子（下次续期时会自动生成）"
fi

# 立即验证钩子能跑（不动证书）
/etc/letsencrypt/hooks/reload-nginx.sh && info "钩子手工执行成功 ✓"

# ─────────────────────────── 6. 续期自检 ───────────────────────────
step "6. 续期自检"
info "定时器状态："
systemctl list-timers 'snap.certbot.renew.timer' --no-pager 2>/dev/null | head -3 || warn "没找到 certbot 续期定时器"

if [[ "$SKIP_RENEW_TEST" -eq 0 ]]; then
    info "跑 certbot renew --dry-run --run-deploy-hooks（约 2~3 分钟，不需要动真证书）"
    if certbot renew --dry-run --run-deploy-hooks; then
        info "续期演练通过 ✓（含钩子）"
    else
        warn "续期演练失败——**6.7 天后证书会过期**，请立刻排查 80 端口与 DNS/网络"
    fi
fi

# ─────────────────────────── 7. 完成 ───────────────────────────
step "7. 完成"
CERT_END="$(openssl x509 -in "${LIVE_DIR}/fullchain.pem" -noout -enddate 2>/dev/null | cut -d= -f2 || true)"
cat <<EOF

${C_GRN}${C_BLD}════════ HTTPS 配置完成 ════════${C_RST}

  访问地址   ${C_BLD}https://${PUBLIC_IP}/${C_RST}chat
  证书路径   ${LIVE_DIR}/fullchain.pem
  到期时间   ${CERT_END}

${C_YEL}【必须记住】IP 证书只有 160 小时（约 6.7 天）有效期${C_RST}
  靠 snap.certbot.renew.timer 自动续期 + renew_hook 自动 reload nginx。
  **关掉 80 端口 = 续期失败 = 6.7 天后全站报错。**

  三条必查命令：
    openssl x509 -in ${LIVE_DIR}/fullchain.pem -noout -dates
    certbot renew --dry-run
    tail -20 /var/log/letsencrypt/reload-nginx.log

EOF
