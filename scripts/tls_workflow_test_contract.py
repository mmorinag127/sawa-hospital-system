"""Isolate the exact reviewed CA additions in historical workflow comparisons."""

from copy import deepcopy
import hashlib


STEP_HASHES = {
    "Update TLS trust stores": "968d90b4e4b11f75a179ea88389558a80c03474648303138a5a134517e0fd91f",
    "Update Python trust bundles": "79b83ae18d6f1580dd8f45702b27effcef000ed3ec798361d561e520b6ac819a",
}


def before_ca_upgrade(workflow):
    result = deepcopy(workflow)
    assert result["env"].pop("NODE_OPTIONS") == "--use-openssl-ca"
    assert result["env"].pop("CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE") == "/etc/ssl/certs/ca-certificates.crt"
    for job in result["jobs"].values():
        steps = []
        for step in job.get("steps", []):
            name = step.get("name")
            if name in STEP_HASHES:
                assert hashlib.sha256(step["run"].encode()).hexdigest() == STEP_HASHES[name]
                assert set(step) == ({"name", "run", "shell"} if name == "Update TLS trust stores" else {"name", "run"})
                if "shell" in step:
                    assert step["shell"] == "bash"
                continue
            if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                assert step.pop("with") == {"version": "0.13.4"}
            if "run" in step:
                for runtime, script, argument in (("python", "verify_tls_trust.py", " --online"), ("node", "verify_tls_trust.mjs", "")):
                    probe = (f'docker run --rm --entrypoint {runtime} \\\n'
                             f'  -v "$PWD/scripts/{script}:/tmp/{script}:ro" \\\n'
                             f'  "$image" /tmp/{script}{argument}\n')
                    step["run"] = step["run"].replace(probe, "")
            steps.append(step)
        if "steps" in job:
            job["steps"] = steps
    return result
