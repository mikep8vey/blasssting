#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$APP_DIR/venv"
ENV_FILE="$APP_DIR/.env"
SERVICE_NAME="underhaven-blaster"
NGINX_SITE="/etc/nginx/sites-available/$SERVICE_NAME"
ACME_ROOT="/var/www/underhaven-blaster"

echo "================================================"
echo "       UNDERHAVEN BLASTER INSTALLER"
echo "================================================"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root."
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv nginx curl snapd

if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$APP_DIR/requirements.txt"

# Only the local administrator is configured during installation.
if [ ! -f "$ENV_FILE" ]; then
  SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  read -r -p "Administrator username [admin]: " USERNAME
  USERNAME="${USERNAME:-admin}"

  while true; do
    read -r -s -p "Administrator password: " PASSWORD
    echo
    read -r -s -p "Confirm administrator password: " CONFIRM
    echo
    if [ -n "$PASSWORD" ] && [ "$PASSWORD" = "$CONFIRM" ]; then
      break
    fi
    echo "Passwords must be non-empty and match."
  done

  HASH="$(PASSWORD="$PASSWORD" "$VENV/bin/python" -c 'import os; from werkzeug.security import generate_password_hash; print(generate_password_hash(os.environ["PASSWORD"]))')"
  unset PASSWORD CONFIRM

  cat > "$ENV_FILE" <<EOF
BLASSS_TING_ADMIN_USERNAME=$USERNAME
BLASSS_TING_ADMIN_PASSWORD_HASH=$HASH
FLASK_SECRET_KEY=$SECRET
TEXTBELT_API_KEY=
CONTACT_FILE=contacts.txt
SESSION_COOKIE_SECURE=true
PORT=5000
EOF
  chmod 600 "$ENV_FILE"
fi

touch "$APP_DIR/contacts.txt"
chmod 600 "$APP_DIR/contacts.txt"

cp "$APP_DIR/deploy/systemd/underhaven-blaster.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

SERVER_IP="${SERVER_IP:-}"
if [ -z "$SERVER_IP" ]; then
  SERVER_IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org || true)"
fi
if [ -z "$SERVER_IP" ]; then
  read -r -p "Public server IPv4 address: " SERVER_IP
fi

if ! [[ "$SERVER_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "Invalid IPv4 address: $SERVER_IP"
  exit 1
fi

mkdir -p "$ACME_ROOT/.well-known/acme-challenge"
chown -R www-data:www-data "$ACME_ROOT"

# Temporary HTTP-only Nginx configuration for the ACME HTTP-01 challenge.
cat > "$NGINX_SITE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $SERVER_IP;

    location ^~ /.well-known/acme-challenge/ {
        root $ACME_ROOT;
        default_type text/plain;
        try_files \$uri =404;
    }

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -sf "$NGINX_SITE" "/etc/nginx/sites-enabled/$SERVICE_NAME"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx
systemctl restart nginx

echo
echo "Installing/updating Certbot..."

systemctl enable --now snapd.socket || true
sleep 3

if [ ! -x /snap/bin/certbot ]; then
  snap install certbot --classic
fi

CERTBOT="/snap/bin/certbot"
"$CERTBOT" --version

if ! "$CERTBOT" certonly --help 2>&1 | grep -q -- "--ip-address"; then
  echo "The installed Certbot does not support IP-address certificates."
  echo "Certbot 5.4+ is required."
  exit 1
fi

echo
echo "Requesting Let's Encrypt IP certificate for $SERVER_IP..."

"$CERTBOT" certonly \
  --webroot \
  --webroot-path "$ACME_ROOT" \
  --ip-address "$SERVER_IP" \
  --preferred-profile shortlived \
  --agree-tos \
  --register-unsafely-without-email \
  --non-interactive \
  --cert-name "$SERVER_IP"

# Replace the temporary config with HTTPS and an HTTP-to-HTTPS redirect.
cat > "$NGINX_SITE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $SERVER_IP;

    location ^~ /.well-known/acme-challenge/ {
        root $ACME_ROOT;
        default_type text/plain;
        try_files \$uri =404;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $SERVER_IP;

    ssl_certificate /etc/letsencrypt/live/$SERVER_IP/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$SERVER_IP/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;
    ssl_session_tickets off;

    add_header Strict-Transport-Security "max-age=31536000" always;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
EOF

nginx -t
systemctl reload nginx

# IP certificates are short-lived. Check twice per day and reload Nginx after renewal.
cat > /etc/systemd/system/underhaven-blaster-cert-renew.service <<EOF
[Unit]
Description=Renew UnderHaven Blaster IP TLS certificate

[Service]
Type=oneshot
ExecStart=$CERTBOT renew --cert-name $SERVER_IP --deploy-hook "systemctl reload nginx"
EOF

cat > /etc/systemd/system/underhaven-blaster-cert-renew.timer <<EOF
[Unit]
Description=UnderHaven Blaster TLS renewal check

[Timer]
OnBootSec=10min
OnUnitActiveSec=12h
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now underhaven-blaster-cert-renew.timer

# Never expose Flask port 5000.
if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null || true
  ufw allow 80/tcp >/dev/null || true
  ufw allow 443/tcp >/dev/null || true
  ufw deny 5000/tcp >/dev/null || true

  if ! ufw status | grep -q "Status: active"; then
    echo
    read -r -p "Enable UFW now? [Y/n]: " ENABLE_UFW
    ENABLE_UFW="${ENABLE_UFW:-Y}"
    if [[ "$ENABLE_UFW" =~ ^[Yy]$ ]]; then
      ufw --force enable
    fi
  fi
fi

echo
echo "================================================"
echo "       UNDERHAVEN BLASTER READY"
echo "================================================"
echo
echo "HTTPS: https://$SERVER_IP/"
echo "Flask: 127.0.0.1:5000 (private)"
echo "Port 5000 is NOT opened publicly."
echo
echo "Certificate: /etc/letsencrypt/live/$SERVER_IP/"
echo
echo "Renewal timer:"
echo "  systemctl status underhaven-blaster-cert-renew.timer"
echo
echo "The Textbelt API key is configured later in the dashboard."
