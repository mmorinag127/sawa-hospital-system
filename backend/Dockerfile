ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Native PostgreSQL tools preserve schema, sequences, constraints and raw values.
# The proxy is the same version/hash already pinned by the regular stg workflow.
# Official PGDG repository supplies clients matching each supported source major.
# https://www.postgresql.org/download/linux/debian/
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && . /etc/os-release \
    && printf 'Types: deb\nURIs: https://apt.postgresql.org/pub/repos/apt\nSuites: %s-pgdg\nComponents: main\nSigned-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc\n' "$VERSION_CODENAME" > /etc/apt/sources.list.d/pgdg.sources \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-15 postgresql-client-16 postgresql-client-17 postgresql-client-18 \
    && curl -fsSL https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.24.1/cloud-sql-proxy.linux.amd64 -o /usr/local/bin/cloud-sql-proxy \
    && echo 'fae2766aac9d614a2bdef2f2a7778f3d054f3acd5ff07a81a9e300bd471512eb  /usr/local/bin/cloud-sql-proxy' | sha256sum -c - \
    && chmod 755 /usr/local/bin/cloud-sql-proxy \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src ./src
COPY alembic.ini ./alembic.ini
COPY alembic/catalog_contracts.json ./alembic/catalog_contracts.json
COPY migrations ./migrations
COPY ocr_pipeline ./ocr_pipeline
COPY tmp ./tmp
COPY pyproject.toml .

# Compile immutable application sources at build time, not on each cold start.
RUN python -m compileall -q src migrations ocr_pipeline

# Trust Cloud Run's reverse proxy headers so request.url matches the public HTTPS URL.
# This is required for verifying Cloud Scheduler OIDC tokens whose audience is the target URL.
CMD ["uvicorn", "src.http_entrypoint:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
