#!/usr/bin/env python3
"""
تولید دیتاست intent با ذخیره‌سازی state در output/ و استفاده از
format=json در Ollama برای اجبار به خروجی JSON معتبر.

سه حالت کاری:
  1. from_hints      → ساخت intent از حوزه‌های راهنما
  2. discovered_hints → ساخت intent از حوزه‌های کشف‌شده توسط مدل
  3. discover_domains → خود مدل حوزه‌ی جدید کشف می‌کند
  4. enhance         → تقویت intentهای کم‌نمونه + ساخت intentهای مرتبط
"""
import json
import os
import re
import sys
import time
import random
import requests
from pathlib import Path
from datetime import datetime, timezone

# ═══════════════════════════════════════════════════════════════════════════
# تنظیمات
# ═══════════════════════════════════════════════════════════════════════════
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL = os.environ.get("MODEL", "qwen2.5:14b")
SHARD_ID = int(os.environ.get("SHARD_ID", "0"))
TOTAL_SHARDS = int(os.environ.get("TOTAL_SHARDS", "1"))
TIME_BUDGET_MIN = int(os.environ.get("TIME_BUDGET_MIN", "340"))
INTENTS_PER_ROUND = int(os.environ.get("INTENTS_PER_ROUND", "20"))
VERSION = os.environ.get("VERSION", "v0")

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SHARD_FILE = OUTPUT_DIR / f"shard_{SHARD_ID}.json"
CHECKPOINT_FILE = OUTPUT_DIR / "checkpoint.json"
DISCOVERED_DOMAINS_FILE = OUTPUT_DIR / "discovered_domains.json"
SHARD_META_FILE = OUTPUT_DIR / "shard_meta.json"
LOG_FILE = OUTPUT_DIR / "generation_log.txt"

# ═══════════════════════════════════════════════════════════════════════════
# حوزه‌های راهنما (اولیه)
# ═══════════════════════════════════════════════════════════════════════════
DOMAIN_HINTS = [
    "سلام و احوال‌پرسی", "خداحافظی و پایان مکالمه", "تشکر و قدردانی",
    "معرفی خود (اسم، سن، شهر، جنسیت)", "سوالات هویتی و شخصی",
    "هدف تحصیلی (کنکور، نهایی، دانشگاه، رتبه)", "امتحان و آزمون",
    "تکلیف و پروژه", "نمره و کارنامه", "مدرسه و آموزشگاه",
    "کلاس و معلم", "مشاوره‌ی تحصیلی", "انتخاب رشته",
    "کنکور تجربی", "کنکور ریاضی", "کنکور انسانی", "امتحان نهایی",
    "درس‌های عمومی", "درس‌های تخصصی",
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
# پرامپت‌ها
# ═══════════════════════════════════════════════════════════════════════════
PROMPT_FROM_HINTS = """# نقش
تو متخصص NLU هستی و برای ربات StudyQuest دیتاست intent می‌سازی.

# وظیفه
دقیقاً {n_intents} intent از حوزه‌های زیر بساز. هر intent: ۱۰ تا ۱۵ نمونه.

# حوزه‌ها
{hint_domains}

# قوانین intent
- نام انگلیسی snake_case، معنای واحد
- intentهای مشابه جدا باشند (ask_wake ≠ tell_wake)

# تنوع نمونه (الزامی)
- طول: کوتاه/متوسط/بلند
- لحن: رسمی/محاوره/خودمونی/شوخ
- ارقام: «۱۰»/«10»/«ده»/«۱۰:۳۰»
- مترادف‌ها: پا می‌شم، بلند می‌شم، از خواب درمیام
- ساختار: مثبت/منفی/سوالی/شرطی/با توضیح
- محاوره: «آره خب»، «خب معلومه»، «چرا که نه»

# ممنوع
❌ تکرار با تغییر یک کلمه
❌ جملات کتابی
❌ بیش از ۱۵ کلمه
❌ فحش و رکیک

# intentهای قبلی (تکرار نکن)
{existing_sample}

# خروجی
فقط JSON خالص بدون markdown، بدون توضیح."""

PROMPT_DISCOVER_DOMAINS = """# نقش
تو متخصص NLU هستی و برای ربات StudyQuest حوزه‌های جدید کشف می‌کنی.

# وظیفه
دقیقاً {n_new_domains} حوزه‌ی جدید و کاملاً متفاوت کشف کن که تا الان پوشش داده نشده.

# قوانین
- هر حوزه با حوزه‌های موجود کاملاً متفاوت باشد
- مرتبط با ربات برنامه‌ریز درسی فارسی
- به جنبه‌های پنهان/احساسی/فرهنگی فکر کن
- خلاق باش

# حوزه‌های موجود (تکرار نکن)
{existing_domains}

# خروجی
فقط JSON خالص: {{"new_domains": ["حوزه‌ی جدید ۱", "حوزه‌ی جدید ۲"]}}"""

PROMPT_ENHANCE = """# نقش
تو متخصص NLU هستی.

# وظیفه
intentهای زیر کم‌نمونه هستند. برای هرکدام:
۱. ۱۰ تا ۱۵ نمونه‌ی جدید و متفاوت اضافه کن
۲. ۲ تا ۳ intent مرتبط و ظریف بساز

# intentهای هدف
{target_intents}

# intentهای موجود (تکرار نکن)
{existing_sample}

# قوانین نمونه
- لحن متنوع، ارقام متنوع، ساختار متنوع
- محاوره‌های واقعی
- ایموجی به مقدار کم

# خروجی
فقط JSON خالص:
{{
  "new_samples_for_existing": {{"intent_name_1": ["...", "..."]}},
  "new_related_intents": {{"new_intent_name": ["...", "..."]}}
}}"""


# ═══════════════════════════════════════════════════════════════════════════
# لاگ
# ═══════════════════════════════════════════════════════════════════════════
def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] [s{SHARD_ID}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# فراخوانی Ollama با format=json (اجبار به JSON معتبر)
# ═══════════════════════════════════════════════════════════════════════════
def call_ollama(prompt, max_tokens=4000, timeout=1200):
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",           # ← کلید اصلی: اجبار به JSON معتبر
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.9,
            "top_p": 0.95,
            "top_k": 80,
            "repeat_penalty": 1.15,
        },
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("response", "")


# ═══════════════════════════════════════════════════════════════════════════
# استخراج JSON مقاوم
# ═══════════════════════════════════════════════════════════════════════════
def extract_json(text):
    """
    استخراج JSON از پاسخ مدل با تحمل خطاهای رایج:
    1. markdown code fences
    2. متن اضافه قبل/بعد
    3. رشته‌های شکسته با newline خام (بدون escape)
    4. JSON ناقص (استخراج intentهای کامل)
    """
    if not text:
        return None

    # ۱. حذف markdown fences
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)

    # ۲. پیدا کردن محدوده‌ی { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None

    candidate = text[start:end + 1]

    # تلاش اول: JSON خام
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # تلاش دوم: ترمیم newlineهای escape‌نشده داخل رشته‌ها
    fixed = re.sub(r'(?<!\\)\n(?=[^"]*")', '\\\\n', candidate)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # تلاش سوم: استخراج intentهای کامل از JSON ناقص
    repaired = _repair_truncated_json(candidate)
    if repaired:
        return repaired

    return None


def _repair_truncated_json(text):
    """اگر JSON ناقص باشد، intentهای کامل را استخراج می‌کند."""
    pattern = r'"([a-z][a-z0-9_]*)"\s*:\s*\[((?:[^\[\]]|\[[^\]]*\])*?)\]'
    matches = re.findall(pattern, text)
    if not matches:
        return None
    result = {}
    for key, samples_str in matches:
        try:
            samples = json.loads(f"[{samples_str}]")
            if isinstance(samples, list) and len(samples) >= 3:
                result[key] = [s for s in samples if isinstance(s, str) and s.strip()]
        except json.JSONDecodeError:
            inner = samples_str.strip().strip(",")
            parts = re.findall(r'"([^"]+)"', inner)
            if len(parts) >= 3:
                result[key] = parts
    return result if result else None


def clean_intents_dict(data):
    """فقط intentهای معتبر snake_case با آرایه‌ی حداقل ۳ رشته‌ای."""
    cleaned = {}
    if not isinstance(data, dict):
        return cleaned
    for k, v in data.items():
        if not isinstance(k, str) or not re.match(r"^[a-z][a-z0-9_]*$", k):
            continue
        if not isinstance(v, list):
            continue
        samples = [s.strip() for s in v if isinstance(s, str) and s.strip()]
        if len(samples) >= 3:
            cleaned[k] = samples
    return cleaned


# ═══════════════════════════════════════════════════════════════════════════
# State Management
# ═══════════════════════════════════════════════════════════════════════════
def load_state():
    """بارگذاری state از output/ (که ورک‌فلو از dataset_state/ کپی کرده)."""
    state = {
        "dataset": {},
        "domains_used": [],
        "discovered_domains": [],
        "iteration": 0,
        "mode_history": [],
    }

    if SHARD_FILE.exists():
        try:
            data = json.loads(SHARD_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                state["dataset"] = data
                log(f"♻️ دیتاست قبلی: {len(data)} intent")
        except Exception as e:
            log(f"⚠️ خطا در خواندن دیتاست: {e}")

    if CHECKPOINT_FILE.exists():
        try:
            meta = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            for key in ("domains_used", "discovered_domains", "iteration", "mode_history"):
                if key in meta and meta[key]:
                    state[key] = meta[key]
            log(f"♻️ checkpoint: iter={state['iteration']}, "
                f"used={len(state['domains_used'])}, "
                f"discovered={len(state['discovered_domains'])}")
        except Exception as e:
            log(f"⚠️ خطا در خواندن checkpoint: {e}")

    if DISCOVERED_DOMAINS_FILE.exists():
        try:
            dd = json.loads(DISCOVERED_DOMAINS_FILE.read_text(encoding="utf-8"))
            if isinstance(dd, dict):
                if isinstance(dd.get("discovered"), list):
                    state["discovered_domains"] = dd["discovered"]
                if isinstance(dd.get("used"), list) and not state["domains_used"]:
                    state["domains_used"] = dd["used"]
        except Exception:
            pass

    return state


def save_state(state):
    """ذخیره state در output/ (ورک‌فلو بعداً آن را به dataset_state/ می‌برد)."""
    SHARD_FILE.write_text(
        json.dumps(state["dataset"], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    meta = {
        "iteration": state.get("iteration", 0),
        "domains_used": state.get("domains_used", []),
        "discovered_domains": state.get("discovered_domains", []),
        "mode_history": state.get("mode_history", [])[-50:],
        "intents": len(state["dataset"]),
        "samples": sum(len(v) for v in state["dataset"].values()),
        "shard_id": SHARD_ID,
        "version": VERSION,
        "model": MODEL,
        "last_update": datetime.now(timezone.utc).isoformat(),
    }
    CHECKPOINT_FILE.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    DISCOVERED_DOMAINS_FILE.write_text(
        json.dumps({
            "discovered": state.get("discovered_domains", []),
            "used": state.get("domains_used", []),
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    shard_meta = {
        "shard_id": SHARD_ID,
        "total_shards": TOTAL_SHARDS,
        "version": VERSION,
        "model": MODEL,
        "intents": len(state["dataset"]),
        "samples": sum(len(v) for v in state["dataset"].values()),
        "iterations": state.get("iteration", 0),
        "domains_used": len(state.get("domains_used", [])),
        "discovered_domains": len(state.get("discovered_domains", [])),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    SHARD_META_FILE.write_text(
        json.dumps(shard_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ═══════════════════════════════════════════════════════════════════════════
# کمک‌کننده‌ها
# ═══════════════════════════════════════════════════════════════════════════
def sample_existing(dataset, max_intents=80):
    names = list(dataset.keys())
    if not names:
        return "(هیچ)"
    if len(names) <= max_intents:
        return "\n".join(f"- {n}" for n in names)
    step = max(1, len(names) // max_intents)
    sampled = names[::step][:max_intents]
    return "\n".join(f"- {n}" for n in sampled) + f"\n(و {len(names) - len(sampled)} مورد دیگر)"


def pick_domains_from_hints(used, count=6):
    remaining = [d for d in DOMAIN_HINTS if d not in used]
    if len(remaining) < count:
        return None
    random.seed(SHARD_ID * 1000 + len(used))
    random.shuffle(remaining)
    return remaining[:count]


def pick_weak_intents(dataset, count=8):
    items = sorted(dataset.items(), key=lambda kv: len(kv[1]))
    return [k for k, v in items[:count] if len(v) < 20]


def get_all_known_domains(state):
    return list(DOMAIN_HINTS) + list(state.get("discovered_domains", []))


# ═══════════════════════════════════════════════════════════════════════════
# اعمال نتایج
# ═══════════════════════════════════════════════════════════════════════════
def apply_hints_result(state, parsed, domains):
    new_count, merged_count = 0, 0
    cleaned = clean_intents_dict(parsed)
    for intent, samples in cleaned.items():
        if intent in state["dataset"]:
            existing = set(state["dataset"][intent])
            added = [s for s in samples if s not in existing]
            if added:
                state["dataset"][intent] = (state["dataset"][intent] + added)[:30]
                merged_count += 1
        else:
            state["dataset"][intent] = samples[:20]
            new_count += 1
    state["domains_used"].extend(domains or [])
    if len(state["domains_used"]) > 500:
        state["domains_used"] = state["domains_used"][-300:]
    return new_count, merged_count


def apply_discover_result(state, parsed):
    nd = parsed.get("new_domains", [])
    if not isinstance(nd, list):
        return 0
    existing = set(state.get("discovered_domains", []))
    added = 0
    for d in nd:
        if isinstance(d, str) and d.strip() and d.strip() not in existing:
            state.setdefault("discovered_domains", []).append(d.strip())
            added += 1
    return added


def apply_enhance_result(state, parsed):
    ns = parsed.get("new_samples_for_existing", {})
    nr = parsed.get("new_related_intents", {})
    added_samples, added_intents = 0, 0

    if isinstance(ns, dict):
        for intent, samples in ns.items():
            if intent not in state["dataset"] or not isinstance(samples, list):
                continue
            existing = set(state["dataset"][intent])
            added = [s.strip() for s in samples
                     if isinstance(s, str) and s.strip() and s.strip() not in existing]
            if added:
                state["dataset"][intent] = (state["dataset"][intent] + added)[:30]
                added_samples += len(added)

    if isinstance(nr, dict):
        cleaned = clean_intents_dict(nr)
        for intent, samples in cleaned.items():
            if intent not in state["dataset"]:
                state["dataset"][intent] = samples[:20]
                added_intents += 1

    return added_samples, added_intents


# ═══════════════════════════════════════════════════════════════════════════
# انتخاب حالت و ساخت پرامپت
# ═══════════════════════════════════════════════════════════════════════════
def choose_mode_and_prompt(state):
    # ۱. راهنماهای اولیه
    domains = pick_domains_from_hints(state["domains_used"], count=6)
    if domains:
        prompt = PROMPT_FROM_HINTS.format(
            n_intents=INTENTS_PER_ROUND,
            hint_domains="\n".join(f"- {d}" for d in domains),
            existing_sample=sample_existing(state["dataset"]),
        )
        return "from_hints", prompt, domains

    # ۲. حوزه‌های کشف‌شده
    remaining_discovered = [
        d for d in state.get("discovered_domains", [])
        if d not in state["domains_used"]
    ]
    if len(remaining_discovered) >= 3:
        chosen = remaining_discovered[:6]
        prompt = PROMPT_FROM_HINTS.format(
            n_intents=INTENTS_PER_ROUND,
            hint_domains="\n".join(f"- {d}" for d in chosen),
            existing_sample=sample_existing(state["dataset"]),
        )
        return "discovered_hints", prompt, chosen

    # ۳. کشف حوزه‌ی جدید
    all_domains = get_all_known_domains(state)
    prompt = PROMPT_DISCOVER_DOMAINS.format(
        n_new_domains=15,
        existing_domains="\n".join(f"- {d}" for d in all_domains),
    )
    return "discover_domains", prompt, None


def build_enhance_prompt(state):
    weak = pick_weak_intents(state["dataset"], count=8)
    if not weak:
        return None
    target = "\n".join(
        f"- {name} ({len(state['dataset'][name])} نمونه): {state['dataset'][name][:2]}"
        for name in weak
    )
    prompt = PROMPT_ENHANCE.format(
        target_intents=target,
        existing_sample=sample_existing(state["dataset"]),
    )
    return prompt


# ═══════════════════════════════════════════════════════════════════════════
# حلقه‌ی اصلی
# ═══════════════════════════════════════════════════════════════════════════
def main():
    log("=" * 70)
    log(f"🚀 shard {SHARD_ID}/{TOTAL_SHARDS} | model: {MODEL} | version: {VERSION}")
    log(f"⏱️ budget: {TIME_BUDGET_MIN} min | state: {SHARD_FILE}")
    log("=" * 70)

    start = time.time()
    deadline = start + TIME_BUDGET_MIN * 60

    state = load_state()
    iteration = state.get("iteration", 0)

    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 5

    while time.time() < deadline:
        iteration += 1
        remaining_min = (deadline - time.time()) / 60
        log(f"🔁 iter {iteration} | intents: {len(state['dataset'])} | "
            f"left: {remaining_min:.1f}m")

        mode, prompt, metadata = choose_mode_and_prompt(state)
        log(f"   🎯 mode: {mode}")

        try:
            t0 = time.time()
            timeout_sec = min(1200, max(60, int(deadline - time.time()) - 30))
            response = call_ollama(prompt, timeout=timeout_sec)
            elapsed = time.time() - t0
            log(f"   ⏱️ {elapsed:.0f}s | {len(response)} chars")
            consecutive_failures = 0
        except requests.exceptions.Timeout:
            log("   ⏰ timeout")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log("   ❌ تعداد خطاهای متوالی زیاد — توقف")
                break
            continue
        except Exception as e:
            log(f"   ❌ خطا: {e}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log("   ❌ تعداد خطاهای متوالی زیاد — توقف")
                break
            time.sleep(5)
            continue

        parsed = extract_json(response)
        if not parsed:
            log(f"   ⚠️ JSON نامعتبر: {response[:120]!r}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log("   ❌ تعداد خطاهای متوالی زیاد — توقف")
                break
            continue

        # اعمال نتیجه
        if mode in ("from_hints", "discovered_hints"):
            n_new, n_merged = apply_hints_result(state, parsed, metadata)
            log(f"   ✅ +{n_new} intent جدید | +{n_merged} ادغام")

        elif mode == "discover_domains":
            n_domains = apply_discover_result(state, parsed)
            log(f"   🧭 +{n_domains} حوزه‌ی جدید")

            if n_domains == 0:
                log("   ⚠️ هیچ حوزه‌ی جدیدی کشف نشد — می‌رویم enhance")
                enhance_prompt = build_enhance_prompt(state)
                if enhance_prompt:
                    try:
                        resp2 = call_ollama(
                            enhance_prompt,
                            timeout=min(900, max(60, int(deadline - time.time()) - 30))
                        )
                        parsed2 = extract_json(resp2)
                        if parsed2:
                            ns, ni = apply_enhance_result(state, parsed2)
                            log(f"   🔧 +{ns} نمونه | +{ni} intent مرتبط")
                    except Exception as e:
                        log(f"   ❌ enhance: {e}")

        state["iteration"] = iteration
        state.setdefault("mode_history", []).append(mode)
        state["mode_history"] = state["mode_history"][-50:]
        save_state(state)

        size_kb = SHARD_FILE.stat().st_size / 1024 if SHARD_FILE.exists() else 0
        log(f"   📊 مجموع: {len(state['dataset'])} intent | {size_kb:.1f}KB")

    elapsed_total = (time.time() - start) / 60
    log("=" * 70)
    log(f"🏁 shard {SHARD_ID} پایان — {iteration} دور در {elapsed_total:.1f} دقیقه")
    log(f"📦 {len(state['dataset'])} intent | "
        f"{sum(len(v) for v in state['dataset'].values())} نمونه")
    log("=" * 70)

    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("⏹️ متوقف شد توسط کاربر")
        sys.exit(0)
    except Exception as e:
        log(f"💥 خطای غیرمنتظره: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
