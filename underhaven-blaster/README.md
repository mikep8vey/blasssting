# UnderHaven Blaster — HTTPS/IP Edition

UnderHaven Blaster is a Flask dashboard for authorized SMS communications through Textbelt.

## Installation

Run the installer as root on an Ubuntu/Debian VPS:

```bash
cd /root
unzip underhaven-blaster-https-complete.zip
cd underhaven-blaster
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

Certbot 5.4+ is required for IP-address certificates. The installer checks for the actual `--ip-address` option and does not rely on the older false-negative help check.

## Final installer output

At successful completion the installer prints both URLs, for example:

```text
HTTP URL:  http://162.33.179.232/
HTTPS URL: https://162.33.179.232/
```

HTTP remains available for the ACME challenge and redirects normal visitors to HTTPS.

## Contact-list upload

After login, the dashboard has a **Contact List Upload** section. Upload a UTF-8 `.txt` file containing one authorized recipient per line.

Uploading a file replaces the current `contacts.txt`. The upload is authenticated, limited to 1 MB, and the stored file is readable/writable only by the application service account.

Do not upload sensitive contact lists to GitHub. The production `contacts.txt` is intentionally excluded from the repository.

## Textbelt

The dashboard can:

- save and verify a Textbelt API key;
- check the remaining Textbelt quota;
- load the server-side contact list;
- upload a replacement contact list;
- enter up to five message variants;
- set a delay of at least one second;
- start/stop an authorized campaign;
- display sent/failed progress.

Use only for recipients who have authorized the communication and follow Textbelt's terms and applicable law. Do not use the software for spam, unsolicited bulk messaging, or rate-limit avoidance.

## HTTPS certificate renewal

IP certificates are short-lived. Certbot schedules renewal, and the installer also creates a systemd renewal timer:

```bash
systemctl status underhaven-blaster-cert-renew.timer
```

Test renewal with:

```bash
certbot renew --dry-run
```

The renewal deploy hook reloads Nginx after a successful renewal.

## Service checks

```bash
systemctl status underhaven-blaster
systemctl status nginx
systemctl status underhaven-blaster-cert-renew.timer
```

Logs:

```bash
journalctl -u underhaven-blaster -f
journalctl -u nginx -f
```

## Security notes

Do not commit these files to GitHub:

```text
.env
contacts.txt
venv/
```

The application listens on `127.0.0.1:5000`, not `0.0.0.0:5000`. Nginx is the public entry point.
