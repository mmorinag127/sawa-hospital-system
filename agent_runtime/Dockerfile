ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libreoffice-calc fonts-noto-cjk \
 && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge '20250419+deb13u1' \
 && rm -rf /var/lib/apt/lists/*
# Separate environments preserve each native API's dependency contract.
RUN python -m venv --system-site-packages /opt/agent/hospital \
 && python -m venv /opt/agent/gateway \
 && python -m venv /opt/agent/shift \
 && python -m venv /opt/agent/school-lunch
RUN for environment in hospital gateway shift school-lunch; do \
    /opt/agent/$environment/bin/python -m pip install --no-cache-dir --upgrade --no-deps 'pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70' 'certifi @ https://files.pythonhosted.org/packages/c4/51/29046e48f693c54e5b7412216d447a1d8362c9653148d98c02cdf65fa733/certifi-2026.10.19-py3-none-any.whl#sha256=42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b' || exit 1; \
    done
RUN /opt/agent/hospital/bin/pip install --no-cache-dir httpx==0.28.1 psutil==7.2.2
COPY experiments/agent_assistant/requirements.lock /tmp/agent-requirements.lock
RUN /opt/agent/gateway/bin/pip install --no-cache-dir -r /tmp/agent-requirements.lock psycopg2-binary==2.9.11
COPY agent_runtime/native/shift /opt/sawa-agent/native/shift
RUN /opt/agent/shift/bin/pip install --no-cache-dir -r /opt/sawa-agent/native/shift/backend/requirements.txt
RUN /opt/agent/shift/bin/pip install --no-cache-dir httpx==0.28.1 psutil==7.2.2
COPY agent_runtime/native/school-lunch /opt/sawa-agent/native/school-lunch
RUN /opt/agent/school-lunch/bin/pip install --no-cache-dir -r /opt/sawa-agent/native/school-lunch/backend/requirements.txt -r /opt/sawa-agent/native/school-lunch/worker/requirements.txt psutil
COPY scripts/verify_tls_trust.py /tmp/verify_tls_trust.py
RUN for environment in hospital gateway shift school-lunch; do \
    /opt/agent/$environment/bin/python /tmp/verify_tls_trust.py --online || exit 1; \
    done
COPY backend /opt/sawa-agent/native/hospital/backend
COPY ocr_pipeline /opt/sawa-agent/native/hospital/ocr_pipeline
COPY agent_runtime/baselines /opt/sawa-agent/baselines
COPY agent_runtime/release.json /opt/sawa-agent/release.json
COPY agent_runtime/manifests /opt/sawa-agent/manifests
COPY experiments /app/experiments
COPY frontend/public/assistant-workflow.css /app/frontend/public/assistant-workflow.css
RUN /opt/agent/gateway/bin/python -m experiments.agent_assistant.prepare_packaged
# Hospital comes from the same release checkout, including current shared fixes.
ARG SOURCE_REVISION
RUN /opt/agent/gateway/bin/python -c 'import json,os; from pathlib import Path; from experiments.agent_assistant.runtime import source_manifest; Path("/opt/sawa-agent/manifests/hospital.json").write_text(json.dumps(dict(source_revision=os.environ["SOURCE_REVISION"],files=source_manifest("/opt/sawa-agent/native/hospital"))))'
RUN SAWA_TRIAL_WEB_TOKEN=synthetic-image-smoke-token-not-a-credential-20261021 \
    SAWA_TRIAL_OPERATOR_ACCOUNT=build-smoke@example.invalid \
    PORTAL_AUTH_ME_URL=https://worker-stg-avlnzjjrca-dt.a.run.app/portal/auth/me \
    GEMINI_AGENT_MODEL=gemini-3.8-flash \
    /opt/agent/gateway/bin/python -m experiments.agent_assistant.check_packaged
CMD ["/opt/agent/gateway/bin/python","-m","experiments.agent_assistant.runtime"]
