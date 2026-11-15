import os

from dubber import checkpoint as ck


def test_fingerprint_small_and_big(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.bin"
    a.write_text('{"x": 1}')
    b.write_bytes(os.urandom(3 * 1024 * 1024))
    fa, fb = ck.fp(a), ck.fp(b)
    a.write_text('{"x": 1}')                       # rewritten, same content → same fingerprint
    assert ck.fp(a) == fa
    data = bytearray(b.read_bytes())
    data[len(data) // 2] ^= 0xFF                   # change inside a sampled window
    b.write_bytes(bytes(data))
    assert ck.fp(b) != fb
    assert ck.fp(tmp_path / "missing") is None


def test_store_hit_miss_force_and_changed_output(tmp_path):
    out = tmp_path / "asr.json"
    out.write_text("[1]")
    cp = ck.Store(tmp_path)
    k = ck.key("transcribe", "audio-fp", {"whisper_model": "large-v3-turbo"})
    assert cp.hit("transcribe", k, ["asr.json"]) is None          # never ran
    cp.save("transcribe", k, ["asr.json"], "12 câu")
    cp2 = ck.Store(tmp_path)                                       # reloaded from disk
    assert cp2.hit("transcribe", k, ["asr.json"])["detail"] == "12 câu"
    assert cp2.hit("transcribe", ck.key("transcribe", "other-audio"), ["asr.json"]) is None   # input changed
    assert ck.Store(tmp_path, force={"transcribe"}).hit("transcribe", k, ["asr.json"]) is None  # you asked to redo it
    out.write_text("[2]")                                          # output changed on disk
    assert ck.Store(tmp_path).hit("transcribe", k, ["asr.json"]) is None
    out.unlink()
    assert ck.Store(tmp_path).hit("transcribe", k, ["asr.json"]) is None


def test_code_and_settings_change_the_key():
    s1 = {"separate_model": "bs_roformer", "bg_volume": 0.8}
    s2 = dict(s1, bg_volume=1.0)                                   # a setting this step does not read
    assert ck.key("separate", ck.pick(s1, "separate_model")) == ck.key("separate", ck.pick(s2, "separate_model"))
    assert ck.key("separate", ck.pick(s1, "separate_model")) != \
        ck.key("separate", ck.pick(dict(s1, separate_model="htdemucs"), "separate_model"))
    assert ck.code("translate") and ck.code("translate") != ck.code("render")
