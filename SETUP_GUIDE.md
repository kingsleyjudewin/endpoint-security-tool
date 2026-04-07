# DataGuard Admin System - Setup Guide

This guide will help you set up and run the complete DataGuard Admin System with real-time frontend-backend integration.

## 🏗️ Architecture Overview

```
[ Agent Devices ] ⇄ (Socket JSON events) ⇄ [ data_guard_admin.py ]
                                        ⇅
                                [ Flask Server (flask_server.py) ]
                                        ⇅
                                  [ Frontend (index.html + app.js) ]
```

## 📋 Prerequisites

- Python 3.7 or higher
- A local web server for the frontend (Live Server, Python HTTP server, etc.)

## 🚀 Quick Start

### 1. Install Python Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Start the Backend Servers

**Option A: Start both servers together (Recommended)**
```bash
cd backend
python start_servers.py
```

**Option B: Start servers separately**
```bash
# Terminal 1: Socket Server (for agents)
cd backend
python data_guard_admin.py

# Terminal 2: Flask Server (for frontend)
cd backend
python flask_server.py
```

### 3. Start the Frontend

**Option A: Using Live Server (VS Code)**
1. Open the `frontend` folder in VS Code
2. Install "Live Server" extension
3. Right-click on `index.html` → "Open with Live Server"

**Option B: Using Python HTTP Server**
```bash
cd frontend
python -m http.server 5500
```

**Option C: Using Node.js http-server**
```bash
cd frontend
npx http-server -p 5500
```

### 4. Access the Dashboard

Open your browser and go to: `http://localhost:5500`

## 🔧 Configuration

### Backend Configuration

The backend runs on two ports:
- **Socket Server**: `0.0.0.0:5555` (for agent connections)
- **Flask Server**: `127.0.0.1:5000` (for frontend API)

To change ports, modify the startup scripts or use command line arguments:

```bash
# Custom socket server port
python data_guard_admin.py --port 5556

# Custom Flask server port
python flask_server.py --port 5001
```

### Frontend Configuration

The frontend is configured to connect to:
- **API Base URL**: `http://127.0.0.1:5000/api`
- **WebSocket URL**: `ws://127.0.0.1:5000`

To change these URLs, edit `frontend/app.js`:

```javascript
let apiBaseUrl = 'http://YOUR_SERVER:5000/api';
let wsUrl = 'ws://YOUR_SERVER:5000';
```

## 📡 API Endpoints

The Flask server provides the following REST API endpoints:

### GET /api/agents
Returns list of connected agents
```json
{
  "success": true,
  "agents": [
    {
      "id": "Agent-192.168.1.100",
      "name": "Agent-192.168.1.100",
      "ip": "192.168.1.100",
      "status": "connected",
      "connected_at": "2025-01-27T10:30:00Z",
      "last_seen": "2025-01-27T10:35:00Z",
      "monitored_paths": []
    }
  ],
  "total": 1
}
```

### GET /api/events?limit=50
Returns recent events
```json
{
  "success": true,
  "events": [
    {
      "type": "file_created",
      "path": "/Documents/report.pdf",
      "agent": "Agent-192.168.1.100",
      "timestamp": "2025-01-27T10:35:00Z",
      "details": "file_created event"
    }
  ],
  "total": 1
}
```

### GET /api/stats
Returns dashboard statistics
```json
{
  "success": true,
  "stats": {
    "active_agents": 1,
    "total_events": 5,
    "usb_activities": 2,
    "monitored_folders": 0,
    "event_counts": {
      "file_created": 2,
      "usb_inserted": 1,
      "usb_removed": 1
    }
  }
}
```

### POST /api/command
Send command to agent
```json
{
  "agent_id": "Agent-192.168.1.100",
  "command": "monitor_path",
  "path": "/home/user/documents"
}
```

## 🔌 WebSocket Events

The Flask server broadcasts the following WebSocket events:

### Client → Server Events
- `join_dashboard` - Join the dashboard

### Server → Client Events
- `connected` - Connection established
- `dashboard_joined` - Successfully joined dashboard
- `new_event` - New event from agents
- `stats_update` - Updated statistics
- `command_sent` - Command sent to agent

## 🧪 Testing the Integration

### 1. Test Agent Connection

Create a simple test agent to verify the socket connection:

```python
# test_agent.py
import socket
import json
import time

def test_agent():
    # Connect to the admin server
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.connect(('127.0.0.1', 5555))
    
    # Send agent registration
    agent_data = {
        'agent_id': 'Test-Agent-001',
        'type': 'agent_connected',
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ')
    }
    client_socket.send((json.dumps(agent_data) + '\n').encode('utf-8'))
    
    # Send some test events
    for i in range(5):
        event_data = {
            'agent_id': 'Test-Agent-001',
            'type': 'file_created',
            'filename': f'test_file_{i}.txt',
            'location': '/tmp',
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        client_socket.send((json.dumps(event_data) + '\n').encode('utf-8'))
        time.sleep(2)
    
    client_socket.close()

if __name__ == '__main__':
    test_agent()
```

Run the test agent:
```bash
python test_agent.py
```

### 2. Test Frontend Connection

1. Open the frontend dashboard
2. Check the connection status in the top-right corner
3. You should see "Connected" status
4. The test agent should appear in the "Connected Agents" panel
5. Events should appear in the "Real-Time Event Feed"

### 3. Test Commands

1. Select the test agent from the dropdown
2. Enter a path (e.g., `/tmp`)
3. Click "Start Monitoring"
4. Check the console for command confirmation

## 🐛 Troubleshooting

### Common Issues

**1. Frontend shows "Disconnected"**
- Check if Flask server is running on port 5000
- Verify CORS settings in `flask_server.py`
- Check browser console for WebSocket errors

**2. No agents appear**
- Check if socket server is running on port 5555
- Verify agent connections in the socket server terminal
- Test with the provided test agent script

**3. Events not updating**
- Check WebSocket connection status
- Verify events are being sent from agents
- Check browser console for WebSocket errors

**4. Commands not working**
- Verify agent is connected and responsive
- Check Flask server logs for API errors
- Ensure agent supports the command format

### Debug Mode

Enable debug mode for more detailed logging:

```bash
python flask_server.py --debug
```

### Logs

Check the terminal output for:
- Socket server: Agent connections and events
- Flask server: API requests and WebSocket connections
- Browser console: Frontend errors and WebSocket status

## 🔄 Development Workflow

1. **Backend Changes**: Restart the Flask server after code changes
2. **Frontend Changes**: Refresh the browser (Live Server auto-refreshes)
3. **Socket Server**: Restart if you modify the core agent handling logic

## 📝 Notes

- The system maintains backward compatibility with the original socket server
- All existing agent code should work without modification
- The Flask server acts as a bridge between the socket server and frontend
- Real-time updates are handled via WebSocket connections
- REST API provides fallback for data fetching

## 🚀 Production Deployment

For production deployment:

1. Use a production WSGI server (Gunicorn, uWSGI)
2. Configure proper CORS origins
3. Use environment variables for configuration
4. Set up proper logging and monitoring
5. Use HTTPS for secure connections
6. Configure firewall rules for the socket server

Example production setup:
```bash
# Install Gunicorn
pip install gunicorn

# Run with Gunicorn
gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:5000 flask_server:app
```
