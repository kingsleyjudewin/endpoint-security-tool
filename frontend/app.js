// Real Data Storage (fetched from backend)
let agents = [];
let events = [];
let stats = {
    active_agents: 0,
    total_events: 0,
    usb_activities: 0,
    monitored_folders: 0
};

// Application State
let streamPaused = false;
let connectionStatus = false;
let socket = null;
let apiBaseUrl = 'http://127.0.0.1:5000/api';
// Use the HTTP origin for socket.io client so the client performs a proper HTTP handshake.
// This is preferred over raw ws:// for Socket.IO connections.
let wsUrl = 'http://127.0.0.1:5000';
// Track recent created and moved files for quick UI panels
let createdFiles = [];
let movedFiles = [];
// Remember the last monitor request so Stop can default to it if inputs are cleared
let lastMonitor = null;
// Track pending commands waiting for agent acknowledgement
let pendingCommands = {}; // key -> { agentId, command, timeoutId }

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
    initializeApp();
});

function initializeApp() {
    // Initialize WebSocket connection
    initializeWebSocket();
    
    // Setup event listeners
    setupEventListeners();
    
    // Load initial data
    loadInitialData();
    
    // Show welcome message
    showToast('info', 'Connecting...', 'Connecting to DataGuard server...');
}

// ========== WEBSOCKET CONNECTION ==========

function initializeWebSocket() {
    try {
        // Use Socket.IO client
        // For debugging, force websocket transport to avoid polling/upgrade behavior
        // (useful to determine if polling->websocket upgrades are causing reconnects).
        socket = io(wsUrl, { transports: ['websocket'] });
        
        socket.on('connect', () => {
            connectionStatus = true;
            updateConnectionStatus(true);
            showToast('success', 'Connected', 'Connected to DataGuard server');
            socket.emit('join_dashboard');
            console.log('[socket] connect', { id: socket.id, time: new Date().toISOString() });
        });
        
        socket.on('disconnect', (reason) => {
            connectionStatus = false;
            updateConnectionStatus(false);
            showToast('error', 'Disconnected', 'Lost connection to server');
            console.warn('[socket] disconnect', { reason: reason, id: socket.id, time: new Date().toISOString() });
        });

        // More detailed client-side debug logs to correlate with server engineio logs
        socket.on('connect_error', (err) => {
            console.error('[socket] connect_error', err, new Date().toISOString());
        });

        socket.on('reconnect_attempt', (attempt) => {
            console.log('[socket] reconnect_attempt', attempt, new Date().toISOString());
        });
        
        socket.on('new_event', (eventData) => {
            // Always record created/moved file changes for the recent-files panels
            try {
                if (eventData && eventData.type === 'file_created') {
                    createdFiles.unshift(eventData);
                    if (createdFiles.length > 20) createdFiles.pop();
                    renderCreatedFiles();
                }

                if (eventData && eventData.type === 'file_moved') {
                    movedFiles.unshift(eventData);
                    if (movedFiles.length > 20) movedFiles.pop();
                    renderMovedFiles();
                }
            } catch (e) {
                console.error('Error updating recent files lists:', e);
            }

            // If monitoring_started/monitoring_stopped, refresh agents to update monitored paths
            try {
                if (eventData && (eventData.type === 'monitoring_started' || eventData.type === 'monitoring_stopped')) {
                    fetchAgents();
                }
            } catch (e) {}

            if (!streamPaused) {
                addNewEvent(eventData);
            }
        });
        
        socket.on('stats_update', (statsData) => {
            stats = statsData;
            updateStats();
        });
        
        socket.on('command_sent', (commandData) => {
            addConsoleLog(`📤 Command sent to ${commandData.agent_id}: ${commandData.command}`);
            // mark pending on the agent card
            try { setAgentPending(commandData.agent_id, true); } catch(e){}
            // refresh agents so monitored paths reflect immediate removal
            fetchAgents();
        });

        // Listen for command results coming from agents (acknowledgement)
        socket.on('command_result', (result) => {
            try {
                const { agent_id, command, success, message, timestamp } = result || {};
                const text = success
                    ? `✅ ${agent_id}: ${command} — ${message || 'OK'}`
                    : `❌ ${agent_id}: ${command} — ${message || 'Failed'}`;
                addConsoleLog(text);
                showToast(success ? 'success' : 'error', 'Command Result', text);
                updateAgentCommandResult(agent_id, success, message, timestamp);
                // Clear pending timeout for this command (if any)
                try {
                    const key = `${agent_id}:${command}`;
                    if (pendingCommands[key]) {
                        clearTimeout(pendingCommands[key].timeoutId);
                        delete pendingCommands[key];
                        // clear pending UI for this agent
                        setAgentPending(agent_id, false);
                    }
                } catch (e) {
                    console.warn('Failed to clear pending command timer', e);
                }
            } catch (e) {
                console.error('Error handling command_result:', e);
            }
        });
        
        socket.on('connected', (data) => {
            console.log('WebSocket connected:', data.message);
        });
        
        socket.on('dashboard_joined', (data) => {
            console.log('Joined dashboard:', data.message);
        });
        
    } catch (error) {
        console.error('WebSocket connection failed:', error);
        showToast('error', 'Connection Failed', 'Failed to connect to server');
        updateConnectionStatus(false);
    }
}

// ========== API FUNCTIONS ==========

async function fetchAgents() {
    try {
        const response = await fetch(`${apiBaseUrl}/agents`);
        const data = await response.json();
        
        if (data.success) {
            agents = data.agents;
            populateAgentDropdown();
            renderAgents();
            return true;
        } else {
            throw new Error(data.error || 'Failed to fetch agents');
        }
    } catch (error) {
        console.error('Error fetching agents:', error);
        showToast('error', 'Error', 'Failed to fetch agents');
        return false;
    }
}

async function fetchEvents() {
    try {
        const response = await fetch(`${apiBaseUrl}/events?limit=50`);
        const data = await response.json();
        
        if (data.success) {
            events = data.events;
            renderEvents();
            // Populate recent created/moved lists from the fetched events
            try {
                createdFiles = events.filter(e => e.type === 'file_created').slice(-20).reverse();
                movedFiles = events.filter(e => e.type === 'file_moved').slice(-20).reverse();
                renderCreatedFiles();
                renderMovedFiles();
            } catch (e) {
                console.warn('Error populating recent files from initial events', e);
            }
            return true;
        } else {
            throw new Error(data.error || 'Failed to fetch events');
        }
    } catch (error) {
        console.error('Error fetching events:', error);
        showToast('error', 'Error', 'Failed to fetch events');
        return false;
    }
}

async function fetchStats() {
    try {
        const response = await fetch(`${apiBaseUrl}/stats`);
        const data = await response.json();
        
        if (data.success) {
            stats = data.stats;
            updateStats();
            return true;
        } else {
            throw new Error(data.error || 'Failed to fetch stats');
        }
    } catch (error) {
        console.error('Error fetching stats:', error);
        showToast('error', 'Error', 'Failed to fetch stats');
        return false;
    }
}

async function sendCommandToAgent(agentId, command, path = '') {
    try {
        const response = await fetch(`${apiBaseUrl}/command`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                agent_id: agentId,
                command: command,
                path: path
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('success', 'Command Sent', `Command sent to agent ${agentId}`);
            addConsoleLog(`✅ Command sent to ${agentId}: ${command}`);
                // Start a pending timer to wait for agent acknowledgement (command_result)
                try {
                    const key = `${agentId}:${command}`;
                    if (pendingCommands[key]) {
                        clearTimeout(pendingCommands[key].timeoutId);
                    }
                    const timeoutId = setTimeout(() => {
                        // No acknowledgement received in time
                        addConsoleLog(`❗ No response from ${agentId} for ${command}`);
                        showToast('error', 'No Response', `${agentId} did not acknowledge ${command}`);
                        updateAgentCommandResult(agentId, false, 'No response from agent', new Date().toISOString());
                        delete pendingCommands[key];
                    }, 8000); // 8s timeout

                    pendingCommands[key] = { agentId, command, timeoutId };
                } catch (e) {
                    console.warn('Failed to start pending command timer', e);
                }
            return true;
        } else {
            throw new Error(data.error || 'Failed to send command');
        }
    } catch (error) {
        console.error('Error sending command:', error);
        showToast('error', 'Error', `Failed to send command: ${error.message}`);
        return false;
    }
}

async function loadInitialData() {
    // Load all initial data
    await Promise.all([
        fetchAgents(),
        fetchEvents(),
        fetchStats()
    ]);
    
    // Start periodic updates
    startAutoUpdate();
}

function populateAgentDropdown() {
    const select = document.getElementById('agentSelect');
    select.innerHTML = '<option value="">Select Agent</option>';
    
    agents.forEach(agent => {
        const option = document.createElement('option');
        option.value = agent.id;
        option.textContent = `${agent.name} (${agent.ip})`;
        option.disabled = agent.status !== 'connected';
        select.appendChild(option);
    });
}

function updateStats() {
    // Animated counter for stats
    animateCounter('activeAgents', stats.active_agents);
    animateCounter('totalEvents', stats.total_events);
    animateCounter('usbActivities', stats.usb_activities);
    animateCounter('monitoredFolders', stats.monitored_folders);
}

function animateCounter(elementId, targetValue) {
    const element = document.getElementById(elementId);
    const duration = 1000;
    const steps = 50;
    const increment = targetValue / steps;
    let current = 0;
    
    const timer = setInterval(() => {
        current += increment;
        if (current >= targetValue) {
            element.textContent = targetValue;
            clearInterval(timer);
        } else {
            element.textContent = Math.floor(current);
        }
    }, duration / steps);
}

function renderAgents() {
    const container = document.getElementById('agentList');
    container.innerHTML = '';
    
    agents.forEach(agent => {
        const card = document.createElement('div');
        card.className = `agent-card ${agent.status}`;
        
        const statusBadgeClass = agent.status;
        const statusText = agent.status.charAt(0).toUpperCase() + agent.status.slice(1);
        
        card.innerHTML = `
            <div class="agent-header">
                <div class="agent-name">${agent.name}</div>
                <div class="agent-status-badge ${statusBadgeClass}">${statusText}</div>
            </div>
            <div class="agent-info">
                <div><strong>ID:</strong> ${agent.id}</div>
                <div><strong>IP:</strong> ${agent.ip}</div>
                <div><strong>Last Seen:</strong> ${formatTimeAgo(agent.last_seen)}</div>
                <div class="agent-monitored-paths" style="margin-top:6px; font-size:12px; color:#ddd;">
                    ${renderMonitoredPathsInline(agent.monitored_paths || [])}
                </div>
                <div class="agent-command-result" style="margin-top:6px; font-size:12px; color:#999;"></div>
                <div class="agent-pending" style="margin-top:6px; font-size:12px; color:#ffa500; display:none;">⏳ Pending...</div>
            </div>
            <div class="agent-actions">
                <button class="agent-btn" onclick="monitorPath('${agent.id}')">👁️ Monitor</button>
                <button class="agent-btn" onclick="viewLogs('${agent.id}')">📜 Logs</button>
                <button class="agent-btn" onclick="sendCommand('${agent.id}')">⚙️ Command</button>
            </div>
        `;
        
        // add a data attribute for quick lookup when showing command results/pending/paths
        card.dataset.agentId = agent.id;
        container.appendChild(card);
    });
}

function renderMonitoredPathsInline(paths) {
    if (!paths || paths.length === 0) return '<em style="color:#999;">No monitored paths</em>';
    // show up to 2 paths inline for compact UI
    const visible = paths.slice(0,2).map(p => escapeHtml(p));
    const more = paths.length > 2 ? ` (+${paths.length-2} more)` : '';
    return `Monitored: ${visible.join(', ')}${more}`;
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function setAgentPending(agentId, isPending) {
    try {
        const card = document.querySelector(`.agent-card[data-agent-id="${agentId}"]`);
        if (!card) return;
        const pendingEl = card.querySelector('.agent-pending');
        if (!pendingEl) return;
        pendingEl.style.display = isPending ? 'block' : 'none';
    } catch (e) {
        console.warn('Failed to set agent pending UI', e);
    }
}

function updateAgentCommandResult(agentId, success, message = '', timestamp = '') {
    try {
        const card = document.querySelector(`.agent-card[data-agent-id="${agentId}"]`);
        if (!card) return;
        const resultEl = card.querySelector('.agent-command-result');
        if (!resultEl) return;
        resultEl.textContent = `${timestamp ? formatTimeAgo(timestamp) + ' — ' : ''}${message || (success ? 'Command succeeded' : 'Command failed')}`;
        resultEl.style.color = success ? '#00cc66' : '#ff4444';

        // briefly pulse the card to draw attention
        card.style.transition = 'box-shadow 0.2s ease';
        card.style.boxShadow = success ? '0 0 8px rgba(0,204,102,0.3)' : '0 0 8px rgba(255,68,68,0.3)';
        setTimeout(() => { card.style.boxShadow = ''; }, 1200);
    } catch (e) {
        console.warn('Failed to update agent command result UI', e);
    }
}

function renderEvents() {
    const container = document.getElementById('eventFeed');
    
    // Keep only last 20 events
    const recentEvents = events.slice(-20).reverse();
    
    container.innerHTML = '';
    
    recentEvents.forEach(event => {
        const eventItem = createEventElement(event);
        container.appendChild(eventItem);
    });
}

function renderCreatedFiles() {
    const ul = document.getElementById('createdFilesList');
    if (!ul) return;
    ul.innerHTML = '';
    // show up to 10 most recent created files
    createdFiles.slice(0, 10).forEach(ev => {
        const li = document.createElement('li');
        const time = formatTimeAgo(ev.timestamp);
        const agentName = agents.find(a => a.id === ev.agent)?.name || ev.agent;
        const filename = ev.filename || ev.raw?.filename || ev.path || ev.details || 'Unknown';
        li.textContent = `${time} — ${filename} (${agentName})`;
        ul.appendChild(li);
    });
}

function renderMovedFiles() {
    const ul = document.getElementById('movedFilesList');
    if (!ul) return;
    ul.innerHTML = '';
    // show up to 10 most recent moved files
    movedFiles.slice(0, 10).forEach(ev => {
        const li = document.createElement('li');
        const time = formatTimeAgo(ev.timestamp);
        const agentName = agents.find(a => a.id === ev.agent)?.name || ev.agent;
        // Use from_name/to_name if available, otherwise fall back to path/details
        const fromName = ev.from_name || ev.raw?.from_name || '';
        const toName = ev.to_name || ev.raw?.to_name || '';
        let display = '';
        if (fromName && toName) {
            display = `${fromName} → ${toName}`;
        } else if (ev.path) {
            display = ev.path;
        } else if (ev.details) {
            display = ev.details;
        } else if (ev.raw && (ev.raw.from_name || ev.raw.to_name)) {
            display = `${ev.raw.from_name || ''} → ${ev.raw.to_name || ''}`;
        } else {
            display = 'Unknown';
        }

        li.textContent = `${time} — ${display} (${agentName})`;
        ul.appendChild(li);
    });
}

function createEventElement(event) {
    const item = document.createElement('div');
    item.className = `event-item ${event.type}`;
    
    const icon = getEventIcon(event.type);
    const color = getEventColor(event.type);
    const agentName = agents.find(a => a.id === event.agent)?.name || event.agent;
    
    item.innerHTML = `
        <div class="event-header">
            <div class="event-type" style="color: ${color}">
                ${icon} ${formatEventType(event.type)}
            </div>
            <div class="event-time">${formatTimeAgo(event.timestamp)}</div>
        </div>
        <div class="event-path">${event.path}</div>
        <div class="event-agent">Agent: ${agentName} | ${event.details}</div>
    `;
    
    return item;
}

function getEventIcon(type) {
    const icons = {
        file_created: '🆕',
        file_deleted: '🗑️',
        file_modified: '✏️',
        file_moved: '🔀',
        usb_inserted: '🔌',
        usb_removed: '❌'
    };
    return icons[type] || '📄';
}

function getEventColor(type) {
    const colors = {
        file_created: '#00ff88',
        file_deleted: '#ff0055',
        file_modified: '#ffbb00',
        file_moved: '#00fff7',
        usb_inserted: '#a800ff',
        usb_removed: '#a800ff'
    };
    return colors[type] || '#e0e0e0';
}

function formatEventType(type) {
    return type.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
}

function formatTimeAgo(timestamp) {
    const now = new Date();
    const then = new Date(timestamp);
    const diff = Math.floor((now - then) / 1000);
    
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
}

function setupEventListeners() {
    // Start Monitoring Button
    document.getElementById('startMonitorBtn').addEventListener('click', () => {
        const path = document.getElementById('pathInput').value;
        const agentId = document.getElementById('agentSelect').value;
        
        if (!path || !agentId) {
            showToast('error', 'Invalid Input', 'Please enter a path and select an agent');
            return;
        }
        
        startMonitoring(path, agentId);
    });
    // Stop Monitoring Button
    document.getElementById('stopMonitorBtn').addEventListener('click', () => {
        const path = document.getElementById('pathInput').value;
        const agentId = document.getElementById('agentSelect').value;

        if (!agentId) {
            showToast('error', 'Invalid Input', 'Please select an agent to stop monitoring');
            return;
        }

        stopMonitoring(agentId, path);
    });
    
    // Stream Controls
    document.getElementById('pauseStreamBtn').addEventListener('click', toggleStream);
    document.getElementById('clearEventsBtn').addEventListener('click', clearEvents);
    document.getElementById('filterEventsBtn').addEventListener('click', () => {
        showToast('info', 'Filter', 'Event filtering feature coming soon');
    });
    
    // Refresh Agents
    document.getElementById('refreshAgentsBtn').addEventListener('click', () => {
        refreshAgents();
    });
    
    // Console Commands
    document.querySelectorAll('.btn-console').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const command = e.target.dataset.command;
            executeCommand(command);
        });
    });
    
    // Modal Controls
    document.getElementById('modalClose').addEventListener('click', closeModal);
    document.getElementById('modalCancelBtn').addEventListener('click', closeModal);
    document.getElementById('modalOverlay').addEventListener('click', (e) => {
        if (e.target.id === 'modalOverlay') closeModal();
    });
    
    // Navigation
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');
            const page = item.dataset.page;
            showToast('info', 'Navigation', `Switched to ${page} view`);
        });
    });
}

async function startMonitoring(path, agentId) {
    const agent = agents.find(a => a.id === agentId);
    if (!agent) {
        showToast('error', 'Error', 'Agent not found');
        return;
    }
    
    // Send command to backend
    const success = await sendCommandToAgent(agentId, 'monitor_path', path);
    
    if (success) {
        // Clear inputs
        document.getElementById('pathInput').value = '';
        document.getElementById('agentSelect').value = '';
        
        // Remember last monitor so Stop can use it if inputs were cleared
        lastMonitor = { agentId: agentId, path: path };

        // Show toast
        showToast('success', 'Monitoring Started', `${agent.name} is now monitoring ${path}`);
    }
}

async function stopMonitoring(agentId = null, path = '') {
    // If agentId not provided, read from UI
    if (!agentId) {
        agentId = document.getElementById('agentSelect').value;
        path = document.getElementById('pathInput').value || '';
    }

    // Fallback to the last monitor request if inputs are empty
    if (!agentId && lastMonitor) {
        agentId = lastMonitor.agentId;
        path = path || lastMonitor.path || '';
    }

    if (!agentId) {
        showToast('error', 'Invalid Input', 'Please select an agent to stop monitoring (or start a monitor first)');
        return;
    }

    const agent = agents.find(a => a.id === agentId);
    if (!agent) {
        showToast('error', 'Error', 'Agent not found');
        return;
    }

    const success = await sendCommandToAgent(agentId, 'stop_monitor', path || '');
    if (success) {
        // Clear inputs
        document.getElementById('pathInput').value = '';
        document.getElementById('agentSelect').value = '';
        // If we stopped the last monitored path, clear lastMonitor
        if (lastMonitor && lastMonitor.agentId === agentId && (!path || path === lastMonitor.path)) {
            lastMonitor = null;
        }
        showToast('success', 'Monitoring Stopped', `${agent.name} stop requested${path ? ' for ' + path : ''}`);
        addConsoleLog(`⏹️ Stop monitor requested for ${agent.name} ${path}`);
    }
}

function toggleStream() {
    streamPaused = !streamPaused;
    const btn = document.getElementById('pauseStreamBtn');
    btn.textContent = streamPaused ? '▶️' : '⏸️';
    btn.title = streamPaused ? 'Resume Stream' : 'Pause Stream';
    
    showToast('info', streamPaused ? 'Stream Paused' : 'Stream Resumed', 
              streamPaused ? 'Event feed paused' : 'Event feed resumed');
}

function clearEvents() {
    events = [];
    renderEvents();
    showToast('success', 'Events Cleared', 'Event feed has been cleared');
}

async function refreshAgents() {
    addConsoleLog('🔄 Refreshing agent connections...');
    
    const success = await fetchAgents();
    if (success) {
        addConsoleLog('✅ Agent refresh complete');
        showToast('success', 'Agents Refreshed', 'All agent connections updated');
    } else {
        addConsoleLog('❌ Agent refresh failed');
    }
}

function addNewEvent(eventData) {
    // Add new event to the beginning of the array
    events.unshift(eventData);
    
    // Keep only last 50 events
    if (events.length > 50) {
        events = events.slice(0, 50);
    }
    
    // Add to feed with animation
    const eventFeed = document.getElementById('eventFeed');
    const eventElement = createEventElement(eventData);
    eventFeed.insertBefore(eventElement, eventFeed.firstChild);
    
    // Keep only last 20 events in the UI
    if (eventFeed.children.length > 20) {
        eventFeed.removeChild(eventFeed.lastChild);
    }
    
    // Update stats
    stats.total_events = events.length;
    updateStats();
}

async function executeCommand(command) {
    addConsoleLog(`⚙️ Executing command: ${command}`);
    
    switch(command) {
        case 'refresh_agents':
            await refreshAgents();
            break;
        case 'show_stats':
            await fetchStats();
            addConsoleLog(`📊 Active Agents: ${stats.active_agents}`);
            addConsoleLog(`📊 Total Events: ${stats.total_events}`);
            addConsoleLog(`📊 USB Activities: ${stats.usb_activities}`);
            addConsoleLog(`📊 Monitored Folders: ${stats.monitored_folders}`);
            break;
        case 'monitor_path':
            showToast('info', 'Monitor Path', 'Use the top bar to start monitoring a path');
            break;
        case 'clear_logs':
            document.getElementById('consoleLog').innerHTML = '<p class="console-line">Console cleared...</p>';
            showToast('success', 'Console Cleared', 'Command history cleared');
            break;
        default:
            addConsoleLog(`❌ Unknown command: ${command}`);
    }
}

function addConsoleLog(message) {
    const console = document.getElementById('consoleLog');
    const line = document.createElement('p');
    line.className = 'console-line';
    line.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    console.appendChild(line);
    console.scrollTop = console.scrollHeight;
}

function monitorPath(agentId) {
    const agent = agents.find(a => a.id === agentId);
    if (!agent) return;
    
    showModal(
        '👁️ Monitor Path',
        `
            <div style="margin-bottom: 16px;">
                <label style="display: block; margin-bottom: 8px; font-weight: 600;">Agent: ${agent.name}</label>
                <label style="display: block; margin-bottom: 8px; font-weight: 600;">Enter Path to Monitor:</label>
                <input type="text" id="modalPathInput" class="glass-input" placeholder="/path/to/folder" style="width: 100%;">
            </div>
        `,
        () => {
            const path = document.getElementById('modalPathInput').value;
            if (path) {
                startMonitoring(path, agentId);
                closeModal();
            } else {
                showToast('error', 'Invalid Path', 'Please enter a valid path');
            }
        }
    );
}

function viewLogs(agentId) {
    const agent = agents.find(a => a.id === agentId);
    if (!agent) return;
    
    const agentEvents = events.filter(e => e.agent === agentId).slice(-10);
    
    const logsHtml = agentEvents.length > 0 
        ? agentEvents.map(e => `
            <div style="padding: 8px; margin-bottom: 8px; background: rgba(0,0,0,0.3); border-left: 3px solid ${getEventColor(e.type)}; border-radius: 4px;">
                <div style="font-size: 12px; color: #999;">${formatTimeAgo(e.timestamp)}</div>
                <div style="font-weight: 600;">${formatEventType(e.type)}</div>
                <div style="font-size: 12px; font-family: monospace;">${e.path}</div>
            </div>
        `).join('')
        : '<p style="text-align: center; color: #999;">No events recorded</p>';
    
    showModal(
        `📜 Logs - ${agent.name}`,
        `<div style="max-height: 400px; overflow-y: auto;">${logsHtml}</div>`,
        null,
        'Close'
    );
}

function sendCommand(agentId) {
    const agent = agents.find(a => a.id === agentId);
    if (!agent) return;
    
    showModal(
        `⚙️ Send Command - ${agent.name}`,
        `
            <div style="margin-bottom: 16px;">
                <label style="display: block; margin-bottom: 8px; font-weight: 600;">Select Command:</label>
                <select id="modalCommandSelect" class="glass-select" style="width: 100%;">
                    <option value="status">Check Status</option>
                    <option value="restart">Restart Agent</option>
                    <option value="pause">Pause Monitoring</option>
                    <option value="resume">Resume Monitoring</option>
                </select>
            </div>
        `,
        async () => {
            const command = document.getElementById('modalCommandSelect').value;
            const success = await sendCommandToAgent(agentId, command);
            if (success) {
                closeModal();
            }
        }
    );
}

function showModal(title, body, onConfirm = null, confirmText = 'Confirm') {
    const overlay = document.getElementById('modalOverlay');
    const modal = document.getElementById('modal');
    const modalTitle = document.getElementById('modalTitle');
    const modalBody = document.getElementById('modalBody');
    const confirmBtn = document.getElementById('modalConfirmBtn');
    
    modalTitle.textContent = title;
    modalBody.innerHTML = body;
    confirmBtn.textContent = confirmText;
    
    if (onConfirm) {
        confirmBtn.onclick = onConfirm;
        confirmBtn.style.display = 'inline-flex';
    } else {
        confirmBtn.style.display = 'none';
    }
    
    overlay.classList.add('active');
}

function closeModal() {
    document.getElementById('modalOverlay').classList.remove('active');
}

function showToast(type, title, message) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    const icons = {
        success: '✅',
        error: '❌',
        info: 'ℹ️'
    };
    
    toast.innerHTML = `
        <div class="toast-icon">${icons[type] || 'ℹ️'}</div>
        <div class="toast-content">
            <div class="toast-title">${title}</div>
            <div class="toast-message">${message}</div>
        </div>
    `;
    
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.style.animation = 'toastSlideIn 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function startAutoUpdate() {
    // Periodic data refresh (as backup to WebSocket updates)
    setInterval(async () => {
        if (connectionStatus) {
            await Promise.all([
                fetchStats(),
                fetchAgents()
            ]);
        }
    }, 30000); // Refresh every 30 seconds
}


function updateConnectionStatus(connected) {
    connectionStatus = connected;
    const statusElement = document.getElementById('connectionStatus');
    const dot = statusElement.querySelector('.status-dot');
    const text = statusElement.querySelector('.status-text');
    
    if (connected) {
        dot.className = 'status-dot connected';
        text.textContent = 'Connected';
    } else {
        dot.className = 'status-dot disconnected';
        text.textContent = 'Disconnected';
    }
}