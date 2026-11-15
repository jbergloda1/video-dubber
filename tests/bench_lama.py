"""Benchmark the local big-lama model on the GPU at a few crop sizes (fp32 / fp16).
Usage: python -m tests.bench_lama"""
import time

import torch

from dubber import config

torch.backends.cudnn.benchmark = True
model = torch.jit.load(str(config.DATA / "models" / "lama" / "big-lama.pt"), map_location="cuda").eval()


def bench(h, w, half=False, batch=1, n=8):
    img = torch.rand(batch, 3, h, w, device="cuda")
    mask = torch.zeros(batch, 1, h, w, device="cuda")
    mask[..., h // 3: 2 * h // 3, w // 6: 5 * w // 6] = 1
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16, enabled=half):
        for _ in range(3):
            out = model(img, mask)
        torch.cuda.synchronize()
        t = time.time()
        for _ in range(n):
            out = model(img, mask)
        torch.cuda.synchronize()
    return (time.time() - t) / n * 1000 / batch, bool(torch.isfinite(out).all())


for h, w in [(256, 640), (128, 320), (192, 512)]:
    for half in (False, True):
        try:
            ms, ok = bench(h, w, half)
            print(f"{w}x{h} fp16={half}: {ms:.0f} ms/crop, finite={ok}")
        except Exception as e:
            print(f"{w}x{h} fp16={half}: ERR {str(e)[:80]}")
ms, _ = bench(128, 320, False, batch=8)
print(f"320x128 batch=8 fp32: {ms:.0f} ms/crop")
