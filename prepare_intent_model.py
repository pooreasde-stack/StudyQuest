"""
اسکریپت آمادهسازی مدل embedding برای اپ StudyQuest.

مدل پایه: alphaedge-ai/multilingual-e5-small-fas-32768
خروجیها:
  - model_int8.onnx          (~۳۳ MB) — مدل کوانتیزه
  - tokenizer_hf/tokenizer.json  (~۲ MB) — توکنایزر برای استفاده در جاوا
  - intent_embeddings.json   (~۱۰۰ KB) — embedding از پیشمحاسبهشده
"""

import json
import shutil
from pathlib import Path
import numpy as np

MODEL_ID = "alphaedge-ai/multilingual-e5-small-fas-32768"
OUT_DIR = Path("onnx_export")
OUT_DIR.mkdir(exist_ok=True)


def export_model():
    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    print(f"📥 دانلود و تبدیل مدل به ONNX: {MODEL_ID}")
    model = ORTModelForFeatureExtraction.from_pretrained(MODEL_ID, export=True)
    model.save_pretrained(OUT_DIR)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    tokenizer.save_pretrained(OUT_DIR)
    print(f"✅ مدل به ONNX تبدیل شد → {OUT_DIR}")


def quantize_model():
    from onnxruntime.quantization import quantize_dynamic, QuantType

    src = OUT_DIR / "model.onnx"
    dst = OUT_DIR / "model_int8.onnx"
    print("🔧 کوانتیزهسازی int8 ...")

    quantize_dynamic(
        model_input=str(src),
        model_output=str(dst),
        weight_type=QuantType.QUInt8,
    )

    size_mb = dst.stat().st_size / (1024 * 1024)
    print(f"✅ کوانتیزه شد → {dst}  (حجم نهایی: {size_mb:.1f} MB)")
    return size_mb


def save_tokenizer_for_java():
    """
    بهجای ساخت گراف ONNX توکنایزر (که برای این مدل ممکن نیست)،
    فایل tokenizer.json را در پوشهی جدا ذخیره میکنیم تا در Android
    با کتابخونهی DJL HuggingFace Tokenizers بارگذاری شود.
    """
    print("🔧 ذخیرهی توکنایزر برای استفاده در Android ...")

    tokenizer_dir = OUT_DIR / "tokenizer_hf"
    tokenizer_dir.mkdir(exist_ok=True)

    # فایل اصلی که در جاوا لازم داریم
    src_json = OUT_DIR / "tokenizer.json"
    if not src_json.exists():
        raise FileNotFoundError(
            f"فایل tokenizer.json پیدا نشد در {OUT_DIR}. "
            "مطمئن شو export_model() قبلاً اجرا شده."
        )

    dst_json = tokenizer_dir / "tokenizer.json"
    shutil.copy(src_json, dst_json)

    # فایلهای کمکی (اختیاری، برای راحتی)
    for fname in ["tokenizer_config.json", "special_tokens_map.json"]:
        src = OUT_DIR / fname
        if src.exists():
            shutil.copy(src, tokenizer_dir / fname)

    size_kb = dst_json.stat().st_size / 1024
    print(f"✅ توکنایزر ذخیره شد → {dst_json}  ({size_kb:.0f} KB)")
    print("   در Android با DJL HuggingFace Tokenizers استفاده میشود.")


def build_intent_embeddings():
    import onnxruntime as ort
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    session = ort.InferenceSession(str(OUT_DIR / "model_int8.onnx"))

    required_inputs = {i.name for i in session.get_inputs()}
    print(f"ℹ️ ورودیهای مدل: {sorted(required_inputs)}")

    def embed(text: str) -> np.ndarray:
        inputs = tokenizer(
            "query: " + text,
            return_tensors="np",
            padding=True,
            truncation=True,
            max_length=128,
        )
        feed = {k: v for k, v in inputs.items() if k in required_inputs}
        outputs = session.run(None, feed)
        last_hidden = outputs[0]
        mask = inputs["attention_mask"][..., None]
        pooled = (last_hidden * mask).sum(1) / mask.sum(1)
        vec = pooled[0]
        return vec / np.linalg.norm(vec)

    with open("intents.json", encoding="utf-8") as f:
        intents = json.load(f)

    result = {}
    for intent_name, examples in intents.items():
        print(f"   • {intent_name}: {len(examples)} نمونه")
        result[intent_name] = [embed(ex).tolist() for ex in examples]

    out_path = OUT_DIR / "intent_embeddings.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"✅ embeddingها ساخته شد → {out_path}")


if __name__ == "__main__":
    export_model()
    size_mb = quantize_model()
    save_tokenizer_for_java()
    build_intent_embeddings()

    print("\n" + "=" * 50)
    print(f"حجم مدل نهایی: {size_mb:.1f} MB")
    print("=" * 50)
    print("خروجیها:")
    print("  • onnx_export/model_int8.onnx")
    print("  • onnx_export/tokenizer_hf/tokenizer.json")
    print("  • onnx_export/intent_embeddings.json")
