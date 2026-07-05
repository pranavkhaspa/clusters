#!/bin/bash
# Daytona LFHAI Cluster Bootstrapper for Hugging Face SDK Spaces
set -e

echo "=========================================================="
echo "   Setting up Daytona LFHAI Cluster on Hugging Face Space  "
echo "=========================================================="

# 1. Install required Python packages
echo "Installing Python dependencies..."
pip install --no-cache-dir fastapi uvicorn requests pyngrok

# 2. Download and configure Daytona CLI
if ! command -v daytona &> /dev/null; then
    echo "Daytona CLI not found globally. Installing locally..."
    mkdir -p bin
    curl -fL https://github.com/daytonaio/daytona/releases/latest/download/daytona-linux-amd64 -o bin/daytona
    chmod +x bin/daytona
    export PATH="$PWD/bin:$PATH"
else
    echo "Daytona CLI is already installed globally."
fi

# 3. Boot the FastAPI API & Web Dashboard
echo "Starting Daytona Node Pool API and Dashboard on port 7860..."
export WORKSPACE_DIR="$PWD"
exec uvicorn web.app:app --host 0.0.0.0 --port 7860
