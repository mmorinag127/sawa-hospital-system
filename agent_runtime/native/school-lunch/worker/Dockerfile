FROM python:3.12-slim-trixie

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge '20250419+deb13u1' \
    && rm -rf /var/lib/apt/lists/*
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
RUN python -m pip install --no-cache-dir --upgrade --no-deps 'pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70' 'certifi @ https://files.pythonhosted.org/packages/c4/51/29046e48f693c54e5b7412216d447a1d8362c9653148d98c02cdf65fa733/certifi-2026.10.19-py3-none-any.whl#sha256=42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b'

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PYTHONPATH=/app/backend:/app/worker:/app/ocr_pipeline

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY worker/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/src ./backend/src
COPY worker/service ./worker/service
COPY ocr_pipeline ./ocr_pipeline
COPY worker/scripts ./worker/scripts

RUN python -c "from app.yomitoku_runner import run_yomitoku; from yomitoku import DocumentAnalyzer; assert callable(run_yomitoku); assert DocumentAnalyzer.__name__"

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8080"]
