#!/bin/bash
set -e
cd /opt/auto_checking_transcript
git pull
.venv/bin/pip install -r requirements.txt -q
systemctl restart auto-checking-transcript
systemctl status auto-checking-transcript --no-pager
