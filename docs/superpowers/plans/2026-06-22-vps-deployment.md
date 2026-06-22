# VPS Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the `auto_checking_transcript` Telegram bot to Ubuntu VPS `45.80.69.22` as a systemd service alongside the existing `friday-reminder` bot.

**Architecture:** Private GitHub repo cloned via SSH Deploy Key; Python venv created on server; bot runs as a systemd service with `Restart=on-failure`; updates delivered via `deploy.sh` script committed to the repo.

**Tech Stack:** Ubuntu VPS, Python 3, systemd, git (SSH), python-telegram-bot, google-genai, python-dotenv.

## Global Constraints

- Never touch `/opt/friday-reminder/` or its systemd unit
- `.env` is never committed to git — always created manually on the server
- WorkingDirectory for the service must be `/opt/auto_checking_transcript` (required for `from src import ...`)
- Service name: `auto-checking-transcript`
- Python venv path: `/opt/auto_checking_transcript/.venv`

---

### Task 1: Commit `deploy.sh` to the repository

**Files:**
- Create: `deploy.sh` (repo root)

**Interfaces:**
- Produces: `deploy.sh` available after `git pull` on VPS

- [ ] **Step 1: Create `deploy.sh` locally**

Create `deploy.sh` in the repo root with this exact content:

```bash
#!/bin/bash
set -e
cd /opt/auto_checking_transcript
git pull
.venv/bin/pip install -r requirements.txt -q
systemctl restart auto-checking-transcript
systemctl status auto-checking-transcript --no-pager
```

- [ ] **Step 2: Commit and push**

```bash
git add deploy.sh
git commit -m "add deploy.sh for VPS update workflow"
git push origin main
```

- [ ] **Step 3: Verify on GitHub**

Open `https://github.com/bullbashechka/auto_checking_transcript` in browser — `deploy.sh` should be visible in the repo root.

---

### Task 2: Generate SSH Deploy Key on VPS

**Where:** Run all commands over SSH: `ssh root@45.80.69.22`

- [ ] **Step 1: SSH into the VPS**

```bash
ssh root@45.80.69.22
```

- [ ] **Step 2: Generate ed25519 key**

```bash
ssh-keygen -t ed25519 -C "auto-checking-transcript-deploy" -f ~/.ssh/auto_checking_deploy -N ""
```

Expected: Two files created — `~/.ssh/auto_checking_deploy` (private) and `~/.ssh/auto_checking_deploy.pub` (public).

- [ ] **Step 3: Copy the public key**

```bash
cat ~/.ssh/auto_checking_deploy.pub
```

Copy the full output (starts with `ssh-ed25519 ...`).

- [ ] **Step 4: Add to SSH config so git uses this key**

```bash
cat >> ~/.ssh/config << 'EOF'

Host github-auto-checking
    HostName github.com
    User git
    IdentityFile ~/.ssh/auto_checking_deploy
EOF
```

- [ ] **Step 5: Add Deploy Key on GitHub**

1. Open `https://github.com/bullbashechka/auto_checking_transcript/settings/keys`
2. Click **Add deploy key**
3. Title: `VPS auto-checking-transcript`
4. Key: paste the public key from Step 3
5. Leave **Allow write access** unchecked
6. Click **Add key**

- [ ] **Step 6: Test the connection**

```bash
ssh -T github-auto-checking
```

Expected output:
```
Hi bullbashechka! You've successfully authenticated, but GitHub does not provide shell access.
```

---

### Task 3: Clone repository and set up Python environment

**Where:** VPS, logged in as root

- [ ] **Step 1: Clone the repo**

```bash
git clone github-auto-checking:bullbashechka/auto_checking_transcript.git /opt/auto_checking_transcript
```

Note: uses the SSH Host alias `github-auto-checking` defined in Task 2, Step 4.

- [ ] **Step 2: Verify the clone**

```bash
ls /opt/auto_checking_transcript
```

Expected: `src/  scripts/  requirements.txt  deploy.sh  .env.example  README.md  ...`

- [ ] **Step 3: Check Python version**

```bash
python3 --version
```

Expected: `Python 3.10.x` or higher. If Python 3 is not installed:

```bash
apt update && apt install -y python3 python3-venv python3-pip
```

- [ ] **Step 4: Create virtual environment**

```bash
cd /opt/auto_checking_transcript
python3 -m venv .venv
```

- [ ] **Step 5: Install dependencies**

```bash
.venv/bin/pip install -r requirements.txt
```

Expected: All packages install without errors. Final line will be something like `Successfully installed ...`

- [ ] **Step 6: Verify key packages installed**

```bash
.venv/bin/pip show python-telegram-bot google-genai openpyxl
```

Expected: Version info printed for all three packages.

---

### Task 4: Create `.env` on the VPS

**Where:** VPS, `/opt/auto_checking_transcript/`

- [ ] **Step 1: View the template**

```bash
cat /opt/auto_checking_transcript/.env.example
```

Output will show the required variables:
```
TELEGRAM_TOKEN=put_your_telegram_bot_token_here
GEMINI_API_KEY=put_your_gemini_api_key_here
ALLOWED_USER_IDS=123456789,987654321
ALLOW_ANY=
GEMINI_MODEL=gemini-2.5-flash
LLM_CONCURRENCY=5
```

- [ ] **Step 2: Create `.env` with real values**

```bash
nano /opt/auto_checking_transcript/.env
```

Fill in:
```dotenv
TELEGRAM_TOKEN=<your real telegram bot token>
GEMINI_API_KEY=<your real gemini api key>
ALLOWED_USER_IDS=<your telegram id, comma-separated>
ALLOW_ANY=
GEMINI_MODEL=gemini-2.5-flash
LLM_CONCURRENCY=5
```

Save: `Ctrl+O`, `Enter`, `Ctrl+X`.

- [ ] **Step 3: Verify .env exists and is not empty**

```bash
wc -l /opt/auto_checking_transcript/.env
```

Expected: `6 /opt/auto_checking_transcript/.env` (or similar, not 0)

- [ ] **Step 4: Confirm .env is not tracked by git**

```bash
cd /opt/auto_checking_transcript && git status
```

Expected: `.env` does NOT appear in the output (it's in `.gitignore`).

---

### Task 5: Create and enable the systemd service

**Where:** VPS

- [ ] **Step 1: Create the service file**

```bash
cat > /etc/systemd/system/auto-checking-transcript.service << 'EOF'
[Unit]
Description=Auto Checking Transcript Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/auto_checking_transcript
ExecStart=/opt/auto_checking_transcript/.venv/bin/python -m src.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

- [ ] **Step 2: Reload systemd daemon**

```bash
systemctl daemon-reload
```

- [ ] **Step 3: Enable service to start on reboot**

```bash
systemctl enable auto-checking-transcript
```

Expected: `Created symlink /etc/systemd/system/multi-user.target.wants/auto-checking-transcript.service → /etc/systemd/system/auto-checking-transcript.service.`

- [ ] **Step 4: Start the service**

```bash
systemctl start auto-checking-transcript
```

- [ ] **Step 5: Verify it is running**

```bash
systemctl status auto-checking-transcript
```

Expected: status shows `Active: active (running)` and a line like:
```
Main PID: XXXXX (python)
```

If status shows `failed` — check logs in Task 6, Step 1.

---

### Task 6: Verify the bot works end-to-end

- [ ] **Step 1: Check logs for startup errors**

```bash
journalctl -u auto-checking-transcript -n 50 --no-pager
```

Expected: Lines like:
```
INFO root: Starting bot. Whitelist: [XXXXXXX]
```

No `ERROR` or `Traceback` lines. If there are errors:
- `RuntimeError: TELEGRAM_TOKEN is not set` → check `.env` has the token
- `RuntimeError: GEMINI_API_KEY is not set` → check `.env` has the key
- `ModuleNotFoundError` → re-run `.venv/bin/pip install -r requirements.txt`

- [ ] **Step 2: Test via Telegram**

Open Telegram, find your bot by username, send `/start`.

Expected: Bot replies with a welcome message.

- [ ] **Step 3: Test `/id` command**

Send `/id` to the bot.

Expected: Bot replies with your Telegram numeric ID.

- [ ] **Step 4: Test file processing**

Send an `.xlsx` timesheet file to the bot.

Expected sequence of messages:
1. «Скачиваю файл…»
2. «Анализирую содержание, это может занять минуту…»
3. Report with corrections
4. Corrected `.xlsx` file with `_исправленное_` in the filename

- [ ] **Step 5: Verify the existing bot is unaffected**

```bash
systemctl status friday-reminder
```

Expected: Still `Active: active (running)`.

---

### Task 7: Test the update workflow

- [ ] **Step 1: Make a trivial local change and push**

On your local machine, add a comment or blank line to any file, commit and push:

```bash
git add <any file>
git commit -m "test deploy workflow"
git push origin main
```

- [ ] **Step 2: Run deploy.sh on VPS**

```bash
bash /opt/auto_checking_transcript/deploy.sh
```

Expected output:
```
From github.com:bullbashechka/auto_checking_transcript
   abc1234..def5678  main -> origin/main
Updating abc1234..def5678
Fast-forward
 ...
Requirement already satisfied: ...
● auto-checking-transcript.service - Auto Checking Transcript Telegram Bot
     Loaded: loaded (...)
     Active: active (running) since ...
```

- [ ] **Step 3: Confirm bot is still responding in Telegram**

Send `/start` to the bot again — it should respond normally after the restart.
