# ใช้ Python Image พื้นฐาน
FROM python:3.10-slim

# กำหนด Working Directory
WORKDIR /app

# คัดลอกไฟล์ requirements.txt มาติดตั้ง
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# คัดลอกโค้ดทั้งหมดเข้าไปใน Container
COPY . .

# รันแอปพลิเคชันด้วย Uvicorn (กำหนดพอร์ตตามที่ Cloud Run กำหนดผ่านแวดล้อม $PORT)
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}