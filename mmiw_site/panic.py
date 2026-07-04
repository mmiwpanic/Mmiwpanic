from __future__ import annotations
import uuid, hashlib
from fastapi import APIRouter, Depends, Request, HTTPException
from typing import List, Optional

from .db import connect, now_ts
from .schemas import (
    UserCreateIn, UserCreateOut,
    ContactIn, ContactOut,
    SafetyProfileIn, SafetyProfileOut,
    PanicTriggerIn, PanicTriggerOut, PanicDeliveryStatus,
    CheckinIn, LocationPingIn, LocationTrailOut, LocationPointOut,
)
from .auth import create_user, require_user
from .rate_limit import allow as rate_allow
from .notify import dispatch
from .ai_assist import structure_panic_note
from .settings import settings
from .task_logger import audit_log

router = APIRouter(tags=["panic"])


# ---------- User registration ----------

@router.post("/users", response_model=UserCreateOut, status_code=201)
def register_user(body: UserCreateIn):
    """Creates a new panic-button user. Returns a one-time API key.
    This key is the ONLY way to authenticate as this user — store it safely
    (e.g. in the device's keychain / secure storage on the client side)."""
    result = create_user(body.display_name)
    return UserCreateOut(**result)


# ---------- Contacts ----------

@router.post("/contacts", response_model=ContactOut, status_code=201)
def add_contact(body: ContactIn, user: dict = Depends(require_user)):
    contact_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        """INSERT INTO contacts (id, user_id, label, contact_type, destination, is_le, priority, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (contact_id, user["id"], body.label, body.contact_type, body.destination,
         1 if body.is_le else 0, body.priority, now_ts()),
    )
    conn.commit()
    conn.close()
    return ContactOut(id=contact_id, created_at=now_ts(), **body.model_dump())


@router.get("/contacts", response_model=List[ContactOut])
def list_contacts(user: dict = Depends(require_user)):
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM contacts WHERE user_id = ? ORDER BY priority ASC, created_at ASC",
        (user["id"],),
    ).fetchall()
    conn.close()
    return [ContactOut(**{**dict(r), "is_le": bool(r["is_le"])}) for r in rows]


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: str, user: dict = Depends(require_user)):
    conn = connect()
    cur = conn.execute(
        "DELETE FROM contacts WHERE id = ? AND user_id = ?", (contact_id, user["id"])
    )
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    if not deleted:
        raise HTTPException(404, "Contact not found")
    return None


# ---------- Safety profile (prep-mode) ----------

@router.put("/safety-profile", response_model=SafetyProfileOut)
def upsert_safety_profile(body: SafetyProfileIn, user: dict = Depends(require_user)):
    conn = connect()
    conn.execute(
        """INSERT INTO safety_profiles (
             user_id, full_name, description, vehicle, emergency_note, home_address,
             tracking_enabled, checkin_window_sec, location_retention_days,
             auto_delete_if_no_case, updated_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET
             full_name=excluded.full_name, description=excluded.description,
             vehicle=excluded.vehicle, emergency_note=excluded.emergency_note,
             home_address=excluded.home_address,
             tracking_enabled=excluded.tracking_enabled,
             checkin_window_sec=excluded.checkin_window_sec,
             location_retention_days=excluded.location_retention_days,
             auto_delete_if_no_case=excluded.auto_delete_if_no_case,
             updated_at=excluded.updated_at""",
        (user["id"], body.full_name, body.description, body.vehicle,
         body.emergency_note, body.home_address,
         1 if body.tracking_enabled else 0, body.checkin_window_sec,
         body.location_retention_days, 1 if body.auto_delete_if_no_case else 0,
         now_ts()),
    )
    conn.commit()
    conn.close()
    return SafetyProfileOut(updated_at=now_ts(), **body.model_dump())


@router.get("/safety-profile", response_model=SafetyProfileOut)
def get_safety_profile(user: dict = Depends(require_user)):
    conn = connect()
    row = conn.execute(
        "SELECT * FROM safety_profiles WHERE user_id = ?", (user["id"],)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "No safety profile set yet")
    d = dict(row)
    d["tracking_enabled"] = bool(d["tracking_enabled"])
    d["auto_delete_if_no_case"] = bool(d["auto_delete_if_no_case"])
    return SafetyProfileOut(**d)


# ---------- Panic trigger, check-in, and delivery ----------

def _ip_hash(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _get_profile(user_id: str) -> dict:
    conn = connect()
    row = conn.execute("SELECT * FROM safety_profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def _send_deliveries(panic_id: str, user: dict, profile: dict, structured_note: str,
                      lat: Optional[float], lng: Optional[float], include_le: bool) -> List[PanicDeliveryStatus]:
    """Sends the actual alert to contacts. Shared by both the immediate-send
    path (no countdown configured) and the deferred path (countdown expired
    with no check-in) so there is exactly one place this logic lives."""
    conn = connect()
    contact_rows = conn.execute(
        "SELECT * FROM contacts WHERE user_id = ? ORDER BY priority ASC", (user["id"],)
    ).fetchall()
    contacts = [dict(r) for r in contact_rows]
    recipients = [c for c in contacts if not c["is_le"] or include_le]

    subject = f"URGENT: Safety alert from {profile.get('full_name') or user['display_name']}"
    body_lines = [
        f"A personal safety alert was triggered by {profile.get('full_name') or user['display_name']}.",
        f"Message: {structured_note}",
    ]
    if lat is not None and lng is not None:
        body_lines.append(f"Approximate location: https://maps.google.com/?q={lat},{lng}")
    if profile.get("description"):
        body_lines.append(f"Description: {profile['description']}")
    if profile.get("vehicle"):
        body_lines.append(f"Vehicle: {profile['vehicle']}")
    if profile.get("emergency_note"):
        body_lines.append(f"Additional info: {profile['emergency_note']}")

    # If a location trail was recorded during the countdown, summarize it —
    # this is what actually delivers on "leave a trail" for the worst case.
    trail_rows = conn.execute(
        "SELECT lat, lng, recorded_at FROM location_points WHERE panic_event_id = ? ORDER BY recorded_at ASC",
        (panic_id,),
    ).fetchall()
    if trail_rows:
        body_lines.append(f"Location trail recorded ({len(trail_rows)} points):")
        last = trail_rows[-1]
        body_lines.append(f"Most recent point: https://maps.google.com/?q={last['lat']},{last['lng']}")

    message_body = "\n".join(body_lines)

    deliveries: List[PanicDeliveryStatus] = []
    for contact in recipients:
        result = dispatch(contact, subject, message_body)
        delivery_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO panic_deliveries (id, panic_event_id, contact_id, channel, status, error, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (delivery_id, panic_id, contact["id"], contact["contact_type"],
             "ok" if result.ok else "failed", result.error, now_ts()),
        )
        deliveries.append(PanicDeliveryStatus(
            contact_label=contact["label"], channel=contact["contact_type"],
            ok=result.ok, error=result.error,
        ))

    conn.execute("UPDATE panic_events SET status = 'sent' WHERE id = ?", (panic_id,))
    conn.commit()
    conn.close()
    return deliveries


@router.post("/panic/trigger", response_model=PanicTriggerOut, status_code=201)
def trigger_panic(body: PanicTriggerIn, request: Request, user: dict = Depends(require_user)):
    if not rate_allow(f"panic:{user['id']}", limit=settings.panic_rate_limit,
                       window_sec=settings.panic_rate_window_sec):
        audit_log(_ip_hash(request), "panic.trigger", "rate_limited", user_id=user["id"])
        raise HTTPException(status_code=429, detail="Too many panic triggers. Please wait before trying again.")

    profile = _get_profile(user["id"])
    structured_note = structure_panic_note(body.note or "")

    tracking_active = bool(profile.get("tracking_enabled"))
    checkin_window = profile.get("checkin_window_sec") or 90

    panic_id = str(uuid.uuid4())
    conn = connect()

    # If tracking is pre-authorized, this trigger starts a countdown instead
    # of sending immediately — the person gets checkin_window_sec to confirm
    # they're safe (or cancel) before contacts are actually notified. If
    # tracking is off, this behaves like before: send right away, no countdown.
    if tracking_active:
        status = "pending"
        deadline = now_ts() + checkin_window
    else:
        status = "sent"
        deadline = None

    conn.execute(
        """INSERT INTO panic_events (
             id, user_id, ip_hash, note, structured_note, lat, lng, status,
             checkin_deadline, checked_in_at, tracking_active, created_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (panic_id, user["id"], _ip_hash(request), body.note, structured_note,
         body.lat, body.lng, status, deadline, None,
         1 if tracking_active else 0, now_ts()),
    )
    conn.commit()
    conn.close()

    if tracking_active:
        # Deferred: don't notify contacts yet. The client is expected to call
        # /panic/checkin before the deadline, or /panic/location repeatedly
        # (which the "expire" sweep below will eventually resolve).
        deliveries: List[PanicDeliveryStatus] = []
        audit_log(_ip_hash(request), "panic.trigger", "pending_countdown",
                  user_id=user["id"], panic_event_id=panic_id, checkin_window_sec=checkin_window)
    else:
        deliveries = _send_deliveries(panic_id, user, profile, structured_note,
                                       body.lat, body.lng, body.include_le)
        audit_log(_ip_hash(request), "panic.trigger", "sent",
                  user_id=user["id"], panic_event_id=panic_id,
                  recipients=len(deliveries), le_included=body.include_le)

    return PanicTriggerOut(
        panic_event_id=panic_id,
        redirect=settings.panic_redirect,
        status=status,
        checkin_deadline=deadline,
        tracking_active=tracking_active,
        deliveries=deliveries,
    )


@router.post("/panic/checkin")
def checkin(body: CheckinIn, user: dict = Depends(require_user)):
    """The person confirming they're safe before the countdown expires.
    Cancels the pending send — contacts are never notified for this event."""
    conn = connect()
    row = conn.execute(
        "SELECT * FROM panic_events WHERE id = ? AND user_id = ?",
        (body.panic_event_id, user["id"]),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Panic event not found")

    if row["status"] != "pending":
        conn.close()
        return {"panic_event_id": body.panic_event_id, "status": row["status"],
                "note": "This event was already resolved (sent or previously checked in)."}

    conn.execute(
        "UPDATE panic_events SET status = 'checked_in_safe', checked_in_at = ? WHERE id = ?",
        (now_ts(), body.panic_event_id),
    )
    conn.commit()
    conn.close()
    audit_log("system", "panic.checkin", "confirmed_safe",
              user_id=user["id"], panic_event_id=body.panic_event_id)
    return {"panic_event_id": body.panic_event_id, "status": "checked_in_safe"}


@router.post("/panic/location")
def record_location(body: LocationPingIn, user: dict = Depends(require_user)):
    """Records one point of the location trail during an active (pending)
    panic event. Rejects points for events that aren't currently pending, so
    a stale client can't append to an already-resolved or expired event."""
    conn = connect()
    row = conn.execute(
        "SELECT * FROM panic_events WHERE id = ? AND user_id = ?",
        (body.panic_event_id, user["id"]),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Panic event not found")
    if row["status"] != "pending":
        conn.close()
        raise HTTPException(409, f"This panic event is no longer active (status={row['status']})")

    profile = _get_profile(user["id"])
    retention_days = profile.get("location_retention_days") or 30
    expires_at = now_ts() + (retention_days * 86400)

    point_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO location_points (id, panic_event_id, user_id, lat, lng, accuracy_m, recorded_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (point_id, body.panic_event_id, user["id"], body.lat, body.lng, body.accuracy_m, now_ts(), expires_at),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "point_id": point_id}


@router.get("/panic/{panic_event_id}/trail", response_model=LocationTrailOut)
def get_trail(panic_event_id: str, user: dict = Depends(require_user)):
    conn = connect()
    event_row = conn.execute(
        "SELECT * FROM panic_events WHERE id = ? AND user_id = ?", (panic_event_id, user["id"])
    ).fetchone()
    if not event_row:
        conn.close()
        raise HTTPException(404, "Panic event not found")

    points = conn.execute(
        "SELECT lat, lng, accuracy_m, recorded_at FROM location_points WHERE panic_event_id = ? ORDER BY recorded_at ASC",
        (panic_event_id,),
    ).fetchall()
    conn.close()
    return LocationTrailOut(
        panic_event_id=panic_event_id,
        status=event_row["status"],
        points=[LocationPointOut(**dict(p)) for p in points],
    )


@router.delete("/panic/{panic_event_id}/trail", status_code=204)
def delete_trail(panic_event_id: str, user: dict = Depends(require_user)):
    """Lets the person (or a case manager, in a future extension) delete a
    location trail on demand — the explicit 'go back and delete this if
    needed' control."""
    conn = connect()
    event_row = conn.execute(
        "SELECT * FROM panic_events WHERE id = ? AND user_id = ?", (panic_event_id, user["id"])
    ).fetchone()
    if not event_row:
        conn.close()
        raise HTTPException(404, "Panic event not found")

    conn.execute("DELETE FROM location_points WHERE panic_event_id = ?", (panic_event_id,))
    conn.commit()
    conn.close()
    audit_log("system", "panic.trail.delete", "manual", user_id=user["id"], panic_event_id=panic_event_id)
    return None


def process_expired_countdowns() -> int:
    """Finds all 'pending' panic events whose check-in deadline has passed
    and sends their deliveries. Meant to be called on a schedule (e.g. every
    10-15 seconds by a background task, or by a cron-style job in
    production) — it's a plain function, not tied to any specific scheduler,
    so it can be wired into whatever the deployment environment supports.
    Returns the number of events processed."""
    conn = connect()
    expired = conn.execute(
        "SELECT * FROM panic_events WHERE status = 'pending' AND checkin_deadline IS NOT NULL AND checkin_deadline <= ?",
        (now_ts(),),
    ).fetchall()
    conn.close()

    count = 0
    for row in expired:
        event = dict(row)
        conn2 = connect()
        user_row = conn2.execute("SELECT id, display_name, role FROM users WHERE id = ?", (event["user_id"],)).fetchone()
        conn2.close()
        if not user_row:
            continue
        user = dict(user_row)
        profile = _get_profile(user["id"])
        _send_deliveries(event["id"], user, profile, event["structured_note"],
                          event["lat"], event["lng"], include_le=False)
        audit_log("system", "panic.countdown_expired", "auto_sent",
                  user_id=user["id"], panic_event_id=event["id"])
        count += 1
    return count


def purge_expired_location_data() -> int:
    """Deletes location points whose retention period has passed, unless
    they're attached to a panic event linked to an active case (a future
    extension can add that case-linkage check; for now this purges anything
    past its expires_at). Meant to be run on a schedule, same as
    process_expired_countdowns. Returns the number of points deleted."""
    conn = connect()
    cur = conn.execute("DELETE FROM location_points WHERE expires_at <= ?", (now_ts(),))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    if deleted:
        audit_log("system", "location.purge", "expired", count=deleted)
    return deleted
