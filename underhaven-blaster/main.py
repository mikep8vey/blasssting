import os
import random
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash
from contact_importer import import_url

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)
CONTACT_FILE = BASE_DIR / os.getenv("CONTACT_FILE", "contacts.txt")

HOST = "127.0.0.1"
PORT = int(os.getenv("PORT", "5000"))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY is required. Run install.sh first.")

app.config.update(
    MAX_CONTENT_LENGTH=1024 * 1024,
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
    "quota_before": None,
    "quota_after": None,
    "quota_used": 0,
    "results": [],
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
    if not CONTACT_FILE.exists():
        return []
    contacts = []
    for line in CONTACT_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            contacts.append(value)
    return contacts


def textbelt_quota(api_key):
    if not api_key:
        return {"success": False, "error": "No Textbelt API key configured."}
    response = requests.get(f"https://textbelt.com/quota/{api_key}", timeout=15)
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


def textbelt_delivery_status(text_id, api_key):
    if not text_id:
        return {"success": False, "error": "No Textbelt text ID."}
    response = requests.get(
        f"https://textbelt.com/status/{text_id}",
        params={"key": api_key},
        timeout=15,
    )
    try:
        data = response.json()
    except ValueError:
        data = {"status": "UNKNOWN", "error": response.text}
    data["_http_status"] = response.status_code
    return data


def message_byte_length(message):
    return len(message.encode("utf-8"))


def estimated_segments(message):
    # Textbelt charges by SMS segments. This is deliberately an estimate;
    # Textbelt's carrier encoding determines the exact segment count.
    byte_length = message_byte_length(message)
    return max(1, (byte_length + 139) // 140)


def reset_campaign(contacts):
    campaign.update({
        "running": True,
        "stop_requested": False,
        "sent": 0,
        "failed": 0,
        "total": len(contacts),
        "current": "",
        "message": "Campaign started.",
        "last_error": "",
        "quota_before": None,
        "quota_after": None,
        "quota_used": 0,
        "results": [],
    })


def campaign_worker(contacts, messages, delay, api_key):
    global campaign

    try:
        try:
            before_data = textbelt_quota(api_key)
            quota_before = before_data.get("quotaRemaining")
        except Exception:
            quota_before = None

        with send_lock:
            reset_campaign(contacts)
            campaign["quota_before"] = quota_before

        for index, phone in enumerate(contacts):
            with send_lock:
                if campaign["stop_requested"]:
                    campaign["message"] = "Campaign stopped."
                    break
                campaign["current"] = phone

            selected_message = random.choice(messages)
            result_record = {
                "phone": phone,
                "message": selected_message,
                "success": False,
                "text_id": None,
                "delivery_status": "NOT_SENT",
                "error": "",
                "quota_remaining": None,
                "quota_used": None,
                "estimated_segments": estimated_segments(selected_message),
            }

            try:
                result = send_text(phone, selected_message, api_key)
                result_record["success"] = bool(result.get("success"))
                result_record["text_id"] = result.get("textId")
                result_record["quota_remaining"] = result.get("quotaRemaining")
                result_record["error"] = result.get("error", "") if not result.get("success") else ""

                if result.get("success"):
                    result_record["delivery_status"] = "SENT"
                    with send_lock:
                        campaign["sent"] += 1
                else:
                    result_record["delivery_status"] = "FAILED"
                    with send_lock:
                        campaign["failed"] += 1
                        campaign["last_error"] = result.get("error", "Textbelt request failed.")

                # Textbelt returns quotaRemaining on normal send responses.
                # Comparing it with the previous value reveals multi-segment usage.
                with send_lock:
                    previous_quota = campaign["quota_after"]
                    current_quota = result.get("quotaRemaining")
                    if current_quota is not None:
                        if previous_quota is None:
                            previous_quota = campaign["quota_before"]
                        if previous_quota is not None:
                            used = max(0, int(previous_quota) - int(current_quota))
                            result_record["quota_used"] = used
                            campaign["quota_used"] += used
                        campaign["quota_after"] = current_quota

                # A successful API response means accepted by Textbelt, not necessarily
                # delivered to the handset. Keep the text ID so the dashboard can check it.
                if result.get("success") and result.get("textId"):
                    result_record["delivery_status"] = "SENT"

            except Exception as exc:
                result_record["error"] = str(exc)
                result_record["delivery_status"] = "ERROR"
                with send_lock:
                    campaign["failed"] += 1
                    campaign["last_error"] = str(exc)

            with send_lock:
                campaign["results"].append(result_record)

            if index < len(contacts) - 1:
                time.sleep(max(1.0, delay))

        try:
            after_data = textbelt_quota(api_key)
            with send_lock:
                campaign["quota_after"] = after_data.get("quotaRemaining")
                if campaign["quota_before"] is not None and campaign["quota_after"] is not None:
                    campaign["quota_used"] = max(
                        0,
                        int(campaign["quota_before"]) - int(campaign["quota_after"]),
                    )
        except Exception:
            pass

        with send_lock:
            if campaign["stop_requested"]:
                campaign["message"] = "Campaign stopped."
            else:
                campaign["message"] = "Campaign completed."

    finally:
        with send_lock:
            campaign["running"] = False
            campaign["current"] = ""


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



@app.post("/api/import-url")
def import_contact_url():
    gate = require_login()
    if gate:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    payload = request.get_json(silent=True) or {}
    url = str(payload.get("url", "")).strip()
    if not url:
        return jsonify({"success": False, "error": "URL is required."}), 400
    if len(url) > 2048:
        return jsonify({"success": False, "error": "URL is too long."}), 400

    try:
        record = import_url(url)
        return jsonify({"success": True, "record": record})
    except requests.RequestException as exc:
        return jsonify({"success": False, "error": f"Unable to fetch the page: {exc}"}), 502
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

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


@app.post("/api/contacts/upload")
def upload_contacts():
    gate = require_login()
    if gate:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    with send_lock:
        if campaign["running"]:
            return jsonify({"success": False, "error": "Stop the current campaign before replacing the contact list."}), 409

    uploaded = request.files.get("contacts")
    if not uploaded or not uploaded.filename:
        return jsonify({"success": False, "error": "Choose a .txt contact file."}), 400

    if not uploaded.filename.lower().endswith(".txt"):
        return jsonify({"success": False, "error": "Only .txt files are accepted."}), 400

    try:
        raw = uploaded.read()
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"success": False, "error": "The contact file must be UTF-8 text."}), 400

    contacts = []
    seen = set()
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if len(value) > 64 or any(ch.isspace() for ch in value):
            return jsonify({"success": False, "error": f"Invalid contact line: {value[:80]}"}), 400
        if value not in seen:
            seen.add(value)
            contacts.append(value)

    if not contacts:
        return jsonify({"success": False, "error": "The uploaded file contains no contacts."}), 400

    try:
        CONTACT_FILE.write_text("\n".join(contacts) + "\n", encoding="utf-8")
        os.chmod(CONTACT_FILE, 0o600)
    except OSError as exc:
        return jsonify({"success": False, "error": f"Could not save contacts: {exc}"}), 500

    return jsonify({
        "success": True,
        "count": len(contacts),
        "message": f"Uploaded {len(contacts)} contacts to contacts.txt."
    })


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

    # Warn before starting if any configured message is likely to use multiple segments.
    max_bytes = max(message_byte_length(message) for message in messages)
    if max_bytes > 140:
        warning = (
            "One or more messages exceed 140 UTF-8 bytes and may consume multiple SMS segments. "
            "The dashboard will show actual quota usage reported by Textbelt."
        )
    else:
        warning = ""

    thread = threading.Thread(
        target=campaign_worker,
        args=(contacts, messages, delay, api_key),
        daemon=True,
    )
    thread.start()
    return jsonify({
        "success": True,
        "message": "Campaign started.",
        "total": len(contacts),
        "warning": warning,
    })


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


@app.post("/api/campaign/refresh-delivery")
def refresh_delivery():
    gate = require_login()
    if gate:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    api_key = get_api_key()
    if not api_key:
        return jsonify({"success": False, "error": "Textbelt API key is not configured."}), 400

    with send_lock:
        results = [dict(item) for item in campaign["results"] if item.get("text_id")]

    for item in results:
        try:
            status_data = textbelt_delivery_status(item["text_id"], api_key)
            item["delivery_status"] = status_data.get("status", "UNKNOWN")
            if status_data.get("error"):
                item["error"] = status_data["error"]
        except Exception as exc:
            item["delivery_status"] = "UNKNOWN"
            item["error"] = str(exc)

        with send_lock:
            for stored in campaign["results"]:
                if stored.get("text_id") == item.get("text_id"):
                    stored.update(item)
                    break

    with send_lock:
        return jsonify({"success": True, "results": campaign["results"]})


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
