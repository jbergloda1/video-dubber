"""VieNeu-TTS worker. Runs in its OWN environment (vendor/vieneu-env: torch 2.8 + transformers 4.57.6 + vieneu),
never in the app's Python, so its pinned packages cannot break Whisper & co.

Protocol: one JSON object per line on stdin, one JSON reply per line on stdout.
  {"cmd": "voices"}                                        -> {"ok": true, "voices": [[label, id], ...]}
  {"cmd": "synth", "items": [{"text", "voice", "out"}]}    -> {"ok": true, "done": n}
  {"cmd": "ping"}                                          -> {"ok": true, "device": "..."}
Everything the library prints goes to stderr. The worker exits when stdin closes (the app quit or restarted), so
it never keeps the GPU memory after the server is gone."""
import json
import sys
import traceback

PROTO = sys.stdout
sys.stdout = sys.stderr            # library chatter must not corrupt the protocol


def reply(obj):
    PROTO.write(json.dumps(obj, ensure_ascii=False) + "\n")
    PROTO.flush()


def main():
    from vieneu import Vieneu
    tts = Vieneu()
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        device = "cpu"
    reply({"ok": True, "ready": True, "device": device})
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            cmd = req.get("cmd")
            if cmd == "ping":
                reply({"ok": True, "device": device})
            elif cmd == "voices":
                reply({"ok": True, "voices": [list(v) if isinstance(v, (list, tuple)) else [str(v), str(v)]
                                              for v in tts.list_preset_voices()]})
            elif cmd == "synth":
                items = req["items"]
                by_voice = {}
                for it in items:
                    by_voice.setdefault(it["voice"], []).append(it)
                n = 0
                for voice, group in by_voice.items():
                    texts = [it["text"] for it in group]
                    audios = tts.infer_batch(texts, voice=voice) if len(texts) > 1 else [tts.infer(texts[0], voice=voice)]
                    for it, a in zip(group, audios):
                        tmp = it["out"] + ".part.wav"
                        tts.save(a, tmp)
                        import os
                        os.replace(tmp, it["out"])
                        n += 1
                reply({"ok": True, "done": n})
            else:
                reply({"ok": False, "error": f"unknown cmd {cmd!r}"})
        except Exception as e:
            traceback.print_exc()
            reply({"ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
