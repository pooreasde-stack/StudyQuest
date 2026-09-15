#!/usr/bin/env python3
"""
ادغام shardها از /tmp/shards (آرتیفکت‌ها) و ساخت دیتاست نهایی در /tmp/merged.
هر shard فایل shard_N.json دارد (dict از intent -> لیست نمونه).
"""
import json
import os
from pathlib import Path
from collections import defaultdict

SHARDS_DIR = Path("/tmp/shards")
MERGED_DIR = Path("/tmp/merged")
MERGED_DIR.mkdir(parents=True, exist_ok=True)

VERSION = os.environ.get("VERSION", "v0")
EXPECTED = int(os.environ.get("SHARDS", "20"))


def log(msg):
    print(f"[merge] {msg}", flush=True)


def find_shard_file(shard_dir):
    """پیدا کردن فایل shard_N.json در پوشه‌ی یک shard.
    ساختار پوشه: intent-shard-v42-5/shard_5.json
    """
    candidates = list(shard_dir.glob("shard_*.json"))
    # حذف فایل‌های meta
    candidates = [c for c in candidates if "_meta" not in c.name]
    return candidates[0] if candidates else None


def main():
    log("=" * 60)
    log(f"🔀 ادغام shardها | نسخه: {VERSION}")
    log("=" * 60)

    if not SHARDS_DIR.exists():
        log(f"❌ پوشه‌ی {SHARDS_DIR} وجود ندارد")
        raise SystemExit(1)

    # هر shard در یک زیرپوشه‌ی جداگانه است (چون download-artifact@v4
    # با pattern و merge-multiple: false هر artifact را در پوشه‌ی خودش می‌گذارد)
    shard_dirs = sorted([d for d in SHARDS_DIR.iterdir() if d.is_dir()])
    log(f"📂 {len(shard_dirs)} پوشه‌ی shard پیدا شد")

    if not shard_dirs:
        log("❌ هیچ پوشه‌ی shardi یافت نشد")
        raise SystemExit(1)

    merged = defaultdict(list)
    total_samples = 0
    processed = 0

    for shard_dir in shard_dirs:
        sf = find_shard_file(shard_dir)
        if sf is None:
            log(f"⚠️ {shard_dir.name}: هیچ فایل shard_*.json پیدا نشد")
            continue

        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                log(f"⚠️ {sf.name}: فرمت نامعتبر (dict نیست)")
                continue

            for intent, samples in data.items():
                if not isinstance(samples, list):
                    continue
                existing = set(merged[intent])
                for s in samples:
                    if isinstance(s, str) and s.strip() and s.strip() not in existing:
                        merged[intent].append(s.strip())
                        existing.add(s.strip())
                        total_samples += 1

            processed += 1
            log(f"✅ {shard_dir.name}: {len(data)} intent")

        except Exception as e:
            log(f"❌ {sf.name}: {e}")

    # حذف intentهایی که کمتر از ۳ نمونه دارند
    cleaned = {k: v for k, v in merged.items() if len(v) >= 3}

    log(f"📊 {len(cleaned)} intent یکتا | {total_samples} نمونه")
    log(f"📦 shardهای پردازش‌شده: {processed}/{EXPECTED}")

    # ذخیره‌ی دیتاست نهایی
    out_file = MERGED_DIR / f"intents_{VERSION}.json"
    out_file.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # کپی با نام "آخرین"
    latest = MERGED_DIR / "intents_latest.json"
    latest.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # آمار
    total_intents = len(cleaned)
    total_unique_samples = sum(len(v) for v in cleaned.values())
    stats = {
        "version": VERSION,
        "shards_expected": EXPECTED,
        "shards_processed": processed,
        "total_intents": total_intents,
        "total_samples": total_unique_samples,
        "avg_samples_per_intent": round(
            total_unique_samples / total_intents, 2
        ) if total_intents else 0,
        "file_size_bytes": out_file.stat().st_size,
        "file_size_mb": round(out_file.stat().st_size / (1024 * 1024), 2),
    }
    (MERGED_DIR / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # ادغام متادیتای shardها
    metas = []
    for shard_dir in shard_dirs:
        mf = shard_dir / "shard_meta.json"
        if mf.exists():
            try:
                metas.append(json.loads(mf.read_text(encoding="utf-8")))
            except Exception:
                pass
    if metas:
        (MERGED_DIR / "shards_meta.json").write_text(
            json.dumps(metas, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    log("=" * 60)
    log(f"🏁 {stats['file_size_mb']}MB | {total_intents} intent | "
        f"{total_unique_samples} نمونه")
    log("=" * 60)


if __name__ == "__main__":
    main()
