# Daytona LFHAI Cluster Control Plane & local TUI

This repository provides an automated orchestration system for a cluster of 7 independent Daytona nodes (sandboxes). It keeps the nodes alive, automatically rotates their SSH credentials every 23 hours, exposes a FastAPI control API and glassmorphic Web Dashboard, and includes a zero-dependency Terminal User Interface (TUI) to monitor and SSH into any cluster machine directly from your local shell.

---

## Repository Files

* `web/app.py`: FastAPI server containing the web dashboard, REST API, live stats collector thread, and credential rotation cron thread.
* `tui.py`: Zero-dependency terminal user interface (run locally).
* `controller.py`: The core Daytona manager CLI helper.
* `keep_active.sh`: Watchdog monitoring script that keeps the server process running 24/7.
* `hf_setup.sh`: Bootstrapper script that installs dependencies, gets the Daytona CLI, and runs the server.
* `Dockerfile`: Container configuration for Hugging Face Spaces.
* `.gitignore`: Ensures virtual environments, logs, and sensitive credentials/configs are never pushed to GitHub.

---

## Setup & Running on a Ubuntu/Debian Machine

### Step 1: Clone the Repository
```bash
git clone https://github.com/pranavkhaspa/clusters.git
cd clusters
```

### Step 2: Configure API Keys & Ngrok Token
You can inject your 7 Daytona API keys and Ngrok token using two methods:

#### Method A: Using Environment Variables (Recommended)
Export the variables in your current shell session before starting:
```bash
# Export Daytona API keys (one for each node)
export DAYTONA_KEY_1="dtn_ab333b713849db4c..."
export DAYTONA_KEY_2="dtn_efd68247001d22..."
export DAYTONA_KEY_3="dtn_824aacffa93ef1..."
export DAYTONA_KEY_4="dtn_c5ade9951ac413..."
export DAYTONA_KEY_5="dtn_ea89de6ea05b68..."
export DAYTONA_KEY_6="dtn_de2a61ab7b2c29..."
export DAYTONA_KEY_7="dtn_5fde7c7fd96831..."

# Export Ngrok Authtoken
export NGROK_AUTHTOKEN="2niO6DxaVsKxRLbZVEPGDXNDu..."
```
*(To make these persistent, you can append these `export` lines to your `~/.bashrc` file).*

#### Method B: Modifying the Code Directly
If you prefer not to use environment variables, you can paste your API keys directly into the `NODE_KEYS` dictionary in `web/app.py` (around line 25) and `controller.py` (around line 30), and your Ngrok token in `web/app.py` (around line 350).

---

### Step 3: Run the Watchdog for 24/7 Uptime
The watchdog script (`keep_active.sh`) will automatically initialize a Python virtual environment, install the dependencies, download the Daytona CLI, start the server, and monitor port `7860`. If the server crashes, the watchdog restarts it automatically.

1. **Clear any existing processes using port 7860**:
   ```bash
   sudo fuser -k 7860/tcp
   ```
2. **Start the watchdog in the background**:
   ```bash
   chmod +x keep_active.sh
   nohup ./keep_active.sh > watchdog.log 2>&1 &
   ```

---

### Step 4: Verify Status & Retrieve Ngrok Link
* Check the watchdog loop logs:
  ```bash
  cat watchdog.log
  ```
* Read the uvicorn server logs to fetch your public Ngrok URL:
  ```bash
  cat server.log
  ```
  *(Look for the `NGROK TUNNEL ESTABLISHED` line near the bottom of the log file).*

---

## Launch the Local TUI Client

On your local machine (where you want to SSH from), download `tui.py` and run it by pointing it to your public Ngrok URL:
```bash
chmod +x tui.py
./tui.py --api https://<your-ngrok-subdomain>.ngrok-free.app
```

### TUI Commands:
* `[↑/↓]`: Navigate between nodes.
* `[Enter]`: Connect via SSH into the highlighted node (suspends TUI and launches native SSH session).
* `[S]`: Toggle node power state (Start / Stop).
* `[R]`: Manually trigger SSH credential rotation across all nodes.
* `[Q]`: Exit TUI.

---

## Deploying to Hugging Face Spaces

If you prefer to host the control plane on Hugging Face Spaces instead of a local machine:
1. Create a new Hugging Face Space, choose **Docker** as the template, and select **Blank**.
2. Go to **Settings** -> **Variables and secrets** and create **Secrets** named `DAYTONA_KEY_1` to `DAYTONA_KEY_7` and `NGROK_AUTHTOKEN`.
3. Push `Dockerfile`, `web/app.py`, `controller.py`, and `.gitignore` to your Hugging Face Space repository.
4. Access the dashboard from the Hugging Face space URL or use the printed Ngrok URL in your local TUI.
