document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const browseBtn = document.getElementById('browseBtn');
    const selectedFoldersList = document.getElementById('selectedFoldersList');
    const activateBtn = document.getElementById('activateBtn');
    const rescanBtn = document.getElementById('rescanBtn');
    const clearBtn = document.getElementById('clearBtn');
    
    const indexingPhase = document.getElementById('indexingPhase');
    const indexingPercentage = document.getElementById('indexingPercentage');
    const indexingBarFill = document.getElementById('indexingBarFill');
    const indexingMessage = document.getElementById('indexingMessage');
    const metricCompleted = document.getElementById('metricCompleted');
    const metricChunks = document.getElementById('metricChunks');
    
    const statsAccordionTrigger = document.getElementById('statsAccordionTrigger');
    const statsAccordionContent = document.getElementById('statsAccordionContent');
    const statsJson = document.getElementById('statsJson');
    
    const welcomeContainer = document.getElementById('welcomeContainer');
    const messagesContainer = document.getElementById('messagesContainer');
    const chatSystemInfo = document.getElementById('chatSystemInfo');
    const chatSystemInfoText = document.getElementById('chatSystemInfoText');
    const chatInput = document.getElementById('chatInput');
    const sendBtn = document.getElementById('sendBtn');
    
    const sidebarToggle = document.getElementById('sidebarToggle');
    const appSidebar = document.getElementById('appSidebar');

    // App State
    let messages = [];
    let isIndexingActive = false;
    let isSending = false;
    let selectedDirectories = [];
    let initialDirsSynced = false;

    // ==========================================================================
    // SIDEBAR & RESPONSIVENESS
    // ==========================================================================
    sidebarToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        appSidebar.classList.toggle('active');
        sidebarToggle.classList.toggle('active');
    });

    document.addEventListener('click', (e) => {
        if (window.innerWidth <= 768 && appSidebar.classList.contains('active') && !appSidebar.contains(e.target)) {
            appSidebar.classList.remove('active');
            sidebarToggle.classList.remove('active');
        }
    });

    // Accordion Toggle
    statsAccordionTrigger.addEventListener('click', () => {
        statsAccordionTrigger.classList.toggle('active');
        if (statsAccordionContent.style.maxHeight) {
            statsAccordionContent.style.maxHeight = null;
        } else {
            statsAccordionContent.style.maxHeight = statsAccordionContent.scrollHeight + "px";
        }
    });

    // ==========================================================================
    // FOLDER PICKER & RENDER LOGIC
    // ==========================================================================
    function renderSelectedFolders() {
        selectedFoldersList.innerHTML = '';
        if (selectedDirectories.length === 0) {
            const emptyHint = document.createElement('li');
            emptyHint.style.fontSize = '11px';
            emptyHint.style.color = 'var(--text-muted)';
            emptyHint.style.textAlign = 'center';
            emptyHint.style.padding = '10px 0';
            emptyHint.textContent = 'No folders added yet.';
            selectedFoldersList.appendChild(emptyHint);
            activateBtn.disabled = true;
            return;
        }

        selectedDirectories.forEach((path, index) => {
            const li = document.createElement('li');
            li.className = 'folder-item';
            
            const pathSpan = document.createElement('span');
            pathSpan.className = 'folder-path';
            pathSpan.textContent = path;
            pathSpan.title = path;
            
            const removeBtn = document.createElement('button');
            removeBtn.className = 'folder-remove-btn';
            removeBtn.type = 'button';
            removeBtn.innerHTML = '🗑️';
            removeBtn.title = 'Remove folder';
            removeBtn.addEventListener('click', () => {
                selectedDirectories.splice(index, 1);
                renderSelectedFolders();
                updateActivateButtonState();
            });

            li.appendChild(pathSpan);
            li.appendChild(removeBtn);
            selectedFoldersList.appendChild(li);
        });

        updateActivateButtonState();
    }

    function updateActivateButtonState(isWorking = false) {
        activateBtn.disabled = selectedDirectories.length === 0 || isWorking;
    }

    // Handle Textarea Auto-Resize & Enter key
    chatInput.addEventListener('input', () => {
        chatInput.style.height = 'auto';
        chatInput.style.height = (chatInput.scrollHeight) + 'px';
    });

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // ==========================================================================
    // BACKEND INTEGRATION (API CALLS & POLLING)
    // ==========================================================================
    async function apiCall(endpoint, method = 'GET', body = null) {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
            }
        };
        if (body) {
            options.body = JSON.stringify(body);
        }
        const res = await fetch(endpoint, options);
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || `Server returned ${res.status}`);
        }
        return res.json();
    }

    // Browse native directory selection
    browseBtn.addEventListener('click', async () => {
        try {
            browseBtn.disabled = true;
            const originalText = browseBtn.innerHTML;
            browseBtn.innerHTML = '<span>⏳ Opening Explorer...</span>';
            
            const data = await apiCall('/api/select_directories', 'POST');
            
            browseBtn.disabled = false;
            browseBtn.innerHTML = originalText;

            if (data.directories && data.directories.length > 0) {
                data.directories.forEach(path => {
                    if (!selectedDirectories.includes(path)) {
                        selectedDirectories.push(path);
                    }
                });
                renderSelectedFolders();
            }
        } catch (err) {
            alert(`Error opening folder browser: ${err.message}`);
            browseBtn.disabled = false;
            browseBtn.innerHTML = '<span>📁 Browse & Add Folders...</span>';
        }
    });

    // Activate/Switch index directory list
    activateBtn.addEventListener('click', async () => {
        if (selectedDirectories.length === 0) return;
        
        try {
            activateBtn.disabled = true;
            const data = await apiCall('/api/activate', 'POST', { directories: selectedDirectories });
            messages = [];
            renderMessages();
            await pollStatus();
        } catch (err) {
            alert(`Error switching directories: ${err.message}`);
        } finally {
            activateBtn.disabled = false;
        }
    });

    // Re-scan button click
    rescanBtn.addEventListener('click', async () => {
        try {
            rescanBtn.disabled = true;
            await apiCall('/api/scan', 'POST');
        } catch (err) {
            alert(`Error running scan: ${err.message}`);
        }
    });

    // Clear chat button click
    clearBtn.addEventListener('click', async () => {
        try {
            await apiCall('/api/clear', 'POST');
            messages = [];
            renderMessages();
        } catch (err) {
            alert(`Error clearing chat: ${err.message}`);
        }
    });

    // Send chat message
    sendBtn.addEventListener('click', sendMessage);

    async function sendMessage() {
        const text = chatInput.value.trim();
        if (!text || isSending) return;

        isSending = true;
        chatInput.value = '';
        chatInput.style.height = 'auto';
        chatInput.disabled = true;
        sendBtn.disabled = true;

        // Optimistically add user message
        messages.push({ role: 'user', content: text });
        renderMessages();

        // Add assistant typing placeholder
        const placeholderId = `bot-placeholder-${Date.now()}`;
        appendTypingPlaceholder(placeholderId);

        try {
            const data = await apiCall('/api/chat', 'POST', { prompt: text });
            // Remove typing placeholder
            const placeholder = document.getElementById(placeholderId);
            if (placeholder) placeholder.remove();

            messages.push({
                role: 'assistant',
                content: data.answer,
                citations: data.citations
            });
            renderMessages();
        } catch (err) {
            const placeholder = document.getElementById(placeholderId);
            if (placeholder) placeholder.remove();
            
            messages.push({
                role: 'assistant',
                content: `⚠️ Failed to generate response. Error: ${err.message}`,
                citations: []
            });
            renderMessages();
        } finally {
            isSending = false;
            chatInput.disabled = false;
            chatInput.focus();
            updateInputZoneState();
        }
    }

    function appendTypingPlaceholder(id) {
        welcomeContainer.style.display = 'none';
        messagesContainer.style.display = 'flex';
        
        const row = document.createElement('div');
        row.className = 'chat-message-row assistant';
        row.id = id;
        row.innerHTML = `
            <div class="chat-avatar">AI</div>
            <div class="message-bubble">
                <div class="typing-spinner">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        `;
        messagesContainer.appendChild(row);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    // Poll status loop
    async function pollStatus() {
        try {
            const data = await apiCall('/api/status');
            updateUI(data);
        } catch (err) {
            console.error('Polling status failed', err);
        }
        setTimeout(pollStatus, 1000);
    }

    // ==========================================================================
    // UI UPDATING & RENDERING
    // ==========================================================================
    function updateUI(status) {
        // Sync active directories from server on initial load/switch
        if (status.active_directories && !initialDirsSynced) {
            selectedDirectories = [...status.active_directories];
            initialDirsSynced = true;
            renderSelectedFolders();
        }

        // Index status panel
        indexingPhase.textContent = status.phase ? status.phase.replace(/_/g, ' ').toUpperCase() : 'IDLE';
        const progressPct = Math.round((status.progress || 0) * 100);
        indexingPercentage.textContent = `${progressPct}%`;
        indexingBarFill.style.width = `${progressPct}%`;
        
        if (status.error) {
            indexingMessage.textContent = status.error;
            indexingMessage.style.color = 'var(--accent-error)';
        } else {
            indexingMessage.textContent = status.message || 'Ready';
            indexingMessage.style.color = 'var(--text-muted)';
        }

        metricCompleted.textContent = `${status.completed || 0}/${status.total || 0}`;
        metricChunks.textContent = status.indexed_chunks || 0;

        // Run details accordion JSON
        statsJson.textContent = JSON.stringify(status.stats || {}, null, 2);

        // Enable / disable indexing commands
        const isWorking = status.phase && status.phase !== 'idle' && status.phase !== 'ready' && status.phase !== 'error';
        rescanBtn.disabled = selectedDirectories.length === 0 || isWorking;
        updateActivateButtonState(isWorking);
        browseBtn.disabled = isWorking;
        
        // System block / prompt input enabling
        isIndexingActive = status.ready;
        updateInputZoneState(status);
    }

    function updateInputZoneState(statusInfo) {
        const status = statusInfo || {};
        
        if (!isIndexingActive) {
            chatInput.disabled = true;
            sendBtn.disabled = true;
            chatSystemInfo.style.display = 'flex';
            
            if (selectedDirectories.length > 0) {
                if (status.error) {
                    chatSystemInfo.className = 'chat-system-info error';
                    chatSystemInfoText.textContent = `Error in indexing: ${status.error}`;
                } else {
                    chatSystemInfo.className = 'chat-system-info';
                    chatSystemInfoText.textContent = status.message || 'Preparing local index. Chat will unlock when ready.';
                }
            } else {
                chatSystemInfo.className = 'chat-system-info';
                chatSystemInfoText.textContent = 'Add document folders using the browse button in the sidebar to begin.';
            }
        } else {
            chatSystemInfo.style.display = 'none';
            if (!isSending) {
                chatInput.disabled = false;
                sendBtn.disabled = !chatInput.value.trim();
            }
        }
    }

    // Toggle Send Button on input
    chatInput.addEventListener('input', () => {
        if (isIndexingActive && !isSending) {
            sendBtn.disabled = !chatInput.value.trim();
        }
    });

    // Render message history
    function renderMessages() {
        if (messages.length === 0) {
            welcomeContainer.style.display = 'flex';
            messagesContainer.style.display = 'none';
            messagesContainer.innerHTML = '';
            return;
        }

        welcomeContainer.style.display = 'none';
        messagesContainer.style.display = 'flex';
        messagesContainer.innerHTML = '';

        messages.forEach(msg => {
            const row = document.createElement('div');
            row.className = `chat-message-row ${msg.role}`;
            
            const avatar = document.createElement('div');
            avatar.className = 'chat-avatar';
            avatar.textContent = msg.role === 'user' ? 'U' : 'AI';

            const bubble = document.createElement('div');
            bubble.className = 'message-bubble';
            
            // Format markdown-like text
            const formattedBody = renderMarkdown(msg.content);
            bubble.innerHTML = formattedBody;

            // Render citations
            if (msg.role === 'assistant' && msg.citations && msg.citations.length > 0) {
                const citationsDiv = document.createElement('div');
                citationsDiv.className = 'message-citations';
                citationsDiv.innerHTML = `<div class="citations-title">📁 Source References</div>`;
                
                const list = document.createElement('ul');
                list.className = 'citations-list';
                msg.citations.forEach(citation => {
                    const item = document.createElement('li');
                    item.className = 'citation-item';
                    item.innerHTML = `
                        <span class="citation-file" title="${citation.source_file_name}">${citation.source_file_name}</span>
                        <span class="citation-page">Pg ${citation.page_number}</span>
                    `;
                    list.appendChild(item);
                });
                citationsDiv.appendChild(list);
                bubble.appendChild(citationsDiv);
            }

            row.appendChild(avatar);
            row.appendChild(bubble);
            messagesContainer.appendChild(row);
        });

        // Scroll to bottom
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    // ==========================================================================
    // OFFLINE LIGHTWEIGHT MARKDOWN PARSER
    // ==========================================================================
    function renderMarkdown(text) {
        if (!text) return "";
        let html = text;
        
        // Escape standard HTML tags to prevent XSS (since input is local, but good practice)
        html = html.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        
        // Code blocks: ```language ... ```
        html = html.replace(/```(?:[a-zA-Z0-9]+)?\n([\s\S]*?)\n```/g, '<pre><code>$1</code></pre>');
        
        // Inline code: `code`
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        
        // Bold: **text**
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        
        // Italic: *text*
        html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        
        // Bullet list lines (start with - or * followed by a space)
        const lines = html.split('\n');
        let inList = false;
        const processedLines = lines.map(line => {
            const listMatch = line.match(/^(\s*)[-*]\s+(.+)$/);
            if (listMatch) {
                let prefix = '';
                if (!inList) {
                    inList = true;
                    prefix = '<ul style="margin-left: 1.5rem; margin-top: 0.5rem; margin-bottom: 0.5rem; list-style-type: disc;">';
                }
                return `${prefix}<li>${listMatch[2]}</li>`;
            } else {
                let suffix = '';
                if (inList) {
                    inList = false;
                    suffix = '</ul>';
                }
                return suffix + line;
            }
        });
        
        html = processedLines.join('\n');
        if (inList) html += '</ul>';

        // Paragraph breaks
        html = html.replace(/\n/g, '<br>');
        
        return html;
    }

    // Start Polling Loop immediately on boot
    pollStatus();
    renderSelectedFolders();
});
