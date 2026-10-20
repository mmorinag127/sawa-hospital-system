"""Boot the real feature APIs against empty migrated trial copies.

Checks routing, storage confinement and native authentication. No PDF is uploaded,
no LLM or solver job is started, and this is not a daily-workflow acceptance test.
"""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
import httpx

from .catalog import OPERATIONS
from .sandbox import Sandbox
from .store import Store


async def main():
    root = Path(__file__).resolve().parents[2] / "tmp" / "agent-assistant-trial"
    config = json.loads((root / "config.json").read_text())
    proof = root / ("native-check-"+datetime.now().strftime("%Y%m%dT%H%M%S%f"))
    store = Store(proof)
    store.set_budget(1)
    run, _ = store.create("native-contract-test", "native-contract-check", "空の試用DBで既存APIの起動と経路を検査", list(config["domains"]), 1)
    sandbox = Sandbox(proof, config["domains"])
    sandbox.prepare(run)
    result = {}
    try:
        await sandbox.start(run)
        for domain in run["domains"]:
            health = await sandbox.request(run, domain, "GET", "/health")
            response = await sandbox.request(run, domain, "GET", "/openapi.json")
            if not response.is_success:
                raise RuntimeError(f"{domain}: schema retrieval failed: {response.status_code}")
            paths = response.json()["paths"]
            canonical = {re.sub(r"\{[^}]+\}", "{}", path): methods for path, methods in paths.items()}
            missing = [name for name, op in OPERATIONS.items() if op.domain == domain and op.method.lower() not in canonical.get(re.sub(r"\{[^}]+\}", "{}", op.path), {})]
            result[domain] = {"health": health.status_code, "missing_operations": missing,
                              "operation_count": sum(op.domain == domain for op in OPERATIONS.values())}
            if missing:
                raise RuntimeError(f"{domain}: missing native operations: {missing}")
        key = (run["id"], "school-lunch-worker")
        async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
            response = await client.get(sandbox.urls[key]+"/health/ocr-runtime", headers={"X-Trial-Token": sandbox.tokens[key]})
            result["school-lunch"]["worker_runtime_status"] = response.status_code
            result["school-lunch"]["worker_runtime"] = response.json()
        if response.status_code != 200:
            raise RuntimeError("school-lunch OCR runtime is not ready")
        await sandbox.stop(run["id"])
        restored = await sandbox.restore(run)
        result["isolation"] = {"all_children_reaped": not sandbox.children, "restored": restored["restored"]}
        (proof / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({"proof": str(proof), "results": result}, ensure_ascii=False))
    finally:
        await sandbox.stop(run["id"])


if __name__ == "__main__":
    asyncio.run(main())
