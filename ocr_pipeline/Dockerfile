FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV HF_HOME=/opt/hf
ENV HF_HUB_DISABLE_TELEMETRY=1
RUN mkdir -p "${HF_HOME}" \
  && python -c "import os; os.environ['HF_HOME']=os.environ.get('HF_HOME','/opt/hf'); from yomitoku import DocumentAnalyzer; configs={'ocr':{'text_detector':{},'text_recognizer':{}},'layout_analyzer':{'layout_parser':{},'table_structure_recognizer':{}}}; DocumentAnalyzer(configs=configs, device='cpu', visualize=False); print('yomitoku models cached')"

COPY app/ ./app
COPY src/data ./src/data

ENV PORT=8080
ENV GUNICORN_TIMEOUT=3600
CMD ["sh", "-c", "gunicorn -b :8080 -w 1 -t ${GUNICORN_TIMEOUT} app.main:app"]
