"""Audit actual same-source image environments without installing into them."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from local_hospital_test_runner import git, reject_hosted_runner


INVENTORY = (
    "import importlib.metadata,json; "
    "print(json.dumps(sorted(set((d.metadata['Name'],d.version) "
    "for d in importlib.metadata.distributions()))))"
)
ENVIRONMENTS = (
    ("BACKEND", "backend", "python"),
    ("OCR_PIPELINE", "producer", "python"),
    ("AGENT_RUNTIME", "agent-hospital", "/opt/agent/hospital/bin/python"),
    ("AGENT_RUNTIME", "agent-gateway", "/opt/agent/gateway/bin/python"),
    ("AGENT_RUNTIME", "agent-shift", "/opt/agent/shift/bin/python"),
    ("AGENT_RUNTIME", "agent-school", "/opt/agent/school-lunch/bin/python"),
)


def audit_runtime(image, name, interpreter, source, directory):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version

    inspected = json.loads(subprocess.check_output([
        "docker", "image", "inspect", image, "--format", "{{json .}}",
    ], text=True))
    identity = inspected["Id"]
    if (inspected["Config"]["Labels"].get("sawa.git_sha") != source
            or inspected["Os"] != "linux" or inspected["Architecture"] != "amd64"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity)):
        raise ValueError("Audit image source differs from the fixed checkout")
    raw = subprocess.check_output([
        "docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none", "--read-only",
        "--entrypoint", interpreter, identity, "-c", INVENTORY,
    ], text=True)
    inventory = json.loads(raw)
    if not isinstance(inventory, list) or not inventory:
        raise ValueError("Actual installed inventory is empty")
    packages, aliases, installed = {}, {}, {}
    for package, version in inventory:
        key = canonicalize_name(package)
        if key in installed and installed[key] != version:
            raise ValueError("Conflicting installed package versions")
        installed[key] = version
        parsed = Version(version)
        if key in {"torch", "torchvision"} and parsed.local == "cpu":
            aliases[key] = {"installed": version, "advisory_lookup": parsed.public}
            version = parsed.public
        requirement = Requirement(package + "==" + version)
        key = canonicalize_name(requirement.name)
        if key in packages and packages[key] != str(requirement):
            raise ValueError("Conflicting installed package versions")
        packages[key] = str(requirement)
    (directory / (name + ".inventory.json")).write_text(raw)
    (directory / (name + ".source.json")).write_text(json.dumps({
        "source_sha": source, "image_id": identity, "interpreter": interpreter,
        "audit_version_aliases": aliases,
    }, indent=2) + "\n")
    requirements = directory / (name + ".requirements.txt")
    requirements.write_text("\n".join(packages[key] for key in sorted(packages)) + "\n")
    report = directory / (name + ".audit.json")
    code = subprocess.run([
        sys.executable, "-m", "pip_audit", "--disable-pip", "--no-deps",
        "--progress-spinner", "off", "--format", "json", "--aliases", "on",
        "--requirement", str(requirements), "--output", str(report),
    ]).returncode
    if code:
        return code
    dependencies = json.loads(report.read_text())["dependencies"]
    if any(value.get("skip_reason") or type(value.get("vulns")) is not list or value["vulns"]
           for value in dependencies):
        raise ValueError("Audit left skipped or affected distributions")
    actual = {(canonicalize_name(value["name"]), value["version"]) for value in dependencies}
    expected = {(key, next(iter(Requirement(value).specifier)).version) for key, value in packages.items()}
    if actual != expected:
        raise ValueError("Audit did not cover the complete installed inventory")
    return 0


def main():
    reject_hosted_runner()
    source = git("rev-parse", "HEAD")
    directory = Path(os.environ["SAWA_LOCAL_EVIDENCE_DIR"]) / "runtime-audits"
    directory.mkdir(exist_ok=False)
    failed = False
    for component, name, interpreter in ENVIRONMENTS:
        image = os.environ.get("SAWA_LOCAL_TEST_IMAGE_" + component)
        try:
            if not image:
                raise ValueError("An explicitly built image is required: " + component)
            code = audit_runtime(image, name, interpreter, source, directory)
        except Exception as exc:
            print(name + ": inventory/audit failed: " + str(exc), flush=True)
            failed = True
            continue
        print(name + ": audit exit " + str(code), flush=True)
        failed |= code != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
