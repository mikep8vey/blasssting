#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$APP_DIR/venv"
ENV_FILE="$APP_DIR/.env"
SERVICE_NAME="underhaven-blaster"
NGINX_SITE="/etc/nginx/sites-available/$SERVICE_NAME"

echo "=============================================="
echo "       UNDERHAVEN BLASTER INSTALLER"
echo "=============================================="

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required."
  exit 1
fi

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "Python venv support is missing."
  echo "Run: apt update && apt install -y python3-venv"
  exit 1
fi

if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$APP_DIR/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
  SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  read -r -p "Administrator username [admin]: " USERNAME
  USERNAME="${USERNAME:-admin}"

  while true; do
    read -r -s -p "Administrator password: " PASSWORD
    echo
    read -r -s -p "Confirm administrator password: " CONFIRM
    echo
    [ -n "$PASSWORD" ] && [ "$PASSWORD" = "$CONFIRM" ] && break
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
SESSION_COOKIE_SECURE=false
PORT=5000
EOF
  chmod 600 "$ENV_FILE"
fi

touch "$APP_DIR/contacts.txt"
chmod 600 "$APP_DIR/contacts.txt"

# Install Nginx and configure it to proxy public HTTP to localhost:5000.
if ! command -v nginx >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
fi

cp "$APP_DIR/deploy/nginx/underhaven-blaster.conf" "$NGINX_SITE"
ln -sf "$NGINX_SITE" "/etc/nginx/sites-enabled/$SERVICE_NAME"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx
systemctl restart nginx

# Install and enable the application service.
cp "$APP_DIR/deploy/systemd/underhaven-blaster.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# Firewall: 5000 is intentionally NOT opened.
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
else
  echo "UFW is not installed; port 5000 remains bound to localhost only."
fi

echo
echo "=============================================="
echo "       INSTALLATION COMPLETE"
echo "=============================================="
echo
echo "Do NOT enter your Textbelt API key here."
echo
echo "Open the application using:"
echo "  http://YOUR_SERVER_IP/"
echo
echo "Flask remains private at:"
echo "  127.0.0.1:5000"
echo
echo "After logging in, enter the Textbelt API key in"
echo "the Textbelt Connection section and click Save & Verify."
echo
echo "Service:"
echo "  systemctl status $SERVICE_NAME"
echo
echo "Nginx:"
echo "  systemctl status nginx"
echo
