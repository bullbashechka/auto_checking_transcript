# VPS Deployment Design

**Date:** 2026-06-22  
**Status:** Approved

## Overview

Deploy the `auto_checking_transcript` Telegram bot to an existing Ubuntu VPS (`45.80.69.22`) alongside the already-running `friday-reminder` bot. Process management via systemd, code delivery via `git clone` from a private GitHub repo authenticated with an SSH deploy key.

## Server Context

- OS: Ubuntu
- Access: `root@45.80.69.22`
- RAM: 1 GB / Disk: 10 GB NVMe
- Existing bot: `/opt/friday-reminder/` managed by systemd (do not touch)

## 1. GitHub Authentication

Generate an `ed25519` SSH key on the VPS, add the public key as a **read-only Deploy Key** in the GitHub repo settings (`bullbashechka/auto_checking_transcript`). Clone via SSH:

```
git clone git@github.com:bullbashechka/auto_checking_transcript.git /opt/auto_checking_transcript
```

## 2. Directory Structure

```
/opt/
├── friday-reminder/            # existing bot — untouched
└── auto_checking_transcript/
    ├── .venv/                  # created on server, not in git
    ├── src/
    ├── scripts/
    ├── requirements.txt
    ├── .env                    # created manually once, never committed
    └── deploy.sh               # update script
```

`.env` is created once manually on the server via `nano /opt/auto_checking_transcript/.env`, using `.env.example` as a template.

## 3. systemd Service

File: `/etc/systemd/system/auto-checking-transcript.service`

```ini
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
```

`WorkingDirectory` is required so that `from src import ...` resolves correctly. `ExecStart` calls Python from the venv directly — no activation needed. `.env` is loaded by `python-dotenv` from `WorkingDirectory`.

Commands:
```bash
systemctl enable auto-checking-transcript   # autostart on reboot
systemctl start auto-checking-transcript
systemctl status auto-checking-transcript
systemctl restart auto-checking-transcript
journalctl -u auto-checking-transcript -f   # logs
```

## 4. Update Workflow

File: `/opt/auto_checking_transcript/deploy.sh`

```bash
#!/bin/bash
set -e
cd /opt/auto_checking_transcript
git pull
.venv/bin/pip install -r requirements.txt -q
systemctl restart auto-checking-transcript
systemctl status auto-checking-transcript --no-pager
```

To deploy an update:
```bash
bash /opt/auto_checking_transcript/deploy.sh
```

## First-Time Setup Summary

1. Generate SSH key on VPS, add public key as GitHub Deploy Key
2. `git clone git@github.com:bullbashechka/auto_checking_transcript.git /opt/auto_checking_transcript`
3. `cd /opt/auto_checking_transcript && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. Create `/opt/auto_checking_transcript/.env` with real keys
5. Create and enable systemd service
6. `systemctl start auto-checking-transcript && systemctl status auto-checking-transcript`
