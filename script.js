const API_URL = "https://tatip-backend-417035159214.us-central1.run.app";

// ==========================================
// 1. ระบบ Auth (Login)
// ==========================================
async function handleLogin(event) {
    event.preventDefault(); // ป้องกันไม่ให้ฟอร์มรีเฟรชหน้าเว็บ

    const usernameInput = document.getElementById('username').value.trim();
    const passwordInput = document.getElementById('password').value.trim();

    try {
        // ส่ง username และ password ไปเช็กที่ FastAPI บน Cloud Run
        const response = await fetch(`${API_URL}/api/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                username: usernameInput, 
                password: passwordInput 
            })
        });
        
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            alert('เข้าสู่ระบบสำเร็จ ยินดีต้อนรับคุณ ' + usernameInput);
            
            // เก็บข้อมูลผู้ใช้ไว้ใน localStorage
            const userData = {
                username: usernameInput,
                role: result.role,
                tenant_id: result.tenant_id
            };
            localStorage.setItem('currentUser', JSON.stringify(userData));

            // ตรวจสอบสิทธิ์ (Role) เพื่อแยกหน้าเว็บ
            if (result.role === 'admin' || result.role === 'branch_manager') {
                // ถ้าเป็น Admin ไปหน้าจัดการระบบ
                window.location.href = 'admin_users.html'; 
            } else {
                // ถ้าเป็น Customer ไปหน้าแชทหลัก
                window.location.href = 'index.html'; 
            }
        } else {
            alert('❌ เข้าสู่ระบบไม่สำเร็จ: ' + (result.detail || 'Username หรือ Password ไม่ถูกต้อง'));
        }
    } catch (error) {
        console.error('Login Error:', error);
        alert('❌ เกิดข้อผิดพลาดในการเชื่อมต่อกับเซิร์ฟเวอร์ Backend บน Cloud Run');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname.includes('chat.html')) {
        const urlParams = new URLSearchParams(window.location.search);
        const query = urlParams.get('q');
        if (query) {
            document.getElementById('chatInput').value = query;
            submitChatMessage();
            window.history.replaceState({}, document.title, "chat.html");
        }
    }
    if (window.location.pathname.includes('history.html')) loadHistory();
});

// ==========================================
// 2. ระบบ Modal
// ==========================================
function openSupportModal() { document.getElementById('ticketModal')?.classList.add('active'); }
function closeSupportModal() { document.getElementById('ticketModal')?.classList.remove('active'); }
function submitSupportTicket(event) {
    event.preventDefault();
    alert('ระบบได้รับ Ticket ของคุณแล้ว วิศวกรจะติดต่อกลับโดยเร็วที่สุดครับ');
    closeSupportModal();
    event.target.reset();
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
function handleChatKeyPress(event) { if (event.key === 'Enter') submitChatMessage(); }
function sendChatMessage(text) {
    document.getElementById('chatInput').value = text;
    submitChatMessage();
}
function resetChat() {
    if(confirm('ต้องการเริ่มการสนทนาใหม่หรือไม่?')) {
        document.getElementById('chatMessages').innerHTML = `
            <div class="message-group ai">
                <div class="message-avatar"><i class="fa-solid fa-robot"></i></div>
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
    chatBox.innerHTML += `
        <div class="message-group user">
            <div class="message-avatar"><i class="fa-solid fa-user"></i></div>
            <div class="message-body">
                <div class="message-bubble">${message}</div>
                <div class="message-time">${timeNow} น.</div>
            </div>
        </div>`;
    inputField.value = '';
    chatBox.scrollTop = chatBox.scrollHeight;

    const typingId = "typing-" + Date.now();
    chatBox.innerHTML += `
        <div id="${typingId}" class="message-group ai">
            <div class="message-avatar"><i class="fa-solid fa-robot"></i></div>
            <div class="message-body">
                <div class="message-bubble" style="color: #64748B; font-style: italic;"><i class="fa-solid fa-circle-notch fa-spin"></i> กำลังค้นหาข้อมูลจาก RAG Engine...</div>
            </div>
        </div>`;
    chatBox.scrollTop = chatBox.scrollHeight;

    try {
        const response = await fetch(`${API_URL}/api/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: message })
        });
        const data = await response.json();
        document.getElementById(typingId).remove();

        if (data.status === "success") {
            const formattedReply = data.reply.replace(/\n/g, "<br>");
            chatBox.innerHTML += `
                <div class="message-group ai">
                    <div class="message-avatar"><i class="fa-solid fa-robot"></i></div>
                    <div class="message-body">
                        <div class="message-bubble">${formattedReply}</div>
                        <div class="message-time">${new Date().toLocaleTimeString('th-TH', {hour: '2-digit', minute:'2-digit'})} น.</div>
                    </div>
                </div>`;
            saveToHistory(message, data.reply);
        } else {
            alert("API Error: " + data.reply);
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

// ==========================================
// 5. ระบบประวัติการสนทนา (History)
// ==========================================
function saveToHistory(question, answer) {
    let history = JSON.parse(localStorage.getItem('tatip_chat_history')) || [];
    
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
    localStorage.setItem('tatip_chat_history', JSON.stringify(history));
}

function loadHistory() {
    const historyList = document.getElementById('historyList');
    if (!historyList) return;
    
    let history = JSON.parse(localStorage.getItem('tatip_chat_history')) || [];
    historyList.innerHTML = '';
    
    if (history.length === 0) {
        historyList.innerHTML = '<p style="color: #64748B; text-align: center; grid-column: 1/-1; padding: 3rem; font-size: 0.95rem;">ยังไม่มีประวัติการสนทนาในระบบ</p>';
        return;
    }
    
    history.forEach((item, index) => {
        const safeQuestion = JSON.stringify(item.question);
        const safeAnswer = JSON.stringify(item.answer);

        historyList.innerHTML += `
            <div class="history-card" style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 2px 6px rgba(0,0,0,0.03);">
                <div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                        <span style="font-size: 0.75rem; background: #fef2f2; color: #dc2626; padding: 4px 10px; border-radius: 6px; font-weight: 600;">${item.id}</span>
                        <span style="font-size: 0.75rem; color: #94a3b8;"><i class="fa-regular fa-clock"></i> ${item.date} • ${item.time} น.</span>
                    </div>
                    <h4 style="font-size: 0.95rem; color: #1e293b; margin: 0 0 8px 0; font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.4;">📌 ${item.question}</h4>
                    <p style="font-size: 0.85rem; color: #64748B; margin: 0; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.5;">${item.answer}</p>
                </div>
                <div style="margin-top: 18px; border-top: 1px solid #f1f5f9; padding-top: 12px; display: flex; justify-content: flex-end;">
                    <button onclick='openTranscript(${safeQuestion}, ${safeAnswer})' style="background: #f8fafc; border: 1px solid #e2e8f0; color: #d32f2f; padding: 6px 14px; border-radius: 6px; font-family: 'Prompt', sans-serif; font-size: 0.8rem; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;">
                        ดูรายละเอียดเต็ม <i class="fa-solid fa-arrow-right" style="font-size: 0.7rem;"></i>
                    </button>
                </div>
            </div>`;
    });
}

function openTranscript(question, answer) {
    const modal = document.getElementById('transcriptModal');
    const body = document.getElementById('transcriptBody');
    
    if (modal && body) {
        body.innerHTML = `
            <div style="background: #F8FAFC; padding: 14px; border-radius: 8px; margin-bottom: 14px; border-left: 4px solid #0284c7;">
                <strong style="color: #0f172a; display: block; margin-bottom: 6px; font-size: 0.9rem;"><i class="fa-solid fa-user"></i> คำถามของคุณ:</strong>
                <span style="color: #334155; font-size: 0.85rem; line-height: 1.5;">${question}</span>
            </div>
            <div style="background: #FEE2E2; padding: 14px; border-radius: 8px; border-left: 4px solid #d32f2f;">
                <strong style="color: #991b1b; display: block; margin-bottom: 6px; font-size: 0.9rem;"><i class="fa-solid fa-robot"></i> คำตอบจาก TATIP AI:</strong>
                <span style="color: #7f1d1d; font-size: 0.85rem; line-height: 1.6; white-space: pre-line;">${answer}</span>
            </div>`;
        
        modal.classList.add('active');
    }
}

function closeTranscriptModal() {
    const modal = document.getElementById('transcriptModal');
    if (modal) {
        modal.classList.remove('active');
    }
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
async function fetchCollectionData() {
    const colName = document.getElementById('collectionSelect').value;
    const tbody = document.getElementById('dynamicTableBody');
    const thead = document.getElementById('dynamicTableHeader');
    
    if (!tbody || !thead) return;

    tbody.innerHTML = `<tr><td colspan="10" class="loading">กำลังดึงข้อมูลจากตาราง ${colName}...</td></tr>`;

    try {
        const response = await fetch(`${API_URL}/api/admin/collection/${colName}`);
        const result = await response.json();

        tbody.innerHTML = '';
        if (result.status === 'success' && result.data.length > 0) {
            const allKeysSet = new Set();
            result.data.forEach(item => {
                Object.keys(item).forEach(key => allKeysSet.add(key));
            });
            
            const keys = Array.from(allKeysSet);
            const docIdIndex = keys.indexOf('_document_id');
            if (docIdIndex > -1) {
                keys.splice(docIdIndex, 1);
                keys.unshift('_document_id');
            }

            let headerHtml = `<th>#</th>`;
            keys.forEach(key => {
                const cleanKey = key === '_document_id' ? 'Document ID' : key.toUpperCase();
                headerHtml += `<th>${cleanKey}</th>`;
            });
            thead.innerHTML = headerHtml;

            result.data.forEach((item, index) => {
                let rowHtml = `<td>${index + 1}</td>`;
                keys.forEach(key => {
                    let val = item[key];
                    if (typeof val === 'object' && val !== null) {
                        val = JSON.stringify(val);
                    } else if (val === undefined || val === null) {
                        val = '-';
                    }
                    
                    if (key === '_document_id') {
                        rowHtml += `<td><code>${val}</code></td>`;
                    } else {
                        rowHtml += `<td>${val}</td>`;
                    }
                });
                tbody.innerHTML += `<tr>${rowHtml}</tr>`;
            });

        } else {
            thead.innerHTML = `<th>สถานะ</th>`;
            tbody.innerHTML = `<tr><td class="loading">ตารางนี้ยังไม่มีข้อมูล (Document ว่างเปล่า)</td></tr>`;
        }
    } catch (error) {
        console.error('Error:', error);
        tbody.innerHTML = `<tr><td class="loading" style="color: red;">ไม่สามารถดึงข้อมูลตารางนี้ได้</td></tr>`;
    }
}