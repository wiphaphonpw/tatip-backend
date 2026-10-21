from fastapi import FastAPI, Request, HTTPException
import hashlib
import hmac
import base64
from datetime import datetime
from typing import Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import vertexai
from vertexai.preview import rag
from vertexai.generative_models import GenerativeModel, Tool
from google.cloud import firestore

# 1. กำหนดค่าโปรเจกต์และคลังข้อมูล
PROJECT_ID = "tatip-ai-rag"
LOCATION = "us-central1"
CORPUS_NAME = "projects/tatip-ai-rag/locations/asia-southeast1/ragCorpora/2305843009213693952"

# 2. เริ่มต้นการเชื่อมต่อ Vertex AI และ Firestore
vertexai.init(project=PROJECT_ID, location=LOCATION)
db = firestore.Client(project=PROJECT_ID, database="customers")

app = FastAPI()

# อนุญาตให้หน้าเว็บ (Frontend) เรียกใช้งาน API นี้ได้
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# Pydantic Models (ประกาศไว้ด้านบนสุดทั้งหมด)
# ==========================================
class ChatRequest(BaseModel):
    message: str

class CreateUserRequest(BaseModel):
    user_id: str
    username: str
    password: str
    tenant_id: str
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
# API Endpoints
# ==========================================

# หน้าทดสอบว่าเซิร์ฟเวอร์ทำงานไหม
@app.get("/")
def read_root():
    return {"message": "TATIP AI Backend is running!"}

# 3. API สำหรับรับคำถามและตอบกลับ (RAG Chat)
@app.post("/api/chat")
async def chat_with_tatip(request: ChatRequest):
    try:
        system_instruction = (
            "คุณคือ TATIP AI ผู้ช่วยอัจฉริยะสำหรับซัพพอร์ตระบบกล้องวงจรปิด CCTV และ Dashboard "
            "หน้าที่ของคุณคือตอบคำถามลูกค้าโดยอ้างอิงจากข้อมูลใน RAG Corpus "
            "ข้อกำหนดสำคัญ: "
            "1. ห้ามตอบสั้นห้วนจนเกินไป "
            "2. ให้เขียนอธิบายขั้นตอน วิธีการตรวจสอบ หรือวิธีแก้ไขปัญหาเป็นข้อๆ (Step-by-step) ให้ละเอียดชัดเจน "
            "3. หากมีคำแนะนำเพิ่มเติมที่เป็นประโยชน์ ให้ระบุเพิ่มเข้ามาด้วยเสมอเพื่อช่วยให้ลูกค้าแก้ปัญหาได้จริง"
        )

        model = GenerativeModel(
            "gemini-2.5-flash-lite",
            system_instruction=system_instruction
        )
        
        rag_retrieval_tool = Tool.from_retrieval(
            retrieval=rag.Retrieval(
                source=rag.VertexRagStore(
                    rag_resources=[rag.RagResource(rag_corpus=CORPUS_NAME)],
                    similarity_top_k=3,
                ),
            )
        )
        
        response = model.generate_content(
            request.message,
            tools=[rag_retrieval_tool],
        )
        
        return {"status": "success", "reply": response.text}
        
    except Exception as e:
        return {"status": "error", "reply": str(e)}

# 4. API สำหรับดึงรายชื่อผู้ใช้จาก Collection "users" มาแสดงหน้าเว็บ
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

# 5. API สำหรับดึงข้อมูลทุก Collection แบบไดนามิกตามที่เลือกในหน้าแอดมิน
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

# 6. API สำหรับสร้าง User ใหม่
@app.post("/api/admin/create-user")
def create_customer_user(request: CreateUserRequest):
    try:
        user_ref = db.collection("users").document(request.user_id)
        user_ref.set({
            "USER_ID": request.user_id,
            "USERNAME": request.username,
            "PASSWORD": request.password,
            "TENANT_ID": request.tenant_id,
            "ROLE": request.role
        })
        return {"status": "success", "message": f"สร้างบัญชีให้กับ {request.username} สำเร็จเรียบร้อยแล้ว!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 7. API สำหรับบันทึกข้อความแชทและสร้าง conversation ลง Firestore
@app.post("/api/save-chat-history")
async def save_chat_history(user_id: str, message: str, reply: str, tenant_id: str):
    try:
        timestamp = datetime.now()
        
        message_ref = db.collection("messages").document()
        message_ref.set({
            "USER_ID": user_id,
            "MESSAGE": message,
            "REPLY": reply,
            "TENANT_ID": tenant_id,
            "CREATE_DATE": timestamp
        })

        conv_ref = db.collection("conversations").document(user_id)
        conv_ref.set({
            "USER_ID": user_id,
            "TENANT_ID": tenant_id,
            "LAST_MESSAGE": message,
            "LAST_UPDATE": timestamp
        }, merge=True)

        return {"status": "success", "message": "บันทึกข้อมูลลง Firestore สำเร็จ"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 8. API สำหรับบันทึกเคสแจ้งปัญหา (Support Cases)
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

# 9. API สำหรับเพิ่มหรืออัปเดตข้อมูลในตารางใดๆ ผ่านหน้าแอดมิน
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

# 10. API สำหรับตรวจสอบรหัสผ่าน
@app.post("/api/login")
def login_user(request: LoginRequest):
    try:
        users_ref = db.collection("users")
        docs = users_ref.where("USERNAME", "==", request.username).stream()
        
        user_found = None
        for doc in docs:
            data = doc.to_dict()
            if data.get("PASSWORD") == request.password:
                user_found = data
                break
        
        if user_found:
            return {
                "status": "success", 
                "message": "เข้าสู่ระบบสำเร็จ",
                "role": user_found.get("ROLE"),
                "tenant_id": user_found.get("TENANT_ID")
            }
        else:
            raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
            
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# สมมติว่าดึงค่า Secret จาก Secret Manager ตามหลักการ Security ในรายงาน[cite: 22]
LINE_CHANNEL_SECRET = "a0c445f0b3cda3b40c19d8c703707e18,LINE_CHANNEL_ACCESS_TOKEN=tTvVfZh6KnyG7k0320byqucPCpwNl2ktKILFgDqdMN7yyqCkMYrngPyPERa4tiZYI8a4nCOTQvoXYuzrqei4Bn5q9zzBL/N8RgA7q+9EzUd7JwMK95q3xRmTk6Jsu+ld75pkxrHWro2rzLIX7zd6kgdB04t89/1O/w1cDnyilFU=" 

@app.post("/api/line/webhook")
async def line_webhook(request: Request):
    body = await request.body()
    body_str = body.decode('utf-8')
    
    # 1. ตรวจสอบลายเซ็นดิจิทัล (Signature Verification) เพื่อความปลอดภัยตามหลัก LINE API[cite: 25]
    signature = request.headers.get('X-Line-Signature', '')
    hash = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), body, hashlib.sha256).digest()
    computed_signature = base64.b64encode(hash).decode('utf-8')
    
    if computed_signature != signature:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    data = await request.json()
    events = data.get("events", [])
    
    for event in events:
        if event["type"] == "message" and event["message"]["type"] == "text":
            line_user_id = event["source"]["userId"]  # ได้ LINE_USER_ID ของลูกค้า[cite: 37]
            user_message = event["message"]["text"]    # ข้อความที่ลูกค้าพิมพ์
            
            # 2. ค้นหาข้อมูลใน Firestore ตาราง users ว่า LINE_USER_ID นี้ผูกกับ Tenant ใด
            users_ref = db.collection("users")
            query = users_ref.where("LINE_USER_ID", "==", line_user_id).stream()
            
            tenant_id = "default_tenant"
            user_id = "guest_user"
            
            for doc in query:
                user_data = doc.to_dict()
                tenant_id = user_data.get("TENANT_ID", "default_tenant")
                user_id = user_data.get("USER_ID", "guest_user")
            
            # 3. นำข้อความไปประมวลผลต่อผ่าน RAG Engine / Orchestrator พร้อมบันทึกลงตาราง messages และ conversations[cite: 35, 38, 39]
            # (เรียกใช้งานฟังก์ชันประมวลผลต่อตาม Architecture ในรายงาน)
            
    return {"status": "success"}

    system_instruction = (
    "คุณคือ TATIP AI ผู้ช่วยอัจฉริยะสำหรับสนับสนุนการให้บริการลูกค้าของระบบ TATIP "
    "หน้าที่ของคุณคือตอบคำถามโดยอ้างอิงจากเนื้อหาในเอกสารคู่มือที่ระบบดึงมาให้เท่านั้น "
    "ห้ามใช้ความรู้ทั่วไปภายนอก ห้ามแต่งคำตอบขึ้นมาเองเด็ดขาด "
    "หากคำถามหรือเรื่องดังกล่าวไม่อยู่ในเอกสารคู่มือ ให้ตอบตามรูปแบบนี้เท่านั้น: "
    "'เรื่องนี้ยังไม่มีข้อมูลในคู่มือการใช้งาน รบกวนติดต่อทีมงาน (Admin) เพื่อสอบถามโดยตรงค่ะ'"
)