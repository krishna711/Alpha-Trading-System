# Alpha Trading System (Multi-User Institutional Edition)

An institutional-grade algorithmic trading system designed for **Dhan Broker** running the **1-Hour SuperTrend (8, 3.2) + MACD (9, 23, 7)** alpha strategy with complete **multi-user support**, **role-based authentication**, **interactive PnL equity charts**, and **independent broker account routing**.

---

## 🌟 Key Features

1. **🔐 Multi-User Authentication & Security:**
   - Password hashing with salted PBKDF2-HMAC-SHA256.
   - HttpOnly cookie sessions with full CSRF/session protection.
   - Default Administrator account: `admin` / `admin123` (changeable upon deployment).
   - Admin can create unlimited trader accounts from the Web UI or CLI.

2. **👥 Admin Performance Dashboard & Header User Switcher:**
   - Header dropdown allows admins to instantly switch between **"All Users (Overview)"** and any individual user's active dashboard.
   - Overview metrics table displays real-time performance per user: Trading Mode, Auto-Login status, Total Allocated Capital, Open Positions, Realized PnL, Total Trades, and Win Rate %.

3. **📈 Interactive PnL Graph (Equity Curve):**
   - High-resolution visual equity curve powered by Chart.js.
   - Dynamic gradient fill (emerald green for profit, coral red for drawdown).
   - Real-time updates as trades close and hourly mark-to-market prices evolve.

4. **💼 Isolated User Portfolios & Dhan Routing:**
   - Each trader has their own independent stock universe, capital allocations, open positions, and trade history.
   - Each trader can enter their own Dhan Client ID, PIN, TOTP secret, and API keys. Orders execute strictly against that specific trader's Dhan account.

5. **🕒 Automated 09:00 AM Dhan Login (TOTP + PIN):**
   - Automated morning login routine triggers at **09:00 AM IST** across all enabled user accounts.
   - Uses `pyotp` with the user's Google Authenticator secret key and 6-digit PIN to refresh daily access tokens automatically before market open.

6. **⚡ Smart Caching 1-Hour Data Stream:**
   - Ingests free 1-hour candles via Yahoo Finance (`yfinance`).
   - Shared candle cache per cycle prevents duplicate network requests and avoids rate limiting when multiple traders monitor overlapping high-beta tickers (e.g. `BSE`, `MCX`).

7. **🔘 Per-Stock Trading Toggle (Enable / Disable):**
   - Pause or resume strategy execution for any stock with a single click at the beginning of each row without deleting the stock or losing configured capital.

---

## 🌐 Installation & Launching the Web UI Dashboard

```bash
git clone https://github.com/krishna711/Alpha-Trading-System.git
cd Alpha-Trading-System
pip install -r requirements.txt
python manage.py web --port 8000
```
Then open your browser at: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

> On first launch, the system automatically creates `state/trading_system.db` and `config/trading_config.json` from `config/trading_config.example.json`.

### 🔑 Default Credentials:
- **Username:** `admin`
- **Password:** `admin123`

---

## 👤 Using the Multi-User System

### 1. Creating New Users (Admin)
- **Via Web UI:** In the top header or admin view, click **+ Create New User**, enter their username, full name, password, and role (`user` or `admin`).
- **Via CLI:**
  ```bash
  python manage.py create-user trader1 MyPass123 --name "Rahul Sharma"
  ```

### 2. Switching Between Users (Admin Header Switcher)
- Admins will see a **Viewing** User Switcher dropdown in the top header.
- Select **"All Users (Overview)"** to review all active users, their capital allocations, win rates, and PnL.
- Select any individual user (e.g. `trader1`) to view and manage their specific portfolio, stop losses, Dhan credentials, and PnL curve.

### 3. Setting Up User Dhan Broker Credentials
1. Log in as the user (or switch to their view as admin).
2. Click **Dhan Login ⚙** in the header.
3. Enter the user's **Dhan Client ID**, **6-digit PIN**, and **Google Authenticator TOTP Secret**.
4. Check **Enable Daily 09:00 AM Auto-Login** and click **Save Credentials**.
5. Click **🔑 Test Login Now** to verify authentication.

---

## 🛠️ Management CLI Commands (`manage.py`)

| Action | Command |
|---|---|
| **Launch Web UI** | `python manage.py web` |
| **List All Users & Stats** | `python manage.py list-users` |
| **Create New User** | `python manage.py create-user <username> <pass> --name "Name"` |
| **Change User Password** | `python manage.py change-password <username> <new_pass>` |
| **Delete User Account** | `python manage.py delete-user <username>` |
| **View System Status** | `python manage.py status` |
| **View Market Holidays Calendar** | `python manage.py holidays` |
| **Add Market Holiday** | `python manage.py add-holiday 2026-11-10 "Market Holiday"` |
| **Delete Market Holiday** | `python manage.py delete-holiday <id>` |
| **Scan Real-Time Signals** | `python manage.py scan` |
| **Force Off-Market Scan** | `python manage.py scan --force` |
| **Update Capital for a Stock** | `python manage.py set-capital BSE 300000` |
| **Enable/Disable Stock** | `python manage.py toggle-stock MCX` |
| **Add Any New Stock** | `python manage.py add-stock HAL 150000` |
| **Remove a Stock** | `python manage.py remove-stock SHRIRAMFIN` |
| **Toggle 09:00 AM Auto-Login**| `python manage.py toggle-auto-login` |
| **Test Immediate Login** | `python manage.py dhan-login` |
| **Close Active Position** | `python manage.py close-position CDSL` |
| **Reset Paper Positions** | `python manage.py reset-paper` |

---

## 🏖️ Weekend & Holiday Protection

The system is equipped with an integrated **Market Schedule Manager**:
- **Automatic Weekend Detection:** Prevents auto-logins and signal scans on Saturdays and Sundays.
- **Admin Holiday Calendar:** Pre-configured with official NSE/BSE holidays. Admins can add or remove holidays anytime directly from the Web UI (**📅 Holidays** button) or via CLI (`add-holiday`, `delete-holiday`).
- **Bot Idling:** When the market is closed, `run_trading_bot.py` sleeps efficiently without placing orders or burning API limits.
- **Header Status Pill:** Displays live market status in the Web UI: `🟢 Market Open`, `🏖️ Market Holiday: [Name]`, or `🏖️ Market Closed (Weekend)`.

---

## 🚀 Running the Bot Daemon

Run the multi-user trading bot daemon in the background or as a systemd service:

```bash
python run_trading_bot.py
```

The daemon automatically:
1. Checks for weekends and market holidays; sleeps if market is off.
2. Fires morning auto-login at **09:00 AM IST** across all configured user accounts on active trading days.
3. Evaluates hourly candle closes at **10:15, 11:15, 12:15, 13:15, 14:15, 15:15 IST** for each active user.
4. Monitors intraday stop losses every **60 seconds** across all users' open positions.
