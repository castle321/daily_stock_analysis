# DSA → ECS → OpenClaw → Feishu

This small ECS-side service polls the GitHub Actions workflow and forwards successful Markdown report artifacts to an existing OpenClaw Feishu direct-message target.

## Flow

1. GitHub Actions runs `.github/workflows/daily_analysis.yml`.
2. The workflow uploads `analysis-reports-<run_number>` containing `reports/` and `logs/`.
3. ECS polls the workflow's completed runs every 120 seconds (configurable).
4. Only successful runs are considered. Only `reports/**/*.md` files are extracted; logs are never sent.
5. SQLite records delivered run IDs to prevent routine duplicate delivery.
6. OpenClaw CLI sends the report to the configured Feishu `open_id`.

No inbound ECS port, domain name, TLS certificate, webhook, or additional Feishu bot is required.

## ECS installation

Prerequisites: Python 3.10+, an already working OpenClaw CLI configured for Feishu, and a GitHub fine-grained PAT.

Create a fine-grained PAT for this repository with **Actions: Read-only** permission (Metadata read access is included). Do not put this token in the GitHub repository; it belongs only on ECS.

Copy this directory to `/opt/dsa-monitor`, then:

```bash
sudo mkdir -p /opt/dsa-monitor
# Copy monitor.py, .env.example and dsa-monitor.service into /opt/dsa-monitor
sudo cp /opt/dsa-monitor/.env.example /opt/dsa-monitor/.env
sudo nano /opt/dsa-monitor/.env
sudo chmod 600 /opt/dsa-monitor/.env
sudo chmod 755 /opt/dsa-monitor/monitor.py
```

Set `GITHUB_TOKEN` and `FEISHU_OPEN_ID`. Confirm the OpenClaw binary path with `command -v openclaw`; update `OPENCLAW_BIN` if needed. The service runs as root so it can reuse the existing `/root` OpenClaw configuration.

Install and start:

```bash
sudo cp /opt/dsa-monitor/dsa-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dsa-monitor
sudo systemctl status dsa-monitor --no-pager
sudo journalctl -u dsa-monitor -f
```

## Validation

1. In GitHub, open **Actions → 每日股票分析 → Run workflow** and run a test.
2. Confirm the run concludes successfully and has an `analysis-reports-<run_number>` artifact.
3. Watch `journalctl -u dsa-monitor -f`; the report should arrive in the configured Feishu chat.
4. Restarting the service should not normally resend a run already recorded as `sent`.

## Delivery semantics and troubleshooting

- Poll interval defaults to 120 seconds; minimum accepted value is 30 seconds.
- Failed API, download, or OpenClaw send operations are logged and retried on later polls.
- Failed workflow runs are intentionally ignored.
- Runs without a report artifact are retried on later polls; an artifact containing no Markdown report is recorded as `no_report`.
- SQLite state is stored in `data/monitor.sqlite3`; preserve it across deployments to retain de-duplication.
- Delivery is **at-least-once**, not exactly-once: if the process crashes after Feishu accepts a message but before SQLite records success, a duplicate can occur after restart.
- Reports longer than the configured message size are split at line boundaries and sent in numbered parts.
- If GitHub returns 401/403, check PAT expiry and repository Actions read permission.
- If OpenClaw sends fail, run the same `openclaw message send --channel feishu --target <open_id> --message "test"` command as root and verify `OPENCLAW_BIN` and `OPENCLAW_HOME`.
