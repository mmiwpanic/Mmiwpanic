from __future__ import annotations
"""
Access control model for case/tip/evidence data.

Roles (stored on the user record, set only by an admin — never self-assigned):
  - "public"    : default role for anyone who registers. Can browse public
                  cases, submit tips, and (if granted case_access) manage
                  a specific case as family.
  - "le"        : law enforcement / tribal police. Can see le_only cases and
                  submit LE requests with elevated trust, but does NOT get
                  blanket access to every case — case_access grants are still
                  used for anything beyond the public_level visibility rule.
  - "moderator" : can review/verify tips, change case status, grant case_access
                  to family members.
  - "admin"     : full access, including changing anyone's role.

Case-level access (case_access table):
  A user can be explicitly granted a role on a SPECIFIC case, independent of
  their global role. This is how a family member gets edit rights on their
  own loved one's case without becoming a site-wide moderator. access_role is
  one of: "family_editor", "family_viewer".

Visibility rule for GET /cases and GET /cases/{id} (enforced in main.py):
  - public_level == "public"   -> visible to everyone, including anonymous
  - public_level == "partners" -> visible to logged-in users with role
                                   le/moderator/admin, or explicit case_access
  - public_level == "le_only"  -> visible only to role le/moderator/admin,
                                   or explicit case_access

Nothing here is visible-by-default to an anonymous caller except public_level
== "public" cases. This is the fix for the previous version, where every
case was returned to every caller regardless of public_level.
"""
import secrets, hashlib, uuid
from typing import Optional
from fastapi import Header, HTTPException
from .db import connect, now_ts

VALID_ROLES = {"public", "le", "moderator", "admin"}
VALID_CASE_ACCESS_ROLES = {"family_editor", "family_viewer"}


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_user(display_name: str, role: str = "public") -> dict:
    """Creates a new user and returns their plaintext API key ONCE.
    role defaults to 'public' — elevated roles (le/moderator/admin) must be
    granted afterward by an existing admin via set_user_role(), never at
    self-registration time. This prevents anyone from just claiming to be LE."""
    if role not in VALID_ROLES:
        role = "public"
    user_id = str(uuid.uuid4())
    api_key = secrets.token_urlsafe(32)
    key_hash = _hash_key(api_key)
    conn = connect()
    conn.execute(
        "INSERT INTO users (id, api_key_hash, display_name, role, created_at) VALUES (?,?,?,?,?)",
        (user_id, key_hash, display_name, role, now_ts()),
    )
    conn.commit()
    conn.close()
    return {"user_id": user_id, "api_key": api_key, "role": role}


def get_user_by_key(api_key: str) -> Optional[dict]:
    if not api_key:
        return None
    key_hash = _hash_key(api_key)
    conn = connect()
    row = conn.execute(
        "SELECT id, display_name, role FROM users WHERE api_key_hash = ?", (key_hash,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


async def require_user(x_api_key: str = Header(..., alias="X-API-Key")) -> dict:
    """Use for endpoints that require ANY logged-in user (panic, contacts, etc.)."""
    user = get_user_by_key(x_api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return user


async def optional_user(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> Optional[dict]:
    """Use for endpoints that behave differently for logged-in vs anonymous
    callers but don't require login (e.g. GET /cases, which filters results
    based on who's asking rather than rejecting anonymous callers outright)."""
    if not x_api_key:
        return None
    return get_user_by_key(x_api_key)


def require_role(user: Optional[dict], allowed_roles: set[str]) -> None:
    """Raises 403 if the user's global role isn't in allowed_roles.
    Call this explicitly inside an endpoint after resolving the user via
    require_user or optional_user."""
    if not user or user.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions for this action")


def get_case_access_role(user_id: str, case_id: str) -> Optional[str]:
    conn = connect()
    row = conn.execute(
        "SELECT access_role FROM case_access WHERE user_id = ? AND case_id = ?",
        (user_id, case_id),
    ).fetchone()
    conn.close()
    return row["access_role"] if row else None


def can_view_case(user: Optional[dict], case: dict) -> bool:
    """Central visibility rule — used by both list and detail endpoints so
    the rule can never drift between the two."""
    level = case.get("public_level", "public")
    if level == "public":
        return True
    if not user:
        return False
    if user.get("role") in ("le", "moderator", "admin"):
        return True
    if get_case_access_role(user["id"], case["id"]):
        return True
    return False


def can_edit_case(user: Optional[dict], case: dict) -> bool:
    if not user:
        return False
    if user.get("role") in ("moderator", "admin"):
        return True
    access_role = get_case_access_role(user["id"], case["id"])
    return access_role == "family_editor"


def grant_case_access(case_id: str, target_user_id: str, access_role: str, granted_by_user_id: str) -> str:
    if access_role not in VALID_CASE_ACCESS_ROLES:
        raise ValueError(f"invalid access_role: {access_role}")
    grant_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        """INSERT INTO case_access (id, case_id, user_id, access_role, granted_by, created_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(case_id, user_id) DO UPDATE SET
             access_role=excluded.access_role, granted_by=excluded.granted_by, created_at=excluded.created_at""",
        (grant_id, case_id, target_user_id, access_role, granted_by_user_id, now_ts()),
    )
    conn.commit()
    conn.close()
    return grant_id


def set_user_role(target_user_id: str, new_role: str) -> None:
    if new_role not in VALID_ROLES:
        raise ValueError(f"invalid role: {new_role}")
    conn = connect()
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, target_user_id))
    conn.commit()
    conn.close()
