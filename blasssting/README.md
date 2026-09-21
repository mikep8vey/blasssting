# BLASSS TING

Flask administrator dashboard for authorized SMS communications through Textbelt.

## Important

Never commit `.env`, `contacts.txt`, `venv/`, API keys, VPS passwords, SSH private keys, or real recipient lists.

## Installation

```bash
git clone YOUR_GITHUB_REPOSITORY
cd blasssting
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Generate the Flask secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Generate the administrator password hash:

```bash
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('YOUR_PASSWORD'))"
```

Put those values and the Textbelt API key in `.env`.

## Contacts

Create `contacts.txt` on the VPS. Do not commit it.

One authorized recipient per line.

## Security

Flask binds only to `127.0.0.1:5000`. Do not expose port 5000 publicly. Use Nginx as the public reverse proxy.

Use a BLASSS TING administrator password separate from the VPS root password.

## Nginx

```bash
sudo apt update
sudo apt install -y nginx
sudo cp deploy/nginx/blasssting.conf /etc/nginx/sites-available/blasssting
sudo ln -s /etc/nginx/sites-available/blasssting /etc/nginx/sites-enabled/blasssting
sudo nginx -t
sudo systemctl reload nginx
```

Replace `YOUR_DOMAIN_OR_SERVER_IP` in the Nginx configuration.

## Firewall

BLASSS TING does not automatically change UFW.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
sudo ufw status verbose
```

Do not allow TCP port 5000 from the Internet.

## systemd

```bash
sudo cp deploy/systemd/blasssting.service /etc/systemd/system/blasssting.service
sudo systemctl daemon-reload
sudo systemctl enable blasssting
sudo systemctl start blasssting
sudo systemctl status blasssting
```

Logs:

```bash
sudo journalctl -u blasssting -f
```

## Updating

```bash
cd /root/blasssting
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart blasssting
```

## HTTPS

HTTPS is strongly recommended for the public administrator login. After HTTPS is configured, set `SESSION_COOKIE_SECURE=true` in `.env` and restart the service.

Use the application only for lawful, authorized communications and follow applicable consent, carrier, and Textbelt requirements.
