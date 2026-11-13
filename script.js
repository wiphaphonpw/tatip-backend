const API_URL = "https://tatip-backend-417035159214.us-central1.run.app";

// ==========================================
// 1. ระบบ Auth (Login แบบรวดเร็ว + มี Timeout)
// ==========================================
async function handleLogin(event) {
    event.preventDefault();

    const usernameInput = document.getElementById('username').value.trim();
    const passwordInput = document.getElementById('password').value.trim();
    const btnSubmit = event.target.querySelector('button[type="submit"]');

    if (!usernameInput || !passwordInput) {
        alert('กรุณากรอก Username และ Password');
        return;
    }

    // แสดงสถานะกำลังเข้าสู่ระบบ
    if (btnSubmit) {
        btnSubmit.disabled = true;
        btnSubmit.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> กำลังเข้าสู่ระบบ...';
    }

    try {
        // กำหนด Timeout 8 วินาที เพื่อไม่ให้ระบบค้างหมุนนานเกินไป
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 8000);

        const response = await fetch(`${API_URL}/api/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                username: usernameInput, 
                password: passwordInput 
            }),
            signal: controller.signal
        });

        clearTimeout(timeoutId);
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            const userData = {
                username: usernameInput,
                user_id: result.user_id,
                role: result.role,
                tenant_id: result.tenant_id,
                access_token: result.access_token
            };
            localStorage.setItem('currentUser', JSON.stringify(userData));

            // เคลียร์ประวัติค้างเก่า (ถ้ามี) แล้วย้ายหน้าทันที
            if (result.role === 'admin' || result.role === 'branch_manager') {
                window.location.replace('admin_users.html');
            } else {
                window.location.replace('index.html');
            }
        } else {
            alert('❌ เข้าสู่ระบบไม่สำเร็จ: ' + (result.detail || 'Username หรือ Password ไม่ถูกต้อง'));
        }
    } catch (error) {
        if (error.name === 'AbortError') {
            alert('❌ การเชื่อมต่อใช้เวลานานเกินไป กรุณาตรวจสอบอินเทอร์เน็ตหรือลองใหม่อีกครั้ง');
        } else {
            console.error('Login Error:', error);
            alert('❌ ไม่สามารถเชื่อมต่อกับเซิร์ฟเวอร์ Backend บน Cloud Run ได้');
        }
    } finally {
        if (btnSubmit) {
            btnSubmit.disabled = false;
            btnSubmit.innerHTML = 'เข้าสู่ระบบ';
        }
    }
}

// ==========================================
// ฟังก์ชันออกจากระบบ (ผูกกับ window โดยตรงเพื่อป้องกัน Scope Error)
// ==========================================
window.logoutUser = function() {
    if (confirm('คุณต้องการออกจากระบบหรือไม่?')) {
        // 1. เคลียร์ข้อมูลล็อกอินทั้งหมด
        localStorage.removeItem('currentUser');
        localStorage.clear();
        
        // 2. ใช้ location.href ส่งไปยังหน้า login.html
        window.location.href = 'login.html';
    }
};

// ==========================================
// 2. ระบบ Modal
// ==========================================
function openSupportModal() { document.getElementById('ticketModal')?.classList.add('active'); }
function closeSupportModal() { document.getElementById('ticketModal')?.classList.remove('active'); }
async function submitSupportTicket(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const issue = form.querySelector('textarea')?.value.trim() || '';
    const category = form.querySelector('#supportCategory')?.value || '';
    const currentUser = JSON.parse(localStorage.getItem('currentUser') || '{}');

    if (!currentUser.access_token || !currentUser.user_id) {
        alert('กรุณาเข้าสู่ระบบก่อนส่งเรื่องให้เจ้าหน้าที่');
        return;
    }
    if (issue.length < 5) {
        alert('กรุณาระบุรายละเอียดปัญหาอย่างน้อย 5 ตัวอักษร');
        return;
    }

    const submitButton = form.querySelector('button[type="submit"]');
    const originalButtonText = submitButton?.innerHTML;
    if (submitButton) {
        submitButton.disabled = true;
        submitButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> กำลังส่งเรื่อง...';
    }

    try {
        const response = await fetch(`${API_URL}/api/support-cases`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${currentUser.access_token}`
            },
            body: JSON.stringify({
                issue,
                category,
                conversation_id: currentUser.user_id
            })
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.status !== 'success') {
            throw new Error(result.detail || 'ส่งเรื่องไม่สำเร็จ กรุณาลองใหม่');
        }

        closeSupportModal();
        form.reset();
        alert(`ส่งเรื่องให้เจ้าหน้าที่สำเร็จ เลขที่เคส ${result.case_id}${result.category ? ` (ประเภท: ${result.category})` : ''}`);
    } catch (error) {
        alert(error.message || 'ส่งเรื่องไม่สำเร็จ กรุณาลองใหม่');
    } finally {
        if (submitButton) {
            submitButton.disabled = false;
            submitButton.innerHTML = originalButtonText || 'ส่งเรื่อง';
        }
    }
}
function handleDirectContactSubmit(event) {
    event.preventDefault();
    alert('ส่งข้อความถึงทีมสำเร็จ!');
    event.target.reset();
}

// ==========================================
// 3. ระบบนำทาง (Search & Routing)
// ==========================================
function handleHomeSearch(event) { if (event.key === 'Enter') executeHomeSearch(); }
function executeHomeSearch() {
    const input = document.getElementById('homeSearchInput').value.trim();
    if (input) window.location.href = `chat.html?q=${encodeURIComponent(input)}`;
}
function askQuickQuestion(question) { window.location.href = `chat.html?q=${encodeURIComponent(question)}`; }
function startChatCategory(category) { window.location.href = `chat.html?q=${encodeURIComponent('สอบถามเรื่อง ' + category)}`; }

// ==========================================
// 4. ระบบ API Chat (หน้า chat.html)
// ==========================================
function renderChatReply(value) {
    const escaped = String(value ?? '').replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[character]);
    return escaped
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
}

function handleChatKeyPress(event) { if (event.key === 'Enter') submitChatMessage(); }
function sendChatMessage(text) {
    document.getElementById('chatInput').value = text;
    submitChatMessage();
}
function resetChat() {
    if(confirm('ต้องการเริ่มการสนทนาใหม่หรือไม่?')) {
        document.getElementById('chatMessages').innerHTML = `
            <div class="message-group ai">
                <div class="message-avatar"><span class="logo-mark chat-logo-mark" role="img" aria-label="TATIP AI"><img src="./assets/tatip-logo-source.png" alt=""></span></div>
                <div class="message-body">
                    <div class="message-bubble">สวัสดีค่ะ TATIP AI ยินดีให้บริการค่ะ คุณสามารถพิมพ์สอบถามปัญหากล้อง วงจรปิด การใช้งาน Dashboard หรือการตั้งค่าต่างๆ ได้เลยค่ะ</div>
                    <div class="message-time">เมื่อครู่นี้</div>
                </div>
            </div>`;
    }
}

async function submitChatMessage() {
    const inputField = document.getElementById('chatInput');
    const chatBox = document.getElementById('chatMessages');
    const message = inputField.value.trim();
    if (!message) return;

    const timeNow = new Date().toLocaleTimeString('th-TH', {hour: '2-digit', minute:'2-digit'});
    const safeUserMessage = escapeHistoryHtml(message);
    chatBox.innerHTML += `
        <div class="message-group user">
            <div class="message-avatar"><i class="fa-solid fa-user"></i></div>
            <div class="message-body">
                <div class="message-bubble">${safeUserMessage}</div>
                <div class="message-time">${timeNow} น.</div>
            </div>
        </div>`;
    inputField.value = '';
    chatBox.scrollTop = chatBox.scrollHeight;

    const typingId = "typing-" + Date.now();
    chatBox.innerHTML += `
        <div id="${typingId}" class="message-group ai">
            <div class="message-avatar"><span class="logo-mark chat-logo-mark" role="img" aria-label="TATIP AI"><img src="./assets/tatip-logo-source.png" alt=""></span></div>
            <div class="message-body">
                <div class="message-bubble" style="color: #64748B; font-style: italic;"><i class="fa-solid fa-circle-notch fa-spin"></i> กำลังค้นหาข้อมูลจาก RAG Engine...</div>
            </div>
        </div>`;
    chatBox.scrollTop = chatBox.scrollHeight;

    try {
        const username = getCurrentUsername();
        const storageKey = `tatip_chat_history_${username}`;
        const localHistory = JSON.parse(localStorage.getItem(storageKey) || '[]');
        const recentHistory = localHistory.slice(0, 3).reverse().map(item => ({
            question: item.question || '',
            answer: item.answer || ''
        }));

        const payloadBody = JSON.stringify({
            message: message,
            history: recentHistory
        });

        // 1. พยายามใช้ Streaming API (/api/chat/stream) ก่อนเพื่อให้แสดงข้อความแบบ Real-time
        let finalReply = '';
        let usedStream = false;

        try {
            const streamResponse = await fetch(`${API_URL}/api/chat/stream`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: payloadBody
            });

            const contentType = streamResponse.headers.get("content-type") || "";
            if (streamResponse.ok && contentType.includes("text/event-stream") && streamResponse.body) {
                usedStream = true;
                const typingEl = document.getElementById(typingId);
                if (typingEl) typingEl.remove();

                const bubbleId = "ai-bubble-" + Date.now();
                const timeStr = new Date().toLocaleTimeString('th-TH', {hour: '2-digit', minute:'2-digit'});
                chatBox.innerHTML += `
                    <div class="message-group ai">
                        <div class="message-avatar"><span class="logo-mark chat-logo-mark" role="img" aria-label="TATIP AI"><img src="./assets/tatip-logo-source.png" alt=""></span></div>
                        <div class="message-body">
                            <div id="${bubbleId}" class="message-bubble"></div>
                            <div class="message-time">${timeStr} น.</div>
                        </div>
                    </div>`;

                const bubbleEl = document.getElementById(bubbleId);
                const reader = streamResponse.body.getReader();
                const decoder = new TextDecoder("utf-8");
                let buffer = "";

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const events = buffer.split("\n\n");
                    buffer = events.pop() || "";

                    for (const eventText of events) {
                        const line = eventText.split("\n").find(l => l.startsWith("data: "));
                        if (!line) continue;
                        try {
                            const parsed = JSON.parse(line.slice(6));
                            if (parsed.chunk) {
                                finalReply += parsed.chunk;
                                bubbleEl.innerHTML = renderChatReply(finalReply);
                                chatBox.scrollTop = chatBox.scrollHeight;
                            }
                            if (parsed.done && parsed.reply) {
                                finalReply = parsed.reply;
                                bubbleEl.innerHTML = renderChatReply(finalReply);
                                chatBox.scrollTop = chatBox.scrollHeight;
                            }
                        } catch (_) {
                            // ข้าม JSON ที่ยังไม่สมบูรณ์
                        }
                    }
                }
            }
        } catch (_) {
            // หาก Streaming Endpoint ยังไม่พร้อมใช้งาน ให้ข้ามไปใช้ Fallback แบบปกติ
        }

        // 2. Fallback กลับไปใช้ /api/chat แบบปกติ (รองรับเซิร์ฟเวอร์เดิม)
        if (!usedStream) {
            const response = await fetch(`${API_URL}/api/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: payloadBody
            });
            const data = await response.json();
            const typingEl = document.getElementById(typingId);
            if (typingEl) typingEl.remove();

            if (data.status === "success") {
                finalReply = data.reply;
                const formattedReply = renderChatReply(finalReply);
                chatBox.innerHTML += `
                    <div class="message-group ai">
                        <div class="message-avatar"><span class="logo-mark chat-logo-mark" role="img" aria-label="TATIP AI"><img src="./assets/tatip-logo-source.png" alt=""></span></div>
                        <div class="message-body">
                            <div class="message-bubble">${formattedReply}</div>
                            <div class="message-time">${new Date().toLocaleTimeString('th-TH', {hour: '2-digit', minute:'2-digit'})} น.</div>
                        </div>
                    </div>`;
            } else {
                alert("API Error: " + data.reply);
                return;
            }
        }

        if (finalReply) {
            saveToHistory(message, finalReply);
            saveChatHistoryToServer(message, finalReply);
        }
    } catch (error) {
        document.getElementById(typingId).remove();
        chatBox.innerHTML += `
            <div class="message-group ai">
                <div class="message-avatar"><i class="fa-solid fa-triangle-exclamation" style="color: #DC2626;"></i></div>
                <div class="message-body">
                    <div class="message-bubble" style="border-color: #DC2626; color: #DC2626; background: #FEE2E2;">
                        ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์ Backend บน Cloud Run ได้
                    </div>
                </div>
            </div>`;
    }
    chatBox.scrollTop = chatBox.scrollHeight;
}

function initChatPageFromQuery() {
    const inputField = document.getElementById('chatInput');
    const chatBox = document.getElementById('chatMessages');
    if (!inputField || !chatBox) return;

    const params = new URLSearchParams(window.location.search);
    const initialQuestion = (params.get('q') || '').trim();
    if (!initialQuestion) return;

    // ล้างพารามิเตอร์ q ออกจาก URL เพื่อป้องกันการส่งซ้ำเมื่อรีเฟรชหน้า
    params.delete('q');
    const cleanQuery = params.toString();
    const cleanUrl = `${window.location.pathname}${cleanQuery ? `?${cleanQuery}` : ''}${window.location.hash || ''}`;
    window.history.replaceState({}, document.title, cleanUrl);

    sendChatMessage(initialQuestion);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initChatPageFromQuery);
} else {
    initChatPageFromQuery();
}

// ==========================================
// 5. ระบบประวัติการสนทนา (แยกตาม Account ผู้ใช้)
// ==========================================

// ฟังก์ชันดึงชื่อ User ที่ล็อกอินปัจจุบัน
function getCurrentUsername() {
    const currentUserStr = localStorage.getItem('currentUser');
    if (currentUserStr) {
        try {
            const userObj = JSON.parse(currentUserStr);
            return userObj.username || userObj.user_id || 'guest';
        } catch (e) {
            return 'guest';
        }
    }
    return 'guest';
}

async function saveChatHistoryToServer(question, answer) {
    try {
        const currentUser = JSON.parse(localStorage.getItem('currentUser') || '{}');
        if (!currentUser.user_id || !currentUser.tenant_id || !currentUser.access_token) {
            console.warn('ไม่สามารถบันทึกประวัติลง Firestore: กรุณาออกจากระบบและเข้าสู่ระบบใหม่');
            return;
        }

        const response = await fetch(`${API_URL}/api/save-chat-history`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${currentUser.access_token}`
            },
            body: JSON.stringify({
                USER_ID: currentUser.user_id,
                TENANT_ID: currentUser.tenant_id,
                MESSAGE: question,
                REPLY: answer,
                CONVERSATION_ID: currentUser.user_id
            })
        });
        if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            console.error('บันทึกประวัติแชทไม่สำเร็จ:', result.detail || response.statusText);
        }
    } catch (error) {
        console.error('ไม่สามารถบันทึกประวัติแชทลง Firestore ได้:', error);
    }
}

function saveToHistory(question, answer) {
    // 1. ระบุ Key โดยนำชื่อ Account ปัจจุบันมาต่อท้าย (เช่น tatip_chat_history_user01)
    const username = getCurrentUsername();
    const storageKey = `tatip_chat_history_${username}`;

    let history = JSON.parse(localStorage.getItem(storageKey)) || [];
    
    const cleanAnswer = answer
        .replace(/\*\*/g, '')  
        .replace(/\*/g, '')    
        .replace(/<br>/g, ' ') 
        .trim();

    history.unshift({
        id: 'CASE-' + Date.now().toString().slice(-8),
        question: question,
        answer: cleanAnswer,
        date: new Date().toLocaleDateString('th-TH', { day: 'numeric', month: 'short', year: 'numeric' }),
        time: new Date().toLocaleTimeString('th-TH', {hour: '2-digit', minute:'2-digit'})
    });
    
    if (history.length > 50) history.pop();
    localStorage.setItem(storageKey, JSON.stringify(history));
}

async function loadHistory() {
    const historyList = document.getElementById('historyList');
    if (!historyList) return;

    const username = getCurrentUsername();
    const storageKey = `tatip_chat_history_${username}`;
    const currentUser = JSON.parse(localStorage.getItem('currentUser') || '{}');
    const localHistory = JSON.parse(localStorage.getItem(storageKey) || '[]');
    let serverHistory = [];
    let loadMessage = '';

    if (currentUser.user_id && currentUser.access_token) {
        try {
            const response = await fetch(`${API_URL}/api/history/${encodeURIComponent(currentUser.user_id)}`, {
                headers: { 'Authorization': `Bearer ${currentUser.access_token}` }
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }
            serverHistory = buildHistoryTurns(result.data || []);
        } catch (error) {
            console.error('โหลดประวัติจาก Firestore ไม่สำเร็จ:', error);
            loadMessage = 'โหลดประวัติจากระบบไม่สำเร็จ กำลังแสดงประวัติในอุปกรณ์นี้';
        }
    } else {
        loadMessage = 'เข้าสู่ระบบใหม่เพื่อดูประวัติที่บันทึกบน Firestore';
    }

    const seen = new Set();
    const history = [...serverHistory, ...localHistory].filter(item => {
        const question = normalizeHistoryForComparison(item.question);
        const answer = normalizeHistoryForComparison(item.answer);
        const key = `${question}\u0000${answer}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
    renderHistoryCards(history, loadMessage);
}

function normalizeHistoryForComparison(value) {
    return String(value ?? '')
        .replace(/<br\s*\/?\s*>/gi, ' ')
        .replace(/[*_`~]/g, '')
        .replace(/\s+/g, ' ')
        .trim()
        .toLocaleLowerCase();
}

function parseHistorySortParts(item) {
    const raw = String(item?.CREATE_DATE || '').trim();
    const epochMs = raw ? Date.parse(raw) : 0;
    const ms = Number.isFinite(epochMs) ? epochMs : 0;
    const fracMatch = raw.match(/\.(\d+)/);
    const subNs = fracMatch ? Number(fracMatch[1].padEnd(9, '0').slice(0, 9)) : 0;
    const sender = String(item?.SENDER || '').toLowerCase();
    const senderRank = sender === 'user' ? 0 : sender === 'ai' ? 1 : 2;
    const docId = String(item?._document_id || '');
    return { ms, subNs, senderRank, docId };
}

function compareHistoryMessages(left, right) {
    const a = parseHistorySortParts(left);
    const b = parseHistorySortParts(right);
    if (a.ms !== b.ms) return a.ms - b.ms;
    if (a.subNs !== b.subNs) return a.subNs - b.subNs;
    if (a.senderRank !== b.senderRank) return a.senderRank - b.senderRank;
    return a.docId.localeCompare(b.docId);
}

function createHistoryTurnRecord(id, question, answer, timestamp, fallbackSubNs = 0) {
    const date = timestamp ? new Date(timestamp) : new Date();
    const ms = Number.isFinite(date.getTime()) ? date.getTime() : 0;
    const validDate = ms > 0 ? date : new Date();
    return {
        id,
        question: question || '',
        answer: answer || '',
        date: validDate.toLocaleDateString('th-TH', { day: 'numeric', month: 'short', year: 'numeric' }),
        time: validDate.toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' }),
        sortTime: ms,
        sortSubNs: fallbackSubNs
    };
}

function buildHistoryTurns(messages) {
    if (!Array.isArray(messages) || messages.length === 0) return [];

    const turns = [];
    const turnGroups = new Map();
    const legacyConversationGroups = new Map();

    messages.forEach(item => {
        if (!item || typeof item !== 'object') return;
        const sender = String(item.SENDER || '').toLowerCase();
        const turnId = String(item.TURN_ID || '').trim();

        if (turnId && (sender === 'user' || sender === 'ai')) {
            const convId = String(item.CONVERSATION_ID || '__default__').trim();
            const key = `${convId}::${turnId}`;
            const group = turnGroups.get(key) || { user: null, ai: null };
            if (sender === 'user' && !group.user) {
                group.user = item;
            } else if (sender === 'ai' && !group.ai) {
                group.ai = item;
            }
            turnGroups.set(key, group);
            return;
        }

        if (item.MESSAGE && item.REPLY) {
            // Keep support for older records that stored a whole turn in one document.
            const sortParts = parseHistorySortParts(item);
            turns.push(createHistoryTurnRecord(
                item._document_id || `legacy-${turns.length}`,
                item.MESSAGE,
                item.REPLY,
                item.CREATE_DATE,
                sortParts.subNs
            ));
            return;
        }

        if (sender === 'user' || sender === 'ai') {
            const convId = String(item.CONVERSATION_ID || '__default__').trim();
            if (!legacyConversationGroups.has(convId)) {
                legacyConversationGroups.set(convId, []);
            }
            legacyConversationGroups.get(convId).push(item);
        }
    });

    turnGroups.forEach((group, key) => {
        if (!group.user || !group.ai) return;
        const timestamp = group.ai.CREATE_DATE || group.user.CREATE_DATE;
        const sortParts = parseHistorySortParts(group.ai.CREATE_DATE ? group.ai : group.user);
        turns.push(createHistoryTurnRecord(
            group.ai._document_id || group.user._document_id || `turn-${key}`,
            group.user.MESSAGE || '',
            group.ai.MESSAGE || '',
            timestamp,
            sortParts.subNs
        ));
    });

    legacyConversationGroups.forEach(convMessages => {
        const ordered = [...convMessages].sort(compareHistoryMessages);
        // Pair each AI reply only with the immediately preceding unanswered user message
        // in the same conversation so an orphaned user question never shifts later turns.
        let pendingUser = null;

        ordered.forEach(item => {
            const sender = String(item.SENDER || '').toLowerCase();
            if (sender === 'user') {
                pendingUser = item;
            } else if (sender === 'ai') {
                if (!pendingUser) return;
                const userMessage = pendingUser;
                pendingUser = null;
                const timestamp = item.CREATE_DATE || userMessage.CREATE_DATE;
                const sortParts = parseHistorySortParts(item.CREATE_DATE ? item : userMessage);
                turns.push(createHistoryTurnRecord(
                    item._document_id || userMessage._document_id || `history-${turns.length}`,
                    userMessage.MESSAGE || '',
                    item.MESSAGE || '',
                    timestamp,
                    sortParts.subNs
                ));
            }
        });
    });

    return turns.sort((left, right) => {
        if (right.sortTime !== left.sortTime) return right.sortTime - left.sortTime;
        return (right.sortSubNs || 0) - (left.sortSubNs || 0);
    });
}

function escapeHistoryHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[character]);
}

function renderHistoryCards(history, loadMessage = '') {
    const historyList = document.getElementById('historyList');
    if (!historyList) return;

    const statusHtml = loadMessage
        ? `<p class="history-load-message" style="color:#64748B; grid-column:1/-1; text-align:center;">${escapeHistoryHtml(loadMessage)}</p>`
        : '';
    if (history.length === 0) {
        historyList.innerHTML = statusHtml || `<p style="color:#64748B; text-align:center; grid-column:1/-1; padding:3rem;">ยังไม่มีประวัติการสนทนาสำหรับบัญชี <strong>${escapeHistoryHtml(getCurrentUsername())}</strong></p>`;
        return;
    }

    historyList.innerHTML = statusHtml + history.map(item => `
        <div class="history-card" style="background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:20px; display:flex; flex-direction:column; justify-content:space-between; box-shadow:0 2px 6px rgba(0,0,0,.03);">
            <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <span style="font-size:.75rem; background:#fef2f2; color:#dc2626; padding:4px 10px; border-radius:6px; font-weight:600;">${escapeHistoryHtml(item.id)}</span>
                    <span style="font-size:.75rem; color:#94a3b8;"><i class="fa-regular fa-clock"></i> ${escapeHistoryHtml(item.date)} • ${escapeHistoryHtml(item.time)} น.</span>
                </div>
                <h4 style="font-size:.95rem; color:#1e293b; margin:0 0 8px; font-weight:600; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; line-height:1.4;">📌 ${escapeHistoryHtml(item.question)}</h4>
                <p style="font-size:.85rem; color:#64748B; margin:0; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; line-height:1.5;">${escapeHistoryHtml(item.answer)}</p>
            </div>
            <div style="margin-top:18px; border-top:1px solid #f1f5f9; padding-top:12px; display:flex; justify-content:flex-end;">
                <button type="button" data-open-transcript style="background:#f8fafc; border:1px solid #e2e8f0; color:#d32f2f; padding:6px 14px; border-radius:6px; font-family:'Prompt',sans-serif; font-size:.8rem; font-weight:600; cursor:pointer; display:inline-flex; align-items:center; gap:6px;">ดูรายละเอียดเต็ม <i class="fa-solid fa-arrow-right" style="font-size:.7rem;"></i></button>
            </div>
        </div>`).join('');

    historyList.querySelectorAll('[data-open-transcript]').forEach((button, index) => {
        button.addEventListener('click', () => openTranscript(history[index].question, history[index].answer));
    });
}

function openTranscript(question, answer) {
    const modal = document.getElementById('transcriptModal');
    const body = document.getElementById('transcriptBody');
    if (!modal || !body) return;
    body.innerHTML = `<h4>คำถาม</h4><p>${escapeHistoryHtml(question).replace(/\n/g, '<br>')}</p><h4>คำตอบ</h4><p>${escapeHistoryHtml(answer).replace(/\n/g, '<br>')}</p>`;
    modal.style.display = 'flex';
}

function closeTranscriptModal() {
    const modal = document.getElementById('transcriptModal');
    if (modal) modal.style.display = 'none';
}

function filterHistory() {
    const search = (document.getElementById('historySearch')?.value || '').trim().toLowerCase();
    document.querySelectorAll('#historyList .history-card').forEach(card => {
        card.style.display = card.textContent.toLowerCase().includes(search) ? '' : 'none';
    });
}

if (document.getElementById('historyList')) {
    loadHistory();
}

// ==========================================
// 6. ระบบ FAQ & Admin Table Fetch
// ==========================================
function toggleFaq(element) { element.parentElement.classList.toggle('active'); }
function filterFaqCategory(category, btnElement) {
    document.querySelectorAll('.chip').forEach(btn => btn.classList.remove('active'));
    btnElement.classList.add('active');
    document.querySelectorAll('.faq-accordion-item').forEach(item => {
        item.style.display = (category === 'all' || item.getAttribute('data-category') === category) ? 'block' : 'none';
    });
}

// ฟังก์ชันเปิด Modal เพิ่มข้อมูล
function openAddModal() {
    const colName = document.getElementById('collectionSelect').value;
    const modalTitle = document.getElementById('modalTitle');
    const addModal = document.getElementById('addDocModal');
    
    if (modalTitle && addModal) {
        modalTitle.innerHTML = `<i class="fa-solid fa-circle-plus"></i> เพิ่มข้อมูลในตาราง: ${colName}`;
        addModal.style.display = 'flex';
    } else {
        console.error("ไม่พบโครงสร้าง Modal ในหน้าเว็บนี้");
    }
}

// ฟังก์ชันปิด Modal เพิ่มข้อมูล
function closeAddModal() {
    const addModal = document.getElementById('addDocModal');
    const form = document.getElementById('addDocForm');
    if (addModal) addModal.style.display = 'none';
    if (form) form.reset();
}

// ฟังก์ชันสลับดูข้อมูลทุกตารางและแปลง JSON ให้เป็นตารางคอลัมน์อัตโนมัติ
function escapeCollectionCell(value) {
    return String(value ?? '-').replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[character]);
}

async function fetchCollectionData() {
    const colName = document.getElementById('collectionSelect')?.value;
    const tbody = document.getElementById('dynamicTableBody');
    const thead = document.getElementById('dynamicTableHeader');
    const currentUser = JSON.parse(localStorage.getItem('currentUser') || '{}');

    if (!colName || !tbody || !thead) return;
    if (!currentUser.access_token) {
        thead.innerHTML = '<th>สถานะ</th>';
        tbody.innerHTML = '<tr><td class="loading">กรุณาเข้าสู่ระบบใหม่เพื่อดูข้อมูล</td></tr>';
        return;
    }

    tbody.innerHTML = `<tr><td colspan="12" class="loading">กำลังดึงข้อมูลจากตาราง ${escapeCollectionCell(colName)}...</td></tr>`;

    try {
        const response = await fetch(`${API_URL}/api/admin/collection/${encodeURIComponent(colName)}`, {
            headers: { 'Authorization': `Bearer ${currentUser.access_token}` }
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.status !== 'success') {
            throw new Error(result.detail || 'ไม่สามารถดึงข้อมูลตารางนี้ได้');
        }

        tbody.innerHTML = '';
        if (Array.isArray(result.data) && result.data.length > 0) {
            const allKeysSet = new Set();
            result.data.forEach(item => Object.keys(item).forEach(key => allKeysSet.add(key)));

            const keys = Array.from(allKeysSet);
            const docIdIndex = keys.indexOf('_document_id');
            if (docIdIndex > -1) {
                keys.splice(docIdIndex, 1);
                keys.unshift('_document_id');
            }

            let headerHtml = '<th>#</th>';
            keys.forEach(key => {
                const cleanKey = key === '_document_id' ? 'Document ID' : key.toUpperCase();
                headerHtml += `<th>${escapeCollectionCell(cleanKey)}</th>`;
            });
            const canUpdateCases = colName === 'support_cases' &&
                ['admin', 'branch_manager'].includes(currentUser.role);
            if (canUpdateCases) headerHtml += '<th>อัปเดตสถานะ</th>';
            thead.innerHTML = headerHtml;

            const statusLabels = {
                open: 'เปิด',
                in_progress: 'กำลังดำเนินการ',
                resolved: 'แก้ไขแล้ว',
                closed: 'ปิดเคส'
            };
            result.data.forEach((item, index) => {
                let rowHtml = `<td>${index + 1}</td>`;
                keys.forEach(key => {
                    let val = item[key];
                    if (typeof val === 'object' && val !== null) val = JSON.stringify(val);
                    if (val === undefined || val === null) val = '-';
                    const safeValue = escapeCollectionCell(val);
                    rowHtml += key === '_document_id'
                        ? `<td><code>${safeValue}</code></td>`
                        : `<td>${safeValue}</td>`;
                });

                if (canUpdateCases) {
                    const caseId = String(item._document_id || '');
                    const normalized = String(item.STATUS || 'open').trim().toLowerCase();
                    const currentStatus = statusLabels[normalized] ? normalized : 'open';
                    const statusOptions = Object.entries(statusLabels)
                        .map(([value, label]) => `<option value="${value}" ${currentStatus === value ? 'selected' : ''}>${label}</option>`)
                        .join('');
                    rowHtml += `<td class="case-status-cell"><div class="case-status-control"><select data-case-id="${escapeCollectionCell(caseId)}" data-saved-status="${escapeCollectionCell(currentStatus)}" aria-label="สถานะเคส">${statusOptions}</select><button type="button" class="btn-primary btn-save-status" style="width:auto;padding:.35rem .65rem;white-space:nowrap" onclick="updateSupportCaseStatusFromRow(this)">บันทึก</button></div></td>`;
                }
                tbody.insertAdjacentHTML('beforeend', `<tr>${rowHtml}</tr>`);
            });
        } else {
            thead.innerHTML = '<th>สถานะ</th>';
            tbody.innerHTML = '<tr><td class="loading">ตารางนี้ยังไม่มีข้อมูล (Document ว่างเปล่า)</td></tr>';
        }
    } catch (error) {
        console.error('Error loading Firestore collection:', error);
        thead.innerHTML = '<th>สถานะ</th>';
        tbody.innerHTML = `<tr><td class="loading" style="color:red;">${escapeCollectionCell(error.message || 'ไม่สามารถดึงข้อมูลตารางนี้ได้')}</td></tr>`;
    }
}

async function updateSupportCaseStatusFromRow(button) {
    const select = button?.parentElement?.querySelector('select[data-case-id]');
    if (!select) return;
    const currentUser = JSON.parse(localStorage.getItem('currentUser') || '{}');
    if (!currentUser.access_token) {
        alert('กรุณาเข้าสู่ระบบใหม่ก่อนอัปเดตสถานะเคส');
        return;
    }

    const caseId = select.dataset.caseId;
    const status = select.value;
    const oldStatus = select.dataset.savedStatus || select.querySelector('option[selected]')?.value || 'open';
    select.disabled = true;
    button.disabled = true;
    try {
        const response = await fetch(`${API_URL}/api/admin/support-cases/${encodeURIComponent(caseId)}`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${currentUser.access_token}`
            },
            body: JSON.stringify({ status })
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.status !== 'success') {
            throw new Error(result.detail || 'อัปเดตสถานะเคสไม่สำเร็จ');
        }
        select.dataset.savedStatus = status;
        alert(`อัปเดตสถานะเคส ${caseId} เป็น “${select.options[select.selectedIndex].text}” แล้ว`);
    } catch (error) {
        select.value = oldStatus;
        alert(error.message || 'อัปเดตสถานะเคสไม่สำเร็จ');
    } finally {
        select.disabled = false;
        button.disabled = false;
    }
}
