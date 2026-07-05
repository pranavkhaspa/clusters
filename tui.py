#!/usr/bin/env python3
import os
import sys
import json
import time
import requests
import argparse
import subprocess

# Keyboard input setup for Unix (arrow keys, enter, etc.)
try:
    import tty
    import termios
except ImportError:
    # Fallback if run on Windows or non-standard environment
    tty = None
    termios = None

def getch():
    if not tty or not termios:
        # Fallback raw input
        return sys.stdin.read(1)
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        ch = sys.stdin.read(1)
        # Handle escape sequences for arrows
        if ch == '\x1b':
            ch += sys.stdin.read(2)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch

def parse_key():
    ch = getch()
    if ch == '\x1b[A':
        return 'up'
    elif ch == '\x1b[B':
        return 'down'
    elif ch in ('\r', '\n'):
        return 'enter'
    elif ch.lower() == 'q':
        return 'quit'
    elif ch.lower() == 'r':
        return 'refresh'
    elif ch.lower() == 's':
        return 'toggle_state'
    return ch

# ANSI Escape Codes for Styling
CLEAR_SCREEN = "\033[H\033[2J"
COLOR_RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
BG_BLUE = "\033[44m"
BG_BLACK_FG_WHITE = "\033[7m"

def render_progress_bar(percent, width=15):
    """
    Renders a text progress bar: [████░░░░░░░░]
    """
    filled_len = int(round(width * percent / 100))
    bar = "█" * filled_len + "░" * (width - filled_len)
    
    if percent > 85:
        color = RED
    elif percent > 65:
        color = YELLOW
    else:
        color = GREEN
        
    return f"{color}[{bar}]{COLOR_RESET} {percent}%"

def fetch_data(api_url):
    try:
        res = requests.get(f"{api_url}/api/status", timeout=5)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        return {"error": str(e)}

def trigger_refresh(api_url):
    try:
        requests.post(f"{api_url}/api/refresh", timeout=5)
        return True
    except Exception:
        return False

def trigger_node_start(api_url, node_name):
    try:
        requests.post(f"{api_url}/api/node/{node_name}/start", timeout=5)
        return True
    except Exception:
        return False

def trigger_node_stop(api_url, node_name):
    try:
        requests.post(f"{api_url}/api/node/{node_name}/stop", timeout=5)
        return True
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="Daytona LFHAI Node Pool TUI Connection Manager")
    parser.add_argument(
        "--api",
        default=os.environ.get("DAYTONA_CLUSTER_API", "http://localhost:7860"),
        help="URL of the Daytona cluster API server (default: http://localhost:7860)"
    )
    args = parser.parse_args()
    api_url = args.api.rstrip('/')

    selected_idx = 0
    nodes = []
    global_stats = {}
    last_update = 0
    message_log = ""
    message_time = 0

    def add_message(msg):
        nonlocal message_log, message_time
        message_log = msg
        message_time = time.time()

    # Clear screen initially
    print(CLEAR_SCREEN, end="")

    while True:
        # Fetch status every 5 seconds or on manual reload
        now = time.time()
        if now - last_update > 5 or not nodes:
            data = fetch_data(api_url)
            if "error" in data:
                nodes = []
                global_stats = {"error": data["error"]}
            else:
                nodes = data.get("nodes", [])
                global_stats = data.get("global", {})
            last_update = now

        # Render Header
        print("\033[H", end="") # move cursor to top
        print(f"{BG_BLUE}{BOLD}   DAYTONA LFHAI CLUSTER CONTROL PANEL (TUI)   {COLOR_RESET}")
        print(f"API Endpoint: {CYAN}{api_url}{COLOR_RESET}")
        
        if "error" in global_stats:
            print(f"\n{RED}{BOLD}Connection Error: {global_stats['error']}{COLOR_RESET}")
            print("Trying to reconnect...")
            print("\nPress [Q] to Quit. [R] Force Reload.")
            key = parse_key()
            if key == 'quit':
                break
            elif key == 'refresh':
                nodes = []
            time.sleep(1)
            continue

        online = global_stats.get("online_count", 0)
        total = global_stats.get("total_count", 0)
        last_ref = global_stats.get("last_refresh", "Never")
        if last_ref != "Never":
            try:
                # Format to local display
                last_ref = last_ref.split(".")[0].replace("T", " ") + " UTC"
            except Exception:
                pass
                
        print(f"Nodes Online: {GREEN}{BOLD}{online}/{total}{COLOR_RESET} | Last Key Rotation: {YELLOW}{last_ref}{COLOR_RESET}")
        print("=" * 60)
        print(f"{BOLD}{'  Node Name':<16} | {'State':<10} | {'CPU Load':<8} | {'Memory (RAM) Usage':<20}{COLOR_RESET}")
        print("-" * 60)

        # Render Node List
        for idx, node in enumerate(nodes):
            name = node.get("name")
            status = node.get("status")
            cpu = node.get("cpu_load", 0.0)
            ram_pct = node.get("ram_percent", 0.0)
            
            # Format status color
            status_str = f"{GREEN}online{COLOR_RESET}" if status == "started" else f"{RED}offline{COLOR_RESET}"
            
            # Highlight selected row
            prefix = "> " if idx == selected_idx else "  "
            line = f"{prefix}{name:<14} | {status_str:<19} | {cpu:<8.2f} | {render_progress_bar(ram_pct, width=10)}"
            
            if idx == selected_idx:
                print(f"{BG_BLACK_FG_WHITE}{line}{COLOR_RESET}")
            else:
                print(line)

        print("-" * 60)

        # Render Selected Node Info Card
        if nodes and selected_idx < len(nodes):
            sel_node = nodes[selected_idx]
            print(f"{BOLD}Selected Node Info:{COLOR_RESET}")
            print(f"  Sandbox ID: {CYAN}{sel_node.get('sandboxId')}{COLOR_RESET}")
            print(f"  Region/Target: {sel_node.get('region')} | Expiration: {YELLOW}{sel_node.get('expires_at')}{COLOR_RESET}")
            if sel_node.get("status") == "started":
                print(f"  SSH Command: {BOLD}{sel_node.get('ssh_command')}{COLOR_RESET}")
            else:
                print(f"  SSH Command: {RED}Node offline. Start node to enable SSH.{COLOR_RESET}")
        else:
            print("\nNo nodes found.")

        print("=" * 60)
        
        # Action messages
        if time.time() - message_time < 3:
            print(f"{MAGENTA}{BOLD}{message_log}{COLOR_RESET}")
        else:
            print() # empty line
            
        print(f"{BOLD}Keys:{COLOR_RESET} [↑/↓] Navigate | [Enter] Connect via SSH | [S] Start/Stop Node")
        print(f"      [R] Rotate SSH Keys | [Q] Quit TUI")

        # Read Input (blocking but with short timeout / non-blocking check)
        key = parse_key()
        
        if key == 'quit':
            break
        elif key == 'up':
            if selected_idx > 0:
                selected_idx -= 1
        elif key == 'down':
            if selected_idx < len(nodes) - 1:
                selected_idx += 1
        elif key == 'refresh':
            add_message("Rotating keys cluster-wide...")
            trigger_refresh(api_url)
            # reload data immediately
            nodes = []
        elif key == 'toggle_state':
            if nodes and selected_idx < len(nodes):
                node = nodes[selected_idx]
                name = node.get("name")
                if node.get("status") == "started":
                    add_message(f"Stopping node {name}...")
                    trigger_node_stop(api_url, name)
                else:
                    add_message(f"Starting node {name}...")
                    trigger_node_start(api_url, name)
                nodes = [] # reload immediately
        elif key == 'enter':
            if nodes and selected_idx < len(nodes):
                node = nodes[selected_idx]
                if node.get("status") == "started":
                    ssh_cmd = node.get("ssh_command")
                    if ssh_cmd:
                        print(CLEAR_SCREEN, end="")
                        print(f"Connecting to {BOLD}{node.get('name')}{COLOR_RESET}...")
                        print(f"Running: {CYAN}{ssh_cmd}{COLOR_RESET}\n")
                        # Suspend python TUI and enter interactive SSH session
                        subprocess.run(ssh_cmd, shell=True)
                        print("\nDisconnected. Press any key to return to TUI...")
                        getch()
                        print(CLEAR_SCREEN, end="")
                        # force reload after SSH session
                        nodes = []
                    else:
                        add_message("Error: SSH command not found for node.")
                else:
                    add_message("Error: Node is offline. Start it before connecting.")

if __name__ == "__main__":
    main()
