"""Who speaks each line, and are they male or female?

Short dialogue lines (0.3–1.5 s, music bleeding into the separated vocals) defeated the old "one ECAPA embedding
per line + one clustering threshold" approach: a 5-minute couple story came out as 15 speakers and whole clusters
got the wrong gender. Now:

1. Pitch per line (pYIN on the separated vocals). Lines with a clear pitch are confidently male (< 160 Hz) or
   female (> 215 Hz) — pitch is the most reliable gender cue.
2. A speaker embedding per line with CAM++ (3D-Speaker, trained on ~200k CHINESE speakers; sherpa-onnx, CPU) —
   much steadier on short Chinese lines than ECAPA/VoxCeleb (falls back to ECAPA if the model is missing).
3. The unsure lines (pitch 160–215 Hz, or none) take the gender whose voice they are closest to: centroids of the
   confident male / female lines, nudged a little by their pitch.
4. Speakers are clustered INSIDE each gender (a cluster can never mix a man and a woman), tiny clusters merged into
   the nearest real one.
"""
import sys
from pathlib import Path

import numpy as np

from . import config

_enc = None
_cam = None
FEMALE_HZ = 165.0          # (kept for callers) one speaker's median pitch above this = female
MALE_SURE, FEMALE_SURE = 160.0, 215.0
CLUSTER_TH = 0.7            # cosine distance inside one gender
DIAR_DIR = config.DATA / "models" / "diar"
CAM_MODEL = DIAR_DIR / "campplus_zh.onnx"
PYDEPS = Path(__file__).resolve().parent.parent / "vendor" / "pydeps"


def _encoder(device):
    global _enc
    if _enc is None:
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy
        # COPY: Windows without developer mode can't create the symlinks SpeechBrain uses by default
        _enc = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb",
                                              savedir=str(config.DATA / "models" / "ecapa"),
                                              run_opts={"device": device}, local_strategy=LocalStrategy.COPY)
    return _enc


def _campplus():
    """CAM++ zh speaker-embedding extractor (sherpa-onnx from vendor/pydeps), or None."""
    global _cam
    if _cam is None and CAM_MODEL.exists():
        try:
            if PYDEPS.exists() and str(PYDEPS) not in sys.path:
                sys.path.append(str(PYDEPS))
            import sherpa_onnx
            _cam = sherpa_onnx.SpeakerEmbeddingExtractor(
                sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(CAM_MODEL), num_threads=4))
        except Exception:
            _cam = None
    return _cam


def unload():
    global _enc, _cam
    _enc = None
    _cam = None


def _load(path, sr=16000):
    import librosa
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    return y


def median_f0(y, sr=16000):
    import librosa
    if len(y) < sr * 0.3:
        return None
    f0, voiced, _ = librosa.pyin(y, fmin=60, fmax=450, sr=sr, frame_length=1024)
    f0 = f0[voiced & ~np.isnan(f0)]
    return float(np.median(f0)) if len(f0) > 5 else None


def line_pitch(seg, sr=16000):
    """(median f0, voiced frames) of one line; (None, n) when there is too little voiced sound."""
    import librosa
    if len(seg) < sr * 0.15:
        return None, 0
    # hop 512 (32 ms): twice as fast as the default, same gender on 96 % of lines (the rest are the unsure ones
    # the voice comparison decides anyway)
    f0, voiced, _ = librosa.pyin(seg, fmin=60, fmax=500, sr=sr, frame_length=1024, hop_length=512)
    v = f0[voiced & ~np.isnan(f0)]
    return (float(np.median(v)), len(v)) if len(v) >= 2 else (None, len(v))


def _embed_cam(cam, seg, sr=16000):
    st = cam.create_stream()
    # very short lines are looped to 1 s: CAM++ needs a little audio, the voice is the same
    s2 = seg if len(seg) >= sr else np.tile(seg, int(np.ceil(sr / max(len(seg), 1))))[:sr]
    st.accept_waveform(sr, s2.astype(np.float32))
    st.input_finished()
    e = np.array(cam.compute(st))
    return e / (np.linalg.norm(e) + 1e-9)


def genders_from(E, f0, nv):
    """Line genders (0 male / 1 female): pitch where it is clear, voice similarity to each gender otherwise."""
    male = (f0 < MALE_SURE) & (nv >= 4)
    fem = (f0 > FEMALE_SURE) & (nv >= 4)
    g = np.where(fem, 1, 0)
    unsure = ~(male | fem)
    if male.sum() >= 2 and fem.sum() >= 2 and E is not None:
        cM, cF = E[male].mean(0), E[fem].mean(0)
        cM, cF = cM / np.linalg.norm(cM), cF / np.linalg.norm(cF)
        prior = np.where(np.isnan(f0), 0.0, np.clip((f0 - 185) / 60, -1.5, 1.5) * 0.12)
        g = np.where(unsure, ((E @ cF - E @ cM + prior) > 0).astype(int), g)
    else:
        # only one gender is clearly present: the unsure lines follow pitch, else that gender
        only = 1 if fem.sum() > male.sum() else 0
        g = np.where(unsure, np.where(np.isnan(f0), only, (f0 >= 185).astype(int)), g)
    return g


def cluster_within(E, g, th=CLUSTER_TH):
    """Speaker ids, clustering separately inside each gender; tiny clusters join the nearest real one."""
    from sklearn.cluster import AgglomerativeClustering
    lab = np.full(len(g), -1)
    nxt = 0
    for gg in (0, 1):
        idx = np.where(g == gg)[0]
        if len(idx) == 0:
            continue
        if len(idx) < 3:
            lab[idx] = nxt
            nxt += 1
            continue
        l2 = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                     distance_threshold=th).fit(E[idx]).labels_.copy()
        cnt = np.bincount(l2)
        big = [c for c in range(len(cnt)) if cnt[c] >= max(3, 0.08 * len(idx))] or [int(np.argmax(cnt))]
        cents = {c: E[idx][l2 == c].mean(0) for c in range(len(cnt))}
        for c in range(len(cnt)):
            if c not in big:
                l2[l2 == c] = max(big, key=lambda b: float(cents[c] @ cents[b]))
        for c in sorted(set(l2)):
            lab[idx[l2 == c]] = nxt
            nxt += 1
    return lab


def detect(job, audio_path, lines, device="cuda"):
    sr = 16000
    y = _load(audio_path, sr)
    segs = []
    for l in lines:
        a, b = int(l["start"] * sr), int(l["end"] * sr)
        segs.append(y[max(a, 0):max(b, a + 1)])
    n = len(lines)
    if not n:
        return lines, ""

    cam = _campplus()
    enc = None
    if cam is None:
        try:
            import torch
            enc = _encoder(device if torch.cuda.is_available() else "cpu")
        except Exception as e:
            job.log(f"Không tải được model nhận diện người nói ({e}) → chỉ đoán giới tính theo cao độ", "warn")
    job.log("Nhận diện người nói: " + ("CAM++ tiếng Trung + cao độ từng câu" if cam is not None
                                        else "ECAPA + cao độ từng câu" if enc is not None else "chỉ cao độ"))
    f0 = np.full(n, np.nan)
    nv = np.zeros(n, int)
    embs = []
    for i, seg in enumerate(segs):
        job.check_cancel()
        p, k = line_pitch(seg, sr)
        f0[i] = p if p else np.nan
        nv[i] = k
        if cam is not None:
            embs.append(_embed_cam(cam, seg, sr))
        elif enc is not None:
            import torch
            s2 = seg if len(seg) >= sr * 0.6 else np.tile(seg, int(np.ceil(sr * 0.6 / max(len(seg), 1))))
            with torch.no_grad():
                e = enc.encode_batch(torch.from_numpy(s2).float().unsqueeze(0)).squeeze().cpu().numpy()
            embs.append(e / (np.linalg.norm(e) + 1e-9))
        job.step("speakers", progress=0.85 * (i + 1) / n, detail=f"{i + 1}/{n} câu")
    E = np.stack(embs) if embs else None

    g = genders_from(E, f0, nv)
    lab = cluster_within(E, g) if E is not None and n >= 4 else np.where(g == 1, 1, 0)

    # renumber speakers by first appearance
    order = {}
    for x in lab:
        order.setdefault(int(x), len(order))
    labels = [order[int(x)] for x in lab]

    genders = {}
    for spk in sorted(set(labels)):
        idx = [i for i, x in enumerate(labels) if x == spk]
        fem = sum(int(g[i]) for i in idx)
        pitch = [f0[i] for i in idx if not np.isnan(f0[i])]
        genders[spk] = ("female" if fem * 2 >= len(idx) else "male", float(np.median(pitch)) if pitch else None)
    job.step("speakers", progress=1.0)

    for i, l in enumerate(lines):
        l["speaker"] = f"S{labels[i] + 1}"
        l["gender"] = "female" if g[i] else "male"        # per line: a speaker's odd line keeps its own pitch
    summary = ", ".join(f"S{k+1}: {'nữ' if gd == 'female' else 'nam'}"
                        f" ({p:.0f}Hz, {labels.count(k)} câu)" if p else f"S{k+1}: {'nữ' if gd == 'female' else 'nam'}"
                        for k, (gd, p) in genders.items())
    job.log(f"Người nói: {summary}")
    return lines, summary
