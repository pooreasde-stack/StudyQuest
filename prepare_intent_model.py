"""
اسکریپت آمادهسازی مدل embedding چندزبانه برای اپ اندروید StudyQuest.

نصب پیشنیازها:
    pip install "optimum[onnxruntime]" onnxruntime onnxruntime-extensions transformers numpy

خروجیها (این ۳ فایل باید به app/src/main/assets/ کپی شوند):
  - model_int8.onnx          مدل کوانتیزهشده (~۳۰MB)
  - tokenizer.onnx           توکنایزر بهصورت گراف ONNX
  - intent_embeddings.json   بردارهای از پیشمحاسبهشده هر intent
"""

import json
from pathlib import Path
import numpy as np

MODEL_ID = "intfloat/multilingual-e5-small"
OUT_DIR = Path("onnx_export")
OUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------
# مرحله ۱: خروجیگرفتن مدل به ONNX با optimum
# ---------------------------------------------------------------
def export_model():
    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    print("📥 دانلود و تبدیل مدل به ONNX ...")
    model = ORTModelForFeatureExtraction.from_pretrained(MODEL_ID, export=True)
    model.save_pretrained(OUT_DIR)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    tokenizer.save_pretrained(OUT_DIR)
    print(f"✅ مدل به ONNX تبدیل شد → {OUT_DIR}")


# ---------------------------------------------------------------
# مرحله ۲: کوانتیزهسازی int8 بهینهشده برای حفظ دقت
# ---------------------------------------------------------------
def quantize_model():
    from onnxruntime.quantization import quantize_dynamic, QuantType

    src = OUT_DIR / "model.onnx"
    dst = OUT_DIR / "model_int8.onnx"

    print("🔧 کوانتیزهسازی int8 با تنظیمات حفظ دقت ...")

    quantize_dynamic(
        model_input=str(src),
        model_output=str(dst),
        weight_type=QuantType.QUInt8,
        # کوانتیزهکردن Gather برای فشردهسازی جدول embedding
        # (بزرگترین بخش مدل + حفظ دقت چون فقط وزنها فشرده میشن)
        op_types_to_quantize=["MatMul", "Gather"],
        extra_options={
            "ActivationSymmetric": False,   # حفظ دقت بیشتر
            "WeightSymmetric": True,         # وزنها متقارن (پایدارتر)
        },
    )

    size_mb = dst.stat().st_size / (1024 * 1024)
    print(f"✅ کوانتیزه شد → {dst}  (حجم نهایی: {size_mb:.1f} MB)")
    return size_mb


# ---------------------------------------------------------------
# مرحله ۳: ساخت توکنایزر بهصورت گراف ONNX
# ---------------------------------------------------------------
def export_tokenizer_onnx():
    from transformers import AutoTokenizer
    from onnxruntime_extensions import gen_processing_models

    print("🔧 ساخت گراف ONNX توکنایزر ...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)

    # ورودی باید خودِ توکنایزر باشد، نه مسیر پوشه
    pre_model = gen_processing_models(
        tokenizer,
        pre_kwargs={"WITH_DEFAULT_INPUTS": True},
    )
    if isinstance(pre_model, tuple):
        pre_model = pre_model[0]

    tok_path = OUT_DIR / "tokenizer.onnx"
    with open(tok_path, "wb") as f:
        f.write(pre_model.SerializeToString())
    size_kb = tok_path.stat().st_size / 1024
    print(f"✅ توکنایزر ONNX ساخته شد → {tok_path}  ({size_kb:.0f} KB)")


# ---------------------------------------------------------------
# مرحله ۴: محاسبهی embedding جملههای نمونه هر intent
# ---------------------------------------------------------------
def build_intent_embeddings():
    import onnxruntime as ort
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    session = ort.InferenceSession(str(OUT_DIR / "model_int8.onnx"))

    def embed(text: str) -> np.ndarray:
        # E5 با پیشوند "query: " برای ورودی کاربر
        inputs = tokenizer(
            "query: " + text,
            return_tensors="np",
            padding=True,
            truncation=True,
            max_length=128,
        )
        outputs = session.run(None, dict(inputs))
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
    export_tokenizer_onnx()
    build_intent_embeddings()

    print("\n" + "=" * 50)
    print(f"حجم مدل نهایی: {size_mb:.1f} MB")
    if size_mb < 100:
        print("✅ زیر سقف ۱۰۰MB گیتهاب — بدون نیاز به Git LFS")
    else:
        print("⚠️ بالای ۱۰۰MB — Git LFS لازم است")
    print("=" * 50)
