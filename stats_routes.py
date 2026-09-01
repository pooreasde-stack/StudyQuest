# -*- coding: utf-8 -*-
"""
stats_routes.py
================================================================
یک اندپوینتِ آماریِ سبک و فقط-خواندنی (read-only): تعداد کل کاربرانِ
ثبت‌نام‌شده + تعداد کاربرانی که در ۷۲ ساعتِ گذشته «فعال» بوده‌اند.

طبق درخواستِ صریح، مثل challenges_routes.py یک فایلِ کاملاً مجزاست و
هیچ‌کدام از فایل‌های users.json/progress.json را تغییر نمی‌دهد —
فقط می‌خواند و جمع می‌زند.

«فعال» یعنی حداقل یکی از این دو در ۷۲ ساعتِ اخیر رخ داده باشد:
  ۱) پروفایلِ کاربر ثبت‌نام/سینک شده باشد (users.json → updatedAt)
  ۲) کاربر پیشرفتِ مطالعه ثبت کرده باشد (progress.json → receivedAt)

استفاده در server.py:
    import stats_routes
    stats_routes.init_stats(BASE_DIR)
    app.register_blueprint(stats_routes.stats_bp)
"""
import json
import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify

stats_bp = Blueprint("stats_bp", __name__)

_USERS_FILE = None
_PROGRESS_FILE = None
ACTIVE_WINDOW_HOURS = 72


def init_stats(base_dir):
    """باید یک‌بار، هنگامِ راه‌اندازیِ سرور (در server.py)، صدا زده شود."""
    global _USERS_FILE, _PROGRESS_FILE
    _USERS_FILE = os.path.join(base_dir, "users.json")
    _PROGRESS_FILE = os.path.join(base_dir, "progress.json")


def _read_json_list(path):
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _parse_iso(s):
    """timestampهای پروژه گاهی با پسوندِ 'Z' ذخیره می‌شوند (UTC) — این
    فرمت مستقیماً با datetime.fromisoformat سازگار نیست، برای همین
    قبلش 'Z' با '+00:00' جایگزین می‌شود."""
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


@stats_bp.route("/api/stats/users", methods=["GET"])
def users_stats():
    users = _read_json_list(_USERS_FILE)
    progress = _read_json_list(_PROGRESS_FILE)

    total_users = len(users)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=ACTIVE_WINDOW_HOURS)

    active_ids = set()

    for u in users:
        uid = u.get("userId")
        dt = _parse_iso(u.get("updatedAt")) or _parse_iso(u.get("createdAt"))
        if uid and dt and dt >= cutoff:
            active_ids.add(uid)

    for p in progress:
        uid = p.get("userId")
        dt = _parse_iso(p.get("receivedAt")) or _parse_iso(p.get("clientTimestamp"))
        if uid and dt and dt >= cutoff:
            active_ids.add(uid)

    return jsonify({
        "status": "success",
        "data": {
            "totalUsers": total_users,
            "activeUsersLast72h": len(active_ids),
            "windowHours": ACTIVE_WINDOW_HOURS,
            "generatedAt": now.isoformat().replace("+00:00", "Z"),
        },
        "message": "آمار کاربران دریافت شد",
    })
