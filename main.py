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
import re
import tempfile
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Literal
from pydantic import BaseModel, Field
import vertexai
from vertexai.preview import rag
from vertexai.generative_models import GenerativeModel, GenerationConfig
from google.cloud import firestore, storage
from fastapi.responses import Response, StreamingResponse

# 1. กำหนดค่าโปรเจกต์และคลังข้อมูล (รองรับ Environment Variable Override)
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "tatip-ai-rag").strip()
LOCATION = os.getenv("GCP_LOCATION", "asia-southeast1").strip()
CORPUS_NAME = os.getenv(
    "RAG_CORPUS_NAME",
    "projects/tatip-ai-rag/locations/asia-southeast1/ragCorpora/2305843009213693952",
).strip()
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-3.5-flash").strip()
SUPPORT_MEDIA_BUCKET = os.getenv("SUPPORT_MEDIA_BUCKET", "").strip()
SUPPORT_FILE_LIMIT = 5
SUPPORT_FILE_BYTES_LIMIT = 20 * 1024 * 1024
SUPPORT_TOTAL_BYTES_LIMIT = 20 * 1024 * 1024
SUPPORT_ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/webm"}
SUPPORT_INFO_DEFAULTS = {
    "BUSINESS_HOURS": "ยังไม่ได้ตั้งค่าเวลาทำการ",
    "RESPONSE_TIME": "ยังไม่ได้กำหนดเวลาตอบกลับ",
    "TIMEZONE": "Asia/Bangkok",
}

# 2. เริ่มต้นการเชื่อมต่อ Vertex AI และ Firestore
try:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
except Exception as _e:
    logging.warning("Vertex AI initialization warning: %s", str(_e))

try:
    db = firestore.Client(project=PROJECT_ID, database="customers")
except Exception as _e:
    logging.warning("Firestore initialization warning: %s", str(_e))
    db = None

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
class ChatHistoryItem(BaseModel):
    question: str
    answer: str

class ChatRequest(BaseModel):
    message: str
    history: list[ChatHistoryItem] | None = None

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
    category: str = Field(default="", max_length=80)
    camera_name: str = Field(default="", max_length=120)
    conversation_id: str | None = None

class CustomerCaseReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=3000)

class CustomerCaseFeedbackRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=1000)

class SupportSettingsRequest(BaseModel):
    business_hours: str = Field(min_length=1, max_length=250)
    response_time: str = Field(min_length=1, max_length=200)

class UpdateSupportCaseStatusRequest(BaseModel):
    status: Literal["open", "in_progress", "resolved", "closed"]
    admin_note: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=80)

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


class CameraZoneItem(BaseModel):
    zone_id: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="general", max_length=40)
    floor: int = Field(default=1, ge=1, le=10)
    x_m: float = Field(ge=0.0, le=200.0)
    y_m: float = Field(ge=0.0, le=200.0)
    width_m: float = Field(gt=0.0, le=200.0)
    length_m: float = Field(gt=0.0, le=200.0)
    active: bool = True


class CameraPlacementItem(BaseModel):
    camera_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    floor: int = Field(default=1, ge=1, le=10)
    zone_id: str = Field(default="", max_length=60)
    zone_name: str = Field(default="", max_length=120)
    x_m: float = Field(ge=0.0, le=200.0)
    y_m: float = Field(ge=0.0, le=200.0)
    direction_deg: float = Field(default=90.0, ge=0.0, le=360.0)
    fov_deg: float = Field(default=90.0, ge=20.0, le=180.0)
    range_m: float = Field(default=10.0, ge=1.0, le=80.0)
    camera_type: str = Field(default="standard_4mp", max_length=60)
    resolution: str = Field(default="4MP (2688x1520)", max_length=80)
    lens_label: str = Field(default="3.6mm · FOV 88°", max_length=80)
    detection_distance: str = Field(default="ประมาณ 8–12 ม.", max_length=80)
    analytics: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=1000)
    is_custom: bool = False


class CameraLayoutSaveRequest(BaseModel):
    layout_id: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=140)
    business_type: str = Field(min_length=1, max_length=40)
    width_m: float = Field(ge=2.0, le=200.0)
    length_m: float = Field(ge=2.0, le=200.0)
    ceiling_height_m: float | None = Field(default=None, ge=1.8, le=30.0)
    floors: int = Field(default=1, ge=1, le=10)
    entrance_count: int = Field(default=1, ge=1, le=6)
    entrance_side: str = Field(default="front", max_length=20)
    analytics_focus: list[str] = Field(default_factory=list)
    zones: list[CameraZoneItem] = Field(default_factory=list)
    cameras: list[CameraPlacementItem] = Field(default_factory=list)
    coverage_summary: Dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=2000)


# ==========================================
# Helper Functions สำหรับประมวลผล RAG
# ==========================================
def prepare_rag_model_and_prompt(
    user_message: str,
    history: list[ChatHistoryItem] | None = None,
) -> tuple[Any, str | None, str | None]:
    """ค้นหา RAG Context ประกอบ Prompt และเตรียม Model คืนค่า (model, prompt, fallback_reply)"""
    search_query = user_message.strip()
    if history and len(search_query) < 40:
        last_item = history[-1]
        last_question = getattr(last_item, "question", "") or ""
        if last_question:
            search_query = f"{last_question} {search_query}"

    # ใช้ RagRetrievalConfig เพื่อรองรับ SDK ปัจจุบัน พร้อม fallback กรณีใช้พารามิเตอร์เดิม
    try:
        results = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=CORPUS_NAME)],
            text=search_query,
            rag_retrieval_config=rag.RagRetrievalConfig(top_k=3),
        )
    except (TypeError, AttributeError):
        results = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=CORPUS_NAME)],
            text=search_query,
            similarity_top_k=3,
        )

    context_chunks = []
    if results and hasattr(results, "contexts") and results.contexts:
        raw_contexts = getattr(results.contexts, "contexts", []) or []
        for index, context in enumerate(raw_contexts, 1):
            chunk_text = getattr(context, "text", "") or ""
            chunk_text = chunk_text.strip()
            if chunk_text:
                source_title = (
                    getattr(context, "source_display_name", "")
                    or getattr(context, "source_uri", "")
                    or f"เอกสารชุดที่ {index}"
                )
                context_chunks.append(f"--- แหล่งที่มา: {source_title} ---\n{chunk_text}")

    context_text = "\n\n".join(context_chunks).strip()
    if not context_text:
        return None, None, "เรื่องนี้ยังไม่มีข้อมูลในคู่มือการใช้งาน รบกวนติดต่อทีมงาน (Admin) เพื่อสอบถามโดยตรงค่ะ"

    strict_system_instruction = (
        "คุณคือ TATIP AI ผู้ช่วยอัจฉริยะสำหรับสนับสนุนการให้บริการลูกค้าของระบบ TATIP "
        "หน้าที่ของคุณคือตอบคำถามโดยอ้างอิงจาก [ข้อมูลอ้างอิงจากคู่มือ] ที่แนบมาให้เท่านั้น "
        "ห้ามใช้ความรู้ทั่วไปภายนอก ห้ามแต่งคำตอบขึ้นมาเองเด็ดขาด "
        "หากข้อมูลในคู่มือไม่ครอบคลุม ให้ตอบอย่างสุภาพว่าไม่มีข้อมูลในคู่มือและแนะนำให้ติดต่อทีมงาน Admin"
    )

    history_context = ""
    if history:
        turn_lines = []
        for item in history[-3:]:
            q = getattr(item, "question", "") or ""
            a = getattr(item, "answer", "") or ""
            if q:
                turn_lines.append(f"ลูกค้า: {q.strip()}")
            if a:
                turn_lines.append(f"TATIP AI: {a.strip()}")
        if turn_lines:
            history_context = "[บทสนทนาก่อนหน้า]\n" + "\n".join(turn_lines) + "\n\n"

    model = GenerativeModel(
        GEMINI_MODEL_NAME,
        system_instruction=strict_system_instruction,
        generation_config=GenerationConfig(temperature=0.0),
    )
    prompt = f"[ข้อมูลอ้างอิงจากคู่มือ]\n{context_text}\n\n{history_context}[คำถามของลูกค้า]\n{user_message}"
    return model, prompt, None


def extract_safe_candidate_text(response: Any) -> tuple[str, bool]:
    """ดึงข้อความจาก Candidate อย่างปลอดภัย พร้อมตรวจสอบ Safety Filter คืนค่า (text, is_safety_blocked)"""
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        first_candidate = candidates[0]
        finish_reason = getattr(first_candidate, "finish_reason", None)
        if finish_reason and "SAFETY" in str(finish_reason).upper():
            return "", True
        try:
            return (response.text or ""), False
        except (ValueError, AttributeError):
            content = getattr(first_candidate, "content", None)
            parts = getattr(content, "parts", []) or []
            extracted = [getattr(p, "text", "") for p in parts if getattr(p, "text", "")]
            return "".join(extracted), False
    return "", False


async def get_rag_reply(user_message: str, history: list[ChatHistoryItem] | None = None) -> str:
    try:
        model, prompt, fallback = prepare_rag_model_and_prompt(user_message, history)
        if fallback:
            return fallback

        response = await model.generate_content_async(prompt)
        reply_text, is_safety_blocked = extract_safe_candidate_text(response)

        if is_safety_blocked:
            return "ขออภัยค่ะ ระบบไม่สามารถประมวลผลคำตอบได้เนื่องจากติดข้อจำกัดด้านความปลอดภัย กรุณาติดต่อทีมงาน (Admin) ค่ะ"

        if not reply_text.strip():
            return "ขออภัยค่ะ ระบบไม่สามารถประมวลผลคำตอบได้ในขณะนี้ กรุณาติดต่อทีมงาน (Admin) เพื่อสอบถามโดยตรงค่ะ"

        return reply_text.strip()

    except Exception as e:
        logging.exception("RAG Processing Error: %s", str(e))
        return f"เกิดข้อผิดพลาดในการดึงข้อมูลคู่มือ: {str(e)}"


async def stream_rag_reply(user_message: str, history: list[ChatHistoryItem] | None = None):
    """สร้าง SSE Stream สำหรับทยอยแสดงผลคำตอบจาก Gemini RAG"""
    try:
        model, prompt, fallback = prepare_rag_model_and_prompt(user_message, history)
        if fallback:
            yield f"data: {json.dumps({'chunk': fallback}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True, 'reply': fallback}, ensure_ascii=False)}\n\n"
            return

        response_stream = await model.generate_content_async(prompt, stream=True)
        full_reply = []
        is_blocked = False

        async for chunk in response_stream:
            chunk_text, safety_blocked = extract_safe_candidate_text(chunk)
            if safety_blocked:
                is_blocked = True
                break
            if chunk_text:
                full_reply.append(chunk_text)
                yield f"data: {json.dumps({'chunk': chunk_text}, ensure_ascii=False)}\n\n"

        if is_blocked:
            safety_msg = "ขออภัยค่ะ ระบบไม่สามารถประมวลผลคำตอบได้เนื่องจากติดข้อจำกัดด้านความปลอดภัย กรุณาติดต่อทีมงาน (Admin) ค่ะ"
            yield f"data: {json.dumps({'chunk': safety_msg, 'done': True, 'reply': safety_msg}, ensure_ascii=False)}\n\n"
            return

        final_text = "".join(full_reply).strip()
        if not final_text:
            final_text = "ขออภัยค่ะ ระบบไม่สามารถประมวลผลคำตอบได้ในขณะนี้ กรุณาติดต่อทีมงาน (Admin) เพื่อสอบถามโดยตรงค่ะ"
            yield f"data: {json.dumps({'chunk': final_text}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'done': True, 'reply': final_text}, ensure_ascii=False)}\n\n"

    except Exception as e:
        logging.exception("RAG Streaming Error: %s", str(e))
        error_msg = f"เกิดข้อผิดพลาดในการดึงข้อมูลคู่มือ: {str(e)}"
        yield f"data: {json.dumps({'error': error_msg, 'done': True, 'reply': error_msg}, ensure_ascii=False)}\n\n"


# ==========================================
# API Endpoints
# ==========================================

def get_support_info():
    try:
        snapshot = db.collection("system_settings").document("support").get()
        saved = snapshot.to_dict() or {} if snapshot.exists else {}
    except Exception:
        logging.exception("Could not load public support settings")
        saved = {}
    business_hours = str(saved.get("BUSINESS_HOURS") or "").strip()
    response_time = str(saved.get("RESPONSE_TIME") or "").strip()
    return {
        "business_hours": business_hours or SUPPORT_INFO_DEFAULTS["BUSINESS_HOURS"],
        "response_time": response_time or SUPPORT_INFO_DEFAULTS["RESPONSE_TIME"],
        "timezone": str(saved.get("TIMEZONE") or SUPPORT_INFO_DEFAULTS["TIMEZONE"]),
        "configured": bool(business_hours and response_time),
    }


@app.get("/")
def read_root():
    return {"message": "TATIP AI Backend is running!"}


@app.get("/api/support/info")
def get_public_support_info():
    return {"status": "success", "data": get_support_info()}


@app.get("/api/admin/support-settings")
def get_admin_support_settings(current_user: Dict[str, Any] = Depends(require_admin_user)):
    snapshot = db.collection("system_settings").document("support").get()
    saved = snapshot.to_dict() or {} if snapshot.exists else {}
    return {
        "status": "success",
        "data": {
            "business_hours": str(saved.get("BUSINESS_HOURS") or ""),
            "response_time": str(saved.get("RESPONSE_TIME") or ""),
            "timezone": str(saved.get("TIMEZONE") or "Asia/Bangkok"),
            "updated_at": saved.get("UPDATED_AT").isoformat() if hasattr(saved.get("UPDATED_AT"), "isoformat") else None,
        },
    }


@app.put("/api/admin/support-settings")
def update_admin_support_settings(
    request: SupportSettingsRequest,
    current_user: Dict[str, Any] = Depends(require_admin_user),
):
    business_hours = request.business_hours.strip()
    response_time = request.response_time.strip()
    if not business_hours or not response_time:
        raise HTTPException(status_code=400, detail="กรุณากรอกเวลาทำการและเวลาตอบกลับ")
    db.collection("system_settings").document("support").set({
        "BUSINESS_HOURS": business_hours,
        "RESPONSE_TIME": response_time,
        "TIMEZONE": "Asia/Bangkok",
        "UPDATED_AT": datetime.now(timezone.utc),
        "UPDATED_BY": current_user["USER_ID"],
    }, merge=True)
    return {"status": "success", "message": "บันทึกข้อมูลการให้บริการแล้ว"}


# API สำหรับรับคำถามและตอบกลับ (RAG Chat หน้าเว็บ)
@app.post("/api/chat")
async def chat_with_tatip(request: ChatRequest):
    reply_text = await get_rag_reply(request.message, request.history)
    return {"status": "success", "reply": reply_text}


# API สำหรับรับคำถามและตอบกลับแบบ Streaming (SSE)
@app.post("/api/chat/stream")
async def chat_stream_with_tatip(request: ChatRequest):
    return StreamingResponse(
        stream_rag_reply(request.message, request.history),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

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

# ==========================================
# API ระบบวางแผนและบันทึกตำแหน่งกล้อง (Camera Layouts)
# ==========================================
def serialize_camera_layout(doc_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    created_at = data.get("CREATED_AT")
    updated_at = data.get("UPDATED_AT")
    return {
        "layout_id": data.get("LAYOUT_ID") or doc_id,
        "name": data.get("NAME", "แผนผังติดตั้งกล้อง"),
        "business_type": data.get("BUSINESS_TYPE", "restaurant"),
        "width_m": data.get("WIDTH_M", 10.0),
        "length_m": data.get("LENGTH_M", 12.0),
        "ceiling_height_m": data.get("CEILING_HEIGHT_M"),
        "floors": data.get("FLOORS", 1),
        "entrance_count": data.get("ENTRANCE_COUNT", 1),
        "entrance_side": data.get("ENTRANCE_SIDE", "front"),
        "analytics_focus": data.get("ANALYTICS_FOCUS") or [],
        "zones": data.get("ZONES") or [],
        "cameras": data.get("CAMERAS") or [],
        "coverage_summary": data.get("COVERAGE_SUMMARY") or {},
        "notes": data.get("NOTES", ""),
        "user_id": data.get("USER_ID", ""),
        "tenant_id": data.get("TENANT_ID", ""),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
    }


def can_access_camera_layout(data: Dict[str, Any], current_user: Dict[str, Any]) -> bool:
    role = current_user.get("ROLE")
    if role == "admin":
        return True
    if role == "branch_manager":
        return data.get("TENANT_ID") == current_user.get("TENANT_ID")
    return data.get("USER_ID") == current_user.get("USER_ID")


@app.get("/api/camera-layouts")
def list_camera_layouts(current_user: Dict[str, Any] = Depends(require_current_user)):
    try:
        ref = db.collection("camera_layouts")
        role = current_user.get("ROLE")
        if role == "branch_manager":
            ref = ref.where("TENANT_ID", "==", current_user.get("TENANT_ID"))
        elif role != "admin":
            ref = ref.where("USER_ID", "==", current_user.get("USER_ID"))

        items = [
            serialize_camera_layout(doc.id, doc.to_dict() or {})
            for doc in ref.stream()
        ]
        items.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
        return {"status": "success", "data": items}
    except Exception as error:
        logging.exception("Failed to list camera layouts")
        raise HTTPException(status_code=500, detail="ไม่สามารถโหลดรายการแผนผังกล้องได้") from error


@app.get("/api/camera-layouts/{layout_id}")
def get_camera_layout(
    layout_id: str,
    current_user: Dict[str, Any] = Depends(require_current_user),
):
    try:
        snapshot = db.collection("camera_layouts").document(layout_id).get()
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="ไม่พบแผนผังกล้องที่ต้องการ")
        data = snapshot.to_dict() or {}
        if not can_access_camera_layout(data, current_user):
            raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์เข้าถึงแผนผังกล้องนี้")
        return {"status": "success", "data": serialize_camera_layout(snapshot.id, data)}
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to get camera layout")
        raise HTTPException(status_code=500, detail="ไม่สามารถโหลดแผนผังกล้องได้") from error


@app.post("/api/camera-layouts")
def save_camera_layout(
    request: CameraLayoutSaveRequest,
    current_user: Dict[str, Any] = Depends(require_current_user),
):
    try:
        now = datetime.now(timezone.utc)
        requested_id = (request.layout_id or "").strip()
        is_update = False
        created_at = now

        if requested_id:
            if not all(ch.isalnum() or ch in "-_" for ch in requested_id):
                raise HTTPException(status_code=400, detail="รหัสแผนผังไม่ถูกต้อง")
            existing = db.collection("camera_layouts").document(requested_id).get()
            if existing.exists:
                existing_data = existing.to_dict() or {}
                if not can_access_camera_layout(existing_data, current_user):
                    raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์แก้ไขแผนผังกล้องนี้")
                is_update = True
                created_at = existing_data.get("CREATED_AT") or now
            layout_id = requested_id
        else:
            layout_id = f"LAY-{secrets.token_hex(4).upper()}"

        payload = {
            "LAYOUT_ID": layout_id,
            "NAME": request.name.strip(),
            "BUSINESS_TYPE": request.business_type.strip(),
            "WIDTH_M": round(request.width_m, 2),
            "LENGTH_M": round(request.length_m, 2),
            "CEILING_HEIGHT_M": round(request.ceiling_height_m, 2) if request.ceiling_height_m is not None else None,
            "FLOORS": request.floors,
            "ENTRANCE_COUNT": request.entrance_count,
            "ENTRANCE_SIDE": request.entrance_side.strip(),
            "ANALYTICS_FOCUS": request.analytics_focus,
            "ZONES": [z.model_dump() for z in request.zones],
            "CAMERAS": [c.model_dump() for c in request.cameras],
            "COVERAGE_SUMMARY": request.coverage_summary,
            "NOTES": request.notes.strip(),
            "USER_ID": current_user["USER_ID"],
            "TENANT_ID": current_user.get("TENANT_ID") or "default_tenant",
            "CREATED_AT": created_at,
            "UPDATED_AT": now,
        }

        db.collection("camera_layouts").document(layout_id).set(payload, merge=True)
        return {
            "status": "success",
            "layout_id": layout_id,
            "updated": is_update,
            "message": "บันทึกแผนผังการติดตั้งกล้องเรียบร้อยแล้ว",
            "data": serialize_camera_layout(layout_id, payload),
        }
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to save camera layout")
        raise HTTPException(status_code=500, detail="ไม่สามารถบันทึกแผนผังกล้องได้") from error


@app.delete("/api/camera-layouts/{layout_id}")
def delete_camera_layout(
    layout_id: str,
    current_user: Dict[str, Any] = Depends(require_current_user),
):
    try:
        doc_ref = db.collection("camera_layouts").document(layout_id)
        snapshot = doc_ref.get()
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="ไม่พบแผนผังกล้องที่ต้องการลบ")
        data = snapshot.to_dict() or {}
        if not can_access_camera_layout(data, current_user):
            raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ลบแผนผังกล้องนี้")
        doc_ref.delete()
        return {"status": "success", "message": "ลบแผนผังการติดตั้งกล้องสำเร็จแล้ว"}
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to delete camera layout")
        raise HTTPException(status_code=500, detail="ไม่สามารถลบแผนผังกล้องได้") from error


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
            "troubleshooting_state",
            "camera_layouts",
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

        transformation_config = None
        try:
            from vertexai.preview.rag import TransformationConfig, ChunkingConfig
            transformation_config = TransformationConfig(
                chunking_config=ChunkingConfig(
                    chunk_size=1024,
                    chunk_overlap=200,
                )
            )
        except Exception:
            logging.warning("TransformationConfig not supported or failed to initialize, using default chunking")

        rag_file = rag.upload_file(
            corpus_name=CORPUS_NAME,
            path=temp_path,
            display_name=document_title,
            transformation_config=transformation_config,
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


# API ลบเอกสารความรู้ออกจาก Vertex AI RAG Corpus และ Firestore
@app.delete("/api/admin/knowledge/{rag_file_id}")
def delete_knowledge_file(
    rag_file_id: str,
    current_user: Dict[str, Any] = Depends(require_admin_user),
):
    try:
        doc_ref = db.collection("knowledge_articles").document(rag_file_id)
        doc_snap = doc_ref.get()
        if not doc_snap.exists:
            raise HTTPException(status_code=404, detail="ไม่พบเอกสารนี้ในระบบ")

        doc_data = doc_snap.to_dict() or {}
        rag_file_name = doc_data.get("RAG_FILE_NAME") or f"{CORPUS_NAME}/ragFiles/{rag_file_id}"

        # 1. ลบไฟล์ออกจาก Vertex AI RAG Corpus (ถ้ามีอยู่)
        try:
            rag.delete_file(name=rag_file_name)
        except Exception as rag_err:
            logging.warning("Could not delete file from Vertex AI RAG Corpus: %s", str(rag_err))

        # 2. ลบออกจาก Firestore Collection knowledge_articles
        doc_ref.delete()

        return {
            "status": "success",
            "message": f"ลบเอกสาร '{doc_data.get('TITLE') or rag_file_id}' สำเร็จเรียบร้อยแล้ว",
        }
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to delete knowledge document: %s", str(error))
        raise HTTPException(status_code=500, detail="ไม่สามารถลบเอกสารความรู้ได้") from error


# Public contact form supports both signed-in users and visitors.
def inline_content_disposition(filename: str) -> str:
    """Build an RFC 5987 Content-Disposition header safe for Unicode filenames."""
    ascii_fallback = "".join(
        character for character in filename
        if 32 <= ord(character) <= 126 and character not in {'"', "\\"}
    ) or "attachment"
    encoded_filename = quote(filename, safe="")
    return f'inline; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_filename}'


def support_media_bucket():
    if not SUPPORT_MEDIA_BUCKET:
        raise HTTPException(status_code=503, detail="ยังไม่ได้ตั้งค่า Cloud Storage สำหรับไฟล์แนบ")
    return storage.Client(project=PROJECT_ID).bucket(SUPPORT_MEDIA_BUCKET)


async def store_support_attachments(case_id: str, files: list[UploadFile]) -> list[Dict[str, Any]]:
    valid_uploads: list[UploadFile] = []
    for upload in files or []:
        filename = (upload.filename or "").strip()
        if not filename:
            probe = await upload.read(1)
            if not probe:
                continue
            await upload.seek(0)
        valid_uploads.append(upload)

    if not valid_uploads:
        return []
    if len(valid_uploads) > SUPPORT_FILE_LIMIT:
        raise HTTPException(status_code=400, detail=f"แนบไฟล์ได้ไม่เกิน {SUPPORT_FILE_LIMIT} ไฟล์")

    bucket = support_media_bucket()
    attachments = []
    total_bytes = 0
    for upload in valid_uploads:
        content_type = (upload.content_type or "").lower().split(";", 1)[0].strip()
        if content_type not in SUPPORT_ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=400, detail="รองรับเฉพาะ JPG, PNG, WEBP, MP4 และ WEBM")
        content = await upload.read(SUPPORT_FILE_BYTES_LIMIT + 1)
        if not content:
            raise HTTPException(status_code=400, detail="ไฟล์แนบต้องไม่ว่างเปล่า")
        if len(content) > SUPPORT_FILE_BYTES_LIMIT:
            raise HTTPException(status_code=413, detail="ไฟล์แต่ละไฟล์ต้องมีขนาดไม่เกิน 20 MB")
        total_bytes += len(content)
        if total_bytes > SUPPORT_TOTAL_BYTES_LIMIT:
            raise HTTPException(status_code=413, detail="ไฟล์แนบทั้งหมดรวมกันต้องไม่เกิน 20 MB")

        original_name = Path(upload.filename or "attachment").name
        safe_name = "".join(char for char in original_name if char.isalnum() or char in "._-")[:100] or "attachment"
        attachment_id = secrets.token_hex(12)
        object_name = f"support-cases/{case_id}/{attachment_id}-{safe_name}"
        bucket.blob(object_name).upload_from_string(content, content_type=content_type)
        attachments.append({
            "ATTACHMENT_ID": attachment_id,
            "FILE_NAME": safe_name,
            "OBJECT_NAME": object_name,
            "CONTENT_TYPE": content_type,
            "SIZE_BYTES": len(content),
            "CREATE_DATE": datetime.now(timezone.utc),
        })
    return attachments


SUPPORT_CATEGORY_ALIASES = {
    "cctv": "กล้อง / CCTV",
    "camera": "กล้อง / CCTV",
    "กล้อง": "กล้อง / CCTV",
    "กล้อง / cctv": "กล้อง / CCTV",
    "roi": "การตี ROI / พื้นที่วิเคราะห์",
    "การตี roi": "การตี ROI / พื้นที่วิเคราะห์",
    "การตี roi / พื้นที่วิเคราะห์": "การตี ROI / พื้นที่วิเคราะห์",
    "auth": "การเข้าสู่ระบบ / บัญชีผู้ใช้",
    "login": "การเข้าสู่ระบบ / บัญชีผู้ใช้",
    "บัญชีผู้ใช้": "การเข้าสู่ระบบ / บัญชีผู้ใช้",
    "การเข้าสู่ระบบ": "การเข้าสู่ระบบ / บัญชีผู้ใช้",
    "การเข้าสู่ระบบ / บัญชีผู้ใช้": "การเข้าสู่ระบบ / บัญชีผู้ใช้",
    "dashboard": "Dashboard และรายงาน",
    "dashboard และรายงาน": "Dashboard และรายงาน",
    "alerts": "การแจ้งเตือน (LINE / ระบบ)",
    "การแจ้งเตือน": "การแจ้งเตือน (LINE / ระบบ)",
    "การแจ้งเตือน (line / ระบบ)": "การแจ้งเตือน (LINE / ระบบ)",
    "network": "เครือข่าย / อินเทอร์เน็ต",
    "เครือข่าย": "เครือข่าย / อินเทอร์เน็ต",
    "เครือข่าย / อินเทอร์เน็ต": "เครือข่าย / อินเทอร์เน็ต",
    "ai_chat": "แชท AI / คู่มือระบบ",
    "แชท ai": "แชท AI / คู่มือระบบ",
    "แชท ai / คู่มือระบบ": "แชท AI / คู่มือระบบ",
    "unrelated": "เรื่องที่ไม่เกี่ยวข้อง",
    "off_topic": "เรื่องที่ไม่เกี่ยวข้อง",
    "เรื่องที่ไม่เกี่ยวข้อง": "เรื่องที่ไม่เกี่ยวข้อง",
    "ไม่เกี่ยวข้อง": "เรื่องที่ไม่เกี่ยวข้อง",
}

LEGACY_UNCLASSIFIED_CATEGORY_LABELS = {
    "",
    "auto",
    "all",
    "อื่น ๆ",
    "อื่นๆ",
    "other",
    "others",
    "ทั่วไป",
    "general",
    "ไม่ระบุ",
}


SUPPORT_ISSUE_CLASSIFICATION_RULES = (
    (
        "การตี ROI / พื้นที่วิเคราะห์",
        re.compile(r"(?<![a-z])roi(?![a-z])|ตี\s*roi|ตีกรอบ|วาดกรอบ|ขีดเส้น|กำหนดจุด|กำหนดพื้นที่|พื้นที่วิเคราะห์|พื้นที่ตรวจจับ|polygon|โซนตรวจจับ|เขตพื้นที่|นับคนเข้าออก|(?<![a-z])zone(?![a-z])"),
    ),
    (
        "การแจ้งเตือน (LINE / ระบบ)",
        re.compile(r"แจ้งเตือน|แจ้งเหตุ|ส่งข้อความเข้าไลน์|ทางไลน์|ผ่านไลน์|เข้าไลน์|บัญชีไลน์|เชื่อม\s*line|เชื่อมบัญชี\s*line|ไลน์\s*oa|(?<![ฟน])ไลน์|(?<![a-z])(?:line|notification|notify|alert|push)(?![a-z])"),
    ),
    (
        "การเข้าสู่ระบบ / บัญชีผู้ใช้",
        re.compile(r"เข้าสู่ระบบ|เข้าระบบ|ล็อกอิน|ล็อคอิน|เข้าใช้งานไม่ได้|เข้าไม่ได้|(?<![a-z])(?:login|logout|password|username|account|session|token)(?![a-z])|ออกจากระบบ|รหัสผ่าน|ลืมรหัส|เปลี่ยนรหัส|บัญชี|สร้างบัญชี|เพิ่มผู้ใช้|เปิดสิทธิ์|สิทธิ์|เซสชัน|(?<!\d)(?:401|403)(?!\d)|ไม่พบผู้ใช้"),
    ),
    (
        "Dashboard และรายงาน",
        re.compile(r"(?<![a-z])(?:dashboard|heatmap)(?![a-z])|แดชบอร์ด|ฮีทแมพ|รายงาน|สถิติ|กราฟ|สรุปผล|สรุปยอด|ตัวเลขไม่ขึ้น"),
    ),
    (
        "เครือข่าย / อินเทอร์เน็ต",
        re.compile(r"(?<![a-z])(?:internet|network|wifi|wi-fi|bandwidth|firewall|lan|poe)(?![a-z])|อินเทอร์เน็ต|เครือข่าย|พอร์ต\s*554|เน็ตหลุด|สัญญาณหลุด|เชื่อมต่อไม่ได้|หลุดบ่อย|เน็ต"),
    ),
    (
        "กล้อง / CCTV",
        re.compile(r"กล้อง|(?<![a-z])(?:cctv|camera|cam\d*|ip\s*address|rtsp|ptz|nvr|dvr|onvif|offline|black\s*screen)(?![a-z])|ภาพดับ|จอดำ|จอมืด|ไม่มีภาพ|ภาพไม่ขึ้น|ภาพค้าง|ภาพมัว|ดูภาพ|ดูย้อนหลัง|ออฟไลน์|ออนไลน์|เลนส์|ติดกล้อง|ติดตั้งกล้อง|วางกล้อง|จุดติดกล้อง|มุมกล้อง|ไมโครโฟน|ไมค์|เสียง"),
    ),
    (
        "แชท AI / คู่มือระบบ",
        re.compile(r"แชท|(?<![a-z])(?:chat|rag|tatip\s*ai|bot)(?![a-z])|คู่มือ|ถาม\s*ai|บอท|ตอบผิด|ไม่ตอบ|ประวัติการสนทนา"),
    ),
)


def classify_support_issue_category(explicit_category: str | None, issue_text: str = "") -> str:
    raw = explicit_category.strip() if isinstance(explicit_category, str) else ""
    if raw and raw.lower() not in LEGACY_UNCLASSIFIED_CATEGORY_LABELS:
        mapped = SUPPORT_CATEGORY_ALIASES.get(raw.lower())
        if mapped:
            return mapped
        return raw[:80]

    text = str(issue_text or "").lower()
    for category_name, pattern in SUPPORT_ISSUE_CLASSIFICATION_RULES:
        if pattern.search(text):
            return category_name
    return "เรื่องที่ไม่เกี่ยวข้อง"


# Public contact form supports both signed-in users and visitors.
@app.post("/api/contact-cases", status_code=201)
async def submit_public_contact_case(
    full_name: str = Form(..., min_length=1, max_length=120),
    phone: str = Form(..., min_length=8, max_length=30),
    issue: str = Form(..., min_length=5, max_length=3000),
    category: str = Form(default="", max_length=80),
    website: str = Form(default="", max_length=200),
    attachments: list[UploadFile] = File(default=[]),
    current_user: Dict[str, Any] | None = Depends(get_optional_current_user),
):
    if website.strip():
        return {"status": "success", "message": "ส่งข้อความให้เจ้าหน้าที่แล้ว"}

    full_name = full_name.strip()
    phone = phone.strip()
    issue = issue.strip()
    resolved_category = classify_support_issue_category(category, issue)
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

    try:
        stored_attachments = await store_support_attachments(case_id, attachments)
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
            "ISSUE_TYPE": resolved_category,
            "PRIORITY": "P2",
            "STATUS": "open",
            "SOURCE": "contact_form",
            "COMPLETED_STEPS": [],
            "ATTACHMENTS": stored_attachments,
            "CREATE_DATE": now,
            "UPDATED_AT": now,
        }
        db.collection("support_cases").document(case_id).set(case_data)
        return {
            "status": "success",
            "case_id": case_id,
            "category": resolved_category,
            "message": "ส่งข้อความให้เจ้าหน้าที่แล้ว",
            "linked_to_account": bool(current_user),
            "attachment_count": len(stored_attachments),
            "support_info": get_support_info(),
        }
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to submit public contact case")
        raise HTTPException(status_code=500, detail="ไม่สามารถส่งข้อความได้ กรุณาลองใหม่") from error
    finally:
        for upload in attachments:
            await upload.close()


@app.get("/api/admin/dashboard/summary")
def get_admin_dashboard_summary(
    current_user: Dict[str, Any] = Depends(require_admin_or_manager),
):
    cases_query = db.collection("support_cases")
    if current_user["ROLE"] == "branch_manager":
        cases_query = cases_query.where("TENANT_ID", "==", current_user.get("TENANT_ID"))
    cases = [(doc.id, doc.to_dict() or {}) for doc in cases_query.stream()]
    status_counts = {"open": 0, "in_progress": 0, "resolved": 0, "closed": 0}
    category_counts: Dict[str, int] = {}
    daily_counts: Dict[str, int] = {}
    today = datetime.now(timezone.utc).date()
    week_days = [(today - timedelta(days=offset)) for offset in range(6, -1, -1)]
    for day in week_days:
        daily_counts[day.isoformat()] = 0

    def case_date(value):
        if hasattr(value, "to_datetime"):
            value = value.to_datetime()
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return None

    recent = []
    urgent_count = 0
    attachment_count = 0
    rating_sum = 0
    rating_count = 0
    for case_id, data in cases:
        feedback = data.get("CUSTOMER_FEEDBACK")
        if isinstance(feedback, dict):
            try:
                rating = int(feedback.get("RATING", 0))
            except (TypeError, ValueError):
                rating = 0
            if 1 <= rating <= 5:
                rating_sum += rating
                rating_count += 1
        status = str(data.get("STATUS") or "open").strip().lower()
        if status not in status_counts:
            status = "open"
        status_counts[status] = status_counts.get(status, 0) + 1
        if str(data.get("PRIORITY", "")).upper() in {"P1", "URGENT", "HIGH"}:
            urgent_count += 1
        issue = str(data.get("ISSUE") or data.get("CONTENT") or "")
        category = classify_support_issue_category(data.get("ISSUE_TYPE") or data.get("CATEGORY"), issue)
        category_counts[category] = category_counts.get(category, 0) + 1
        attachments = data.get("ATTACHMENTS") or []
        if not isinstance(attachments, list):
            attachments = []
        attachment_count += len(attachments)
        created = case_date(data.get("CREATE_DATE") or data.get("CREATED_AT"))
        updated = case_date(data.get("UPDATED_AT") or data.get("CREATE_DATE") or data.get("CREATED_AT"))
        if created and (today - timedelta(days=6)) <= created.date() <= today:
            key = created.date().isoformat()
            daily_counts[key] = daily_counts.get(key, 0) + 1
        recent.append({
            "case_id": case_id,
            "user_id": str(data.get("USER_ID", "")),
            "issue": issue,
            "category": category,
            "status": status,
            "priority": str(data.get("PRIORITY", "P2")),
            "tenant_id": str(data.get("TENANT_ID", "")),
            "branch": str(data.get("BRANCH", "")),
            "camera_name": str(data.get("CAMERA_NAME", "")),
            "admin_note": str(data.get("ADMIN_NOTE", "")),
            "contact_name": str(data.get("CONTACT_NAME", "")),
            "contact_phone": str(data.get("CONTACT_PHONE", "")),
            "created_at": created.isoformat() if created else None,
            "updated_at": updated.isoformat() if updated else None,
            "attachments": [{
                "attachment_id": item.get("ATTACHMENT_ID"),
                "file_name": item.get("FILE_NAME", "ไฟล์แนบ"),
                "content_type": item.get("CONTENT_TYPE", ""),
                "size_bytes": int(item.get("SIZE_BYTES") or 0),
            } for item in attachments if isinstance(item, dict)],
        })
    recent.sort(key=lambda item: item["created_at"] or "", reverse=True)
    return {
        "status": "success",
        "summary": {
            "total": len(cases),
            "open": status_counts["open"],
            "in_progress": status_counts["in_progress"],
            "resolved": status_counts["resolved"] + status_counts["closed"],
            "urgent": urgent_count,
            "attachments": attachment_count,
            "satisfaction_average": round(rating_sum / rating_count, 1) if rating_count else None,
            "satisfaction_count": rating_count,
        },
        "status_counts": status_counts,
        "categories": [{"name": key, "count": value} for key, value in sorted(category_counts.items(), key=lambda pair: pair[1], reverse=True)],
        "last_7_days": [{"date": key, "count": value} for key, value in daily_counts.items()],
        "recent_cases": recent[:12],
        "cases": recent,
    }


@app.get("/api/admin/support-cases/{case_id}/attachments/{attachment_id}")
def get_support_case_attachment(
    case_id: str,
    attachment_id: str,
    current_user: Dict[str, Any] = Depends(require_admin_or_manager),
):
    case_snapshot = db.collection("support_cases").document(case_id).get()
    if not case_snapshot.exists:
        raise HTTPException(status_code=404, detail="ไม่พบเคสนี้")
    case_data = case_snapshot.to_dict() or {}
    if current_user["ROLE"] == "branch_manager" and case_data.get("TENANT_ID") != current_user.get("TENANT_ID"):
        raise HTTPException(status_code=403, detail="ดูไฟล์ได้เฉพาะเคสใน Tenant ของตนเอง")
    attachment = next((item for item in case_data.get("ATTACHMENTS", []) if isinstance(item, dict) and item.get("ATTACHMENT_ID") == attachment_id), None)
    if not attachment:
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์แนบนี้")
    try:
        content_type = attachment.get("CONTENT_TYPE", "application/octet-stream")
        if content_type not in SUPPORT_ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=415, detail="ชนิดไฟล์นี้ไม่รองรับ")
        content = support_media_bucket().blob(attachment["OBJECT_NAME"]).download_as_bytes()
        filename = "".join(char for char in str(attachment.get("FILE_NAME", "attachment")) if char.isalnum() or char in "._-")[:100] or "attachment"
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": inline_content_disposition(filename),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to fetch private support attachment")
        raise HTTPException(status_code=503, detail="เปิดไฟล์แนบไม่สำเร็จ") from error


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
        resolved_category = classify_support_issue_category(request.category, issue)
        case_data = {
            "CASE_ID": case_id,
            "USER_ID": user_id,
            "TENANT_ID": tenant_id,
            "CONVERSATION_ID": conversation_id,
            "BRANCH": user_data.get("BRANCH_NAME", ""),
            "CAMERA_NAME": request.camera_name.strip(),
            "CAMERA_STATUS": "unknown",
            "ISSUE": issue,
            "ISSUE_TYPE": resolved_category,
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
            "category": resolved_category,
            "message": "ส่งเรื่องให้เจ้าหน้าที่สำเร็จ",
        }
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to submit customer support case")
        raise HTTPException(status_code=500, detail="ไม่สามารถส่งเรื่องให้เจ้าหน้าที่ได้ กรุณาลองใหม่") from error


@app.get("/api/customer/dashboard/summary")
def get_customer_dashboard_summary(current_user: Dict[str, Any] = Depends(require_current_user)):
    user_id = str(current_user.get("USER_ID") or "").strip()
    tenant_id = str(current_user.get("TENANT_ID") or "").strip()
    if not user_id or not tenant_id:
        raise HTTPException(status_code=401, detail="กรุณาเข้าสู่ระบบใหม่")
    try:
        user_doc = next(iter(db.collection("users").where("USER_ID", "==", user_id).limit(1).stream()), None)
        if user_doc is None:
            user_doc = db.collection("users").document(user_id).get()
        user_data = user_doc.to_dict() or {} if user_doc and user_doc.exists else {}
        counts = {"open": 0, "in_progress": 0, "resolved": 0, "closed": 0}
        recent, file_count = [], 0
        def iso(value):
            if hasattr(value, "to_datetime"):
                value = value.to_datetime()
            return value.isoformat() if hasattr(value, "isoformat") else None
        query = db.collection("support_cases").where("USER_ID", "==", user_id).stream()
        for doc in query:
            data = doc.to_dict() or {}
            if str(data.get("TENANT_ID") or "").strip() != tenant_id:
                continue
            status = str(data.get("STATUS") or "open").lower()
            if status not in counts:
                status = "open"
            counts[status] += 1
            raw_files = data.get("ATTACHMENTS") or []
            files = [{
                "attachment_id": str(item.get("ATTACHMENT_ID")),
                "file_name": str(item.get("FILE_NAME") or "ไฟล์แนบ"),
                "content_type": str(item.get("CONTENT_TYPE") or ""),
            } for item in raw_files if isinstance(item, dict) and item.get("ATTACHMENT_ID")] if isinstance(raw_files, list) else []
            file_count += len(files)
            created = iso(data.get("CREATE_DATE") or data.get("CREATED_AT"))
            updated = iso(data.get("UPDATED_AT") or data.get("CREATE_DATE") or data.get("CREATED_AT"))
            issue_text = str(data.get("ISSUE") or data.get("CONTENT") or "")
            category = classify_support_issue_category(data.get("ISSUE_TYPE") or data.get("CATEGORY"), issue_text)
            recent.append({
                "case_id": doc.id, "issue": issue_text,
                "category": category,
                "status": status, "priority": str(data.get("PRIORITY") or "P2"),
                "camera_name": str(data.get("CAMERA_NAME") or ""),
                "branch": str(data.get("BRANCH") or user_data.get("BRANCH_NAME") or ""),
                "created_at": created, "updated_at": updated, "attachments": files,
            })
        recent.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return {
            "status": "success",
            "support_info": get_support_info(),
            "profile": {"display_name": str(user_data.get("USERNAME") or user_id),
                        "branch": str(user_data.get("BRANCH_NAME") or ""),
                        "tenant_name": str(user_data.get("TENANT_NAME") or "")},
            "summary": {"total": sum(counts.values()), "open": counts["open"],
                        "in_progress": counts["in_progress"],
                        "resolved": counts["resolved"] + counts["closed"],
                        "attachments": file_count},
            "recent_cases": recent[:50],
        }
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to load customer dashboard")
        raise HTTPException(status_code=500, detail="โหลดภาพรวมของคุณไม่สำเร็จ") from error


@app.get("/api/customer/support-cases/{case_id}")
def get_customer_support_case_detail(
    case_id: str,
    current_user: Dict[str, Any] = Depends(require_current_user),
):
    case_snapshot = db.collection("support_cases").document(case_id).get()
    if not case_snapshot.exists:
        raise HTTPException(status_code=404, detail="ไม่พบเคสนี้")
    data = case_snapshot.to_dict() or {}
    if (str(data.get("USER_ID") or "").strip() != str(current_user.get("USER_ID") or "").strip()
        or str(data.get("TENANT_ID") or "").strip() != str(current_user.get("TENANT_ID") or "").strip()):
        raise HTTPException(status_code=404, detail="ไม่พบเคสนี้")

    def customer_case_iso(value):
        if hasattr(value, "to_datetime"):
            value = value.to_datetime()
        return value.isoformat() if hasattr(value, "isoformat") else None

    status = str(data.get("STATUS") or "open").lower()
    created_at = customer_case_iso(data.get("CREATE_DATE") or data.get("CREATED_AT"))
    updated_at = customer_case_iso(data.get("UPDATED_AT") or data.get("CREATE_DATE") or data.get("CREATED_AT"))
    events = [{
        "event_id": "created",
        "event_type": "created",
        "sender": "system",
        "message": "ส่งเคสเข้าระบบแล้ว",
        "status": "open",
        "created_at": created_at,
    }]
    for event in data.get("CASE_UPDATES") or []:
        if not isinstance(event, dict):
            continue
        events.append({
            "event_id": str(event.get("EVENT_ID") or ""),
            "event_type": str(event.get("EVENT_TYPE") or "message"),
            "sender": str(event.get("SENDER") or "support"),
            "message": str(event.get("MESSAGE") or ""),
            "status": str(event.get("STATUS") or ""),
            "created_at": customer_case_iso(event.get("CREATE_DATE")),
        })
    if status != "open" and not any(item.get("event_type") == "status" and item.get("status") == status for item in events):
        events.append({
            "event_id": "current-status",
            "event_type": "status",
            "sender": "support",
            "message": "สถานะปัจจุบัน: " + {
                "in_progress": "กำลังดำเนินการ",
                "resolved": "แก้ไขแล้ว",
                "closed": "ปิดเคสแล้ว",
            }.get(status, status),
            "status": status,
            "created_at": updated_at,
        })
    events.sort(key=lambda item: item.get("created_at") or "")
    attachments = [{
        "attachment_id": str(item.get("ATTACHMENT_ID")),
        "file_name": str(item.get("FILE_NAME") or "ไฟล์แนบ"),
        "content_type": str(item.get("CONTENT_TYPE") or ""),
    } for item in (data.get("ATTACHMENTS") or [])
      if isinstance(item, dict) and item.get("ATTACHMENT_ID")]
    issue_text = str(data.get("ISSUE") or data.get("CONTENT") or "")
    category = classify_support_issue_category(data.get("ISSUE_TYPE") or data.get("CATEGORY"), issue_text)
    return {
        "status": "success",
        "case": {
            "case_id": case_snapshot.id,
            "issue": issue_text,
            "category": category,
            "status": status,
            "priority": str(data.get("PRIORITY") or "P2"),
            "branch": str(data.get("BRANCH") or ""),
            "camera_name": str(data.get("CAMERA_NAME") or ""),
            "created_at": created_at,
            "updated_at": updated_at,
            "attachments": attachments,
            "feedback": ({
                "rating": int(data.get("CUSTOMER_FEEDBACK", {}).get("RATING", 0)),
                "comment": str(data.get("CUSTOMER_FEEDBACK", {}).get("COMMENT") or ""),
                "created_at": customer_case_iso(data.get("CUSTOMER_FEEDBACK", {}).get("CREATE_DATE")),
            } if isinstance(data.get("CUSTOMER_FEEDBACK"), dict) else None),
        },
        "events": events,
    }


@app.post("/api/customer/support-cases/{case_id}/replies", status_code=201)
def reply_to_customer_support_case(
    case_id: str,
    request: CustomerCaseReplyRequest,
    current_user: Dict[str, Any] = Depends(require_current_user),
):
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="กรุณาพิมพ์ข้อความก่อนส่ง")
    case_ref = db.collection("support_cases").document(case_id)
    case_snapshot = case_ref.get()
    if not case_snapshot.exists:
        raise HTTPException(status_code=404, detail="ไม่พบเคสนี้")
    data = case_snapshot.to_dict() or {}
    if (str(data.get("USER_ID") or "").strip() != str(current_user.get("USER_ID") or "").strip()
        or str(data.get("TENANT_ID") or "").strip() != str(current_user.get("TENANT_ID") or "").strip()):
        raise HTTPException(status_code=404, detail="ไม่พบเคสนี้")
    if str(data.get("STATUS") or "open").lower() == "closed":
        raise HTTPException(status_code=409, detail="เคสนี้ปิดแล้ว กรุณาเปิดเคสใหม่หากยังต้องการความช่วยเหลือ")
    now = datetime.now(timezone.utc)
    case_ref.update({
        "CASE_UPDATES": firestore.ArrayUnion([{
            "EVENT_ID": secrets.token_hex(8),
            "EVENT_TYPE": "message",
            "SENDER": "customer",
            "MESSAGE": message,
            "CREATE_DATE": now,
        }]),
        "UPDATED_AT": now,
        "LAST_CUSTOMER_MESSAGE_AT": now,
    })
    return {"status": "success", "case_id": case_id, "message": "ส่งข้อความเพิ่มเติมให้เจ้าหน้าที่แล้ว"}


@app.post("/api/customer/support-cases/{case_id}/feedback", status_code=201)
def submit_customer_case_feedback(
    case_id: str,
    request: CustomerCaseFeedbackRequest,
    current_user: Dict[str, Any] = Depends(require_current_user),
):
    case_ref = db.collection("support_cases").document(case_id)
    transaction = db.transaction()
    now = datetime.now(timezone.utc)
    feedback = {
        "RATING": request.rating,
        "COMMENT": request.comment.strip(),
        "USER_ID": str(current_user.get("USER_ID") or ""),
        "CREATE_DATE": now,
    }

    @firestore.transactional
    def save_feedback(tx):
        case_snapshot = case_ref.get(transaction=tx)
        if not case_snapshot.exists:
            raise HTTPException(status_code=404, detail="ไม่พบเคสนี้")
        case_data = case_snapshot.to_dict() or {}
        if (str(case_data.get("USER_ID") or "").strip() != str(current_user.get("USER_ID") or "").strip()
            or str(case_data.get("TENANT_ID") or "").strip() != str(current_user.get("TENANT_ID") or "").strip()):
            raise HTTPException(status_code=404, detail="ไม่พบเคสนี้")
        if str(case_data.get("STATUS") or "open").lower() not in {"resolved", "closed"}:
            raise HTTPException(status_code=409, detail="ประเมินได้เมื่อเคสแก้ไขแล้วหรือปิดเคส")
        if isinstance(case_data.get("CUSTOMER_FEEDBACK"), dict):
            raise HTTPException(status_code=409, detail="เคสนี้ได้รับการประเมินแล้ว")
        tx.update(case_ref, {
            "CUSTOMER_FEEDBACK": feedback,
            "CASE_UPDATES": firestore.ArrayUnion([{
                "EVENT_ID": secrets.token_hex(8),
                "EVENT_TYPE": "feedback",
                "SENDER": "customer",
                "MESSAGE": f"ลูกค้าประเมินการช่วยเหลือ {request.rating}/5 ดาว"
                    + (f": {request.comment.strip()}" if request.comment.strip() else ""),
                "CREATE_DATE": now,
            }]),
        })

    try:
        save_feedback(transaction)
        return {"status": "success", "case_id": case_id, "message": "ขอบคุณสำหรับการประเมิน"}
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to save customer case feedback")
        raise HTTPException(status_code=500, detail="บันทึกการประเมินไม่สำเร็จ") from error


@app.get("/api/customer/support-cases/{case_id}/attachments/{attachment_id}")
def get_customer_case_attachment(case_id: str, attachment_id: str,
        current_user: Dict[str, Any] = Depends(require_current_user)):
    case_doc = db.collection("support_cases").document(case_id).get()
    if not case_doc.exists:
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์แนบนี้")
    data = case_doc.to_dict() or {}
    if (str(data.get("USER_ID") or "").strip() != str(current_user.get("USER_ID") or "").strip()
        or str(data.get("TENANT_ID") or "").strip() != str(current_user.get("TENANT_ID") or "").strip()):
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์แนบนี้")
    item = next((x for x in data.get("ATTACHMENTS", [])
                 if isinstance(x, dict) and x.get("ATTACHMENT_ID") == attachment_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์แนบนี้")
    try:
        media_type = item.get("CONTENT_TYPE", "application/octet-stream")
        if media_type not in SUPPORT_ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=415, detail="ชนิดไฟล์นี้ไม่รองรับ")
        content = support_media_bucket().blob(item["OBJECT_NAME"]).download_as_bytes()
        filename = "".join(c for c in str(item.get("FILE_NAME", "attachment")) if c.isalnum() or c in "._-")[:100] or "attachment"
        return Response(content=content, media_type=media_type, headers={
            "Content-Disposition": inline_content_disposition(filename),
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
    except HTTPException:
        raise
    except Exception as error:
        logging.exception("Failed to load customer attachment")
        raise HTTPException(status_code=503, detail="เปิดไฟล์แนบไม่สำเร็จ") from error


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


# API ให้เจ้าหน้าที่ดูบทสนทนาและตอบกลับ Support Case
@app.get("/api/admin/support-cases/{case_id}")
def get_admin_support_case_detail(
    case_id: str,
    current_user: Dict[str, Any] = Depends(require_admin_or_manager),
):
    case_snapshot = db.collection("support_cases").document(case_id).get()
    if not case_snapshot.exists:
        raise HTTPException(status_code=404, detail="ไม่พบ Support Case นี้")
    data = case_snapshot.to_dict() or {}
    if current_user["ROLE"] == "branch_manager" and data.get("TENANT_ID") != current_user.get("TENANT_ID"):
        raise HTTPException(status_code=403, detail="ดูได้เฉพาะเคสใน Tenant ของตนเอง")

    def staff_case_iso(value):
        if hasattr(value, "to_datetime"):
            value = value.to_datetime()
        return value.isoformat() if hasattr(value, "isoformat") else None

    issue_text = str(data.get("ISSUE") or data.get("CONTENT") or "")
    category = classify_support_issue_category(data.get("ISSUE_TYPE") or data.get("CATEGORY"), issue_text)
    return {
        "status": "success",
        "case": {
            "case_id": case_snapshot.id,
            "user_id": str(data.get("USER_ID") or ""),
            "tenant_id": str(data.get("TENANT_ID") or ""),
            "issue": issue_text,
            "category": category,
            "status": str(data.get("STATUS") or "open").strip().lower(),
            "priority": str(data.get("PRIORITY") or "P2"),
            "branch": str(data.get("BRANCH") or ""),
            "camera_name": str(data.get("CAMERA_NAME") or ""),
            "admin_note": str(data.get("ADMIN_NOTE") or ""),
            "contact_name": str(data.get("CONTACT_NAME") or ""),
            "contact_phone": str(data.get("CONTACT_PHONE") or ""),
            "created_at": staff_case_iso(data.get("CREATE_DATE") or data.get("CREATED_AT")),
            "updated_at": staff_case_iso(data.get("UPDATED_AT") or data.get("CREATE_DATE")),
            "customer_feedback": ({
                "rating": int(data.get("CUSTOMER_FEEDBACK", {}).get("RATING", 0)),
                "comment": str(data.get("CUSTOMER_FEEDBACK", {}).get("COMMENT") or ""),
                "created_at": staff_case_iso(data.get("CUSTOMER_FEEDBACK", {}).get("CREATE_DATE")),
            } if isinstance(data.get("CUSTOMER_FEEDBACK"), dict) else None),
        },
        "events": [{
            "event_id": str(event.get("EVENT_ID") or ""),
            "event_type": str(event.get("EVENT_TYPE") or "message"),
            "sender": str(event.get("SENDER") or "support"),
            "message": str(event.get("MESSAGE") or ""),
            "status": str(event.get("STATUS") or ""),
            "created_at": staff_case_iso(event.get("CREATE_DATE")),
        } for event in (data.get("CASE_UPDATES") or []) if isinstance(event, dict)],
    }


@app.post("/api/admin/support-cases/{case_id}/replies", status_code=201)
def reply_to_support_case(
    case_id: str,
    request: CustomerCaseReplyRequest,
    current_user: Dict[str, Any] = Depends(require_admin_or_manager),
):
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="กรุณาพิมพ์ข้อความก่อนส่ง")
    case_ref = db.collection("support_cases").document(case_id)
    case_snapshot = case_ref.get()
    if not case_snapshot.exists:
        raise HTTPException(status_code=404, detail="ไม่พบ Support Case นี้")
    data = case_snapshot.to_dict() or {}
    if current_user["ROLE"] == "branch_manager" and data.get("TENANT_ID") != current_user.get("TENANT_ID"):
        raise HTTPException(status_code=403, detail="ตอบกลับได้เฉพาะเคสใน Tenant ของตนเอง")
    if str(data.get("STATUS") or "open").lower() == "closed":
        raise HTTPException(status_code=409, detail="เคสปิดแล้ว ไม่สามารถตอบกลับได้")
    now = datetime.now(timezone.utc)
    reply_event = {
        "EVENT_ID": secrets.token_hex(8),
        "EVENT_TYPE": "message",
        "SENDER": "support",
        "MESSAGE": message,
        "CREATE_DATE": now,
    }
    existing_updates = data.get("CASE_UPDATES")
    case_updates_value = (
        [reply_event]
        if existing_updates is not None and not isinstance(existing_updates, list)
        else firestore.ArrayUnion([reply_event])
    )
    case_ref.update({
        "CASE_UPDATES": case_updates_value,
        "UPDATED_AT": now,
        "LAST_SUPPORT_MESSAGE_AT": now,
        "UPDATED_BY": current_user["USER_ID"],
    })
    line_notification = "not_linked"
    try:
        user_id = str(data.get("USER_ID") or "").strip()
        user_snapshot = next(iter(db.collection("users").where("USER_ID", "==", user_id).limit(1).stream()), None)
        if user_snapshot is None and user_id:
            fallback = db.collection("users").document(user_id).get()
            user_snapshot = fallback if fallback.exists else None
        user_data = user_snapshot.to_dict() or {} if user_snapshot else {}
        if user_data.get("TENANT_ID") == data.get("TENANT_ID"):
            line_user_id = str(user_data.get("LINE_USER_ID") or "").strip()
            if line_user_id:
                text = f"TATIP AI มีข้อความตอบกลับจากเจ้าหน้าที่ในเคส {case_id} กรุณาเปิดหน้า Dashboard เพื่อติดตามรายละเอียด"
                line_notification = "sent" if send_line_push_message(line_user_id, text) else "failed"
    except Exception:
        logging.exception("Could not send LINE notification for support reply")
        line_notification = "failed"
    return {
        "status": "success",
        "case_id": case_id,
        "line_notification": line_notification,
        "message": "ส่งข้อความตอบกลับแล้ว",
    }


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

        previous_status = str(case_data.get("STATUS") or "open").strip().lower()
        now = datetime.now(timezone.utc)
        updates = {
            "STATUS": request.status,
            "UPDATED_AT": now,
            "UPDATED_BY": current_user["USER_ID"],
        }
        if request.admin_note is not None:
            updates["ADMIN_NOTE"] = request.admin_note.strip()
        if request.category is not None and request.category.strip():
            updates["ISSUE_TYPE"] = classify_support_issue_category(request.category, str(case_data.get("ISSUE") or ""))
        if request.status in {"resolved", "closed"}:
            updates["RESOLVED_AT"] = now
        else:
            updates["RESOLVED_AT"] = firestore.DELETE_FIELD

        if previous_status != request.status:
            status_labels = {
                "open": "เคสได้รับการอัปเดต: รอดำเนินการ",
                "in_progress": "เจ้าหน้าที่กำลังดำเนินการ",
                "resolved": "เจ้าหน้าที่แจ้งว่าแก้ไขแล้ว",
                "closed": "เคสถูกปิดแล้ว",
            }
            status_event = {
                "EVENT_ID": secrets.token_hex(8),
                "EVENT_TYPE": "status",
                "SENDER": "support",
                "STATUS": request.status,
                "MESSAGE": status_labels[request.status],
                "CREATE_DATE": now,
            }
            existing_updates = case_data.get("CASE_UPDATES")
            if existing_updates is not None and not isinstance(existing_updates, list):
                updates["CASE_UPDATES"] = [status_event]
            else:
                updates["CASE_UPDATES"] = firestore.ArrayUnion([status_event])
        case_ref.update(updates)
        line_notification = "unchanged"
        if previous_status != request.status:
            line_notification = notify_support_case_status(case_data, case_id, request.status)
        final_category = updates.get("ISSUE_TYPE") or classify_support_issue_category(
            case_data.get("ISSUE_TYPE") or case_data.get("CATEGORY"),
            str(case_data.get("ISSUE") or ""),
        )
        return {
            "status": "success",
            "case_id": case_id,
            "new_status": request.status,
            "category": final_category,
            "admin_note": updates.get("ADMIN_NOTE", str(case_data.get("ADMIN_NOTE") or "")),
            "updated_at": now.isoformat(),
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
            "troubleshooting_state",
            "camera_layouts",
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


