#!/usr/bin/env python3
"""
Data Guardian Browser Monitor
Real-time Browser History → Local Dashboard API
Supports: Chrome, Edge, Brave, Opera, Firefox (multiple profiles)
Auto-connects via Wi-Fi or mobile hotspot.
"""

import os
import time
import sqlite3
import shutil
import tempfile
import platform
import socket
import requests
import asyncio
import subprocess
from datetime import datetime, timedelta

POLL_INTERVAL = 5.0  # seconds

def find_server():
    """Scan local network for dashboard API"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        try:
            s.close()
        except Exception:
            pass

    # Try localhost first
    test = f"http://127.0.0.1:5000/api/browser_event"
    try:
        requests.get(test.replace('/api/browser_event', '/'), timeout=1)
        print(f"✅ Found dashboard at localhost")
        return test
    except Exception:
        pass

    # Scan local network
    base = ".".join(ip.split(".")[:-1]) + "."
    print(f"🔍 Scanning network {base}0/24 for dashboard...")
    
    for n in range(1, 255):
        test_ip = f"{base}{n}"
        test = f"http://{test_ip}:5000/api/browser_event"
        try:
            # Try to connect to the root endpoint first
            requests.get(f"http://{test_ip}:5000/", timeout=0.3)
            print(f"✅ Found dashboard at {test_ip}")
            return test
        except Exception:
            pass
    
    print("⚠️  No dashboard found, using localhost fallback")
    return "http://127.0.0.1:5000/api/browser_event"

# Discover dashboard API on local network
DASHBOARD_API = None

# Agent identifier (uses computer name)
AGENT_ID = platform.node()

EPOCH_WEBKIT = datetime(1601, 1, 1)

selected_profiles = {}
last_seen = {}

def webkit_to_dt(webkit_micro):
    """Convert WebKit timestamp to datetime"""
    try:
        return EPOCH_WEBKIT + timedelta(microseconds=int(webkit_micro))
    except:
        return None

def dt_to_webkit(dt):
    """Convert datetime to WebKit timestamp"""
    delta = dt - EPOCH_WEBKIT
    return int(delta.total_seconds() * 1_000_000)

def firefox_time_to_dt(micro):
    """Convert Firefox timestamp to datetime"""
    try:
        return datetime.utcfromtimestamp(micro / 1_000_000)
    except:
        return None

def safe_copy(src):
    """Create a temporary copy of database file"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
    tmp.close()
    try:
        shutil.copy2(src, tmp.name)
        return tmp.name
    except Exception as e:
        print(f"❌ Error copying {src}: {e}")
        return None

def detect_profiles():
    """Detect all browser profiles on the system"""
    user = os.path.expanduser("~")
    system = platform.system().lower()

    if system == "windows":
        local = os.environ.get("LOCALAPPDATA", "")
        chrome = os.path.join(local, "Google", "Chrome", "User Data")
        edge = os.path.join(local, "Microsoft", "Edge", "User Data")
        brave = os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")
        opera = os.path.join(local, "Opera Software", "Opera Stable")
        firefox = os.path.join(user, "AppData", "Roaming", "Mozilla", "Firefox", "Profiles")
    elif system == "darwin":
        chrome = os.path.join(user, "Library/Application Support/Google/Chrome")
        edge = os.path.join(user, "Library/Application Support/Microsoft Edge")
        brave = os.path.join(user, "Library/Application Support/BraveSoftware/Brave-Browser")
        opera = os.path.join(user, "Library/Application Support/com.operasoftware.Opera")
        firefox = os.path.join(user, "Library/Application Support/Firefox/Profiles")
    else:  # Linux
        chrome = os.path.join(user, ".config/google-chrome")
        edge = os.path.join(user, ".config/microsoft-edge")
        brave = os.path.join(user, ".config/BraveSoftware/Brave-Browser")
        opera = os.path.join(user, ".config/opera")
        firefox = os.path.join(user, ".mozilla/firefox")

    profiles = {}

    def find_history(base, browser):
        """Find History files in browser profile directories"""
        if os.path.exists(base):
            try:
                for item in os.listdir(base):
                    item_path = os.path.join(base, item)
                    if os.path.isdir(item_path):
                        history_path = os.path.join(item_path, "History")
                        if os.path.isfile(history_path):
                            profiles[f"{browser}:{item}"] = ("webkit", history_path)
                            print(f"  Found {browser} profile: {item}")
            except Exception as e:
                print(f"  Error scanning {browser}: {e}")

    print("🔍 Detecting browser profiles...")
    find_history(chrome, "chrome")
    find_history(edge, "edge")
    find_history(brave, "brave")
    find_history(opera, "opera")

    # Firefox profiles
    if os.path.exists(firefox):
        try:
            for item in os.listdir(firefox):
                item_path = os.path.join(firefox, item)
                if os.path.isdir(item_path):
                    places_path = os.path.join(item_path, "places.sqlite")
                    if os.path.isfile(places_path):
                        profiles[f"firefox:{item}"] = ("firefox", places_path)
                        print(f"  Found firefox profile: {item}")
        except Exception as e:
            print(f"  Error scanning firefox: {e}")

    return profiles

def send_to_dashboard(browser, title, url, ts):
    """Send browsing data to dashboard API"""
    payload = {
        "agent_id": AGENT_ID,
        "browser": browser,
        "title": title,
        "url": url,
        "timestamp": ts
    }
    try:
        response = requests.post(DASHBOARD_API, json=payload, timeout=5)
        if response.status_code == 200:
            print(f"✅ Sent: {browser} - {title[:50]}...")
        else:
            print(f"⚠️  Dashboard returned {response.status_code}")
    except Exception as e:
        print(f"❌ Error sending to dashboard: {e}")

async def monitor():
    """Main monitoring loop"""
    global last_seen
    print(f"\n🔄 Starting monitoring loop (polling every {POLL_INTERVAL}s)...\n")
    
    while selected_profiles:
        for name, (mode, db_path) in selected_profiles.items():
            tmp = None
            try:
                # Copy database to avoid locking issues
                tmp = safe_copy(db_path)
                if not tmp:
                    continue
                
                conn = sqlite3.connect(tmp)
                cur = conn.cursor()

                if mode == "webkit":
                    # Query Chrome/Edge/Brave/Opera history
                    last_time = last_seen.get(db_path, 0)
                    cur.execute(
                        "SELECT url, title, last_visit_time FROM urls WHERE last_visit_time > ? ORDER BY last_visit_time",
                        (last_time,)
                    )
                    rows = cur.fetchall()
                    
                    for url, title, t in rows:
                        dt = webkit_to_dt(t)
                        ts = dt.isoformat() if dt else datetime.utcnow().isoformat()
                        send_to_dashboard(name, title or "(No Title)", url, ts)
                    
                    if rows:
                        last_seen[db_path] = max(t for _, _, t in rows)

                elif mode == "firefox":
                    # Query Firefox history
                    last_time = last_seen.get(db_path, 0)
                    cur.execute(
                        "SELECT url, title, last_visit_date FROM moz_places WHERE last_visit_date > ? ORDER BY last_visit_date",
                        (last_time,)
                    )
                    rows = cur.fetchall()
                    
                    for url, title, t in rows:
                        if t:
                            dt = firefox_time_to_dt(t)
                            ts = dt.isoformat() if dt else datetime.utcnow().isoformat()
                            send_to_dashboard(name, title or "(No Title)", url, ts)
                    
                    if rows:
                        last_seen[db_path] = max(t for _, _, t in rows if t)

                conn.close()
                
            except sqlite3.OperationalError as e:
                print(f"⚠️  Database locked for {name}: {e}")
            except Exception as e:
                print(f"❌ Error monitoring {name}: {e}")
            finally:
                # Clean up temporary file
                if tmp and os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass

        await asyncio.sleep(POLL_INTERVAL)

# -------------------
# Wi-Fi helper functions
# -------------------

def run_cmd(cmd):
    """Run a shell command and return result"""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                          text=True, check=False, timeout=10)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)

def get_current_ssid():
    """Get currently connected Wi-Fi SSID (works with hotspots too)"""
    sysname = platform.system().lower()

    if sysname == "windows":
        rc, out, err = run_cmd(["netsh", "wlan", "show", "interfaces"])
        if rc == 0 and out:
            for line in out.splitlines():
                line = line.strip()
                if line.lower().startswith("ssid") and ":" in line and "bssid" not in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) >= 2:
                        ssid = parts[1].strip()
                        if ssid and ssid.lower() != "none":
                            return ssid
        return None

    elif sysname == "darwin":
        # Try airport command first
        airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
        if os.path.exists(airport_path):
            rc, out, err = run_cmd([airport_path, "-I"])
            if rc == 0 and out:
                for line in out.splitlines():
                    if " SSID:" in line or line.strip().startswith("SSID:"):
                        parts = line.split(":", 1)
                        if len(parts) >= 2:
                            ssid = parts[1].strip()
                            if ssid:
                                return ssid
        
        # Try networksetup
        rc, out, err = run_cmd(["networksetup", "-getairportnetwork", "en0"])
        if rc == 0 and out and ":" in out:
            ssid = out.split(":")[-1].strip()
            if ssid and "not associated" not in ssid.lower():
                return ssid
        return None

    else:  # Linux
        # Try nmcli first
        rc, out, err = run_cmd(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"])
        if rc == 0 and out:
            for line in out.splitlines():
                if line.startswith("yes:"):
                    ssid = line.split(":", 1)[1]
                    if ssid:
                        return ssid
        
        # Try iwgetid
        rc, out, err = run_cmd(["iwgetid", "-r"])
        if rc == 0 and out:
            return out.strip()
        
        return None

def is_network_available():
    """Check if any network connection is available"""
    try:
        # Try to resolve a DNS name
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except:
        return False

# -------------------
# Main entry
# -------------------

def start_monitoring():
    """Initialize and start the monitoring process"""
    global selected_profiles, last_seen, DASHBOARD_API

    print("🚀 Browser Monitoring Agent Starting...")
    print(f"📱 Agent ID: {AGENT_ID}")
    print(f"💻 Platform: {platform.system()}\n")

    # Wait for network connection
    print("📡 Checking for network connection...")
    while not is_network_available():
        print("⚠️  No network connection. Waiting...")
        time.sleep(5)

    current_ssid = get_current_ssid()
    if current_ssid:
        print(f"✅ Connected to Wi-Fi/Hotspot: {current_ssid}\n")
    else:
        print("✅ Network connection detected (Ethernet or unknown Wi-Fi)\n")

    # Find dashboard server
    print("🔍 Looking for dashboard server...")
    DASHBOARD_API = find_server()
    print(f"📍 Dashboard API: {DASHBOARD_API}\n")

    # Detect browser profiles
    selected_profiles = detect_profiles()
    
    if not selected_profiles:
        print("\n❌ No browser profiles detected!")
        print("   Make sure you have Chrome, Firefox, Edge, Brave, or Opera installed.")
        return

    print(f"\n✅ Found {len(selected_profiles)} browser profile(s)")
    
    # Initialize last seen timestamps
    now_webkit = dt_to_webkit(datetime.utcnow())
    now_firefox = int(datetime.utcnow().timestamp() * 1_000_000)
    
    for name, (mode, path) in selected_profiles.items():
        if mode == "webkit":
            last_seen[path] = now_webkit
        else:
            last_seen[path] = now_firefox

    # Start monitoring
    try:
        asyncio.run(monitor())
    except KeyboardInterrupt:
        print("\n\n👋 Monitoring stopped by user")
    except Exception as e:
        print(f"\n❌ Monitoring error: {e}")

if __name__ == "__main__":
    start_monitoring()