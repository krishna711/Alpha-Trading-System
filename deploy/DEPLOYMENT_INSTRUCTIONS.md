# Production Server Deployment Guide — Alpha Trading System (Multi-User Edition)

This guide covers end-to-end production deployment of the **Alpha Trading System** on an Ubuntu Linux VPS (AWS EC2, DigitalOcean, Hetzner, Linode) with 24/7 `systemd` services and Nginx reverse proxy.

---

## 🔄 Key Changes Since Multi-User & Security Upgrades

1. **Git-Based Deployment:** Clone directly from `https://github.com/krishna711/Alpha-Trading-System.git` into `/home/ubuntu/trading_system` (no manual SCP needed).
2. **Zero Manual Credential JSON Editing:**
   - `config/trading_config.json` is excluded from Git for security and is **auto-created** from `config/trading_config.example.json` on first boot.
   - Each user's Dhan credentials (Client ID, PIN, TOTP Secret, API keys), stock allocations, and trading modes (`PAPER` / `LIVE`) are stored isolated inside SQLite (`state/trading_system.db`) and managed directly through the Web UI.
3. **Built-In Web Authentication (No Nginx Basic Auth Needed):**
   - Because the app now includes built-in multi-user login & role-based session authentication, **do NOT enable Nginx `auth_basic` / `.htpasswd`** (it would prompt for two separate logins).
   - Strongly recommended: Enable free HTTPS/SSL via Let's Encrypt (`certbot`) to encrypt login passwords and session cookies in transit.
4. **Immediate Default Admin Password Change:**
   - First boot creates default admin `admin` / `admin123`. Change this password immediately after launching!
5. **Holiday Calendar Management:**
   - Pre-seeded with 2026 holidays; admins can add/delete holidays anytime via the **📅 Holidays** modal in the header.

---

## 1. Server Requirements

- **OS:** Ubuntu 22.04 LTS or Ubuntu 24.04 LTS (x86_64 or ARM64)
- **Specs:** 1 vCPU, 1–2 GB RAM (e.g., AWS `t3.micro` / `t4g.small`, DigitalOcean Basic Droplet)
- **Firewall Ports:** `22` (SSH), `80` (HTTP), `443` (HTTPS)

---

## 2. Step-by-Step Server Setup

### Step 1: SSH Into Your Server & Update Packages
```bash
ssh ubuntu@YOUR_SERVER_IP
sudo apt update && sudo apt upgrade -y
```

### Step 2: Set System Timezone to IST (`Asia/Kolkata`)
> **Important:** Required so the **09:00 AM auto-login**, **09:15–15:30 market hours**, and **hourly bar evaluations** run on Indian Standard Time.

```bash
sudo timedatectl set-timezone Asia/Kolkata
timedatectl
```
Confirm `Time zone: Asia/Kolkata (IST, +0530)`. *(Both `systemd` unit files also enforce `TZ=Asia/Kolkata`.)*

### Step 3: Install Python, Git, and Tools
```bash
sudo apt install python3 python3-pip python3-venv git curl ufw nginx -y
```

### Step 4: Clone the Repository & Install Python Dependencies
Clone directly into `/home/ubuntu/trading_system` (matching the `WorkingDirectory` in the `.service` files):
```bash
cd /home/ubuntu
git clone https://github.com/krishna711/Alpha-Trading-System.git trading_system
cd /home/ubuntu/trading_system
```

Create and activate the virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 5: Initialize Database & Change Default Admin Password
Trigger database initialization and verify the default setup:
```bash
python manage.py list-users
```
You should see the seeded `admin` user. Immediately set a strong production password for `admin`:
```bash
python manage.py change-password admin "YourStrongAdminPassword123!"
```
*(Or change it in the browser right after logging in via the **🔑 Password** button in the top header.)*

---

## 3. Configure 24/7 Background Services (`systemd`)

Both service files are already configured in `deploy/` for `/home/ubuntu/trading_system`:
- [`deploy/alpha-web.service`](alpha-web.service) — Runs the FastAPI Web UI on `127.0.0.1:8000`
- [`deploy/alpha-bot.service`](alpha-bot.service) — Runs the multi-user trading daemon (`run_trading_bot.py`)

### Enable & Start Both Services:
```bash
cd /home/ubuntu/trading_system
sudo cp deploy/alpha-web.service /etc/systemd/system/
sudo cp deploy/alpha-bot.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable alpha-web alpha-bot
sudo systemctl start alpha-web alpha-bot
```

Verify both services are `active (running)`:
```bash
sudo systemctl status alpha-web
sudo systemctl status alpha-bot
```

> **Note:** If you run the server as a user other than `ubuntu`, edit `User=ubuntu` and `/home/ubuntu/trading_system` inside both `.service` files before copying them.

---

## 4. Configure Nginx Reverse Proxy & Optional HTTPS

### Option A: Quick Setup Using Provided Config
Copy the Nginx template (already updated without `auth_basic` conflict):
```bash
sudo cp /home/ubuntu/trading_system/deploy/nginx_trading_bot.conf /etc/nginx/sites-available/trading_bot
```
Edit `server_name`:
```bash
sudo nano /etc/nginx/sites-available/trading_bot
# Replace `your_domain_or_server_ip` with your VPS IP or domain (e.g., trade.yourdomain.com)
```
Enable the site and restart Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/trading_bot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

### Option B: Enable Free HTTPS / SSL (Recommended if Using a Domain)
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d trade.yourdomain.com
```

### Configure Firewall (UFW):
```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

---

## 5. First-Time Login & Post-Deploy Checklist

1. Open **`http://YOUR_SERVER_IP`** (or `https://trade.yourdomain.com`) in your browser.
2. Log in as **`admin`** with the password you set in Step 5 (or `admin123` if unchanged — change immediately!).
3. **Verify Holidays:** Click **📅 Holidays** in the header to inspect/add upcoming market holidays.
4. **Configure Dhan Auto-Login:**
   - Switch to the desired user (or your admin profile) using the header dropdown.
   - Click **Dhan Login ⚙**, enter Client ID, 6-digit PIN, and TOTP Secret, check **Enable Daily 09:00 AM Auto-Login**, save, and click **🔑 Test Login Now**.
5. **Create Traders (Optional):** Return to **All Users (Overview)** ➔ click **+ Create New User** to add accounts for other traders. Each trader gets isolated Dhan credentials, capital, stocks, and PnL curve.
6. **Go Live When Ready:** Users stay safely in **`PAPER`** mode until switched to **`🔴 LIVE TRADING`** in the top header.

---

## 6. Deploying Future Updates (`git pull`) & Maintenance

Whenever new updates are pushed to GitHub:
```bash
cd /home/ubuntu/trading_system
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart alpha-web alpha-bot
```
*(Your `state/trading_system.db`, `config/trading_config.json`, and `logs/` are safely `.gitignore`d, so `git pull` will never overwrite your users, credentials, positions, or trade history.)*

### Useful Server Commands:
| Task | Command |
|---|---|
| **Watch Trading Bot Logs** | `tail -f /home/ubuntu/trading_system/logs/systemd_bot.log` |
| **Watch Web UI Logs** | `tail -f /home/ubuntu/trading_system/logs/systemd_web.log` |
| **View Systemd Journal** | `journalctl -u alpha-bot -u alpha-web -f` |
| **Restart Both Services** | `sudo systemctl restart alpha-web alpha-bot` |
| **List All Users & PnL** | `source venv/bin/activate && python manage.py list-users` |
| **Reset User Password** | `source venv/bin/activate && python manage.py change-password <user> <newpass>` |
| **View Market Holidays** | `source venv/bin/activate && python manage.py holidays` |
| **Force Test Signal Scan** | `source venv/bin/activate && python manage.py scan --force` |
