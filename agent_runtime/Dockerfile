ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
# Separate environments preserve each native API's dependency contract.
RUN python -m venv --system-site-packages /opt/agent/hospital \
 && python -m venv /opt/agent/gateway \
 && python -m venv /opt/agent/shift \
 && python -m venv /opt/agent/school-lunch
RUN /opt/agent/hospital/bin/pip install --no-cache-dir httpx==0.28.1 psutil==7.2.2
COPY experiments/agent_assistant/requirements.lock /tmp/agent-requirements.lock
RUN /opt/agent/gateway/bin/pip install --no-cache-dir -r /tmp/agent-requirements.lock psycopg2-binary==2.9.11
COPY agent_runtime/native/shift /opt/sawa-agent/native/shift
RUN /opt/agent/shift/bin/pip install --no-cache-dir -r /opt/sawa-agent/native/shift/backend/requirements.txt httpx psutil
COPY agent_runtime/native/school-lunch /opt/sawa-agent/native/school-lunch
RUN /opt/agent/school-lunch/bin/pip install --no-cache-dir -r /opt/sawa-agent/native/school-lunch/backend/requirements.txt -r /opt/sawa-agent/native/school-lunch/worker/requirements.txt psutil
COPY backend /opt/sawa-agent/native/hospital/backend
COPY ocr_pipeline /opt/sawa-agent/native/hospital/ocr_pipeline
COPY agent_runtime/baselines /opt/sawa-agent/baselines
COPY agent_runtime/release.json /opt/sawa-agent/release.json
COPY agent_runtime/manifests /opt/sawa-agent/manifests
COPY experiments /app/experiments
COPY frontend/public/assistant-workflow.css /app/frontend/public/assistant-workflow.css
# Hospital comes from the same release checkout, including current shared fixes.
ARG SOURCE_REVISION
RUN /opt/agent/gateway/bin/python -c 'import json,os; from pathlib import Path; from experiments.agent_assistant.runtime import source_manifest; Path("/opt/sawa-agent/manifests/hospital.json").write_text(json.dumps(dict(source_revision=os.environ["SOURCE_REVISION"],files=source_manifest("/opt/sawa-agent/native/hospital"))))'
RUN SAWA_TRIAL_WEB_TOKEN=synthetic-image-smoke-token-not-a-credential-20261021 \
    SAWA_TRIAL_OPERATOR_ACCOUNT=build-smoke@example.invalid \
    PORTAL_AUTH_ME_URL=https://worker-stg-avlnzjjrca-dt.a.run.app/portal/auth/me \
    GEMINI_AGENT_MODEL=gemini-3.8-flash \
    /opt/agent/gateway/bin/python -m experiments.agent_assistant.check_packaged
CMD ["/opt/agent/gateway/bin/python","-m","experiments.agent_assistant.runtime"]
