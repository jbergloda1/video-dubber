"""Measure the pipeline on real jobs: wall-clock time of each full run (from the job's own log) and the time spent
in each step. Writes data/bench/<date>.json and prints a comparison with the previous measurement.

    python tools/bench.py                 # all finished jobs
    python tools/bench.py --since 2026-11-15   # only runs that started on/after this date (e.g. after a change)
"""
import argparse
import datetime as dt
import glob
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "bench"


def runs_of(job: dict):
    """Full runs in a job's log: 'Chế độ:' … 'Hoàn tất'. Yields (start, end, t_render_done)."""
    start = render_done = None
    for l in job.get("logs") or []:
        m = l.get("msg", "")
        if m.startswith("Chế độ:") and start is None:
            start, render_done = l["t"], None
        elif start is not None and (m.startswith("Đã xuất:") or "TikTok:" in m or m.startswith("AI:")):
            pass
        if start is not None and m.startswith("Hoàn tất"):
            yield start, l["t"], render_done
            start = None
        elif m.startswith(("Đã huỷ", "LỖI")):
            start = None
    return


def collect(since: float = 0.0):
    rows = []
    for f in glob.glob(str(ROOT / "data" / "jobs" / "*" / "job.json")):
        try:
            j = json.loads(Path(f).read_text(encoding="utf-8"))
        except ValueError:
            continue
        vd = j.get("video_duration") or 0
        if not vd or j.get("status") != "done":
            continue
        full = [r for r in runs_of(j) if r[0] >= since]
        steps = {s["id"]: s.get("secs") or 0 for s in j["steps"] if s.get("status") == "done" and "♻" not in (s.get("detail") or "")}
        # a "full" run did the heavy steps; reruns that reuse everything are not comparable
        if not full or not steps.get("render"):
            continue
        a, b, _ = full[-1]          # the last run is the one the step times belong to
        rows.append({"id": j["id"], "mode": (j.get("options") or {}).get("output_mode"), "video_s": round(vd, 1),
                     "wall_s": round(b - a, 1), "steps": steps,
                     "tail_s": round(sum(steps.get(k, 0) for k in ("summary", "tiktok")), 1)})
    return rows


def summarize(rows):
    if not rows:
        return {}
    med = lambda xs: round(st.median(xs), 2) if xs else 0
    ids = sorted({k for r in rows for k in r["steps"]})
    return {"jobs": len(rows), "video_s": med([r["video_s"] for r in rows]), "wall_s": med([r["wall_s"] for r in rows]),
            "wall_per_video_s": med([r["wall_s"] / r["video_s"] for r in rows]),
            "sum_of_steps_s": med([sum(r["steps"].values()) for r in rows]),
            "tail_after_render_s": med([r["tail_s"] for r in rows]),
            "steps": {k: med([r["steps"][k] for r in rows if r["steps"].get(k)]) for k in ids}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    a = ap.parse_args()
    since = dt.datetime.fromisoformat(a.since).timestamp() if a.since else 0.0
    rows = collect(since)
    s = summarize(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    prev = sorted(OUT.glob("*.json"))
    name = OUT / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    name.write_text(json.dumps({"since": a.since, "summary": s, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    if not s:
        print("Không có job nào để đo.")
        return
    old = json.loads(prev[-1].read_text(encoding="utf-8"))["summary"] if prev else {}
    def line(label, key, unit="s"):
        o = old.get(key)
        d = f"   (trước: {o}{unit})" if o is not None else ""
        print(f"{label:38} {s[key]}{unit}{d}")
    print(f"{s['jobs']} job, video trung vị {s['video_s']}s")
    line("Thời gian thật một job (trung vị)", "wall_s")
    line("Giây xử lý / giây video", "wall_per_video_s", "")
    line("Cộng dồn các bước (không tính song song)", "sum_of_steps_s")
    line("Sau render (tiêu đề + kiểm tra TikTok)", "tail_after_render_s")
    for k, v in sorted(s["steps"].items(), key=lambda kv: -kv[1]):
        o = (old.get("steps") or {}).get(k)
        print(f"  {k:14} {v:7.1f}s" + (f"   (trước: {o}s)" if o is not None else ""))
    print("Đã ghi", name)


if __name__ == "__main__":
    main()
