#!/usr/bin/env python3
"""
DataGuard Integrated Server
Runs both Flask web server and Socket server for agents
"""

import os
import sys
import json
import socket
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import logging
from collections import defaultdict

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
    UNDERLINE = '\033[4m'

# ========== 📊 Admin Dashboard State ==========
class AdminDashboard:
    def __init__(self):
        self.agents = {}  # agent_id -> {'socket': socket, 'connected_at': timestamp, 'last_seen': timestamp}
        self.events = []  # List of all events
        self.event_counts = defaultdict(int)  # Event type -> count
        self.lock = threading.Lock()
    
    def add_agent(self, agent_id, client_socket):
        """Register a new agent"""
        with self.lock:
            now = datetime.now().isoformat()
            self.agents[agent_id] = {
                'socket': client_socket,
                'connected_at': now,
                'last_seen': now,
                'address': client_socket.getpeername(),
                'monitored_paths': []
            }
    
    def remove_agent(self, agent_id):
        """Remove an agent"""
        with self.lock:
            if agent_id in self.agents:
                del self.agents[agent_id]

    def add_monitored_path(self, agent_id, path):
        """Record that an agent started monitoring a path"""
        with self.lock:
            if agent_id in self.agents:
                paths = self.agents[agent_id].setdefault('monitored_paths', [])
                if path and path not in paths:
                    paths.append(path)

    def remove_monitored_path(self, agent_id, path):
        """Remove a monitored path for an agent"""
        with self.lock:
            if agent_id in self.agents:
                paths = self.agents[agent_id].get('monitored_paths', [])
                if path in paths:
                    paths.remove(path)

    def get_monitored_paths(self, agent_id):
        with self.lock:
            return list(self.agents.get(agent_id, {}).get('monitored_paths', []))
    
    def update_agent_activity(self, agent_id):
        """Update last seen timestamp"""
        with self.lock:
            if agent_id in self.agents:
                self.agents[agent_id]['last_seen'] = datetime.now().isoformat()
    
    def add_event(self, event_data):
        """Add event to history"""
        with self.lock:
            self.events.append(event_data)
            event_type = event_data.get('type', 'unknown')
            self.event_counts[event_type] += 1
            
            # Keep only last 1000 events
            if len(self.events) > 1000:
                self.events = self.events[-1000:]
    
    def get_stats(self):
        """Get dashboard statistics"""
        with self.lock:
            return {
                'total_agents': len(self.agents),
                'active_agents': list(self.agents.keys()),
                'total_events': len(self.events),
                'event_counts': dict(self.event_counts)
            }
    
    def send_command_to_agent(self, agent_id, command_data):
        """Send a command to a specific agent"""
        with self.lock:
            if agent_id not in self.agents:
                return False, "Agent not found or disconnected"
            
            try:
                agent_socket = self.agents[agent_id]['socket']
                message = json.dumps(command_data) + '\n'
                agent_socket.sendall(message.encode('utf-8'))
                return True, "Command sent successfully"
            except Exception as e:
                return False, f"Failed to send command: {e}"

# ========== 📢 Logging System ==========
def log_event(emoji: str, message: str, color=Colors.CYAN):
    """Log events with timestamp and color"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"{color}[{timestamp}] {emoji} {message}{Colors.END}")

def log_success(message: str):
    log_event("✅", message, Colors.GREEN)

def log_error(message: str):
    log_event("❌", message, Colors.RED)

def log_warning(message: str):
    log_event("⚠️", message, Colors.YELLOW)

def log_info(message: str):
    log_event("ℹ️", message, Colors.BLUE)

def log_agent_event(agent_id: str, emoji: str, message: str, color=Colors.CYAN):
    """Log events from agents"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"{color}[{timestamp}] [{agent_id}] {emoji} {message}{Colors.END}")

# ========== 🎯 Event Handlers ==========
def handle_agent_event(dashboard, event_data, socketio_instance=None):
    """Handle events from agents"""
    agent_id = event_data.get('agent_id', 'Unknown')
    event_type = event_data.get('type', 'unknown')
    
    dashboard.update_agent_activity(agent_id)
    dashboard.add_event(event_data)
    
    # Broadcast to web clients
    if socketio_instance:
        broadcast_event(event_data, socketio_instance)
    
    # Route event to appropriate handler
    if event_type == 'agent_connected':
        log_agent_event(agent_id, "🤖", f"Agent connected", Colors.GREEN)
    
    elif event_type == 'agent_disconnected':
        log_agent_event(agent_id, "🔴", f"Agent disconnected", Colors.RED)
    
    elif event_type == 'command_response':
        success = event_data.get('success', False)
        message = event_data.get('message', '')
        if success:
            log_agent_event(agent_id, "✅", f"Command executed: {message}", Colors.GREEN)
        else:
            log_agent_event(agent_id, "❌", f"Command failed: {message}", Colors.RED)
        # Emit a structured command result to web clients so UI can acknowledge per-agent
        try:
            if socketio_instance:
                # Normalize command name for UI: if agent sent 'stop_monitor_path' map it back to 'stop_monitor'
                agent_cmd = event_data.get('command', '')
                ui_command = 'stop_monitor' if agent_cmd == 'stop_monitor_path' else agent_cmd
                socketio_instance.emit('command_result', {
                    'agent_id': agent_id,
                    'command': ui_command,
                    'success': success,
                    'message': message,
                    'timestamp': event_data.get('timestamp', datetime.now().isoformat())
                })
        except Exception as e:
            logger.error(f"Failed to emit command_result event: {e}")
        # If this was a successful stop_monitor acknowledgement (from agent or UI-mapped name), update monitored paths
        try:
            cmd = event_data.get('command', '')
            if cmd in ('stop_monitor', 'stop_monitor_path') and success:
                path = event_data.get('path', '')
                dashboard.remove_monitored_path(agent_id, path)
                broadcast_stats()
        except Exception as e:
            logger.error(f"Error updating monitored paths after command_response: {e}")
    
    elif event_type == 'log':
        level = event_data.get('level', 'info')
        emoji = event_data.get('emoji', 'ℹ️')
        message = event_data.get('message', '')
        color_map = {
            'success': Colors.GREEN,
            'error': Colors.RED,
            'warning': Colors.YELLOW,
            'info': Colors.BLUE
        }
        log_agent_event(agent_id, emoji, message, color_map.get(level, Colors.CYAN))
    
    elif event_type == 'file_created':
        obj_type = event_data.get('object_type', 'file')
        filename = event_data.get('filename', '')
        location = event_data.get('location', '')
        log_agent_event(agent_id, "🆕", f"{obj_type.capitalize()} created: {filename} in {location}", Colors.GREEN)
    
    elif event_type == 'file_deleted':
        obj_type = event_data.get('object_type', 'file')
        filename = event_data.get('filename', '')
        location = event_data.get('location', '')
        log_agent_event(agent_id, "🗑️", f"{obj_type.capitalize()} deleted: {filename} from {location}", Colors.RED)
    
    elif event_type == 'file_modified':
        filename = event_data.get('filename', '')
        location = event_data.get('location', '')
        log_agent_event(agent_id, "✏️", f"File modified: {filename} in {location}", Colors.YELLOW)
    
    elif event_type == 'file_moved':
        obj_type = event_data.get('object_type', 'file')
        from_name = event_data.get('from_name', '')
        to_name = event_data.get('to_name', '')
        location = event_data.get('location', '')
        log_agent_event(agent_id, "🔀", f"{obj_type.capitalize()} moved: {from_name} → {to_name} in {location}", Colors.CYAN)
    
    elif event_type == 'usb_inserted':
        drive = event_data.get('drive', '')
        log_agent_event(agent_id, "🔌", f"USB Inserted: {drive}", Colors.GREEN)
    
    elif event_type == 'usb_removed':
        drive = event_data.get('drive', '')
        log_agent_event(agent_id, "⏏️", f"USB Removed: {drive}", Colors.RED)
    
    elif event_type == 'monitoring_started':
        path = event_data.get('path', '')
        log_agent_event(agent_id, "👁️", f"Started monitoring: {path}", Colors.GREEN)
        # Record the monitored path for this agent so UI can show/stop it
        try:
            dashboard.add_monitored_path(agent_id, path)
            # Broadcast updated stats to clients
            broadcast_stats()
        except Exception as e:
            logger.error(f"Failed to record monitored path: {e}")

    elif event_type == 'monitoring_stopped':
        path = event_data.get('path', '')
        log_agent_event(agent_id, "🛑", f"Stopped monitoring: {path}", Colors.YELLOW)
        try:
            dashboard.remove_monitored_path(agent_id, path)
            broadcast_stats()
        except Exception as e:
            logger.error(f"Failed to remove monitored path: {e}")

# ========== 🌐 Flask App Setup ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dataguard_secret_key_2024'
CORS(app, origins=["http://localhost:5500", "http://127.0.0.1:5500", "http://localhost:5502", "http://127.0.0.1:5502", "http://localhost:3000"])

socketio = SocketIO(app, cors_allowed_origins=["http://localhost:5500", "http://127.0.0.1:5500", "http://localhost:5502", "http://127.0.0.1:5502", "http://localhost:3000"], logger=True, engineio_logger=True)

# Global dashboard instance
dashboard = None
connected_clients = set()

@app.route('/')
def index():
    """Serve the main dashboard page"""
    return render_template('index.html')

# ========== REST API ENDPOINTS ==========

@app.route('/api/agents', methods=['GET'])
def get_agents():
    """Get list of connected agents"""
    try:
        with dashboard.lock:
            agents_list = []
            for agent_id, agent_info in dashboard.agents.items():
                agents_list.append({
                    'id': agent_id,
                    'name': agent_id,
                    'ip': agent_info.get('address', ['Unknown'])[0] if agent_info.get('address') else 'Unknown',
                    'status': 'connected',
                    'connected_at': agent_info.get('connected_at', ''),
                    'last_seen': agent_info.get('last_seen', ''),
                    'monitored_paths': list(agent_info.get('monitored_paths', []))
                })
            
            return jsonify({
                'success': True,
                'agents': agents_list,
                'total': len(agents_list)
            })
    except Exception as e:
        logger.error(f"Error getting agents: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/events', methods=['GET'])
def get_events():
    """Get recent events (last 50)"""
    try:
        limit = request.args.get('limit', 50, type=int)
        with dashboard.lock:
            recent_events = dashboard.events[-limit:] if dashboard.events else []
            
            formatted_events = []
            for event in recent_events:
                formatted_events.append({
                    'type': event.get('type', 'unknown'),
                    # Prefer full_path when available so frontend can match against monitored folders
                    'path': event.get('full_path', event.get('location', event.get('filename', event.get('message', 'Unknown')))),
                    'full_path': event.get('full_path', ''),
                    'agent': event.get('agent_id', 'Unknown'),
                    'timestamp': event.get('timestamp', datetime.now().isoformat()),
                    'details': event.get('message', f"{event.get('type', 'unknown')} event")
                })
            
            return jsonify({
                'success': True,
                'events': formatted_events,
                'total': len(formatted_events)
            })
    except Exception as e:
        logger.error(f"Error getting events: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get dashboard statistics"""
    try:
        stats = dashboard.get_stats()
        usb_activities = stats['event_counts'].get('usb_inserted', 0) + stats['event_counts'].get('usb_removed', 0)
        
        return jsonify({
            'success': True,
            'stats': {
                'active_agents': stats['total_agents'],
                'total_events': stats['total_events'],
                'usb_activities': usb_activities,
                'monitored_folders': 0,
                'event_counts': stats['event_counts']
            }
        })
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/command', methods=['POST'])
def send_command():
    """Send command to agent"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data provided'}), 400
        
        agent_id = data.get('agent_id')
        command = data.get('command')
        path = data.get('path', '')
        
        if not agent_id or not command:
            return jsonify({'success': False, 'error': 'agent_id and command are required'}), 400
        
        command_data = {
            'timestamp': datetime.now().isoformat()
        }

        # Map UI command names to agent-side command names when necessary
        # UI uses 'stop_monitor' while the agent expects 'stop_monitor_path'
        original_command = command
        agent_command = 'stop_monitor_path' if command == 'stop_monitor' else command
        command_data['command'] = agent_command

        # Always include path if provided (useful for stop_monitor and other commands)
        if path:
            command_data['path'] = path
        
        success, message = dashboard.send_command_to_agent(agent_id, command_data)
        
        if success:
            # Emit command_sent using the UI-visible command name (original_command)
            socketio.emit('command_sent', {
                'agent_id': agent_id,
                'command': original_command,
                'path': path,
                'timestamp': datetime.now().isoformat()
            })
            # If we just requested a stop for a monitored path, remove it locally so the UI reflects the stop immediately.
            try:
                if original_command == 'stop_monitor' and path:
                    dashboard.remove_monitored_path(agent_id, path)
                    # broadcast updated stats and let clients know
                    broadcast_stats()
            except Exception as e:
                logger.error(f"Error removing monitored path after stop request: {e}")
            
            return jsonify({
                'success': True,
                'message': message,
                'command': command_data
            })
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Error sending command: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ========== WEBSOCKET EVENTS ==========

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    connected_clients.add(request.sid)
    # Log extra context for debugging reconnects
    remote = request.remote_addr
    referer = request.headers.get('Referer', 'Unknown')
    ua = request.headers.get('User-Agent', 'Unknown')
    logger.info(f"Web client connected: {request.sid} remote={remote} referer={referer} ua={ua}")
    emit('connected', {'message': 'Connected to DataGuard server'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    connected_clients.discard(request.sid)
    # Log extra context for debugging reconnects
    remote = request.remote_addr
    referer = request.headers.get('Referer', 'Unknown')
    ua = request.headers.get('User-Agent', 'Unknown')
    logger.info(f"Web client disconnected: {request.sid} remote={remote} referer={referer} ua={ua}")

@socketio.on('join_dashboard')
def handle_join_dashboard():
    """Client joins the dashboard"""
    emit('dashboard_joined', {'message': 'Joined dashboard successfully'})

# ========== EVENT BROADCASTING ==========

def broadcast_event(event_data, socketio_instance):
    """Broadcast event to all connected WebSocket clients"""
    if connected_clients:
        # Include useful raw fields so the frontend can render created/moved events with detail
        formatted_event = {
            'type': event_data.get('type', 'unknown'),
            # Prefer full_path when available so UI and filtering get the absolute path
            'full_path': event_data.get('full_path', ''),
            'path': event_data.get('full_path', event_data.get('location', event_data.get('filename', event_data.get('message', 'Unknown')))),
            'filename': event_data.get('filename', ''),
            'from_name': event_data.get('from_name', ''),
            'to_name': event_data.get('to_name', ''),
            'location': event_data.get('location', ''),
            'object_type': event_data.get('object_type', ''),
            'agent': event_data.get('agent_id', 'Unknown'),
            'timestamp': event_data.get('timestamp', datetime.now().isoformat()),
            'details': event_data.get('message', f"{event_data.get('type', 'unknown')} event"),
            'raw': event_data
        }

        # If this is a file event, only broadcast if it belongs to an actively monitored path
        try:
            et = formatted_event.get('type', '')
            if et and et.startswith('file_'):
                agent_id = formatted_event.get('agent')
                # Determine event path to compare against monitored paths
                ev_path = ''
                raw = formatted_event.get('raw', {}) or {}
                # Consider common path fields: full_path, to_path, from_path, path, filename, location
                ev_path = (
                    raw.get('full_path') or raw.get('to_path') or raw.get('from_path') or raw.get('path')
                    or formatted_event.get('full_path') or formatted_event.get('path') or raw.get('filename') or formatted_event.get('filename')
                    or raw.get('location') or formatted_event.get('location') or ''
                )
                # Normalize for comparison
                ev_path_norm = ev_path.lower() if isinstance(ev_path, str) else ''
                monitored = dashboard.get_monitored_paths(agent_id)
                # If monitored paths exist, require the event path to be under one of them
                if monitored:
                    matched = False
                    for mp in monitored:
                        try:
                            if not mp:
                                continue
                            if ev_path_norm.startswith(mp.lower()):
                                matched = True
                                break
                        except Exception:
                            continue
                    if not matched:
                        # Skip broadcasting events outside monitored paths (this enforces stop-monitor locally)
                        # Debug assist: when no match, log a short debug line so we can see why events are dropped
                        logger.debug(f"Dropping event for agent={agent_id} type={et} ev_path='{ev_path}' monitored={monitored}")
                        return
        except Exception as e:
            logger.debug(f"Error filtering broadcast_event by monitored paths: {e}")

        socketio_instance.emit('new_event', formatted_event)

def broadcast_stats():
    """Broadcast updated stats to all connected clients"""
    if connected_clients:
        try:
            stats = dashboard.get_stats()
            usb_activities = stats['event_counts'].get('usb_inserted', 0) + stats['event_counts'].get('usb_removed', 0)
            # Count monitored folders across agents
            monitored_folders = 0
            with dashboard.lock:
                for a in dashboard.agents.values():
                    monitored_folders += len(a.get('monitored_paths', []))

            formatted_stats = {
                'active_agents': stats['total_agents'],
                'total_events': stats['total_events'],
                'usb_activities': usb_activities,
                'monitored_folders': monitored_folders,
                'event_counts': stats['event_counts']
            }
            
            socketio.emit('stats_update', formatted_stats)
        except Exception as e:
            logger.error(f"Error broadcasting stats: {e}")

# ========== SOCKET SERVER FOR AGENTS ==========

def handle_client(client_socket, address):
    """Handle individual agent connection"""
    agent_id = None
    buffer = ""
    
    try:
        log_info(f"New agent connection from {address}")
        
        while True:
            data = client_socket.recv(4096).decode('utf-8')
            if not data:
                break
            
            buffer += data
            
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                if line.strip():
                    try:
                        event_data = json.loads(line)
                        
                        if agent_id is None:
                            agent_id = event_data.get('agent_id', f"Agent-{address[0]}")
                            dashboard.add_agent(agent_id, client_socket)
                            broadcast_stats()
                        
                        handle_agent_event(dashboard, event_data, socketio)
                        
                    except json.JSONDecodeError as e:
                        log_error(f"Invalid JSON from {agent_id or address}: {e}")
    
    except Exception as e:
        log_error(f"Error handling agent {agent_id or address}: {e}")
    
    finally:
        if agent_id:
            dashboard.remove_agent(agent_id)
            log_warning(f"Agent {agent_id} disconnected")
            broadcast_stats()
        client_socket.close()

def start_socket_server(host='0.0.0.0', port=5555):
    """Start the socket server for agents"""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(5)
    
    log_success(f"Agent Socket Server started on {host}:{port}")
    log_info("Waiting for agent connections...")
    
    try:
        while True:
            client_socket, address = server_socket.accept()
            client_thread = threading.Thread(
                target=handle_client,
                args=(client_socket, address),
                daemon=True
            )
            client_thread.start()
    except KeyboardInterrupt:
        log_warning("\nShutting down socket server...")
        server_socket.close()

# ========== BACKGROUND TASKS ==========

def stats_broadcaster():
    """Periodically broadcast stats updates"""
    while True:
        try:
            time.sleep(5)
            broadcast_stats()
        except Exception as e:
            logger.error(f"Error in stats broadcaster: {e}")

# ========== BANNER ==========

def print_banner():
    """Display startup banner"""
    banner = f"""
{Colors.CYAN}{Colors.BOLD}
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║          🛡️  DATA GUARD SERVER  🛡️                       ║
║                                                           ║
║         Central Monitoring & Control System               ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
{Colors.END}
    """
    print(banner)

def get_local_ip():
    """Get local IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except:
        return "127.0.0.1"

# ========== MAIN ==========

def main():
    global dashboard
    
    print_banner()
    
    # Initialize dashboard
    dashboard = AdminDashboard()
    
    # Get local IP
    local_ip = get_local_ip()
    
    # Configuration
    flask_host = '0.0.0.0'  # Listen on all interfaces
    flask_port = 5000
    socket_host = '0.0.0.0'  # Listen on all interfaces
    socket_port = 5555
    
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='DataGuard Integrated Server')
    parser.add_argument('--flask-port', type=int, default=5000, help='Flask web server port')
    parser.add_argument('--socket-port', type=int, default=5555, help='Agent socket server port')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    args = parser.parse_args()
    
    flask_port = args.flask_port
    socket_port = args.socket_port
    
    # Display connection info
    print(f"\n{Colors.GREEN}{Colors.BOLD}🌐 SERVER INFORMATION{Colors.END}")
    print(f"{Colors.CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print(f"{Colors.YELLOW}Local IP Address:{Colors.END} {local_ip}")
    print(f"{Colors.YELLOW}Web Dashboard:{Colors.END} http://{local_ip}:{flask_port}")
    print(f"{Colors.YELLOW}Agent Connection:{Colors.END} {local_ip}:{socket_port}")
    print(f"{Colors.CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}\n")
    
    # Start socket server in background thread
    socket_thread = threading.Thread(
        target=start_socket_server,
        args=(socket_host, socket_port),
        daemon=True
    )
    socket_thread.start()
    
    # Start stats broadcaster
    stats_thread = threading.Thread(target=stats_broadcaster, daemon=True)
    stats_thread.start()
    
    # Give socket server time to start
    time.sleep(1)
    
    # Start Flask server (blocking)
    log_info(f"Starting Flask web server on {flask_host}:{flask_port}")
    socketio.run(app, host=flask_host, port=flask_port, debug=args.debug, allow_unsafe_werkzeug=True)

if __name__ == '__main__':
    main()