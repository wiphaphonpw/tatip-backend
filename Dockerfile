# ใช้ Python 3.10 ตัวบางเบา
FROM python:3.10-slim

# กำหนด Working Directory
WORKDIR /app

# คัดลอกไฟล์ requirements.txt และติดตั้งแพ็กเกจ
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# คัดลอกโค้ดทั้งหมดเข้ามา
COPY . .

# รัน FastAPI ด้วย Uvicorn (เปิดรับพอร์ต 8080 ตามที่ Cloud Run กำหนด)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]