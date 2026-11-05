from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import hashlib
import requests
import hmac
import base64
import json
import secrets
import logging
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Literal
from pydantic import BaseModel, Field
import vertexai
from vertexai.preview import rag
from vertexai.generative_models import GenerativeModel, GenerationConfig
from google.cloud import firestore

# 1. กำหนดค่าโปรเจกต์และคลังข้อมูล
PROJECT_ID = "tatip-ai-rag"
LOCATION = "asia-southeast1"
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


def get_optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Dict[str, Any] | None:
    if credentials is None:
        return None
    return require_current_user(credentials)


def require_admin_user(
    current_user: Dict[str, Any] = Depends(require_current_user),
) -> Dict[str, Any]:
    if current_user.get("ROLE") != "admin":
        raise HTTPException(status_code=403, detail="เฉพาะ Admin เท่านั้นที่จัดการเอกสารความรู้ได้")
    return current_user

def require_admin_or_manager(
    current_user: Dict[str, Any] = Depends(require_current_user),
) -> Dict[str, Any]:
    if current_user.get("ROLE") not in {"admin", "branch_manager"}:
        raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ใช้งานส่วนผู้ดูแลระบบ")
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

class CustomerSupportCaseRequest(BaseModel):
    issue: str = Field(min_length=5, max_length=3000)
    camera_name: str = Field(default="", max_length=120)
    conversation_id: str | None = None

class UpdateSupportCaseStatusRequest(BaseModel):
    status: Literal["open", "in_progress", "resolved", "closed"]
    admin_note: str | None = Field(default=None, max_length=2000)

class PublicContactCaseRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=8, max_length=30)
    issue: str = Field(min_length=5, max_length=3000)
    website: str = Field(default="", max_length=200)

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
            "gemini-3.5-flash",
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
def get_all_users(current_user: Dict[str, Any] = Depends(require_admin_or_manager)):
    try:
        users_ref = db.collection("users")
        if current_user["ROLE"] == "branch_manager":
            users_ref = users_ref.where("TENANT_ID", "==", current_user["TENANT_ID"])
        user_list = []
        for doc in users_ref.stream():
            data = doc.to_dict() or {}
            user_list.append({
                "id": doc.id,
                "user_id": data.get("USER_ID", "-"),
                "username": data.get("USERNAME", "-"),
                "tenant_id": data.get("TENANT_ID", "-"),
                "role": data.get("ROLE", "-")
            })
        return {"status": "success", "data": user_list}
    except Exception as e:
        logging.exception("Failed to list users")
        raise HTTPException(status_code=500, detail="ไม่สามารถโหลดรายชื่อผู้ใช้ได้") from e

# API ดึงข้อมูลทุก Collection แบบไดนามิก
@app.get("/api/admin/collection/{collection_name}")
def get_collection_data(
    collection_name: str,
    current_user: Dict[str, Any] = Depends(require_admin_or_manager),
):
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
        if current_user["ROLE"] == "branch_manager":
            ref = ref.where("TENANT_ID", "==", current_user["TENANT_ID"])
        data_list = []
        for doc in ref.stream():
            item = doc.to_dict() or {}
            item.pop("PASSWORD", None)
            item["_document_id"] = doc.id
            data_list.append(item)
            
        return {"status": "success", "data": data_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API สร้าง User และ Tenant ใน batch เดียวกัน
@app.post("/api/admin/create-user")
def create_customer_user(
    request: CreateUserRequest,
    current_user: Dict[str, Any] = Depends(require_admin_or_manager),
):
    try:
        if request.role not in {"admin", "branch_manager", "customer"}:
            raise HTTPException(status_code=400, detail="บทบาทผู้ใช้ไม่ถูกต้อง")
        if current_user["ROLE"] == "branch_manager":
            if request.tenant_id != current_user["TENANT_ID"]:
                raise HTTPException(status_code=403, detail="จัดการผู้ใช้ได้เฉพาะ Tenant ของตนเอง")
            if request.role != "customer":
                raise HTTPException(status_code=403, detail="Branch Manager สร้างได้เฉพาะบัญชี Customer")
            existing_user = db.collection("users").document(request.user_id).get()
            if existing_user.exists:
                existing_data = existing_user.to_dict() or {}
                if existing_data.get("TENANT_ID") != current_user["TENANT_ID"]:
                    raise HTTPException(status_code=403, detail="ไม่สามารถแก้ไขบัญชีของ Tenant อื่นได้")
                if existing_data.get("ROLE") != "customer":
                    raise HTTPException(status_code=403, detail="ไม่สามารถแก้ไขบัญชีผู้ดูแลระบบได้")
            tenant_snapshot = db.collection("tenants").document(request.tenant_id).get()
            if not tenant_snapshot.exists:
                raise HTTPException(status_code=404, detail="ไม่พบข้อมูลองค์กรของผู้ใช้")
            tenant_data = tenant_snapshot.to_dict() or {}
            tenant_name = tenant_data.get("TENANT_NAME") or request.tenant_id
        else:
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
        if current_user["ROLE"] == "admin":
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
        logging.exception("Failed to create user")
        raise HTTPException(status_code=500, detail="ไม่สามารถสร้างบัญชีผู้ใช้ได้") from e

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


# Public contact form supports both signed-in users and visitors.
@app.post("/api/contact-cases", status_code=201)
def submit_public_contact_case(
    request: PublicContactCaseRequest,
    current_user: Dict[str, Any] | None = Depends(get_optional_current_user),
):
    # Honeypot for basic bot filtering. Never persist visitor supplied identifiers.
    if request.website.strip():
        return {"status": "success", "message": "ส่งข้อความให้เจ้าหน้าที่แล้ว"}

    full_name = request.full_name.strip()
    phone = request.phone.strip()
    issue = request.issue.strip()
    phone_digits = "".join(character for character in phone if character.isdigit())
    if not full_name or len(phone_digits) < 8 or len(issue) < 5:
        raise HTTPException(status_code=400, detail="กรุณาตรวจสอบชื่อ เบอร์โทรศัพท์ และรายละเอียดปัญหา")

    now = datetime.now(timezone.utc)
    case_id = f"CASE-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3).upper()}"
    user_id = current_user["USER_ID"] if current_user else "guest"
    tenant_id = (current_user.get("TENANT_ID") or "tenant_general") if current_user else "tenant_general"
    branch_name = ""
    if current_user:
        user_query = db.collection("users").where("USER_ID", "==", user_id).limit(1).stream()
        user_snapshot = next(iter(user_query), None)
        if user_snapshot:
            branch_name = (user_snapshot.to_dict() or {}).get("BRANCH_NAME", "")

    case_data = {
        "CASE_ID": case_id,
        "USER_ID": user_id,
        "TENANT_ID": tenant_id,
        "CONTACT_NAME": full_name,
        "CONTACT_PHONE": phone,
        "BRANCH": branch_name or "Online Support",
        "CAMERA_NAME": "N/A",
        "CAMERA_STATUS": "unknown",
        "ISSUE": issue,
        "PRIORITY": "P2",
        "STATUS": "open",
        "SOURCE": "contact_form",
        "COMPLETED_STEPS": [],
        "CREATE_DATE": now,
        "UPDATED_AT": now,
    }
    try:
        db.collection("support_cases").document(case_id).set(case_data)
        return {
            "status": "success",
            "case_id": case_id,
            "message": "ส่งข้อความให้เจ้าหน้าที่แล้ว",
            "linked_to_account": bool(current_user),
        }
    except Exception as error:
        logging.exception("Failed to submit public contact case")
        raise HTTPException(status_code=500, detail="ไม่สามารถส่งข้อความได้ กรุณาลองใหม่") from error


# API สำหรับลูกค้าเปิด Support Case จากหน้าแชท
@app.post("/api/support-cases", status_code=201)
def submit_customer_support_case(
    request: CustomerSupportCaseRequest,
    current_user: Dict[str, Any] = Depends(require_current_user),
):
    issue = request.issue.strip()
    if len(issue) < 5:
        raise HTTPException(status_code=400, detail="กรุณาระบุรายละเอียดปัญหาอย่างน้อย 5 ตัวอักษร")

    user_id = current_user["USER_ID"]
    tenant_id = current_user.get("TENANT_ID")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="ไม่พบข้อมูลองค์กรของผู้ใช้")

    try:
        user_snapshot = db.collection("users").document(user_id).get()
        user_data = user_snapshot.to_dict() or {} if user_snapshot.exists else {}
        now = datetime.now(timezone.utc)
        case_id = f"CASE-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3).upper()}"
        conversation_id = (request.conversation_id or user_id).strip()
        case_data = {
            "CASE_ID": case_id,
            "USER_ID": user_id,
            "TENANT_ID": tenant_id,
            "CONVERSATION_ID": conversation_id,
            "BRANCH": user_data.get("BRANCH_NAME", ""),
            "CAMERA_NAME": request.camera_name.strip(),
            "CAMERA_STATUS": "unknown",
            "ISSUE": issue,
            "PRIORITY": "P2",
            "STATUS": "open",
            "COMPLETED_STEPS": [],
            "CREATE_DATE": now,
            "UPDATED_AT": now,
        }
        db.collection("support_cases").document(case_id).set(case_data)
        return {
            "status": "success",
            "case_id": case_id,
            "message": "ส่งเรื่องให้เจ้าหน้าที่สำเร็จ",
        }
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to submit customer support case")
        raise HTTPException(status_code=500, detail="ไม่สามารถส่งเรื่องให้เจ้าหน้าที่ได้ กรุณาลองใหม่") from error


def line_api_configured() -> bool:
    return bool(os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip())


def send_line_push_message(line_user_id: str, message_text: str) -> bool:
    access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not access_token or not line_user_id:
        return False
    try:
        response = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "to": line_user_id,
                "messages": [{"type": "text", "text": message_text[:5000]}],
            },
            timeout=10,
        )
        if response.status_code >= 300:
            logging.warning("LINE push failed with status %s", response.status_code)
            return False
        return True
    except requests.RequestException:
        logging.exception("LINE push request failed")
        return False


def notify_support_case_status(case_data: Dict[str, Any], case_id: str, status: str) -> str:
    user_id = str(case_data.get("USER_ID") or "").strip()
    if not user_id or user_id.lower() == "guest":
        return "not_linked"
    try:
        user_query = db.collection("users").where("USER_ID", "==", user_id).limit(1).stream()
        user_snapshot = next(iter(user_query), None)
        if user_snapshot is None:
            user_snapshot = db.collection("users").document(user_id).get()
            if not user_snapshot.exists:
                return "not_linked"
        user_data = user_snapshot.to_dict() or {}
        line_user_id = str(user_data.get("LINE_USER_ID") or "").strip()
        if not line_user_id:
            return "not_linked"

        status_labels = {
            "open": "รับเรื่องแล้ว",
            "in_progress": "กำลังดำเนินการ",
            "resolved": "แก้ไขแล้ว",
            "closed": "ปิดเคสแล้ว",
        }
        message_text = (
            f"TATIP AI แจ้งอัปเดตเคส {case_id}\n"
            f"สถานะ: {status_labels.get(status, status)}"
        )
        return "sent" if send_line_push_message(line_user_id, message_text) else "failed"
    except Exception:
        logging.exception("Could not prepare LINE status notification")
        return "failed"


# API สำหรับ Admin หรือ Branch Manager อัปเดตสถานะ Support Case
@app.patch("/api/admin/support-cases/{case_id}")
def update_support_case_status(
    case_id: str,
    request: UpdateSupportCaseStatusRequest,
    current_user: Dict[str, Any] = Depends(require_admin_or_manager),
):
    try:
        case_ref = db.collection("support_cases").document(case_id)
        case_snapshot = case_ref.get()
        if not case_snapshot.exists:
            raise HTTPException(status_code=404, detail="ไม่พบ Support Case นี้")

        case_data = case_snapshot.to_dict() or {}
        if current_user["ROLE"] == "branch_manager" and case_data.get("TENANT_ID") != current_user.get("TENANT_ID"):
            raise HTTPException(status_code=403, detail="จัดการได้เฉพาะเคสใน Tenant ของตนเอง")

        previous_status = case_data.get("STATUS")
        now = datetime.now(timezone.utc)
        updates = {
            "STATUS": request.status,
            "UPDATED_AT": now,
            "UPDATED_BY": current_user["USER_ID"],
        }
        if request.admin_note is not None:
            updates["ADMIN_NOTE"] = request.admin_note.strip()
        if request.status in {"resolved", "closed"}:
            updates["RESOLVED_AT"] = now
        else:
            updates["RESOLVED_AT"] = firestore.DELETE_FIELD

        case_ref.update(updates)
        line_notification = "unchanged"
        if previous_status != request.status:
            line_notification = notify_support_case_status(case_data, case_id, request.status)
        return {
            "status": "success",
            "case_id": case_id,
            "new_status": request.status,
            "line_notification": line_notification,
        }
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to update support case status")
        raise HTTPException(status_code=500, detail="ไม่สามารถอัปเดตสถานะเคสได้") from error


# API บันทึกเคสแจ้งปัญหา (Support Cases)
@app.post("/api/admin/create-case")
async def create_support_case(
    request: SupportCaseRequest,
    current_user: Dict[str, Any] = Depends(require_admin_user),
):
    try:
        case_data = {
            "CASE_ID": request.case_id,
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
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("Failed to add collection document")
        raise HTTPException(status_code=500, detail="ไม่สามารถเพิ่มข้อมูลได้") from e

# API สำหรับเพิ่มข้อมูลลงตารางใดๆ
@app.post("/api/admin/collection/add")
def add_document_to_collection(
    request: GenericDocumentRequest,
    current_user: Dict[str, Any] = Depends(require_admin_user),
):
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

# Create a short-lived code to securely link the signed-in TATIP account to LINE.
@app.get("/api/line/link-status")
def get_line_link_status(current_user: Dict[str, Any] = Depends(require_current_user)):
    user_query = db.collection("users").where("USER_ID", "==", current_user["USER_ID"]).limit(1).stream()
    user_snapshot = next(iter(user_query), None)
    if user_snapshot is None:
        user_snapshot = db.collection("users").document(current_user["USER_ID"]).get()
    user_data = user_snapshot.to_dict() or {} if user_snapshot.exists else {}
    return {"status": "success", "linked": bool(user_data.get("LINE_USER_ID"))}


@app.post("/api/line/link-code")
def create_line_link_code(current_user: Dict[str, Any] = Depends(require_current_user)):
    if not os.getenv("LINE_CHANNEL_SECRET", "").strip() or not line_api_configured():
        raise HTTPException(
            status_code=503,
            detail="LINE ยังตั้งค่าไม่ครบ กรุณาให้ผู้ดูแลตั้งค่า LINE Channel Secret และ Access Token ใน Cloud Run",
        )

    user_query = db.collection("users").where("USER_ID", "==", current_user["USER_ID"]).limit(1).stream()
    user_snapshot = next(iter(user_query), None)
    if user_snapshot is None:
        user_snapshot = db.collection("users").document(current_user["USER_ID"]).get()
    if not user_snapshot.exists:
        raise HTTPException(status_code=404, detail="ไม่พบบัญชีผู้ใช้")

    raw_code = f"TATIP-{secrets.token_hex(4).upper()}"
    code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    codes_ref = db.collection("line_link_codes")
    for old_code in codes_ref.where("USER_ID", "==", current_user["USER_ID"]).stream():
        old_code.reference.delete()

    codes_ref.document(code_hash).set({
        "USER_ID": current_user["USER_ID"],
        "USER_DOCUMENT_ID": user_snapshot.id,
        "CREATE_DATE": now,
        "EXPIRES_AT": now + timedelta(minutes=10),
    })
    return {
        "status": "success",
        "code": raw_code,
        "expires_in_seconds": 600,
        "message": "ส่งข้อความ เชื่อม ตามด้วยรหัสนี้ไปยัง LINE Official Account ของ TATIP ภายใน 10 นาที",
    }


# LINE Webhook Endpoint
@app.post("/api/line/webhook")
async def line_webhook(request: Request):
    try:
        body = await request.body()
        channel_secret = os.getenv("LINE_CHANNEL_SECRET", "").strip()
        signature = request.headers.get("x-line-signature", "")
        if channel_secret:
            expected_signature = base64.b64encode(
                hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
            ).decode("ascii")
            if not signature or not hmac.compare_digest(signature, expected_signature):
                raise HTTPException(status_code=401, detail="ลายเซ็น LINE webhook ไม่ถูกต้อง")

        data = json.loads(body.decode("utf-8") or "{}")
        events = data.get("events", [])

        for event in events:
            if event.get("type") != "message" or event.get("message", {}).get("type") != "text":
                continue
            source = event.get("source") or {}
            line_user_id = str(source.get("userId") or "")
            user_message = str(event.get("message", {}).get("text") or "").strip()
            if not line_user_id or not user_message:
                continue

            command_parts = user_message.split()
            if command_parts and command_parts[0].lower() in {"เชื่อม", "/link"}:
                if not channel_secret or not signature:
                    logging.warning("Rejected LINE account-link command because webhook signature validation is not configured")
                    continue
                if len(command_parts) != 2:
                    send_line_push_message(
                        line_user_id,
                        "รูปแบบไม่ถูกต้อง กรุณาส่งข้อความ “เชื่อม TATIP-รหัส” ที่แสดงบนหน้าเว็บ",
                    )
                    continue

                code_hash = hashlib.sha256(command_parts[1].upper().encode("utf-8")).hexdigest()
                code_ref = db.collection("line_link_codes").document(code_hash)
                transaction = db.transaction()

                @firestore.transactional
                def consume_link_code(txn):
                    code_snapshot = code_ref.get(transaction=txn)
                    if not code_snapshot.exists:
                        return "invalid"
                    code_data = code_snapshot.to_dict() or {}
                    expires_at = code_data.get("EXPIRES_AT")
                    if expires_at is None:
                        return "invalid"
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    if expires_at <= datetime.now(timezone.utc):
                        txn.delete(code_ref)
                        return "expired"

                    owner_user_id = code_data.get("USER_ID")
                    owner_doc_id = code_data.get("USER_DOCUMENT_ID")
                    user_ref = db.collection("users").document(owner_doc_id)
                    owner_snapshot = user_ref.get(transaction=txn)
                    if not owner_snapshot.exists:
                        txn.delete(code_ref)
                        return "invalid"
                    owner_data = owner_snapshot.to_dict() or {}
                    current_line_id = owner_data.get("LINE_USER_ID")
                    if current_line_id and current_line_id != line_user_id:
                        # Authenticated account owner may securely replace an old LINE connection.
                        pass

                    txn.set(user_ref, {
                        "LINE_USER_ID": line_user_id,
                        "LINE_LINKED_AT": datetime.now(timezone.utc),
                    }, merge=True)
                    txn.delete(code_ref)
                    return owner_user_id

                linked_user_id = consume_link_code(transaction)
                if linked_user_id == "expired":
                    send_line_push_message(line_user_id, "รหัสเชื่อมบัญชีหมดอายุแล้ว กรุณาสร้างรหัสใหม่จากหน้า TATIP")
                    continue
                if linked_user_id in {"invalid", None}:
                    send_line_push_message(line_user_id, "ไม่พบรหัสเชื่อมบัญชีนี้ หรือรหัสถูกใช้แล้ว กรุณาสร้างรหัสใหม่จากหน้า TATIP")
                    continue

                # A LINE account must not be connected to multiple TATIP accounts.
                linked_elsewhere = False
                for user_doc in db.collection("users").where("LINE_USER_ID", "==", line_user_id).stream():
                    if user_doc.to_dict().get("USER_ID") != linked_user_id:
                        linked_elsewhere = True
                        break
                if linked_elsewhere:
                    # This check is after transaction in old data; remove only the just-created link.
                    owner_query = db.collection("users").where("USER_ID", "==", linked_user_id).limit(1).stream()
                    owner_doc = next(iter(owner_query), None)
                    if owner_doc:
                        owner_doc.reference.update({"LINE_USER_ID": firestore.DELETE_FIELD, "LINE_LINKED_AT": firestore.DELETE_FIELD})
                    send_line_push_message(line_user_id, "LINE นี้เชื่อมกับบัญชี TATIP อื่นอยู่แล้ว กรุณาติดต่อผู้ดูแลระบบ")
                    continue

                send_line_push_message(line_user_id, f"เชื่อมบัญชี TATIP สำเร็จแล้ว ({linked_user_id}) จากนี้จะได้รับแจ้งเตือนสถานะเคสทาง LINE")
                continue

            # ข้อความอื่นยังคงใช้ RAG และบันทึกประวัติ LINE ตามเดิม
            users_ref = db.collection("users")
            query = users_ref.where("LINE_USER_ID", "==", line_user_id).stream()
            tenant_id = "default_tenant"
            for doc in query:
                user_data = doc.to_dict() or {}
                tenant_id = user_data.get("TENANT_ID", "default_tenant")

            ai_reply = await get_rag_reply(user_message)
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
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("LINE webhook processing failed")
        raise HTTPException(status_code=500, detail="ประมวลผล LINE webhook ไม่สำเร็จ") from error


