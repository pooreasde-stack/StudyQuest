"""
اسکریپت آماده‌سازی مدل embedding فارسی برای StudyQuest.

مدل: alphaedge-ai/multilingual-e5-small-fas-32768

خروجی‌ها (این ۴ فایل رو به app/src/main/assets/ کپی کن):
  - onnx_export/model_int8.onnx                 کوانتیزه‌شده int8، ~۳۳ مگابایت
  - onnx_export/intent_embeddings.json          بردار میانگین (centroid) هر intent
  - onnx_export/io_names.json                   اسم واقعی ورودی/خروجی گراف مدل
  - onnx_export/tokenizer_hf/tokenizer.json     توکنایزر برای DJL در Android

⚠️ نکته‌ی مهم:
توکنایزر ONNX ساخته نمی‌شود چون onnxruntime_extensions فقط توکنایزرهای slow
را پشتیبانی می‌کند و این مدل هرس‌شده فقط فایل fast (tokenizer.json) دارد.
در Android از کتابخانه‌ی ai.djl.huggingface:tokenizers استفاده کن.
"""

import json
import shutil
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
# مرحله ۳: ذخیره‌ی توکنایزر برای استفاده در Android
# ---------------------------------------------------------------
def save_tokenizer_for_android():
    """
    مدل هرس‌شده فقط توکنایزر fast (tokenizer.json) دارد.
    کتابخانه‌ی onnxruntime_extensions فقط slow tokenizer را پشتیبانی می‌کند
    (خطای: Unsupported processor/tokenizer: PreTrainedTokenizerFast).
    پس به‌جای ساخت گراف ONNX، فایل توکنایزر را در پوشه‌ی جدا کپی می‌کنیم
    تا در سمت Android با کتابخانه‌ی DJL HuggingFace Tokenizers بارگذاری شود.
    """
    print("در حال ذخیره‌ی توکنایزر برای Android ...")

    tokenizer_dir = OUT_DIR / "tokenizer_hf"
    tokenizer_dir.mkdir(exist_ok=True)

    src_json = OUT_DIR / "tokenizer.json"
    if not src_json.exists():
        raise FileNotFoundError(f"فایل tokenizer.json پیدا نشد در {OUT_DIR}")

    shutil.copy(src_json, tokenizer_dir / "tokenizer.json")

    for fname in ["tokenizer_config.json", "special_tokens_map.json"]:
        src = OUT_DIR / fname
        if src.exists():
            shutil.copy(src, tokenizer_dir / fname)

    size_kb = (tokenizer_dir / "tokenizer.json").stat().st_size / 1024
    print(f"✅ توکنایزر ذخیره شد → {tokenizer_dir}  ({size_kb:.0f} کیلوبایت)")


# ---------------------------------------------------------------
# مرحله ۴: بازرسی خودکار اسم ورودی/خروجی گراف مدل
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
        "model": names_of(OUT_DIR / "model_int8.onnx"),
    }
    out_path = OUT_DIR / "io_names.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(io_map, f, ensure_ascii=False, indent=2)

    print("\n=== اسم واقعی ورودی/خروجی گراف مدل ===")
    print(json.dumps(io_map, ensure_ascii=False, indent=2))
    print(f"✅ ذخیره شد → {out_path}")
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
    save_tokenizer_for_android()
    io_map = inspect_onnx_io()
    build_intent_embeddings()
    print("\n=== خلاصه ===")
    print(f"حجم مدل کوانتیزه: {size_mb:.1f} مگابایت")
    print("۴ فایل رو به app/src/main/assets/ کپی کن:")
    print("  model_int8.onnx")
    print("  intent_embeddings.json")
    print("  io_names.json")
    print("  tokenizer_hf/tokenizer.json")
