"""auth.py: ユーザー認証・管理システム（SQLite + bcrypt + Fernet暗号化）"""

import json
import os
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import bcrypt

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")

# ─────────────────────────────────────────────
# DB初期化
# ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            phone TEXT DEFAULT '',
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            api_key TEXT DEFAULT '',
            is_verified INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            newsletter_consent INTEGER DEFAULT 0,
            verification_token TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS idea_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_url TEXT NOT NULL,
            product_title TEXT NOT NULL,
            ideas_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    # 期限切れトークンを掃除
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("DELETE FROM password_resets WHERE expires_at < ?", (now,))
    conn.commit()
    conn.close()


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────
# 管理設定（SMTP等）
# ─────────────────────────────────────────────
def get_setting(key: str, default: str = "") -> str:
    conn = _get_conn()
    row = conn.execute("SELECT value FROM admin_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO admin_settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# APIキー暗号化（Fernet）
# ─────────────────────────────────────────────
def _get_fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None

    key = os.getenv("FERNET_KEY", "")
    if not key:
        key = get_setting("fernet_key")
    if not key:
        key = Fernet.generate_key().decode()
        set_setting("fernet_key", key)
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_api_key(plaintext: str) -> str:
    if not plaintext or plaintext.startswith("enc:"):
        return plaintext
    f = _get_fernet()
    if f is None:
        return plaintext
    return "enc:" + f.encrypt(plaintext.encode()).decode()


def decrypt_api_key(stored: str) -> str:
    if not stored or not stored.startswith("enc:"):
        return stored
    f = _get_fernet()
    if f is None:
        return stored
    try:
        return f.decrypt(stored[4:].encode()).decode()
    except Exception:
        return ""


# ─────────────────────────────────────────────
# ユーザー管理
# ─────────────────────────────────────────────
def create_user(
    email: str,
    phone: str,
    name: str,
    password: str,
    newsletter_consent: bool = False,
) -> tuple:
    """ユーザー作成。(success: bool, token_or_error: str) を返す。"""
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    token = secrets.token_urlsafe(32)
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO users
               (email, phone, name, password_hash, is_verified, newsletter_consent,
                verification_token, created_at)
               VALUES (?, ?, ?, ?, 0, ?, ?, ?)""",
            (
                email.lower().strip(),
                phone.strip(),
                name.strip(),
                password_hash,
                1 if newsletter_consent else 0,
                token,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        conn.close()
        return True, token
    except sqlite3.IntegrityError:
        return False, "このメールアドレスは既に登録されています"
    except Exception as e:
        return False, str(e)


def verify_email_token(token: str) -> bool:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id FROM users WHERE verification_token=? AND is_verified=0",
        (token,),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE users SET is_verified=1, verification_token='' WHERE id=?",
            (row["id"],),
        )
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False


def authenticate(email: str, password: str) -> tuple:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE email=?", (email.lower().strip(),)
    ).fetchone()
    conn.close()
    if not row:
        return None, "メールアドレスまたはパスワードが違います"
    if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return None, "メールアドレスまたはパスワードが違います"
    if not row["is_verified"]:
        return None, "メール認証が完了していません。届いたメールのリンクをクリックしてください。"
    return dict(row), ""


def update_api_key(user_id: int, api_key: str):
    encrypted = encrypt_api_key(api_key)
    conn = _get_conn()
    conn.execute("UPDATE users SET api_key=? WHERE id=?", (encrypted, user_id))
    conn.commit()
    conn.close()


def get_user_api_key(user_id: int) -> str:
    conn = _get_conn()
    row = conn.execute("SELECT api_key FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return ""
    return decrypt_api_key(row["api_key"])


def get_all_users() -> list:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT id, email, phone, name, is_verified, is_admin,
                  newsletter_consent, created_at
           FROM users ORDER BY created_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_user_verified(user_id: int, verified: bool):
    conn = _get_conn()
    conn.execute("UPDATE users SET is_verified=? WHERE id=?", (1 if verified else 0, user_id))
    conn.commit()
    conn.close()


def delete_user(user_id: int):
    conn = _get_conn()
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM password_resets WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM idea_history WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# セッション管理
# ─────────────────────────────────────────────
def create_session(user_id: int, days: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires_at),
    )
    conn.commit()
    conn.close()
    return token


def validate_session(token: str) -> dict | None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    row = conn.execute(
        """SELECT u.* FROM users u
           JOIN sessions s ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > ?""",
        (token, now),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(token: str):
    conn = _get_conn()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# パスワードリセット
# ─────────────────────────────────────────────
def create_reset_token(email: str) -> tuple:
    """パスワードリセットトークン発行。(True, token) または (False, error)。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, name FROM users WHERE email=? AND is_verified=1",
        (email.lower().strip(),),
    ).fetchone()
    conn.close()
    if not row:
        # セキュリティ上、存在しないメールも同じ文言にする
        return False, "このメールアドレスは登録されていないか、未認証です"
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute(
        "DELETE FROM password_resets WHERE user_id=?", (row["id"],)
    )
    conn.execute(
        "INSERT INTO password_resets (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, row["id"], expires_at),
    )
    conn.commit()
    conn.close()
    return True, token


def validate_reset_token(token: str) -> dict | None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    row = conn.execute(
        """SELECT u.id, u.name, u.email FROM users u
           JOIN password_resets r ON u.id = r.user_id
           WHERE r.token = ? AND r.expires_at > ?""",
        (token, now),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def apply_reset_password(token: str, new_password: str) -> bool:
    user = validate_reset_token(token)
    if not user:
        return False
    password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    conn = _get_conn()
    conn.execute(
        "UPDATE users SET password_hash=? WHERE id=?", (password_hash, user["id"])
    )
    conn.execute("DELETE FROM password_resets WHERE token=?", (token,))
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    conn.commit()
    conn.close()
    return True


# ─────────────────────────────────────────────
# アイデア履歴
# ─────────────────────────────────────────────
def save_idea_history(user_id: int, product_url: str, product_title: str, ideas: list):
    conn = _get_conn()
    conn.execute(
        """INSERT INTO idea_history (user_id, product_url, product_title, ideas_json, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, product_url, product_title, json.dumps(ideas, ensure_ascii=False),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def get_idea_history(user_id: int, limit: int = 30) -> list:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT id, product_url, product_title, ideas_json, created_at
           FROM idea_history WHERE user_id=?
           ORDER BY created_at DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["ideas"] = json.loads(d["ideas_json"])
        except Exception:
            d["ideas"] = []
        result.append(d)
    return result


def delete_history_item(history_id: int, user_id: int):
    conn = _get_conn()
    conn.execute(
        "DELETE FROM idea_history WHERE id=? AND user_id=?", (history_id, user_id)
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# ドラフト状態（ページリフレッシュ復元用）
# ─────────────────────────────────────────────
def _ensure_draft_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_states (
            user_id INTEGER PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)


def save_draft_state(user_id: int, state: dict) -> None:
    conn = _get_conn()
    _ensure_draft_table(conn)
    conn.execute(
        """INSERT INTO draft_states (user_id, state_json, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(user_id) DO UPDATE SET
               state_json = excluded.state_json,
               updated_at = excluded.updated_at""",
        (user_id, json.dumps(state, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def get_draft_state(user_id: int) -> dict | None:
    conn = _get_conn()
    _ensure_draft_table(conn)
    row = conn.execute(
        "SELECT state_json FROM draft_states WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return json.loads(row["state_json"])
    except Exception:
        return None


# ─────────────────────────────────────────────
# メール送信
# ─────────────────────────────────────────────
def _get_smtp_config() -> dict:
    """env → DBの優先順でSMTP設定を取得する。"""
    host = os.getenv("SMTP_HOST") or get_setting("smtp_host")
    port = int(os.getenv("SMTP_PORT") or get_setting("smtp_port") or "587")
    user = os.getenv("SMTP_USER") or get_setting("smtp_user")
    pw   = os.getenv("SMTP_PASS") or get_setting("smtp_pass")
    frm  = os.getenv("SMTP_FROM") or get_setting("smtp_from") or user
    return {"host": host, "port": port, "user": user, "pass": pw, "from": frm}


def _send_email(to_email: str, subject: str, text_body: str, html_body: str) -> tuple:
    cfg = _get_smtp_config()
    if not cfg["host"] or not cfg["user"]:
        return False, "SMTP未設定"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
            server.starttls()
            server.login(cfg["user"], cfg["pass"])
            server.sendmail(cfg["from"], to_email, msg.as_string())
        return True, ""
    except Exception as e:
        return False, str(e)


def send_verification_email(email: str, name: str, token: str, base_url: str) -> tuple:
    verify_url = f"{base_url}?verify_token={token}"
    text_body = f"""{name} 様

ご登録ありがとうございます。

以下のリンクをクリックしてメールアドレスの確認を完了してください。

{verify_url}

このリンクは24時間有効です。

─────────────────────
クラファン新商品アイデアジェネレーター
"""
    html_body = f"""<html><body style="font-family:sans-serif;color:#333">
<p>{name} 様</p>
<p>ご登録ありがとうございます。</p>
<p>以下のボタンをクリックしてメールアドレスの確認を完了してください。</p>
<p>
  <a href="{verify_url}"
     style="background:#2c7be5;color:white;padding:12px 28px;
            border-radius:6px;text-decoration:none;display:inline-block;
            font-weight:bold">
    ✅ メールアドレスを確認する
  </a>
</p>
<p style="color:#888;font-size:12px">このリンクは24時間有効です。</p>
<hr style="border:none;border-top:1px solid #eee">
<p style="font-size:12px;color:#aaa">クラファン新商品アイデアジェネレーター</p>
</body></html>"""
    return _send_email(email, "【クラファンツール】メールアドレスの確認", text_body, html_body)


def send_password_reset_email(email: str, name: str, token: str, base_url: str) -> tuple:
    reset_url = f"{base_url}?reset_token={token}"
    text_body = f"""{name} 様

パスワードリセットのリクエストを受け付けました。

以下のリンクから新しいパスワードを設定してください（1時間以内に有効）。

{reset_url}

このリクエストに心当たりがない場合は無視してください。

─────────────────────
クラファン新商品アイデアジェネレーター
"""
    html_body = f"""<html><body style="font-family:sans-serif;color:#333">
<p>{name} 様</p>
<p>パスワードリセットのリクエストを受け付けました。</p>
<p>以下のボタンから新しいパスワードを設定してください。</p>
<p>
  <a href="{reset_url}"
     style="background:#e74c3c;color:white;padding:12px 28px;
            border-radius:6px;text-decoration:none;display:inline-block;
            font-weight:bold">
    🔑 パスワードをリセットする
  </a>
</p>
<p style="color:#888;font-size:12px">このリンクは1時間有効です。心当たりがない場合は無視してください。</p>
<hr style="border:none;border-top:1px solid #eee">
<p style="font-size:12px;color:#aaa">クラファン新商品アイデアジェネレーター</p>
</body></html>"""
    return _send_email(email, "【クラファンツール】パスワードリセット", text_body, html_body)
