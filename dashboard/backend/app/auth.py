"""认证与授权：PBKDF2 密码、token、角色校验、登录锁定。"""
import hashlib
import hmac
import secrets
import string
import time

from . import config, db

# ── 密码 ──


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """返回 (pwd_hash_hex, salt_hex)。PBKDF2-HMAC-SHA256，260000 轮。"""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), config.PBKDF2_ROUNDS)
    return dk.hex(), salt


def verify_password(password: str, pwd_hash: str, salt: str) -> bool:
    try:
        calc, _ = hash_password(password, salt)
    except Exception:
        return False
    return hmac.compare_digest(calc, pwd_hash)


def gen_password(n: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def valid_username(name: str) -> bool:
    if not name or not (3 <= len(name) <= 20):
        return False
    return all(c.isalnum() or c == "_" for c in name) and name.isascii()


# ── 用户 ──


def get_user(username: str):
    return db.query_one("SELECT * FROM users WHERE username = ?", (username,))


def list_users() -> list[dict]:
    rows = db.query(
        "SELECT username, role, created, disabled, must_change FROM users ORDER BY "
        "CASE role WHEN 'admin' THEN 1 WHEN 'operator' THEN 2 ELSE 3 END, username")
    return [dict(r) for r in rows]


def create_user(username: str, password: str, role: str = "viewer",
                must_change: int = 0) -> dict:
    """建号。返回 {status, message}；重名/非法返回 error。"""
    username = (username or "").strip()
    if not valid_username(username):
        return {"status": "error", "message": "用户名需为 3-20 位字母、数字或下划线。"}
    if not password or len(password) < 8:
        return {"status": "error", "message": "密码至少 8 位。"}
    if role not in config.ROLES:
        return {"status": "error", "message": f"角色只能是 {'/'.join(config.ROLES)}。"}
    if get_user(username) is not None:
        return {"status": "error", "message": "该用户名已被占用。"}
    h, salt = hash_password(password)
    db.write("INSERT INTO users(username, pwd_hash, salt, role, created, disabled, must_change)"
             " VALUES(?,?,?,?,?,0,?)",
             (username, h, salt, role, db.now_iso(), 1 if must_change else 0))
    return {"status": "ok", "message": f"已创建账号 {username}（角色 {role}）。", "username": username}


def set_password(username: str, password: str, must_change: int = 0) -> dict:
    if not password or len(password) < 8:
        return {"status": "error", "message": "密码至少 8 位。"}
    if get_user(username) is None:
        return {"status": "error", "message": "账号不存在。"}
    h, salt = hash_password(password)
    db.write("UPDATE users SET pwd_hash=?, salt=?, must_change=? WHERE username=?",
             (h, salt, 1 if must_change else 0, username))
    # 改密后吊销该用户全部 token
    revoke_all(username)
    return {"status": "ok", "message": "密码已更新。"}


def set_disabled(username: str, disabled: bool) -> dict:
    if get_user(username) is None:
        return {"status": "error", "message": "账号不存在。"}
    db.write("UPDATE users SET disabled=? WHERE username=?", (1 if disabled else 0, username))
    if disabled:
        revoke_all(username)
    return {"status": "ok", "message": "已停用该账号。" if disabled else "已启用该账号。"}


def set_role(username: str, role: str) -> dict:
    if role not in config.ROLES:
        return {"status": "error", "message": f"角色只能是 {'/'.join(config.ROLES)}。"}
    if get_user(username) is None:
        return {"status": "error", "message": "账号不存在。"}
    if role != config.ADMIN_ROLE and count_admins() <= 1 and is_admin(username):
        return {"status": "error", "message": "至少要保留一个管理员账号。"}
    db.write("UPDATE users SET role=? WHERE username=?", (role, username))
    return {"status": "ok", "message": f"已把 {username} 的角色改为 {role}。"}


def is_admin(username: str) -> bool:
    u = get_user(username)
    return bool(u and u["role"] == config.ADMIN_ROLE)


def count_admins() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND disabled=0")
    return int(row["n"]) if row else 0


# ── token ──


def _token_id(token: str) -> str:
    """token 在库里的存法：SHA-256 十六进制，库里不存原始串。

    这样即使 farm.db 被整库下载，也拿不到能直接用的会话凭证。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = db.now_ts()
    db.write("INSERT INTO tokens(token, username, created, expires) VALUES(?,?,?,?)",
             (_token_id(token), username, now, now + config.TOKEN_TTL))
    return token


def user_by_token(token: str):
    """返回 (user_row, None) 或 (None, 错误消息)。"""
    if not token:
        return None, "未登录或登录已过期"
    tid = _token_id(token)
    row = db.query_one("SELECT * FROM tokens WHERE token = ?", (tid,))
    if row is None:
        return None, "未登录或登录已过期"
    if int(row["expires"]) < db.now_ts():
        db.write("DELETE FROM tokens WHERE token = ?", (tid,))
        return None, "未登录或登录已过期"
    u = get_user(row["username"])
    if u is None:
        return None, "未登录或登录已过期"
    if int(u["disabled"]):
        return None, "账号已停用"
    return u, None


def revoke(token: str) -> None:
    db.write("DELETE FROM tokens WHERE token = ?", (_token_id(token),))


def revoke_all(username: str) -> None:
    db.write("DELETE FROM tokens WHERE username = ?", (username,))


def purge_expired() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM tokens WHERE expires < ?", (db.now_ts(),))
    n = int(row["n"]) if row else 0
    if n:
        db.write("DELETE FROM tokens WHERE expires < ?", (db.now_ts(),))
    return n


# ── 登录失败锁定 ──


def login_locked(username: str) -> int:
    """返回剩余锁定秒数，0 表示未锁定。"""
    row = db.query_one("SELECT count, locked_ts FROM login_fails WHERE username=?", (username,))
    if row is None:
        return 0
    locked_ts = int(row["locked_ts"] or 0)
    if locked_ts <= 0:
        return 0
    left = locked_ts + config.LOGIN_LOCK_SEC - int(time.time())
    return max(0, left)


def note_fail(username: str) -> None:
    row = db.query_one("SELECT count FROM login_fails WHERE username=?", (username,))
    if row is None:
        db.write("INSERT INTO login_fails(username, count, locked_ts) VALUES(?,?,0)", (username, 1))
        return
    n = int(row["count"]) + 1
    locked = int(time.time()) if n >= config.LOGIN_MAX_FAIL else 0
    if locked:
        n = 0
    db.write("UPDATE login_fails SET count=?, locked_ts=? WHERE username=?", (n, locked, username))


def clear_fail(username: str) -> None:
    db.write("DELETE FROM login_fails WHERE username=?", (username,))


def login(username: str, password: str) -> dict:
    """完整登录流程。返回 {status, token, username, role, must_change, message}。"""
    username = (username or "").strip()
    left = login_locked(username)
    if left > 0:
        return {"status": "error",
                "message": f"登录失败次数过多，请 {left // 60 + 1} 分钟后再试。"}
    u = get_user(username)
    if u is None or not verify_password(password, u["pwd_hash"], u["salt"]):
        note_fail(username)
        return {"status": "error", "message": "用户名或密码不正确"}
    if int(u["disabled"]):
        return {"status": "error", "message": "账号已停用"}
    clear_fail(username)
    token = issue_token(username)
    return {"status": "ok", "token": token, "username": username,
            "role": u["role"], "must_change": bool(u["must_change"])}


def ensure_admin() -> dict:
    """首次启动建管理员。缺省密码随机并打印一次。"""
    if not config.ADMIN_PASSWORD:
        u = get_user(config.ADMIN_USER)
        if u is not None:
            return {"created": False, "username": config.ADMIN_USER}
        pwd = gen_password(14)
        config.ADMIN_PASSWORD = pwd
        create_user(config.ADMIN_USER, pwd, "admin", must_change=1)
        print("=" * 62, flush=True)
        print(f"[FARM] 首次启动，已创建管理员：{config.ADMIN_USER} / {pwd}", flush=True)
        print("[FARM] 该密码只显示这一次，首次登录会被要求立即修改。", flush=True)
        print("=" * 62, flush=True)
        return {"created": True, "username": config.ADMIN_USER, "password": pwd}
    out = create_user(config.ADMIN_USER, config.ADMIN_PASSWORD, "admin", must_change=0)
    if out.get("status") == "ok":
        return {"created": True, "username": config.ADMIN_USER, "password": None}
    # 已存在则不覆盖密码（避免重启把改过的密码冲掉）
    return {"created": False, "username": config.ADMIN_USER}


# ── 审计 ──


def audit(username: str, action: str, detail: str = "", ip: str = "", ok: bool = True) -> None:
    try:
        db.write("INSERT INTO audit(ts, username, action, detail, ip, ok) VALUES(?,?,?,?,?,?)",
                 (db.now_ts(), username, action, detail, ip, 1 if ok else 0))
    except Exception:  # noqa: BLE001
        pass
