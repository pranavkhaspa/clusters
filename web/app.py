import os
import sys
import json
import subprocess
import time
import logging
import threading
from datetime import datetime, timezone
import requests
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# Add parent directory to path so we can import from controller
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Base paths
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
CONFIGS_DIR = os.path.join(WORKSPACE_DIR, "configs")
CONNECTIONS_DIR = os.path.join(WORKSPACE_DIR, "connections")
CONNECTIONS_JSON = os.path.join(WORKSPACE_DIR, "connections.json")
import shutil

# Resolve Daytona binary path dynamically for portability
DAYTONA_BIN = shutil.which("daytona")
if not DAYTONA_BIN:
    local_bin = os.path.join(WORKSPACE_DIR, "bin", "daytona")
    if os.path.exists(local_bin):
        DAYTONA_BIN = local_bin
    else:
        DAYTONA_BIN = "/usr/local/bin/daytona"

API_BASE_URL = "https://app.daytona.io/api"

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("daytona-api")

# API Keys setup (Read from env first for HF Space security, fallback to hardcoded keys)
NODE_KEYS = {
    "lfhai-node-01": os.environ.get("DAYTONA_KEY_1", "dtn_ab333b713849db4c93fdc99283d860cdbdc4863c6a63c9cb010b1a3e5e1badd5"),
    "lfhai-node-02": os.environ.get("DAYTONA_KEY_2", "dtn_efd68247001d2240fbcd41a371752915cceee92cad281e87e0ba82316b335f86"),
    "lfhai-node-03": os.environ.get("DAYTONA_KEY_3", "dtn_824aacffa93ef17e49f5a1dc7b1975b9b170e918d1c0010884caa56fd2ac552a"),
    "lfhai-node-04": os.environ.get("DAYTONA_KEY_4", "dtn_c5ade9951ac4132ec4e4f861746b4750dc6bc0c0b6d9d7d99c72bdcc1d0a2249"),
    "lfhai-node-05": os.environ.get("DAYTONA_KEY_5", "dtn_ea89de6ea05b6884f34d10e8cddc49039781d000a505588f5a9adfb9cb8f0ddb"),
    "lfhai-node-06": os.environ.get("DAYTONA_KEY_6", "dtn_de2a61ab7b2c298633868bd92034c59b49f428cfa5d2c81fde3344be92a5bc60"),
    "lfhai-node-07": os.environ.get("DAYTONA_KEY_7", "dtn_5fde7c7fd968317bbae2d1c6b094df238ec960e7afcca3c7210f6972b44c63c3")
}

# In-memory status cache
node_status_cache = {}
global_stats = {
    "online_count": 0,
    "total_count": len(NODE_KEYS),
    "last_refresh": "Never"
}

# Lock for safe thread updates
cache_lock = threading.Lock()

# ----------------- Helper Functions -----------------

def get_env_for_node(node_name):
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = os.path.join(CONFIGS_DIR, node_name)
    return env

def write_node_config(node_name, api_key):
    node_config_dir = os.path.join(CONFIGS_DIR, node_name, "daytona")
    os.makedirs(node_config_dir, exist_ok=True)
    config_data = {
        "activeProfile": "initial",
        "profiles": [
            {
                "id": "initial",
                "name": "initial",
                "api": {
                    "url": API_BASE_URL,
                    "key": api_key,
                    "token": None
                },
                "activeOrganizationId": None
            }
        ]
    }
    with open(os.path.join(node_config_dir, "config.json"), "w") as f:
        json.dump(config_data, f, indent=2)

def call_api(api_key, method, path, params=None, json_data=None):
    url = f"{API_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.request(method, url, headers=headers, params=params, json=json_data, timeout=15)
        response.raise_for_status()
        return response.json() if response.text else {}
    except Exception as e:
        logger.error(f"API {method} {path} failed: {e}")
        return None

def get_sandbox_details(node_name, api_key):
    try:
        res = call_api(api_key, "GET", "/sandbox")
        if not res:
            return None
        for item in res.get("items", []):
            if item.get("name") == node_name:
                return item
        return None
    except Exception:
        return None

# ----------------- Orchestration Core -----------------

def bootstrap_all():
    logger.info("Initializing configurations...")
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    
    # Write a base Dockerfile for the sandboxes to request custom resources
    node_dockerfile = os.path.join(WORKSPACE_DIR, "Dockerfile.node")
    try:
        with open(node_dockerfile, "w") as f:
            f.write("FROM daytonaio/sandbox:0.8.0\n")
    except Exception as e:
        logger.error(f"Failed to write Dockerfile.node: {e}")
        
    for node_name, api_key in NODE_KEYS.items():
        write_node_config(node_name, api_key)
        # Check if sandbox exists
        sandbox = get_sandbox_details(node_name, api_key)
        if not sandbox:
            logger.info(f"Sandbox {node_name} not found. Creating with 8GB RAM, 4 vCPUs...")
            env = get_env_for_node(node_name)
            cmd = [
                DAYTONA_BIN, "create", "--name", node_name,
                "--dockerfile", node_dockerfile,
                "--cpu", "4", "--memory", "8", "--disk", "10",
                "--auto-stop", "43200", "--auto-archive", "10080", "--auto-delete", "-1"
            ]
            subprocess.run(cmd, env=env, capture_output=True)

def refresh_credentials_task(expires_in_minutes=1440):
    logger.info("Starting SSH credentials rotation background task...")
    os.makedirs(CONNECTIONS_DIR, exist_ok=True)
    connections_data = {}
    
    # Load existing if available
    if os.path.exists(CONNECTIONS_JSON):
        try:
            with open(CONNECTIONS_JSON, "r") as f:
                connections_data = json.load(f)
        except Exception:
            pass

    for node_name, api_key in NODE_KEYS.items():
        try:
            sandbox = get_sandbox_details(node_name, api_key)
            if not sandbox:
                continue
                
            sandbox_id = sandbox.get("id")
            state = sandbox.get("state", "stopped")
            
            # Request new credential
            path = f"/sandbox/{sandbox_id}/ssh-access"
            params = {"expiresInMinutes": expires_in_minutes}
            ssh_info = call_api(api_key, "POST", path, params=params)
            
            if ssh_info:
                token = ssh_info.get("token")
                created_at = ssh_info.get("createdAt")
                expires_at = ssh_info.get("expiresAt")
                ssh_command = ssh_info.get("sshCommand")
                
                connections_data[node_name] = {
                    "sandboxId": sandbox_id,
                    "status": state,
                    "generatedAt": created_at,
                    "expiresAt": expires_at,
                    "sshCommand": ssh_command,
                    "token": token
                }
                
                # Write individual md
                md_content = (
                    f"Node: {node_name}\nSandbox ID: {sandbox_id}\n\n"
                    f"Generated:\n{created_at}\n\nExpires:\n{expires_at}\n\n"
                    f"SSH:\n\n{ssh_command}\n"
                )
                node_idx = node_name.split("-")[-1]
                with open(os.path.join(CONNECTIONS_DIR, f"node-{node_idx}.md"), "w") as f:
                    f.write(md_content)
        except Exception as e:
            logger.error(f"Failed to refresh SSH for {node_name}: {e}")

    try:
        with open(CONNECTIONS_JSON, "w") as f:
            json.dump(connections_data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save connections.json: {e}")
        
    with cache_lock:
        global_stats["last_refresh"] = datetime.now(timezone.utc).isoformat()

# ----------------- Background Worker Threads -----------------

def stats_collector_loop():
    """
    Runs continuously, polling memory (RAM) and CPU loadavg of online sandboxes.
    """
    logger.info("Starting live stats collector loop...")
    while True:
        temp_status = {}
        online = 0
        
        # Load connections data for current SSH details
        conn_details = {}
        if os.path.exists(CONNECTIONS_JSON):
            try:
                with open(CONNECTIONS_JSON, "r") as f:
                    conn_details = json.load(f)
            except Exception:
                pass

        for node_name, api_key in NODE_KEYS.items():
            sandbox = get_sandbox_details(node_name, api_key)
            if not sandbox:
                temp_status[node_name] = {
                    "name": node_name,
                    "status": "offline",
                    "sandboxId": "N/A",
                    "cpu_load": 0.0,
                    "ram_total": 0,
                    "ram_used": 0,
                    "ram_percent": 0.0,
                    "ssh_command": "",
                    "expires_at": "N/A",
                    "region": "N/A"
                }
                continue
                
            state = sandbox.get("state", "stopped")
            sandbox_id = sandbox.get("id")
            region = sandbox.get("target", "eu")
            
            node_conn = conn_details.get(node_name, {})
            ssh_command = node_conn.get("sshCommand", f"daytona ssh {node_name}")
            expires_at = node_conn.get("expiresAt", "N/A")
            
            cpu_load = 0.0
            ram_total = 768  # Default Container memory allocation
            ram_used = 0
            ram_percent = 0.0
            
            if state == "started":
                online += 1
                env = get_env_for_node(node_name)
                
                # Fetch RAM info
                try:
                    res_ram = subprocess.run(
                        [DAYTONA_BIN, "exec", node_name, "--", "free", "-m"],
                        env=env, capture_output=True, text=True, timeout=5
                    )
                    if res_ram.returncode == 0:
                        lines = res_ram.stdout.split("\n")
                        for line in lines:
                            if line.startswith("Mem:"):
                                parts = line.split()
                                # Check if output is in KB or MB
                                # 'free -m' output has: Mem: total used free shared buff/cache available
                                total_val = int(parts[1])
                                used_val = int(parts[2])
                                # If total > 100000, free output was in KB despite -m, adjust to MB
                                if total_val > 10000:
                                    ram_total = round(total_val / 1024)
                                    ram_used = round(used_val / 1024)
                                else:
                                    ram_total = total_val
                                    ram_used = used_val
                                ram_percent = round((ram_used / ram_total) * 100, 1) if ram_total > 0 else 0.0
                                break
                except Exception:
                    pass
                    
                # Fetch CPU info
                try:
                    res_cpu = subprocess.run(
                        [DAYTONA_BIN, "exec", node_name, "--", "cat", "/proc/loadavg"],
                        env=env, capture_output=True, text=True, timeout=5
                    )
                    if res_cpu.returncode == 0:
                        cpu_load = float(res_cpu.stdout.split()[0])
                except Exception:
                    pass
            
            temp_status[node_name] = {
                "name": node_name,
                "status": state,
                "sandboxId": sandbox_id,
                "cpu_load": cpu_load,
                "ram_total": ram_total,
                "ram_used": ram_used,
                "ram_percent": ram_percent,
                "ssh_command": ssh_command,
                "expires_at": expires_at,
                "region": region
            }
            
        with cache_lock:
            node_status_cache.update(temp_status)
            global_stats["online_count"] = online
            
        time.sleep(15)

def cron_refresh_loop():
    """
    Ensures SSH credentials are rotated every 23 hours.
    23 hours = 82800 seconds.
    """
    logger.info("Starting auto-refresh cron loop (23 hours)...")
    while True:
        # Wait 23 hours
        time.sleep(82800)
        try:
            # Make sure all nodes are running before rotating credentials
            for node_name, api_key in NODE_KEYS.items():
                sandbox = get_sandbox_details(node_name, api_key)
                if sandbox and sandbox.get("state") != "started":
                    logger.info(f"Auto-refresh: Node {node_name} is stopped. Starting it first...")
                    env = get_env_for_node(node_name)
                    subprocess.run([DAYTONA_BIN, "start", node_name], env=env, capture_output=True)
            
            refresh_credentials_task()
        except Exception as e:
            logger.error(f"Error in auto-refresh cron: {e}")

# ----------------- FastAPI Setup -----------------

app = FastAPI(title="Daytona LFHAI Cluster API", description="Control Plane for Daytona Node Pool")

@app.on_event("startup")
def startup_event():
    # 1. Bootstrap
    bootstrap_all()
    # 2. Run initial start and SSH rotation
    refresh_credentials_task()
    # 3. Start background threads
    t_stats = threading.Thread(target=stats_collector_loop, daemon=True)
    t_stats.start()
    t_cron = threading.Thread(target=cron_refresh_loop, daemon=True)
    t_cron.start()

    # Start ngrok tunnel if token is available
       # ------------------------------------------------------------
    # Optional ngrok tunnel (for convenience only)
    # HF Space itself is still the primary endpoint.
    # ------------------------------------------------------------
    ngrok_token = os.environ.get("NGROK_AUTHTOKEN")

    if ngrok_token:
        try:
            from pyngrok import ngrok

            logger.info("Starting ngrok tunnel...")

            ngrok.set_auth_token(ngrok_token)

            # Kill any old tunnels
            ngrok.kill()

            # Detect where uvicorn is actually listening.
            # HF Spaces reverse-proxies to 7860, but locally your app runs on 8000.
            local_port = int(os.environ.get("PORT", 8000))

            tunnel = ngrok.connect(
                addr=local_port,
                proto="http",
                bind_tls=True,
            )

            public_url = tunnel.public_url

            logger.info("=" * 60)
            logger.info("NGROK TUNNEL READY")
            logger.info(public_url)
            logger.info("=" * 60)

            with cache_lock:
                global_stats["ngrok_url"] = public_url

        except Exception:
            logger.exception("Failed to start ngrok tunnel")

# ----------------- API Endpoints -----------------

@app.get("/api/status")
def get_api_status():
    with cache_lock:
        # Sort node names
        sorted_nodes = [node_status_cache[name] for name in sorted(node_status_cache.keys())]
        return JSONResponse(content={
            "nodes": sorted_nodes,
            "global": global_stats
        })

@app.post("/api/refresh")
def trigger_refresh(background_tasks: BackgroundTasks):
    background_tasks.add_task(refresh_credentials_task)
    return {"message": "SSH credential rotation triggered in background."}

@app.post("/api/node/{node_name}/start")
def trigger_node_start(node_name: str, background_tasks: BackgroundTasks):
    if node_name not in NODE_KEYS:
        raise HTTPException(status_code=404, detail="Node not found")
    
    def start_task():
        logger.info(f"Starting {node_name} via API request...")
        env = get_env_for_node(node_name)
        subprocess.run([DAYTONA_BIN, "start", node_name], env=env, capture_output=True)
        # Immediately trigger credentials generation once started
        refresh_credentials_task()
        
    background_tasks.add_task(start_task)
    return {"message": f"Start command triggered for {node_name}."}

@app.post("/api/node/{node_name}/stop")
def trigger_node_stop(node_name: str, background_tasks: BackgroundTasks):
    if node_name not in NODE_KEYS:
        raise HTTPException(status_code=404, detail="Node not found")
        
    def stop_task():
        logger.info(f"Stopping {node_name} via API request...")
        env = get_env_for_node(node_name)
        subprocess.run([DAYTONA_BIN, "stop", node_name], env=env, capture_output=True)
        
    background_tasks.add_task(stop_task)
    return {"message": f"Stop command triggered for {node_name}."}

# ----------------- Web Dashboard HTML (Inline for portability) -----------------

@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Daytona LFHAI Node Pool Dashboard</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;600;800&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-gradient: radial-gradient(circle at top right, #111827, #030712);
                --card-bg: rgba(17, 24, 39, 0.6);
                --card-border: rgba(255, 255, 255, 0.08);
                --accent-blue: #3b82f6;
                --accent-indigo: #6366f1;
                --accent-emerald: #10b981;
                --accent-rose: #f43f5e;
                --text-primary: #f3f4f6;
                --text-secondary: #9ca3af;
                --glass-glow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
            }

            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
            }

            body {
                font-family: 'Inter', sans-serif;
                background: var(--bg-gradient);
                color: var(--text-primary);
                min-height: 100vh;
                padding: 2rem;
                display: flex;
                flex-direction: column;
                overflow-x: hidden;
            }

            h1, h2, h3 {
                font-family: 'Outfit', sans-serif;
            }

            header {
                max-width: 1400px;
                width: 100%;
                margin: 0 auto 2rem auto;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                padding-bottom: 1.5rem;
            }

            .logo-area {
                display: flex;
                align-items: center;
                gap: 0.75rem;
            }

            .logo-glow {
                width: 12px;
                height: 12px;
                background-color: var(--accent-emerald);
                border-radius: 50%;
                box-shadow: 0 0 15px var(--accent-emerald);
                animation: pulse 2s infinite;
            }

            .logo-text {
                font-size: 1.8rem;
                font-weight: 800;
                background: linear-gradient(135deg, #fff 30%, #a5b4fc 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }

            .header-stats {
                display: flex;
                gap: 2rem;
            }

            .stat-badge {
                background: var(--card-bg);
                border: 1px solid var(--card-border);
                backdrop-filter: blur(12px);
                border-radius: 12px;
                padding: 0.5rem 1rem;
                display: flex;
                flex-direction: column;
                align-items: flex-end;
            }

            .stat-badge span:first-child {
                font-size: 0.75rem;
                color: var(--text-secondary);
                text-transform: uppercase;
                letter-spacing: 0.05em;
            }

            .stat-badge span:last-child {
                font-size: 1.1rem;
                font-weight: 700;
                color: #fff;
            }

            .action-btn {
                background: linear-gradient(135deg, var(--accent-blue), var(--accent-indigo));
                color: #fff;
                border: none;
                border-radius: 8px;
                padding: 0.6rem 1.2rem;
                font-size: 0.85rem;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s ease;
                box-shadow: 0 4px 15px rgba(99, 102, 241, 0.2);
            }

            .action-btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(99, 102, 241, 0.4);
            }

            .action-btn:active {
                transform: translateY(0);
            }

            .grid-container {
                max-width: 1400px;
                width: 100%;
                margin: 0 auto;
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
                gap: 1.5rem;
                flex-grow: 1;
            }

            .card {
                background: var(--card-bg);
                border: 1px solid var(--card-border);
                border-radius: 16px;
                backdrop-filter: blur(16px);
                box-shadow: var(--glass-glow);
                padding: 1.5rem;
                display: flex;
                flex-direction: column;
                gap: 1.25rem;
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                position: relative;
                overflow: hidden;
            }

            .card::before {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 4px;
                background: linear-gradient(90deg, var(--accent-blue), var(--accent-indigo));
                opacity: 0.8;
            }

            .card:hover {
                transform: translateY(-5px);
                border-color: rgba(99, 102, 241, 0.3);
                box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
            }

            .card-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
            }

            .card-title {
                font-size: 1.25rem;
                font-weight: 700;
                color: #fff;
            }

            .status-dot-wrapper {
                display: flex;
                align-items: center;
                gap: 0.5rem;
                font-size: 0.75rem;
                font-weight: 600;
                text-transform: uppercase;
                background: rgba(255, 255, 255, 0.03);
                padding: 0.25rem 0.6rem;
                border-radius: 50px;
                border: 1px solid rgba(255, 255, 255, 0.05);
            }

            .dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
            }

            .dot.online {
                background-color: var(--accent-emerald);
                box-shadow: 0 0 8px var(--accent-emerald);
            }

            .dot.offline {
                background-color: var(--text-secondary);
            }

            .dot.loading {
                background-color: #f59e0b;
                animation: pulse 1.5s infinite;
            }

            .metrics {
                display: flex;
                flex-direction: column;
                gap: 1rem;
            }

            .metric-bar-group {
                display: flex;
                flex-direction: column;
                gap: 0.35rem;
            }

            .metric-label {
                display: flex;
                justify-content: space-between;
                font-size: 0.8rem;
                color: var(--text-secondary);
            }

            .metric-bar-bg {
                background: rgba(255, 255, 255, 0.05);
                height: 8px;
                border-radius: 4px;
                overflow: hidden;
            }

            .metric-bar-fill {
                height: 100%;
                background: linear-gradient(90deg, var(--accent-blue), var(--accent-indigo));
                border-radius: 4px;
                transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1);
            }

            .metric-bar-fill.warning {
                background: linear-gradient(90deg, #f59e0b, #d97706);
            }

            .metric-bar-fill.danger {
                background: linear-gradient(90deg, var(--accent-rose), #e11d48);
            }

            .info-field {
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.03);
                border-radius: 8px;
                padding: 0.75rem;
                font-size: 0.75rem;
                display: flex;
                flex-direction: column;
                gap: 0.25rem;
            }

            .info-field span:first-child {
                color: var(--text-secondary);
                font-weight: 500;
            }

            .info-field span:last-child {
                font-family: monospace;
                color: #e5e7eb;
                word-break: break-all;
            }

            .card-actions {
                display: flex;
                gap: 0.5rem;
                margin-top: auto;
            }

            .card-btn {
                flex: 1;
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 0.5rem;
                color: #e5e7eb;
                font-size: 0.8rem;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
                display: flex;
                justify-content: center;
                align-items: center;
                gap: 0.4rem;
            }

            .card-btn:hover {
                background: rgba(255, 255, 255, 0.1);
                color: #fff;
            }

            .card-btn.primary {
                background: rgba(16, 185, 129, 0.1);
                border-color: rgba(16, 185, 129, 0.2);
                color: var(--accent-emerald);
            }

            .card-btn.primary:hover {
                background: rgba(16, 185, 129, 0.2);
            }

            .card-btn.danger {
                background: rgba(244, 63, 94, 0.1);
                border-color: rgba(244, 63, 94, 0.2);
                color: var(--accent-rose);
            }

            .card-btn.danger:hover {
                background: rgba(244, 63, 94, 0.2);
            }

            .copy-tooltip {
                position: relative;
            }

            footer {
                max-width: 1400px;
                width: 100%;
                margin: 2rem auto 0 auto;
                text-align: center;
                font-size: 0.75rem;
                color: var(--text-secondary);
                border-top: 1px solid rgba(255, 255, 255, 0.05);
                padding-top: 1.5rem;
            }

            @keyframes pulse {
                0%, 100% {
                    transform: scale(1);
                    opacity: 1;
                }
                50% {
                    transform: scale(1.1);
                    opacity: 0.6;
                }
            }

            @media(max-width: 768px) {
                body {
                    padding: 1rem;
                }
                header {
                    flex-direction: column;
                    align-items: flex-start;
                    gap: 1rem;
                }
                .header-stats {
                    width: 100%;
                    justify-content: space-between;
                }
            }
        </style>
    </head>
    <body>
        <header>
            <div class="logo-area">
                <div class="logo-glow"></div>
                <h1 class="logo-text">LFHAI Cluster Control Plane</h1>
            </div>
            <div class="header-stats">
                <div class="stat-badge" id="ngrok-badge" style="display: none;">
                    <span>Ngrok Tunnel</span>
                    <span id="ngrok-url">-</span>
                </div>
                <div class="stat-badge">
                    <span>Nodes Online</span>
                    <span id="nodes-online">- / -</span>
                </div>
                <div class="stat-badge">
                    <span>Last Rotation</span>
                    <span id="last-refresh">Never</span>
                </div>
                <div>
                    <button class="action-btn" onclick="triggerRotation()">Rotate SSH Keys</button>
                </div>
            </div>
        </header>

        <main class="grid-container" id="nodes-grid">
            <!-- Node cards injected dynamically -->
        </main>

        <footer>
            Daytona LFHAI Node Pool Orchestrator &copy; 2026. Made with Google DeepMind Antigravity.
        </footer>

        <script>
            async function fetchStatus() {
                try {
                    const response = await fetch('/api/status');
                    const data = await response.json();
                    
                    // Update header
                    document.getElementById('nodes-online').textContent = `${data.global.online_count} / ${data.global.total_count}`;
                    
                    if (data.global.ngrok_url) {
                        document.getElementById('ngrok-badge').style.display = 'flex';
                        document.getElementById('ngrok-url').innerHTML = `<a href="${data.global.ngrok_url}" target="_blank" style="color:#6366f1; text-decoration:none;">${data.global.ngrok_url.replace("https://", "")}</a>`;
                    } else {
                        document.getElementById('ngrok-badge').style.display = 'none';
                    }
                    
                    const lastRef = data.global.last_refresh;
                    if (lastRef !== 'Never') {
                        const date = new Date(lastRef);
                        document.getElementById('last-refresh').textContent = date.toLocaleTimeString();
                    } else {
                        document.getElementById('last-refresh').textContent = 'Never';
                    }

                    // Render grid
                    const grid = document.getElementById('nodes-grid');
                    grid.innerHTML = '';
                    
                    data.nodes.forEach(node => {
                        const isOnline = node.status === 'started';
                        const dotClass = isOnline ? 'online' : 'offline';
                        
                        // Set bar color classes based on usage
                        const ramClass = node.ram_percent > 85 ? 'danger' : (node.ram_percent > 65 ? 'warning' : '');
                        const cpuClass = node.cpu_load > 1.5 ? 'danger' : (node.cpu_load > 0.8 ? 'warning' : '');

                        const card = document.createElement('div');
                        card.className = 'card';
                        
                        card.innerHTML = `
                            <div class="card-header">
                                <h3 class="card-title">${node.name}</h3>
                                <div class="status-dot-wrapper">
                                    <div class="dot ${dotClass}"></div>
                                    <span>${node.status}</span>
                                </div>
                            </div>
                            
                            <div class="metrics">
                                <div class="metric-bar-group">
                                    <div class="metric-label">
                                        <span>CPU Load (1m Avg)</span>
                                        <span>${node.cpu_load.toFixed(2)}</span>
                                    </div>
                                    <div class="metric-bar-bg">
                                        <div class="metric-bar-fill ${cpuClass}" style="width: ${Math.min(node.cpu_load * 50, 100)}%"></div>
                                    </div>
                                </div>
                                
                                <div class="metric-bar-group">
                                    <div class="metric-label">
                                        <span>RAM (Memory)</span>
                                        <span>${node.ram_used} MB / ${node.ram_total} MB (${node.ram_percent}%)</span>
                                    </div>
                                    <div class="metric-bar-bg">
                                        <div class="metric-bar-fill ${ramClass}" style="width: ${node.ram_percent}%"></div>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="info-field">
                                <span>Sandbox ID</span>
                                <span>${node.sandboxId}</span>
                            </div>
                            
                            ${isOnline ? `
                            <div class="info-field">
                                <span>SSH Connection Command</span>
                                <span>${node.ssh_command || 'ssh.app.daytona.io'}</span>
                            </div>
                            ` : ''}

                            <div class="card-actions">
                                ${isOnline ? `
                                    <button class="card-btn" onclick="copySSH('${node.ssh_command}')">Copy SSH</button>
                                    <button class="card-btn danger" onclick="stopNode('${node.name}')">Stop</button>
                                ` : `
                                    <button class="card-btn primary" onclick="startNode('${node.name}')">Start Node</button>
                                `}
                            </div>
                        `;
                        grid.appendChild(card);
                    });
                } catch (err) {
                    console.error("Failed to fetch status:", err);
                }
            }

            async function triggerRotation() {
                if (confirm("Rotate credentials for all nodes?")) {
                    await fetch('/api/refresh', { method: 'POST' });
                    alert("Rotation triggered!");
                    setTimeout(fetchStatus, 1500);
                }
            }

            async function startNode(name) {
                await fetch(`/api/node/${name}/start`, { method: 'POST' });
                alert(`Starting ${name}...`);
                setTimeout(fetchStatus, 2000);
            }

            async function stopNode(name) {
                if (confirm(`Stop node ${name}?`)) {
                    await fetch(`/api/node/${name}/stop`, { method: 'POST' });
                    alert(`Stopping ${name}...`);
                    setTimeout(fetchStatus, 2000);
                }
            }

            function copySSH(cmd) {
                navigator.clipboard.writeText(cmd);
                alert("SSH command copied to clipboard!");
            }

            // Poll every 5s
            setInterval(fetchStatus, 5000);
            fetchStatus();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
