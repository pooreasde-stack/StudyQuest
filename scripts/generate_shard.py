#!/usr/bin/env python3
"""
تولید بخشی از دیتاست intent detection در یک shard مشخص.
هر shard فقط روی زیرمجموعه‌ای از حوزه‌ها کار می‌کند تا تنوع تضمین شود.
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
MODEL = os.environ.get("MODEL", "qwen2.5:7b")
SHARD_ID = int(os.environ.get("SHARD_ID", "0"))
TOTAL_SHARDS = int(os.environ.get("TOTAL_SHARDS", "1"))
TIME_BUDGET_MIN = int(os.environ.get("TIME_BUDGET_MIN", "280"))
INTENTS_PER_ROUND = int(os.environ.get("INTENTS_PER_ROUND", "40"))
VERSION = os.environ.get("VERSION", "v0")

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SHARD_FILE = OUTPUT_DIR / f"shard_{SHARD_ID}.json"
LOG_FILE = OUTPUT_DIR / "generation_log.txt"
CHECKPOINT_FILE = OUTPUT_DIR / "checkpoint.json"

# ═══════════════════════════════════════════════════════════════════════════
# حوزه‌ها — بزرگ‌ترین لیست ممکن برای پوشش همه‌چیز
# ═══════════════════════════════════════════════════════════════════════════
DOMAIN_HINTS = [
    # مکالمات روزمره
    "سلام و احوال‌پرسی", "خداحافظی و پایان مکالمه", "تشکر و قدردانی",
    "معرفی خود (اسم، سن، شهر، جنسیت)", "سوالات هویتی و شخصی",
    # تحصیل
    "هدف تحصیلی (کنکور، نهایی، دانشگاه، رتبه)", "امتحان و آزمون",
    "تکلیف و پروژه", "نمره و کارنامه", "مدرسه و آموزشگاه",
    "کلاس و معلم", "مشاوره‌ی تحصیلی", "انتخاب رشته",
    "کنکور تجربی", "کنکور ریاضی", "کنکور انسانی",
    "امتحان نهایی", "درس‌های عمومی", "درس‌های تخصصی",
    "ریاضی و فرمول", "فیزیک", "شیمی", "زیست", "ادبیات", "عربی", "دینی",
    "زبان انگلیسی", "تاریخ", "جغرافیا", "اقتصاد", "فلسفه", "منطق",
    # برنامه‌ریزی و مطالعه
    "برنامه‌ریزی روزانه", "برنامه‌ریزی هفتگی", "برنامه‌ریزی ماهانه",
    "درخواست تغییر برنامه", "لغو برنامه", "به تعویق انداختن",
    "پومودورو و تکنیک مطالعه", "تست‌زنی", "یادگیری فعال",
    "خلاصه‌نویسی", "فلش‌کارت", "حل تمرین",
    "محل مطالعه", "حواس‌پرتی", "تمرکز", "گوشی موبایل",
    # زمان و خواب
    "ساعت بیداری", "ساعت خواب", "کم‌خوابی", "بی‌خوابی",
    "خواب آلودگی", "چرت روزانه", "ساعت مطالعه", "زمان‌بندی",
    "وعده‌های غذایی", "صبحانه", "ناهار", "شام", "میان‌وعده",
    # احساسات
    "خستگی", "انگیزه", "بی‌انگیزگی", "استرس", "اضطراب",
    "شادی و هیجان", "غم و افسردگی", "خشم", "ترس", "تنهایی",
    "اعتماد به نفس", "خودشناسی", "خودآگاهی", "هدف‌گذاری",
    "امید به آینده", "پشیمانی و حسرت", "خاطرات گذشته",
    # ارتباط با ربات
    "بازخورد به ربات", "امتیاز دادن", "انتقاد از ربات",
    "سوالات متداول درباره ربات", "دستورات حالت زنده",
    "توقف و ادامه", "پرش به مرحله‌ی بعدی",
    # مشکلات و شکایات
    "اعتراض به برنامه", "شکایت از ربات", "گله‌مندی",
    "دعوا و بحث (بدون فحش)", "نارضایتی",
    # درخواست‌ها
    "درخواست کمک روانی", "درخواست استراحت", "درخواست مرخصی",
    "درخواست شخصی‌سازی", "پیشنهاد به ربات",
    # روابط
    "رابطه با دوستان", "رابطه با خانواده", "رابطه با معلم",
    "عشق و شکست عشقی", "قلدری و آزار", "رقابت با دوستان",
    # سرگرمی
    "بازی‌های ویدیویی", "شبکه‌های اجتماعی", "یوتیوب و پادکست",
    "کتاب و رمان", "موزیک و ساز", "نقاشی و طراحی", "عکاسی",
    "سفر و گردش", "طبیعت‌گردی", "آشپزی", "مد و پوشاک",
    # سلامت
    "سلامت جسمی", "سردرد", "کمردرد", "بیماری",
    "سلامت روانی", "افسردگی", "بی‌حوصلگی", "ناامیدی",
    # مالی و کار
    "خرید و بودجه", "پول توجیبی", "کار و درآمد", "کمک به خانواده",
    "مشکلات مالی",
    # معنویت
    "دین و معنویت", "دعا و نیایش", "سوالات متافیزیکی",
    # تصمیم‌گیری
    "تردید و شک", "بی‌تصمیمی", "مقایسه", "انتخاب بین گزینه‌ها",
    # طنز و شوخی
    "شوخی و طنز", "لطیفه", "دلگرمی و تشویق",
    # عمیق
    "سوالات فلسفی", "سوالات وجودی", "معنای زندگی",
    # رویدادها
    "تولد و جشن", "عزاداری و سوگ", "مرگ عزیزان",
    "تعطیلات و تفریح", "مسافرت",
    # متنوع
    "مقایسه‌ی دانشگاه‌ها", "شهریه و هزینه", "خوابگاه",
    "دور بودن از خانواده", "مهاجرت", "آینده‌ی شغلی",
    "غلبه بر ترس", "کنترل خشم", "بخشش", "شکرگزاری",
    "روزهای خوب و بد", "پیش‌بینی آینده", "تصمیم‌های سخت",
    "همدلی", "دلداری دادن", "نصیحت خواستن", "نصیحت کردن",
    "تعریف و تحسین", "قدردانی", "عذرخواهی", "قبول عذرخواهی",
    "قول دادن", "تعهد", "مسئولیت‌پذیری", "نظم و انضباط",
    "عادت‌سازی", "ترک عادت بد", "شکست و بازگشت",
]

PROMPT_TEMPLATE = """# نقش
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


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] [shard-{SHARD_ID}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def call_ollama(prompt, max_tokens=8000, timeout=900):
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.9,
            "top_p": 0.95,
            "top_k": 60,
            "repeat_penalty": 1.15,
        },
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("response", "")


def extract_json(text):
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        if not isinstance(data, dict):
            return None
        cleaned = {}
        for k, v in data.items():
            if not isinstance(k, str) or not re.match(r"^[a-z][a-z0-9_]*$", k):
                continue
            if not isinstance(v, list):
                continue
            samples = [s.strip() for s in v if isinstance(s, str) and s.strip()]
            if len(samples) >= 3:
                cleaned[k] = samples
        return cleaned if cleaned else None
    except json.JSONDecodeError:
        return None


def load_state():
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"dataset": {}, "iteration": 0, "domains_used": []}


def save_state(state):
    CHECKPOINT_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # ذخیره‌ی دیتاست هم به‌روز شود
    SHARD_FILE.write_text(
        json.dumps(state["dataset"], ensure_ascii=False, indent=2), encoding="utf-8"
    )


def sample_existing(dataset, max_intents=60):
    names = list(dataset.keys())
    if len(names) <= max_intents:
        return "\n".join(f"- {n}" for n in names) or "(هیچ)"
    step = max(1, len(names) // max_intents)
    sampled = names[::step][:max_intents]
    return "\n".join(f"- {n}" for n in sampled) + f"\n(و {len(names) - len(sampled)} مورد دیگر)"


def pick_domains(used, count=6):
    """هر shard از یک offset متفاوت شروع می‌کند تا توزیع یکنواخت باشد."""
    random.seed(SHARD_ID * 1000 + len(used))
    pool = [d for d in DOMAIN_HINTS if d not in used]
    if len(pool) < count:
        pool = DOMAIN_HINTS[:]
    random.shuffle(pool)
    return pool[:count]


def main():
    log("=" * 60)
    log(f"🚀 shard {SHARD_ID}/{TOTAL_SHARDS} — مدل: {MODEL} | نسخه: {VERSION}")
    log(f"⏱️ بودجه‌ی زمانی: {TIME_BUDGET_MIN} دقیقه")
    log("=" * 60)

    start_time = time.time()
    deadline = start_time + (TIME_BUDGET_MIN * 60)

    state = load_state()
    dataset = state["dataset"]
    used = state["domains_used"]

    if dataset:
        log(f"♻️ بازیابی: {len(dataset)} intent")

    iteration = state["iteration"]
    max_iterations = 5000

    while time.time() < deadline and iteration < max_iterations:
        iteration += 1
        remaining_min = (deadline - time.time()) / 60
        log(f"🔁 دور {iteration} — intent: {len(dataset)} | زمان باقی: {remaining_min:.1f} دقیقه")

        domains = pick_domains(used, count=6)
        prompt = PROMPT_TEMPLATE.format(
            n_intents=INTENTS_PER_ROUND,
            hint_domains="\n".join(f"- {d}" for d in domains),
            existing_sample=sample_existing(dataset),
        )

        try:
            t0 = time.time()
            response = call_ollama(prompt, timeout=min(900, int((deadline - time.time()) - 30)))
            elapsed = time.time() - t0
            log(f"   ⏱️ پاسخ در {elapsed:.0f}s ({len(response)} کاراکتر)")
        except requests.exceptions.Timeout:
            log(f"   ⏰ timeout در فراخوانی مدل")
            continue
        except Exception as e:
            log(f"   ❌ خطا: {e}")
            time.sleep(5)
            continue

        parsed = extract_json(response)
        if not parsed:
            log(f"   ⚠️ JSON نامعتبر — {response[:120]!r}")
            continue

        new_count = 0
        merged_count = 0
        for intent, samples in parsed.items():
            if intent in dataset:
                existing_set = set(dataset[intent])
                added = [s for s in samples if s not in existing_set]
                if added:
                    dataset[intent] = (dataset[intent] + added)[:25]
                    merged_count += 1
            else:
                dataset[intent] = samples[:20]
                new_count += 1

        used.extend(domains)
        if len(used) > 400:
            used = used[-200:]

        state["dataset"] = dataset
        state["iteration"] = iteration
        state["domains_used"] = used
        save_state(state)

        size_kb = SHARD_FILE.stat().st_size / 1024 if SHARD_FILE.exists() else 0
        log(f"   ✅ +{new_count} جدید | +{merged_count} ادغام | مجموع: {len(dataset)} | حجم: {size_kb:.1f}KB")

    elapsed_total = (time.time() - start_time) / 60
    log("=" * 60)
    log(f"🏁 shard {SHARD_ID} پایان — {iteration} دور در {elapsed_total:.1f} دقیقه")
    log(f"📦 {len(dataset)} intent | فایل: {SHARD_FILE}")
    log("=" * 60)

    # ثبت متادیتا
    meta = {
        "shard_id": SHARD_ID,
        "total_shards": TOTAL_SHARDS,
        "version": VERSION,
        "intents": len(dataset),
        "samples": sum(len(v) for v in dataset.values()),
        "iterations": iteration,
        "elapsed_minutes": round(elapsed_total, 2),
        "model": MODEL,
    }
    (OUTPUT_DIR / "shard_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
