#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$APP_DIR/.env"
VENV_DIR="$APP_DIR/venv"

echo "=== BLASSS TING INSTALLER ==="
command -v python3 >/dev/null 2>&1 || { echo "Python 3 is required."; exit 1; }
[ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$APP_DIR/requirements.txt"
if [ ! -f "$ENV_FILE" ]; then
  FLASK_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  read -r -p "Administrator username [admin]: " ADMIN_USERNAME
  ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
  while true; do
    read -r -s -p "Administrator password: " ADMIN_PASSWORD; echo
    [ -n "$ADMIN_PASSWORD" ] || { echo "Password cannot be empty."; continue; }
    read -r -s -p "Confirm administrator password: " ADMIN_PASSWORD_CONFIRM; echo
    [ "$ADMIN_PASSWORD" = "$ADMIN_PASSWORD_CONFIRM" ] && break
    echo "Passwords do not match."
  done
  ADMIN_PASSWORD_HASH="$(ADMIN_PASSWORD="$ADMIN_PASSWORD" "$VENV_DIR/bin/python" -c 'import os; from werkzeug.security import generate_password_hash; print(generate_password_hash(os.environ["ADMIN_PASSWORD"]))')"
  unset ADMIN_PASSWORD ADMIN_PASSWORD_CONFIRM
  read -r -p "Textbelt API key: " TEXTBELT_API_KEY
  cat > "$ENV_FILE" <<EOT
BLASSS_TING_ADMIN_USERNAME=$ADMIN_USERNAME
BLASSS_TING_ADMIN_PASSWORD_HASH=$ADMIN_PASSWORD_HASH
FLASK_SECRET_KEY=$FLASK_SECRET
TEXTBELT_API_KEY=$TEXTBELT_API_KEY
CONTACT_FILE=contacts.txt
SESSION_COOKIE_SECURE=false
EOT
  chmod 600 "$ENV_FILE"
else
  echo ".env already exists; keeping it."
fi
[ -f "$APP_DIR/contacts.txt" ] || touch "$APP_DIR/contacts.txt"
chmod 600 "$APP_DIR/contacts.txt"
touch "$APP_DIR/.gitignore"
for item in ".env" ".env.*" "contacts.txt" "venv/" ".venv/" "__pycache__/" "*.pyc"; do
  grep -qxF "$item" "$APP_DIR/.gitignore" || echo "$item" >> "$APP_DIR/.gitignore"
done
"$VENV_DIR/bin/python" -c "import main"
echo "Installation complete. Start with: source venv/bin/activate && python main.py"
echo "Do not expose port 5000 publicly."
