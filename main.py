import os
import json
import time
import random
import threading
import urllib.request
import urllib.parse
import urllib.error
from flask import Flask, request, jsonify, render_template_string

APP_NAME = "SMS Sender"
TEXTBELT_URL = "https://textbelt.com/text"
CONTACT_FILE = "contacts.txt"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTACT_PATH = os.path.join(BASE_DIR, CONTACT_FILE)

app = Flask(__name__)

# ------------------------------------------------------------
# Campaign state
# ------------------------------------------------------------

state = {
    "running": False,
    "paused": False,
    "stop_requested": False,

    "total": 0,
    "current": 0,
    "sent": 0,
    "failed": 0,

    "delay": 2,
    "messages": [],
    "contacts": [],

    "current_phone": "",
    "current_message_number": 0,

    "logs": []
}

state_lock = threading.Lock()
worker_thread = None


# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

def add_log(message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    with state_lock:
        state["logs"].append(f"[{timestamp}] {message}")

        # Keep the log from growing forever
        if len(state["logs"]) > 500:
            state["logs"] = state["logs"][-500:]


# ------------------------------------------------------------
# Contacts
# ------------------------------------------------------------

def normalize_phone(phone):
    phone = phone.strip()

    # Remove common formatting characters
    phone = (
        phone.replace(" ", "")
        .replace("(", "")
        .replace(")", "")
        .replace(".", "")
        .replace("-", "")
    )

    if not phone.startswith("+"):
        return None

    digits = phone[1:]

    if not digits.isdigit():
        return None

    if not 8 <= len(digits) <= 15:
        return None

    return "+" + digits


def load_contacts():
    if not os.path.exists(CONTACT_PATH):
        return [], []

    valid = []
    invalid = []
    seen = set()

    with open(CONTACT_PATH, "r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            original = line.strip()

            if not original:
                continue

            phone = normalize_phone(original)

            if phone is None:
                invalid.append({
                    "line": line_number,
                    "value": original
                })
                continue

            if phone in seen:
                continue

            seen.add(phone)
            valid.append(phone)

    return valid, invalid


# ------------------------------------------------------------
# Textbelt
# ------------------------------------------------------------

def send_sms(phone, message, api_key):
    data = urllib.parse.urlencode({
        "phone": phone,
        "message": message,
        "key": api_key
    }).encode("utf-8")

    request_obj = urllib.request.Request(
        TEXTBELT_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request_obj, timeout=30) as response:
            raw = response.read().decode("utf-8")
            result = json.loads(raw)

            return result

    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8")
            return {
                "success": False,
                "error": f"HTTP {error.code}: {body}"
            }
        except Exception:
            return {
                "success": False,
                "error": f"HTTP {error.code}"
            }

    except Exception as error:
        return {
            "success": False,
            "error": str(error)
        }


# ------------------------------------------------------------
# Campaign worker
# ------------------------------------------------------------

def campaign_worker(api_key):
    global worker_thread

    with state_lock:
        contacts = list(state["contacts"])
        messages = list(state["messages"])
        delay = state["delay"]

    add_log(f"Campaign started with {len(contacts)} contacts.")

    for index, phone in enumerate(contacts):

        # Stop requested
        with state_lock:
            if state["stop_requested"]:
                state["running"] = False
                state["paused"] = False
                state["current_phone"] = ""
                add_log("Campaign stopped.")
                return

        # Pause loop
        while True:
            with state_lock:
                paused = state["paused"]
                stopped = state["stop_requested"]

            if stopped:
                with state_lock:
                    state["running"] = False
                    state["paused"] = False
                    state["current_phone"] = ""

                add_log("Campaign stopped.")
                return

            if not paused:
                break

            time.sleep(0.5)

        # Select a random message
        message_number = random.randrange(len(messages))
        selected_message = messages[message_number]

        with state_lock:
            state["current"] = index + 1
            state["current_phone"] = phone
            state["current_message_number"] = message_number + 1

        add_log(
            f"Sending to {phone} using Message {message_number + 1}..."
        )

        result = send_sms(
            phone,
            selected_message,
            api_key
        )

        success = result.get("success", False)

        if success:
            with state_lock:
                state["sent"] += 1

            add_log(
                f"✓ Sent to {phone} "
                f"(Message {message_number + 1})"
            )

        else:
            with state_lock:
                state["failed"] += 1

            error_message = (
                result.get("error")
                or result.get("message")
                or "Unknown error"
            )

            add_log(
                f"✗ Failed for {phone}: {error_message}"
            )

        # Wait before next recipient
        if index < len(contacts) - 1:

            remaining = delay

            while remaining > 0:

                with state_lock:
                    stopped = state["stop_requested"]
                    paused = state["paused"]

                if stopped:
                    with state_lock:
                        state["running"] = False
                        state["paused"] = False
                        state["current_phone"] = ""

                    add_log("Campaign stopped.")
                    return

                if paused:
                    time.sleep(0.5)
                    continue

                sleep_time = min(0.5, remaining)
                time.sleep(sleep_time)
                remaining -= sleep_time

    with state_lock:
        state["running"] = False
        state["paused"] = False
        state["stop_requested"] = False
        state["current_phone"] = ""

    add_log("✓ Campaign completed.")

    worker_thread = None


# ------------------------------------------------------------
# Web interface
# ------------------------------------------------------------

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>SMS Sender</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f4f6f8;
    color: #17202a;
    font-family: Arial, sans-serif;
}

.container {
    width: min(1100px, 94%);
    margin: 30px auto;
}

.header {
    background: white;
    padding: 25px;
    border-radius: 14px;
    margin-bottom: 20px;
    box-shadow: 0 3px 12px rgba(0,0,0,.08);
}

.header h1 {
    margin: 0 0 6px;
}

.header p {
    margin: 0;
    color: #6b7280;
}

.card {
    background: white;
    border-radius: 14px;
    padding: 22px;
    margin-bottom: 20px;
    box-shadow: 0 3px 12px rgba(0,0,0,.08);
}

label {
    display: block;
    font-weight: bold;
    margin-bottom: 7px;
}

input, textarea {
    width: 100%;
    padding: 11px;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    font-size: 15px;
}

textarea {
    min-height: 100px;
    resize: vertical;
}

.message {
    margin-bottom: 15px;
}

.grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 15px;
}

button {
    border: none;
    border-radius: 8px;
    padding: 11px 17px;
    cursor: pointer;
    font-weight: bold;
    margin-right: 7px;
    margin-top: 10px;
}

.primary {
    background: #2563eb;
    color: white;
}

.success {
    background: #16a34a;
    color: white;
}

.warning {
    background: #d97706;
    color: white;
}

.danger {
    background: #dc2626;
    color: white;
}

.secondary {
    background: #e5e7eb;
    color: #111827;
}

.stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
}

.stat {
    background: #f8fafc;
    border-radius: 10px;
    padding: 18px;
}

.stat-title {
    color: #6b7280;
    font-size: 13px;
}

.stat-value {
    font-size: 27px;
    font-weight: bold;
    margin-top: 5px;
}

.progress {
    width: 100%;
    height: 18px;
    background: #e5e7eb;
    border-radius: 20px;
    overflow: hidden;
    margin-top: 15px;
}

.progress-bar {
    height: 100%;
    width: 0%;
    background: #2563eb;
    transition: width .3s;
}

.log {
    background: #111827;
    color: #d1fae5;
    padding: 15px;
    border-radius: 9px;
    height: 300px;
    overflow-y: auto;
    font-family: monospace;
    font-size: 13px;
    white-space: pre-wrap;
}

.status {
    font-weight: bold;
    margin-bottom: 10px;
}

@media(max-width: 700px) {
    .grid,
    .stats {
        grid-template-columns: 1fr;
    }
}

</style>
</head>

<body>

<div class="container">

    <div class="header">
        <h1>SMS Sender</h1>
        <p>Ubuntu VPS SMS Campaign Dashboard</p>
    </div>

    <div class="card">

        <h2>Textbelt API</h2>

        <label>API Key</label>

        <input
            id="apiKey"
            type="password"
            placeholder="Enter your Textbelt API key"
        >

        <button class="secondary" onclick="testApiKey()">
            Test API Key
        </button>

        <div id="apiResult"></div>

    </div>

    <div class="card">

        <h2>Contacts</h2>

        <p>
            Contacts are automatically loaded from
            <strong>contacts.txt</strong> on the VPS.
        </p>

        <button class="secondary" onclick="reloadContacts()">
            Reload Contacts
        </button>

        <p id="contactInfo"></p>

    </div>

    <div class="card">

        <h2>Messages</h2>

        <div class="message">
            <label>Message 1</label>
            <textarea id="message1"></textarea>
        </div>

        <div class="message">
            <label>Message 2</label>
            <textarea id="message2"></textarea>
        </div>

        <div class="message">
            <label>Message 3</label>
            <textarea id="message3"></textarea>
        </div>

        <div class="message">
            <label>Message 4</label>
            <textarea id="message4"></textarea>
        </div>

        <div class="message">
            <label>Message 5</label>
            <textarea id="message5"></textarea>
        </div>

    </div>

    <div class="card">

        <h2>Campaign Settings</h2>

        <div class="grid">

            <div>
                <label>Delay between messages (seconds)</label>

                <input
                    id="delay"
                    type="number"
                    min="1"
                    max="3600"
                    value="2"
                >
            </div>

        </div>

        <button class="primary" onclick="startCampaign()">
            Start Campaign
        </button>

        <button class="warning" onclick="pauseCampaign()">
            Pause
        </button>

        <button class="success" onclick="resumeCampaign()">
            Resume
        </button>

        <button class="danger" onclick="stopCampaign()">
            Stop
        </button>

    </div>

    <div class="card">

        <h2>Campaign Progress</h2>

        <div id="status" class="status">
            Ready
        </div>

        <div class="stats">

            <div class="stat">
                <div class="stat-title">Total</div>
                <div id="total" class="stat-value">0</div>
            </div>

            <div class="stat">
                <div class="stat-title">Processed</div>
                <div id="current" class="stat-value">0</div>
            </div>

            <div class="stat">
                <div class="stat-title">Sent</div>
                <div id="sent" class="stat-value">0</div>
            </div>

            <div class="stat">
                <div class="stat-title">Failed</div>
                <div id="failed" class="stat-value">0</div>
            </div>

        </div>

        <div class="progress">
            <div id="progressBar" class="progress-bar"></div>
        </div>

        <p id="currentPhone"></p>

    </div>

    <div class="card">

        <h2>Activity Log</h2>

        <div id="log" class="log"></div>

    </div>

</div>

<script>

async function api(url, options = {}) {

    const response = await fetch(url, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {})
        }
    });

    return response.json();
}


async function reloadContacts() {

    const result = await api("/api/contacts");

    document.getElementById("contactInfo").innerText =
        `${result.valid} valid contacts loaded. ${result.invalid} invalid lines.`;
}


async function testApiKey() {

    const apiKey =
        document.getElementById("apiKey").value.trim();

    if (!apiKey) {
        alert("Enter your Textbelt API key.");
        return;
    }

    const result = await api("/api/test-key", {
        method: "POST",
        body: JSON.stringify({
            api_key: apiKey
        })
    });

    document.getElementById("apiResult").innerText =
        result.message || JSON.stringify(result);
}


async function startCampaign() {

    const apiKey =
        document.getElementById("apiKey").value.trim();

    const messages = [];

    for (let i = 1; i <= 5; i++) {

        const value =
            document.getElementById("message" + i).value.trim();

        if (value) {
            messages.push(value);
        }
    }

    const delay =
        Number(document.getElementById("delay").value);

    if (!apiKey) {
        alert("Enter your Textbelt API key.");
        return;
    }

    if (messages.length === 0) {
        alert("Enter at least one message.");
        return;
    }

    if (!delay || delay < 1) {
        alert("Delay must be at least 1 second.");
        return;
    }

    if (!confirm(
        "Start the campaign for all loaded contacts? " +
        "Make sure every recipient has appropriate consent."
    )) {
        return;
    }

    const result = await api("/api/start", {
        method: "POST",
        body: JSON.stringify({
            api_key: apiKey,
            messages: messages,
            delay: delay
        })
    });

    alert(result.message || JSON.stringify(result));
}


async function pauseCampaign() {

    await api("/api/pause", {
        method: "POST"
    });

}


async function resumeCampaign() {

    await api("/api/resume", {
        method: "POST"
    });

}


async function stopCampaign() {

    if (!confirm("Stop the current campaign?")) {
        return;
    }

    await api("/api/stop", {
        method: "POST"
    });

}


async function updateStatus() {

    try {

        const result = await api("/api/status");

        document.getElementById("total").innerText =
            result.total;

        document.getElementById("current").innerText =
            result.current;

        document.getElementById("sent").innerText =
            result.sent;

        document.getElementById("failed").innerText =
            result.failed;

        let percentage = 0;

        if (result.total > 0) {
            percentage =
                (result.current / result.total) * 100;
        }

        document.getElementById("progressBar").style.width =
            percentage + "%";

        let status = "Ready";

        if (result.running && result.paused) {
            status = "PAUSED";
        }
        else if (result.running) {
            status = "RUNNING";
        }
        else if (result.current >= result.total && result.total > 0) {
            status = "COMPLETED";
        }

        document.getElementById("status").innerText =
            status;

        if (result.current_phone) {

            document.getElementById("currentPhone").innerText =
                "Current recipient: " +
                result.current_phone +
                " | Message variant: " +
                result.current_message_number;

        } else {

            document.getElementById("currentPhone").innerText = "";

        }

        document.getElementById("log").innerText =
            result.logs.join("\n");

        const log = document.getElementById("log");
        log.scrollTop = log.scrollHeight;

    }
    catch(error) {

        console.error(error);

    }
}


setInterval(updateStatus, 1000);

reloadContacts();
updateStatus();

</script>

</body>
</html>
"""


# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/contacts")
def contacts_api():

    contacts, invalid = load_contacts()

    return jsonify({
        "valid": len(contacts),
        "invalid": len(invalid),
        "contacts": contacts
    })


@app.route("/api/test-key", methods=["POST"])
def test_key():

    data = request.get_json() or {}

    api_key = data.get("api_key", "").strip()

    if not api_key:
        return jsonify({
            "success": False,
            "message": "API key is required."
        }), 400

    # Textbelt quota endpoint
    url = (
        "https://textbelt.com/quota/"
        + urllib.parse.quote(api_key)
    )

    try:

        with urllib.request.urlopen(url, timeout=15) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

        return jsonify({
            "success": True,
            "message": "API key check completed.",
            "data": result
        })

    except Exception as error:

        return jsonify({
            "success": False,
            "message": str(error)
        }), 400


@app.route("/api/start", methods=["POST"])
def start_campaign():

    global worker_thread

    data = request.get_json() or {}

    api_key = data.get("api_key", "").strip()
    messages = data.get("messages", [])
    delay = data.get("delay", 2)

    if not api_key:
        return jsonify({
            "success": False,
            "message": "API key is required."
        }), 400

    if not isinstance(messages, list):
        return jsonify({
            "success": False,
            "message": "Invalid messages."
        }), 400

    messages = [
        str(message).strip()
        for message in messages
        if str(message).strip()
    ]

    if not messages:
        return jsonify({
            "success": False,
            "message": "At least one message is required."
        }), 400

    try:
        delay = float(delay)
    except Exception:
        return jsonify({
            "success": False,
            "message": "Invalid delay."
        }), 400

    if delay < 1:
        return jsonify({
            "success": False,
            "message": "Delay must be at least 1 second."
        }), 400

    contacts, invalid = load_contacts()

    if not contacts:
        return jsonify({
            "success": False,
            "message": "No valid contacts found."
        }), 400

    with state_lock:

        if state["running"]:
            return jsonify({
                "success": False,
                "message": "A campaign is already running."
            }), 400

        state["running"] = True
        state["paused"] = False
        state["stop_requested"] = False

        state["total"] = len(contacts)
        state["current"] = 0
        state["sent"] = 0
        state["failed"] = 0

        state["delay"] = delay
        state["messages"] = messages
        state["contacts"] = contacts

        state["current_phone"] = ""
        state["current_message_number"] = 0
        state["logs"] = []

    add_log(
        f"Loaded {len(contacts)} contacts."
    )

    if invalid:
        add_log(
            f"Ignored {len(invalid)} invalid contact lines."
        )

    worker_thread = threading.Thread(
        target=campaign_worker,
        args=(api_key,),
        daemon=True
    )

    worker_thread.start()

    return jsonify({
        "success": True,
        "message": "Campaign started."
    })


@app.route("/api/pause", methods=["POST"])
def pause_campaign():

    with state_lock:

        if not state["running"]:
            return jsonify({
                "success": False,
                "message": "No campaign is running."
            })

        state["paused"] = True

    add_log("Campaign paused.")

    return jsonify({
        "success": True,
        "message": "Campaign paused."
    })


@app.route("/api/resume", methods=["POST"])
def resume_campaign():

    with state_lock:

        if not state["running"]:
            return jsonify({
                "success": False,
                "message": "No campaign is running."
            })

        state["paused"] = False

    add_log("Campaign resumed.")

    return jsonify({
        "success": True,
        "message": "Campaign resumed."
    })


@app.route("/api/stop", methods=["POST"])
def stop_campaign():

    with state_lock:

        if not state["running"]:
            return jsonify({
                "success": False,
                "message": "No campaign is running."
            })

        state["stop_requested"] = True

    return jsonify({
        "success": True,
        "message": "Stop requested."
    })


@app.route("/api/status")
def status():

    with state_lock:
        result = dict(state)

    # Don't expose API key or other sensitive information.
    result.pop("messages", None)
    result.pop("contacts", None)

    return jsonify(result)


# ------------------------------------------------------------
# Start server
# ------------------------------------------------------------

if __name__ == "__main__":

    print()
    print("=" * 50)
    print("SMS Sender")
    print("=" * 50)
    print("Server running on port 5000")
    print("Open http://YOUR_VPS_IP:5000")
    print("=" * 50)
    print()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )