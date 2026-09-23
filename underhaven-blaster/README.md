# UnderHaven Blaster

UnderHaven Blaster is a Flask-based SMS management dashboard using Textbelt.

## Installation

On Ubuntu:

```bash
cd /root/blasssting
unzip underhaven-blaster.zip
cd underhaven-blaster
chmod +x install.sh
./install.sh
```

The installer asks only for the administrator username and password.

It automatically:

- creates the Python virtual environment
- installs Python dependencies
- generates the Flask secret
- creates `.env`
- creates `contacts.txt`
- installs/configures Nginx
- installs/enables systemd
- proxies Nginx to `127.0.0.1:5000`
- does not open port 5000 publicly
- optionally enables UFW and allows SSH/HTTP/HTTPS

## Textbelt API key

The installer does **not** ask for the Textbelt API key.

After installation, open:

```text
http://YOUR_SERVER_IP/
```

Sign in and enter the Textbelt key in **Textbelt Connection**.

The dashboard verifies the key using Textbelt's quota endpoint and displays the returned remaining quota. Textbelt documents `/quota/<key>` for checking remaining quota without sending an SMS.

The verified key is stored server-side in `.env`, not in browser storage.

## Contacts

Create:

```text
contacts.txt
```

with one authorized recipient per line.

Keep the real file out of GitHub.

## Sending

The dashboard supports up to five message variants. When multiple variants are supplied, one is selected randomly for each contact.

The minimum delay is 1 second. Textbelt advises not exceeding one SMS request per second.

Only send messages to recipients who have opted in and comply with Textbelt's terms and applicable law.

## Service

```bash
systemctl status underhaven-blaster
systemctl restart underhaven-blaster
journalctl -u underhaven-blaster -f
```

## Nginx

```bash
systemctl status nginx
nginx -t
```

The application listens only on:

```text
127.0.0.1:5000
```

Nginx is the public entry point on port 80/443.

Port 5000 should not be opened in the firewall.

## Security

Do not commit:

```text
.env
contacts.txt
venv/
```

The included `.gitignore` protects these files from normal Git tracking.
