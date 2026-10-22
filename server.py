import os
import re
import html
import hmac
import secrets
import logging
from datetime import datetime, timezone
from functools import wraps
from collections import defaultdict, deque
from threading import Lock

import psycopg
import requests

from psycopg.rows import dict_row
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session,
    abort
)
from dotenv import load_dotenv
from werkzeug.security import check_password_hash

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", "")
app.config["MAX_CONTENT_LENGTH"] = 8192
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.getenv("COOKIE_SECURE", "true").lower() == "true"
)

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID", "8902663892")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")

if not app.secret_key:
    raise RuntimeError("Не задан SECRET_KEY")

if not DATABASE_URL:
    raise RuntimeError("Не задан DATABASE_URL")

if not ADMIN_PASSWORD_HASH:
    raise RuntimeError("Не задан ADMIN_PASSWORD_HASH")


# ==========================================
# БАЗА ДАННЫХ
# ==========================================

def get_db():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10
    )


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    fullname VARCHAR(100) NOT NULL,
                    age INTEGER NOT NULL,
                    experience TEXT NOT NULL,
                    portfolio VARCHAR(500) NOT NULL,
                    languages VARCHAR(500) NOT NULL,
                    contacts VARCHAR(200) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('pending', 'accepted', 'rejected'))
                )
            """)


# ==========================================
# ЗАЩИТА
# ==========================================

def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return func(*args, **kwargs)
    return wrapper


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)

    return session["csrf"]


def verify_csrf():
    expected = session.get("csrf", "")
    received = request.form.get("csrf", "")

    if not expected or not hmac.compare_digest(expected, received):
        abort(403)


# Простейший лимит запросов на одном процессе.
# Для серьёзного публичного проекта нужен Redis
# или внешняя защита, например Cloudflare Turnstile.

rate_lock = Lock()
rate_history = defaultdict(deque)


def is_rate_limited(key, limit, period):
    now = datetime.now(timezone.utc).timestamp()

    with rate_lock:
        history = rate_history[key]

        while history and now - history[0] > period:
            history.popleft()

        if len(history) >= limit:
            return True

        history.append(now)
        return False


# ==========================================
# TELEGRAM
# ==========================================

def send_telegram(text):
    if not BOT_TOKEN:
        app.logger.warning("BOT_TOKEN отсутствует")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": ADMIN_ID,
                "text": text,
                "parse_mode": "HTML"
            },
            timeout=12
        )

        response.raise_for_status()

        return response.json().get("ok", False)

    except (requests.RequestException, ValueError):
        app.logger.exception("Ошибка Telegram")
        return False


def safe(value):
    return html.escape(str(value))


# ==========================================
# ГЛАВНАЯ
# ==========================================

@app.route("/")
def index():
    return render_template("index.html")


# ==========================================
# ПОДАЧА ЗАЯВКИ
# ==========================================

@app.route("/api/apply", methods=["POST"])
def apply():

    ip = request.remote_addr or "unknown"

    if is_rate_limited(("apply", ip), 5, 3600):
        return jsonify(
            error="Слишком много заявок. Попробуйте позже."
        ), 429

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify(error="Неверные данные"), 400

    fields = {
        "fullname": 100,
        "age": 3,
        "experience": 2000,
        "portfolio": 500,
        "languages": 500,
        "contacts": 200
    }

    for field, max_length in fields.items():
        value = data.get(field)

        if not isinstance(value, str):
            return jsonify(error="Заполните все поля"), 400

        value = value.strip()

        if not value or len(value) > max_length:
            return jsonify(error="Некорректные данные"), 400

        data[field] = value

    if data.get("consent") is not True:
        return jsonify(
            error="Необходимо согласие на обработку данных"
        ), 400

    try:
        age = int(data["age"])

        if not 1 <= age <= 120:
            raise ValueError

    except ValueError:
        return jsonify(error="Некорректный возраст"), 400

    if not re.match(
        r"^https?://[^\s/]+\.[^\s/]+",
        data["portfolio"],
        re.IGNORECASE
    ):
        return jsonify(error="Некорректная ссылка"), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO applications (
                        fullname,
                        age,
                        experience,
                        portfolio,
                        languages,
                        contacts
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    data["fullname"],
                    age,
                    data["experience"],
                    data["portfolio"],
                    data["languages"],
                    data["contacts"]
                ))

                application_id = cur.fetchone()["id"]

    except psycopg.Error:
        app.logger.exception("Ошибка базы данных")
        return jsonify(
            error="Не удалось сохранить заявку"
        ), 500

    text = f"""
📩 <b>НОВАЯ ЗАЯВКА DEVTEAM!</b>

🆔 <b>Заявка:</b> #{application_id}

👤 <b>Имя:</b>
{safe(data['fullname'])}

🎂 <b>Возраст:</b>
{age}

💼 <b>Опыт:</b>
{safe(data['experience'])}

📁 <b>Портфолио:</b>
{safe(data['portfolio'])}

💻 <b>Языки:</b>
{safe(data['languages'])}

💬 <b>Контакты:</b>
{safe(data['contacts'])}

━━━━━━━━━━━━━━━━
✅ Заявка сохранена в DEVTEAM.
"""

    # Заявка сохраняется независимо от доступности Telegram.
    if not send_telegram(text):
        app.logger.warning(
            "Заявка %s сохранена, но Telegram недоступен",
            application_id
        )

    return jsonify(
        success=True,
        id=application_id
    )


# ==========================================
# АВТОРИЗАЦИЯ АДМИНА
# ==========================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if session.get("admin"):
        return redirect(url_for("admin_panel"))

    error = None

    if request.method == "POST":
        verify_csrf()

        ip = request.remote_addr or "unknown"

        if is_rate_limited(("login", ip), 10, 900):
            error = "Слишком много попыток. Попробуйте позже."

        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")

            valid_username = hmac.compare_digest(
                username,
                ADMIN_USERNAME
            )

            valid_password = check_password_hash(
                ADMIN_PASSWORD_HASH,
                password
            )

            if valid_username and valid_password:
                session.clear()
                session["admin"] = True
                csrf_token()

                return redirect(url_for("admin_panel"))

            error = "Неверный логин или пароль"

    return render_template(
        "admin_login.html",
        error=error,
        csrf=csrf_token()
    )


@app.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    verify_csrf()
    session.clear()

    return redirect(url_for("index"))


# ==========================================
# ПАНЕЛЬ АДМИНИСТРАТОРА
# ==========================================

@app.route("/admin")
@admin_required
def admin_panel():

    search = request.args.get("search", "").strip()[:100]

    with get_db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE status = 'pending'
                    ) AS pending,
                    COUNT(*) FILTER (
                        WHERE status = 'accepted'
                    ) AS accepted,
                    COUNT(*) FILTER (
                        WHERE status = 'rejected'
                    ) AS rejected
                FROM applications
            """)

            stats = cur.fetchone()

            if search:
                cur.execute("""
                    SELECT *
                    FROM applications
                    WHERE fullname ILIKE %s
                    ORDER BY created_at DESC
                    LIMIT 200
                """, (f"%{search}%",))

            else:
                cur.execute("""
                    SELECT *
                    FROM applications
                    ORDER BY created_at DESC
                    LIMIT 200
                """)

            applications = cur.fetchall()

    return render_template(
        "admin.html",
        stats=stats,
        applications=applications,
        search=search,
        csrf=csrf_token()
    )


# ==========================================
# ИЗМЕНЕНИЕ СТАТУСА
# ==========================================

@app.route("/admin/status/<int:application_id>", methods=["POST"])
@admin_required
def update_status(application_id):
    verify_csrf()

    status = request.form.get("status")

    if status not in ("accepted", "rejected"):
        abort(400)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE applications
                SET status = %s
                WHERE id = %s
            """, (status, application_id))

    return redirect(url_for("admin_panel"))


# ==========================================
# УДАЛЕНИЕ
# ==========================================

@app.route("/admin/delete/<int:application_id>", methods=["POST"])
@admin_required
def delete_application(application_id):
    verify_csrf()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM applications
                WHERE id = %s
            """, (application_id,))

    return redirect(url_for("admin_panel"))


# ==========================================
# ЗАПУСК
# ==========================================

if __name__ == "__main__":
    init_db()

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=False
    )
