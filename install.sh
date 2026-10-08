#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="underhaven-blaster"
SERVICE_USER="underhaven"
ENV_FILE="$APP_DIR/.env"
VENV="$APP_DIR/venv"
NGINX_SITE="/etc/nginx/sites-available/$SERVICE_NAME"
ACME_ROOT="/var/www/underhaven-blaster"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Run this installer as root."
    exit 1
fi

# If the repository was cloned/downloaded under /root, move the production copy
# to /opt so the unprivileged service account can enter its WorkingDirectory.
case "$APP_DIR" in
    /root|/root/*)
        TARGET_DIR="/opt/underhaven-blaster"
        if [ "$APP_DIR" != "$TARGET_DIR" ]; then
            echo "Application is under /root. Copying it to $TARGET_DIR ..."
            mkdir -p "$TARGET_DIR"
            cp -a "$APP_DIR/." "$TARGET_DIR/"
            exec "$TARGET_DIR/install.sh"
        fi
        ;;
esac

echo
echo "================================================"
echo "       UNDERHAVEN BLASTER INSTALLER"
echo "================================================"
echo

echo "[1/9] Installing system packages..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    nginx \
    curl \
    snapd \
    ufw

# Chromium package names vary by Debian/Ubuntu release.
if ! command -v chromium >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
    apt-get install -y chromium || apt-get install -y chromium-browser
fi

echo "[2/9] Creating Python environment..."
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$APP_DIR/requirements.txt"

# Google Maps renders results client-side. Use the distro Chromium package so
# the unprivileged service account can run the browser without downloading a
# second browser bundle during installation.
CHROMIUM_PATH="$(command -v chromium || command -v chromium-browser || true)"
if [ -z "$CHROMIUM_PATH" ]; then
    echo "ERROR: Chromium was not installed. Google Maps importing requires Chromium."
    exit 1
fi

echo "[3/9] Creating administrator account..."
if [ ! -f "$ENV_FILE" ]; then
    read -r -p "Administrator username [admin]: " ADMIN_USERNAME
    ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"

    while true; do
        read -r -s -p "Administrator password: " ADMIN_PASSWORD
        echo
        read -r -s -p "Confirm administrator password: " ADMIN_PASSWORD_CONFIRM
        echo
        [ -n "$ADMIN_PASSWORD" ] || { echo "Password cannot be empty."; continue; }
        [ "$ADMIN_PASSWORD" = "$ADMIN_PASSWORD_CONFIRM" ] || { echo "Passwords do not match."; continue; }
        break
    done

    FLASK_SECRET_KEY="$($VENV/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    ADMIN_PASSWORD_HASH="$(ADMIN_PASSWORD="$ADMIN_PASSWORD" "$VENV/bin/python" -c 'import os; from werkzeug.security import generate_password_hash; print(generate_password_hash(os.environ["ADMIN_PASSWORD"]))')"
    unset ADMIN_PASSWORD ADMIN_PASSWORD_CONFIRM

    cat > "$ENV_FILE" <<EOF
BLASSS_TING_ADMIN_USERNAME=$ADMIN_USERNAME
BLASSS_TING_ADMIN_PASSWORD_HASH=$ADMIN_PASSWORD_HASH
FLASK_SECRET_KEY=$FLASK_SECRET_KEY
TEXTBELT_API_KEY=
CONTACT_FILE=contacts.txt
SESSION_COOKIE_SECURE=true
PORT=5000
EOF
    chmod 600 "$ENV_FILE"
else
    echo "Existing .env found. Keeping existing configuration."
    ADMIN_USERNAME="$(grep '^BLASSS_TING_ADMIN_USERNAME=' "$ENV_FILE" | cut -d= -f2- || true)"
    ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
fi

touch "$APP_DIR/contacts.txt"
chmod 600 "$APP_DIR/contacts.txt"

echo "[4/9] Creating application service..."
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
chmod 700 "$ENV_FILE"

cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=UnderHaven Blaster
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=CHROMIUM_PATH=$CHROMIUM_PATH
ExecStart=$VENV/bin/python $APP_DIR/main.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "[5/9] Detecting public IPv4 address..."
SERVER_IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org || true)"
if [ -z "$SERVER_IP" ]; then
    read -r -p "Enter this server's public IPv4 address: " SERVER_IP
fi
if ! [[ "$SERVER_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    echo "ERROR: Invalid IPv4 address: $SERVER_IP"
    exit 1
fi

HTTP_URL="http://$SERVER_IP/"
HTTPS_URL="https://$SERVER_IP/"
CERT_PATH="/etc/letsencrypt/live/$SERVER_IP/fullchain.pem"
KEY_PATH="/etc/letsencrypt/live/$SERVER_IP/privkey.pem"

echo "Server IP: $SERVER_IP"

echo "[6/9] Configuring HTTP/Nginx..."
mkdir -p "$ACME_ROOT/.well-known/acme-challenge"
chown -R www-data:www-data "$ACME_ROOT"

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

echo "[7/9] Installing Certbot and requesting IP certificate..."
systemctl enable --now snapd.socket || true
sleep 5
if [ ! -x /snap/bin/certbot ]; then
    snap install certbot --classic
fi
CERTBOT=/snap/bin/certbot
"$CERTBOT" --version

if ! "$CERTBOT" certonly --help all 2>&1 | grep -q -- "--ip-address"; then
    echo "ERROR: Installed Certbot does not expose --ip-address."
    echo "Installed version:"
    "$CERTBOT" --version
    exit 1
fi

if [ ! -f "$CERT_PATH" ] || [ ! -f "$KEY_PATH" ]; then
    "$CERTBOT" certonly \
        --webroot \
        --webroot-path "$ACME_ROOT" \
        --ip-address "$SERVER_IP" \
        --preferred-profile shortlived \
        --agree-tos \
        --register-unsafely-without-email \
        --non-interactive \
        --cert-name "$SERVER_IP"
else
    echo "Existing certificate found for $SERVER_IP."
fi

# Reload Nginx automatically after a successful certificate renewal.
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/underhaven-blaster-nginx.sh <<'EOF'
#!/usr/bin/env bash
systemctl reload nginx
EOF
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/underhaven-blaster-nginx.sh

echo "[8/9] Configuring HTTPS..."
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

    ssl_certificate $CERT_PATH;
    ssl_certificate_key $KEY_PATH;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

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

echo "[9/9] Configuring firewall..."
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw deny 5000/tcp
ufw --force enable

echo
echo "Checking services..."
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "ERROR: UnderHaven Blaster service failed."
    systemctl status "$SERVICE_NAME" --no-pager -l
    exit 1
fi
if ! systemctl is-active --quiet nginx; then
    echo "ERROR: Nginx failed."
    systemctl status nginx --no-pager -l
    exit 1
fi

# Verify the local Flask endpoint and the public HTTPS endpoint before declaring success.
if ! curl -fsS --max-time 10 -o /dev/null http://127.0.0.1:5000/; then
    echo "ERROR: Flask is not responding on 127.0.0.1:5000."
    journalctl -u "$SERVICE_NAME" -n 50 --no-pager
    exit 1
fi
if ! curl -kfsS --max-time 15 -o /dev/null "https://$SERVER_IP/"; then
    echo "ERROR: HTTPS endpoint did not respond."
    nginx -t
    systemctl status nginx --no-pager -l
    exit 1
fi

echo
echo "================================================"
echo "       UNDERHAVEN BLASTER READY"
echo "================================================"
echo
echo "HTTP URL:  $HTTP_URL"
echo "HTTPS URL: $HTTPS_URL"
echo
echo "HTTP automatically redirects to HTTPS."
echo
echo "Flask:"
echo "  127.0.0.1:5000 (PRIVATE)"
echo
echo "Public:"
echo "  80  HTTP"
echo "  443 HTTPS"
echo
echo "Blocked:"
echo "  5000"
echo
echo "Administrator login:"
echo "  $ADMIN_USERNAME"
echo
echo "Textbelt API:"
echo "  Configure it after logging into the dashboard."
echo
echo "SSL certificate:"
echo "  $CERT_PATH"
echo
echo "Certificate renewal:"
echo "  Certbot automatic renewal enabled."
echo
echo "Created by @Nendndndndbdjd"
echo "================================================"
echo
