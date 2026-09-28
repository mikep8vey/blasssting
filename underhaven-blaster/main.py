import os
import random
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
CONTACT_FILE = BASE_DIR / os.getenv("CONTACT_FILE", "contacts.txt")

load_dotenv(ENV_FILE)

HOST = "127.0.0.1"
PORT = int(os.getenv("PORT", "5000"))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY is required. Run install.sh first.")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
)

ADMIN_USERNAME = os.getenv("BLASSS_TING_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("BLASSS_TING_ADMIN_PASSWORD_HASH", "")

send_lock = threading.Lock()
campaign = {
    "running": False,
    "stop_requested": False,
    "sent": 0,
    "failed": 0,
    "total": 0,
    "current": "",
    "message": "",
    "last_error": "",
}

def logged_in():
    return session.get("authenticated") is True

def require_login():
    if not logged_in():
        return redirect(url_for("login"))
    return None

def get_api_key():
    load_dotenv(ENV_FILE, override=True)
    return os.getenv("TEXTBELT_API_KEY", "").strip()

def save_api_key(api_key):
    set_key(str(ENV_FILE), "TEXTBELT_API_KEY", api_key)
    os.chmod(ENV_FILE, 0o600)
    load_dotenv(ENV_FILE, override=True)

def load_contacts():
    path = BASE_DIR / os.getenv("CONTACT_FILE", "contacts.txt")
    if not path.exists():
        return []
    contacts = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            contacts.append(value)
    return contacts

def textbelt_quota(api_key):
    if not api_key:
        return {"success": False, "error": "No Textbelt API key configured."}
    response = requests.get(
        f"https://textbelt.com/quota/{api_key}",
        timeout=15,
    )
    data = response.json()
    data["_http_status"] = response.status_code
    return data

def send_text(phone, message, api_key):
    response = requests.post(
        "https://textbelt.com/text",
        data={"phone": phone, "message": message, "key": api_key},
        timeout=30,
    )
    try:
        data = response.json()
    except ValueError:
        data = {"success": False, "error": response.text}
    data["_http_status"] = response.status_code
    return data

def campaign_worker(contacts, messages, delay, api_key):
    global campaign
    with send_lock:
        campaign["running"] = True
        campaign["stop_requested"] = False
        campaign["sent"] = 0
        campaign["failed"] = 0
        campaign["total"] = len(contacts)
        campaign["current"] = ""
        campaign["message"] = "Campaign started."
        campaign["last_error"] = ""

    for index, phone in enumerate(contacts):
        with send_lock:
            if campaign["stop_requested"]:
                campaign["message"] = "Campaign stopped."
                break
            campaign["current"] = phone

        selected_message = random.choice(messages)
        try:
            result = send_text(phone, selected_message, api_key)
            if result.get("success"):
                with send_lock:
                    campaign["sent"] += 1
            else:
                with send_lock:
                    campaign["failed"] += 1
                    campaign["last_error"] = result.get("error", "Textbelt request failed.")
        except Exception as exc:
            with send_lock:
                campaign["failed"] += 1
                campaign["last_error"] = str(exc)

        if index < len(contacts) - 1:
            # Textbelt advises not exceeding 1 SMS request per second.
            time.sleep(max(1.0, delay))

    with send_lock:
        campaign["running"] = False
        campaign["current"] = ""
        if campaign["stop_requested"]:
            campaign["message"] = "Campaign stopped."
        else:
            campaign["message"] = "Campaign completed."

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response

@app.route("/login", methods=["GET", "POST"])
def login():
    if logged_in():
        return redirect(url_for("dashboard"))
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and ADMIN_PASSWORD_HASH and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session.clear()
            session["authenticated"] = True
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)

@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def index():
    return redirect(url_for("dashboard") if logged_in() else url_for("login"))

@app.route("/dashboard")
def dashboard():
    gate = require_login()
    if gate:
        return gate
    return render_template(
        "dashboard.html",
        api_configured=bool(get_api_key()),
        contacts_count=len(load_contacts()),
    )

@app.get("/api/status")
def api_status():
    gate = require_login()
    if gate:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    key = get_api_key()
    if not key:
        return jsonify({"configured": False, "message": "No Textbelt API key configured."})

    try:
        data = textbelt_quota(key)
        return jsonify({
            "configured": True,
            "success": data.get("success", False),
            "quotaRemaining": data.get("quotaRemaining"),
            "error": data.get("error"),
            "httpStatus": data.get("_http_status"),
        })
    except Exception as exc:
        return jsonify({"configured": True, "success": False, "error": str(exc)}), 502

@app.post("/api/textbelt/key")
def update_textbelt_key():
    gate = require_login()
    if gate:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    key = request.json.get("api_key", "").strip() if request.is_json else ""
    if not key:
        return jsonify({"success": False, "error": "API key is required."}), 400

    try:
        data = textbelt_quota(key)
        if not data.get("success"):
            return jsonify({
                "success": False,
                "error": data.get("error", "Textbelt rejected the API key."),
                "quotaRemaining": data.get("quotaRemaining"),
            }), 400

        save_api_key(key)
        return jsonify({
            "success": True,
            "quotaRemaining": data.get("quotaRemaining"),
            "message": "Textbelt API key verified and saved.",
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502

@app.get("/api/contacts")
def contacts_api():
    gate = require_login()
    if gate:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    return jsonify({"success": True, "count": len(load_contacts())})

@app.post("/api/campaign/start")
def start_campaign():
    gate = require_login()
    if gate:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    with send_lock:
        if campaign["running"]:
            return jsonify({"success": False, "error": "A campaign is already running."}), 409

    api_key = get_api_key()
    if not api_key:
        return jsonify({"success": False, "error": "Configure and verify your Textbelt API key first."}), 400

    payload = request.get_json(silent=True) or {}
    messages = [str(x).strip() for x in payload.get("messages", []) if str(x).strip()]
    try:
        delay = float(payload.get("delay", 1))
    except (TypeError, ValueError):
        delay = 1

    if not messages:
        return jsonify({"success": False, "error": "Enter at least one message."}), 400
    if delay < 1:
        return jsonify({"success": False, "error": "Delay must be at least 1 second."}), 400

    contacts = load_contacts()
    if not contacts:
        return jsonify({"success": False, "error": "contacts.txt is empty."}), 400

    thread = threading.Thread(
        target=campaign_worker,
        args=(contacts, messages, delay, api_key),
        daemon=True,
    )
    thread.start()
    return jsonify({"success": True, "message": "Campaign started.", "total": len(contacts)})

@app.post("/api/campaign/stop")
def stop_campaign():
    gate = require_login()
    if gate:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    with send_lock:
        campaign["stop_requested"] = True
    return jsonify({"success": True})

@app.get("/api/campaign/status")
def campaign_status():
    gate = require_login()
    if gate:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    with send_lock:
        return jsonify(campaign.copy())

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
