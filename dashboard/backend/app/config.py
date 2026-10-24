"""集中配置。所有可调项都从环境变量读，默认值即生产值。"""
import os
from pathlib import Path

APP_NAME = "FARM"
APP_TITLE = "FARM · 智能灌溉监控看板"


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# ── 数据目录 ──
DATA_DIR = Path(_env("FARM_DATA", "/opt/farm/data"))
DB_PATH = Path(_env("FARM_DB", str(DATA_DIR / "farm.db")))
UPLOAD_DIR = DATA_DIR / "files"

# ── 上游灌溉服务（只读，绝不修改它） ──
UPSTREAM = _env("FARM_UPSTREAM", "http://127.0.0.1:5000").rstrip("/")
UPSTREAM_TIMEOUT = float(_env("FARM_UPSTREAM_TIMEOUT", "6"))
IRRDB_PATH = Path(_env("FARM_IRRDB", "/home/ubuntu/irrigation_history.db"))

# ── 采集 ──
SAMPLE_SEC = int(_env("FARM_SAMPLE_SEC", "60"))
STALE_AFTER_SEC = int(_env("FARM_STALE_SEC", "600"))

# ── 认证 ──
TOKEN_TTL = int(_env("FARM_TOKEN_TTL", str(7 * 24 * 3600)))
PBKDF2_ROUNDS = int(_env("FARM_PBKDF2_ROUNDS", "260000"))
LOGIN_MAX_FAIL = int(_env("FARM_LOGIN_MAX_FAIL", "5"))
LOGIN_LOCK_SEC = int(_env("FARM_LOGIN_LOCK_SEC", "600"))

# ── 角色 ──
ROLES = ("viewer", "operator", "admin")
WRITE_ROLES = ("operator", "admin")
ADMIN_ROLE = "admin"

# ── 业务阈值（告警用；同时用于状态判定：dry<DRY / wet>WET） ──
DRY_PCT = float(_env("FARM_DRY_PCT", "30"))
WET_PCT = float(_env("FARM_WET_PCT", "95"))
LONG_IRRIGATION_SEC = int(_env("FARM_LONG_IRR_SEC", str(60 * 60)))
WET_STREAK = int(_env("FARM_WET_STREAK", "3"))
UPSTREAM_DOWN_STREAK = int(_env("FARM_DOWN_STREAK", "3"))

# ── 灌溉计时器 ──
# 上游每 5 秒推一次读数；这里给 10 秒 = 漏掉 2 次心跳就认为「无法确认泵在转」，
# 立刻冻结计时（断网 / 模拟器挂掉后几秒内停表）。
TIMER_FRESH_SEC = int(_env("FARM_TIMER_FRESH_SEC", "10"))
# 两次新鲜观测的间隔上限：超过说明中间有一段没观测到，那段时间泵到底转没转
# 无法证实，整段不计入（宁可少算，不多算）。
TIMER_GAP_TRUST_SEC = int(_env("FARM_TIMER_GAP_SEC", "120"))
# 单次灌溉计时上限，防上游异常值把计时撑爆
TIMER_MAX_RUN_SEC = int(_env("FARM_TIMER_MAX_RUN_SEC", str(4 * 3600)))

# ── 通道呈现 ──
CHANNEL_LABELS = {1: "1 号地块", 2: "2 号地块", 3: "3 号地块"}
NUM_CHANNELS = 3

# ── 服务 ──
HOST = _env("FARM_HOST", "0.0.0.0")
PORT = int(_env("FARM_PORT", "8000"))

# ── 初始管理员（部署脚本注入，缺省随机） ──
ADMIN_USER = _env("FARM_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("FARM_ADMIN_PASSWORD") or ""

PUBLIC_PATHS = {"/health", "/", "/chat", "/api/login",
                # 只读展示端点：给公开 3D 展示页用，字段白名单，见 main.api_showcase
                "/api/showcase",
                # v2 三个公开只读端点（CONTRACT_V2 §2.2/2.3/2.4）。
                # 逐个精确列出，绝不写成 "/api/showcase/*" 通配 ——
                # 通配会把日后加到 /api/showcase/ 下的任何写接口一起公开出去。
                "/api/showcase/history",
                "/api/showcase/prediction",
                "/api/showcase/alerts"}
