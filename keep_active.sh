#!/bin/bash
# Daytona LFHAI Cluster Keep-Active Watchdog (Runs 24/7)

echo "=========================================================="
echo "    Starting Daytona LFHAI Watchdog Process (24/7 Active)  "
echo "=========================================================="

# Get the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Ensure venv is created
if [ ! -d "venv" ]; then
    echo "Creating python virtual environment..."
    python3 -m venv venv
fi

# Make sure hf_setup.sh is executable
chmod +x hf_setup.sh

# Check interval (in seconds)
CHECK_INTERVAL=30

echo "Watchdog running. Monitoring port 7860 every $CHECK_INTERVAL seconds..."

while true; do
    # Check if uvicorn is listening on port 7860
    # Works across netstat, ss, or lsof
    if ! ss -tuln | grep -q :7860 && ! netstat -tuln | grep -q :7860; then
        echo "[$(date)] WARNING: API server on port 7860 is offline. Restarting..."
        
        # Activate virtual env and run script in background
        source venv/bin/activate
        ./hf_setup.sh >> server.log 2>&1 &
        
        echo "[$(date)] API server restart triggered in background."
    fi
    
    sleep $CHECK_INTERVAL
done
