"""auth.py: ユーザー認証・管理システム（SQLite + bcrypt）"""

import os
import secrets
import smtplib
import sqlite3
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import bcrypt

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")


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
    conn.commit()
    conn.close()


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    """トークンでメール認証。成功すれば True。"""
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
    """認証。(user_dict | None, error_message) を返す。"""
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
    conn = _get_conn()
    conn.execute("UPDATE users SET api_key=? WHERE id=?", (api_key, user_id))
    conn.commit()
    conn.close()


def get_user_api_key(user_id: int) -> str:
    conn = _get_conn()
    row = conn.execute("SELECT api_key FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return row["api_key"] if row else ""


def get_all_users() -> list:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT id, email, phone, name, is_verified, is_admin,
                  newsletter_consent, created_at
           FROM users ORDER BY created_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_session(user_id: int, days: int = 30) -> str:
    from datetime import timedelta
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


def send_verification_email(
    email: str, name: str, token: str, base_url: str
) -> tuple:
    """確認メール送信。SMTP未設定の場合は (False, 'SMTP未設定') を返す。"""
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    from_email = os.getenv("SMTP_FROM", smtp_user)

    if not smtp_host or not smtp_user:
        return False, "SMTP未設定"

    verify_url = f"{base_url}?verify_token={token}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "【クラファンツール】メールアドレスの確認"
    msg["From"] = from_email
    msg["To"] = email

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

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, email, msg.as_string())
        return True, ""
    except Exception as e:
        return False, str(e)
