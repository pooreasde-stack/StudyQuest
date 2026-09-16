"""
اسکریپت آماده‌سازی مدل embedding فارسی برای StudyQuest.
این را یک‌بار روی کامپیوتر خودت یا از طریق GitHub Actions اجرا کن.

مدل: alphaedge-ai/multilingual-e5-small-fas-32768
(نسخه‌ی از قبل هرس‌شده‌ی multilingual-e5-small، مخصوص فارسی — واقعی و آماده،
نیاز به هیچ هرس دستی نداره)

نصب پیش‌نیازها:
    pip install "optimum[onnxruntime]" onnxruntime onnxruntime-extensions onnx transformers numpy

خروجی‌ها (این ۴ فایل رو به app/src/main/assets/ کپی کن):
  - onnx_export/model_int8.onnx          کوانتیزه‌شده int8، تخمین ~۳۳ مگابایت
  - onnx_export/tokenizer.onnx           توکنایزر به‌صورت گراف ONNX جدا
  - onnx_export/intent_embeddings.json   یک بردار «میانگین» (centroid) برای هر intent
  - onnx_export/io_names.json            اسم واقعی ورودی/خروجی گراف‌ها (خودکار، بدون Netron)

⚠️ چرا centroid و نه بردار هر نمونه:
اگه intents.json هزاران نمونه داشته باشه (که الان داره)، ذخیره‌ی بردار
تک‌تک نمونه‌ها فایل embedding رو به چند ده مگابایت می‌رسونه. به‌جاش، همه‌ی
نمونه‌های هر intent رو embed می‌کنیم، میانگین می‌گیریم، و فقط همون یک بردار
میانگین (renormalize شده) رو ذخیره می‌کنیم. حجم فایل نهایی صرفاً به تعداد
intent‌ها بستگی داره، نه تعداد نمونه‌ها — یعنی می‌تونی هر چقدر نمونه
می‌خوای برای دقت بهتر بدی، بدون نگرانی از حجم.

⚠️ این اسکریپت رو خودم (چون دسترسی اینترنت ندارم) اجرا/تست نکردم.
"""

import json
from pathlib import Path
import numpy as np

MODEL_ID = "alphaedge-ai/multilingual-e5-small-fas-32768"
OUT_DIR = Path("onnx_export")
OUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------
# مرحله ۱: خروجی‌گرفتن مدل به ONNX با optimum
# ---------------------------------------------------------------
def export_model():
    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    print(f"در حال دانلود و تبدیل مدل به ONNX: {MODEL_ID} ...")
    model = ORTModelForFeatureExtraction.from_pretrained(MODEL_ID, export=True)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model.save_pretrained(OUT_DIR)
    tokenizer.save_pretrained(OUT_DIR)
    print("✅ تبدیل به ONNX انجام شد →", OUT_DIR)


# ---------------------------------------------------------------
# مرحله ۲: کوانتیزه‌کردن به int8 برای کاهش حجم
# ---------------------------------------------------------------
def quantize_model():
    from onnxruntime.quantization import quantize_dynamic, QuantType

    src = OUT_DIR / "model.onnx"
    dst = OUT_DIR / "model_int8.onnx"
    quantize_dynamic(str(src), str(dst), weight_type=QuantType.QUInt8)
    size_mb = dst.stat().st_size / (1024 * 1024)
    print(f"✅ کوانتیزه شد → {dst}  (حجم واقعی: {size_mb:.1f} مگابایت)")
    return size_mb


# ---------------------------------------------------------------
# مرحله ۳: ساخت توکنایزر به‌صورت گراف ONNX جدا
# (این کار باعث می‌شه تو جاوا نیازی به پیاده‌سازی دستی SentencePiece/BPE نباشه)
# ---------------------------------------------------------------
def export_tokenizer_onnx():
    from onnxruntime_extensions import gen_processing_models

    print("در حال ساخت گراف ONNX توکنایزر ...")
    pre_m, _ = gen_processing_models(str(OUT_DIR), pre_kwargs={"WITH_DEFAULT_INPUTS": True})
    tok_path = OUT_DIR / "tokenizer.onnx"
    with open(tok_path, "wb") as f:
        f.write(pre_m.SerializeToString())
    print("✅ توکنایزر ONNX ساخته شد →", tok_path)


# ---------------------------------------------------------------
# مرحله ۴: بازرسی خودکار اسم ورودی/خروجی گراف‌ها — به‌جای باز کردن دستی با Netron
# ---------------------------------------------------------------
def inspect_onnx_io():
    import onnx

    def names_of(path):
        g = onnx.load(str(path)).graph
        return {
            "inputs": [i.name for i in g.input],
            "outputs": [o.name for o in g.output],
        }

    io_map = {
        "tokenizer": names_of(OUT_DIR / "tokenizer.onnx"),
        "model": names_of(OUT_DIR / "model_int8.onnx"),
    }
    out_path = OUT_DIR / "io_names.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(io_map, f, ensure_ascii=False, indent=2)

    print("\n=== اسم واقعی ورودی/خروجی گراف‌ها (به‌جای چک دستی با Netron) ===")
    print(json.dumps(io_map, ensure_ascii=False, indent=2))
    print(f"✅ ذخیره شد → {out_path}")
    print("   این فایل رو هم به app/src/main/assets/ کپی کن — IntentMatcher.java")
    print("   خودش این اسم‌ها رو از روی همین فایل می‌خونه، دیگه هاردکد نیست.")
    return io_map


# ---------------------------------------------------------------
# مرحله ۵: محاسبه‌ی یک بردار «میانگین» (centroid) برای هر intent
# ---------------------------------------------------------------
def build_intent_embeddings():
    import onnxruntime as ort
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(OUT_DIR)
    session = ort.InferenceSession(str(OUT_DIR / "model_int8.onnx"))
    required_inputs = {i.name for i in session.get_inputs()}

    def embed(text: str) -> np.ndarray:
        inputs = tokenizer("query: " + text, return_tensors="np", padding=True, truncation=True, max_length=64)
        feed = {k: v for k, v in inputs.items() if k in required_inputs}
        outputs = session.run(None, feed)
        last_hidden = outputs[0]
        mask = inputs["attention_mask"][..., None]
        pooled = (last_hidden * mask).sum(1) / mask.sum(1)
        vec = pooled[0]
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    with open("intents.json", encoding="utf-8") as f:
        intents = json.load(f)

    result = {}
    for intent_name, examples in intents.items():
        vecs = np.array([embed(ex) for ex in examples])
        centroid = vecs.mean(axis=0)
        norm = np.linalg.norm(centroid)
        centroid = centroid / norm if norm > 0 else centroid
        result[intent_name] = centroid.tolist()
        print(f"   • {intent_name}: {len(examples)} نمونه → ۱ بردار میانگین")

    out_path = OUT_DIR / "intent_embeddings.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    size_kb = out_path.stat().st_size / 1024
    print(f"✅ centroid همه‌ی {len(result)} intent ساخته شد → {out_path}  ({size_kb:.0f} کیلوبایت)")


if __name__ == "__main__":
    export_model()
    size_mb = quantize_model()
    export_tokenizer_onnx()
    io_map = inspect_onnx_io()
    build_intent_embeddings()
    print("\n=== خلاصه ===")
    print(f"حجم مدل کوانتیزه: {size_mb:.1f} مگابایت")
    print("۴ فایل رو به app/src/main/assets/ کپی کن:")
    print("  model_int8.onnx, tokenizer.onnx, intent_embeddings.json, io_names.json")
