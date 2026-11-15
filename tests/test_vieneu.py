from dubber import tts, vieneu_tts


def test_label_gives_gender_region_style():
    v = vieneu_tts.parse_label("⭐ Mai Anh — Nữ · Bắc · Phong cách tin tức", "Mai Anh")
    assert v == {"id": "Mai Anh", "name": "⭐ Mai Anh", "gender": "female", "info": "miền Bắc, tin tức"}
    assert vieneu_tts.parse_label("Thái Sơn — Nam · Nam · Phong cách kể chuyện", "Thái Sơn")["gender"] == "male"


def test_vieneu_voice_and_cache_key():
    s = {"tts_provider": "vieneu", "voice_gender_mode": "auto", "vieneu_voice_male": "Hải Đăng", "vieneu_voice_female": "Mai Anh"}
    assert tts.voice_for(s, "female") == "Mai Anh" and tts.voice_for(s, "male") == "Hải Đăng"
    a = tts._cache_name("Xin chào", s, "vieneu", "Hải Đăng")
    assert a != tts._cache_name("Xin chào", s, "vieneu", "Mai Anh") and a != tts._cache_name("Xin chào", dict(s, edge_rate="+0%", edge_pitch="+0Hz"), "edge", "Hải Đăng")


def test_missing_install_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(vieneu_tts, "python_path", lambda: tmp_path / "nope" / "python.exe")
    monkeypatch.setattr(vieneu_tts, "_w", None)
    try:
        vieneu_tts._worker()
        assert False
    except RuntimeError as e:
        assert "install_vieneu.ps1" in str(e)
