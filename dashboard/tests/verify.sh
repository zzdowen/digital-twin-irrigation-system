#!/usr/bin/env bash
# =============================================================================
# FARM 灌溉监控看板 · 端到端验收脚本（严格按 CONTRACT.md §9）
#
# 用法：
#   bash tests/verify.sh                                   # 打 http://127.0.0.1:8000
#   bash tests/verify.sh --user admin --pass 'Xx123456'     # 指定账号密码
#   bash tests/verify.sh --admin-password 'Xx123456'        # 同上（别名）
#   bash tests/verify.sh --url http://203.0.113.10:8000     # 打公网地址
#   bash tests/verify.sh --user admin --pass '...' --viewer-password '...'
#   bash tests/verify.sh --verbose                          # 打印每步原始响应
#
# 参数：
#   --url URL               服务地址，默认 http://127.0.0.1:8000
#   --user NAME             登录用户名，默认 admin
#   --pass PWD              登录密码（--admin-password 是等价别名）
#   --viewer-password PWD   viewer 账号密码，第 11 条越权检查用；
#                           不给则自动尝试创建/复用一个临时 viewer 账号
#   -v, --verbose           打印原始响应体
#   -h, --help              显示本帮助
#
# 退出码：
#   0  ← 11 条全部 PASS（这是唯一算「部署成功」的情况）
#   1  ← 任意一条 FAIL
#   2  ← 服务连不上 / 本机缺 curl（环境问题，不是代码问题）
#
# 前提：
#   * 需要 curl。JSON 解析优先用 jq，没有 jq 时退回 python3。
#   * 第 11 条需要一个 viewer 账号。若 admin 密码可用，脚本会自己建一个
#     （用户名形如 vfy_viewer_<时间戳>），建完**不删除**——改动数据库比留下测试号更危险。
#     想自己指定就用 --viewer-password 配 --viewer-user。
#
# 关于第 6 条在「首次登录未改密」时会失败：
#   契约 §3 规定 must_change=1 时除 /api/me、/api/password 外全部 403。
#   所以如果 admin 还是初始密码，第 6 条起会 403 —— 那是**契约生效**，不是 bug。
#   正确做法：先用看板改一次密码，再跑本脚本。
# =============================================================================
set -uo pipefail    # 注意：不用 -e。验收要一条条跑完看结果，不能第一条失败就退。

# ─────────────────────────── 参数 ───────────────────────────
BASE_URL="${FARM_URL:-http://127.0.0.1:8000}"
LOGIN_USER="${FARM_USER:-admin}"
LOGIN_PASS="${FARM_PASS:-${FARM_ADMIN_PASSWORD:-}}"
VIEWER_USER="${FARM_VIEWER_USER:-}"
VIEWER_PASS="${FARM_VIEWER_PASS:-}"
VERBOSE=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
  C_CYN=$'\033[36m'; C_BLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
  C_RED=""; C_GRN=""; C_YEL=""; C_CYN=""; C_BLD=""; C_DIM=""; C_RST=""
fi

usage() { sed -n '2,38p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)              BASE_URL="${2:?--url 需要地址}"; shift 2 ;;
    --user)             LOGIN_USER="${2:?--user 需要用户名}"; shift 2 ;;
    --pass|--password)  LOGIN_PASS="${2:?--pass 需要密码}"; shift 2 ;;
    --admin-password)   LOGIN_PASS="${2:?--admin-password 需要密码}"; shift 2 ;;
    --viewer-user)      VIEWER_USER="${2:?--viewer-user 需要用户名}"; shift 2 ;;
    --viewer-password)  VIEWER_PASS="${2:?--viewer-password 需要密码}"; shift 2 ;;
    --verbose|-v)       VERBOSE=1; shift ;;
    -h|--help)          usage; exit 0 ;;
    *)                  echo "未知参数：$1（用 --help 查看用法）" >&2; exit 2 ;;
  esac
done
BASE_URL="${BASE_URL%/}"

# ─────────────────────────── 计数与输出 ───────────────────────────
TOTAL=0; PASSED=0; FAILED=0
declare -a RESULTS=()

WORK="$(mktemp -d "${TMPDIR:-/tmp}/farm-verify.XXXXXX" 2>/dev/null)" \
  || { echo "无法创建临时目录" >&2; exit 2; }
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

info() { printf '%s[INFO]%s %s\n' "$C_CYN" "$C_RST" "$*"; }
dim()  { printf '%s       %s%s\n' "$C_DIM" "$*" "$C_RST"; }
passm(){ printf '%s[PASS]%s %s\n' "$C_GRN" "$C_RST" "$*"; }
failm(){ printf '%s[FAIL]%s %s\n' "$C_RED" "$C_RST" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_YEL" "$C_RST" "$*" >&2; }

# record PASS|FAIL 编号 标题 实测细节
record() {
  local st="$1" no="$2" title="$3" detail="${4:-}"
  TOTAL=$((TOTAL + 1))
  if [[ "$st" == "PASS" ]]; then
    PASSED=$((PASSED + 1)); passm "#${no} ${title}"
  else
    FAILED=$((FAILED + 1)); failm "#${no} ${title}"
  fi
  [[ -n "$detail" ]] && dim "$detail"
  RESULTS+=("${st}|${no}|${title}")
  return 0
}

# ─────────────────────────── JSON 取值（jq 优先，python3 兜底） ───────────────────────────
HAVE_JQ=0
command -v jq >/dev/null 2>&1 && HAVE_JQ=1

PY_BIN=""
for cand in "${PROJECT_DIR}/venv/bin/python" "/opt/farm/venv/bin/python" \
            "${PROJECT_DIR}/../venv/bin/python"; do
  [[ -x "$cand" ]] && { PY_BIN="$cand"; break; }
done
[[ -z "$PY_BIN" ]] && command -v python3 >/dev/null 2>&1 && PY_BIN="$(command -v python3)"
[[ -z "$PY_BIN" ]] && command -v python >/dev/null 2>&1 && PY_BIN="$(command -v python)"

cat > "${WORK}/jsonq.py" <<'PYEOF'
import json, sys

def walk(node, parts):
    for p in parts:
        if p == "":
            continue
        if p == "len":
            return len(node) if isinstance(node, (list, dict, str)) else 0
        if isinstance(node, list):
            node = node[int(p)]
            continue
        if isinstance(node, dict):
            node = node[p]
            continue
        raise KeyError(p)
    return node

def main():
    with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
        node = json.load(f)
    parts = [p for p in sys.argv[2].split(".") if p != ""]
    try:
        v = walk(node, parts)
    except (KeyError, IndexError, TypeError, ValueError):
        sys.exit(3)
    if v is None:
        sys.exit(3)
    if isinstance(v, bool):
        print("true" if v else "false")
    elif isinstance(v, (dict, list)):
        print(json.dumps(v, ensure_ascii=False))
    else:
        print(v)

main()
PYEOF

jq_filter() {
  printf '%s' "$1" | awk -F'.' '{
    out = ""; n = 0
    for (i = 1; i <= NF; i++) {
      if ($i == "") continue
      if ($i ~ /^[0-9]+$/) out = out "[" $i "]"
      else out = out "." $i
      n++
    }
    if (n == 0) out = "."
    print out
  }'
}

# jsonval <file> <a.b.0.c>：取标量，缺失返回 1
jsonval() {
  local file="$1" path="$2" out
  [[ -s "$file" ]] || return 1
  if [[ "$HAVE_JQ" -eq 1 ]]; then
    out="$(jq -r "$(jq_filter "$path")" "$file" 2>/dev/null)" || return 1
    [[ "$out" == "null" ]] && return 1
    printf '%s' "$out"; return 0
  fi
  [[ -n "$PY_BIN" ]] || return 1
  out="$("$PY_BIN" "${WORK}/jsonq.py" "$file" "$path" 2>/dev/null)" || return 1
  printf '%s' "$out"
}

# jlen <file> <path>：数组/字典长度
jlen() {
  local file="$1" path="$2" out
  [[ -s "$file" ]] || { echo 0; return 0; }
  if [[ "$HAVE_JQ" -eq 1 ]]; then
    out="$(jq -r "($(jq_filter "$path")) | length" "$file" 2>/dev/null)" || out=""
    [[ "$out" =~ ^[0-9]+$ ]] && { echo "$out"; return 0; }
  fi
  if [[ -n "$PY_BIN" ]]; then
    out="$("$PY_BIN" "${WORK}/jsonq.py" "$file" "${path}.len" 2>/dev/null)" || out=""
    [[ "$out" =~ ^[0-9]+$ ]] && { echo "$out"; return 0; }
  fi
  echo 0
}

show_body() { [[ "$VERBOSE" -eq 1 ]] && { head -c "${2:-1200}" "$1" 2>/dev/null; echo; }; return 0; }

# ─────────────────────────── HTTP ───────────────────────────
HTTP_CODE=""
BODY="${WORK}/last.json"

http() {  # http <METHOD> <path> [curl 额外参数...]
  local method="$1" path="$2"; shift 2
  local code rc
  code="$(curl -sS -o "$BODY" -w '%{http_code}' --max-time 30 \
            -X "$method" "${BASE_URL}${path}" "$@" 2>"${WORK}/curl.err")"
  rc=$?
  HTTP_CODE="${code:-000}"
  if [[ $rc -ne 0 ]]; then
    printf '%s' "$(cat "${WORK}/curl.err" 2>/dev/null)" > "$BODY"
    return 1
  fi
  return 0
}

TOKEN=""
auth_http() { local m="$1" p="$2"; shift 2; http "$m" "$p" -H "Authorization: Bearer ${TOKEN}" "$@"; }

# ─────────────────────────── 0. 环境自检 ───────────────────────────
printf '\n%s%s══════ FARM 看板验收（CONTRACT §9 · 11 条）══════%s\n\n' "$C_BLD" "$C_CYN" "$C_RST"
info "目标地址：${BASE_URL}"
info "登录账号：${LOGIN_USER}$([[ -n "$LOGIN_PASS" ]] && echo "（已给密码）" || echo "（未给密码，第 4/5 条会提示）")"

command -v curl >/dev/null 2>&1 || { failm "缺少 curl，无法验收"; exit 2; }
info "curl     ：$(command -v curl)"
[[ "$HAVE_JQ" -eq 1 ]] && info "JSON 解析：jq ($(command -v jq))" \
                       || { [[ -n "$PY_BIN" ]] && info "JSON 解析：python3 ($PY_BIN)" || warn "既没 jq 也没 python3，JSON 校验会退化"; }

info "探测服务是否在线……"
if ! http GET /health; then
  failm "连不上 ${BASE_URL}/health：$(head -c 300 "$BODY" 2>/dev/null)"
  cat <<EOF

请依次检查：
  1) 服务起来了吗：   systemctl status farm.service
  2) 端口在听吗：     ss -ltnp | grep 8000
  3) 本机直连通吗：   curl -v ${BASE_URL}/health
  4) 外网访问不通：   云厂商控制台 → 防火墙 → 是否放行了 TCP 8000
  5) 看日志：         journalctl -u farm.service -n 80 --no-pager
EOF
  exit 2
fi
info "服务在线。"

printf '\n%s──── 逐条验收 ────%s\n\n' "$C_BLD" "$C_RST"

# =============================================================================
# 1) GET /health → 200 且 status=ok
# =============================================================================
http GET /health
H_STA="$(jsonval "$BODY" status || true)"
H_UP="$(jsonval "$BODY" upstream_ok || true)"
if [[ "$HTTP_CODE" == "200" && "$H_STA" == "ok" ]]; then
  record PASS 1 "GET /health → 200 且 status=ok" "HTTP 200 | status=ok${H_UP:+ | upstream_ok=${H_UP}}"
else
  record FAIL 1 "GET /health → 200 且 status=ok" \
    "HTTP ${HTTP_CODE}（期望 200）| status=${H_STA:-?} | body=$(head -c 200 "$BODY" 2>/dev/null)"
  show_body "$BODY"
fi

# =============================================================================
# 2) GET /chat → 200 且响应含 <html
# =============================================================================
http GET /chat
CHAT_HEAD="$(head -c 400 "$BODY" 2>/dev/null | tr '[:upper:]' '[:lower:]')"
if [[ "$HTTP_CODE" == "200" ]] && [[ "$CHAT_HEAD" == *"<html"* || "$CHAT_HEAD" == *"<!doctype"* ]]; then
  record PASS 2 "GET /chat → 200 且返回 HTML 页面" "HTTP 200 | 首 40 字节：$(head -c 40 "$BODY" 2>/dev/null | tr -d '\n\r')"
else
  record FAIL 2 "GET /chat → 200 且返回 HTML 页面" \
    "HTTP ${HTTP_CODE}（期望 200）| 首 40 字节：$(head -c 40 "$BODY" 2>/dev/null | tr -d '\n\r')"
  show_body "$BODY"
fi

# =============================================================================
# 3) 未登录 GET /api/overview → 401 且 message=未登录或登录已过期
# =============================================================================
http GET /api/overview
NA_MSG="$(jsonval "$BODY" message || true)"
NA_STA="$(jsonval "$BODY" status || true)"
if [[ "$HTTP_CODE" == "401" && "$NA_MSG" == "未登录或登录已过期" && "$NA_STA" == "error" ]]; then
  record PASS 3 "未登录 GET /api/overview → 401 且提示正确" "HTTP 401 | message=${NA_MSG}"
else
  record FAIL 3 "未登录 GET /api/overview → 401 且提示正确" \
    "HTTP ${HTTP_CODE}（期望 401）| status=${NA_STA:-?} | message=${NA_MSG:-（缺）}"
  show_body "$BODY"
fi

# =============================================================================
# 4) POST /api/login 错密码 → status=error
# =============================================================================
BAD_PASS="wrong_$(date +%s)_nope"
http POST /api/login -H 'Content-Type: application/json' \
  -d "$(printf '{"username":"%s","password":"%s"}' "$LOGIN_USER" "$BAD_PASS")"
BAD_STA="$(jsonval "$BODY" status || true)"
BAD_MSG="$(jsonval "$BODY" message || true)"
BAD_TOKEN="$(jsonval "$BODY" token || true)"
# 期望：HTTP 200 或 401，status=error，且**没有** token
if [[ "$BAD_STA" == "error" && -z "$BAD_TOKEN" && "$HTTP_CODE" != "000" ]]; then
  record PASS 4 "POST /api/login 错误密码 → error 且不发 token" \
    "HTTP ${HTTP_CODE} | status=error | message=${BAD_MSG:-（无）}"
else
  record FAIL 4 "POST /api/login 错误密码 → error 且不发 token" \
    "HTTP ${HTTP_CODE} | status=${BAD_STA:-?} | got_token=${BAD_TOKEN:+YES} | message=${BAD_MSG:-（无）}"
  show_body "$BODY"
fi

# =============================================================================
# 5) POST /api/login 正确密码 → 拿到 token
# =============================================================================
TOKEN=""
ROLE=""
MUST_CHANGE=""
if [[ -z "$LOGIN_PASS" ]]; then
  record FAIL 5 "POST /api/login 正确密码 → 拿到 token" \
    "未提供密码。用 --user admin --pass '你的密码' 重跑（或设环境变量 FARM_PASS）"
else
  http POST /api/login -H 'Content-Type: application/json' \
    -d "$(printf '{"username":"%s","password":"%s"}' "$LOGIN_USER" "$LOGIN_PASS")"
  TOKEN="$(jsonval "$BODY" token || true)"
  ROLE="$(jsonval "$BODY" role || true)"
  MUST_CHANGE="$(jsonval "$BODY" must_change || true)"
  OK_STA="$(jsonval "$BODY" status || true)"
  OK_MSG="$(jsonval "$BODY" message || true)"
  if [[ "$HTTP_CODE" == "200" && "$OK_STA" == "ok" && -n "$TOKEN" ]]; then
    record PASS 5 "POST /api/login 正确密码 → 拿到 token" \
      "HTTP 200 | role=${ROLE:-?} | must_change=${MUST_CHANGE:-false} | token=${TOKEN:0:8}…"
  else
    TOKEN=""
    record FAIL 5 "POST /api/login 正确密码 → 拿到 token" \
      "HTTP ${HTTP_CODE} | status=${OK_STA:-?} | message=${OK_MSG:-（无）}"
    show_body "$BODY"
  fi
fi

if [[ -n "$MUST_CHANGE" && "$MUST_CHANGE" == "true" ]]; then
  warn "该账号 must_change=true（还是初始密码）。契约 §3：改密前除 /api/me、/api/password 外全部 403，"
  warn "所以第 6~11 条会失败。先去浏览器改一次密码，再用新密码重跑本脚本。"
fi

# =============================================================================
# 6) GET /api/overview 带 token → 200，channels 长度 3，含 status/data_age_sec
# =============================================================================
if [[ -z "$TOKEN" ]]; then
  record FAIL 6 "GET /api/overview（带 token）→ 字段齐全且 3 个通道" "没有 token（依赖第 5 条）"
else
  auth_http GET /api/overview
  OV_STA="$(jsonval "$BODY" status || true)"
  NCH="$(jlen "$BODY" channels)"
  AGE="$(jsonval "$BODY" data_age_sec || true)"
  UPOK="$(jsonval "$BODY" upstream_ok || true)"
  AVG="$(jsonval "$BODY" soil_moisture_avg || true)"
  # channels[0] 里必须有 channel / soil_moisture / pump_status 三个键
  CH0="$(jsonval "$BODY" channels.0.channel || true)"
  CH0M="$(jsonval "$BODY" channels.0.soil_moisture || true)"
  CH0P="$(jsonval "$BODY" channels.0.pump_status || true)"
  if [[ "$HTTP_CODE" == "200" && "$OV_STA" == "ok" && "$NCH" -eq 3 && -n "$AGE" \
        && -n "$CH0" && -n "$CH0P" ]]; then
    record PASS 6 "GET /api/overview（带 token）→ 字段齐全且 3 个通道" \
      "HTTP 200 | channels=${NCH} | data_age_sec=${AGE}s | upstream_ok=${UPOK:-?} | avg=${AVG:-?} | 首通道 moisture=${CH0M:-?}"
  else
    record FAIL 6 "GET /api/overview（带 token）→ 字段齐全且 3 个通道" \
      "HTTP ${HTTP_CODE} | status=${OV_STA:-?} | channels=${NCH}（期望 3）| data_age_sec=${AGE:-缺} | ch0.channel=${CH0:-缺} | ch0.pump_status=${CH0P:-缺}"
    show_body "$BODY" 800
  fi
fi

# =============================================================================
# 7) GET /api/history?hours=24&points=100 → series 长度 3，每通道 [ts,val]
# =============================================================================
if [[ -z "$TOKEN" ]]; then
  record FAIL 7 "GET /api/history?hours=24&points=100 → 3 通道时间序列" "没有 token（依赖第 5 条）"
else
  auth_http GET "/api/history?hours=24&points=100"
  HI_STA="$(jsonval "$BODY" status || true)"
  NSER="$(jlen "$BODY" series)"
  NPTS0="$(jlen "$BODY" series.0.moisture)"
  TS0="$(jsonval "$BODY" series.0.moisture.0.0 || true)"
  VL0="$(jsonval "$BODY" series.0.moisture.0.1 || true)"
  LAB0="$(jsonval "$BODY" series.0.label || true)"
  # 时间戳必须是整数（Unix 秒），长度 10 位左右；早期版本误用 ISO 串会在这里暴露
  TS_OK=0
  [[ "$TS0" =~ ^[0-9]{9,11}$ ]] && TS_OK=1
  if [[ "$HTTP_CODE" == "200" && "$HI_STA" == "ok" && "$NSER" -eq 3 \
        && "${NPTS0:-0}" -gt 50 && "$TS_OK" -eq 1 && -n "$VL0" ]]; then
    record PASS 7 "GET /api/history?hours=24&points=100 → 3 通道时间序列" \
      "HTTP 200 | series=${NSER} | 首通道点数=${NPTS0} | 首点=[${TS0}, ${VL0}] | label=${LAB0:-?}"
  else
    record FAIL 7 "GET /api/history?hours=24&points=100 → 3 通道时间序列" \
      "HTTP ${HTTP_CODE} | status=${HI_STA:-?} | series=${NSER}（期望 3）| 首通道点数=${NPTS0:-0}（期望 >50）| 首点ts=${TS0:-缺}（需为 Unix 秒整数）"
    show_body "$BODY" 800
  fi
fi

# =============================================================================
# 8) GET /api/events?days=30 → 200 且 events 是数组
# =============================================================================
if [[ -z "$TOKEN" ]]; then
  record FAIL 8 "GET /api/events?days=30 → 200 且 events 是数组" "没有 token（依赖第 5 条）"
else
  auth_http GET "/api/events?days=30"
  EV_STA="$(jsonval "$BODY" status || true)"
  EV_TXT="$(jsonval "$BODY" events || true)"
  # events 必须存在且是 JSON 数组（空数组 [] 也算通过——上游最近一条事件是 2026-09-06）
  EV_OK=0
  [[ "$EV_TXT" == \[* ]] && EV_OK=1
  NEV="$(jlen "$BODY" events)"
  if [[ "$HTTP_CODE" == "200" && "$EV_STA" == "ok" && "$EV_OK" -eq 1 ]]; then
    record PASS 8 "GET /api/events?days=30 → 200 且 events 是数组" \
      "HTTP 200 | events=${NEV} 条 | 类型=array"
  else
    record FAIL 8 "GET /api/events?days=30 → 200 且 events 是数组" \
      "HTTP ${HTTP_CODE} | status=${EV_STA:-?} | events 类型=$(printf '%s' "${EV_TXT:-缺}" | head -c 1)（期望 [）"
    show_body "$BODY" 800
  fi
fi

# =============================================================================
# 9) GET /api/alerts?limit=10 → 200
# =============================================================================
if [[ -z "$TOKEN" ]]; then
  record FAIL 9 "GET /api/alerts?limit=10 → 200" "没有 token（依赖第 5 条）"
else
  auth_http GET "/api/alerts?limit=10"
  AL_STA="$(jsonval "$BODY" status || true)"
  AL_TXT="$(jsonval "$BODY" alerts || true)"
  NA_AL="$(jlen "$BODY" alerts)"
  AL_OK=0
  [[ "$AL_TXT" == \[* ]] && AL_OK=1
  if [[ "$HTTP_CODE" == "200" && "$AL_STA" == "ok" && "$AL_OK" -eq 1 ]]; then
    record PASS 9 "GET /api/alerts?limit=10 → 200" "HTTP 200 | alerts=${NA_AL} 条（空数组也算通过）"
  else
    record FAIL 9 "GET /api/alerts?limit=10 → 200" \
      "HTTP ${HTTP_CODE} | status=${AL_STA:-?} | alerts 类型=$(printf '%s' "${AL_TXT:-缺}" | head -c 1)（期望 [）"
    show_body "$BODY" 600
  fi
fi

# =============================================================================
# 10) GET /api/export.csv → 200 且首 3 字节 = EF BB BF（UTF-8 BOM）
# =============================================================================
if [[ -z "$TOKEN" ]]; then
  record FAIL 10 "GET /api/export.csv → 200 且带 UTF-8 BOM" "没有 token（依赖第 5 条）"
else
  CSV_FILE="${WORK}/export.csv"
  CSV_CODE="$(curl -sS -o "$CSV_FILE" -w '%{http_code}' --max-time 60 \
      -H "Authorization: Bearer ${TOKEN}" "${BASE_URL}/api/export.csv?hours=24" 2>/dev/null || echo 000)"
  CSV_SIZE="$(wc -c < "$CSV_FILE" 2>/dev/null | tr -d ' ')"
  # 用 od 读前 3 字节；没 od 时退回 python
  MAGIC="$(od -An -tx1 -N3 "$CSV_FILE" 2>/dev/null | tr -d ' \n')"
  if [[ -z "$MAGIC" && -n "$PY_BIN" ]]; then
    MAGIC="$("$PY_BIN" -c 'import sys;print(open(sys.argv[1],"rb").read(3).hex())' "$CSV_FILE" 2>/dev/null || true)"
  fi
  MAGIC="$(printf '%s' "$MAGIC" | tr '[:lower:]' '[:upper:]')"
  # Content-Disposition 也要是 attachment（契约 §4）
  CDISP="$(curl -sS -o /dev/null -D - --max-time 20 -H "Authorization: Bearer ${TOKEN}" \
      "${BASE_URL}/api/export.csv?hours=24" 2>/dev/null \
      | grep -i '^content-disposition' | tr -d '\r' || true)"
  CD_OK=0
  [[ "$CDISP" == *attachment* ]] && CD_OK=1
  if [[ "$CSV_CODE" == "200" && "$MAGIC" == "EFBBBF" && "${CSV_SIZE:-0}" -gt 20 ]]; then
    record PASS 10 "GET /api/export.csv → 200 且带 UTF-8 BOM" \
      "HTTP 200 | ${CSV_SIZE} 字节 | 首 3 字节=${MAGIC} | ${CDISP:-（无 Content-Disposition）}"
  else
    record FAIL 10 "GET /api/export.csv → 200 且带 UTF-8 BOM" \
      "HTTP ${CSV_CODE} | ${CSV_SIZE:-0} 字节 | 首 3 字节=${MAGIC:-空}（期望 EFBBBF）| attachment=${CD_OK}"
    [[ "${CSV_SIZE:-0}" -gt 0 && "${CSV_SIZE:-0}" -le 2000 ]] && head -c 400 "$CSV_FILE" 2>/dev/null && echo
  fi
fi

# =============================================================================
# 11) viewer 角色 POST /api/control → 403
# =============================================================================
# 需要一个 viewer 账号。优先用 --viewer-password；否则用 admin 现建一个。
VTOKEN=""
VUSER="${VIEWER_USER:-vfy_view_$(date +%s | tail -c 6)}"
VPASS="${VIEWER_PASS:-Verify#$(date +%s)${RANDOM}}"

V11_DONE=0    # 第 11 条是否已记录结果（避免重复计数）

# 步骤 1：确定 viewer 凭据
if [[ -z "$TOKEN" ]]; then
  record FAIL 11 "viewer POST /api/control → 403" "没有 admin token（依赖第 5 条），无法准备 viewer 账号"
  V11_DONE=1
elif [[ -n "$VIEWER_PASS" ]]; then
  # 用户自己给了 viewer 账号，直接用
  [[ -z "$VIEWER_USER" ]] && VUSER="$LOGIN_USER"   # 没给用户名就用登录用户（试探它的角色）
  info "使用指定的 viewer 账号：${VUSER}"
else
  # 用 admin 权限建一个临时 viewer
  info "创建临时 viewer 账号：${VUSER}（验证越权拦截用，建完不删）"
  http POST /api/users -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
    -d "$(printf '{"username":"%s","password":"%s","role":"viewer"}' "$VUSER" "$VPASS")"
  MK_STA="$(jsonval "$BODY" status || true)"
  MK_MSG="$(jsonval "$BODY" message || true)"
  if [[ "$MK_STA" != "ok" ]]; then
    record FAIL 11 "viewer POST /api/control → 403" \
      "无法创建 viewer 账号：HTTP ${HTTP_CODE} status=${MK_STA:-?} message=${MK_MSG:-（无）}"
    V11_DONE=1
  fi
fi

# 步骤 2：viewer 登录拿 token
if [[ "$V11_DONE" -eq 0 ]]; then
  http POST /api/login -H 'Content-Type: application/json' \
    -d "$(printf '{"username":"%s","password":"%s"}' "$VUSER" "$VPASS")"
  VLOGIN_STA="$(jsonval "$BODY" status || true)"
  VTOKEN="$(jsonval "$BODY" token || true)"
  VROLE="$(jsonval "$BODY" role || true)"
  if [[ -z "$VTOKEN" ]]; then
    record FAIL 11 "viewer POST /api/control → 403" \
      "viewer 登录失败：HTTP ${HTTP_CODE} status=${VLOGIN_STA:-?}（用户 ${VUSER}）"
    V11_DONE=1
  fi
fi

# 步骤 3：viewer 尝试下发控制，必须是 403
if [[ "$V11_DONE" -eq 0 ]]; then
  http POST /api/control -H "Authorization: Bearer ${VTOKEN}" -H 'Content-Type: application/json' \
    -d '{"action":"start","channel":1,"duration_minutes":1}'
  C_STA="$(jsonval "$BODY" status || true)"
  C_MSG="$(jsonval "$BODY" message || true)"
  if [[ "$HTTP_CODE" == "403" && "$C_STA" == "error" && "$C_MSG" == "当前账号没有该操作权限" ]]; then
    record PASS 11 "viewer POST /api/control → 403" \
      "HTTP 403 | 登录身份 role=${VROLE:-?} | message=${C_MSG}"
  else
    record FAIL 11 "viewer POST /api/control → 403" \
      "HTTP ${HTTP_CODE}（期望 403）| role=${VROLE:-?} | status=${C_STA:-?} | message=${C_MSG:-（无）}"
    show_body "$BODY" 500
  fi
fi

# =============================================================================
# 汇总
# =============================================================================
printf '\n%s──── 验收汇总 ────%s\n\n' "$C_BLD" "$C_RST"
for row in "${RESULTS[@]}"; do
  IFS='|' read -r st no title <<< "$row"
  if [[ "$st" == "PASS" ]]; then
    printf '  %s✓%s #%s  %s\n' "$C_GRN" "$C_RST" "$no" "$title"
  else
    printf '  %s✗%s #%s  %s\n' "$C_RED" "$C_RST" "$no" "$title"
  fi
done
printf '\n  共 %s 条：%s%s 通过%s / %s%s 失败%s\n\n' \
  "$TOTAL" "$C_GRN" "$PASSED" "$C_RST" "$C_RED" "$FAILED" "$C_RST"

if [[ "$FAILED" -eq 0 && "$TOTAL" -eq 11 ]]; then
  printf '%s%s════════ 11 条全部通过 · 部署验收成功 ════════%s\n\n' "$C_BLD" "$C_GRN" "$C_RST"
  exit 0
fi

if [[ "$TOTAL" -ne 11 ]]; then
  printf '%s%s════════ 验收不完整：只跑了 %s 条，应为 11 条 ════════%s\n\n' \
    "$C_BLD" "$C_YEL" "$TOTAL" "$C_RST"
fi

cat <<EOF
排障建议（按失败编号对号入座）：
  #1 /health 失败      → systemctl status farm.service；journalctl -u farm.service -n 80
  #2 /chat 不是 HTML   → web/index.html 没部署上去，检查 /opt/farm/web/index.html 是否存在
  #3 未登录不是 401    → 后端鉴权依赖没生效，看 app/main.py 的中间件
  #4 错密码也不报错    → 登录逻辑被短路了，重点看 app/auth.py 的 login()
  #5 密码不对          → 用 --pass 传当前密码；若忘密码见 docs/DEPLOY.md「重置管理员密码」
  #6 overview 缺字段   → 上游不可达时会退化（upstream_ok=false）。先 curl 127.0.0.1:5000/api/realtime-data
  #7 history 点数不足  → 上游 history 表 5 分钟一条，24 小时只有约 288 点；points=100 应能降采样出来
  #8 /9 事件或告警    → 空数组是正常的（上游最近事件在 2026-09-06）；FAIL 说明字段名不对
  #10 CSV 没 BOM       → 导出必须写 utf-8-sig；看 app/main.py 的 export.csv 实现
  #11 viewer 能控制    → 越权校验漏了，这是安全问题，必须修 app/main.py
EOF
printf '\n'
exit 1
