from flask import Flask, request, jsonify, render_template_string
import requests
import threading
import time
import random
import os
from datetime import datetime

app = Flask(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "BLASSS TING"
TEXTBELT_URL = "https://textbelt.com/text"
QUOTA_URL = "https://textbelt.com/quota"
CONTACT_FILE = "contacts.txt"

# Keep Flask behind Nginx in production.
HOST = "0.0.0.0"
PORT = 5000


# ============================================================
# GLOBAL CAMPAIGN STATE
# ============================================================

campaign_lock = threading.Lock()

campaign = {
    "running": False,
    "paused": False,
    "stop_requested": False,

    "contacts": [],
    "total": 0,
    "current_index": 0,

    "sent": 0,
    "failed": 0,

    "current_number": "",
    "current_message_number": None,

    "delay": 2,

    "started_at": None,
    "finished_at": None,

    "logs": []
}


# ============================================================
# CONTACT HANDLING
# ============================================================

def load_contacts():
    """
    Load contacts from contacts.txt.

    Blank lines are ignored.
    Duplicate numbers are removed.
    """

    if not os.path.exists(CONTACT_FILE):
        return []

    contacts = []

    try:
        with open(CONTACT_FILE, "r", encoding="utf-8") as file:
            for line in file:
                number = line.strip()

                if not number:
                    continue

                if number not in contacts:
                    contacts.append(number)

    except Exception as exc:
        print(f"Error loading contacts: {exc}")

    return contacts


# ============================================================
# LOGGING
# ============================================================

def add_log(message, level="info"):
    timestamp = datetime.now().strftime("%H:%M:%S")

    entry = {
        "time": timestamp,
        "message": message,
        "level": level
    }

    with campaign_lock:
        campaign["logs"].insert(0, entry)

        # Keep memory usage reasonable.
        campaign["logs"] = campaign["logs"][:300]


# ============================================================
# TEXTBELT
# ============================================================

def send_sms(phone, message, api_key):
    try:
        response = requests.post(
            TEXTBELT_URL,
            data={
                "phone": phone,
                "message": message,
                "key": api_key
            },
            timeout=30
        )

        try:
            return response.json()
        except Exception:
            return {
                "success": False,
                "error": response.text
            }

    except requests.RequestException as exc:
        return {
            "success": False,
            "error": str(exc)
        }


def check_api_key(api_key):
    if not api_key:
        return {
            "success": False,
            "error": "API key is required."
        }

    try:
        response = requests.get(
            f"{QUOTA_URL}/{api_key}",
            timeout=15
        )

        data = response.json()

        return data

    except Exception as exc:
        return {
            "success": False,
            "error": str(exc)
        }


# ============================================================
# CAMPAIGN WORKER
# ============================================================

def campaign_worker(api_key, messages, delay):
    contacts = list(campaign["contacts"])

    add_log(
        f"Campaign started with {len(contacts)} recipient(s).",
        "success"
    )

    for index, phone in enumerate(contacts):

        # ----------------------------------------------------
        # STOP CHECK
        # ----------------------------------------------------

        with campaign_lock:
            if campaign["stop_requested"]:
                break

        # ----------------------------------------------------
        # PAUSE CHECK
        # ----------------------------------------------------

        while True:
            with campaign_lock:
                paused = campaign["paused"]
                stopped = campaign["stop_requested"]

            if stopped:
                break

            if not paused:
                break

            time.sleep(0.25)

        with campaign_lock:
            if campaign["stop_requested"]:
                break

        # ----------------------------------------------------
        # SELECT RANDOM MESSAGE
        # ----------------------------------------------------

        selected_message_index = random.randrange(len(messages))
        selected_message = messages[selected_message_index]

        with campaign_lock:
            campaign["current_index"] = index + 1
            campaign["current_number"] = phone
            campaign["current_message_number"] = selected_message_index + 1

        add_log(
            f"Sending to {phone} using Message {selected_message_index + 1}...",
            "info"
        )

        # ----------------------------------------------------
        # SEND
        # ----------------------------------------------------

        result = send_sms(
            phone,
            selected_message,
            api_key
        )

        success = bool(result.get("success"))

        with campaign_lock:
            if success:
                campaign["sent"] += 1
            else:
                campaign["failed"] += 1

        if success:
            add_log(
                f"✓ Successfully sent to {phone}",
                "success"
            )
        else:
            error = (
                result.get("error")
                or result.get("errorMessage")
                or "Unknown Textbelt error"
            )

            add_log(
                f"✕ Failed for {phone}: {error}",
                "error"
            )

        # ----------------------------------------------------
        # DELAY
        # ----------------------------------------------------

        if index < len(contacts) - 1:

            remaining = float(delay)

            while remaining > 0:

                with campaign_lock:
                    stopped = campaign["stop_requested"]
                    paused = campaign["paused"]

                if stopped:
                    break

                if paused:
                    time.sleep(0.25)
                    continue

                sleep_time = min(0.25, remaining)
                time.sleep(sleep_time)
                remaining -= sleep_time

        with campaign_lock:
            if campaign["stop_requested"]:
                break

    # --------------------------------------------------------
    # FINISH
    # --------------------------------------------------------

    with campaign_lock:
        was_stopped = campaign["stop_requested"]

        campaign["running"] = False
        campaign["paused"] = False
        campaign["stop_requested"] = False
        campaign["finished_at"] = datetime.now().isoformat()

    if was_stopped:
        add_log("Campaign stopped.", "warning")
    else:
        add_log("✓ Campaign completed.", "success")


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/api/contacts")
def contacts_api():

    contacts = load_contacts()

    with campaign_lock:
        campaign["contacts"] = contacts

    return jsonify({
        "success": True,
        "count": len(contacts),
        "contacts": contacts
    })


@app.route("/api/test-key", methods=["POST"])
def test_key():

    data = request.get_json(silent=True) or {}

    api_key = data.get("api_key", "").strip()

    if not api_key:
        return jsonify({
            "success": False,
            "error": "Enter your Textbelt API key."
        }), 400

    result = check_api_key(api_key)

    return jsonify(result)


@app.route("/api/start", methods=["POST"])
def start_campaign():

    data = request.get_json(silent=True) or {}

    api_key = data.get("api_key", "").strip()

    messages = [
        str(data.get("message1", "")).strip(),
        str(data.get("message2", "")).strip(),
        str(data.get("message3", "")).strip(),
        str(data.get("message4", "")).strip(),
        str(data.get("message5", "")).strip()
    ]

    messages = [
        message
        for message in messages
        if message
    ]

    try:
        delay = float(data.get("delay", 2))
    except (TypeError, ValueError):
        delay = 2

    delay = max(1, min(delay, 3600))

    if not api_key:
        return jsonify({
            "success": False,
            "error": "Textbelt API key is required."
        }), 400

    if not messages:
        return jsonify({
            "success": False,
            "error": "Enter at least one message."
        }), 400

    contacts = load_contacts()

    if not contacts:
        return jsonify({
            "success": False,
            "error": "No contacts were found in contacts.txt."
        }), 400

    with campaign_lock:

        if campaign["running"]:
            return jsonify({
                "success": False,
                "error": "A campaign is already running."
            }), 400

        campaign["running"] = True
        campaign["paused"] = False
        campaign["stop_requested"] = False

        campaign["contacts"] = contacts
        campaign["total"] = len(contacts)

        campaign["current_index"] = 0
        campaign["sent"] = 0
        campaign["failed"] = 0

        campaign["current_number"] = ""
        campaign["current_message_number"] = None

        campaign["delay"] = delay

        campaign["started_at"] = datetime.now().isoformat()
        campaign["finished_at"] = None

        campaign["logs"] = []

    add_log(
        f"Loaded {len(contacts)} contact(s).",
        "info"
    )

    thread = threading.Thread(
        target=campaign_worker,
        args=(api_key, messages, delay),
        daemon=True
    )

    thread.start()

    return jsonify({
        "success": True,
        "message": "Campaign started."
    })


@app.route("/api/pause", methods=["POST"])
def pause_campaign():

    with campaign_lock:

        if not campaign["running"]:
            return jsonify({
                "success": False,
                "error": "No campaign is currently running."
            }), 400

        campaign["paused"] = True

    add_log("Campaign paused.", "warning")

    return jsonify({
        "success": True
    })


@app.route("/api/resume", methods=["POST"])
def resume_campaign():

    with campaign_lock:

        if not campaign["running"]:
            return jsonify({
                "success": False,
                "error": "No campaign is currently running."
            }), 400

        campaign["paused"] = False

    add_log("Campaign resumed.", "success")

    return jsonify({
        "success": True
    })


@app.route("/api/stop", methods=["POST"])
def stop_campaign():

    with campaign_lock:

        if not campaign["running"]:
            return jsonify({
                "success": False,
                "error": "No campaign is currently running."
            }), 400

        campaign["stop_requested"] = True

    add_log("Stopping campaign...", "warning")

    return jsonify({
        "success": True
    })


@app.route("/api/status")
def campaign_status():

    with campaign_lock:

        total = campaign["total"]
        processed = campaign["sent"] + campaign["failed"]

        if total:
            progress = round(
                (processed / total) * 100,
                1
            )
        else:
            progress = 0

        return jsonify({
            "running": campaign["running"],
            "paused": campaign["paused"],

            "total": total,
            "processed": processed,

            "sent": campaign["sent"],
            "failed": campaign["failed"],

            "remaining": max(0, total - processed),

            "progress": progress,

            "current_number": campaign["current_number"],
            "current_message_number": campaign["current_message_number"],

            "delay": campaign["delay"],

            "started_at": campaign["started_at"],
            "finished_at": campaign["finished_at"],

            "logs": campaign["logs"]
        })


# ============================================================
# HTML / CSS / JAVASCRIPT
# ============================================================

HTML_PAGE = r"""
<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>BLASSS TING — SMS Dashboard</title>

<style>

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

:root {

    --bg: #080b12;
    --sidebar: #0c1018;
    --card: #101620;
    --card2: #131a25;

    --border: rgba(255,255,255,0.08);

    --text: #f4f7fb;
    --muted: #8994a5;

    --accent: #6d7cff;
    --accent2: #8b5cf6;

    --success: #22c55e;
    --warning: #f59e0b;
    --danger: #ef4444;

    --shadow:
        0 20px 60px rgba(0,0,0,0.35);
}

body {

    min-height: 100vh;

    font-family:
        Inter,
        ui-sans-serif,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background:
        radial-gradient(
            circle at 20% 0%,
            rgba(109,124,255,0.13),
            transparent 35%
        ),
        radial-gradient(
            circle at 100% 30%,
            rgba(139,92,246,0.10),
            transparent 30%
        ),
        var(--bg);

    color: var(--text);
}


/* ============================================================
   LAYOUT
   ============================================================ */

.app {

    min-height: 100vh;

    display: flex;
}


/* ============================================================
   SIDEBAR
   ============================================================ */

.sidebar {

    width: 245px;

    background:
        rgba(12,16,24,0.88);

    border-right: 1px solid var(--border);

    padding: 25px 16px;

    position: fixed;

    top: 0;
    bottom: 0;
    left: 0;

    z-index: 10;

    backdrop-filter: blur(18px);
}

.brand {

    display: flex;

    align-items: center;

    gap: 12px;

    padding: 0 10px 30px;
}

.brand-icon {

    width: 42px;
    height: 42px;

    border-radius: 13px;

    display: grid;
    place-items: center;

    font-weight: 900;

    color: white;

    background:
        linear-gradient(
            135deg,
            var(--accent),
            var(--accent2)
        );

    box-shadow:
        0 10px 30px rgba(109,124,255,0.25);
}

.brand-name {

    font-size: 16px;

    font-weight: 800;

    letter-spacing: 1.5px;
}

.brand-small {

    font-size: 11px;

    color: var(--muted);

    margin-top: 2px;
}


.nav-label {

    padding: 10px;

    color: #596477;

    font-size: 10px;

    font-weight: 800;

    letter-spacing: 1.5px;

    text-transform: uppercase;
}

.nav-item {

    width: 100%;

    display: flex;

    align-items: center;

    gap: 12px;

    padding: 12px;

    margin-bottom: 5px;

    border-radius: 10px;

    border: 1px solid transparent;

    color: var(--muted);

    background: transparent;

    font-size: 13px;

    cursor: pointer;

    transition: 0.2s;
}

.nav-item:hover {

    color: white;

    background: rgba(255,255,255,0.04);
}

.nav-item.active {

    color: white;

    background:
        linear-gradient(
            90deg,
            rgba(109,124,255,0.15),
            rgba(109,124,255,0.04)
        );

    border-color:
        rgba(109,124,255,0.15);
}

.nav-icon {

    width: 20px;

    text-align: center;

    opacity: 0.9;
}


.sidebar-bottom {

    position: absolute;

    bottom: 20px;

    left: 16px;
    right: 16px;
}

.server-card {

    border: 1px solid var(--border);

    background: rgba(255,255,255,0.025);

    border-radius: 13px;

    padding: 13px;
}

.server-row {

    display: flex;

    align-items: center;

    gap: 8px;

    font-size: 12px;
}

.status-dot {

    width: 8px;
    height: 8px;

    border-radius: 50%;

    background: var(--success);

    box-shadow:
        0 0 10px rgba(34,197,94,0.6);
}

.server-text {

    color: var(--muted);

    font-size: 11px;

    margin-top: 6px;
}


/* ============================================================
   MAIN
   ============================================================ */

.main {

    margin-left: 245px;

    width: calc(100% - 245px);

    min-height: 100vh;

    padding: 28px;

    max-width: 1700px;
}

.topbar {

    display: flex;

    justify-content: space-between;

    align-items: center;

    margin-bottom: 28px;
}

.page-title {

    font-size: 27px;

    font-weight: 800;

    letter-spacing: -0.7px;
}

.page-subtitle {

    color: var(--muted);

    font-size: 13px;

    margin-top: 5px;
}

.top-status {

    display: flex;

    align-items: center;

    gap: 8px;

    border: 1px solid var(--border);

    background: rgba(255,255,255,0.025);

    padding: 9px 13px;

    border-radius: 10px;

    font-size: 12px;

    color: var(--muted);
}


/* ============================================================
   STATS
   ============================================================ */

.stats {

    display: grid;

    grid-template-columns:
        repeat(4, minmax(0, 1fr));

    gap: 15px;

    margin-bottom: 18px;
}

.stat {

    background:
        linear-gradient(
            145deg,
            rgba(255,255,255,0.045),
            rgba(255,255,255,0.015)
        );

    border: 1px solid var(--border);

    border-radius: 15px;

    padding: 18px;

    box-shadow: var(--shadow);
}

.stat-top {

    display: flex;

    justify-content: space-between;

    align-items: center;

    color: var(--muted);

    font-size: 12px;
}

.stat-icon {

    width: 32px;
    height: 32px;

    border-radius: 9px;

    display: grid;
    place-items: center;

    background: rgba(109,124,255,0.10);

    color: #aab2ff;
}

.stat-value {

    font-size: 27px;

    font-weight: 800;

    margin-top: 14px;
}

.stat-description {

    color: #657084;

    font-size: 11px;

    margin-top: 4px;
}


/* ============================================================
   GRID
   ============================================================ */

.content-grid {

    display: grid;

    grid-template-columns:
        minmax(0, 1.45fr)
        minmax(340px, 0.8fr);

    gap: 18px;
}

.card {

    background:
        linear-gradient(
            145deg,
            rgba(255,255,255,0.045),
            rgba(255,255,255,0.018)
        );

    border: 1px solid var(--border);

    border-radius: 16px;

    box-shadow: var(--shadow);

    overflow: hidden;
}

.card-header {

    padding: 18px 20px;

    border-bottom: 1px solid var(--border);

    display: flex;

    justify-content: space-between;

    align-items: center;
}

.card-title {

    font-size: 14px;

    font-weight: 700;
}

.card-subtitle {

    color: var(--muted);

    font-size: 11px;

    margin-top: 4px;
}

.card-body {

    padding: 20px;
}


/* ============================================================
   FORM
   ============================================================ */

.form-group {

    margin-bottom: 17px;
}

.label-row {

    display: flex;

    justify-content: space-between;

    margin-bottom: 7px;
}

label {

    font-size: 12px;

    color: #b8c1d0;

    font-weight: 600;
}

.hint {

    color: #667184;

    font-size: 10px;
}

input,
textarea {

    width: 100%;

    border: 1px solid rgba(255,255,255,0.09);

    background:
        rgba(0,0,0,0.18);

    color: white;

    border-radius: 10px;

    padding: 11px 12px;

    outline: none;

    font-family: inherit;

    font-size: 12px;

    transition: 0.2s;
}

input:focus,
textarea:focus {

    border-color:
        rgba(109,124,255,0.7);

    box-shadow:
        0 0 0 3px rgba(109,124,255,0.08);
}

textarea {

    min-height: 72px;

    resize: vertical;

    line-height: 1.5;
}

.input-row {

    display: grid;

    grid-template-columns:
        1fr 130px;

    gap: 10px;
}


/* ============================================================
   MESSAGE VARIANTS
   ============================================================ */

.message-tabs {

    display: grid;

    grid-template-columns:
        repeat(5, 1fr);

    gap: 6px;

    margin-bottom: 10px;
}

.message-tab {

    border: 1px solid var(--border);

    background: rgba(255,255,255,0.025);

    color: var(--muted);

    padding: 7px;

    border-radius: 7px;

    font-size: 10px;

    cursor: pointer;
}

.message-tab.active {

    color: white;

    background:
        rgba(109,124,255,0.14);

    border-color:
        rgba(109,124,255,0.3);
}


/* ============================================================
   BUTTONS
   ============================================================ */

.button-row {

    display: flex;

    flex-wrap: wrap;

    gap: 8px;

    margin-top: 20px;
}

button {

    font-family: inherit;
}

.btn {

    border: 1px solid var(--border);

    background: rgba(255,255,255,0.04);

    color: white;

    padding: 10px 15px;

    border-radius: 9px;

    cursor: pointer;

    font-size: 12px;

    font-weight: 700;

    transition: 0.2s;
}

.btn:hover {

    transform: translateY(-1px);

    background: rgba(255,255,255,0.07);
}

.btn-primary {

    border: none;

    background:
        linear-gradient(
            135deg,
            var(--accent),
            var(--accent2)
        );

    box-shadow:
        0 10px 25px rgba(109,124,255,0.18);
}

.btn-success {

    color: #8df0b1;

    border-color:
        rgba(34,197,94,0.2);

    background:
        rgba(34,197,94,0.08);
}

.btn-warning {

    color: #ffd27a;

    border-color:
        rgba(245,158,11,0.2);

    background:
        rgba(245,158,11,0.08);
}

.btn-danger {

    color: #ff9b9b;

    border-color:
        rgba(239,68,68,0.2);

    background:
        rgba(239,68,68,0.08);
}

.btn:disabled {

    opacity: 0.4;

    cursor: not-allowed;

    transform: none;
}


/* ============================================================
   PROGRESS
   ============================================================ */

.progress-container {

    margin-bottom: 20px;
}

.progress-info {

    display: flex;

    justify-content: space-between;

    font-size: 11px;

    color: var(--muted);

    margin-bottom: 8px;
}

.progress-bar {

    height: 8px;

    background:
        rgba(255,255,255,0.06);

    border-radius: 100px;

    overflow: hidden;
}

.progress-fill {

    height: 100%;

    width: 0%;

    border-radius: inherit;

    background:
        linear-gradient(
            90deg,
            var(--accent),
            var(--accent2)
        );

    transition:
        width 0.4s ease;
}


/* ============================================================
   CURRENT SEND
   ============================================================ */

.current-send {

    border:
        1px solid rgba(109,124,255,0.12);

    background:
        rgba(109,124,255,0.045);

    border-radius: 12px;

    padding: 14px;

    margin-bottom: 18px;
}

.current-label {

    font-size: 10px;

    text-transform: uppercase;

    letter-spacing: 1px;

    color: #727e95;

    margin-bottom: 7px;
}

.current-number {

    font-size: 17px;

    font-weight: 700;
}

.current-message {

    color: var(--muted);

    font-size: 11px;

    margin-top: 5px;
}


/* ============================================================
   LOG
   ============================================================ */

.log {

    height: 350px;

    overflow-y: auto;

    padding: 14px;

    background:
        rgba(0,0,0,0.20);

    border-radius: 10px;

    border: 1px solid var(--border);

    font-family:
        ui-monospace,
        SFMono-Regular,
        Menlo,
        monospace;

    font-size: 10px;
}

.log-row {

    display: flex;

    gap: 10px;

    padding: 7px 0;

    border-bottom:
        1px solid rgba(255,255,255,0.035);

    line-height: 1.5;
}

.log-time {

    color: #4e596b;

    flex-shrink: 0;
}

.log-success {

    color: #79dfa0;
}

.log-error {

    color: #ff8585;
}

.log-warning {

    color: #f8c66e;
}

.log-info {

    color: #aab4c5;
}


/* ============================================================
   CONTACT PANEL
   ============================================================ */

.contact-count {

    font-size: 42px;

    font-weight: 800;

    letter-spacing: -2px;
}

.contact-label {

    color: var(--muted);

    font-size: 11px;

    margin-top: 3px;
}

.contact-list {

    margin-top: 18px;

    max-height: 230px;

    overflow-y: auto;
}

.contact {

    display: flex;

    align-items: center;

    gap: 9px;

    padding: 9px 0;

    border-bottom:
        1px solid rgba(255,255,255,0.04);

    font-size: 11px;

    color: #aeb7c7;
}

.contact-dot {

    width: 5px;
    height: 5px;

    background: #667085;

    border-radius: 50%;
}


/* ============================================================
   TOAST
   ============================================================ */

.toast-container {

    position: fixed;

    right: 22px;

    bottom: 22px;

    z-index: 100;
}

.toast {

    min-width: 270px;

    max-width: 380px;

    padding: 13px 15px;

    margin-top: 8px;

    border-radius: 11px;

    border: 1px solid var(--border);

    background:
        rgba(18,23,33,0.96);

    box-shadow: var(--shadow);

    font-size: 12px;

    animation:
        toastIn 0.25s ease;
}

@keyframes toastIn {

    from {
        opacity: 0;
        transform: translateY(10px);
    }

    to {
        opacity: 1;
        transform: translateY(0);
    }
}


/* ============================================================
   RESPONSIVE
   ============================================================ */

.mobile-menu {

    display: none;
}

@media (max-width: 1050px) {

    .stats {

        grid-template-columns:
            repeat(2, 1fr);
    }

    .content-grid {

        grid-template-columns: 1fr;
    }
}

@media (max-width: 750px) {

    .sidebar {

        transform:
            translateX(-100%);

        transition: 0.25s;
    }

    .sidebar.open {

        transform:
            translateX(0);
    }

    .main {

        margin-left: 0;

        width: 100%;

        padding: 18px;
    }

    .mobile-menu {

        display: block;

        border: 1px solid var(--border);

        background: rgba(255,255,255,0.04);

        color: white;

        border-radius: 9px;

        padding: 9px 11px;

        cursor: pointer;
    }

    .topbar {

        gap: 12px;
    }

    .page-title {

        font-size: 22px;
    }

    .top-status {

        display: none;
    }

    .stats {

        grid-template-columns: 1fr 1fr;
    }

    .input-row {

        grid-template-columns: 1fr;
    }
}

@media (max-width: 470px) {

    .stats {

        grid-template-columns: 1fr;
    }

    .message-tabs {

        grid-template-columns:
            repeat(5, 1fr);
    }

    .message-tab {

        padding: 6px 2px;

        font-size: 8px;
    }
}

</style>

</head>


<body>


<div class="app">


<!-- =========================================================
     SIDEBAR
========================================================= -->

<aside class="sidebar" id="sidebar">

    <div class="brand">

        <div class="brand-icon">
            B
        </div>

        <div>

            <div class="brand-name">
                BLASSS TING
            </div>

            <div class="brand-small">
                SMS COMMAND CENTER
            </div>

        </div>

    </div>


    <div class="nav-label">
        Workspace
    </div>


    <button class="nav-item active">

        <span class="nav-icon">◈</span>

        Dashboard

    </button>


    <button
        class="nav-item"
        onclick="scrollToSection('composer')"
    >

        <span class="nav-icon">✉</span>

        Campaign

    </button>


    <button
        class="nav-item"
        onclick="scrollToSection('contactsCard')"
    >

        <span class="nav-icon">♙</span>

        Contacts

    </button>


    <button
        class="nav-item"
        onclick="scrollToSection('activityCard')"
    >

        <span class="nav-icon">≡</span>

        Activity

    </button>


    <div class="nav-label" style="margin-top:18px;">
        System
    </div>


    <button
        class="nav-item"
        onclick="focusApiKey()"
    >

        <span class="nav-icon">⚿</span>

        API Settings

    </button>


    <div class="sidebar-bottom">

        <div class="server-card">

            <div class="server-row">

                <span class="status-dot"></span>

                <strong>Server Online</strong>

            </div>

            <div class="server-text">

                Flask service running

            </div>

        </div>

    </div>

</aside>


<!-- =========================================================
     MAIN
========================================================= -->

<main class="main">


    <div class="topbar">

        <div style="display:flex;align-items:center;gap:12px;">

            <button
                class="mobile-menu"
                onclick="toggleSidebar()"
            >
                ☰
            </button>

            <div>

                <div class="page-title">
                    SMS Dashboard
                </div>

                <div class="page-subtitle">
                    Manage your messaging campaign from one place.
                </div>

            </div>

        </div>


        <div class="top-status">

            <span class="status-dot"></span>

            System operational

        </div>

    </div>


    <!-- =====================================================
         STATS
    ====================================================== -->

    <section class="stats">


        <div class="stat">

            <div class="stat-top">

                <span>Recipients</span>

                <div class="stat-icon">
                    ♙
                </div>

            </div>

            <div
                class="stat-value"
                id="statTotal"
            >
                0
            </div>

            <div class="stat-description">
                Loaded from contacts.txt
            </div>

        </div>


        <div class="stat">

            <div class="stat-top">

                <span>Sent</span>

                <div class="stat-icon">
                    ✓
                </div>

            </div>

            <div
                class="stat-value"
                id="statSent"
            >
                0
            </div>

            <div class="stat-description">
                Successful messages
            </div>

        </div>


        <div class="stat">

            <div class="stat-top">

                <span>Failed</span>

                <div class="stat-icon">
                    !
                </div>

            </div>

            <div
                class="stat-value"
                id="statFailed"
            >
                0
            </div>

            <div class="stat-description">
                Unsuccessful attempts
            </div>

        </div>


        <div class="stat">

            <div class="stat-top">

                <span>Remaining</span>

                <div class="stat-icon">
                    ◷
                </div>

            </div>

            <div
                class="stat-value"
                id="statRemaining"
            >
                0
            </div>

            <div class="stat-description">
                Recipients remaining
            </div>

        </div>


    </section>


    <!-- =====================================================
         MAIN CONTENT
    ====================================================== -->

    <div class="content-grid">


        <!-- =================================================
             COMPOSER
        ================================================== -->

        <section
            class="card"
            id="composer"
        >

            <div class="card-header">

                <div>

                    <div class="card-title">
                        Campaign Composer
                    </div>

                    <div class="card-subtitle">
                        Configure your Textbelt campaign.
                    </div>

                </div>

            </div>


            <div class="card-body">


                <!-- API KEY -->

                <div
                    class="form-group"
                    id="apiSection"
                >

                    <div class="label-row">

                        <label>
                            Textbelt API Key
                        </label>

                        <span class="hint">
                            Stored only in this browser session
                        </span>

                    </div>

                    <div class="input-row">

                        <input
                            id="apiKey"
                            type="password"
                            placeholder="Enter your Textbelt API key"
                        >

                        <button
                            class="btn"
                            onclick="testApiKey()"
                        >
                            Test API Key
                        </button>

                    </div>

                </div>


                <!-- DELAY -->

                <div class="form-group">

                    <div class="label-row">

                        <label>
                            Delay Between Messages
                        </label>

                        <span class="hint">
                            1–3600 seconds
                        </span>

                    </div>

                    <input
                        id="delay"
                        type="number"
                        min="1"
                        max="3600"
                        value="2"
                    >

                </div>


                <!-- MESSAGE VARIANTS -->

                <div class="form-group">

                    <div class="label-row">

                        <label>
                            Message Variants
                        </label>

                        <span class="hint">
                            Random variant selected per recipient
                        </span>

                    </div>


                    <div class="message-tabs">

                        <button
                            class="message-tab active"
                            onclick="showMessage(1)"
                            id="tab1"
                        >
                            MSG 1
                        </button>

                        <button
                            class="message-tab"
                            onclick="showMessage(2)"
                            id="tab2"
                        >
                            MSG 2
                        </button>

                        <button
                            class="message-tab"
                            onclick="showMessage(3)"
                            id="tab3"
                        >
                            MSG 3
                        </button>

                        <button
                            class="message-tab"
                            onclick="showMessage(4)"
                            id="tab4"
                        >
                            MSG 4
                        </button>

                        <button
                            class="message-tab"
                            onclick="showMessage(5)"
                            id="tab5"
                        >
                            MSG 5
                        </button>

                    </div>


                    <textarea
                        id="message1"
                        placeholder="Enter message variant 1..."
                    ></textarea>

                    <textarea
                        id="message2"
                        placeholder="Enter message variant 2..."
                        style="display:none;"
                    ></textarea>

                    <textarea
                        id="message3"
                        placeholder="Enter message variant 3..."
                        style="display:none;"
                    ></textarea>

                    <textarea
                        id="message4"
                        placeholder="Enter message variant 4..."
                        style="display:none;"
                    ></textarea>

                    <textarea
                        id="message5"
                        placeholder="Enter message variant 5..."
                        style="display:none;"
                    ></textarea>

                </div>


                <!-- BUTTONS -->

                <div class="button-row">

                    <button
                        class="btn btn-primary"
                        id="startBtn"
                        onclick="startCampaign()"
                    >
                        ▶ Start Campaign
                    </button>

                    <button
                        class="btn btn-warning"
                        id="pauseBtn"
                        onclick="pauseCampaign()"
                        disabled
                    >
                        ⏸ Pause
                    </button>

                    <button
                        class="btn btn-success"
                        id="resumeBtn"
                        onclick="resumeCampaign()"
                        disabled
                    >
                        ▶ Resume
                    </button>

                    <button
                        class="btn btn-danger"
                        id="stopBtn"
                        onclick="stopCampaign()"
                        disabled
                    >
                        ■ Stop
                    </button>

                </div>

            </div>

        </section>


        <!-- =================================================
             CONTACTS
        ================================================== -->

        <section
            class="card"
            id="contactsCard"
        >

            <div class="card-header">

                <div>

                    <div class="card-title">
                        Contacts
                    </div>

                    <div class="card-subtitle">
                        Current contacts.txt file
                    </div>

                </div>

                <button
                    class="btn"
                    onclick="loadContacts()"
                >
                    ↻ Reload
                </button>

            </div>


            <div class="card-body">

                <div
                    class="contact-count"
                    id="contactCount"
                >
                    0
                </div>

                <div class="contact-label">
                    recipients loaded
                </div>


                <div
                    class="contact-list"
                    id="contactList"
                >

                    <div class="contact">
                        <span class="contact-dot"></span>
                        Loading contacts...
                    </div>

                </div>

            </div>

        </section>


        <!-- =================================================
             PROGRESS
        ================================================== -->

        <section class="card">

            <div class="card-header">

                <div>

                    <div class="card-title">
                        Campaign Progress
                    </div>

                    <div class="card-subtitle">
                        Live delivery status
                    </div>

                </div>

            </div>


            <div class="card-body">


                <div class="current-send">

                    <div class="current-label">
                        Current recipient
                    </div>

                    <div
                        class="current-number"
                        id="currentNumber"
                    >
                        Waiting...
                    </div>

                    <div
                        class="current-message"
                        id="currentMessage"
                    >
                        No active campaign
                    </div>

                </div>


                <div class="progress-container">

                    <div class="progress-info">

                        <span>
                            Overall progress
                        </span>

                        <strong
                            id="progressText"
                        >
                            0%
                        </strong>

                    </div>

                    <div class="progress-bar">

                        <div
                            class="progress-fill"
                            id="progressFill"
                        ></div>

                    </div>

                </div>


                <div class="button-row">

                    <button
                        class="btn"
                        onclick="loadContacts()"
                    >
                        ↻ Refresh Contacts
                    </button>

                </div>

            </div>

        </section>


        <!-- =================================================
             ACTIVITY
        ================================================== -->

        <section
            class="card"
            id="activityCard"
        >

            <div class="card-header">

                <div>

                    <div class="card-title">
                        Live Activity
                    </div>

                    <div class="card-subtitle">
                        Campaign events and delivery results
                    </div>

                </div>

            </div>


            <div class="card-body">

                <div
                    class="log"
                    id="log"
                >

                    <div class="log-row">

                        <span class="log-time">
                            --:--
                        </span>

                        <span class="log-info">
                            Waiting for campaign activity...
                        </span>

                    </div>

                </div>

            </div>

        </section>


    </div>


</main>


</div>


<div
    class="toast-container"
    id="toastContainer"
></div>


<script>

/* ============================================================
   UI HELPERS
============================================================ */

function showToast(message, type = "info") {

    const container =
        document.getElementById("toastContainer");

    const toast =
        document.createElement("div");

    toast.className = "toast";

    toast.innerHTML = message;

    container.appendChild(toast);

    setTimeout(() => {

        toast.style.opacity = "0";

        toast.style.transform =
            "translateY(10px)";

        setTimeout(() => toast.remove(), 250);

    }, 3500);
}


function toggleSidebar() {

    document
        .getElementById("sidebar")
        .classList.toggle("open");
}


function scrollToSection(id) {

    const element =
        document.getElementById(id);

    if (element) {

        element.scrollIntoView({
            behavior: "smooth"
        });
    }

    document
        .getElementById("sidebar")
        .classList.remove("open");
}


function focusApiKey() {

    document
        .getElementById("apiKey")
        .focus();

    document
        .getElementById("sidebar")
        .classList.remove("open");
}


/* ============================================================
   MESSAGE TABS
============================================================ */

function showMessage(number) {

    for (let i = 1; i <= 5; i++) {

        const textarea =
            document.getElementById(
                "message" + i
            );

        const tab =
            document.getElementById(
                "tab" + i
            );

        if (i === number) {

            textarea.style.display = "block";

            tab.classList.add("active");

        } else {

            textarea.style.display = "none";

            tab.classList.remove("active");
        }
    }
}


/* ============================================================
   CONTACTS
============================================================ */

async function loadContacts() {

    try {

        const response =
            await fetch("/api/contacts");

        const data =
            await response.json();

        if (!data.success) {

            showToast(
                data.error || "Unable to load contacts.",
                "error"
            );

            return;
        }

        document.getElementById(
            "contactCount"
        ).textContent = data.count;

        document.getElementById(
            "statTotal"
        ).textContent = data.count;


        const list =
            document.getElementById(
                "contactList"
            );

        if (!data.contacts.length) {

            list.innerHTML = `
                <div class="contact">
                    <span class="contact-dot"></span>
                    No contacts found.
                </div>
            `;

            return;
        }


        const visible =
            data.contacts.slice(0, 100);

        list.innerHTML =
            visible.map(number => `
                <div class="contact">
                    <span class="contact-dot"></span>
                    ${escapeHtml(number)}
                </div>
            `).join("");


        if (data.contacts.length > 100) {

            list.innerHTML += `
                <div class="contact">
                    <span class="contact-dot"></span>
                    + ${data.contacts.length - 100}
                    more contacts
                </div>
            `;
        }


    } catch (error) {

        showToast(
            "Could not connect to the server.",
            "error"
        );
    }
}


/* ============================================================
   API KEY TEST
============================================================ */

async function testApiKey() {

    const apiKey =
        document
            .getElementById("apiKey")
            .value
            .trim();

    if (!apiKey) {

        showToast(
            "Enter your Textbelt API key first.",
            "warning"
        );

        return;
    }


    showToast(
        "Testing Textbelt API key..."
    );


    try {

        const response =
            await fetch(
                "/api/test-key",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({
                        api_key: apiKey
                    })
                }
            );


        const data =
            await response.json();


        if (data.success) {

            showToast(
                "✓ API key is working.",
                "success"
            );

        } else {

            showToast(
                "API key test failed: " +
                (data.error || "Unknown error"),
                "error"
            );
        }


    } catch (error) {

        showToast(
            "Unable to test API key.",
            "error"
        );
    }
}


/* ============================================================
   START
============================================================ */

async function startCampaign() {

    const apiKey =
        document
            .getElementById("apiKey")
            .value
            .trim();

    const delay =
        document
            .getElementById("delay")
            .value;


    const messages = {

        message1:
            document
                .getElementById("message1")
                .value
                .trim(),

        message2:
            document
                .getElementById("message2")
                .value
                .trim(),

        message3:
            document
                .getElementById("message3")
                .value
                .trim(),

        message4:
            document
                .getElementById("message4")
                .value
                .trim(),

        message5:
            document
                .getElementById("message5")
                .value
                .trim()
    };


    if (!apiKey) {

        showToast(
            "Enter your Textbelt API key.",
            "warning"
        );

        return;
    }


    const activeMessages =
        Object.values(messages)
            .filter(Boolean);


    if (!activeMessages.length) {

        showToast(
            "Enter at least one message.",
            "warning"
        );

        return;
    }


    try {

        const response =
            await fetch(
                "/api/start",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({
                        api_key: apiKey,
                        delay: delay,

                        ...messages
                    })
                }
            );


        const data =
            await response.json();


        if (!data.success) {

            showToast(
                data.error ||
                "Unable to start campaign.",
                "error"
            );

            return;
        }


        showToast(
            "✓ Campaign started.",
            "success"
        );


        updateButtons(true);

    } catch (error) {

        showToast(
            "Unable to connect to server.",
            "error"
        );
    }
}


/* ============================================================
   PAUSE
============================================================ */

async function pauseCampaign() {

    const response =
        await fetch(
            "/api/pause",
            {
                method: "POST"
            }
        );

    const data =
        await response.json();

    if (data.success) {

        showToast(
            "Campaign paused.",
            "warning"
        );

    } else {

        showToast(
            data.error || "Unable to pause.",
            "error"
        );
    }
}


/* ============================================================
   RESUME
============================================================ */

async function resumeCampaign() {

    const response =
        await fetch(
            "/api/resume",
            {
                method: "POST"
            }
        );

    const data =
        await response.json();

    if (data.success) {

        showToast(
            "Campaign resumed.",
            "success"
        );

    } else {

        showToast(
            data.error || "Unable to resume.",
            "error"
        );
    }
}


/* ============================================================
   STOP
============================================================ */

async function stopCampaign() {

    const response =
        await fetch(
            "/api/stop",
            {
                method: "POST"
            }
        );

    const data =
        await response.json();

    if (data.success) {

        showToast(
            "Stopping campaign...",
            "warning"
        );

    } else {

        showToast(
            data.error || "Unable to stop.",
            "error"
        );
    }
}


/* ============================================================
   BUTTON STATE
============================================================ */

function updateButtons(running, paused = false) {

    document.getElementById(
        "startBtn"
    ).disabled = running;

    document.getElementById(
        "pauseBtn"
    ).disabled = !running || paused;

    document.getElementById(
        "resumeBtn"
    ).disabled = !running || !paused;

    document.getElementById(
        "stopBtn"
    ).disabled = !running;
}


/* ============================================================
   STATUS POLLING
============================================================ */

async function updateStatus() {

    try {

        const response =
            await fetch(
                "/api/status",
                {
                    cache: "no-store"
                }
            );

        const data =
            await response.json();


        document.getElementById(
            "statTotal"
        ).textContent = data.total;

        document.getElementById(
            "statSent"
        ).textContent = data.sent;

        document.getElementById(
            "statFailed"
        ).textContent = data.failed;

        document.getElementById(
            "statRemaining"
        ).textContent = data.remaining;


        document.getElementById(
            "progressText"
        ).textContent =
            data.progress + "%";


        document.getElementById(
            "progressFill"
        ).style.width =
            data.progress + "%";


        document.getElementById(
            "currentNumber"
        ).textContent =
            data.current_number ||
            "Waiting...";


        if (data.current_message_number) {

            document.getElementById(
                "currentMessage"
            ).textContent =
                "Using Message " +
                data.current_message_number;

        } else {

            document.getElementById(
                "currentMessage"
            ).textContent =
                data.running
                    ? "Preparing..."
                    : "No active campaign";
        }


        updateButtons(
            data.running,
            data.paused
        );


        renderLogs(data.logs);


    } catch (error) {

        // Don't spam the user with connection errors
        // during normal page initialization.
    }
}


/* ============================================================
   LOG RENDERING
============================================================ */

function renderLogs(logs) {

    const container =
        document.getElementById("log");


    if (!logs || !logs.length) {

        container.innerHTML = `
            <div class="log-row">

                <span class="log-time">
                    --:--
                </span>

                <span class="log-info">
                    Waiting for campaign activity...
                </span>

            </div>
        `;

        return;
    }


    container.innerHTML =
        logs.map(entry => `

            <div class="log-row">

                <span class="log-time">
                    ${escapeHtml(entry.time)}
                </span>

                <span class="log-${escapeHtml(entry.level)}">
                    ${escapeHtml(entry.message)}
                </span>

            </div>

        `).join("");
}


/* ============================================================
   ESCAPE HTML
============================================================ */

function escapeHtml(value) {

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


/* ============================================================
   INITIALIZATION
============================================================ */

loadContacts();

updateStatus();

setInterval(
    updateStatus,
    1000
);

</script>

</body>

</html>
"""


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    print("")
    print("=" * 60)
    print(f"  {APP_NAME}")
    print("  SMS Dashboard")
    print("=" * 60)
    print("")
    print(f"  Server: http://{HOST}:{PORT}")
    print("")
    print("  Contacts:", CONTACT_FILE)
    print("")
    print("=" * 60)
    print("")

    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        threaded=True
    )
