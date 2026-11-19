#!/usr/bin/env python3
"""Poll GitHub Actions artifacts and deliver Markdown reports through OpenClaw."""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
LOG_DIR = Path(os.getenv("LOG_DIR", str(ROOT / "logs")))
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "monitor.sqlite3")))
API_BASE = "https://api.github.com"
OWNER = os.environ["GITHUB_OWNER"]
REPO = os.environ["GITHUB_REPO"]
TOKEN = os.environ["GITHUB_TOKEN"]
OPEN_ID = os.environ["FEISHU_OPEN_ID"]
OPENCLAW_BIN = os.getenv("OPENCLAW_BIN", "openclaw")
CHANNEL = os.getenv("OPENCLAW_CHANNEL", "feishu")
POLL_SECONDS = max(30, int(os.getenv("POLL_INTERVAL_SECONDS", "120")))
MAX_MESSAGE_CHARS = max(1000, int(os.getenv("MAX_MESSAGE_CHARS", "3500")))
WORKFLOW_FILE = os.getenv("GITHUB_WORKFLOW_FILE", "daily_analysis.yml")
RUNS_PER_POLL = min(100, max(10, int(os.getenv("RUNS_PER_POLL", "100"))))

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "monitor.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("dsa-monitor")


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deliveries (
            run_id INTEGER PRIMARY KEY,
            run_number INTEGER NOT NULL,
            artifact_id INTEGER,
            status TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    return conn


def api_request(path: str, *, binary: bool = False):
    url = path if path.startswith("https://") else API_BASE + path
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dsa-openclaw-artifact-monitor",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read()
            return data if binary else json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {body}") from exc


def list_runs():
    workflow = urllib.parse.quote(WORKFLOW_FILE, safe="")
    path = (
        f"/repos/{OWNER}/{REPO}/actions/workflows/{workflow}/runs"
        f"?per_page={RUNS_PER_POLL}&status=completed"
    )
    return api_request(path).get("workflow_runs", [])


def list_artifacts(run_id: int):
    path = f"/repos/{OWNER}/{REPO}/actions/runs/{run_id}/artifacts?per_page=100"
    return api_request(path).get("artifacts", [])


def download_artifact(artifact_id: int) -> bytes:
    path = f"/repos/{OWNER}/{REPO}/actions/artifacts/{artifact_id}/zip"
    return api_request(path, binary=True)


def extract_markdown(archive: bytes):
    reports = []
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Only Markdown reports from reports/; never forward logs or arbitrary files.
            normalized = info.filename.replace("\\", "/")
            parts = normalized.split("/")
            if "reports" not in parts or not normalized.lower().endswith(".md"):
                continue
            if info.file_size > 2_000_000:
                log.warning("Skip oversized report: %s (%s bytes)", normalized, info.file_size)
                continue
            content = zf.read(info).decode("utf-8-sig", errors="replace").strip()
            if content:
                reports.append((Path(normalized).name, content))
    reports.sort(key=lambda item: item[0])
    return reports


def split_message(text: str, limit: int):
    """Split at line boundaries when possible; never silently truncate content."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


def send_feishu(message: str):
    command = [
        OPENCLAW_BIN, "message", "send",
        "--channel", CHANNEL,
        "--target", OPEN_ID,
        "--message", message,
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=90,
        env={**os.environ, "HOME": os.getenv("OPENCLAW_HOME", "/root")},
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"OpenClaw send failed ({result.returncode}): "
            f"{(result.stderr or result.stdout)[-1500:]}"
        )
    log.info("OpenClaw accepted a Feishu message (%d chars)", len(message))


def deliver_run(run, conn):
    run_id = int(run["id"])
    run_number = int(run["run_number"])
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT status FROM deliveries WHERE run_id=?", (run_id,)
    ).fetchone()
    if existing and existing[0] in ("sent", "no_report"):
        return

    artifacts = list_artifacts(run_id)
    artifacts = [
        item for item in artifacts
        if not item.get("expired") and item.get("name", "").startswith("analysis-reports-")
    ]
    if not artifacts:
        log.info("Run %s has no report artifact yet; will retry next poll", run_id)
        return

    artifact = max(artifacts, key=lambda item: int(item.get("id", 0)))
    artifact_id = int(artifact["id"])
    archive = download_artifact(artifact_id)
    reports = extract_markdown(archive)
    if not reports:
        conn.execute(
            """INSERT INTO deliveries(run_id,run_number,artifact_id,status,detail,created_at,updated_at)
               VALUES(?,?,?,'no_report','Artifact contains no reports/*.md',?,?)
               ON CONFLICT(run_id) DO UPDATE SET artifact_id=excluded.artifact_id,
               status='no_report',detail=excluded.detail,updated_at=excluded.updated_at""",
            (run_id, run_number, artifact_id, now, now),
        )
        conn.commit()
        log.warning("Run %s artifact %s contains no Markdown report", run_id, artifact_id)
        return

    header = (
        f"📊 DSA 分析报告\n"
        f"仓库：{OWNER}/{REPO}\n"
        f"运行：#{run_number}（run_id={run_id}）\n"
        f"完成时间：{run.get('updated_at', '未知')}\n"
    )
    body = "\n\n---\n\n".join(
        f"## {filename}\n\n{content}" for filename, content in reports
    )
    messages = split_message(header + "\n" + body, MAX_MESSAGE_CHARS)
    log.info("Deliver run %s: %d report(s), %d message part(s)", run_id, len(reports), len(messages))

    # At-least-once delivery: a process crash after Feishu accepts a message but
    # before SQLite commits may cause that part to be sent again on restart.
    for index, message in enumerate(messages, start=1):
        prefix = f"（第 {index}/{len(messages)} 段）\n" if len(messages) > 1 else ""
        send_feishu(prefix + message)

    conn.execute(
        """INSERT INTO deliveries(run_id,run_number,artifact_id,status,detail,created_at,updated_at)
           VALUES(?,?,?,'sent',?,?,?)
           ON CONFLICT(run_id) DO UPDATE SET artifact_id=excluded.artifact_id,
           status='sent',detail=excluded.detail,updated_at=excluded.updated_at""",
        (run_id, run_number, artifact_id, f"{len(reports)} report(s), {len(messages)} part(s)", now, now),
    )
    conn.commit()
    log.info("Run %s delivered successfully", run_id)


def poll_once(conn):
    runs = list_runs()
    candidates = [
        run for run in runs
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    # Oldest first so a burst of completed workflows is delivered in order.
    candidates.sort(key=lambda item: item.get("updated_at", ""))
    for run in candidates:
        try:
            deliver_run(run, conn)
        except Exception:
            log.exception("Failed to process workflow run %s; it will be retried", run.get("id"))


def main():
    log.info(
        "Monitor started: repo=%s/%s workflow=%s interval=%ss target=%s",
        OWNER, REPO, WORKFLOW_FILE, POLL_SECONDS, OPEN_ID,
    )
    with db_connect() as conn:
        while True:
            started = time.monotonic()
            try:
                poll_once(conn)
            except Exception:
                log.exception("Polling failed; will retry")
            elapsed = time.monotonic() - started
            time.sleep(max(5, POLL_SECONDS - elapsed))


if __name__ == "__main__":
    main()
