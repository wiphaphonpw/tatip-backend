from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import hashlib
import hmac
import base64
import json
import secrets
import logging
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
from pydantic import BaseModel, Field
import vertexai
from vertexai.preview import rag
from vertexai.generative_models import GenerativeModel, GenerationConfig
from google.cloud import firestore

# 1. กำหนดค่าโปรเจกต์และคลังข้อมูล
PROJECT_ID = "tatip-ai-rag"
LOCATION = "us-central1"
CORPUS_NAME = "projects/tatip-ai-rag/locations/asia-southeast1/ragCorpora/2305843009213693952"

# 2. เริ่มต้นการเชื่อมต่อ Vertex AI และ Firestore
vertexai.init(project=PROJECT_ID, location=LOCATION)
db = firestore.Client(project=PROJECT_ID, database="customers")

app = FastAPI()
bearer_scheme = HTTPBearer(auto_error=False)
SESSION_LIFETIME = timedelta(hours=24)

# อนุญาตให้หน้าเว็บ (Frontend) เรียกใช้งาน API ได้
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def create_session(user_document_id: str, user_id: str, tenant_id: str, role: str) -> str:
    """Create an opaque bearer token and store only its hash in a user subcollection."""
    token_payload = base64.urlsafe_b64encode(json.dumps({
        "document_id": user_document_id,
        "user_id": user_id,
    }).encode("utf-8")).decode("ascii").rstrip("=")
    token = f"{token_payload}.{secrets.token_urlsafe(32)}"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)

    db.collection("users").document(user_document_id).collection("sessions").document(token_hash).set({
        "USER_ID": user_id,
        "TENANT_ID": tenant_id,
        "ROLE": role,
        "CREATE_DATE": now,
        "EXPIRES_AT": now + SESSION_LIFETIME,
    })
    return token


def require_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="กรุณาเข้าสู่ระบบใหม่")

    token = credentials.credentials
    try:
        payload_part = token.split(".", 1)[0]
        padding = "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part + padding).decode("utf-8"))
        user_document_id = payload["document_id"]
        user_id = payload["user_id"]
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        session_ref = (
            db.collection("users").document(user_document_id)
            .collection("sessions").document(token_hash)
        )
        session_snapshot = session_ref.get()
        if not session_snapshot.exists:
            raise HTTPException(status_code=401, detail="เซสชันหมดอายุหรือไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่")

        session = session_snapshot.to_dict() or {}
        expires_at = session.get("EXPIRES_AT")
        if session.get("USER_ID") != user_id or expires_at is None:
            raise HTTPException(status_code=401, detail="เซสชันไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่")
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            session_ref.delete()
            raise HTTPException(status_code=401, detail="เซสชันหมดอายุ กรุณาเข้าสู่ระบบใหม่")

        return {
            "USER_ID": user_id,
            "TENANT_ID": session.get("TENANT_ID"),
            "ROLE": session.get("ROLE"),
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="เซสชันไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่")


def require_admin_user(
    current_user: Dict[str, Any] = Depends(require_current_user),
) -> Dict[str, Any]:
    if current_user.get("ROLE") != "admin":
        raise HTTPException(status_code=403, detail="เฉพาะ Admin เท่านั้นที่จัดการเอกสารความรู้ได้")
    return current_user

# ==========================================
# Pydantic Models
# ==========================================
class ChatRequest(BaseModel):
    message: str

class SaveChatHistoryRequest(BaseModel):
    user_id: str = Field(alias="USER_ID")
    tenant_id: str = Field(alias="TENANT_ID")
    message: str = Field(alias="MESSAGE")
    reply: str = Field(alias="REPLY")
    conversation_id: str | None = Field(default=None, alias="CONVERSATION_ID")

    class Config:
        allow_population_by_field_name = True

class CreateUserRequest(BaseModel):
    user_id: str
    username: str
    password: str
    tenant_id: str
    tenant_name: str = ""
    branch_name: str = ""
    role: str = "customer"

class LoginRequest(BaseModel):
    username: str
    password: str

class SupportCaseRequest(BaseModel):
    case_id: str
    user_id: str
    tenant_id: str
    branch: str
    camera_name: str
    issue: str
    priority: str = "P1"

class GenericDocumentRequest(BaseModel):
    collection_name: str
    document_id: str
    data: Dict[str, Any]


# ==========================================
# Helper Function สำหรับประมวลผล RAG
# ==========================================
async def get_rag_reply(user_message: str) -> str:
    try:
        # ใช้ rag.retrieval_query แทน rag.retrieval
        results = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=CORPUS_NAME)],
            text=user_message,
            similarity_top_k=3
        )

        context_text = ""
        if results and hasattr(results, 'contexts') and results.contexts:
            for context in results.contexts.contexts:
                if hasattr(context, 'text') and context.text:
                    context_text += context.text + "\n"

        if not context_text.strip():
            return "เรื่องนี้ยังไม่มีข้อมูลในคู่มือการใช้งาน รบกวนติดต่อทีมงาน (Admin) เพื่อสอบถามโดยตรงค่ะ"

        strict_system_instruction = (
            "คุณคือ TATIP AI ผู้ช่วยอัจฉริยะสำหรับสนับสนุนการให้บริการลูกค้าของระบบ TATIP "
            "หน้าที่ของคุณคือตอบคำถามโดยอ้างอิงจาก [ข้อมูลอ้างอิงจากคู่มือ] ที่แนบมาให้เท่านั้น "
            "ห้ามใช้ความรู้ทั่วไปภายนอก ห้ามแต่งคำตอบขึ้นมาเองเด็ดขาด"
        )

        model = GenerativeModel(
            "gemini-2.5-flash-lite",
            system_instruction=strict_system_instruction,
            generation_config=GenerationConfig(temperature=0.0)
        )

        prompt = f"[ข้อมูลอ้างอิงจากคู่มือ]\n{context_text}\n\n[คำถามของลูกค้า]\n{user_message}"
        response = model.generate_content(prompt)
        return response.text

    except Exception as e:
        print(f"RAG Processing Error: {str(e)}")
        return f"เกิดข้อผิดพลาดในการดึงข้อมูลคู่มือ: {str(e)}"


# ==========================================
# API Endpoints
# ==========================================

@app.get("/")
def read_root():
    return {"message": "TATIP AI Backend is running!"}

# API สำหรับรับคำถามและตอบกลับ (RAG Chat หน้าเว็บ)
@app.post("/api/chat")
async def chat_with_tatip(request: ChatRequest):
    reply_text = await get_rag_reply(request.message)
    return {"status": "success", "reply": reply_text}

# API ดึงรายชื่อผู้ใช้จาก Collection "users"
@app.get("/api/admin/users")
def get_all_users():
    try:
        users_ref = db.collection("users")
        docs = users_ref.stream()
        
        user_list = []
        for doc in docs:
            data = doc.to_dict()
            user_list.append({
                "id": doc.id,
                "user_id": data.get("USER_ID", "-"),
                "username": data.get("USERNAME", "-"),
                "password": data.get("PASSWORD", "-"),
                "tenant_id": data.get("TENANT_ID", "-"),
                "role": data.get("ROLE", "-")
            })
        return {"status": "success", "data": user_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API ดึงข้อมูลทุก Collection แบบไดนามิก
@app.get("/api/admin/collection/{collection_name}")
def get_collection_data(collection_name: str):
    try:
        allowed_collections = [
            "users", 
            "conversations", 
            "knowledge_articles", 
            "messages", 
            "support_cases", 
            "tenants", 
            "troubleshooting_state"
        ]
        
        if collection_name not in allowed_collections:
            raise HTTPException(status_code=400, detail="ไม่พบชื่อตารางนี้ในระบบ")

        ref = db.collection(collection_name)
        docs = ref.stream()
        
        data_list = []
        for doc in docs:
            item = doc.to_dict()
            item["_document_id"] = doc.id 
            data_list.append(item)
            
        return {"status": "success", "data": data_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API สร้าง User และ Tenant ใน batch เดียวกัน
@app.post("/api/admin/create-user")
def create_customer_user(request: CreateUserRequest):
    try:
        if request.role not in {"admin", "branch_manager", "customer"}:
            raise HTTPException(status_code=400, detail="บทบาทผู้ใช้ไม่ถูกต้อง")

        tenant_name = request.tenant_name.strip() or request.tenant_id
        batch = db.batch()
        batch.set(db.collection("users").document(request.user_id), {
            "USER_ID": request.user_id,
            "USERNAME": request.username,
            "PASSWORD": request.password,
            "TENANT_ID": request.tenant_id,
            "TENANT_NAME": tenant_name,
            "BRANCH_NAME": request.branch_name.strip(),
            "ROLE": request.role
        }, merge=True)
        batch.set(db.collection("tenants").document(request.tenant_id), {
            "TENANT_ID": request.tenant_id,
            "TENANT_NAME": tenant_name,
            "STATUS": "active"
        }, merge=True)
        batch.commit()
        return {"status": "success", "message": f"สร้างบัญชีให้กับ {request.username} สำเร็จเรียบร้อยแล้ว!"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API บันทึกข้อความผู้ใช้และ AI แยกเป็นคนละเอกสาร
@app.post("/api/save-chat-history")
async def save_chat_history(
    request: SaveChatHistoryRequest,
    current_user: Dict[str, Any] = Depends(require_current_user),
):
    try:
        user_id = request.user_id.strip()
        tenant_id = request.tenant_id.strip()
        conversation_id = (request.conversation_id or user_id).strip()
        if not all((user_id, tenant_id, conversation_id, request.message.strip(), request.reply.strip())):
            raise HTTPException(status_code=400, detail="ข้อมูลประวัติแชทต้องไม่เว้นว่าง")
        if user_id != current_user["USER_ID"] or tenant_id != current_user.get("TENANT_ID"):
            raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์บันทึกประวัติของบัญชีนี้")

        user_timestamp = datetime.now(timezone.utc)
        ai_timestamp = user_timestamp + timedelta(microseconds=1)
        batch = db.batch()
        messages_ref = db.collection("messages")
        for sender, text, created_at in (
            ("user", request.message, user_timestamp),
            ("ai", request.reply, ai_timestamp),
        ):
            batch.set(messages_ref.document(), {
                "CONVERSATION_ID": conversation_id,
                "USER_ID": user_id,
                "TENANT_ID": tenant_id,
                "SENDER": sender,
                "MESSAGE": text,
                "CREATE_DATE": created_at
            })

        batch.set(db.collection("conversations").document(conversation_id), {
            "CONVERSATION_ID": conversation_id,
            "USER_ID": user_id,
            "TENANT_ID": tenant_id,
            "LAST_MESSAGE": request.reply,
            "LAST_UPDATE": ai_timestamp
        }, merge=True)
        batch.commit()

        return {"status": "success", "conversation_id": conversation_id, "message": "บันทึกประวัติแชทสำเร็จ"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API อ่านประวัติแชทของผู้ใช้
@app.get("/api/history/{user_id}")
def get_chat_history(
    user_id: str,
    conversation_id: str | None = None,
    current_user: Dict[str, Any] = Depends(require_current_user),
):
    try:
        if user_id != current_user["USER_ID"]:
            raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ดูประวัติของบัญชีนี้")
        query = db.collection("messages").where("USER_ID", "==", user_id)
        if conversation_id:
            query = query.where("CONVERSATION_ID", "==", conversation_id)

        history = []
        for doc in query.stream():
            item = doc.to_dict()
            item["_document_id"] = doc.id
            created_at = item.get("CREATE_DATE")
            if hasattr(created_at, "isoformat"):
                item["CREATE_DATE"] = created_at.isoformat()
            history.append(item)
        history.sort(key=lambda item: item.get("CREATE_DATE") or "")
        return {"status": "success", "data": history}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API แสดงรายการเอกสารที่อัปโหลดเข้า RAG Corpus
@app.get("/api/admin/knowledge/files")
def list_knowledge_files(
    current_user: Dict[str, Any] = Depends(require_admin_user),
):
    try:
        documents = db.collection("knowledge_articles").where("SOURCE_TYPE", "==", "rag_file").stream()
        items = []
        for document in documents:
            item = document.to_dict() or {}
            item["_document_id"] = document.id
            created_at = item.get("CREATE_DATE")
            if hasattr(created_at, "isoformat"):
                item["CREATE_DATE"] = created_at.isoformat()
            items.append(item)
        items.sort(key=lambda item: item.get("CREATE_DATE") or "", reverse=True)
        return {"status": "success", "data": items}
    except Exception as error:
        logging.exception("Failed to list RAG knowledge files")
        raise HTTPException(status_code=500, detail="ไม่สามารถโหลดรายการเอกสารความรู้ได้") from error


# API อัปโหลดเอกสารจากหน้า Admin เข้า Vertex AI RAG Corpus
@app.post("/api/admin/knowledge/upload")
async def upload_knowledge_file(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    current_user: Dict[str, Any] = Depends(require_admin_user),
):
    allowed_suffixes = {".pdf", ".txt", ".md", ".docx", ".pptx", ".html", ".json"}
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in allowed_suffixes:
        raise HTTPException(status_code=400, detail="รองรับไฟล์ PDF, TXT, Markdown, DOCX, PPTX, HTML และ JSON เท่านั้น")

    document_title = (title.strip() or Path(filename).stem)[:120]
    max_file_size = 25 * 1024 * 1024
    temp_path = None
    total_size = 0
    try:
        with tempfile.NamedTemporaryFile(prefix="tatip-rag-", suffix=suffix, delete=False) as temp_file:
            temp_path = temp_file.name
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_file_size:
                    raise HTTPException(status_code=413, detail="ไฟล์มีขนาดเกิน 25 MB")
                temp_file.write(chunk)

        if total_size == 0:
            raise HTTPException(status_code=400, detail="ไฟล์ว่างเปล่า")

        rag_file = rag.upload_file(
            corpus_name=CORPUS_NAME,
            path=temp_path,
            display_name=document_title,
        )
        rag_file_name = getattr(rag_file, "name", "")
        if not rag_file_name:
            raise RuntimeError("RAG Engine did not return a file resource name")

        rag_file_id = rag_file_name.rsplit("/", 1)[-1]
        now = datetime.now(timezone.utc)
        db.collection("knowledge_articles").document(rag_file_id).set({
            "SOURCE_TYPE": "rag_file",
            "TITLE": document_title,
            "FILE_NAME": filename,
            "RAG_FILE_ID": rag_file_id,
            "RAG_FILE_NAME": rag_file_name,
            "STATUS": "พร้อมใช้",
            "UPLOADED_BY": current_user["USER_ID"],
            "FILE_SIZE_BYTES": total_size,
            "CREATE_DATE": now,
            "UPDATED_AT": now,
        }, merge=True)
        return {
            "status": "success",
            "title": document_title,
            "file_name": filename,
            "rag_file_id": rag_file_id,
            "message": "เพิ่มเอกสารเข้า RAG Corpus สำเร็จ",
        }
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to upload knowledge document to RAG Corpus")
        raise HTTPException(
            status_code=502,
            detail="นำเข้าเอกสารไม่สำเร็จ กรุณาตรวจสอบสิทธิ์ Vertex AI ของ Backend และลองใหม่",
        ) from error
    finally:
        await file.close()
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


# API บันทึกเคสแจ้งปัญหา (Support Cases)
@app.post("/api/admin/create-case")
async def create_support_case(request: SupportCaseRequest):
    try:
        case_data = {
            "CASE_id": request.case_id,
            "USER_ID": request.user_id,
            "TENANT_ID": request.tenant_id,
            "BRANCH": request.branch,
            "CAMERA_NAME": request.camera_name,
            "CAMERA_STATUS": "offline",
            "ISSUE": request.issue,
            "PRIORITY": request.priority,
            "STATUS": "open",
            "COMPLETED_STEPS": ["status"],
            "CREATE_DATE": datetime.now()
        }
        
        db.collection("support_cases").document(request.case_id).set(case_data)
        return {"status": "success", "message": f"สร้างเคส {request.case_id} สำเร็จเรียบร้อย"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API สำหรับเพิ่มข้อมูลลงตารางใดๆ
@app.post("/api/admin/collection/add")
def add_document_to_collection(request: GenericDocumentRequest):
    try:
        allowed_collections = [
            "users", 
            "conversations", 
            "knowledge_articles", 
            "messages", 
            "support_cases", 
            "tenants", 
            "troubleshooting_state"
        ]
        
        if request.collection_name not in allowed_collections:
            raise HTTPException(status_code=400, detail="ไม่อนุญาตให้เขียนข้อมูลลงตารางนี้")

        doc_ref = db.collection(request.collection_name).document(request.document_id)
        payload = request.data
        if "CREATE_DATE" not in payload:
            payload["CREATE_DATE"] = datetime.now()
            
        doc_ref.set(payload)
        return {"status": "success", "message": f"เพิ่มข้อมูลลงตาราง [{request.collection_name}] สำเร็จเรียบร้อยแล้ว!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API ตรวจสอบการเข้าสู่ระบบ
@app.post("/api/login")
def login_user(request: LoginRequest):
    try:
        users_ref = db.collection("users")
        docs = users_ref.where("USERNAME", "==", request.username).stream()
        
        user_found = None
        user_document_id = None
        for doc in docs:
            data = doc.to_dict()
            if data.get("PASSWORD") == request.password:
                user_found = data
                user_document_id = doc.id
                user_found.setdefault("USER_ID", doc.id)
                break
        
        if user_found:
            user_id = user_found.get("USER_ID")
            tenant_id = user_found.get("TENANT_ID")
            role = user_found.get("ROLE")
            access_token = create_session(user_document_id, user_id, tenant_id, role)
            return {
                "status": "success", 
                "message": "เข้าสู่ระบบสำเร็จ",
                "user_id": user_id,
                "role": role,
                "tenant_id": tenant_id,
                "access_token": access_token,
                "token_type": "bearer"
            }
        else:
            raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
            
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# LINE Webhook Endpoint
@app.post("/api/line/webhook")
async def line_webhook(request: Request):
    try:
        data = await request.json()
        events = data.get("events", [])

        for event in events:
            if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
                line_user_id = event["source"]["userId"]
                user_message = event["message"]["text"]

                # ค้นหาผู้ใช้จากตาราง users
                users_ref = db.collection("users")
                query = users_ref.where("LINE_USER_ID", "==", line_user_id).stream()

                tenant_id = "default_tenant"
                for doc in query:
                    user_data = doc.to_dict()
                    tenant_id = user_data.get("TENANT_ID", "default_tenant")

                # ประมวลผล RAG
                ai_reply = await get_rag_reply(user_message)

                # บันทึกประวัติ LINE ตาม schema messages/conversations
                user_id = line_user_id
                conversation_id = f"line_{line_user_id}"
                timestamp = datetime.now(timezone.utc)
                ai_timestamp = timestamp + timedelta(microseconds=1)
                batch = db.batch()
                messages_ref = db.collection("messages")
                for sender, text, created_at in (
                    ("user", user_message, timestamp),
                    ("ai", ai_reply, ai_timestamp),
                ):
                    batch.set(messages_ref.document(), {
                        "CONVERSATION_ID": conversation_id,
                        "USER_ID": user_id,
                        "TENANT_ID": tenant_id,
                        "SENDER": sender,
                        "MESSAGE": text,
                        "CREATE_DATE": created_at
                    })
                batch.set(db.collection("conversations").document(conversation_id), {
                    "CONVERSATION_ID": conversation_id,
                    "USER_ID": user_id,
                    "TENANT_ID": tenant_id,
                    "LAST_MESSAGE": ai_reply,
                    "LAST_UPDATE": ai_timestamp
                }, merge=True)
                batch.commit()

        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


