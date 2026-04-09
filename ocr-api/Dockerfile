FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-deu \
    tesseract-ocr-eng \
    tesseract-ocr-frk \
    imagemagick \
    rclone \
    mutt \
    bc \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ocr-api
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY app/ /ocr-api/app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
