# UnderHaven Blaster — HTTPS/IP Edition

UnderHaven Blaster is a Flask dashboard for authorized SMS communications through Textbelt.

## Install directly on a fresh Ubuntu/Debian VPS

The recommended production location is `/opt/underhaven-blaster` so the unprivileged `underhaven` service account does not need access through `/root`.

### Option 1 — download the current GitHub folder with wget

```bash
apt-get update
apt-get install -y wget unzip
mkdir -p /opt/underhaven-blaster
cd /opt/underhaven-blaster
wget -q https://github.com/mikep8vey/blasssting/archive/refs/heads/main.zip -O blasssting-main.zip
unzip -q blasssting-main.zip
cp -a blasssting-main/underhaven-blaster/. .
rm -rf blasssting-main blasssting-main.zip
chmod +x install.sh
./install.sh
```

### Option 2 — Git

```bash
apt-get update
apt-get install -y git
cd /opt
git clone https://github.com/mikep8vey/blasssting.git
cd /opt/blasssting/underhaven-blaster
chmod +x install.sh
./install.sh
```

The installer asks only for:

```text
Administrator username [admin]:
Administrator password:
Confirm administrator password:
```

The Textbelt API key is **not** requested during installation. Enter and verify it after logging into the dashboard.

## What the installer configures

- Python virtual environment
- UnderHaven Blaster systemd service
- Nginx reverse proxy
- Let's Encrypt IP-address TLS certificate
- Automatic certificate renewal
- HTTP port 80
- HTTPS port 443
- Flask privately on `127.0.0.1:5000`
- UFW rules that do not expose port 5000
- Dedicated unprivileged `underhaven` service account

Certbot 5.4+ is required for IP-address certificates. The installer checks the actual `--ip-address` option.

## Final installer output

At successful completion the installer prints both URLs, using the server's detected public IPv4 address:

```text
HTTP URL:  http://YOUR.SERVER.IP/
HTTPS URL: https://YOUR.SERVER.IP/
```

HTTP redirects normal visitors to HTTPS. Port 5000 remains private.

## Dashboard features

- Secure administrator login
- Textbelt API-key entry and verification after installation
- Live Textbelt quota check
- UTF-8 `.txt` contact-list upload from the dashboard
- One authorized recipient per line
- Up to five message variants
- Random message selection per recipient
- Minimum one-second delay between requests
- Per-recipient result table showing the phone number, accepted/failed result, Textbelt text ID, delivery status, error, and quota used when reported by Textbelt
- Campaign-wide quota-before, quota-after, and actual quota change
- Manual delivery-status refresh through Textbelt's `/status/<textId>` endpoint

### Contact lists

Uploading a contact list replaces the current server-side `contacts.txt`. The upload is authenticated and limited to 1 MB.

Do not put production contact lists or `.env` files in GitHub.

## Why quota can be higher than the number of phone numbers

Textbelt does not necessarily charge exactly one quota unit per API request. Textbelt states that quota corresponds to SMS segments. Longer messages can be split into multiple SMS segments, and failed/non-delivered messages can still consume quota. Non-GSM Unicode characters can also reduce the number of characters that fit in one segment.

The dashboard therefore records the quota remaining before and after a campaign and displays the actual quota change returned by Textbelt. Each recipient row also records the quota change when Textbelt supplies it.

See Textbelt's current documentation for the provider's exact quota rules.

## Delivery status

A successful `/text` response means Textbelt accepted the request; it does not necessarily mean the handset has received it. The dashboard stores the returned `textId` and lets you refresh the delivery status. Textbelt reports statuses such as `DELIVERED`, `SENT`, `SENDING`, and `FAILED`.

## HTTPS certificate renewal

IP certificates are short-lived. Certbot schedules renewal automatically. Check it with:

```bash
systemctl list-timers | grep certbot
certbot renew --dry-run
```

## Service checks

```bash
systemctl status underhaven-blaster --no-pager
systemctl status nginx --no-pager
ss -tulpn | grep -E ':80|:443|:5000'
ufw status
```

Expected public listeners are ports 80 and 443. Flask should listen only on `127.0.0.1:5000`.

Logs:

```bash
journalctl -u underhaven-blaster -f
journalctl -u nginx -f
```

## Updating an existing VPS from GitHub

If your production copy is a Git clone and you have not edited files locally:

```bash
cd /opt/blasssting
git pull origin main
cd underhaven-blaster
source venv/bin/activate
python -m pip install -r requirements.txt
systemctl restart underhaven-blaster
systemctl reload nginx
```

If Git reports local changes, do not use `git reset --hard` unless you intentionally want to discard them. Back up the changes first or use `git restore <file>` for a file you want to replace with the GitHub version.

## Security notes

Do not commit these files to GitHub:

```text
.env
contacts.txt
venv/
__pycache__/
```

The application listens on `127.0.0.1:5000`, not `0.0.0.0:5000`. Nginx is the public entry point.

Use only with recipients who have authorized the communication and follow Textbelt's terms and applicable law. The software is not intended for spam, unsolicited bulk messaging, harassment, or rate-limit avoidance.

**Created by @Nendndndndbdjd**

## Public contact importer

The dashboard includes a **Public Contact Importer**. Paste a public `http://` or
`https://` web-page URL and it extracts only:

- Full Name
- Phone Number
- Email

The importer also has a CSV export button.

A proxy is **not required** for normal public web pages. The importer intentionally
does not rotate proxies, bypass CAPTCHAs, defeat access controls, or evade website
rate limits. Some sites, including dynamic Google Maps/Search pages, may return
limited data or block automated requests; that is a limitation of the source site,
not something the importer attempts to circumvent.

Use the importer only for public contact information you are authorized to collect
and process, and comply with the website's terms and applicable privacy/marketing
laws.

## Public Contact Importer

The dashboard includes a **Public Contact Importer**. Enter a public web-page URL or a Google Maps URL and click **Import contacts**. The importer returns only:

- Full Name
- Phone Number
- Email

Google Maps is rendered with a local headless Chromium browser because Maps builds its business results dynamically in JavaScript. A proxy is **not required**. The importer does not rotate proxies, bypass CAPTCHAs, defeat access controls, or attempt to evade rate limits.

For Google Maps search URLs, multiple visible business listings can be returned. Email addresses are supplemented from a business's public website when Maps exposes a website link; many Maps listings do not publish an email address, so a blank Email field is expected in those cases.

The **Export CSV** button exports the currently imported rows with exactly these columns: `Full Name`, `Phone Number`, `Email`.

If Google Maps cannot be rendered, verify that Chromium is installed and that the service has restarted after installation.
