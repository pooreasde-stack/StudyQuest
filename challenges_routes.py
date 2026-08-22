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

import hashlib
import json
import os
from datetime import datetime, timedelta, time as dtime

from flask import Blueprint, request, jsonify

challenges_bp = Blueprint("challenges", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STREAK_FILE = os.path.join(BASE_DIR, "challenges_streak.json")
LIVE_SESSION_FILE = os.path.join(BASE_DIR, "live_session.json")
SCHEDULE_FILE = os.path.join(BASE_DIR, "session_schedule.json")
PROGRESS_FILE = os.path.join(BASE_DIR, "progress.json")
USERS_FILE = os.path.join(BASE_DIR, "users.json")
PROOFS_DIR = os.path.join(BASE_DIR, "challenge_proofs")

# --- ثابت‌های قابل‌تنظیم (نگاه کن به توضیح بالای فایل) -----------------
FULL_DAY_MINUTES = 30
PARTIAL_DAY_MINUTES = 1
WEEKLY_XP_PER_MINUTE = 2
HEAT_DAYS_COUNT = 28
CHAIN_DAYS_COUNT = 7
MAX_BACKFILL_DAYS = 60  # سقف عقب‌گرد برای جلوگیری از حلقه‌ی خیلی طولانی
DEFAULT_TIMEZONE_OFFSET_HOURS = 3.5  # به‌وقت ایران؛ در session_schedule.json قابل override

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


def _default_live_session(today):
    return {"date": today, "participants": [], "chat": []}


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


def _get_live_session(today):
    session = _read_json(LIVE_SESSION_FILE, _default_live_session(today))
    if session.get("date") != today:
        # روز عوض شده؛ ریست روزانه
        session = _default_live_session(today)
        _write_json(LIVE_SESSION_FILE, session)
    return session


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
    leaderboard = []
    for uid, minutes in sorted(weekly_totals.items(), key=lambda kv: kv[1], reverse=True):
        display = _user_display(uid)
        leaderboard.append({
            "userId": display["userId"],
            "name": display["name"],
            "initials": display["initials"],
            "colorKey": display["colorKey"],
            "minutes": minutes,
            "isMe": uid == user_id,
        })
    if user_id not in weekly_totals:
        display = _user_display(user_id)
        leaderboard.append({
            "userId": display["userId"],
            "name": display["name"],
            "initials": display["initials"],
            "colorKey": display["colorKey"],
            "minutes": 0,
            "isMe": True,
        })

    days_left = max(0, (week_end - today).days)

    # --- جلسه‌ی زنده -----------------------------------------------
    live_session = _get_live_session(today_str)
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


@challenges_bp.route("/api/challenges/checkin", methods=["POST"])
def challenges_checkin():
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    if not user_id:
        return _error("فیلد userId الزامی است", 400)

    today_str = _today_str()
    display = _user_display(user_id)

    with _file_lock:
        session = _read_json_locked(LIVE_SESSION_FILE, _default_live_session(today_str))
        if session.get("date") != today_str:
            session = _default_live_session(today_str)

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


@challenges_bp.route("/api/challenges/chat", methods=["POST"])
def challenges_chat():
    body = request.get_json(silent=True) or {}
    user_id = body.get("userId")
    text = body.get("text")
    if not user_id or not text:
        return _error("فیلدهای userId و text الزامی‌اند", 400)

    today_str = _today_str()
    now_local = _now_local()
    display = _user_display(user_id)

    message = {
        "userId": display["userId"],
        "name": display["name"],
        "initials": display["initials"],
        "colorKey": display["colorKey"],
        "text": text,
        "time": now_local.strftime("%H:%M"),
    }

    with _file_lock:
        session = _read_json_locked(LIVE_SESSION_FILE, _default_live_session(today_str))
        if session.get("date") != today_str:
            session = _default_live_session(today_str)

        chat = session.setdefault("chat", [])
        chat.append(message)
        # فقط ۲۰۰ پیام آخر امروز نگه داشته می‌شود
        session["chat"] = chat[-200:]

        _write_json_unlocked(LIVE_SESSION_FILE, session)

    return _success(data=message, message="پیام ارسال شد")


@challenges_bp.route("/api/challenges/proof", methods=["POST"])
def challenges_proof():
    user_id = request.form.get("userId")
    if not user_id:
        return _error("فیلد userId الزامی است", 400)

    image = request.files.get("image")
    today_str = _today_str()

    if image and image.filename:
        os.makedirs(PROOFS_DIR, exist_ok=True)
        ext = os.path.splitext(image.filename)[1] or ".jpg"
        safe_name = "{}_{}{}".format(today_str, user_id, ext)
        image.save(os.path.join(PROOFS_DIR, safe_name))

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
        session = _read_json_locked(LIVE_SESSION_FILE, _default_live_session(today_str))
        if session.get("date") != today_str:
            session = _default_live_session(today_str)

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


# ================================================================
# راه‌اندازی اولیه‌ی فایل‌های داده (برای فراخوانی از server.py هنگام start)
# ================================================================

def ensure_initial_files():
    _read_json(STREAK_FILE, {})
    _read_json(SCHEDULE_FILE, _default_schedule())
    _read_json(LIVE_SESSION_FILE, _default_live_session(_today_str()))
