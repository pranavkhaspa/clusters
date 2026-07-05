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
    curl -fL \
      https://github.com/daytonaio/daytona/releases/latest/download/daytona-linux-amd64 \
      -o bin/daytona
    chmod +x bin/daytona
    export PATH="$PWD/bin:$PATH"
fi

export WORKSPACE_DIR="$PWD"

echo "Starting FastAPI on localhost:8000..."

exec uvicorn web.app:app \
    --host 127.0.0.1 \
    --port 8000
