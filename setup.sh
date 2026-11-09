#!/usr/bin/env bash
# Cài đặt lần đầu: tạo môi trường Python riêng (.venv) và cài thư viện.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null; then
  echo "Cần Python 3.10 trở lên (sudo apt install python3 python3-venv)" >&2
  exit 1
fi
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt
echo "Cài xong. Chạy: ./run.sh"
