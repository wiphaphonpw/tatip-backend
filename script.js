// ==========================================
// 1. ระบบ Auth (Login)
// ==========================================
function handleLogin(event) {
    event.preventDefault();
    const lineId = document.getElementById("lineUserId").value;
    const tenantId = document.getElementById("tenantId").value;
    
    if (!lineId || !tenantId) return alert("กรุณากรอกข้อมูลให้ครบถ้วน");

    localStorage.setItem("tatip_logged_in", "true");
    localStorage.setItem("tatip_tenant", tenantId);
    window.location.href = "index.html";
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
    alert('ส่งข้อความถึงทีมวิศวกรสำเร็จ!');
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
        const response = await fetch("http://127.0.0.1:8000/api/chat", {
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
                        ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์ Backend ได้ กรุณาเปิดระบบ API (uvicorn) ก่อน
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
    history.unshift({
        id: 'CASE-' + Date.now().toString().slice(-8),
        question: question,
        answer: answer,
        date: new Date().toLocaleDateString('th-TH'),
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
        historyList.innerHTML = '<p style="color: #64748B; text-align: center; grid-column: 1/-1; padding: 2rem;">ยังไม่มีประวัติการสนทนา</p>';
        return;
    }
    history.forEach(item => {
        historyList.innerHTML += `
            <div class="history-card">
                <div>
                    <div class="history-header">
                        <div class="history-icon-type"><i class="fa-solid fa-message"></i></div>
                        <span class="status-badge resolved">AI Resolved</span>
                    </div>
                    <h4>${item.question}</h4>
                    <p>${item.answer.substring(0, 80)}...</p>
                </div>
                <div class="history-footer">
                    <span>${item.id} • ${item.date}</span>
                    <button class="btn-view-transcript" onclick="openTranscript('${item.question}', '${item.answer.replace(/\n/g, '<br>').replace(/'/g, "\\'")}')">ดูแชท <i class="fa-solid fa-arrow-right"></i></button>
                </div>
            </div>`;
    });
}

function filterHistory() {
    const searchText = document.getElementById('historySearch').value.toLowerCase();
    document.querySelectorAll('.history-card').forEach(card => {
        card.style.display = card.querySelector('h4').innerText.toLowerCase().includes(searchText) ? 'block' : 'none';
    });
}

function clearAllHistory() {
    if(confirm('ลบประวัติการสนทนาทั้งหมด?')) { localStorage.removeItem('tatip_chat_history'); loadHistory(); }
}

function openTranscript(question, answer) {
    const modal = document.getElementById('transcriptModal');
    if (modal) {
        document.getElementById('transcriptBody').innerHTML = `
            <div style="background: #F8FAFC; padding: 0.85rem; border-radius: 6px; margin-bottom: 0.75rem; font-size: 0.85rem;"><strong>คุณ:</strong><br>${question}</div>
            <div style="background: #FEE2E2; padding: 0.85rem; border-radius: 6px; font-size: 0.85rem;"><strong>TATIP AI:</strong><br>${answer}</div>`;
        modal.classList.add('active');
    }
}
function closeTranscriptModal() { document.getElementById('transcriptModal')?.classList.remove('active'); }

// ==========================================
// 6. ระบบ FAQ
// ==========================================
function toggleFaq(element) { element.parentElement.classList.toggle('active'); }
function filterFaqCategory(category, btnElement) {
    document.querySelectorAll('.chip').forEach(btn => btn.classList.remove('active'));
    btnElement.classList.add('active');
    document.querySelectorAll('.faq-accordion-item').forEach(item => {
        item.style.display = (category === 'all' || item.getAttribute('data-category') === category) ? 'block' : 'none';
    });
}