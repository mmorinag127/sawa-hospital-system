"""Actions-only retained-revision rehearsal, restoring the original stg traffic."""
import json
import copy
import os
from pathlib import Path
import signal
import subprocess
import sys
import urllib.request

from hospital_release_context import PROJECT, REGION, require_context


def cloud(*args):
    return json.loads(subprocess.check_output(["gcloud", *args, "--project=" + PROJECT,
                                              "--region=" + REGION, "--format=json"]))


def current():
    service = cloud("run", "services", "describe", "worker-stg")
    traffic = [t for t in service["status"]["traffic"] if t.get("percent")]
    if len(traffic) != 1 or traffic[0]["percent"] != 100:
        raise RuntimeError("single staging traffic target required")
    return traffic[0]["revisionName"], service["status"]["url"]


def configuration(revision):
    if (revision["metadata"].get("deletionTimestamp")
            or revision["metadata"]["labels"].get("serving.knative.dev/service") != "worker-stg"
            or not any(c.get("type") == "Ready" and c.get("status") == "True"
                       for c in revision["status"]["conditions"])):
        raise RuntimeError("ready retained worker-stg revision required")
    spec = revision["spec"]
    if len(spec["containers"]) != 1:
        raise RuntimeError("single worker container required")
    runtime = copy.deepcopy(spec)
    container = runtime["containers"][0]
    container.pop("image", None)
    container["env"] = sorted(container.get("env", []), key=lambda entry: entry["name"])
    annotations = revision["metadata"].get("annotations", {})
    return {"spec": runtime, "runtime_annotations": {
        key: value for key, value in annotations.items()
        if key.startswith(("run.googleapis.com/", "autoscaling.knative.dev/"))
        and key not in {"run.googleapis.com/client-name", "run.googleapis.com/client-version",
                        "run.googleapis.com/operation-id", "run.googleapis.com/build-source-location",
                        "run.googleapis.com/build-id", "run.googleapis.com/build-name"}}}


def route(revision):
    subprocess.run(["gcloud", "run", "services", "update-traffic", "worker-stg",
                    "--project=" + PROJECT, "--region=" + REGION,
                    "--to-revisions=" + revision + "=100", "--quiet"], check=True)
    if current()[0] != revision:
        raise RuntimeError("staging traffic switch not confirmed")


def health(url):
    req = urllib.request.Request(url + "/health", headers={"Authorization": "Bearer " + os.environ["STG_ID_TOKEN"]})
    with urllib.request.urlopen(req, timeout=60) as response:
        if response.status != 200 or json.load(response).get("status") != "ok":
            raise RuntimeError("staging health did not pass")


def restore():
    if require_context(os.environ) != "stg":
        raise RuntimeError("staging restoration context required")
    path = Path(os.environ["RUNNER_TEMP"]) / "stg-traffic-rehearsal.json"
    if not path.exists():
        return
    report = json.loads(path.read_text())
    if (report["original"] != os.environ["EXPECTED_REVISION"]
            or report["retained"] != os.environ["RETAINED_REVISION"]):
        raise RuntimeError("restoration record does not match approved targets")
    now, url = current()
    if now == report["retained"]:
        route(report["original"])
    elif now != report["original"]:
        raise RuntimeError("traffic changed independently; refusing restoration")
    health(url)
    report["restored"] = True
    path.write_text(json.dumps(report))


def main():
    if require_context(os.environ) != "stg" or os.environ.get("REHEARSAL_CONFIRMATION") != "REHEARSE_STG_TRAFFIC_AND_RESTORE":
        raise RuntimeError("explicit staging rehearsal context required")
    original, url = current()
    target = os.environ["RETAINED_REVISION"]
    if original != os.environ["EXPECTED_REVISION"] or target == original or not target.startswith("worker-stg-"):
        raise RuntimeError("staging targets changed or invalid")
    before = cloud("run", "revisions", "describe", original)
    retained = cloud("run", "revisions", "describe", target)
    if configuration(before) != configuration(retained):
        raise RuntimeError("retained revision runtime configuration differs")
    report = {"original": original, "retained": target, "restored": False,
              "retained_health": False, "production_modified": False,
              "scope": "staging traffic and health only; not production DB compatibility"}
    path = Path(os.environ["RUNNER_TEMP"]) / "stg-traffic-rehearsal.json"
    path.write_text(json.dumps(report))
    def interrupted(_signum, _frame):
        raise SystemExit("staging rehearsal interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        health(url)
        route(target)
        health(url)
        report["retained_health"] = True
    finally:
        now = current()[0]
        if now == target:
            route(original)
        elif now != original:
            raise RuntimeError("traffic changed independently; refusing to overwrite it")
        report["restored"] = current()[0] == original
        path.write_text(json.dumps(report))
    health(url)
    print("staging retained revision and original restoration verified")


if __name__ == "__main__":
    restore() if sys.argv[1:] == ["--restore"] else main()
