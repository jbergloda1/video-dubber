"""Split the original audio into voice and background (music + effects).

Models (setting "separate_model"):
  - "bs_roformer" (default): BS-RoFormer ep317 (SDR 12.97 on MUSDB vocals) via python-audio-separator, fp16,
    overlap 2. Measured on a known-voice mix (2026-11): ~6.5 dB less original voice left in the background
    than htdemucs, ~4× faster than real time on an RTX 3060.
  - "melband": Mel-Band RoFormer (Kim, fine-tuned by unwa) — fastest of the RoFormers, slightly more leakage.
  - "htdemucs" / "htdemucs_ft": the previous Demucs models (fallback when audio-separator is unavailable).
Each model runs in a child process (frees all VRAM afterwards, isolates crashes); progress comes from tqdm.
"""
import shutil
import sys
from pathlib import Path

from . import config, util

MODELS = {
    "bs_roformer": {"file": "model_bs_roformer_ep_317_sdr_12.9755.ckpt", "label": "BS-RoFormer (tốt nhất)"},
    "melband": {"file": "mel_band_roformer_kim_ft_unwa.ckpt", "label": "Mel-Band RoFormer (nhanh)"},
    "htdemucs_ft": {"label": "Demucs htdemucs_ft"},
    "htdemucs": {"label": "Demucs htdemucs (cũ)"},
}
MODEL_DIR = config.DATA / "models" / "separator"


def available():
    try:
        import audio_separator  # noqa: F401
        return True
    except Exception:
        return False


def _progress(job):
    def on_line(line):
        m = util.PCT.search(line)
        if m and "|" in line:
            job.step("separate", progress=min(float(m.group(1)) / 100, 0.99))
        elif "Downloading" in line or "download" in line.lower() and "model" in line.lower():
            job.step("separate", detail="Tải model tách giọng (chỉ lần đầu)…")
    return on_line


def _roformer(job, wav: Path, out: Path, model: str, dev: str):
    out.mkdir(parents=True, exist_ok=True)
    util.run(job, [util.PY, "-m", "dubber.separate", str(wav), str(out), MODELS[model]["file"], dev],
             cwd=Path(__file__).resolve().parent.parent, on_line=_progress(job))
    stems = list(out.glob("*.wav"))
    voc = next((p for p in stems if "(vocals)" in p.name.lower()), None)
    bg = next((p for p in stems if p is not voc and p.name != wav.name), None)
    if not voc or not bg:
        raise RuntimeError("Tách giọng không tạo ra đủ 2 file")
    return bg, voc


def _demucs(job, wav: Path, out: Path, model: str, dev: str):
    util.run(job, [util.PY, "-m", "demucs", "--two-stems=vocals", "-n", model, "-d", dev, "-o", out, wav],
             on_line=_progress(job))
    base = out / model / wav.stem
    return base / "no_vocals.wav", base / "vocals.wav"


def separate(job, d: Path, s) -> str:
    """d/audio.wav -> d/no_vocals.wav + d/vocals.wav. Returns the name of the model that was used."""
    want = s.get("separate_model") or "bs_roformer"
    dev = "cuda" if util.cuda_ok() else "cpu"
    if dev == "cpu":
        job.log("PyTorch không dùng được GPU → tách giọng bằng CPU (chậm hơn nhiều)", "warn")
    order = [want] + [m for m in ("bs_roformer", "htdemucs") if m != want]
    last = None
    for model in order:
        if model in ("bs_roformer", "melband") and not available():
            job.log("Chưa cài audio-separator (pip install --no-deps audio-separator==0.48.0 …) → dùng Demucs",
                    "warn")
            continue
        sep = d / "sep"
        shutil.rmtree(sep, ignore_errors=True)
        job.step("separate", progress=0.0, detail=f"{MODELS[model]['label']} trên {'GPU' if dev == 'cuda' else 'CPU'}")
        try:
            fn = _roformer if model in ("bs_roformer", "melband") else _demucs
            bg, voc = fn(job, d / "audio.wav", sep, model, dev)
            shutil.move(str(bg), d / "no_vocals.wav")
            shutil.move(str(voc), d / "vocals.wav")
            return MODELS[model]["label"]
        except Exception as e:
            from .jobs import Cancelled
            if isinstance(e, Cancelled):
                raise
            last = e
            job.log(f"{MODELS[model]['label']} lỗi: {str(e).splitlines()[0][:200]} → thử model khác", "warn")
        finally:
            shutil.rmtree(sep, ignore_errors=True)
    raise RuntimeError(f"Không tách được giọng: {last}")


def _main(wav, out, model_file, dev):
    """Child process: run one audio-separator model, write both stems to `out`."""
    import logging
    from audio_separator.separator import Separator
    sep = Separator(output_dir=out, model_file_dir=str(MODEL_DIR), output_format="WAV",
                    log_level=logging.WARNING, use_autocast=dev == "cuda",
                    mdxc_params={"segment_size": 256, "override_model_segment_size": False, "batch_size": 1,
                                 "overlap": 2, "pitch_shift": 0})
    sep.load_model(model_filename=model_file)
    files = sep.separate(wav)
    print("stems:", files, flush=True)


if __name__ == "__main__":
    _main(*sys.argv[1:5])
