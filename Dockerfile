FROM python:3.12-slim

WORKDIR /app

# 安装 Python 依赖（去掉 easyocr，用百度 OCR 不需要它）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y easyocr

# 复制应用代码
COPY main.py nutrition.json ./

# 云托管默认监听 80 端口
EXPOSE 80

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
