ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
# Test-only imports; native application environments remain unchanged.
RUN /opt/agent/gateway/bin/pip install --no-cache-dir \
    SQLAlchemy==2.1.6 alembic==1.20.1 openpyxl==3.1.5 et-xmlfile==2.0.0
