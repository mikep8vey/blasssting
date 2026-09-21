import os
import random
import secrets
import threading
import time
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

load_dotenv()

CONTACT_FILE = os.getenv("CONTACT_FILE", "contacts.txt")
TEXTBELT_URL = "https://textbelt.com/text"
QUOTA_URL = "https://textbelt.com/quota"
TEXTBELT_KEY = os.getenv("TEXTBELT_API_KEY", "").strip()
ADMIN_USERNAME = os.getenv("BLASSS_TING_ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD_HASH = os.getenv("BLASSS_TING_ADMIN_PASSWORD_HASH", "").strip()
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "").strip()

if not SECRET_KEY:
    raise RuntimeError("FLASK_SECRET_KEY is required. Add it to your .env file.")

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    MAX_CONTENT_LENGTH=32 * 1024,
)

campaign = {
    "running": False,
    "paused": False,
    "stop_requested": False,
    "total": 0,
    "processed": 0,
    "success": 0,
    "failed": 0,
    "current": "",
    "logs": [],
    "started_at": None,
}

campaign_lock = threading.RLock()
worker_thread = None


def add_log(message, level="info"):
    with campaign_lock:
        campaign["logs"].append({
            "time": time.strftime("%H:%M:%S"),
            "message": message,
            "level": level,
        })
        campaign["logs"] = campaign["logs"][-200:]


def load_contacts():
    if not os.path.exists(CONTACT_FILE):
        return []
    contacts = []
    with open(CONTACT_FILE, "r", encoding="utf-8") as file:
        for line in file:
            phone = line.strip()
            if phone and not phone.startswith("#"):
                contacts.append(phone)
    return contacts


def login_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "error": "Authentication required."}), 401
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapped


def csrf_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        expected = session.get("csrf_token")
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            return jsonify({"success": False, "error": "Invalid CSRF token."}), 403
        return function(*args, **kwargs)
    return wrapped


def send_sms(phone, message):
    if not TEXTBELT_KEY:
        return {"success": False, "error": "TEXTBELT_API_KEY is not configured."}
    try:
        response = requests.post(
            TEXTBELT_URL,
            data={"phone": phone, "message": message, "key": TEXTBELT_KEY},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        return {"success": False, "error": f"Textbelt request failed: {exc}"}
    except ValueError:
        return {"success": False, "error": "Textbelt returned an invalid response."}


def check_api_key():
    if not TEXTBELT_KEY:
        return {"success": False, "error": "TEXTBELT_API_KEY is not configured."}
    try:
        response = requests.get(f"{QUOTA_URL}/{TEXTBELT_KEY}", timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        return {"success": False, "error": f"Quota request failed: {exc}"}
    except ValueError:
        return {"success": False, "error": "Textbelt returned an invalid response."}


def campaign_worker(contacts, messages, delay):
    global worker_thread
    try:
        for phone in contacts:
            while True:
                with campaign_lock:
                    if campaign["stop_requested"]:
                        campaign["running"] = False
                        campaign["paused"] = False
                        campaign["current"] = ""
                        add_log("Campaign stopped.", "warning")
                        return
                    paused = campaign["paused"]
                if not paused:
                    break
                time.sleep(0.25)

            with campaign_lock:
                campaign["current"] = phone

            result = send_sms(phone, random.choice(messages))

            with campaign_lock:
                campaign["processed"] += 1
                if result.get("success"):
                    campaign["success"] += 1
                else:
                    campaign["failed"] += 1

            if result.get("success"):
                add_log(f"Sent to {phone}", "success")
            else:
                add_log(f"Failed for {phone}: {result.get('error', 'Unknown error')}", "error")

            elapsed = 0.0
            while elapsed < delay:
                with campaign_lock:
                    if campaign["stop_requested"]:
                        campaign["running"] = False
                        campaign["paused"] = False
                        campaign["current"] = ""
                        add_log("Campaign stopped.", "warning")
                        return
                    paused = campaign["paused"]
                if paused:
                    time.sleep(0.25)
                    continue
                time.sleep(0.25)
                elapsed += 0.25

        with campaign_lock:
            campaign["running"] = False
            campaign["paused"] = False
            campaign["current"] = ""
        add_log("Campaign completed.", "success")
    finally:
        worker_thread = None


@app.get("/")
def index():
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    return render_template("dashboard.html", csrf_token=session["csrf_token"])


@app.get("/login")
def login():
    if session.get("authenticated"):
        return redirect(url_for("index"))
    return render_template("login.html")


@app.post("/login")
def do_login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not ADMIN_PASSWORD_HASH:
        return render_template("login.html", error="Administrator password is not configured on the server.")

    if secrets.compare_digest(username, ADMIN_USERNAME) and check_password_hash(ADMIN_PASSWORD_HASH, password):
        session.clear()
        session["authenticated"] = True
        session["username"] = ADMIN_USERNAME
        session["csrf_token"] = secrets.token_urlsafe(32)
        session.permanent = True
        return redirect(url_for("index"))

    return render_template("login.html", error="Invalid administrator credentials."), 401


@app.post("/logout")
@login_required
@csrf_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/contacts")
@login_required
def api_contacts():
    contacts = load_contacts()
    return jsonify({"success": True, "count": len(contacts), "contacts": contacts})


@app.get("/api/test-key")
@login_required
def api_test_key():
    return jsonify(check_api_key())


@app.post("/api/start")
@login_required
@csrf_required
def api_start():
    global worker_thread

    if worker_thread and worker_thread.is_alive():
        return jsonify({"success": False, "error": "A campaign is already running."}), 409

    data = request.get_json(silent=True) or {}
    raw_messages = data.get("messages", [])

    try:
        delay = int(data.get("delay", 2))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Delay must be a whole number."}), 400

    messages = [str(message).strip() for message in raw_messages if str(message).strip()][:5]

    if not messages:
        return jsonify({"success": False, "error": "Enter at least one message."}), 400
    if delay < 1 or delay > 3600:
        return jsonify({"success": False, "error": "Delay must be between 1 and 3600 seconds."}), 400

    contacts = load_contacts()
    if not contacts:
        return jsonify({"success": False, "error": "No contacts found in contacts.txt."}), 400
    if not TEXTBELT_KEY:
        return jsonify({"success": False, "error": "TEXTBELT_API_KEY is not configured."}), 500

    with campaign_lock:
        campaign.update({
            "running": True,
            "paused": False,
            "stop_requested": False,
            "total": len(contacts),
            "processed": 0,
            "success": 0,
            "failed": 0,
            "current": "",
            "logs": [],
            "started_at": time.time(),
        })

    add_log(f"Campaign started for {len(contacts)} contacts.", "info")

    worker_thread = threading.Thread(
        target=campaign_worker,
        args=(contacts, messages, delay),
        daemon=True,
    )
    worker_thread.start()
    return jsonify({"success": True})


@app.post("/api/pause")
@login_required
@csrf_required
def api_pause():
    with campaign_lock:
        if not campaign["running"]:
            return jsonify({"success": False, "error": "No active campaign."}), 400
        campaign["paused"] = True
    add_log("Campaign paused.", "warning")
    return jsonify({"success": True})


@app.post("/api/resume")
@login_required
@csrf_required
def api_resume():
    with campaign_lock:
        if not campaign["running"]:
            return jsonify({"success": False, "error": "No active campaign."}), 400
        campaign["paused"] = False
    add_log("Campaign resumed.", "info")
    return jsonify({"success": True})


@app.post("/api/stop")
@login_required
@csrf_required
def api_stop():
    with campaign_lock:
        if not campaign["running"]:
            return jsonify({"success": False, "error": "No active campaign."}), 400
        campaign["stop_requested"] = True
    return jsonify({"success": True})


@app.get("/api/status")
@login_required
def api_status():
    with campaign_lock:
        data = dict(campaign)
        data["logs"] = list(campaign["logs"])
    return jsonify(data)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "blasssting"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
