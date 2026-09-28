# UnderHaven Blaster — HTTPS/IP Certificate Edition

This package installs UnderHaven Blaster with Nginx, systemd, and HTTPS.

## What it does

The installer configures:

- Python virtual environment
- Flask application
- administrator login
- Nginx reverse proxy
- systemd application service
- a publicly trusted Let's Encrypt certificate for the VPS public IP
- automatic short-lived certificate renewal
- optional UFW firewall rules
- Flask bound privately to `127.0.0.1:5000`

The Textbelt API key is **not requested during installation**. It is entered after logging into the dashboard.

## HTTPS without a domain

Let's Encrypt now supports publicly trusted IP-address certificates. IP certificates use the short-lived profile and are valid for roughly six days, so automated renewal is required.

Certbot 5.4+ is required for IP-address certificates using webroot mode. The installer uses Certbot's webroot method and configures Nginx manually because the Certbot Nginx installer does not yet install IP certificates automatically.

## Install

```bash
cd /root
unzip underhaven-blaster-ssl-complete.zip
cd underhaven-blaster
chmod +x install.sh
./install.sh
```

The installer asks only:

```text
Administrator username [admin]:
Administrator password:
Confirm administrator password:
```

It detects the public IPv4 address. If detection fails, it asks for the public IPv4 address.

When finished:

```text
https://YOUR_SERVER_IP/
```

## Network layout

```text
Internet
   |
   +-- :80  --> Nginx --> HTTPS redirect
   |
   +-- :443 --> Nginx --> 127.0.0.1:5000 --> UnderHaven Blaster
```

Port `5000` is not intended to be public.

## Textbelt

After logging in, configure the Textbelt API key from the dashboard. The server verifies it through Textbelt's quota endpoint and stores it server-side in `.env`.

Do not commit `.env` or `contacts.txt` to GitHub.

## Renewal

Check the timer:

```bash
systemctl status underhaven-blaster-cert-renew.timer
```

Test:

```bash
certbot renew --dry-run
```

The timer checks twice per day and reloads Nginx after a successful renewal.

## Services

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

## Firewall

The installer does not open port `5000`. It allows SSH, HTTP, and HTTPS. If UFW is inactive, it asks whether to enable it.

## SMS compliance

Use the application only for authorized communications and follow Textbelt's terms and applicable law. Do not use it for spam, unsolicited bulk messaging, or rate-limit avoidance.
