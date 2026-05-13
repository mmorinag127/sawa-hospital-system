ARG BASE_IMAGE
FROM ${BASE_IMAGE}

WORKDIR /app

COPY src ./src
COPY migrations ./migrations
COPY ocr_pipeline ./ocr_pipeline
COPY tmp ./tmp
COPY pyproject.toml .

# Trust Cloud Run's reverse proxy headers so request.url matches the public HTTPS URL.
# This is required for verifying Cloud Scheduler OIDC tokens whose audience is the target URL.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
