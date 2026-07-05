#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import time
import logging
from datetime import datetime, timezone, timedelta
import argparse
import requests

# Base paths
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", os.path.dirname(os.path.abspath(__file__)))
CONFIGS_DIR = os.path.join(WORKSPACE_DIR, "configs")
CONNECTIONS_DIR = os.path.join(WORKSPACE_DIR, "connections")
CONNECTIONS_JSON = os.path.join(WORKSPACE_DIR, "connections.json")
DAYTONA_BIN = os.path.join(WORKSPACE_DIR, "bin", "daytona")
API_BASE_URL = "https://app.daytona.io/api"
LOG_FILE = os.path.join(WORKSPACE_DIR, "controller.log")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE)
    ]
)
logger = logging.getLogger("daytona-controller")

# 7 Daytona API keys mapped to node names
NODE_KEYS = {
    "lfhai-node-01": "dtn_ab333b713849db4c93fdc99283d860cdbdc4863c6a63c9cb010b1a3e5e1badd5",
    "lfhai-node-02": "dtn_efd68247001d2240fbcd41a371752915cceee92cad281e87e0ba82316b335f86",
    "lfhai-node-03": "dtn_824aacffa93ef17e49f5a1dc7b1975b9b170e918d1c0010884caa56fd2ac552a",
    "lfhai-node-04": "dtn_c5ade9951ac4132ec4e4f861746b4750dc6bc0c0b6d9d7d99c72bdcc1d0a2249",
    "lfhai-node-05": "dtn_ea89de6ea05b6884f34d10e8cddc49039781d000a505588f5a9adfb9cb8f0ddb",
    "lfhai-node-06": "dtn_de2a61ab7b2c298633868bd92034c59b49f428cfa5d2c81fde3344be92a5bc60",
    "lfhai-node-07": "dtn_5fde7c7fd968317bbae2d1c6b094df238ec960e7afcca3c7210f6972b44c63c3"
}

def get_env_for_node(node_name):
    """
    Returns the system environment variables with XDG_CONFIG_HOME set to the
    node's specific configuration directory.
    """
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = os.path.join(CONFIGS_DIR, node_name)
    return env

def write_node_config(node_name, api_key):
    """
    Writes the Daytona CLI configuration profile for a specific node.
    """
    node_config_dir = os.path.join(CONFIGS_DIR, node_name, "daytona")
    os.makedirs(node_config_dir, exist_ok=True)
    
    config_data = {
        "activeProfile": "initial",
        "profiles": [
            {
                "id": "initial",
                "name": "initial",
                "api": {
                    "url": f"{API_BASE_URL}",
                    "key": api_key,
                    "token": None
                },
                "activeOrganizationId": None
            }
        ]
    }
    
    config_path = os.path.join(node_config_dir, "config.json")
    with open(config_path, "w") as f:
        json.dump(config_data, f, indent=2)
    logger.info(f"Wrote config file for {node_name} to {config_path}")

def call_api(api_key, method, path, params=None, json_data=None):
    """
    Helper function to make requests to the Daytona REST API.
    """
    url = f"{API_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.request(method, url, headers=headers, params=params, json=json_data, timeout=30)
        response.raise_for_status()
        if response.text:
            return response.json()
        return {}
    except requests.exceptions.RequestException as e:
        logger.error(f"API Request to {path} failed: {e}")
        if e.response is not None:
            logger.error(f"Response status: {e.response.status_code}, body: {e.response.text}")
        raise

def get_node_sandbox(node_name, api_key):
    """
    Retrieves the sandbox details for the node from the API.
    Returns the sandbox dict if it exists, otherwise None.
    """
    try:
        res = call_api(api_key, "GET", "/sandbox")
        items = res.get("items", [])
        for item in items:
            if item.get("name") == node_name:
                return item
        return None
    except Exception as e:
        logger.error(f"Failed to check sandbox for {node_name}: {e}")
        return None

def create_sandbox(node_name):
    """
    Creates a sandbox for the node using the Daytona CLI with custom resources.
    Uses Dockerfile.node to allow resource override.
    """
    logger.info(f"Creating sandbox {node_name} with 8GB RAM, 4 vCPUs...")
    node_dockerfile = os.path.join(WORKSPACE_DIR, "Dockerfile.node")
    try:
        with open(node_dockerfile, "w") as f:
            f.write("FROM daytonaio/sandbox:0.8.0\n")
    except Exception as e:
        logger.error(f"Failed to write Dockerfile.node: {e}")
        return False

    env = get_env_for_node(node_name)
    cmd = [
        DAYTONA_BIN, "create",
        "--name", node_name,
        "--dockerfile", node_dockerfile,
        "--cpu", "4",
        "--memory", "8",
        "--disk", "10",
        "--auto-stop", "43200",     # 30 days
        "--auto-archive", "10080",  # 7 days
        "--auto-delete", "-1"       # Disabled
    ]
    try:
        # Run CLI command
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
        logger.info(f"Sandbox {node_name} created successfully: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create sandbox {node_name}. Exit code: {e.returncode}")
        logger.error(f"Stdout: {e.stdout}")
        logger.error(f"Stderr: {e.stderr}")
        return False

def start_sandbox(node_name):
    """
    Starts the sandbox for the node using the Daytona CLI.
    """
    logger.info(f"Starting sandbox {node_name}...")
    env = get_env_for_node(node_name)
    cmd = [DAYTONA_BIN, "start", node_name]
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
        logger.info(f"Sandbox {node_name} started: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to start sandbox {node_name}. Exit code: {e.returncode}")
        logger.error(f"Stderr: {e.stderr}")
        return False

def bootstrap_nodes():
    """
    Phase 1: Bootstraps configurations and ensures sandboxes exist.
    """
    logger.info("--- Starting Bootstrap Phase ---")
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    
    success_count = 0
    for node_name, api_key in NODE_KEYS.items():
        try:
            write_node_config(node_name, api_key)
            sandbox = get_node_sandbox(node_name, api_key)
            if sandbox:
                logger.info(f"Sandbox {node_name} already exists. Leaving as-is.")
            else:
                logger.info(f"Sandbox {node_name} does not exist. Initiating creation.")
                create_sandbox(node_name)
            success_count += 1
        except Exception as e:
            logger.error(f"Error bootstrapping {node_name}: {e}")
            
    logger.info(f"Bootstrap phase finished. Success: {success_count}/{len(NODE_KEYS)}")

def start_nodes():
    """
    Phase 2: Ensures every sandbox is running.
    """
    logger.info("--- Starting Start Phase ---")
    success_count = 0
    for node_name, api_key in NODE_KEYS.items():
        try:
            sandbox = get_node_sandbox(node_name, api_key)
            if not sandbox:
                logger.warning(f"Sandbox {node_name} not found. Skipping start.")
                continue
                
            state = sandbox.get("state", "stopped")
            if state != "started":
                logger.info(f"Sandbox {node_name} is currently {state}. Starting it...")
                start_sandbox(node_name)
            else:
                logger.info(f"Sandbox {node_name} is already running.")
            success_count += 1
        except Exception as e:
            logger.error(f"Error starting {node_name}: {e}")
            
    logger.info(f"Start phase finished. Success: {success_count}/{len(NODE_KEYS)}")

def refresh_ssh_credentials(expires_in_minutes=1440):
    """
    Phase 3 & 4: Generates fresh SSH credentials for each sandbox.
    Stores the connections in connections.json and connections/node-XX.md.
    """
    logger.info(f"--- Generating/Refreshing SSH Credentials (Expiration: {expires_in_minutes} minutes) ---")
    os.makedirs(CONNECTIONS_DIR, exist_ok=True)
    
    connections_data = {}
    
    # Load existing connections.json if it exists to preserve untouched nodes if needed
    if os.path.exists(CONNECTIONS_JSON):
        try:
            with open(CONNECTIONS_JSON, "r") as f:
                connections_data = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read existing connections.json: {e}")

    success_count = 0
    for node_name, api_key in NODE_KEYS.items():
        try:
            sandbox = get_node_sandbox(node_name, api_key)
            if not sandbox:
                logger.warning(f"Sandbox {node_name} not found. Skipping SSH refresh.")
                continue
                
            sandbox_id = sandbox.get("id")
            state = sandbox.get("state", "stopped")
            
            # Warn if it's not started but generate credentials anyway
            if state != "started":
                logger.warning(f"Sandbox {node_name} is {state}. Starting it first is recommended.")
            
            # Call API to create SSH access
            path = f"/sandbox/{sandbox_id}/ssh-access"
            params = {"expiresInMinutes": expires_in_minutes}
            
            logger.info(f"Requesting new SSH credential for {node_name} ({sandbox_id})...")
            ssh_info = call_api(api_key, "POST", path, params=params)
            
            token = ssh_info.get("token")
            created_at = ssh_info.get("createdAt")
            expires_at = ssh_info.get("expiresAt")
            ssh_command = ssh_info.get("sshCommand")
            
            # Update json structure
            connections_data[node_name] = {
                "sandboxId": sandbox_id,
                "status": state,
                "generatedAt": created_at,
                "expiresAt": expires_at,
                "sshCommand": ssh_command,
                "token": token
            }
            
            # Format and write markdown file
            # Format requested:
            # Node: lfhai-node-01
            # Sandbox ID: xxxxxxxx
            # 
            # Generated:
            # 2026-07-05T12:30:00Z
            # 
            # Expires:
            # 2026-07-06T12:30:00Z
            # 
            # SSH:
            # 
            # ssh <token>@ssh.app.daytona.io
            md_content = (
                f"Node: {node_name}\n"
                f"Sandbox ID: {sandbox_id}\n\n"
                f"Generated:\n{created_at}\n\n"
                f"Expires:\n{expires_at}\n\n"
                f"SSH:\n\n"
                f"{ssh_command}\n"
            )
            
            node_idx = node_name.split("-")[-1] # extract "01", "02", etc.
            md_filename = f"node-{node_idx}.md"
            md_filepath = os.path.join(CONNECTIONS_DIR, md_filename)
            
            with open(md_filepath, "w") as f:
                f.write(md_content)
                
            logger.info(f"Updated SSH credentials for {node_name} -> {md_filepath}")
            success_count += 1
            
        except Exception as e:
            logger.error(f"Failed to refresh SSH credentials for {node_name}: {e}")
            
    # Write to connections.json
    try:
        with open(CONNECTIONS_JSON, "w") as f:
            json.dump(connections_data, f, indent=2)
        logger.info(f"Wrote overall connections to {CONNECTIONS_JSON}")
    except Exception as e:
        logger.error(f"Failed to write connections.json: {e}")
        
    logger.info(f"SSH refresh phase finished. Success: {success_count}/{len(NODE_KEYS)}")

def print_status():
    """
    Queries all nodes and displays their state.
    """
    logger.info("--- Querying Nodes Status ---")
    print(f"\n{'Node Name':<15} | {'Sandbox ID':<36} | {'State':<10} | {'Expires At':<25}")
    print("-" * 92)
    
    connections_data = {}
    if os.path.exists(CONNECTIONS_JSON):
        try:
            with open(CONNECTIONS_JSON, "r") as f:
                connections_data = json.load(f)
        except Exception as e:
            pass

    for node_name, api_key in NODE_KEYS.items():
        sandbox = get_node_sandbox(node_name, api_key)
        if sandbox:
            state = sandbox.get("state", "unknown")
            sandbox_id = sandbox.get("id", "N/A")
        else:
            state = "NOT FOUND"
            sandbox_id = "N/A"
            
        conn_info = connections_data.get(node_name, {})
        expires_at = conn_info.get("expiresAt", "N/A")
        
        print(f"{node_name:<15} | {sandbox_id:<36} | {state:<10} | {expires_at:<25}")
    print()

def daemon_mode():
    """
    Runs in infinite loop.
    1. Bootstrap nodes
    2. Start nodes
    3. Generate/Refresh SSH credentials (set expiration to 24 hours)
    4. Sleep for 23 hours, then repeat from step 2 (ensure started and rotate credentials)
    """
    logger.info("=== Daytona LFHAI Controller Daemon Started ===")
    
    # Run initial bootstrap
    bootstrap_nodes()
    
    while True:
        try:
            start_nodes()
            # Refresh with 1440 mins (24 hour expiration)
            refresh_ssh_credentials(expires_in_minutes=1440)
        except Exception as e:
            logger.error(f"Error in daemon loop iteration: {e}")
            
        logger.info("Daemon sleeping for 23 hours before next refresh cycle...")
        # 23 hours = 23 * 3600 = 82800 seconds
        time.sleep(82800)

def main():
    parser = argparse.ArgumentParser(description="Daytona LFHAI Node Pool Controller")
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")
    
    subparsers.add_parser("bootstrap", help="Phase 1: Write configs and verify/create sandboxes")
    subparsers.add_parser("start", help="Phase 2: Verify and start all stopped sandboxes")
    
    refresh_parser = subparsers.add_parser("refresh", help="Phase 3: Generate/rotate SSH credentials")
    refresh_parser.add_argument("--expires", type=int, default=1440, help="SSH token expiration in minutes (default: 1440)")
    
    subparsers.add_parser("status", help="Get live status table of all sandboxes")
    subparsers.add_parser("daemon", help="Run continuously in background, keeping nodes alive and rotating SSH credentials every 23 hours")
    
    args = parser.parse_args()
    
    if args.command == "bootstrap":
        bootstrap_nodes()
    elif args.command == "start":
        start_nodes()
    elif args.command == "refresh":
        refresh_ssh_credentials(expires_in_minutes=args.expires)
    elif args.command == "status":
        print_status()
    elif args.command == "daemon":
        daemon_mode()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
