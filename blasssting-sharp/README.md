# BLASSS TING

Private Flask dashboard for authorized SMS communications through Textbelt.

## Ubuntu installation

```bash
git clone https://github.com/mikep8vey/blasssting.git
cd blasssting
chmod +x install.sh
./install.sh
```

The installer creates the virtual environment, installs dependencies, generates a Flask secret, creates the administrator password hash, asks for the Textbelt API key, and creates `.env`.

## Run

```bash
source venv/bin/activate
python main.py
```

The application should listen on `http://127.0.0.1:5000`.

Do not expose port 5000 directly to the Internet. Use Nginx and a firewall.

## Private files

Never commit `.env`, `contacts.txt`, or `venv/`. `.env.example` is safe to commit.

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

```bash
sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
sudo ufw status verbose
```

Do not allow `5000/tcp` publicly.

## systemd

```bash
sudo cp deploy/systemd/blasssting.service /etc/systemd/system/blasssting.service
sudo systemctl daemon-reload
sudo systemctl enable blasssting
sudo systemctl start blasssting
sudo systemctl status blasssting
```

Logs: `sudo journalctl -u blasssting -f`

Use the software only for communications you are authorized to send and comply with applicable consent, carrier, and Textbelt requirements.
