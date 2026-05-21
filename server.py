#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from base64 import b64decode
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
OUTPUT_DIR = ROOT / "outputs"

DEFAULT_BILI_BROWSER_SCRIPT = ROOT / "bilibili_browser_crawler.py"
DEFAULT_XHS_SCRIPT = Path(os.environ.get("XHS_SCRIPT", ROOT / "xhs_comments_spider.py"))
DEFAULT_BILI_JSON = Path(os.environ.get("BILI_SAMPLE_JSON", ROOT / "samples" / "bilibili_comments.json"))
DEFAULT_XHS_JSON = Path(os.environ.get("XHS_SAMPLE_JSON", ROOT / "samples" / "xhs_comments.json"))
DEFAULT_BILI_PROFILE = ROOT / ".bilibili-profile"
DEFAULT_XHS_PROFILE = ROOT / ".xhs-profile"
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Job:
    id: str
    platform: str
    command: list[str]
    cwd: str
    output_file: str
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    started_at: str = ""
    finished_at: str = ""
    returncode: int | None = None
    logs: list[str] = field(default_factory=list)
    process: subprocess.Popen | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "platform": self.platform,
            "command": self.command,
            "cwd": self.cwd,
            "output_file": self.output_file,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "returncode": self.returncode,
            "logs": self.logs,
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def json_response(handler: BaseHTTPRequestHandler, data: object, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, text: str, status: int = 200, content_type: str = "text/plain") -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", f"{content_type}; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    return json.loads(raw or "{}")


def is_authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not DASHBOARD_PASSWORD:
        return True
    header = handler.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = b64decode(header.removeprefix("Basic ").strip()).decode("utf-8")
    except Exception:
        return False
    _user, sep, password = decoded.partition(":")
    return bool(sep and password == DASHBOARD_PASSWORD)


def auth_required(handler: BaseHTTPRequestHandler) -> None:
    body = b"Authentication required"
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="Comment Dashboard"')
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def safe_output_path(value: str | None, default_name: str) -> Path:
    if not value:
        return OUTPUT_DIR / default_name
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = OUTPUT_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def python_for_script(script_path: Path) -> str:
    local_python = script_path.parent / ".venv" / "bin" / "python"
    if local_python.exists():
        return str(local_python)
    alt_python = script_path.parent / "venv" / "bin" / "python"
    if alt_python.exists():
        return str(alt_python)
    return sys.executable


def run_job(job: Job) -> None:
    with JOBS_LOCK:
        job.status = "running"
        job.started_at = datetime.now().isoformat(timespec="seconds")
    try:
        proc = subprocess.Popen(
            job.command,
            cwd=job.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            start_new_session=True,
        )
        with JOBS_LOCK:
            job.process = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            with JOBS_LOCK:
                job.logs.append(line.rstrip("\n"))
                if len(job.logs) > 2000:
                    job.logs = job.logs[-2000:]
        code = proc.wait()
        with JOBS_LOCK:
            job.returncode = code
            if job.status != "stopped":
                job.status = "finished" if code == 0 else "failed"
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            job.process = None
    except Exception as exc:
        with JOBS_LOCK:
            job.status = "failed"
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            job.logs.append(f"启动失败：{type(exc).__name__}: {exc}")
            job.process = None


def stop_job(job_id: str) -> tuple[bool, str, dict | None]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return False, "job not found", None
        proc = job.process
        if not proc or proc.poll() is not None:
            if job.status not in {"finished", "failed", "stopped"}:
                job.status = "stopped"
                job.finished_at = datetime.now().isoformat(timespec="seconds")
            return True, "job already stopped", job.public()
        job.status = "stopping"
        job.logs.append("收到停止请求，正在终止当前任务...")
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception as exc:
        with JOBS_LOCK:
            job.logs.append(f"停止失败：{type(exc).__name__}: {exc}")
        return False, str(exc), job.public()

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    with JOBS_LOCK:
        job.returncode = proc.poll()
        job.status = "stopped"
        job.finished_at = datetime.now().isoformat(timespec="seconds")
        job.process = None
        job.logs.append("任务已停止。")
        return True, "stopped", job.public()


def stop_profile_processes(profile_dir: Path) -> list[str]:
    profile = str(profile_dir.resolve())
    stopped: list[str] = []
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return [f"无法检查旧登录进程：{type(exc).__name__}: {exc}"]

    current_pid = os.getpid()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid or profile not in command:
            continue
        if not (
            "xhs_comments_spider.py" in command
            or "bilibili_browser_crawler.py" in command
            or "Google Chrome for Testing" in command
        ):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(f"已关闭旧登录/浏览器进程 {pid}")
        except ProcessLookupError:
            continue
        except Exception as exc:
            stopped.append(f"关闭旧进程 {pid} 失败：{type(exc).__name__}: {exc}")
    if stopped:
        time.sleep(0.8)
    return stopped


def finish_login_jobs_for_profile(profile_dir: Path) -> list[str]:
    profile = str(profile_dir.resolve())
    logs: list[str] = []
    with JOBS_LOCK:
        jobs = [
            job for job in JOBS.values()
            if job.platform.endswith("-login")
            and job.process
            and job.process.poll() is None
            and str(Path(job.output_file).expanduser().resolve()) == profile
        ]
    for job in jobs:
        proc = job.process
        if not proc or not proc.stdin:
            continue
        try:
            proc.stdin.write("\n")
            proc.stdin.flush()
            logs.append(f"已自动保存并关闭登录窗口：{job.platform}")
            deadline = time.time() + 8
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.2)
        except Exception as exc:
            logs.append(f"自动保存登录状态失败：{type(exc).__name__}: {exc}")
    return logs


def prepare_profile_for_crawl(profile_dir: Path) -> list[str]:
    logs = finish_login_jobs_for_profile(profile_dir)
    logs.extend(stop_profile_processes(profile_dir))
    return logs


def start_job(platform: str, command: list[str], cwd: Path, output_file: Path) -> Job:
    job = Job(
        id=uuid.uuid4().hex,
        platform=platform,
        command=command,
        cwd=str(cwd),
        output_file=str(output_file),
    )
    with JOBS_LOCK:
        JOBS[job.id] = job
    thread = threading.Thread(target=run_job, args=(job,), daemon=True)
    thread.start()
    return job


def list_json_files() -> list[dict]:
    paths = [DEFAULT_BILI_JSON, DEFAULT_XHS_JSON]
    paths.extend(sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True))
    seen: set[str] = set()
    files = []
    output_root = OUTPUT_DIR.resolve()
    for path in paths:
        if not path.exists() or str(path) in seen:
            continue
        resolved = path.resolve()
        seen.add(str(path))
        stat = path.stat()
        files.append({
            "name": path.name,
            "path": str(path),
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "deletable": resolved.parent == output_root,
        })
    return files


def delete_output_json(file_path: Path) -> dict:
    resolved = file_path.expanduser().resolve()
    output_root = OUTPUT_DIR.resolve()
    if resolved.parent != output_root or resolved.suffix.lower() != ".json":
        return {"ok": False, "path": str(file_path), "error": "只能删除 outputs/ 目录下的 JSON 结果文件"}
    if not resolved.exists():
        return {"ok": False, "path": str(resolved), "error": "file not found"}
    resolved.unlink()
    return {"ok": True, "path": str(resolved), "name": resolved.name}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if not is_authorized(self):
            auth_required(self)
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            self.serve_static("index.html")
            return
        if path in {"/styles.css", "/app.js"}:
            self.serve_static(path.removeprefix("/"))
            return
        if path == "/api/defaults":
            json_response(self, {
                "bilibiliBrowserScript": str(DEFAULT_BILI_BROWSER_SCRIPT),
                "bilibiliProfileDir": str(DEFAULT_BILI_PROFILE),
                "xhsScript": str(DEFAULT_XHS_SCRIPT),
                "xhsProfileDir": str(DEFAULT_XHS_PROFILE),
                "bilibiliJson": str(DEFAULT_BILI_JSON),
                "xhsJson": str(DEFAULT_XHS_JSON),
                "outputsDir": str(OUTPUT_DIR),
            })
            return
        if path == "/api/outputs":
            json_response(self, {"files": list_json_files()})
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = job.public() if job else None
            json_response(self, payload or {"error": "job not found"}, 200 if payload else 404)
            return
        if path == "/api/file":
            query = parse_qs(parsed.query)
            file_path = Path(query.get("path", [""])[0]).expanduser()
            if not file_path.exists():
                json_response(self, {"error": "file not found"}, 404)
                return
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception as exc:
                json_response(self, {"error": f"invalid json: {exc}"}, 400)
                return
            json_response(self, data)
            return
        if path.startswith("/static/"):
            self.serve_static(path.removeprefix("/static/"))
            return
        text_response(self, "Not found", 404)

    def do_POST(self) -> None:
        if not is_authorized(self):
            auth_required(self)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/crawl":
            try:
                body = read_json_body(self)
                platform = body.get("platform")
                keyword = str(body.get("keyword") or "").strip()
                if not keyword:
                    json_response(self, {"error": "请先填写关键词"}, 400)
                    return
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                if platform == "bilibili":
                    output = safe_output_path(body.get("outputFile"), f"bilibili_{keyword}_{stamp}.json")
                    script = Path(body.get("scriptPath") or DEFAULT_BILI_BROWSER_SCRIPT).expanduser()
                    profile_dir = Path(body.get("profileDir") or DEFAULT_BILI_PROFILE).expanduser()
                    preflight_logs = prepare_profile_for_crawl(profile_dir)
                    command = [
                        python_for_script(script),
                        str(script),
                        "--keyword",
                        keyword,
                        "--video-count",
                        str(int(body.get("videoCount") or 10)),
                        "--order",
                        str(body.get("biliOrder") or "totalrank"),
                        "--duration",
                        str(int(body.get("biliDuration") or 0)),
                        "--comment-count",
                        str(int(body.get("commentCount") or 20)),
                        "--max-replies-per-comment",
                        str(int(body.get("maxRepliesPerComment") or 200)),
                        "--output",
                        str(output),
                        "--profile-dir",
                        str(profile_dir),
                    ]
                    if body.get("headless"):
                        command.append("--headless")
                    job = start_job("bilibili", command, script.parent, output)
                    if preflight_logs:
                        with JOBS_LOCK:
                            job.logs.extend(preflight_logs)
                    json_response(self, job.public())
                    return
                if platform == "xhs":
                    output = safe_output_path(body.get("outputFile"), f"xhs_{keyword}_{stamp}.json")
                    script = Path(body.get("scriptPath") or DEFAULT_XHS_SCRIPT).expanduser()
                    profile_dir = Path(body.get("profileDir") or DEFAULT_XHS_PROFILE).expanduser()
                    preflight_logs = prepare_profile_for_crawl(profile_dir)
                    hot_comment_count = max(1, min(int(body.get("commentCount") or 20), 20))
                    max_replies = max(0, min(int(body.get("maxRepliesPerComment") or 10), 50))
                    command = [
                        python_for_script(script),
                        str(script),
                        "--keyword",
                        keyword,
                        "--limit",
                        str(int(body.get("limit") or 10)),
                        "--hot-comment-count",
                        str(hot_comment_count),
                        "--max-replies-per-comment",
                        str(max_replies),
                        "--content-type",
                        str(body.get("xhsContentType") or "all"),
                        "--sort",
                        str(body.get("xhsSort") or "general"),
                        "--output",
                        str(output),
                        "--profile-dir",
                        str(profile_dir),
                    ]
                    if body.get("headless"):
                        command.append("--headless")
                    job = start_job("xhs", command, script.parent, output)
                    if preflight_logs:
                        with JOBS_LOCK:
                            job.logs.extend(preflight_logs)
                    json_response(self, job.public())
                    return
                json_response(self, {"error": "platform must be bilibili or xhs"}, 400)
            except Exception as exc:
                json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 500)
            return
        if path == "/api/files/delete":
            try:
                body = read_json_body(self)
                paths = body.get("paths") or []
                if not isinstance(paths, list) or not paths:
                    json_response(self, {"error": "请选择要删除的 JSON 文件"}, 400)
                    return
                results = [delete_output_json(Path(str(item))) for item in paths]
                deleted = [item for item in results if item.get("ok")]
                failed = [item for item in results if not item.get("ok")]
                json_response(self, {"deleted": deleted, "failed": failed})
            except Exception as exc:
                json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 500)
            return
        if path == "/api/bilibili-login":
            try:
                body = read_json_body(self)
                script = Path(body.get("scriptPath") or DEFAULT_BILI_BROWSER_SCRIPT).expanduser()
                profile_dir = Path(body.get("profileDir") or DEFAULT_BILI_PROFILE).expanduser()
                preflight_logs = stop_profile_processes(profile_dir)
                job = start_job(
                    "bilibili-login",
                    [
                        python_for_script(script),
                        str(script),
                        "--login",
                        "--profile-dir",
                        str(profile_dir),
                    ],
                    script.parent,
                    profile_dir,
                )
                if preflight_logs:
                    with JOBS_LOCK:
                        job.logs.extend(preflight_logs)
                json_response(self, job.public())
            except Exception as exc:
                json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 500)
            return
        if path == "/api/xhs-login":
            try:
                body = read_json_body(self)
                script = Path(body.get("scriptPath") or DEFAULT_XHS_SCRIPT).expanduser()
                profile_dir = Path(body.get("profileDir") or DEFAULT_XHS_PROFILE).expanduser()
                preflight_logs = stop_profile_processes(profile_dir)
                job = start_job(
                    "xhs-login",
                    [
                        python_for_script(script),
                        str(script),
                        "--login",
                        "--profile-dir",
                        str(profile_dir),
                    ],
                    script.parent,
                    profile_dir,
                )
                if preflight_logs:
                    with JOBS_LOCK:
                        job.logs.extend(preflight_logs)
                json_response(self, job.public())
            except Exception as exc:
                json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 500)
            return
        if path.startswith("/api/jobs/") and path.endswith("/finish-login"):
            job_id = path.split("/")[-2]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                proc = job.process if job else None
            if not proc or not proc.stdin:
                json_response(self, {"error": "login job is not running"}, 400)
                return
            try:
                proc.stdin.write("\n")
                proc.stdin.flush()
                json_response(self, {"ok": True})
            except Exception as exc:
                json_response(self, {"error": str(exc)}, 500)
            return
        if path.startswith("/api/jobs/") and path.endswith("/stop"):
            job_id = path.split("/")[-2]
            ok, message, payload = stop_job(job_id)
            status = 200 if ok else 404
            json_response(self, payload or {"error": message}, status)
            return
        text_response(self, "Not found", 404)

    def do_DELETE(self) -> None:
        if not is_authorized(self):
            auth_required(self)
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/file":
            query = parse_qs(parsed.query)
            file_path = Path(query.get("path", [""])[0]).expanduser()
            try:
                result = delete_output_json(file_path)
                json_response(self, result, 200 if result.get("ok") else 400)
            except Exception as exc:
                json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 500)
            return
        text_response(self, "Not found", 404)

    def serve_static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists():
            text_response(self, "Not found", 404)
            return
        content_types = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
        }
        text = target.read_text(encoding="utf-8")
        text_response(self, text, 200, content_types.get(target.suffix, "text/plain"))

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8787"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Comment Dashboard running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
