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
    msmtp \
    msmtp-mta \
    ca-certificates \
    curl \
    bc \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# ImageMagick: allow read/write of TIF files in /tmp (default policy is too restrictive)
RUN sed -i 's|<policy domain="coder" rights="none" pattern="TIFF"|<policy domain="coder" rights="read|write" pattern="TIFF"|g' \
        /etc/ImageMagick-6/policy.xml 2>/dev/null || true

WORKDIR /ocr-api
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt
COPY app/ /ocr-api/app/

RUN addgroup --system ocr && adduser --system --ingroup ocr --home /ocr-api ocr \
    && mkdir -p /ocr-api/.config/msmtp /ocr-api/.config/rclone \
    && chown -R ocr:ocr /ocr-api
USER ocr

COPY --chown=ocr:ocr entrypoint.sh /ocr-api/entrypoint.sh
RUN chmod +x /ocr-api/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/ocr-api/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
