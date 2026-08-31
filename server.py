# -*- coding: utf-8 -*-
"""
server.py — سرور Flask برای StudyQuest
================================================================
نسخه‌ی با لاگ‌گیری کامل برای پیدا کردن خطاهای ۵۰۳
"""

import json
import os
import threading
import sys
from datetime import datetime

from flask import Flask, request, jsonify

# ================================================================
# لاگ‌گیری در ابتدای اجرا
# ================================================================
print("=" * 60)
print("🚀 Starting server.py ...")
print(f"📂 Current working directory: {os.getcwd()}")
print(f"📁 Script directory: {os.path.dirname(os.path.abspath(__file__))}")
print("=" * 60)

# ================================================================
# تنظیمات پایه
# ================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.json")
EVENTS_FILE = os.path.join(BASE_DIR, "events.json")
PROGRESS_FILE = os.path.join(BASE_DIR, "progress.json")
LEADERBOARD_FILE_TEMPLATE = os.path.join(BASE_DIR, "leaderboard_{event_id}.json")
VERSION_FILE = os.path.join(BASE_DIR, "version.txt")

app = Flask(__name__)
# نکته‌ی مهم (فیکس ۵۰۴ روی state/checkin/chat):
# قبلاً اینجا threading.Lock() بود که "reentrant" نیست — یعنی اگر یک
# تابع که از قبل قفل را گرفته، دوباره (حتی غیرمستقیم، از طریق تابع
# دیگری مثل _user_display که خودش _read_json و در نتیجه خودِ همین قفل
# را صدا می‌زند) بخواهد همان قفل را بگیرد، همان‌جا برای همیشه گیر
# می‌کند. چون Flask با threaded=True هر ریکوئست را روی نخ جدا اجرا
# می‌کند، آن یک نخِ گیرکرده هیچ‌وقت آزاد نمی‌شود و بقیه‌ی ریکوئست‌هایی که
# به همین قفل نیاز دارند (state/checkin/chat/proof) تا ابد منتظر
# می‌مانند تا این‌که تونل بعد از مدتی 504 برمی‌گرداند — /ping و
# /api/version چون اصلاً به این قفل نیاز ندارند سالم می‌مانند، دقیقاً
# همان علامتی که مشاهده شد.
# با RLock همان کد قبلی (با همان syntax با with) کار می‌کند، ولی اگر
# یک نخ دوباره (حتی چند بار) همان قفل را بخواهد بگیرد، بلافاصله
# اجازه می‌گیرد (چون شمارنده‌ی داخلی RLock تشخیص می‌دهد همان نخ است) و
# فقط وقتی واقعاً برای همه آزاد می‌شود که تمام لایه‌های with تمام شوند.
# این یعنی هر جای کد (الان یا در آینده) که به‌اشتباه قفل را دوباره از
# همان نخ بگیرد، دیگر دِدلاک نمی‌شود.
_file_lock = threading.RLock()
SERVER_START_TIME = datetime.utcnow()

print("✅ Flask app created")
print(f"📁 BASE_DIR: {BASE_DIR}")
print(f"📄 USERS_FILE: {USERS_FILE}")
print(f"📄 EVENTS_FILE: {EVENTS_FILE}")
print(f"📄 PROGRESS_FILE: {PROGRESS_FILE}")
print(f"📄 VERSION_FILE: {VERSION_FILE}")

# ================================================================
# تلاش برای import challenges_routes
# ================================================================
print("\n🔄 Attempting to import challenges_routes...")
try:
    import challenges_routes
    print("✅ challenges_routes imported successfully")
    CHALLENGES_AVAILABLE = True
except Exception as e:
    print(f"❌ Failed to import challenges_routes: {e}")
    import traceback
    traceback.print_exc()
    print("⚠️ Continuing without challenges_routes")
    CHALLENGES_AVAILABLE = False
    challenges_routes = None

# ================================================================
# توابع کمکی
# ================================================================

def _read_json(path, default):
    """خواندن فایل JSON با لاگ"""
    print(f"📖 Reading file: {path}")
    with _file_lock:
        if not os.path.exists(path):
            print(f"⚠️ File not found, creating: {path}")
            _write_json_unlocked(path, default)
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    print(f"⚠️ File empty, using default: {path}")
                    return default
                data = json.loads(content)
                print(f"✅ File read successfully: {path} ({len(data)} items)")
                return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"❌ Error reading JSON from {path}: {e}")
            return default

def _write_json_unlocked(path, data):
    """نوشتن فایل JSON با لاگ"""
    print(f"✏️ Writing file: {path}")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    print(f"✅ File written: {path}")

def _write_json(path, data):
    with _file_lock:
        _write_json_unlocked(path, data)

def _success(data=None, message=None, status_code=200):
    body = {"status": "success"}
    if data is not None:
        body["data"] = data
    if message is not None:
        body["message"] = message
    return jsonify(body), status_code

def _error(message, status_code=400):
    return jsonify({"status": "error", "message": message}), status_code

# ================================================================
# اتصال Blueprint چالش‌ها (اگر import موفق بود)
# ================================================================
if CHALLENGES_AVAILABLE:
    try:
        print("\n🔧 Binding core helpers to challenges_routes...")
        challenges_routes._bind_core_helpers(
            read_json=_read_json,
            write_json_unlocked=_write_json_unlocked,
            file_lock=_file_lock,
            success=_success,
            error=_error,
        )
        app.register_blueprint(challenges_routes.challenges_bp)
        print("✅ challenges_routes blueprint registered")
    except Exception as e:
        print(f"❌ Error registering challenges blueprint: {e}")
        import traceback
        traceback.print_exc()
        CHALLENGES_AVAILABLE = False
else:
    print("⚠️ challenges_routes not available, skipping blueprint registration")

# ================================================================
# مسیرهای اصلی
# ================================================================

REQUIRED_USER_FIELDS = [
    "firstName", "lastName", "birthYear", "gender", "educationLevel", "field"
]

def _find_user_index(users, user_id):
    for i, u in enumerate(users):
        if u.get("userId") == user_id:
            return i
    return -1

@app.route("/api/register", methods=["POST"])
def register_user():
    print("📥 POST /api/register")
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    if not user_id:
        return _error("فیلد userId الزامی است", 400)
    users = _read_json(USERS_FILE, [])
    record = {
        "userId": user_id,
        "firstName": body.get("firstName", ""),
        "lastName": body.get("lastName", ""),
        "phoneNumber": body.get("phoneNumber", ""),
        "birthDay": body.get("birthDay"),
        "birthMonth": body.get("birthMonth"),
        "birthYear": body.get("birthYear"),
        "gender": body.get("gender", ""),
        "educationLevel": body.get("educationLevel", ""),
        "field": body.get("field", ""),
        "updatedAt": datetime.utcnow().isoformat() + "Z",
    }
    with _file_lock:
        idx = _find_user_index(users, user_id)
        if idx >= 0:
            record["createdAt"] = users[idx].get("createdAt", record["updatedAt"])
            users[idx] = record
        else:
            record["createdAt"] = record["updatedAt"]
            users.append(record)
        _write_json_unlocked(USERS_FILE, users)
    return _success(data=record, message="ثبت‌نام با موفقیت ذخیره شد")

@app.route("/api/user/sync", methods=["POST"])
def sync_user():
    return register_user()

@app.route("/api/user/<user_id>", methods=["GET"])
def get_user(user_id):
    print(f"📥 GET /api/user/{user_id}")
    users = _read_json(USERS_FILE, [])
    idx = _find_user_index(users, user_id)
    if idx < 0:
        return _error("کاربری با این شناسه پیدا نشد", 404)
    return _success(data=users[idx])

@app.route("/api/user/<user_id>", methods=["PUT"])
def update_user(user_id):
    print(f"📥 PUT /api/user/{user_id}")
    body = request.get_json(silent=True) or {}
    users = _read_json(USERS_FILE, [])
    with _file_lock:
        idx = _find_user_index(users, user_id)
        now = datetime.utcnow().isoformat() + "Z"
        if idx < 0:
            record = {"userId": user_id, "createdAt": now}
            users.append(record)
            idx = len(users) - 1
        else:
            record = users[idx]
        for field in REQUIRED_USER_FIELDS + ["phoneNumber", "birthDay", "birthMonth"]:
            if field in body:
                record[field] = body[field]
        record["updatedAt"] = now
        users[idx] = record
        _write_json_unlocked(USERS_FILE, users)
    return _success(data=record, message="تغییرات با موفقیت ذخیره شد")

@app.route("/api/events", methods=["GET"])
def get_events():
    print("📥 GET /api/events")
    events = _read_json(EVENTS_FILE, [])
    return _success(data=events)

@app.route("/api/leaderboard/<event_id>", methods=["GET"])
def get_leaderboard(event_id):
    print(f"📥 GET /api/leaderboard/{event_id}")
    path = LEADERBOARD_FILE_TEMPLATE.format(event_id=event_id)
    leaderboard = _read_json(path, [])
    return _success(data=leaderboard)

@app.route("/api/progress", methods=["POST"])
def submit_progress():
    print("📥 POST /api/progress")
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    event_id = body.get("eventId")
    if not user_id or not event_id:
        return _error("فیلدهای userId و eventId الزامی‌اند", 400)
    entry = {
        "userId": user_id,
        "eventId": event_id,
        "minutes": body.get("minutes", 0),
        "consistencyDays": body.get("consistencyDays", 0),
        "clientTimestamp": body.get("clientTimestamp"),
        "receivedAt": datetime.utcnow().isoformat() + "Z",
    }
    with _file_lock:
        progress = _read_json(PROGRESS_FILE, [])
        is_duplicate = False
        if entry["clientTimestamp"]:
            for p in progress:
                if (p.get("userId") == user_id and
                    p.get("eventId") == event_id and
                    p.get("clientTimestamp") == entry["clientTimestamp"]):
                    is_duplicate = True
                    break
        if not is_duplicate:
            progress.append(entry)
            _write_json_unlocked(PROGRESS_FILE, progress)
    return _success(data=entry, message="پیشرفت ثبت شد")

def _read_version_file():
    if not os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write("1.0.0")
        return "1.0.0"
    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        return f.read().strip() or "1.0.0"

def _version_tuple(v):
    parts = []
    for p in v.strip().split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)

@app.route("/api/version", methods=["GET"])
def check_version():
    print("📥 GET /api/version")
    current_version = request.args.get("current_version", "0.0.0")
    latest_version = _read_version_file()
    update_required = _version_tuple(current_version) < _version_tuple(latest_version)
    message = "نسخه‌ی جدید منتشر شد!" if update_required else "برنامه به‌روز است"
    return jsonify({
        "version": latest_version,
        "update_required": update_required,
        "message": message,
    })

@app.route("/ping", methods=["GET"])
def ping():
    print("📥 GET /ping")
    return jsonify({"status": "alive"})

@app.route("/", methods=["GET"])
def root():
    uptime_seconds = (datetime.utcnow() - SERVER_START_TIME).total_seconds()
    endpoints = [
        "POST /api/register",
        "POST /api/user/sync",
        "GET  /api/user/<userId>",
        "PUT  /api/user/<userId>",
        "GET  /api/events",
        "GET  /api/leaderboard/<eventId>",
        "POST /api/progress",
        "GET  /api/version",
        "GET  /ping",
    ]
    if CHALLENGES_AVAILABLE:
        endpoints.extend([
            "GET  /api/challenges/state",
            "POST /api/challenges/checkin",
            "POST /api/challenges/chat",
            "POST /api/challenges/proof",
            "POST /api/challenges/chat-image",
            "GET  /api/challenges/chat-image-file/<filename>",
            "POST /api/challenges/live-reading/start",
            "POST /api/challenges/live-reading/stop",
        ])
    return jsonify({
        "service": "StudyQuest Server",
        "status": "running",
        "server_time_utc": datetime.utcnow().isoformat() + "Z",
        "uptime_seconds": round(uptime_seconds, 1),
        "endpoints": endpoints,
    })

@app.errorhandler(404)
def not_found(_e):
    return _error("مسیر یافت نشد", 404)

@app.errorhandler(405)
def method_not_allowed(_e):
    return _error("متد HTTP پشتیبانی نمی‌شود", 405)

@app.errorhandler(500)
def server_error(_e):
    return _error("خطای داخلی سرور", 500)

# ================================================================
# اجرا
# ================================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🔁 Running __main__ block...")
    print("=" * 60)

    try:
        print("\n📄 Ensuring base files exist...")
        _read_json(USERS_FILE, [])
        _read_json(EVENTS_FILE, [])
        _read_json(PROGRESS_FILE, [])

        if not os.path.exists(VERSION_FILE):
            with open(VERSION_FILE, "w", encoding="utf-8") as f:
                f.write("1.0.0")
            print("✅ version.txt created")

        if CHALLENGES_AVAILABLE:
            print("🔄 Calling challenges_routes.ensure_initial_files()...")
            challenges_routes.ensure_initial_files()
            print("✅ challenges_routes files ensured")

            print("🤖 Starting bot study simulation...")
            challenges_routes.start_bot_simulation()

        print("✅ All files ready.")

    except Exception as e:
        print(f"\n❌ ERROR during startup: {e}")
        import traceback
        traceback.print_exc()
        print("\n⚠️ Server will exit with error.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("🚀 Starting Flask server on 0.0.0.0:5000")
    print("=" * 60)

    # threaded=True: مهم — Flask dev server به‌صورت پیش‌فرض تک‌نخی است،
    # یعنی اگر یک درخواست (به هر دلیلی، حتی یک باگ آینده) گیر کند/کند
    # شود، کل سرور از جمله /ping برای همه‌ی کاربران بی‌پاسخ می‌ماند و
    # تونل ۵۰۳ برمی‌گرداند. با threaded=True هر درخواست روی نخ جدا اجرا
    # می‌شود و گیر کردن یکی، بقیه را قفل نمی‌کند.
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
