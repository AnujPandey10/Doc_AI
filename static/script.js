document.addEventListener('DOMContentLoaded', () => {
    // ══════════════════════════════════════════════════════════════════════
    // AUTH CHECK — Redirect to login if no valid token
    // ══════════════════════════════════════════════════════════════════════
    const token = localStorage.getItem('rag_token') || sessionStorage.getItem('rag_token');
    if (!token) {
        window.location.href = '/login.html';
        return;
    }

    let currentUser = null;
    try {
        const stored = localStorage.getItem('rag_user') || sessionStorage.getItem('rag_user');
        if (stored) currentUser = JSON.parse(stored);
    } catch (e) { /* ignore */ }

    // ══════════════════════════════════════════════════════════════════════
    // DOM ELEMENTS
    // ══════════════════════════════════════════════════════════════════════
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

    // PDF Viewer
    const pdfViewerPanel = document.getElementById('pdfViewerPanel');
    const pdfViewerFrame = document.getElementById('pdfViewerFrame');
    const pdfViewerFileName = document.getElementById('pdfViewerFileName');
    const pdfPageBadge = document.getElementById('pdfPageBadge');
    const pdfCloseBtn = document.getElementById('pdfCloseBtn');

    // User UI
    const userBadge = document.getElementById('userBadge');
    const userAvatar = document.getElementById('userAvatar');
    const userName = document.getElementById('userName');
    const userRole = document.getElementById('userRole');
    const userMenuBtn = document.getElementById('userMenuBtn');
    const userDropdown = document.getElementById('userDropdown');
    const logoutBtn = document.getElementById('logoutBtn');
    const changePasswordBtn = document.getElementById('changePasswordBtn');
    const manageUsersBtn = document.getElementById('manageUsersBtn');

    // Admin Modal
    const adminModalOverlay = document.getElementById('adminModalOverlay');
    const adminModalClose = document.getElementById('adminModalClose');
    const createUserForm = document.getElementById('createUserForm');
    const createUserError = document.getElementById('createUserError');
    const usersTableBody = document.getElementById('usersTableBody');

    // Password Modal
    const passwordModalOverlay = document.getElementById('passwordModalOverlay');
    const passwordModalClose = document.getElementById('passwordModalClose');
    const changePasswordForm = document.getElementById('changePasswordForm');
    const changePasswordError = document.getElementById('changePasswordError');

    // ══════════════════════════════════════════════════════════════════════
    // APP STATE
    // ══════════════════════════════════════════════════════════════════════
    let messages = [];
    let isIndexingActive = false;
    let isSending = false;
    let selectedDirectories = [];
    let initialDirsSynced = false;
    let activeWs = null;

    // ══════════════════════════════════════════════════════════════════════
    // ROLE-BASED UI
    // ══════════════════════════════════════════════════════════════════════
    function applyRoleVisibility() {
        const isAdmin = currentUser && currentUser.role === 'admin';
        document.querySelectorAll('.admin-only').forEach(el => {
            el.style.display = isAdmin ? '' : 'none';
        });
        if (currentUser) {
            userName.textContent = currentUser.username;
            userRole.textContent = currentUser.role;
            userAvatar.textContent = currentUser.username.charAt(0).toUpperCase();
        }
    }
    applyRoleVisibility();

    // ══════════════════════════════════════════════════════════════════════
    // AUTH API HELPER
    // ══════════════════════════════════════════════════════════════════════
    async function apiCall(endpoint, method = 'GET', body = null) {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
            }
        };
        if (body) {
            options.body = JSON.stringify(body);
        }
        const res = await fetch(endpoint, options);
        if (res.status === 401) {
            // Token expired — redirect to login
            localStorage.removeItem('rag_token');
            localStorage.removeItem('rag_user');
            sessionStorage.removeItem('rag_token');
            sessionStorage.removeItem('rag_user');
            window.location.href = '/login.html';
            throw new Error('Session expired');
        }
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || `Server returned ${res.status}`);
        }
        return res.json();
    }

    // ══════════════════════════════════════════════════════════════════════
    // USER DROPDOWN & LOGOUT
    // ══════════════════════════════════════════════════════════════════════
    userMenuBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        userDropdown.style.display = userDropdown.style.display === 'none' ? 'flex' : 'none';
    });

    document.addEventListener('click', (e) => {
        if (!userBadge.contains(e.target)) {
            userDropdown.style.display = 'none';
        }
    });

    logoutBtn.addEventListener('click', () => {
        localStorage.removeItem('rag_token');
        localStorage.removeItem('rag_user');
        sessionStorage.removeItem('rag_token');
        sessionStorage.removeItem('rag_user');
        window.location.href = '/login.html';
    });

    // ══════════════════════════════════════════════════════════════════════
    // SIDEBAR & RESPONSIVENESS
    // ══════════════════════════════════════════════════════════════════════
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

    // ══════════════════════════════════════════════════════════════════════
    // FOLDER PICKER & RENDER LOGIC
    // ══════════════════════════════════════════════════════════════════════
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

    // ══════════════════════════════════════════════════════════════════════
    // BACKEND INTEGRATION (API CALLS & POLLING)
    // ══════════════════════════════════════════════════════════════════════

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

    // ══════════════════════════════════════════════════════════════════════
    // WEBSOCKET STREAMING CHAT
    // ══════════════════════════════════════════════════════════════════════
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

        // Create streaming assistant bubble
        const streamId = `stream-${Date.now()}`;
        appendStreamingBubble(streamId);

        // Try WebSocket streaming first
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}/ws/chat?token=${encodeURIComponent(token)}`;

        let streamSucceeded = false;

        try {
            await new Promise((resolve, reject) => {
                const ws = new WebSocket(wsUrl);
                activeWs = ws;
                let fullAnswer = '';
                let connected = false;

                ws.onopen = () => {
                    connected = true;
                    ws.send(JSON.stringify({ prompt: text }));
                };

                ws.onmessage = (event) => {
                    try {
                        const data = JSON.parse(event.data);

                        if (data.type === 'token') {
                            fullAnswer += data.content;
                            updateStreamingBubble(streamId, fullAnswer);
                        } else if (data.type === 'done') {
                            streamSucceeded = true;
                            // Remove streaming bubble and add final message
                            removeStreamingBubble(streamId);
                            messages.push({
                                role: 'assistant',
                                content: fullAnswer || 'No response generated.',
                                citations: data.citations || []
                            });
                            renderMessages();
                            ws.close();
                            resolve();
                        } else if (data.type === 'error') {
                            removeStreamingBubble(streamId);
                            messages.push({
                                role: 'assistant',
                                content: `⚠️ ${data.detail}`,
                                citations: []
                            });
                            renderMessages();
                            ws.close();
                            resolve();
                        }
                    } catch (e) {
                        // Non-JSON message, ignore
                    }
                };

                ws.onerror = () => {
                    if (!connected) {
                        // WebSocket failed to connect — fall back to sync
                        reject(new Error('WebSocket connection failed'));
                    }
                };

                ws.onclose = () => {
                    activeWs = null;
                    if (!streamSucceeded && connected) {
                        resolve(); // Clean close after streaming
                    } else if (!connected) {
                        reject(new Error('WebSocket closed before connecting'));
                    }
                };

                // Timeout
                setTimeout(() => {
                    if (!connected) {
                        ws.close();
                        reject(new Error('WebSocket timeout'));
                    }
                }, 5000);
            });
        } catch (wsError) {
            // Fall back to synchronous API
            console.warn('WebSocket unavailable, falling back to sync chat:', wsError.message);
            removeStreamingBubble(streamId);

            const placeholderId = `bot-placeholder-${Date.now()}`;
            appendTypingPlaceholder(placeholderId);

            try {
                const data = await apiCall('/api/chat', 'POST', { prompt: text });
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
            }
        } finally {
            isSending = false;
            chatInput.disabled = false;
            chatInput.focus();
            updateInputZoneState();
        }
    }

    function appendStreamingBubble(id) {
        welcomeContainer.style.display = 'none';
        messagesContainer.style.display = 'flex';

        const row = document.createElement('div');
        row.className = 'chat-message-row assistant';
        row.id = id;
        row.innerHTML = `
            <div class="chat-avatar">AI</div>
            <div class="message-bubble streaming-bubble">
                <span class="streaming-text"></span>
                <span class="streaming-cursor">▊</span>
            </div>
        `;
        messagesContainer.appendChild(row);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function updateStreamingBubble(id, text) {
        const bubble = document.getElementById(id);
        if (!bubble) return;
        const textSpan = bubble.querySelector('.streaming-text');
        if (textSpan) {
            textSpan.innerHTML = renderMarkdown(text);
        }
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function removeStreamingBubble(id) {
        const bubble = document.getElementById(id);
        if (bubble) bubble.remove();
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
            if (err.message === 'Session expired') return;
            console.error('Polling status failed', err);
        }
        setTimeout(pollStatus, 1000);
    }

    // ══════════════════════════════════════════════════════════════════════
    // UI UPDATING & RENDERING
    // ══════════════════════════════════════════════════════════════════════
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

    // ══════════════════════════════════════════════════════════════════════
    // MESSAGE RENDERING WITH CLICKABLE CITATIONS
    // ══════════════════════════════════════════════════════════════════════
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
            avatar.textContent = msg.role === 'user'
                ? (currentUser ? currentUser.username.charAt(0).toUpperCase() : 'U')
                : 'AI';

            const bubble = document.createElement('div');
            bubble.className = 'message-bubble';

            // Format markdown-like text
            const formattedBody = renderMarkdown(msg.content);
            bubble.innerHTML = formattedBody;

            // Render clickable citations
            if (msg.role === 'assistant' && msg.citations && msg.citations.length > 0) {
                const citationsDiv = document.createElement('div');
                citationsDiv.className = 'message-citations';
                citationsDiv.innerHTML = `<div class="citations-title">📁 Source References</div>`;

                const list = document.createElement('ul');
                list.className = 'citations-list';
                msg.citations.forEach(citation => {
                    const item = document.createElement('li');
                    item.className = 'citation-item';

                    const isPdf = citation.source_file_name.toLowerCase().endsWith('.pdf');
                    item.innerHTML = `
                        <span class="citation-file ${isPdf ? 'citation-clickable' : ''}" title="${citation.source_file_name}">${citation.source_file_name}</span>
                        <span class="citation-page">Pg ${citation.page_number}</span>
                    `;

                    if (isPdf) {
                        item.addEventListener('click', () => {
                            openPdfViewer(citation.source_file_name, citation.page_number);
                        });
                        item.classList.add('citation-interactive');
                    }

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

    // ══════════════════════════════════════════════════════════════════════
    // PDF VIEWER
    // ══════════════════════════════════════════════════════════════════════
    function openPdfViewer(fileName, pageNumber) {
        // Find the full path from active directories
        // We construct the URL using the filename and let the backend resolve
        const pdfUrl = `/api/documents/pdf?path=${encodeURIComponent(findFilePath(fileName))}&token=${encodeURIComponent(token)}#page=${pageNumber}`;

        pdfViewerFileName.textContent = fileName;
        pdfPageBadge.textContent = `Page ${pageNumber}`;
        pdfViewerFrame.src = pdfUrl;
        pdfViewerPanel.style.display = 'flex';

        // Highlight active citation
        document.querySelectorAll('.citation-interactive').forEach(el => el.classList.remove('citation-active'));
        document.querySelectorAll('.citation-file').forEach(el => {
            if (el.textContent === fileName) {
                el.closest('.citation-interactive')?.classList.add('citation-active');
            }
        });
    }

    function findFilePath(fileName) {
        // Construct a plausible full path from selected directories
        // The backend will validate it's within indexed dirs
        for (const dir of selectedDirectories) {
            // Use forward slashes for URL safety
            const sep = dir.includes('\\') ? '\\' : '/';
            return `${dir}${sep}${fileName}`;
        }
        return fileName;
    }

    pdfCloseBtn.addEventListener('click', () => {
        pdfViewerPanel.style.display = 'none';
        pdfViewerFrame.src = '';
        document.querySelectorAll('.citation-interactive').forEach(el => el.classList.remove('citation-active'));
    });

    // ══════════════════════════════════════════════════════════════════════
    // ADMIN: USER MANAGEMENT MODAL
    // ══════════════════════════════════════════════════════════════════════
    manageUsersBtn.addEventListener('click', () => {
        userDropdown.style.display = 'none';
        adminModalOverlay.style.display = 'flex';
        loadUsers();
    });

    adminModalClose.addEventListener('click', () => {
        adminModalOverlay.style.display = 'none';
    });

    adminModalOverlay.addEventListener('click', (e) => {
        if (e.target === adminModalOverlay) adminModalOverlay.style.display = 'none';
    });

    async function loadUsers() {
        try {
            const data = await apiCall('/api/auth/users');
            renderUsersTable(data.users);
        } catch (err) {
            console.error('Failed to load users:', err);
        }
    }

    function renderUsersTable(users) {
        usersTableBody.innerHTML = '';
        users.forEach(user => {
            const tr = document.createElement('tr');
            const createdDate = new Date(user.created_at * 1000).toLocaleDateString();
            const isSelf = currentUser && currentUser.id === user.id;

            tr.innerHTML = `
                <td>
                    <div class="user-cell">
                        <span class="user-cell-avatar">${user.username.charAt(0).toUpperCase()}</span>
                        <span>${user.username}</span>
                    </div>
                </td>
                <td>
                    <select class="role-select" data-user-id="${user.id}" ${isSelf ? 'disabled' : ''}>
                        <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>Admin</option>
                        <option value="viewer" ${user.role === 'viewer' ? 'selected' : ''}>Viewer</option>
                    </select>
                </td>
                <td class="date-cell">${createdDate}</td>
                <td>
                    ${isSelf ? '<span class="you-badge">You</span>' :
                    `<button class="btn-delete-user" data-user-id="${user.id}" title="Delete user">
                        <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                    </button>`}
                </td>
            `;
            usersTableBody.appendChild(tr);
        });

        // Bind role change events
        usersTableBody.querySelectorAll('.role-select').forEach(select => {
            select.addEventListener('change', async (e) => {
                const userId = e.target.dataset.userId;
                try {
                    await apiCall(`/api/auth/users/${userId}/role`, 'PUT', { role: e.target.value });
                } catch (err) {
                    alert(`Failed to update role: ${err.message}`);
                    loadUsers();
                }
            });
        });

        // Bind delete events
        usersTableBody.querySelectorAll('.btn-delete-user').forEach(btn => {
            btn.addEventListener('click', async () => {
                const userId = btn.dataset.userId;
                if (!confirm('Are you sure you want to delete this user?')) return;
                try {
                    await apiCall(`/api/auth/users/${userId}`, 'DELETE');
                    loadUsers();
                } catch (err) {
                    alert(`Failed to delete user: ${err.message}`);
                }
            });
        });
    }

    // Create user form
    createUserForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const username = document.getElementById('newUsername').value.trim();
        const password = document.getElementById('newPassword').value;
        const role = document.getElementById('newRole').value;

        createUserError.style.display = 'none';

        try {
            await apiCall('/api/auth/register', 'POST', { username, password, role });
            document.getElementById('newUsername').value = '';
            document.getElementById('newPassword').value = '';
            loadUsers();
        } catch (err) {
            createUserError.textContent = err.message;
            createUserError.style.display = 'block';
        }
    });

    // ══════════════════════════════════════════════════════════════════════
    // CHANGE PASSWORD MODAL
    // ══════════════════════════════════════════════════════════════════════
    changePasswordBtn.addEventListener('click', () => {
        userDropdown.style.display = 'none';
        passwordModalOverlay.style.display = 'flex';
        changePasswordError.style.display = 'none';
    });

    passwordModalClose.addEventListener('click', () => {
        passwordModalOverlay.style.display = 'none';
    });

    passwordModalOverlay.addEventListener('click', (e) => {
        if (e.target === passwordModalOverlay) passwordModalOverlay.style.display = 'none';
    });

    changePasswordForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const current = document.getElementById('currentPassword').value;
        const newPw = document.getElementById('newPasswordChange').value;
        const confirm = document.getElementById('confirmPassword').value;

        changePasswordError.style.display = 'none';

        if (newPw !== confirm) {
            changePasswordError.textContent = 'New passwords do not match.';
            changePasswordError.style.display = 'block';
            return;
        }

        try {
            await apiCall('/api/auth/change-password', 'PUT', {
                current_password: current,
                new_password: newPw,
            });
            passwordModalOverlay.style.display = 'none';
            changePasswordForm.reset();
            alert('Password changed successfully. Please log in again.');
            logoutBtn.click();
        } catch (err) {
            changePasswordError.textContent = err.message;
            changePasswordError.style.display = 'block';
        }
    });

    // ══════════════════════════════════════════════════════════════════════
    // OFFLINE LIGHTWEIGHT MARKDOWN PARSER
    // ══════════════════════════════════════════════════════════════════════
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

    // ══════════════════════════════════════════════════════════════════════
    // BOOT
    // ══════════════════════════════════════════════════════════════════════
    pollStatus();
    renderSelectedFolders();
});
