FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY constraints-yomitoku-cpu.txt /tmp/constraints-yomitoku-cpu.txt
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -c /tmp/constraints-yomitoku-cpu.txt \
    -r requirements.txt

COPY scripts/check_no_gpu_packages.py /tmp/check_no_gpu_packages.py
RUN python /tmp/check_no_gpu_packages.py

ENV HF_HOME=/opt/hf
ENV HF_HUB_DISABLE_TELEMETRY=1
COPY scripts/prewarm_yomitoku_models.py /tmp/prewarm_yomitoku_models.py
RUN mkdir -p "${HF_HOME}" \
  && python /tmp/prewarm_yomitoku_models.py

COPY app/ ./app
COPY src/data ./src/data

ENV PORT=8080
ENV GUNICORN_TIMEOUT=3600
CMD ["sh", "-c", "gunicorn -b :8080 -w 1 -t ${GUNICORN_TIMEOUT} app.main:app"]
