FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf \
    HF_HUB_DISABLE_TELEMETRY=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libglib2.0-0 \
    libgl1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY constraints-yomitoku-cpu.txt /tmp/constraints-yomitoku-cpu.txt
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -c /tmp/constraints-yomitoku-cpu.txt \
    -r requirements.txt

COPY scripts/check_no_gpu_packages.py /tmp/check_no_gpu_packages.py
RUN python /tmp/check_no_gpu_packages.py

COPY scripts/prewarm_yomitoku_models.py /tmp/prewarm_yomitoku_models.py
RUN mkdir -p "${HF_HOME}" \
    && python /tmp/prewarm_yomitoku_models.py

COPY src ./src
COPY migrations ./migrations
COPY ocr_pipeline ./ocr_pipeline
COPY tmp ./tmp
COPY pyproject.toml .

# Trust Cloud Run's reverse proxy headers so request.url matches the public HTTPS URL.
# This is required for verifying Cloud Scheduler OIDC tokens whose audience is the target URL.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
