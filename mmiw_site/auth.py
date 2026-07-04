from __future__ import annotations
"""
Kept for backward compatibility with panic.py's imports. The real
implementation now lives in access.py, which adds role-based permissions
for the case/tip/evidence side of the app. This module just re-exports the
pieces panic.py needs so nothing there had to change.
"""
from .access import create_user, get_user_by_key, require_user, optional_user
