from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import vertexai
from vertexai.preview import rag
from vertexai.generative_models import GenerativeModel, Tool

# 1. กำหนดค่าโปรเจกต์และคลังข้อมูล
PROJECT_ID = "tatip-ai-rag"
LOCATION = "us-central1" # แนะนำเปลี่ยนเป็น us-central1 เพื่อให้รองรับโมเดล Gemini ทุกรุ่นอย่างไร้ปัญหา
CORPUS_NAME = "projects/tatip-ai-rag/locations/asia-southeast1/ragCorpora/2305843009213693952"

# 2. เริ่มต้นการเชื่อมต่อ Vertex AI
vertexai.init(project=PROJECT_ID, location=LOCATION)

app = FastAPI()

# อนุญาตให้หน้าเว็บ (Frontend) เรียกใช้งาน API นี้ได้
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# โครงสร้างข้อมูลที่รับจากหน้าเว็บ
class ChatRequest(BaseModel):
    message: str

# 3. สร้าง Endpoint สำหรับรับคำถามและตอบกลับ
@app.post("/api/chat")
async def chat_with_tatip(request: ChatRequest):
    try:
        # กำหนดคำสั่งพฤติกรรมของ AI (System Instruction) เพื่อให้ตอบละเอียดยิ่งขึ้น
        system_instruction = (
            "คุณคือ TATIP AI ผู้ช่วยอัจฉริยะสำหรับซัพพอร์ตระบบกล้องวงจรปิด CCTV และ Dashboard "
            "หน้าที่ของคุณคือตอบคำถามลูกค้าโดยอ้างอิงจากข้อมูลใน RAG Corpus "
            "ข้อกำหนดสำคัญ: "
            "1. ห้ามตอบสั้นห้วนจนเกินไป "
            "2. ให้เขียนอธิบายขั้นตอน วิธีการตรวจสอบ หรือวิธีแก้ไขปัญหาเป็นข้อๆ (Step-by-step) ให้ละเอียดชัดเจน "
            "3. หากมีคำแนะนำเพิ่มเติมที่เป็นประโยชน์ ให้ระบุเพิ่มเข้ามาด้วยเสมอเพื่อช่วยให้ลูกค้าแก้ปัญหาได้จริง"
        )

        # เลือกโมเดลและใส่ system_instruction
        model = GenerativeModel(
            "gemini-2.5-flash-lite", # หรือชื่อโมเดลที่คุณใช้งานอยู่
            system_instruction=system_instruction
        )
        
        # ตั้งค่าให้ดึงข้อมูลจาก RAG Corpus ของเรา
        rag_retrieval_tool = Tool.from_retrieval(
            retrieval=rag.Retrieval(
                source=rag.VertexRagStore(
                    rag_resources=[rag.RagResource(rag_corpus=CORPUS_NAME)],
                    similarity_top_k=3,
                ),
            )
        )
        
        # ส่งคำถามไปให้ Gemini พร้อมแนบคลังข้อมูลคู่มือไปให้ด้วย
        response = model.generate_content(
            request.message,
            tools=[rag_retrieval_tool],
        )
        
        return {"status": "success", "reply": response.text}
        
    except Exception as e:
        return {"status": "error", "reply": str(e)}

# หน้าทดสอบว่าเซิร์ฟเวอร์ทำงานไหม
@app.get("/")
def read_root():
    return {"message": "TATIP AI Backend is running!"}