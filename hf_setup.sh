#!/bin/bash
set -e

echo "=========================================================="
echo "   Setting up Daytona LFHAI Cluster on Hugging Face Space  "
echo "=========================================================="

echo "Installing Python dependencies..."
pip install --no-cache-dir fastapi uvicorn requests pyngrok

if ! command -v daytona >/dev/null 2>&1; then
    echo "Installing Daytona CLI..."
    mkdir -p bin
    curl -fsSL \
      https://github.com/daytonaio/daytona/releases/latest/download/daytona-linux-amd64 \
      -o bin/daytona
    chmod +x bin/daytona
    export PATH="$PWD/bin:$PATH"
fi

export WORKSPACE_DIR="$PWD"

# HF Spaces provides PORT=7860.
# If running locally, default to 8000.
export PORT=${PORT:-8000}

echo "Starting FastAPI on 0.0.0.0:${PORT}..."

exec uvicorn web.app:app \
    --host 0.0.0.0 \
    --port "${PORT}"
