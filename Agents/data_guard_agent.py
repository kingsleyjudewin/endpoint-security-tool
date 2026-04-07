#!/usr/bin/env python3
"""
DataGuard Agent - File System & USB Monitor
Connects to DataGuard Admin Server automatically on local network
"""

import os
import sys
import json
import socket
import threading
import time
import platform
from datetime import datetime
from pathlib import Path

# Try to import required modules
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("Error: 'watchdog' module not found.")
    print("Please install it using: pip install watchdog")
    sys.exit(1)

# Platform-specific imports
if platform.system() == 'Windows':
    try:
        import win32file
        import win32con
    except ImportError:
        print("Warning: 'pywin32' not installed. USB monitoring will be limited.")
        print("Install it using: pip install pywin32")

# ========== 🎨 Terminal Colors ==========
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

# ========== 📡 Network Discovery ==========
class ServerDiscovery:
    """Auto-discover admin server on local network"""
    
    @staticmethod
    def get_local_ip():
        """Get the local IP address"""
        try:
            # Create a socket to determine local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except:
            return "127.0.0.1"
    
    @staticmethod
    def get_network_range(local_ip):
        """Get the network range to scan"""
        parts = local_ip.split('.')
        return f"{parts[0]}.{parts[1]}.{parts[2]}"
    
    @staticmethod
    def scan_for_server(port=5555, timeout=0.5):
        """Scan local network for admin server"""
        local_ip = ServerDiscovery.get_local_ip()
        network_base = ServerDiscovery.get_network_range(local_ip)
        
        print(f"{Colors.CYAN}🔍 Scanning network {network_base}.0/24 for admin server...{Colors.END}")
        
        # Common IP ranges to check first
        priority_ips = [
            local_ip,  # Same machine
            f"{network_base}.1",  # Common router/gateway
            f"{network_base}.100",
            f"{network_base}.101",
        ]
        
        # Try priority IPs first
        for ip in priority_ips:
            if ServerDiscovery.try_connect(ip, port, timeout):
                return ip
        
        # Scan remaining IPs in range (1-254)
        for i in range(2, 255):
            ip = f"{network_base}.{i}"
            if ip in priority_ips:
                continue
            if ServerDiscovery.try_connect(ip, port, timeout):
                return ip
        
        return None
    
    @staticmethod
    def try_connect(host, port, timeout):
        """Try to connect to a specific host:port"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                print(f"{Colors.GREEN}✅ Found admin server at {host}:{port}{Colors.END}")
                return True
            return False
        except:
            return False

# ========== 🖥️ Agent Configuration ==========
class AgentConfig:
    def __init__(self):
        self.agent_id = self.generate_agent_id()
        self.server_host = None
        self.server_port = 5555
        self.monitored_paths = []
        self.auto_discover = True
        self.reconnect_delay = 5
    
    def generate_agent_id(self):
        """Generate unique agent ID"""
        hostname = socket.gethostname()
        return f"{hostname}"
    
    def load_config(self):
        """Load configuration from file if exists"""
        config_file = Path.home() / '.dataguard_agent.json'
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    data = json.load(f)
                    self.server_host = data.get('server_host')
                    self.server_port = data.get('server_port', 5555)
                    self.monitored_paths = data.get('monitored_paths', [])
                    self.auto_discover = data.get('auto_discover', True)
                    print(f"{Colors.GREEN}✅ Loaded configuration from {config_file}{Colors.END}")
            except Exception as e:
                print(f"{Colors.YELLOW}⚠️ Could not load config: {e}{Colors.END}")
    
    def save_config(self):
        """Save configuration to file"""
        config_file = Path.home() / '.dataguard_agent.json'
        try:
            data = {
                'server_host': self.server_host,
                'server_port': self.server_port,
                'monitored_paths': self.monitored_paths,
                'auto_discover': self.auto_discover
            }
            with open(config_file, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"{Colors.GREEN}✅ Configuration saved to {config_file}{Colors.END}")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠️ Could not save config: {e}{Colors.END}")

# ========== 📁 File System Monitor ==========
class FileSystemMonitor(FileSystemEventHandler):
    def __init__(self, agent):
        self.agent = agent
    
    def on_created(self, event):
        if not event.is_directory:
            self.agent.send_event({
                'type': 'file_created',
                'object_type': 'directory' if event.is_directory else 'file',
                'filename': os.path.basename(event.src_path),
                'location': os.path.dirname(event.src_path),
                'full_path': event.src_path
            })
    
    def on_deleted(self, event):
        self.agent.send_event({
            'type': 'file_deleted',
            'object_type': 'directory' if event.is_directory else 'file',
            'filename': os.path.basename(event.src_path),
            'location': os.path.dirname(event.src_path),
            'full_path': event.src_path
        })
    
    def on_modified(self, event):
        if not event.is_directory:
            self.agent.send_event({
                'type': 'file_modified',
                'filename': os.path.basename(event.src_path),
                'location': os.path.dirname(event.src_path),
                'full_path': event.src_path
            })
    
    def on_moved(self, event):
        self.agent.send_event({
            'type': 'file_moved',
            'object_type': 'directory' if event.is_directory else 'file',
            'from_name': os.path.basename(event.src_path),
            'to_name': os.path.basename(event.dest_path),
            'from_path': event.src_path,
            'to_path': event.dest_path,
            'location': os.path.dirname(event.dest_path)
        })

# ========== 💾 USB Monitor ==========
class USBMonitor:
    def __init__(self, agent):
        self.agent = agent
        self.running = False
        self.known_drives = set()
        self.thread = None
    
    def start(self):
        """Start USB monitoring"""
        self.running = True
        self.known_drives = self.get_drives()
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        self.agent.log_info("💾", "USB monitoring started")
    
    def stop(self):
        """Stop USB monitoring"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
    
    def get_drives(self):
        """Get list of current drives"""
        drives = set()
        if platform.system() == 'Windows':
            try:
                import string
                from ctypes import windll
                bitmask = windll.kernel32.GetLogicalDrives()
                for letter in string.ascii_uppercase:
                    if bitmask & 1:
                        drives.add(f"{letter}:")
                    bitmask >>= 1
            except:
                pass
        else:
            # Linux/Mac - check /media and /mnt
            for mount_point in ['/media', '/mnt']:
                if os.path.exists(mount_point):
                    for item in os.listdir(mount_point):
                        drives.add(os.path.join(mount_point, item))
        return drives
    
    def _monitor_loop(self):
        """Monitor for drive changes"""
        while self.running:
            try:
                current_drives = self.get_drives()
                
                # Check for new drives (inserted)
                new_drives = current_drives - self.known_drives
                for drive in new_drives:
                    self.agent.send_event({
                        'type': 'usb_inserted',
                        'drive': drive,
                        'message': f"USB drive inserted: {drive}"
                    })
                    self.agent.log_success("🔌", f"USB Inserted: {drive}")
                
                # Check for removed drives
                removed_drives = self.known_drives - current_drives
                for drive in removed_drives:
                    self.agent.send_event({
                        'type': 'usb_removed',
                        'drive': drive,
                        'message': f"USB drive removed: {drive}"
                    })
                    self.agent.log_error("⏏️", f"USB Removed: {drive}")
                
                self.known_drives = current_drives
                time.sleep(2)  # Check every 2 seconds
                
            except Exception as e:
                self.agent.log_error("❌", f"USB monitor error: {e}")
                time.sleep(5)

# ========== 🤖 DataGuard Agent ==========
class DataGuardAgent:
    def __init__(self):
        self.config = AgentConfig()
        self.config.load_config()
        
        self.socket = None
        self.connected = False
        self.running = False
        
        self.observers = []
        self.usb_monitor = USBMonitor(self)
        
        self.lock = threading.Lock()
    
    def log_success(self, emoji, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{Colors.GREEN}[{timestamp}] {emoji} {message}{Colors.END}")
    
    def log_error(self, emoji, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{Colors.RED}[{timestamp}] {emoji} {message}{Colors.END}")
    
    def log_warning(self, emoji, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{Colors.YELLOW}[{timestamp}] {emoji} {message}{Colors.END}")
    
    def log_info(self, emoji, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{Colors.CYAN}[{timestamp}] {emoji} {message}{Colors.END}")
    
    def connect_to_server(self):
        """Connect to admin server"""
        # Auto-discover if no host configured or auto_discover is enabled
        if not self.config.server_host or self.config.auto_discover:
            self.log_info("🔍", "Auto-discovering admin server...")
            discovered_host = ServerDiscovery.scan_for_server(self.config.server_port)
            
            if discovered_host:
                self.config.server_host = discovered_host
                self.config.save_config()
            else:
                self.log_error("❌", "Could not find admin server on network")
                return False
        
        try:
            self.log_info("🔌", f"Connecting to {self.config.server_host}:{self.config.server_port}...")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.config.server_host, self.config.server_port))
            self.connected = True
            
            # Send initial connection event
            self.send_event({
                'type': 'agent_connected',
                'message': f'Agent {self.config.agent_id} connected',
                'platform': platform.system(),
                'hostname': socket.gethostname()
            })
            
            self.log_success("✅", f"Connected to admin server at {self.config.server_host}:{self.config.server_port}")
            return True
            
        except Exception as e:
            self.log_error("❌", f"Connection failed: {e}")
            self.connected = False
            return False
    
    def send_event(self, event_data):
        """Send event to admin server"""
        if not self.connected:
            return
        
        try:
            with self.lock:
                event_data['agent_id'] = self.config.agent_id
                event_data['timestamp'] = datetime.now().isoformat()
                
                message = json.dumps(event_data) + '\n'
                self.socket.sendall(message.encode('utf-8'))
        except Exception as e:
            self.log_error("❌", f"Failed to send event: {e}")
            self.connected = False
    
    def start_monitoring(self, path):
        """Start monitoring a path"""
        try:
            if not os.path.exists(path):
                self.log_error("❌", f"Path does not exist: {path}")
                return False
            
            observer = Observer()
            handler = FileSystemMonitor(self)
            observer.schedule(handler, path, recursive=True)
            observer.start()
            
            self.observers.append(observer)
            self.config.monitored_paths.append(path)
            self.config.save_config()
            
            self.send_event({
                'type': 'monitoring_started',
                'path': path,
                'message': f'Started monitoring {path}'
            })
            
            self.log_success("👁️", f"Started monitoring: {path}")
            return True
            
        except Exception as e:
            self.log_error("❌", f"Failed to monitor {path}: {e}")
            return False
    
    def stop_monitoring(self, path):
        """Stop monitoring a specific path"""
        try:
            removed = False
            for observer in list(self.observers):
                for watch in observer._watches.copy():
                    # Check if this observer is monitoring the requested path
                    if watch.path == path:
                        observer.stop()
                        observer.join(timeout=2)
                        self.observers.remove(observer)
                        removed = True
                        break

            if removed:
                # Remove from monitored paths list and save config
                if path in self.config.monitored_paths:
                    self.config.monitored_paths.remove(path)
                    self.config.save_config()

                # Notify server
                self.send_event({
                    'type': 'monitoring_stopped',
                    'path': path,
                    'message': f'Stopped monitoring {path}'
                })

                self.log_warning("🛑", f"Stopped monitoring: {path}")
                return True
            else:
                self.log_warning("⚠️", f"No active monitor found for: {path}")
                return False

        except Exception as e:
            self.log_error("❌", f"Failed to stop monitoring {path}: {e}")
            return False
    
    def handle_command(self, command_data):
        """Handle commands from admin server"""
        command = command_data.get('command')
        
        if command == 'monitor_path':
            path = command_data.get('path')
            success = self.start_monitoring(path)
            self.send_event({
                'type': 'command_response',
                'command': command,
                'success': success,
                'message': f'{"Successfully started" if success else "Failed to start"} monitoring {path}'
            })

        elif command == 'stop_monitor_path':
            path = command_data.get('path')
            success = self.stop_monitoring(path)
            self.send_event({
                'type': 'command_response',
                'command': command,
                'success': success,
                'message': f'{"Successfully stopped" if success else "Failed to stop"} monitoring {path}'
            })

        elif command == 'ping':
            self.send_event({
                'type': 'command_response',
                'command': 'ping',
                'success': True,
                'message': 'pong'
            })
    
    def listen_for_commands(self):
        """Listen for commands from admin server"""
        buffer = ""
        
        while self.connected and self.running:
            try:
                data = self.socket.recv(4096).decode('utf-8')
                if not data:
                    self.log_warning("⚠️", "Server disconnected")
                    self.connected = False
                    break
                
                buffer += data
                
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        try:
                            command_data = json.loads(line)
                            self.log_info("📨", f"Received command: {command_data.get('command')}")
                            self.handle_command(command_data)
                        except json.JSONDecodeError as e:
                            self.log_error("❌", f"Invalid command JSON: {e}")
            
            except Exception as e:
                if self.connected:
                    self.log_error("❌", f"Error receiving commands: {e}")
                self.connected = False
                break
    
    def run(self):
        """Main agent loop"""
        self.running = True
        
        print_banner()
        
        while self.running:
            if not self.connected:
                if not self.connect_to_server():
                    self.log_warning("⏳", f"Retrying in {self.config.reconnect_delay} seconds...")
                    time.sleep(self.config.reconnect_delay)
                    continue
                
                # Start USB monitoring
                self.usb_monitor.start()
                
                # Restart monitoring for saved paths
                for path in self.config.monitored_paths[:]:
                    if os.path.exists(path):
                        self.start_monitoring(path)
                
                # Start listening for commands
                command_thread = threading.Thread(target=self.listen_for_commands, daemon=True)
                command_thread.start()
            
            time.sleep(1)
    
    def stop(self):
        """Stop the agent"""
        self.running = False
        self.connected = False
        
        # Stop USB monitoring
        self.usb_monitor.stop()
        
        # Stop file system observers
        for observer in self.observers:
            observer.stop()
        
        # Close socket
        if self.socket:
            try:
                self.send_event({
                    'type': 'agent_disconnected',
                    'message': f'Agent {self.config.agent_id} disconnecting'
                })
                self.socket.close()
            except:
                pass
        
        self.log_warning("👋", "Agent stopped")

# ========== 🎨 Banner ==========
def print_banner():
    banner = f"""
{Colors.CYAN}{Colors.BOLD}
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║          🛡️  DATA GUARD AGENT  🛡️                        ║
║                                                           ║
║         File System & USB Monitoring Agent                ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
{Colors.END}
    """
    print(banner)

# ========== ▶️ MAIN ==========
def main():
    agent = DataGuardAgent()
    
    try:
        agent.run()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}⚠️ Shutting down agent...{Colors.END}")
        agent.stop()
        print(f"{Colors.GREEN}✅ Agent stopped. Goodbye!{Colors.END}")

if __name__ == "__main__":
    main()