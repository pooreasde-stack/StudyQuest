"""
اسکریپت آمادهسازی مدل embedding برای اپ StudyQuest.

مدل پایه: alphaedge-ai/multilingual-e5-small-fas-32768
(همون E5-small با واژگان بهینهشده فارسی — حجم ~۳۳MB پس از کوانتیزه)

خروجیها (به app/src/main/assets/ کپی شوند):
  - model_int8.onnx          (~۳۳ MB)
  - tokenizer.onnx           (~۵ MB)
  - intent_embeddings.json   (~۱۰۰ KB)
"""

import json
from pathlib import Path
import numpy as np

# ✅ مدل هرسشده فارسی
MODEL_ID = "alphaedge-ai/multilingual-e5-small-fas-32768"
OUT_DIR = Path("onnx_export")
OUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------
# مرحله ۱: خروجیگرفتن مدل به ONNX
# ---------------------------------------------------------------
def export_model():
    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    print(f"📥 دانلود و تبدیل مدل به ONNX: {MODEL_ID}")
    model = ORTModelForFeatureExtraction.from_pretrained(MODEL_ID, export=True)
    model.save_pretrained(OUT_DIR)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    tokenizer.save_pretrained(OUT_DIR)
    print(f"✅ مدل به ONNX تبدیل شد → {OUT_DIR}")


# ---------------------------------------------------------------
# مرحله ۲: کوانتیزهسازی int8
# ---------------------------------------------------------------
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


# ---------------------------------------------------------------
# مرحله ۳: ساخت توکنایزر بهصورت گراف ONNX
# ---------------------------------------------------------------
def export_tokenizer_onnx():
    """
    ⚠️ نکته مهم: onnxruntime_extensions فقط توکنایزرهای slow را پشتیبانی میکند.
    پس حتماً باید use_fast=False پاس بدهیم، وگرنه خطای
    «Unsupported processor/tokenizer: PreTrainedTokenizerFast» میگیریم.
    """
    from transformers import AutoTokenizer
    from onnxruntime_extensions import gen_processing_models

    print("🔧 ساخت گراف ONNX توکنایزر ...")

    # ✅ کلید فیکس: use_fast=False
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, use_fast=False)
    print(f"   کلاس توکنایزر: {type(tokenizer).__name__}")

    result = gen_processing_models(
        tokenizer,
        pre_kwargs={"WITH_DEFAULT_INPUTS": True},
    )
    pre_model = result[0] if isinstance(result, tuple) else result

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

    # ✅ use_fast=False برای هماهنگی با توکنایزر گراف ONNX
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, use_fast=False)
    session = ort.InferenceSession(str(OUT_DIR / "model_int8.onnx"))

    # کشف ورودیهای لازم مدل (فیکس token_type_ids)
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
        # فقط ورودیهایی که مدل میخواهد
        feed = {k: v for k, v in inputs.items() if k in required_inputs}

        outputs = session.run(None, feed)
        last_hidden = outputs[0]                            # (1, seq_len, 384)
        mask = inputs["attention_mask"][..., None]          # (1, seq_len, 1)
        pooled = (last_hidden * mask).sum(1) / mask.sum(1)  # mean pooling
        vec = pooled[0]
        return vec / np.linalg.norm(vec)                    # نرمالسازی کسینوسی

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


# ---------------------------------------------------------------
# اجرا
# ---------------------------------------------------------------
if __name__ == "__main__":
    export_model()
    size_mb = quantize_model()
    export_tokenizer_onnx()
    build_intent_embeddings()

    print("\n" + "=" * 50)
    print(f"حجم مدل نهایی: {size_mb:.1f} MB")
    if size_mb < 95:
        print("✅ زیر سقف ۱۰۰MB — بدون نیاز به Git LFS")
    else:
        print("⚠️ بالای ۹۵MB — Git LFS لازم است")
    print("=" * 50)
