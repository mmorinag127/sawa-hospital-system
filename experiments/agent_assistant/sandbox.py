"""Each request owns copies of local trial databases and file stores.

Rollback discards the request's copies, never restores over a shared database.
Child processes are terminated and reaped before swapping directories.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import sqlite3
import sys
import subprocess
import psutil

import httpx

from .store import Conflict

MODULES = {"hospital": "src.main:app", "shift": "shift_scheduler_backend.main:app", "school-lunch": "src.main:app"}
# No inherited cloud/database credentials or queue destinations.
ENV_ALLOW = {"GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_IDS", "AUTH_PROVIDER", "PORTAL_AUTH_ME_URL",
             "GEMINI_API_KEY", "GOOGLE_API_KEY", "PROCESS_GEMINI_MODEL", "WORKFLOW_V2_LLM_MODEL",
             "GOOGLE_APPLICATION_CREDENTIALS", "OCR_PIPELINE_BUCKET", "OCR_PIPELINE_URL", "OCR_PIPELINE_TARGET_AUDIENCE"}


def copy_tree(source, target):
    source, target = Path(source).resolve(), Path(target)
    if any(p.is_symlink() for p in source.rglob("*")):
        raise Conflict("試用データ内のシンボリックリンクは使用できません")
    shutil.copytree(source, target)


class Sandbox:
    def __init__(self, root, configs, *, durable_store=None):
        self.root = Path(root).resolve()
        self.configs = configs
        self.durable_store = durable_store
        self.children = {}
        self.tokens = {}
        self.urls = {}
        self.logs = {}
        self.lock = asyncio.Lock()

    def path(self, run_id):
        if len(run_id) != 32 or any(ch not in "0123456789abcdef" for ch in run_id):
            raise ValueError("invalid run id")
        return self.root / "runs" / run_id

    def prepare(self, run):
        directory = self.path(run["id"])
        directory.mkdir(parents=True, mode=0o700)
        before = directory / "before"
        before.mkdir()
        for domain in run["domains"]:
            cfg = self.configs.get(domain)
            if not cfg:
                raise Conflict(f"{domain} の試用用データと実行設定が未設定です")
            baseline = Path(cfg["baseline"]).resolve()
            marker = baseline / "trial-manifest.json"
            if not marker.is_file() or json.loads(marker.read_text()).get("purpose") != "sawa-agent-isolated-trial":
                raise Conflict("試用専用と明示したbaselineが必要です")
            if not (baseline / "database.sqlite3").is_file():
                raise Conflict("試用DBがありません")
            copy_tree(baseline, before / domain)
            # Online SQLite backup includes committed WAL contents, without copying locks.
            for suffix in ("-wal", "-shm"):
                (before / domain / ("database.sqlite3"+suffix)).unlink(missing_ok=True)
            with sqlite3.connect(f"file:{baseline / 'database.sqlite3'}?mode=ro", uri=True) as src:
                with sqlite3.connect(before / domain / "database.sqlite3") as dst:
                    src.backup(dst)
        copy_tree(before, directory / "data")
        initialized = False
        for domain in run["domains"]:
            cfg = self.configs[domain]
            if not cfg.get("initialize_pending_sources"):
                continue
            if domain != "hospital":
                raise Conflict("この試用初期化は病院注文専用です")
            worktree = Path(cfg["worktree"]).resolve()
            from .runtime import verify_source
            verify_source(worktree, cfg["source_manifest"])
            data = directory / "data" / domain
            token = self.tokens.setdefault((run["id"], domain), secrets.token_urlsafe(32))
            env = self.environment(run, domain, cfg, data, worktree, token, include_models=False)
            log = directory / "initialization.log"
            with log.open("wb") as output:
                result = subprocess.run([cfg["python"], str(Path(__file__).with_name("domain_host.py")), "--initialize", run["id"]],
                    cwd=data, env=env, stdout=output, stderr=output, timeout=60)
            if result.returncode:
                raise Conflict("試用原本の正規登録に失敗しました。初期化ログを確認してください")
            initialized = True
        if initialized:
            copy_tree(directory / "data", directory / "registered-before")
            os.replace(before, directory / "baseline-before-registration")
            os.replace(directory / "registered-before", before)
        if self.durable_store is not None:
            self.durable_store.persist_files(run["id"])

    def environment(self, run, domain, cfg, data, worktree, token, *, include_models=True):
        if set(cfg.get("env", {})) - ENV_ALLOW:
            raise Conflict("試用プロセスに許可されていない環境設定があります")
        env = {k: os.environ[k] for k in ("PATH", "LANG", "SYSTEMROOT") if k in os.environ}
        env.update(cfg.get("env", {}))
        env["HOME"] = str(data / "home")
        Path(env["HOME"]).mkdir(exist_ok=True)
        if include_models and cfg.get("model_cache"):
            source_cache = Path(cfg["model_cache"]).resolve()
            target_cache = Path(env["HOME"]) / "model-cache"
            if not source_cache.is_dir():
                raise Conflict("固定OCRモデルのキャッシュが見つかりません")
            if any(p.is_symlink() and not p.resolve().is_relative_to(source_cache) for p in source_cache.rglob("*")):
                raise Conflict("OCRモデルのキャッシュが保存範囲外を参照しています")
            if not target_cache.exists():
                shutil.copytree(source_cache, target_cache)
            env["HF_HOME"] = str(target_cache)
        uri = "sqlite:///"+str(data / "database.sqlite3")
        env.update({"DB_URI": uri, "DATABASE_URL": uri, "SAWA_TRIAL_TOKEN": token,
                    "SAWA_TRIAL_MODULE": MODULES[domain], "SAWA_TRIAL_SOURCE": str(worktree / "backend"),
                    "SAWA_TRIAL_DATA": str(data), "SAWA_API_SERVICE_TOKEN": token,
                    "SAWA_REQUIRE_API_AUTH": "true",
                    "SAWA_AGENT_TRIAL_TRANSPORT": "request_local",
                    "OCR_ARTIFACT_DIR": str(data / "ocr-artifacts"),
                    "HOSPITAL_WORKFLOW_OCR_DELIVERY_PROCESS_LEASE_SECONDS": "1800",
                    "LOCAL_STORAGE_ROOT": str(data / "storage"), "STORAGE_ROOT": str(data / "storage"),
                    "OUTPUT_DIR": str(data / "outputs"), "ORDERS_OUTPUT_DIR": str(data / "outputs"),
                    "UPLOAD_DIR": str(data / "uploads"),
                    "ENABLE_DEV_SEED": "false", "AUTO_CREATE_SCHEMA": "false"})
        if domain == "hospital":
            if not cfg.get("facility_master_file"):
                raise Conflict("病院注文の試用用正本JSONの指定が必要です")
            master = (data / cfg["facility_master_file"]).resolve()
            if not master.is_relative_to(data) or not master.is_file():
                raise Conflict("試用コピー内の正本JSONが見つかりません")
            env["FACILITY_MASTER_PATH"] = str(master)
            # Native OCR needs its real storage/pipeline setup. Namespaces are
            # owned by this request even when an operator explicitly configures it.
            env["OCR_PIPELINE_INPUT_PREFIX"] = f"agent-trial/{run['id']}/input/"
            env["OCR_PIPELINE_OUTPUT_PREFIX"] = f"agent-trial/{run['id']}/output/"
        return env

    async def start(self, run):
        for domain in run["domains"]:
            key = (run["id"], domain)
            if key in self.children and self.children[key].returncode is None:
                continue
            cfg = self.configs[domain]
            data = self.path(run["id"]) / "data" / domain
            worktree = Path(cfg["worktree"]).resolve()
            # Only the operator's dedicated named worktree can supply source.
            if cfg.get("source_manifest"):
                from .runtime import verify_source
                verify_source(worktree, cfg["source_manifest"])
            elif not (worktree / ".git").is_file():
                raise Conflict("業務APIには専用Git worktreeを指定してください")
            token = self.tokens.setdefault(key, secrets.token_urlsafe(32))
            env = self.environment(run, domain, cfg, data, worktree, token)
            if domain == "school-lunch":
                worker_key = (run["id"], "school-lunch-worker")
                if worker_key not in self.children or self.children[worker_key].returncode is not None:
                    worker_env = {**env, "SAWA_TRIAL_SOURCE": str(worktree / "worker"),
                                  "SAWA_TRIAL_MODULE": "service.main:app",
                                  "OCR_PIPELINE_ROOT": str(worktree / "ocr_pipeline"),
                                  "IMPLEMENTATION_PRICE_OCR_DISPATCH_MODE": "local",
                                  "IMPLEMENTATION_PRICE_OCR_LOCAL_TOKEN": token}
                    await self.launch(run, "school-lunch-worker", cfg["python"], data, worker_env)
                worker_token = self.tokens[worker_key]
                env.update(IMPLEMENTATION_PRICE_OCR_DISPATCH_MODE="local",
                           IMPLEMENTATION_PRICE_OCR_LOCAL_TOKEN=worker_token,
                           IMPLEMENTATION_PRICE_OCR_WORKER_URL=self.urls[worker_key],
                           IMPLEMENTATION_PRICE_OCR_WORKER_TIMEOUT_SECONDS="1800")
                # Worker validation reads the same captured local URL and timeout.
                # These are set in its child process before importing the native app.
            await self.launch(run, domain, cfg["python"], data, env)

    async def launch(self, run, domain, python, data, env):
        key = (run["id"], domain)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if domain == "school-lunch-worker":
            env.update(IMPLEMENTATION_PRICE_OCR_WORKER_URL=f"http://127.0.0.1:{port}",
                       IMPLEMENTATION_PRICE_OCR_WORKER_TIMEOUT_SECONDS="1800")
        log = open(self.path(run["id"]) / f"{domain}.log", "ab")
        self.logs[key] = log
        self.children[key] = await asyncio.create_subprocess_exec(
            python, str(Path(__file__).with_name("domain_host.py")), str(port), run["id"],
            cwd=data, env=env, stdout=log, stderr=log, start_new_session=True)
        proc = self.children[key]
        (self.path(run["id"]) / f"{domain}.process.json").write_text(json.dumps({"pid": proc.pid, "created": psutil.Process(proc.pid).create_time()}))
        token = env["SAWA_TRIAL_TOKEN"]
        self.tokens[key] = token
        self.urls[key] = f"http://127.0.0.1:{port}"
        try:
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                for _ in range(600):
                    if self.children[key].returncode is not None:
                        raise Conflict(f"{domain} のAPIが起動できません。試用ログを確認してください")
                    try:
                        response = await client.get(self.urls[key]+"/__trial_identity", headers={"X-Trial-Token": token})
                        if response.status_code == 200 and response.json().get("data") == str(data):
                            return
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(.1)
                raise Conflict(f"{domain} のAPI起動が時間切れです")
        except BaseException:
            await self.stop(run["id"])
            raise

    async def stop(self, run_id):
        async with self.lock:
            await self._stop(run_id)

    async def _stop(self, run_id):
        # After a gateway restart, identify our own children by PID, creation time
        # and exact launch marker. Never kill another task's reused PID.
        for marker in self.path(run_id).glob("*.process.json"):
            info = json.loads(marker.read_text())
            if any(p.pid == info["pid"] for p in self.children.values()):
                continue
            try:
                process = psutil.Process(info["pid"])
                if process.create_time() != info["created"] or run_id not in process.cmdline() or str(Path(__file__).with_name("domain_host.py")) not in process.cmdline():
                    raise Conflict("試用APIのプロセス所有権を確認できません。復元を停止しました")
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.to_thread(process.wait, 8)
                except psutil.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    await asyncio.to_thread(process.wait, 8)
            except psutil.NoSuchProcess:
                try:
                    os.killpg(info["pid"], 0)
                except ProcessLookupError:
                    pass
                else:
                    raise Conflict("試用APIの子プロセスが残っています。復元を停止しました")
            marker.unlink()
        keys = [key for key in self.children if key[0] == run_id]
        for key in keys:
            process = self.children[key]
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
        for key in keys:
            process = self.children[key]
            try:
                await asyncio.wait_for(process.wait(), 8)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            self.children.pop(key)
            self.urls.pop(key, None)
            self.tokens.pop(key, None)
            self.logs.pop(key).close()
            (self.path(run_id) / f"{key[1]}.process.json").unlink(missing_ok=True)

    async def restore(self, run):
        await self.stop(run["id"])
        directory = self.path(run["id"])
        if not (directory / "before").is_dir():
            raise Conflict("復元元がありません。元に戻したとは扱いません")
        replacement = directory / "restored"
        if replacement.exists():
            raise Conflict("前の復元が未確定です")
        copy_tree(directory / "before", replacement)
        # The original modified data remains available for inspection.
        archive = directory / ("discarded-"+secrets.token_hex(6))
        os.replace(directory / "data", archive)
        try:
            os.replace(replacement, directory / "data")
        except BaseException:
            os.replace(archive, directory / "data")
            raise
        return {"restored": True, "scope": "this_request_sandbox", "archive": archive.name,
                "notice": "試用データを依頼開始前へ戻しました。API利用料金は戻りません。"}

    def local_jobs(self, run):
        jobs = []
        for domain in run["domains"]:
            for path in (self.path(run["id"]) / "data" / domain / "local-deliveries").glob("*"):
                if path.name.startswith("."):
                    continue
                receipt = json.loads(path.read_text())
                contract = receipt.pop("contract", {})
                jobs.append({"domain": domain, "receipt": path.name, **receipt,
                             "kind": contract.get("kind", "solve"), "generation": contract.get("generation")})
        return jobs

    async def request(self, run, domain, method, path, *, params=None, body=None, authorization=None, files=None, revision=None):
        key = (run["id"], domain)
        if domain not in run["domains"] or key not in self.urls:
            raise Conflict("依頼対象の試用APIが起動していません")
        headers = {"X-Trial-Token": self.tokens[key], "X-Sawa-Service-Token": self.tokens[key]}
        if authorization:
            headers["Authorization"] = authorization
        if revision is not None:
            headers["If-Match"] = str(revision)
        async with httpx.AsyncClient(timeout=180, follow_redirects=False, trust_env=False) as client:
            return await client.request(method, self.urls[key]+path, params=params, json=body if files is None else None,
                                        data=body if files is not None else None, files=files, headers=headers)
