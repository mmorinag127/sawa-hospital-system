FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG LEPTONICA_VERSION=1.87.0
ARG TESSERACT_VERSION=5.5.2

RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf \
    automake \
    ca-certificates \
    curl \
    g++ \
    git \
    libglib2.0-0 \
    libgl1 \
    libarchive-dev \
    libcurl4-openssl-dev \
    libgif-dev \
    libicu-dev \
    libjpeg62-turbo-dev \
    libopenjp2-7-dev \
    libpng-dev \
    libsm6 \
    libtiff-dev \
    libtool \
    libwebp-dev \
    libxext6 \
    libxrender1 \
    make \
    pkg-config \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch ${LEPTONICA_VERSION} https://github.com/DanBloomberg/leptonica.git /tmp/leptonica \
    && cd /tmp/leptonica \
    && ./autogen.sh \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && cd /tmp \
    && rm -rf /tmp/leptonica \
    && git clone --depth 1 --branch ${TESSERACT_VERSION} https://github.com/tesseract-ocr/tesseract.git /tmp/tesseract \
    && cd /tmp/tesseract \
    && ./autogen.sh \
    && PKG_CONFIG_PATH=/usr/local/lib/pkgconfig ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && cd /tmp \
    && rm -rf /tmp/tesseract \
    && mkdir -p /usr/local/share/tessdata \
    && curl -fsSL https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata -o /usr/local/share/tessdata/eng.traineddata \
    && curl -fsSL https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/osd.traineddata -o /usr/local/share/tessdata/osd.traineddata \
    && echo "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2  /usr/local/share/tessdata/eng.traineddata" | sha256sum -c - \
    && echo "9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff  /usr/local/share/tessdata/osd.traineddata" | sha256sum -c - \
    && ldconfig \
    && tesseract --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY migrations ./migrations
COPY pyproject.toml .

# Trust Cloud Run's reverse proxy headers so request.url matches the public HTTPS URL.
# This is required for verifying Cloud Scheduler OIDC tokens whose audience is the target URL.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
