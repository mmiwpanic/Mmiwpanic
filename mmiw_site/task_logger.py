# mmiw_site/task_logger.py
from datetime import datetime
import json
import os
from pathlib import Path

LOG_FILE = os.getenv("AUDIT_LOG_FILE", "audit.log")

def log(event: str, message: str, **fields):
    """
    Generic structured logger.
    - event: short event type ("AUDIT", "ESCALATION", "ALERT", etc.)
    - message: brief description or action
    - **fields: any extra context (ip, status, outcome, user, token_ok, etc.)
    """
    entry = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "event": event,
        "message": message,
    }
    if fields:
        entry.update(fields)

    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry

def audit_log(ip: str, action: str, outcome: str, **fields):
    """Convenience wrapper for audit entries."""
    return log(
        event="AUDIT",
        message=action,
        ip=ip,
        outcome=outcome,
        **fields
    )
