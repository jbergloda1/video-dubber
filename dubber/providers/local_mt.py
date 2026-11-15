"""Built-in offline translation: Meta NLLB-200 (distilled, int8) running through CTranslate2 on the GPU.

This is the "local engine" the fallback chain can always rely on once the model is downloaded — no
service to start, no network. Quality is below the LLMs (no re-segmentation, literal), so it sits at the
end of the chain by default.
"""
import re
import threading
from pathlib import Path

from .. import config
from .base import NotConfigured, Provider, Transient

MODELS = {   # id -> Hugging Face repo with a ready-made CTranslate2 conversion
    "nllb-1.3B": "JustFrederik/nllb-200-distilled-1.3B-ct2-int8",
    "nllb-600M": "JustFrederik/nllb-200-distilled-600M-ct2-int8",
}
NLLB_CODES = {"vi": "vie_Latn", "zh": "zho_Hans", "yue": "yue_Hant", "en": "eng_Latn", "ja": "jpn_Jpan",
              "ko": "kor_Hang", "th": "tha_Thai", "id": "ind_Latn", "es": "spa_Latn", "fr": "fra_Latn",
              "de": "deu_Latn", "ru": "rus_Cyrl", "pt": "por_Latn", "it": "ita_Latn"}
_cache = {}
_lock = threading.Lock()


def model_dir(name):
    return config.DATA / "models" / name


def is_downloaded(name):
    d = model_dir(name)
    return (d / "model.bin").exists() and (d / "sentencepiece.bpe.model").exists()


def download(name, progress=None):
    from huggingface_hub import snapshot_download
    snapshot_download(MODELS[name], local_dir=str(model_dir(name)),
                      allow_patterns=["*.bin", "*.json", "*.model", "*.txt"])
    return model_dir(name)


def guess_source(texts, s):
    sl = s.get("source_lang") or "auto"
    if sl != "auto" and sl in NLLB_CODES:
        return NLLB_CODES[sl]
    joined = "".join(texts[:50])
    if re.search(r"[぀-ヿ]", joined):
        return "jpn_Jpan"
    if re.search(r"[가-힯]", joined):
        return "kor_Hang"
    if re.search(r"[一-鿿]", joined):
        return "zho_Hans"
    if re.search(r"[฀-๿]", joined):
        return "tha_Thai"
    return "eng_Latn"


def _load(name, device):
    key = (name, device)
    with _lock:
        if key not in _cache:
            import ctranslate2
            import sentencepiece as spm
            d = model_dir(name)
            tr = ctranslate2.Translator(str(d), device=device, compute_type="int8_float16" if device == "cuda" else "int8")
            sp = spm.SentencePieceProcessor(model_file=str(d / "sentencepiece.bpe.model"))
            _cache.clear()          # keep at most one model in VRAM
            _cache[key] = (tr, sp)
        return _cache[key]


def unload():
    with _lock:
        _cache.clear()


class LocalNLLB(Provider):
    id, label, kind, local = "local_nllb", "Dịch offline tích hợp (NLLB-200, GPU)", "mt", True

    def name(self, s):
        return s.get("local_mt_model") or "nllb-1.3B"

    def model_name(self, s):
        return self.name(s)

    def configured(self, s):
        if self.name(s) not in MODELS:
            return False, f"Model offline không hợp lệ: {self.name(s)}"
        if not is_downloaded(self.name(s)):
            return False, "Chưa tải model dịch offline (⚙ Cài đặt → Dịch → Tải model offline)"
        return True, ""

    def probe(self, s):
        return self.configured(s)

    def translate_list(self, job, texts, target, s):
        ok, why = self.configured(s)
        if not ok:
            raise NotConfigured(why)
        tgt = NLLB_CODES.get(target)
        if not tgt:
            raise NotConfigured(f"NLLB chưa hỗ trợ ngôn ngữ đích '{target}'")
        src = guess_source(texts, s)
        import ctranslate2
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        try:
            tr, sp = _load(self.name(s), device)
        except Exception as e:
            raise Transient(f"Không nạp được model NLLB: {e}")
        out = []
        for i in range(0, len(texts), 32):
            job.check_cancel()
            batch = texts[i:i + 32]
            source = [[src] + sp.encode(t or ".", out_type=str) + ["</s>"] for t in batch]
            res = tr.translate_batch(source, target_prefix=[[tgt]] * len(batch), beam_size=4,
                                     max_decoding_length=256, repetition_penalty=1.2)
            for t, r in zip(batch, res):
                toks = [x for x in r.hypotheses[0] if x not in (tgt, "</s>")]
                out.append(sp.decode(toks).strip() if t.strip() else "")
        return out
