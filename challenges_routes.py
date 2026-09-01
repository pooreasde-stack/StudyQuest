# -*- coding: utf-8 -*-
"""
 — بخش «چالش‌ها»ی StudyQuest
================================================================
این فایل به‌صورت یک Blueprint مستقل نوشته شده تا server.py دست‌نخورده
بماند (فقط دو خط import و register_blueprint به آن اضافه می‌شود).

اصول این فایل دقیقاً هم‌راستا با server.py است:
    - بدون دیتابیس؛ همه‌چیز در فایل‌های JSON کنار همین فایل.
    - نوشتن اتمیک (`.tmp` + os.replace) با قفل سراسری مشترک.
    - سرور هر ۵:۳۰ ساعت ری‌استارت می‌شود و هیچ چیزی در حافظه‌ی پردازش
      نگه داشته نمی‌شود — فاز جلسه‌ی زنده هر بار از روی ساعت محاسبه
      می‌شود، نه از روی یک state machine در RAM.

فرض‌های طراحی (چون در دیتای ورودی مشخص نشده بودند، این‌جا مستندشان
می‌کنم تا بعداً راحت قابل تنظیم باشند):
    - آستانه‌ی «روز کامل» ۳۰ دقیقه مطالعه و «روز نصفه» هر مقدار >۰
      دقیقه است (FULL_DAY_MINUTES / PARTIAL_DAY_MINUTES پایین‌تر).
    - هفته از شنبه شروع می‌شود.
    - فرمول XP هفتگی = دقیقه × ۲ (WEEKLY_XP_PER_MINUTE) — فقط یک
      عدد شروع منطقی است، بعداً به‌راحتی قابل تغییر.
    - چون endpoint ی برای ثبت «دقیقه‌ی مطالعه‌ی روزانه» جداگانه
      تعریف نشده، وضعیت full/partial/miss هر روز از تجمیع رکوردهای
      همان کاربر در progress.json (که از قبل توسط POST /api/progress
      پر می‌شود) محاسبه می‌شود.
"""

import base64
import binascii
import hashlib
import json
import os
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, time as dtime

from flask import Blueprint, request, jsonify, send_from_directory

challenges_bp = Blueprint("challenges", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STREAK_FILE = os.path.join(BASE_DIR, "challenges_streak.json")
LIVE_SESSION_FILE = os.path.join(BASE_DIR, "live_session.json")
SCHEDULE_FILE = os.path.join(BASE_DIR, "session_schedule.json")
PROGRESS_FILE = os.path.join(BASE_DIR, "progress.json")
USERS_FILE = os.path.join(BASE_DIR, "users.json")
# «در حال مطالعه‌ی زنده» — نگاشتِ userId -> {startedAt, endsAt}. این فایل
# جدا از live_session.json (که فقط برای takeover تمام‌صفحه‌ی «هم‌خوان» با
# فازِ زمان‌بندی‌شده است) نگه داشته می‌شود چون این ویژگی کاملاً مستقل و
# سراسری است: هر کاربری هر وقت تایمرِ خودش را (نه فقط در پنجره‌ی هم‌خوان)
# شروع کند، بقیه باید در تبِ «هفتگی» او را به‌صورت زنده با شمارش‌معکوس
# ببینند.
LIVE_READING_FILE = os.path.join(BASE_DIR, "live_reading.json")
PROOFS_DIR = os.path.join(BASE_DIR, "challenge_proofs")
CHAT_IMAGES_DIR = os.path.join(BASE_DIR, "challenge_chat_images")
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024  # بعد از دیکد base64 — سقفِ منطقی برای جلوگیری از سوءاستفاده


def _decode_and_save_base64_image(image_base64, filename_hint, target_dir, safe_name_prefix):
    """عکسِ base64 اومده از کلاینت رو دیکد و ذخیره می‌کند.

    ⚠️ این مسیر جایگزینِ multipart/form-data شد چون درخواست‌ها از پروکسیِ
    گوگل‌اسکریپت (Code.gs) رد می‌شوند و آن پروکسی همیشه Content-Type را
    "application/json" می‌فرستد — یک بدنه‌ی multipart واقعی boundary‌اش
    را همان‌جا از دست می‌داد و اینجا هرگز request.files پر نمی‌شد. تا وقتی
    خودِ Code.gs هم برای این دو endpoint اصلاح نشده، همه‌چیز باید از
    مسیرِ JSON عادی (که پروکسی سالم رد می‌کند) برود.

    برمی‌گرداند: (safe_name, error_message). یکی از این دو همیشه None است.
    """
    if not image_base64:
        return None, "فیلد imageBase64 الزامی است"

    # اگه کلاینت data URI کامل فرستاده باشه (data:image/jpeg;base64,....)
    b64_data = image_base64
    if "," in b64_data and b64_data.strip().lower().startswith("data:"):
        b64_data = b64_data.split(",", 1)[1]

    try:
        raw = base64.b64decode(b64_data, validate=False)
    except (binascii.Error, ValueError):
        return None, "فرمتِ base64 عکس نامعتبر است"

    if not raw:
        return None, "عکسِ ارسالی خالی است"
    if len(raw) > MAX_IMAGE_BYTES:
        return None, "حجمِ عکس بیش از حد مجاز است"

    ext = os.path.splitext(filename_hint or "")[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        ext = ".jpg"

    os.makedirs(target_dir, exist_ok=True)
    safe_name = "{}{}".format(safe_name_prefix, ext)
    with open(os.path.join(target_dir, safe_name), "wb") as f:
        f.write(raw)

    return safe_name, None

# --- ثابت‌های قابل‌تنظیم (نگاه کن به توضیح بالای فایل) -----------------
FULL_DAY_MINUTES = 30
PARTIAL_DAY_MINUTES = 1
WEEKLY_XP_PER_MINUTE = 2
HEAT_DAYS_COUNT = 28
CHAIN_DAYS_COUNT = 7
MAX_BACKFILL_DAYS = 60  # سقف عقب‌گرد برای جلوگیری از حلقه‌ی خیلی طولانی
DEFAULT_TIMEZONE_OFFSET_HOURS = 3.5  # به‌وقت ایران؛ در session_schedule.json قابل override

# --- «در حال مطالعه‌ی زنده» --------------------------------------------
MIN_LIVE_READING_MINUTES = 1
MAX_LIVE_READING_MINUTES = 300  # سقف ایمنی؛ جلوگیری از باگِ کلاینت که مقدارِ نجومی بفرستد

# --- شبیه‌سازیِ ۲۹ رباتِ مطالعه‌کننده (پایین‌تر، بخشِ «ربات‌ها») ----------
BOT_COUNT = 29
BOT_DAILY_STUDY_PROBABILITY = 0.67
BOT_MIN_SESSION_MINUTES = 15
BOT_MAX_SESSION_MINUTES = 90
BOT_WINDOW_START_MINUTE = 7 * 60   # ۰۷:۰۰
BOT_WINDOW_END_MINUTE = 22 * 60    # ۲۲:۰۰
BOT_TICK_SECONDS = 30

COLOR_PALETTE = ["teal", "violet", "amber", "rose", "blue", "emerald", "fuchsia", "cyan"]

PHASE_ORDER = ["before", "checkin", "study", "proof", "done"]

PHASE_STATUS_LABELS = {
    "before": "جلسه هنوز شروع نشده",
    "checkin": "زمان حضور و غیاب",
    "study": "در حال مطالعه",
    "proof": "زمان ارسال مدرک",
    "done": "جلسه‌ی امروز تمام شد",
}


# ================================================================
# دسترسی به کمکی‌های server.py بدون import چرخه‌ای
# ================================================================
# این توابع در server.py تعریف شده‌اند و هنگام register کردن blueprint
# با _bind_core_helpers() به این ماژول تزریق می‌شوند، تا این فایل دقیقاً
# از همان _file_lock سراسری و همان الگوی _read_json/_write_json استفاده
# کند (نه یک قفل جدا که می‌تواند باعث race condition شود).

_read_json = None
_write_json_unlocked = None
_file_lock = None
_success = None
_error = None


def _bind_core_helpers(read_json, write_json_unlocked, file_lock, success, error):
    global _read_json, _write_json_unlocked, _file_lock, _success, _error
    _read_json = read_json
    _write_json_unlocked = write_json_unlocked
    _file_lock = file_lock
    _success = success
    _error = error


def _write_json(path, data):
    with _file_lock:
        _write_json_unlocked(path, data)


def _read_json_locked(path, default):
    """نسخه‌ای که از قبل داخل _file_lock فراخوانی می‌شود (بدون قفل مضاعف)."""
    if not os.path.exists(path):
        _write_json_unlocked(path, default)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)
    except (json.JSONDecodeError, OSError):
        return default


# ================================================================
# کمکی‌های زمان
# ================================================================

def _timezone_offset_hours():
    schedule = _read_json(SCHEDULE_FILE, _default_schedule())
    try:
        return float(schedule.get("timezoneOffsetHours", DEFAULT_TIMEZONE_OFFSET_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_TIMEZONE_OFFSET_HOURS


def _now_local():
    return datetime.utcnow() + timedelta(hours=_timezone_offset_hours())


def _today_str(now_local=None):
    now_local = now_local or _now_local()
    return now_local.date().isoformat()


def _week_start(d):
    """اولین روز هفته (شنبه) برای تاریخ d (که یک date است)."""
    # weekday(): دوشنبه=۰ ... شنبه=۵ ... یکشنبه=۶
    offset = (d.weekday() - 5) % 7
    return d - timedelta(days=offset)


def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _fmt_date(d):
    return d.isoformat()


def _parse_hhmm(s):
    h, m = s.split(":")
    return dtime(int(h), int(m))


# ================================================================
# فایل‌های پیش‌فرض
# ================================================================

def _default_schedule():
    return {
        "timezoneOffsetHours": DEFAULT_TIMEZONE_OFFSET_HOURS,
        "sessionLabel": "جلسه‌ی مطالعه‌ی شبانه",
        "phases": {
            "before": {"start": "00:00", "end": "20:00"},
            "checkin": {"start": "20:00", "end": "20:10"},
            "study": {"start": "20:10", "end": "21:40"},
            "proof": {"start": "21:40", "end": "21:50"},
            "done": {"start": "21:50", "end": "23:59"},
        },
    }


def _default_live_session(session_key, today):
    return {"sessionKey": session_key, "date": today, "participants": [], "chat": []}


def _session_key(schedule, today):
    """کلید یکتای «جلسه‌ی هم‌خوانِ» فعلی: ترکیب تاریخ امروز + زمان‌های
    دقیق فازهای checkin/study/proof از session_schedule.json.

    قبلاً participants/chat فقط بر اساس «تاریخ» ریست می‌شدند، یعنی اگر
    امروز یک جلسه (مثلاً ۱۲ تا ۱۳) برگزار می‌شد و بعد همان روز فایل
    session_schedule.json به بازه‌ی دیگری (مثلاً ۱۸ تا ۱۹) تغییر
    می‌کرد، چون هنوز همان «تاریخ» بود، شرکت‌کننده‌ها/چتِ جلسه‌ی قبلی
    (confirmed/proofSent) دست‌نخورده می‌ماند و عملاً جلسه‌ی جدید بلوکه
    می‌شد تا نیمه‌شب. حالا کلید بر اساس خودِ زمان‌های فاز هم حساب
    می‌شود، پس هر بار که زمان‌ها در فایل عوض شوند (حتی چند بار در یک
    روز) یک جلسه‌ی هم‌خوانِ کاملاً تازه (بدون شرکت‌کننده/چتِ قبلی)
    ساخته می‌شود — دقیقاً هر تایمی که در فایل نوشته شود، همان جلسه
    برگزار می‌شود."""
    phases = schedule.get("phases", {})
    checkin = phases.get("checkin", {})
    study = phases.get("study", {})
    proof = phases.get("proof", {})
    return "{}|{}-{}|{}-{}|{}-{}".format(
        today,
        checkin.get("start", ""), checkin.get("end", ""),
        study.get("start", ""), study.get("end", ""),
        proof.get("start", ""), proof.get("end", ""),
    )


def _default_streak_record():
    return {
        "currentStreak": 0,
        "bestStreak": 0,
        "freezesLeft": 1,
        "weekStart": None,
        "lastWeekRank": None,
        "weeksOnBoardStreak": 0,
        "days": {},
        "lastFinalizedDate": None,
        "weeklyRecaps": [],
    }


# ================================================================
# کاربر / نمایش (نام، اینیشیال، رنگ)
# ================================================================

def _user_display(user_id):
    users = _read_json(USERS_FILE, [])
    user = next((u for u in users if u.get("userId") == user_id), None)

    if user:
        first = (user.get("firstName") or "").strip()
        last = (user.get("lastName") or "").strip()
        name = (first + " " + last).strip() or user_id
        initials = ((first[:1] if first else "") + (last[:1] if last else "")) or name[:2]
    else:
        name = user_id
        initials = user_id[:2] if len(user_id) >= 2 else user_id

    # رنگ بر اساس هش userId انتخاب می‌شود تا برای هر کاربر همیشه ثابت
    # بماند، بدون این‌که لازم باشد جایی ذخیره‌اش کنیم.
    digest = hashlib.md5(user_id.encode("utf-8")).hexdigest()
    color_key = COLOR_PALETTE[int(digest, 16) % len(COLOR_PALETTE)]

    return {"userId": user_id, "name": name, "initials": initials, "colorKey": color_key}


# ================================================================
# تجمیع دقیقه‌های روزانه/هفتگی از progress.json
# ================================================================

def _entry_date_str(entry, offset_hours):
    """تاریخ محلیِ یک رکورد progress را برمی‌گرداند: اول از clientTimestamp
    (زمان واقعی مطالعه‌ی کلاینت) استفاده می‌شود، اگر نبود از receivedAt."""
    ts = entry.get("clientTimestamp") or entry.get("receivedAt")
    if not ts:
        return None
    ts = ts.rstrip("Z")
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return (dt + timedelta(hours=offset_hours)).date().isoformat()


def _daily_minutes_for_user(progress, user_id, offset_hours):
    """دیکشنری {date_str: مجموع دقیقه} برای یک کاربر خاص."""
    totals = {}
    for entry in progress:
        if entry.get("userId") != user_id:
            continue
        d = _entry_date_str(entry, offset_hours)
        if not d:
            continue
        totals[d] = totals.get(d, 0) + int(entry.get("minutes") or 0)
    return totals


def _weekly_minutes_all_users(progress, week_start, week_end, offset_hours):
    """دیکشنری {userId: مجموع دقیقه} برای همه‌ی کاربران در بازه‌ی هفته
    (week_start و week_end هر دو date و شامل هر دو سر بازه‌اند)."""
    totals = {}
    for entry in progress:
        d = _entry_date_str(entry, offset_hours)
        if not d:
            continue
        dd = _parse_date(d)
        if week_start <= dd <= week_end:
            uid = entry.get("userId")
            if not uid:
                continue
            totals[uid] = totals.get(uid, 0) + int(entry.get("minutes") or 0)
    return totals


def _rank_of_user(totals_dict, user_id):
    """رتبه‌ی کاربر (۱-based) بر اساس دقیقه‌ی نزولی؛ اگر کاربر رکوردی
    نداشت هم با ۰ دقیقه در رتبه‌بندی درنظر گرفته می‌شود."""
    ordered = sorted(totals_dict.items(), key=lambda kv: kv[1], reverse=True)
    ids = [uid for uid, _ in ordered]
    if user_id not in ids:
        ids.append(user_id)
    return ids.index(user_id) + 1, len(ids)


# ================================================================
# منطق استریک + فریز (هسته‌ی اصلی بخش «چالش‌ها»)
# ================================================================

def _roll_week_if_needed(record, today_week_start, progress, offset_hours, user_id):
    """اگر هفته عوض شده باشد: یک weeklyRecap برای هفته‌ی قبلی می‌سازد،
    weeksOnBoardStreak را به‌روزرسانی می‌کند و freezesLeft را ریست
    می‌کند. این تابع فقط منطق را روی record اعمال می‌کند؛ نوشتن روی
    دیسک را تابع فراخواننده انجام می‌دهد."""
    prev_week_start_str = record.get("weekStart")

    if prev_week_start_str == today_week_start.isoformat():
        return  # هنوز همان هفته‌ایم؛ کاری لازم نیست

    if prev_week_start_str is not None:
        prev_start = _parse_date(prev_week_start_str)
        prev_end = prev_start + timedelta(days=6)
        totals = _weekly_minutes_all_users(progress, prev_start, prev_end, offset_hours)
        my_minutes = totals.get(user_id, 0)
        rank, of = _rank_of_user(totals, user_id)

        record["weeklyRecaps"].append({
            "dateLabel": "{} تا {}".format(_fmt_date(prev_start), _fmt_date(prev_end)),
            "rank": rank,
            "prevRank": record.get("lastWeekRank") or rank,
            "of": of,
            "minutes": my_minutes,
            "xp": my_minutes * WEEKLY_XP_PER_MINUTE,
        })
        # فقط ۱۲ رکورد آخر نگه داشته می‌شود تا فایل بی‌رویه بزرگ نشود
        record["weeklyRecaps"] = record["weeklyRecaps"][-12:]

        record["weeksOnBoardStreak"] = (record.get("weeksOnBoardStreak", 0) + 1) if my_minutes > 0 else 0
        record["lastWeekRank"] = rank

    record["weekStart"] = today_week_start.isoformat()
    record["freezesLeft"] = 1


def _backfill_days(record, user_id, progress, offset_hours, today):
    """روزهای قبل از امروز که هنوز نهایی نشده‌اند را بر اساس دقیقه‌ی
    واقعی مطالعه (از progress.json) به full/partial/frozen/miss تبدیل
    و در record['days'] ذخیره می‌کند. استریک و فریز هم همین‌جا به‌روز
    می‌شوند."""
    daily_minutes = _daily_minutes_for_user(progress, user_id, offset_hours)

    last_finalized = record.get("lastFinalizedDate")
    if last_finalized:
        cursor = _parse_date(last_finalized) + timedelta(days=1)
    else:
        cursor = today - timedelta(days=MAX_BACKFILL_DAYS)

    week_start_for_freeze = _parse_date(record["weekStart"]) if record.get("weekStart") else _week_start(cursor)

    while cursor < today:
        # اگر روز جاری به هفته‌ی جدیدی رسیده، freezesLeft هفتگی ریست شود
        cur_week_start = _week_start(cursor)
        if cur_week_start != week_start_for_freeze:
            week_start_for_freeze = cur_week_start
            record["freezesLeft"] = 1

        minutes = daily_minutes.get(_fmt_date(cursor), 0)

        if minutes >= FULL_DAY_MINUTES:
            status = "full"
        elif minutes >= PARTIAL_DAY_MINUTES:
            status = "partial"
        elif record.get("freezesLeft", 0) > 0:
            status = "frozen"
            record["freezesLeft"] -= 1
        else:
            status = "miss"

        record["days"][_fmt_date(cursor)] = status

        if status == "miss":
            record["currentStreak"] = 0
        else:
            record["currentStreak"] = record.get("currentStreak", 0) + 1
            record["bestStreak"] = max(record.get("bestStreak", 0), record["currentStreak"])

        record["lastFinalizedDate"] = _fmt_date(cursor)
        cursor += timedelta(days=1)

    # پاک‌سازی روزهای خیلی قدیمی (بیشتر از حد لازم برای heatDays) تا
    # فایل رشد نامحدود نداشته باشد
    keep_from = today - timedelta(days=HEAT_DAYS_COUNT + 7)
    record["days"] = {
        d: v for d, v in record["days"].items() if _parse_date(d) >= keep_from
    }


def _today_provisional_status(record, user_id, progress, offset_hours, today):
    """وضعیت «زنده»ی امروز، فقط برای نمایش در chainDays/heatDays — روی
    دیسک نوشته نمی‌شود چون امروز هنوز تمام نشده."""
    daily_minutes = _daily_minutes_for_user(progress, user_id, offset_hours)
    minutes = daily_minutes.get(_fmt_date(today), 0)
    if minutes >= FULL_DAY_MINUTES:
        return "full"
    if minutes >= PARTIAL_DAY_MINUTES:
        return "partial"
    return "miss"


def _build_day_list(record, provisional_today_status, today, count):
    days_map = record.get("days", {})
    out = []
    for i in range(count - 1, -1, -1):
        d = today - timedelta(days=i)
        if d == today:
            out.append(provisional_today_status)
        else:
            out.append(days_map.get(_fmt_date(d), "miss"))
    return out


# ================================================================
# جلسه‌ی زنده — محاسبه‌ی فاز از روی ساعت (بدون state machine)
# ================================================================

def _current_phase(schedule, now_local_time):
    phases = schedule.get("phases", {})
    for phase in PHASE_ORDER:
        cfg = phases.get(phase)
        if not cfg:
            continue
        start = _parse_hhmm(cfg["start"])
        end = _parse_hhmm(cfg["end"])
        if start <= now_local_time < end:
            return phase
    # اگر داخل هیچ بازه‌ای نبود (مثلاً شکاف بین تعریف‌ها)، قبل از اولین
    # فاز تعریف‌شده باشیم "before" و بعد از آخرین "done" است
    first_start = _parse_hhmm(phases.get("before", {}).get("start", "00:00"))
    if now_local_time < first_start:
        return "before"
    return "done"


def _seconds_until_phase_end(schedule, phase, now_local):
    cfg = schedule.get("phases", {}).get(phase)
    if not cfg:
        return 0
    end = _parse_hhmm(cfg["end"])
    end_dt = now_local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if end_dt <= now_local:
        return 0
    return int((end_dt - now_local).total_seconds())


def _format_timer(seconds):
    seconds = max(0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return "{:02d}:{:02d}:{:02d}".format(h, m, s)
    return "{:02d}:{:02d}".format(m, s)


def _get_live_session(today, schedule):
    session_key = _session_key(schedule, today)
    session = _read_json(LIVE_SESSION_FILE, _default_live_session(session_key, today))
    if session.get("sessionKey") != session_key:
        # روز یا بازه‌ی زمانیِ جلسه عوض شده؛ ریست
        session = _default_live_session(session_key, today)
        _write_json(LIVE_SESSION_FILE, session)
    return session


# ================================================================
# «در حال مطالعه‌ی زنده» — کمکی‌ها
# ================================================================
# قرارداد فایل live_reading.json: {"userId": {"startedAt": iso, "endsAt": iso}, ...}
# هیچ state machine‌ای در RAM نگه داشته نمی‌شود (هم‌راستا با بقیه‌ی این
# فایل) — «الان کی در حال مطالعه است» همیشه با مقایسه‌ی endsAt با ساعتِ
# فعلی محاسبه می‌شود، هم برای رکوردهای واقعیِ کاربرها (از TimerService)
# هم برای رکوردهای رباتی (پایین‌تر).

def _purge_expired_reading_locked(now_local):
    """رکوردهای منقضی‌شده را از live_reading.json حذف می‌کند (باید از
    داخل _file_lock صدا زده شود). دیکشنریِ تمیزشده را برمی‌گرداند."""
    reading = _read_json_locked(LIVE_READING_FILE, {})
    changed = False
    cleaned = {}
    for uid, rec in reading.items():
        ends_at = rec.get("endsAt")
        try:
            still_active = ends_at and datetime.fromisoformat(ends_at) > now_local
        except ValueError:
            still_active = False
        if still_active:
            cleaned[uid] = rec
        else:
            changed = True
    if changed:
        _write_json_unlocked(LIVE_READING_FILE, cleaned)
    return cleaned


def _active_reading_map(now_local):
    """نسخه‌ی سبک (بدون قفل مضاعف اگر لازم نباشد) برای استفاده در GET
    /api/challenges/state — چون ممکن است لازم باشد فایل را هم پاک‌سازی
    کند، از قفل استفاده می‌شود."""
    with _file_lock:
        return _purge_expired_reading_locked(now_local)


def _seconds_left_for_reading(rec, now_local):
    try:
        ends_at = datetime.fromisoformat(rec.get("endsAt"))
    except (ValueError, TypeError):
        return 0
    return max(0, int((ends_at - now_local).total_seconds()))


def _set_live_reading(user_id, duration_minutes, now_local):
    """شروع/تمدیدِ «در حال مطالعه‌ی زنده»ی یک کاربر. از داخل _file_lock صدا زده می‌شود."""
    duration_minutes = max(MIN_LIVE_READING_MINUTES, min(MAX_LIVE_READING_MINUTES, duration_minutes))
    ends_at = now_local + timedelta(minutes=duration_minutes)
    reading = _read_json_locked(LIVE_READING_FILE, {})
    reading[user_id] = {"startedAt": _fmt_iso(now_local), "endsAt": _fmt_iso(ends_at)}
    _write_json_unlocked(LIVE_READING_FILE, reading)


def _clear_live_reading(user_id):
    """پایانِ «در حال مطالعه‌ی زنده»ی یک کاربر. از داخل _file_lock صدا زده می‌شود."""
    reading = _read_json_locked(LIVE_READING_FILE, {})
    if user_id in reading:
        del reading[user_id]
        _write_json_unlocked(LIVE_READING_FILE, reading)


def _fmt_iso(dt):
    return dt.replace(microsecond=0).isoformat()


# ================================================================
# اندپوینت‌ها
# ================================================================

@challenges_bp.route("/api/challenges/state", methods=["GET"])
def challenges_state():
    user_id = request.args.get("userId")
    if not user_id:
        return _error("پارامتر userId الزامی است", 400)

    now_local = _now_local()
    today = now_local.date()
    today_str = _fmt_date(today)
    offset_hours = _timezone_offset_hours()

    progress = _read_json(PROGRESS_FILE, [])
    schedule = _read_json(SCHEDULE_FILE, _default_schedule())

    # --- استریک + فریز (با قفل، چون ممکن است فایل را به‌روز کنیم) -----
    with _file_lock:
        streak_all = _read_json_locked(STREAK_FILE, {})
        record = streak_all.get(user_id) or _default_streak_record()

        today_week_start = _week_start(today)
        _roll_week_if_needed(record, today_week_start, progress, offset_hours, user_id)
        _backfill_days(record, user_id, progress, offset_hours, today)

        streak_all[user_id] = record
        _write_json_unlocked(STREAK_FILE, streak_all)

    provisional_today = _today_provisional_status(record, user_id, progress, offset_hours, today)
    chain_days = _build_day_list(record, provisional_today, today, CHAIN_DAYS_COUNT)
    heat_days = _build_day_list(record, provisional_today, today, HEAT_DAYS_COUNT)

    # --- لیدربورد هفتگی (از progress.json، بدون فایل جداگانه) ---------
    week_end = today_week_start + timedelta(days=6)
    weekly_totals = _weekly_minutes_all_users(progress, today_week_start, week_end, offset_hours)

    # «در حال مطالعه‌ی زنده»: نگاشتِ userId -> ثانیه‌ی باقی‌مانده، تا در
    # هر ردیفِ لیدربورد یک بجِ زنده با شمارش‌معکوس نشان داده شود — دقیقاً
    # همان چیزی که باعث می‌شود اگر یک کاربر همین الان تایمرش را روشن کند،
    # بقیه بلافاصله (با باز/رفرش‌شدنِ همین تبِ «هفتگی») او را در حال
    # مطالعه با یک تایمر ببینند.
    active_reading = _active_reading_map(now_local)
    reading_seconds = {uid: _seconds_left_for_reading(rec, now_local) for uid, rec in active_reading.items()}

    leaderboard = []
    seen_uids = set()
    for uid, minutes in sorted(weekly_totals.items(), key=lambda kv: kv[1], reverse=True):
        display = _user_display(uid)
        leaderboard.append({
            "userId": display["userId"],
            "name": display["name"],
            "initials": display["initials"],
            "colorKey": display["colorKey"],
            "minutes": minutes,
            "isMe": uid == user_id,
            "reading": uid in reading_seconds,
            "readingSecondsLeft": reading_seconds.get(uid, 0),
        })
        seen_uids.add(uid)
    if user_id not in weekly_totals:
        display = _user_display(user_id)
        leaderboard.append({
            "userId": display["userId"],
            "name": display["name"],
            "initials": display["initials"],
            "colorKey": display["colorKey"],
            "minutes": 0,
            "isMe": True,
            "reading": user_id in reading_seconds,
            "readingSecondsLeft": reading_seconds.get(user_id, 0),
        })
        seen_uids.add(user_id)
    # کاربرهایی که همین الان در حال مطالعه‌اند ولی هنوز هیچ دقیقه‌ای این
    # هفته ثبت نکرده‌اند (جلسه‌شان هنوز تمام نشده) هم باید در جدول دیده
    # شوند — وگرنه بجِ «در حال مطالعه» هیچ‌جا نمایش داده نمی‌شد.
    for uid in reading_seconds:
        if uid in seen_uids:
            continue
        display = _user_display(uid)
        leaderboard.append({
            "userId": display["userId"],
            "name": display["name"],
            "initials": display["initials"],
            "colorKey": display["colorKey"],
            "minutes": 0,
            "isMe": uid == user_id,
            "reading": True,
            "readingSecondsLeft": reading_seconds.get(uid, 0),
        })

    days_left = max(0, (week_end - today).days)

    # --- جلسه‌ی زنده -----------------------------------------------
    live_session = _get_live_session(today_str, schedule)
    phase = _current_phase(schedule, now_local.time())
    seconds_left = _seconds_until_phase_end(schedule, phase, now_local)

    live = {
        "phase": phase,
        "sessionLabel": schedule.get("sessionLabel", ""),
        "statusLabel": PHASE_STATUS_LABELS.get(phase, ""),
        "timerLabel": _format_timer(seconds_left),
        "participants": live_session.get("participants", []),
        "chat": live_session.get("chat", []),
    }

    response = {
        "streak": {
            "currentStreak": record.get("currentStreak", 0),
            "bestStreak": record.get("bestStreak", 0),
            "freezesLeft": record.get("freezesLeft", 0),
            "chainDays": chain_days,
        },
        "weekly": {
            "daysLeft": days_left,
            "weeksOnBoardStreak": record.get("weeksOnBoardStreak", 0),
            "leaderboard": leaderboard,
        },
        "live": live,
        "history": {
            "heatDays": heat_days,
            "weeklyRecaps": record.get("weeklyRecaps", []),
        },
    }

    # نکته‌ی مهم: برخلاف بقیه‌ی endpointهای این فایل، این پاسخ عمداً از
    # _success() استفاده نمی‌کند و بسته‌بندی {"status","data"} ندارد.
    # کلاینت اندروید (ChallengeManager.fetchState -> StateResponse.class)
    # مستقیماً انتظار دارد کلیدهای streak/weekly/live/history در ریشه‌ی
    # JSON باشند؛ اگر این‌جا هم از _success استفاده شود، این فیلدها زیر
    # "data" قرار می‌گیرند و Gson بی‌سروصدا یک StateResponse خالی (با
    # live.sessionLabel == null) می‌سازد — دقیقاً همان باگ «هیچ چالش
    # فعالی نیست» که قبلاً دیده شد. این envelope را عوض نکن مگر این‌که
    # سمت اپ (StateResponse) هم هم‌زمان تغییر کند.
    return jsonify(response), 200


@challenges_bp.route("/api/challenges/live-reading/start", methods=["POST"])
def challenges_live_reading_start():
    """صدا زده می‌شود همان لحظه‌ای که TimerService یک جلسه‌ی مطالعه را
    شروع می‌کند (یا از حالت مکث ادامه می‌دهد). durationMinutes یعنی «چند
    دقیقه‌ی دیگر از الان» این جلسه ادامه دارد (نه کل مدتِ جلسه) — کلاینت
    برای resume همان مقدارِ باقی‌مانده را می‌فرستد. اگر کاربر از قبل هم
    یک رکوردِ زنده داشت (مثلاً بدون STOP، دوباره START زده)، این مقدار
    جایگزینِ آن می‌شود، نه جمع با آن."""
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    duration_minutes = body.get("durationMinutes")
    if not user_id:
        return _error("فیلد userId الزامی است", 400)
    try:
        duration_minutes = int(duration_minutes)
    except (TypeError, ValueError):
        return _error("فیلد durationMinutes الزامی است", 400)
    if duration_minutes <= 0:
        return _error("durationMinutes باید مثبت باشد", 400)

    now_local = _now_local()
    with _file_lock:
        _set_live_reading(user_id, duration_minutes, now_local)

    return _success(message="وضعیت «در حال مطالعه» ثبت شد")


@challenges_bp.route("/api/challenges/live-reading/stop", methods=["POST"])
def challenges_live_reading_stop():
    """صدا زده می‌شود وقتی تایمر مکث/متوقف/تمام می‌شود (یا سرویس نابود
    می‌شود). نبودِ رکورد برای این کاربر خطا نیست — idempotent است."""
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    if not user_id:
        return _error("فیلد userId الزامی است", 400)

    with _file_lock:
        _clear_live_reading(user_id)

    return _success(message="وضعیت «در حال مطالعه» پاک شد")


@challenges_bp.route("/api/challenges/checkin", methods=["POST"])
def challenges_checkin():
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    if not user_id:
        return _error("فیلد userId الزامی است", 400)

    today_str = _today_str()
    display = _user_display(user_id)

    with _file_lock:
        schedule = _read_json_locked(SCHEDULE_FILE, _default_schedule())
        session_key = _session_key(schedule, today_str)
        session = _read_json_locked(LIVE_SESSION_FILE, _default_live_session(session_key, today_str))
        if session.get("sessionKey") != session_key:
            session = _default_live_session(session_key, today_str)

        participants = session.setdefault("participants", [])
        existing = next((p for p in participants if p.get("userId") == user_id), None)
        if existing:
            existing["confirmed"] = True
        else:
            participants.append({
                "userId": display["userId"],
                "name": display["name"],
                "initials": display["initials"],
                "colorKey": display["colorKey"],
                "confirmed": True,
                "proofSent": False,
            })

        _write_json_unlocked(LIVE_SESSION_FILE, session)

    return _success(message="حضور شما ثبت شد")


def _reply_preview_for(m):
    """یک خلاصه‌ی کوتاه از پیام برای نمایش در نقل‌قولِ «پاسخ» می‌سازد."""
    if m.get("proof"):
        return "📸 مدرک مطالعه"
    if m.get("imageUrl"):
        return "🖼️ عکس"
    text = (m.get("text") or "").strip()
    if len(text) > 60:
        text = text[:60] + "…"
    return text


def _find_reply_target(chat_list, reply_to_id):
    """پیامِ اصلی که به آن پاسخ داده شده را پیدا می‌کند و آبجکتِ replyTo
    (برای نمایش کوتاه در بالای حباب) می‌سازد. اگر پیدا نشود (مثلاً پاک
    شده یا خارج از بازه‌ی ۲۰۰ پیامِ اخیر رفته) None برمی‌گرداند — کلاینت
    در این حالت فقط بدونِ نقل‌قول نمایش می‌دهد، نه اینکه ارسال شکست بخورد."""
    if not reply_to_id:
        return None
    for m in chat_list:
        if m.get("id") == reply_to_id:
            return {
                "id": m["id"],
                "name": m.get("name") or "",
                "preview": _reply_preview_for(m),
            }
    return None


@challenges_bp.route("/api/challenges/chat", methods=["POST"])
def challenges_chat():
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    text = body.get("text")
    reply_to_id = body.get("replyToId")
    if not user_id or not text:
        return _error("فیلدهای userId و text الزامی‌اند", 400)

    today_str = _today_str()
    now_local = _now_local()
    display = _user_display(user_id)

    message = {
        "id": uuid.uuid4().hex[:12],
        "userId": display["userId"],
        "name": display["name"],
        "initials": display["initials"],
        "colorKey": display["colorKey"],
        "text": text,
        "time": now_local.strftime("%H:%M"),
        "reactions": {},
    }

    with _file_lock:
        schedule = _read_json_locked(SCHEDULE_FILE, _default_schedule())
        session_key = _session_key(schedule, today_str)
        session = _read_json_locked(LIVE_SESSION_FILE, _default_live_session(session_key, today_str))
        if session.get("sessionKey") != session_key:
            session = _default_live_session(session_key, today_str)

        chat = session.setdefault("chat", [])
        reply_to = _find_reply_target(chat, reply_to_id)
        if reply_to:
            message["replyTo"] = reply_to
        chat.append(message)
        # فقط ۲۰۰ پیام آخر امروز نگه داشته می‌شود
        session["chat"] = chat[-200:]

        _write_json_unlocked(LIVE_SESSION_FILE, session)

    return _success(data=message, message="پیام ارسال شد")


@challenges_bp.route("/api/challenges/proof", methods=["POST"])
def challenges_proof():
    # ⚠️ عمداً JSON (نه multipart/form-data) — به دلیلِ توضیحِ بالای
    # _decode_and_save_base64_image (پروکسیِ گوگل‌اسکریپت باندریِ
    # multipart را گم می‌کرد و این endpoint همیشه «ارسال ناموفق» می‌داد).
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    if not user_id:
        return _error("فیلد userId الزامی است", 400)

    today_str = _today_str()
    image_base64 = body.get("imageBase64")
    filename_hint = body.get("filename")

    if image_base64:
        safe_name, err = _decode_and_save_base64_image(
            image_base64, filename_hint, PROOFS_DIR, "{}_{}".format(today_str, user_id))
        if err:
            return _error(err, 400)

    # مهم: display باید همین‌جا، پیش از گرفتن _file_lock، محاسبه شود.
    # _user_display خودش از طریق _read_json دوباره _file_lock را می‌گیرد؛
    # چون این قفل (threading.Lock) reentrant نیست، فراخوانی‌اش از داخل
    # بلوک «with _file_lock:» پایین باعث دِدلاک کامل سرور می‌شد — دقیقاً
    # همان چیزی که باعث ۵۰۳ شدنِ تونل بعد از اولین درخواست proof از یک
    # کاربر تازه می‌شد (چون Flask dev server پیش‌فرض تک‌نخی است و با گیر
    # کردن آن یک نخ، کل سرور از جمله /ping هم بی‌پاسخ می‌ماند).
    # checkin/chat از قبل همین الگوی درست را داشتند؛ اینجا هم با همان هم‌راستا شد.
    display = _user_display(user_id)

    with _file_lock:
        schedule = _read_json_locked(SCHEDULE_FILE, _default_schedule())
        session_key = _session_key(schedule, today_str)
        session = _read_json_locked(LIVE_SESSION_FILE, _default_live_session(session_key, today_str))
        if session.get("sessionKey") != session_key:
            session = _default_live_session(session_key, today_str)

        participants = session.setdefault("participants", [])
        existing = next((p for p in participants if p.get("userId") == user_id), None)
        if existing:
            existing["proofSent"] = True
        else:
            participants.append({
                "userId": display["userId"],
                "name": display["name"],
                "initials": display["initials"],
                "colorKey": display["colorKey"],
                "confirmed": True,
                "proofSent": True,
            })

        _write_json_unlocked(LIVE_SESSION_FILE, session)

    return _success(message="مدرک با موفقیت دریافت شد")


@challenges_bp.route("/api/challenges/chat-image", methods=["POST"])
def challenges_chat_image():
    """پیامِ چتِ عکس‌دار (مجزا از «عکس مدرک»/proof). قبلاً این مسیر اصلاً
    روی سرور تعریف نشده بود، برای همین کلاینت (ChallengeManager.sendChatImage)
    همیشه خطای شبکه می‌گرفت. پاسخ دقیقاً هم‌شکلِ endpoint چتِ متنی است:
    {"status","data":{...ChatMessage به‌همراه فیلدِ "imageUrl"}}.

    ⚠️ عمداً JSON (نه multipart/form-data) — به دلیلِ توضیحِ بالای
    _decode_and_save_base64_image (پروکسیِ گوگل‌اسکریپت باندریِ multipart
    را گم می‌کرد و این endpoint هم با «ارسال ناموفق» شکست می‌خورد)."""
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    if not user_id:
        return _error("فیلد userId الزامی است", 400)

    image_base64 = body.get("imageBase64")
    filename_hint = body.get("filename")
    reply_to_id = body.get("replyToId")
    if not image_base64:
        return _error("فیلد imageBase64 الزامی است", 400)

    today_str = _today_str()
    now_local = _now_local()

    # مهم: مثل proof، display باید همین‌جا و پیش از گرفتن _file_lock
    # محاسبه شود (چون _user_display خودش از طریق _read_json دوباره
    # همین قفل را می‌گیرد). حالا که قفل از نوع RLock است دیگر دِدلاک
    # نمی‌شود، ولی همین ترتیب را برای خوانایی و هماهنگی با بقیه‌ی
    # اندپوینت‌ها نگه می‌داریم.
    display = _user_display(user_id)

    safe_name, err = _decode_and_save_base64_image(
        image_base64, filename_hint, CHAT_IMAGES_DIR,
        "{}_{}_{}".format(today_str, user_id, uuid.uuid4().hex[:8]))
    if err:
        return _error(err, 400)

    # آدرس کامل (با دامنه/تونل فعلی) تا کلاینت بتواند مستقیماً عکس را
    # از روی همین فیلد imageUrl لود کند.
    image_url = request.host_url.rstrip("/") + "/api/challenges/chat-image-file/" + safe_name

    message = {
        "id": uuid.uuid4().hex[:12],
        "userId": display["userId"],
        "name": display["name"],
        "initials": display["initials"],
        "colorKey": display["colorKey"],
        "text": "",
        "time": now_local.strftime("%H:%M"),
        "proof": False,
        "imageUrl": image_url,
        "reactions": {},
    }

    with _file_lock:
        schedule = _read_json_locked(SCHEDULE_FILE, _default_schedule())
        session_key = _session_key(schedule, today_str)
        session = _read_json_locked(LIVE_SESSION_FILE, _default_live_session(session_key, today_str))
        if session.get("sessionKey") != session_key:
            session = _default_live_session(session_key, today_str)

        chat = session.setdefault("chat", [])
        reply_to = _find_reply_target(chat, reply_to_id)
        if reply_to:
            message["replyTo"] = reply_to
        chat.append(message)
        # فقط ۲۰۰ پیام آخر امروز نگه داشته می‌شود (هم‌راستا با چتِ متنی)
        session["chat"] = chat[-200:]

        _write_json_unlocked(LIVE_SESSION_FILE, session)

    return _success(data=message, message="عکس ارسال شد")


@challenges_bp.route("/api/challenges/chat-image-file/<path:filename>", methods=["GET"])
def challenges_chat_image_file(filename):
    """سرو کردنِ فایل‌های عکسِ چت که در challenges_chat_image ذخیره شدند،
    تا imageUrl برگشتی واقعاً قابل بارگذاری باشد."""
    return send_from_directory(CHAT_IMAGES_DIR, filename)


@challenges_bp.route("/api/challenges/chat-react", methods=["POST"])
def challenges_chat_react():
    """ری‌اکشنِ ایموجی روی یک پیامِ چت (مالِ خودِ کاربر یا بقیه، فرقی
    نمی‌کند). هر کاربر روی هر پیام حداکثر یک ایموجیِ فعال دارد — دوباره
    زدنِ همان ایموجی آن را برمی‌دارد (toggle)؛ زدنِ ایموجیِ دیگر جایگزینِ
    قبلی می‌شود (دقیقاً رفتارِ تلگرام/واتس‌اپ، نه انباشتِ چند ری‌اکشن از
    یک نفر روی یک پیام).
    body: {"userId","messageId","emoji"} -> data: {"messageId","reactions"}"""
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    message_id = body.get("messageId")
    emoji = body.get("emoji")
    if not user_id or not message_id or not emoji:
        return _error("فیلدهای userId، messageId و emoji الزامی‌اند", 400)

    today_str = _today_str()

    with _file_lock:
        schedule = _read_json_locked(SCHEDULE_FILE, _default_schedule())
        session_key = _session_key(schedule, today_str)
        session = _read_json_locked(LIVE_SESSION_FILE, _default_live_session(session_key, today_str))
        if session.get("sessionKey") != session_key:
            session = _default_live_session(session_key, today_str)

        chat = session.setdefault("chat", [])
        target = next((m for m in chat if m.get("id") == message_id), None)
        if not target:
            return _error("پیام موردنظر پیدا نشد (شاید خارج از بازه‌ی امروز است)", 404)

        reactions = target.setdefault("reactions", {})
        # اول هر ری‌اکشنِ قبلیِ همین کاربر روی همین پیام را (با هر ایموجی)
        # پاک می‌کنیم — چون هر کاربر فقط یک ری‌اکشنِ فعال در آنِ واحد دارد.
        had_same = False
        for existing_emoji in list(reactions.keys()):
            users = reactions.get(existing_emoji) or []
            if user_id in users:
                if existing_emoji == emoji:
                    had_same = True
                users.remove(user_id)
                if users:
                    reactions[existing_emoji] = users
                else:
                    del reactions[existing_emoji]

        if not had_same:
            reactions.setdefault(emoji, [])
            if user_id not in reactions[emoji]:
                reactions[emoji].append(user_id)

        target["reactions"] = reactions
        _write_json_unlocked(LIVE_SESSION_FILE, session)

        result = {"messageId": message_id, "reactions": reactions}

    return _success(data=result, message="ری‌اکشن ثبت شد")


# ================================================================
# راه‌اندازی اولیه‌ی فایل‌های داده (برای فراخوانی از server.py هنگام start)
# ================================================================

def ensure_initial_files():
    _read_json(STREAK_FILE, {})
    schedule = _read_json(SCHEDULE_FILE, _default_schedule())
    today_str = _today_str()
    _read_json(LIVE_SESSION_FILE, _default_live_session(_session_key(schedule, today_str), today_str))
    _read_json(LIVE_READING_FILE, {})


# ================================================================
# ۲۹ رباتِ مطالعه‌کننده
# ================================================================
# چون هیچ دیتابیسی وجود ندارد و سرور هر ~۵:۳۰ ساعت کامل ری‌استارت
# می‌شود (progress.json/users.json/live_reading.json هر بار از صفر
# ساخته می‌شوند)، ربات‌ها را با یک فایل/اسکریپتِ جدا و یک‌بارمصرف
# نمی‌سازیم — چون همان لحظه‌ی ری‌استارتِ بعدی، هرچه نوشته بود پاک
# می‌شود. به‌جایش این تردِ پس‌زمینه، همیشه همراه با خودِ سرور زنده
# است و هر روز (به‌وقتِ محلیِ همان تایم‌زونی که بقیه‌ی این فایل استفاده
# می‌کند) برای هر ۲۹ ربات یک برنامه‌ی واقعی می‌سازد: ۶۷٪ احتمال که آن
# روز اصلاً بخوانند، و اگر خواندند، یک بازه‌ی ۱۵ تا ۹۰ دقیقه‌ای که
# کاملاً داخل بازه‌ی ۰۷:۰۰ تا ۲۲:۰۰ جا می‌شود.
#
# در لحظه‌ی شروعِ آن بازه، رباتِ موردنظر دقیقاً مثل یک کاربرِ واقعی که
# تایمرش را روشن کرده وارد live_reading.json می‌شود (یعنی در تبِ
# «هفتگی» با بجِ زنده و شمارش‌معکوس دیده می‌شود) و در لحظه‌ی پایان،
# دقیقاً مثل submitProgress واقعیِ اپ، یک رکورد در progress.json ثبت
# می‌شود — یعنی از دیدِ بقیه‌ی سیستم (لیدربورد، استریک، تاریخچه) هیچ
# فرقی با یک کاربرِ واقعی ندارد.
#
# نکته‌ی مهمِ کارایی (طبق درخواست): چون این هر ۲۹ نفر با هم بررسی
# می‌شوند، در هر tick فقط یک بار progress.json و یک بار live_reading.json
# خوانده/نوشته می‌شود (نه ۲۹ درخواستِ جدا) — یعنی حداکثر دو عملیاتِ
# فایل به‌ازای کل گروه، نه به‌ازای هر ربات.

BOT_FIRST_NAMES = [
    "امیرحسین", "پارسا", "آرمین", "رادین", "کیان", "متین", "آرین", "دانیال",
    "سینا", "علی", "محمد", "حسام", "پویا", "شایان", "نیما",
    "نگار", "ترانه", "باران", "ستایش", "هستی", "آیدا", "رها",
    "پرنیا", "یاسمین", "درسا", "الینا", "ملیکا", "کیمیا", "زهرا",
]
BOT_LAST_NAMES = [
    "کریمی", "محمدی", "حسینی", "رضایی", "احمدی", "موسوی", "قاسمی",
    "صادقی", "نجفی", "رحیمی", "جعفری", "کاظمی", "عباسی", "طاهری",
    "یوسفی", "شریفی", "امینی", "صالحی", "فرجی", "نوری", "رستمی",
    "عزیزی", "فرهادی", "بهرامی", "اکبری", "سلطانی", "غفاری", "خلیلی",
    "وحیدی",
]


def _bot_user_id(index):
    return "bot_{:02d}".format(index + 1)


def _build_bot_roster():
    roster = []
    for i in range(BOT_COUNT):
        first = BOT_FIRST_NAMES[i % len(BOT_FIRST_NAMES)]
        last = BOT_LAST_NAMES[i % len(BOT_LAST_NAMES)]
        roster.append({"userId": _bot_user_id(i), "firstName": first, "lastName": last})
    return roster


def _ensure_bot_users_registered():
    """یک‌بار، در یک عملیاتِ نوشتنِ واحد، هر ۲۹ ربات را (اگر از قبل در
    users.json نبودند) اضافه می‌کند — دقیقاً همان قراردادِ رکوردِ
    /api/register، تا _user_display بتواند اسم/اینیشیال درستشان را
    بسازد."""
    roster = _build_bot_roster()
    with _file_lock:
        users = _read_json_locked(USERS_FILE, [])
        existing_ids = {u.get("userId") for u in users}
        now_iso = datetime.utcnow().isoformat() + "Z"
        changed = False
        for bot in roster:
            if bot["userId"] in existing_ids:
                continue
            users.append({
                "userId": bot["userId"],
                "firstName": bot["firstName"],
                "lastName": bot["lastName"],
                "phoneNumber": "",
                "birthDay": None,
                "birthMonth": None,
                "birthYear": None,
                "gender": "",
                "educationLevel": "",
                "field": "",
                "createdAt": now_iso,
                "updatedAt": now_iso,
            })
            changed = True
        if changed:
            _write_json_unlocked(USERS_FILE, users)
    return [b["userId"] for b in roster]


def _generate_bot_day_plan(bot_ids, day_date, rng):
    """برای یک روزِ مشخص، برای هر ربات تصمیم می‌گیرد امروز می‌خواند یا نه
    و اگر بله، بازه‌ی (start_dt, end_dt) را می‌سازد. rng یک نمونه‌ی
    مستقلِ random.Random است (برای این‌که این تابع کاملاً قابل‌تست/
    determinستیک‌سازی باشد و به وضعیتِ سراسریِ ماژولِ random وابسته نباشد)."""
    plan = {}
    for bot_id in bot_ids:
        if rng.random() >= BOT_DAILY_STUDY_PROBABILITY:
            plan[bot_id] = None
            continue
        duration = rng.randint(BOT_MIN_SESSION_MINUTES, BOT_MAX_SESSION_MINUTES)
        latest_start = BOT_WINDOW_END_MINUTE - duration
        start_minute = rng.randint(BOT_WINDOW_START_MINUTE, latest_start)
        start_dt = datetime.combine(day_date, dtime(0, 0)) + timedelta(minutes=start_minute)
        end_dt = start_dt + timedelta(minutes=duration)
        plan[bot_id] = (start_dt, end_dt)
    return plan


class _BotSimulationState:
    """وضعیتِ کاملاً در-حافظه‌ی تردِ شبیه‌سازی. هیچ‌چیز از این کلاس روی
    دیسک نوشته نمی‌شود — اگر سرور ری‌استارت شود، این وضعیت هم از صفر
    ساخته می‌شود (هم‌راستا با بقیه‌ی این فایل که هیچ state machine‌ای
    را بین ری‌استارت‌ها حفظ نمی‌کند)."""

    def __init__(self, bot_ids):
        self.bot_ids = bot_ids
        self.plan_date = None
        self.plan = {}
        self.started = set()
        self.finished = set()


def _bot_simulation_tick(state, rng):
    now_local = _now_local()
    today = now_local.date()

    if state.plan_date != today:
        state.plan = _generate_bot_day_plan(state.bot_ids, today, rng)
        state.started = set()
        state.finished = set()
        state.plan_date = today

    to_start = []   # لیستِ (userId, start_dt, end_dt)
    to_finish = []  # لیستِ (userId, minutes, end_dt)

    for bot_id, window in state.plan.items():
        if window is None:
            continue
        start_dt, end_dt = window
        if bot_id in state.finished:
            continue
        if now_local >= end_dt:
            to_finish.append((bot_id, int((end_dt - start_dt).total_seconds() // 60), end_dt))
            state.finished.add(bot_id)
        elif now_local >= start_dt and bot_id not in state.started:
            to_start.append((bot_id, start_dt, end_dt))
            state.started.add(bot_id)

    if not to_start and not to_finish:
        return

    # طبق درخواست: کل ۲۹ ربات با هم، در یک عملیاتِ نوشتنِ واحد به‌ازای هر
    # فایل (نه یک نوشتن به‌ازای هر ربات).
    with _file_lock:
        if to_start:
            reading = _read_json_locked(LIVE_READING_FILE, {})
            for bot_id, start_dt, end_dt in to_start:
                reading[bot_id] = {
                    "startedAt": _fmt_iso(start_dt),
                    "endsAt": _fmt_iso(end_dt),
                }
            _write_json_unlocked(LIVE_READING_FILE, reading)

        if to_finish:
            reading = _read_json_locked(LIVE_READING_FILE, {})
            progress = _read_json_locked(PROGRESS_FILE, [])
            offset_hours = _timezone_offset_hours()
            for bot_id, minutes, end_dt in to_finish:
                if bot_id in reading:
                    del reading[bot_id]
                # clientTimestamp باید UTC باشد (دقیقاً مثل isoUtcNow() سمتِ
                # اپ) چون _entry_date_str بعداً خودش offset_hours را رویش
                # اعمال می‌کند؛ end_dt همین‌جا محلی است، پس باید قبل از
                # ذخیره برگردد به UTC — وگرنه offset دوبار اعمال می‌شود.
                end_dt_utc = end_dt - timedelta(hours=offset_hours)
                progress.append({
                    "userId": bot_id,
                    "eventId": "daily_study",
                    "minutes": minutes,
                    "consistencyDays": 0,
                    "clientTimestamp": end_dt_utc.replace(microsecond=0).isoformat() + "Z",
                    "receivedAt": datetime.utcnow().isoformat() + "Z",
                })
            _write_json_unlocked(LIVE_READING_FILE, reading)
            _write_json_unlocked(PROGRESS_FILE, progress)


def _bot_simulation_loop(state):
    rng = random.Random()
    while True:
        try:
            _bot_simulation_tick(state, rng)
        except Exception as ex:  # هیچ خطای این تردِ پس‌زمینه نباید کل سرور را پایین بیاورد
            print("⚠️ خطا در حلقه‌ی شبیه‌سازیِ ربات‌ها: {}".format(ex))
        time.sleep(BOT_TICK_SECONDS)


def start_bot_simulation():
    """باید دقیقاً یک‌بار، از server.py هنگام بالا آمدنِ سرور صدا زده
    شود. یک تردِ daemon (با خروجِ خودکار وقتی خودِ پردازه‌ی سرور بسته
    شود) راه می‌اندازد."""
    bot_ids = _ensure_bot_users_registered()
    state = _BotSimulationState(bot_ids)
    t = threading.Thread(target=_bot_simulation_loop, args=(state,), name="bot-simulation", daemon=True)
    t.start()
    print("🤖 شبیه‌سازیِ {} ربات مطالعه‌کننده شروع شد".format(len(bot_ids)))
