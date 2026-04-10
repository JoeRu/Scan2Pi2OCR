FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
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

RUN addgroup --system ocr && adduser --system --ingroup ocr --home /ocr-api ocr \
    && chown -R ocr:ocr /ocr-api
USER ocr

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
