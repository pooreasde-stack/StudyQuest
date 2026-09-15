#!/usr/bin/env python3
"""
تولید بخشی از دیتاست intent با ۳ حالت:
1. ساخت intent از حوزه‌های راهنما (پیش‌فرض)
2. کشف حوزه‌های جدید توسط خود مدل (وقتی راهنماها تمام شد)
3. تقویت intentهای موجود (وقتی هیچ حوزه‌ی جدیدی نیست)

هر shard مستقل کار می‌کند و با shardهای دیگر ادغام می‌شود.
"""
import json
import os
import re
import sys
import time
import random
import requests
from pathlib import Path
from datetime import datetime

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = os.environ.get("MODEL", "qwen2.5:14b")
SHARD_ID = int(os.environ.get("SHARD_ID", "0"))
TOTAL_SHARDS = int(os.environ.get("TOTAL_SHARDS", "1"))
TIME_BUDGET_MIN = int(os.environ.get("TIME_BUDGET_MIN", "340"))
INTENTS_PER_ROUND = int(os.environ.get("INTENTS_PER_ROUND", "40"))
VERSION = os.environ.get("VERSION", "v0")

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SHARD_FILE = OUTPUT_DIR / f"shard_{SHARD_ID}.json"
LOG_FILE = OUTPUT_DIR / "generation_log.txt"
CHECKPOINT_FILE = OUTPUT_DIR / "checkpoint.json"
DISCOVERED_DOMAINS_FILE = OUTPUT_DIR / "discovered_domains.json"

# ═══════════════════════════════════════════════════════════════════════════
# حوزه‌های راهنمای اولیه — لیست بلند برای شروع
# ═══════════════════════════════════════════════════════════════════════════
DOMAIN_HINTS = [
    "سلام و احوال‌پرسی", "خداحافظی و پایان مکالمه", "تشکر و قدردانی",
    "معرفی خود (اسم، سن، شهر، جنسیت)", "سوالات هویتی و شخصی",
    "هدف تحصیلی (کنکور، نهایی، دانشگاه، رتبه)", "امتحان و آزمون",
    "تکلیف و پروژه", "نمره و کارنامه", "مدرسه و آموزشگاه",
    "کلاس و معلم", "مشاوره‌ی تحصیلی", "انتخاب رشته",
    "کنکور تجربی", "کنکور ریاضی", "کنکور انسانی",
    "امتحان نهایی", "درس‌های عمومی", "درس‌های تخصصی",
    "ریاضی و فرمول", "فیزیک", "شیمی", "زیست", "ادبیات", "عربی", "دینی",
    "زبان انگلیسی", "تاریخ", "جغرافیا", "اقتصاد", "فلسفه", "منطق",
    "برنامه‌ریزی روزانه", "برنامه‌ریزی هفتگی", "برنامه‌ریزی ماهانه",
    "درخواست تغییر برنامه", "لغو برنامه", "به تعویق انداختن",
    "پومودورو و تکنیک مطالعه", "تست‌زنی", "یادگیری فعال",
    "خلاصه‌نویسی", "فلش‌کارت", "حل تمرین",
    "محل مطالعه", "حواس‌پرتی", "تمرکز", "گوشی موبایل",
    "ساعت بیداری", "ساعت خواب", "کم‌خوابی", "بی‌خوابی",
    "خواب آلودگی", "چرت روزانه", "ساعت مطالعه", "زمان‌بندی",
    "وعده‌های غذایی", "صبحانه", "ناهار", "شام", "میان‌وعده",
    "خستگی", "انگیزه", "بی‌انگیزگی", "استرس", "اضطراب",
    "شادی و هیجان", "غم و افسردگی", "خشم", "ترس", "تنهایی",
    "اعتماد به نفس", "خودشناسی", "خودآگاهی", "هدف‌گذاری",
    "امید به آینده", "پشیمانی و حسرت", "خاطرات گذشته",
    "بازخورد به ربات", "امتیاز دادن", "انتقاد از ربات",
    "سوالات متداول درباره ربات", "دستورات حالت زنده",
    "توقف و ادامه", "پرش به مرحله‌ی بعدی",
    "اعتراض به برنامه", "شکایت از ربات", "گله‌مندی",
    "دعوا و بحث (بدون فحش)", "نارضایتی",
    "درخواست کمک روانی", "درخواست استراحت", "درخواست مرخصی",
    "درخواست شخصی‌سازی", "پیشنهاد به ربات",
    "رابطه با دوستان", "رابطه با خانواده", "رابطه با معلم",
    "عشق و شکست عشقی", "قلدری و آزار", "رقابت با دوستان",
    "بازی‌های ویدیویی", "شبکه‌های اجتماعی", "یوتیوب و پادکست",
    "کتاب و رمان", "موزیک و ساز", "نقاشی و طراحی", "عکاسی",
    "سفر و گردش", "طبیعت‌گردی", "آشپزی", "مد و پوشاک",
    "سلامت جسمی", "سردرد", "کمردرد", "بیماری",
    "سلامت روانی", "افسردگی", "بی‌حوصلگی", "ناامیدی",
    "خرید و بودجه", "پول توجیبی", "کار و درآمد", "کمک به خانواده",
    "مشکلات مالی", "دین و معنویت", "دعا و نیایش", "سوالات متافیزیکی",
    "تردید و شک", "بی‌تصمیمی", "مقایسه", "انتخاب بین گزینه‌ها",
    "شوخی و طنز", "لطیفه", "دلگرمی و تشویق",
    "سوالات فلسفی", "سوالات وجودی", "معنای زندگی",
    "تولد و جشن", "عزاداری و سوگ", "مرگ عزیزان",
    "تعطیلات و تفریح", "مسافرت",
    "مقایسه‌ی دانشگاه‌ها", "شهریه و هزینه", "خوابگاه",
    "دور بودن از خانواده", "مهاجرت", "آینده‌ی شغلی",
    "غلبه بر ترس", "کنترل خشم", "بخشش", "شکرگزاری",
    "روزهای خوب و بد", "پیش‌بینی آینده", "تصمیم‌های سخت",
    "همدلی", "دلداری دادن", "نصیحت خواستن", "نصیحت کردن",
    "تعریف و تحسین", "قدردانی", "عذرخواهی", "قبول عذرخواهی",
    "قول دادن", "تعهد", "مسئولیت‌پذیری", "نظم و انضباط",
    "عادت‌سازی", "ترک عادت بد", "شکست و بازگشت",
]

# ═══════════════════════════════════════════════════════════════════════════
# پرامپت‌ها — سه حالت مختلف
# ═══════════════════════════════════════════════════════════════════════════

# حالت ۱: ساخت intent از حوزه‌های راهنما
PROMPT_FROM_HINTS = """# نقش
تو یک متخصص NLU هستی که برای ربات StudyQuest دیتاست intent می‌سازی.

# وظیفه
دقیقاً {n_intents} intent از حوزه‌های زیر بساز. برای هر intent، ۱۵ تا ۲۰ نمونه.

# حوزه‌های این دور
{hint_domains}

# قوانین intent
- نام انگلیسی snake_case، معنای واحد و مشخص
- intentهای مشابه را جدا کن (ask_wake ≠ tell_wake)

# قوانین نمونه (تنوع الزامی)
- طول: کوتاه/متوسط/بلند
- لحن: رسمی/محاوره/خودمونی/شوخ
- ارقام: «۱۰»/«10»/«ده»/«۱۰:۳۰»
- مترادف‌ها: پا می‌شم، بلند می‌شم، از خواب درمیام
- ساختار: مثبت/منفی/سوالی/شرطی
- محاوره: «آره خب»، «خب معلومه»، «چرا که نه»
- ایموجی به مقدار کم: 😩 😐 😊

# ممنوع
❌ تکرار فقط با تغییر یک کلمه
❌ جملات کتابی
❌ بیشتر از ۱۵ کلمه
❌ فحش

# intentهای قبلی (تکرار نکن)
{existing_sample}

# خروجی
فقط JSON خالص، بدون markdown، بدون توضیح:
{{
  "intent_name_1": ["جمله ۱", "جمله ۲", ...],
  "intent_name_2": [...]
}}"""

# حالت ۲: کشف حوزه‌های جدید توسط خود مدل
PROMPT_DISCOVER_DOMAINS = """# نقش
تو یک متخصص NLU هستی که برای ربات StudyQuest دیتاست intent می‌سازی.

# وظیفه
حوزه‌های راهنمای اولیه تمام شده‌اند. الان خودت باید **{n_new_domains} حوزه‌ی جدید و کاملاً متفاوت** کشف کنی که تا الان پوشش داده نشده.

# قوانین حوزه‌های جدید
- هر حوزه باید با حوزه‌های موجود کاملاً متفاوت باشد
- حوزه‌ها باید مرتبط با یک ربات برنامه‌ریز درسی فارسی باشند
- به جنبه‌های پنهان، احساسی، فرهنگی، یا روزمره فکر کن که فراموش شده‌اند
- خلاق باش — حوزه‌های غیرمنتظره و عمیق بساز

# حوزه‌های موجود (این‌ها را تکرار نکن)
{existing_domains}

# خروجی
فقط JSON خالص، بدون markdown:
{{
  "new_domains": [
    "حوزه‌ی جدید ۱",
    "حوزه‌ی جدید ۲",
    ...
  ]
}}"""

# حالت ۳: تقویت intentهای موجود (نمونه‌های بیشتر + intentهای مرتبط)
PROMPT_ENHANCE = """# نقش
تو یک متخصص NLU هستی که برای ربات StudyQuest دیتاست intent می‌سازی.

# وظیفه
intentهای زیر از قبل ساخته شده‌اند ولی هرکدام فقط چند نمونه دارند. وظیفه‌ی تو:

۱. برای **{n_target} intent زیر**، **۱۰ تا ۱۵ نمونه‌ی جدید و متفاوت** اضافه کن (تکرار نکن).
۲. برای هر intent، **۲ تا ۳ intent مرتبط و ظریف** بساز که تا الان پوشش داده نشده.

# intentهای هدف
{target_intents}

# intentهای موجود (برای اطمینان از عدم تکرار)
{existing_sample}

# قوانین نمونه‌های جدید (تنوع الزامی)
- لحن: رسمی/محاوره/خودمونی/شوخ
- ارقام: «۱۰»/«10»/«ده»
- ساختار: مثبت/منفی/سوالی/شرطی/با توضیح
- محاوره‌های واقعی
- احساسات مختلف

# خروجی
فقط JSON خالص، بدون markdown. دو بخش:

{{
  "new_samples_for_existing": {{
    "intent_name_1": ["جمله‌ی جدید ۱", "جمله‌ی جدید ۲", ...],
    "intent_name_2": [...]
  }},
  "new_related_intents": {{
    "new_intent_name_1": ["جمله ۱", "جمله ۲", ...],
    ...
  }}
}}"""


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] [shard-{SHARD_ID}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def call_ollama(prompt, max_tokens=8000, timeout=1200):
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.95,      # برای تنوع بیشتر
            "top_p": 0.95,
            "top_k": 80,
            "repeat_penalty": 1.15,
        },
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("response", "")


def extract_json(text):
    """استخراج JSON از پاسخ مدل، با تحمل markdown و متن اضافه."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def clean_intents_dict(data):
    """پاک‌سازی و اعتبارسنجی intentها — فقط کلیدهای snake_case با مقادیر آرایه‌ای از رشته."""
    cleaned = {}
    for k, v in data.items():
        if not isinstance(k, str) or not re.match(r"^[a-z][a-z0-9_]*$", k):
            continue
        if not isinstance(v, list):
            continue
        samples = [s.strip() for s in v if isinstance(s, str) and s.strip()]
        if len(samples) >= 3:
            cleaned[k] = samples
    return cleaned


def load_state():
    if CHECKPOINT_FILE.exists():
        try:
            state = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            if "dataset" in state and "domains_used" in state:
                return state
        except Exception:
            pass
    return {
        "dataset": {},
        "domains_used": [],
        "discovered_domains": [],
        "iteration": 0,
        "mode_history": [],
    }


def save_state(state):
    CHECKPOINT_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    SHARD_FILE.write_text(
        json.dumps(state["dataset"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    DISCOVERED_DOMAINS_FILE.write_text(
        json.dumps({
            "discovered": state.get("discovered_domains", []),
            "used": state.get("domains_used", []),
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def sample_existing(dataset, max_intents=80):
    names = list(dataset.keys())
    if len(names) <= max_intents:
        return "\n".join(f"- {n}" for n in names) or "(هیچ)"
    step = max(1, len(names) // max_intents)
    sampled = names[::step][:max_intents]
    return "\n".join(f"- {n}" for n in sampled) + f"\n(و {len(names) - len(sampled)} مورد دیگر)"


def pick_domains_from_hints(used, count=6):
    """انتخاب حوزه از راهنماهای اولیه که هنوز استفاده نشده‌اند."""
    remaining = [d for d in DOMAIN_HINTS if d not in used]
    if len(remaining) < count:
        return None  # راهنماها تمام شده
    random.seed(SHARD_ID * 1000 + len(used))
    random.shuffle(remaining)
    return remaining[:count]


def pick_weak_intents(dataset, count=8):
    """intentهایی که کمترین نمونه را دارند برای تقویت انتخاب کن."""
    items = sorted(dataset.items(), key=lambda kv: len(kv[1]))
    return [k for k, v in items[:count] if len(v) < 20]


def get_existing_domains_for_discovery(state):
    """لیست همه‌ی حوزه‌های موجود (راهنما + کشف‌شده) برای جلوگیری از تکرار."""
    all_domains = list(DOMAIN_HINTS) + state.get("discovered_domains", [])
    return all_domains


# ═══════════════════════════════════════════════════════════════════════════
# حالت‌های اجرا
# ═══════════════════════════════════════════════════════════════════════════

def run_from_hints(state, iteration):
    """حالت ۱: ساخت intent از حوزه‌های راهنما."""
    domains = pick_domains_from_hints(state["domains_used"], count=6)
    if domains is None:
        return None  # راهنماها تمام شده

    prompt = PROMPT_FROM_HINTS.format(
        n_intents=INTENTS_PER_ROUND,
        hint_domains="\n".join(f"- {d}" for d in domains),
        existing_sample=sample_existing(state["dataset"]),
    )
    log(f"   📘 حالت: from_hints | {len(domains)} حوزه")
    return prompt, domains, "from_hints"


def run_discover_domains(state, iteration):
    """حالت ۲: خود مدل حوزه‌های جدید کشف کند."""
    existing_domains = get_existing_domains_for_discovery(state)
    n_new = 15

    prompt = PROMPT_DISCOVER_DOMAINS.format(
        n_new_domains=n_new,
        existing_domains="\n".join(f"- {d}" for d in existing_domains),
    )
    log(f"   🧭 حالت: discover_domains | تلاش برای کشف {n_new} حوزه‌ی جدید")
    return prompt, None, "discover_domains"


def run_enhance(state, iteration):
    """حالت ۳: تقویت intentهای موجود."""
    weak = pick_weak_intents(state["dataset"], count=8)
    if not weak:
        return None

    target_intents = "\n".join(
        f"- {name} ({len(state['dataset'][name])} نمونه): {state['dataset'][name][:2]}"
        for name in weak
    )

    prompt = PROMPT_ENHANCE.format(
        n_target=len(weak),
        target_intents=target_intents,
        existing_sample=sample_existing(state["dataset"]),
    )
    log(f"   🔧 حالت: enhance | هدف: {len(weak)} intent ضعیف")
    return prompt, weak, "enhance"


def apply_hints_result(state, parsed, domains):
    """اعمال نتیجه‌ی حالت from_hints."""
    new_count = 0
    merged_count = 0
    for intent, samples in parsed.items():
        if intent in state["dataset"]:
            existing = set(state["dataset"][intent])
            added = [s for s in samples if s not in existing]
            if added:
                state["dataset"][intent] = (state["dataset"][intent] + added)[:30]
                merged_count += 1
        else:
            state["dataset"][intent] = samples[:20]
            new_count += 1
    state["domains_used"].extend(domains)
    return new_count, merged_count


def apply_discover_result(state, parsed):
    """اعمال نتیجه‌ی حالت discover_domains."""
    new_domains = parsed.get("new_domains", [])
    if not isinstance(new_domains, list):
        return 0
    added = 0
    existing = set(state.get("discovered_domains", []))
    for d in new_domains:
        if isinstance(d, str) and d.strip() and d not in existing:
            state.setdefault("discovered_domains", []).append(d.strip())
            added += 1
    log(f"      ✨ {added} حوزه‌ی جدید کشف شد")
    return added


def apply_enhance_result(state, parsed):
    """اعمال نتیجه‌ی حالت enhance (نمونه‌های جدید + intentهای مرتبط)."""
    new_samples = parsed.get("new_samples_for_existing", {})
    new_related = parsed.get("new_related_intents", {})

    added_samples = 0
    added_intents = 0

    if isinstance(new_samples, dict):
        for intent, samples in new_samples.items():
            if intent not in state["dataset"]:
                continue
            if not isinstance(samples, list):
                continue
            existing = set(state["dataset"][intent])
            added = [s.strip() for s in samples if isinstance(s, str) and s.strip() and s.strip() not in existing]
            if added:
                state["dataset"][intent] = (state["dataset"][intent] + added)[:30]
                added_samples += len(added)

    if isinstance(new_related, dict):
        cleaned = clean_intents_dict(new_related)
        for intent, samples in cleaned.items():
            if intent not in state["dataset"]:
                state["dataset"][intent] = samples[:20]
                added_intents += 1

    return added_samples, added_intents


# ═══════════════════════════════════════════════════════════════════════════
# حلقه‌ی اصلی
# ═══════════════════════════════════════════════════════════════════════════

def main():
    log("=" * 70)
    log(f"🚀 shard {SHARD_ID}/{TOTAL_SHARDS} — مدل: {MODEL} | نسخه: {VERSION}")
    log(f"⏱️ بودجه: {TIME_BUDGET_MIN} دقیقه")
    log("=" * 70)

    start_time = time.time()
    deadline = start_time + (TIME_BUDGET_MIN * 60)

    state = load_state()
    if state["dataset"]:
        log(f"♻️ بازیابی: {len(state['dataset'])} intent | "
            f"{len(state['domains_used'])} حوزه‌ی استفاده‌شده | "
            f"{len(state.get('discovered_domains', []))} حوزه‌ی کشف‌شده")

    iteration = state.get("iteration", 0)
    mode_history = state.get("mode_history", [])
    max_iterations = 10000

    while time.time() < deadline and iteration < max_iterations:
        iteration += 1
        remaining_min = (deadline - time.time()) / 60
        log(f"🔁 دور {iteration} | intent: {len(state['dataset'])} | زمان باقی: {remaining_min:.1f}m")

        # انتخاب حالت: اول hints، اگر تموم شد → discover، اگر اونم تموم شد → enhance
        result = run_from_hints(state, iteration)
        if result is None:
            # راهنماها تموم شد → تلاش برای کشف حوزه‌ی جدید
            hints_attempts = sum(1 for m in mode_history[-10:] if m == "from_hints")
            discover_result = run_discover_domains(state, iteration)
            # اگر کشف حوزه زیاد جواب داده، برگرد به hints با حوزه‌های کشف‌شده
            if hints_attempts >= 3 and state.get("discovered_domains"):
                # حوزه‌های کشف‌شده رو موقتاً جایگزین راهنماها کن
                remaining = [d for d in state["discovered_domains"] if d not in state["domains_used"]]
                if remaining:
                    result = (None, remaining[:6], "discovered_hints")
                else:
                    result = discover_result
            else:
                result = discover_result

        if result is None:
            # اگر هیچ راهی نبود → enhance
            result = run_enhance(state, iteration)

        if result is None:
            log("   ⚠️ هیچ حالتی قابل اجرا نبود — احتمالاً دیتاست خالی است")
            time.sleep(5)
            continue

        prompt, metadata, mode = result
        mode_history.append(mode)
        state["mode_history"] = mode_history[-50:]  # فقط ۵۰ تای آخر

        # ساخت پرامپت برای حالت discovered_hints
        if mode == "discovered_hints":
            prompt = PROMPT_FROM_HINTS.format(
                n_intents=INTENTS_PER_ROUND,
                hint_domains="\n".join(f"- {d}" for d in metadata),
                existing_sample=sample_existing(state["dataset"]),
            )

        # فراخوانی مدل
        try:
            t0 = time.time()
            timeout_sec = min(1200, max(60, int(deadline - time.time()) - 30))
            response = call_ollama(prompt, timeout=timeout_sec)
            elapsed = time.time() - t0
            log(f"   ⏱️ پاسخ در {elapsed:.0f}s ({len(response)} کاراکتر)")
        except requests.exceptions.Timeout:
            log(f"   ⏰ timeout")
            continue
        except Exception as e:
            log(f"   ❌ خطا: {e}")
            time.sleep(5)
            continue

        parsed = extract_json(response)
        if not parsed:
            log(f"   ⚠️ JSON نامعتبر — {response[:120]!r}")
            continue

        # اعمال نتیجه بسته به حالت
        if mode in ("from_hints", "discovered_hints"):
            n_new, n_merged = apply_hints_result(state, parsed, metadata or [])
            log(f"   ✅ +{n_new} intent جدید | +{n_merged} ادغام")
        elif mode == "discover_domains":
            n_domains = apply_discover_result(state, parsed)
            if n_domains == 0:
                log(f"   ⚠️ هیچ حوزه‌ی جدیدی کشف نشد")
        elif mode == "enhance":
            n_samples, n_intents = apply_enhance_result(state, parsed)
            log(f"   🔧 +{n_samples} نمونه به intentهای موجود | +{n_intents} intent جدید مرتبط")

        # ذخیره‌ی وضعیت
        state["iteration"] = iteration
        save_state(state)

        size_kb = SHARD_FILE.stat().st_size / 1024 if SHARD_FILE.exists() else 0
        log(f"   📊 مجموع: {len(state['dataset'])} intent | حجم: {size_kb:.1f}KB")

    # پایان
    elapsed_total = (time.time() - start_time) / 60
    log("=" * 70)
    log(f"🏁 shard {SHARD_ID} پایان — {iteration} دور در {elapsed_total:.1f} دقیقه")
    log(f"📦 {len(state['dataset'])} intent | فایل: {SHARD_FILE}")

    total_samples = sum(len(v) for v in state["dataset"].values())
    log(f"📝 {total_samples} نمونه‌ی یکتا در کل")

    # ثبت متادیتا
    meta = {
        "shard_id": SHARD_ID,
        "total_shards": TOTAL_SHARDS,
        "version": VERSION,
        "intents": len(state["dataset"]),
        "samples": total_samples,
        "iterations": iteration,
        "elapsed_minutes": round(elapsed_total, 2),
        "model": MODEL,
        "domains_used": len(state["domains_used"]),
        "discovered_domains": len(state.get("discovered_domains", [])),
        "mode_distribution": {
            m: mode_history.count(m) for m in set(mode_history)
        },
    }
    (OUTPUT_DIR / "shard_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("⏹️ متوقف شد")
    except Exception as e:
        log(f"💥 خطا: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
