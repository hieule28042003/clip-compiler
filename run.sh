#!/usr/bin/env bash
# Chạy web app: ./run.sh [cổng]   (mặc định 8790), rồi mở http://localhost:<cổng>
# Cập nhật yt-dlp khi YouTube đổi cách hoạt động: ./run.sh --update
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || ./setup.sh
if [ "${1:-}" = "--update" ]; then
  .venv/bin/pip install -U "yt-dlp[default]" deno
  shift
fi
PORT="${1:-8790}"
echo "Mở trình duyệt: http://localhost:${PORT}   (Ctrl+C để dừng)"
if command -v xdg-open >/dev/null; then
  (sleep 2 && xdg-open "http://localhost:${PORT}" >/dev/null 2>&1) &
fi
exec .venv/bin/uvicorn server:app --host 127.0.0.1 --port "${PORT}"
