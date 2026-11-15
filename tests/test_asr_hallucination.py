from dubber import asr


def test_known_whisper_credits_are_dropped():
    for t in ("中文字幕——YK", "字幕志愿者 李宗盛", "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
              "Thank you for watching!", "ご視聴ありがとうございました"):
        assert asr.is_hallucination(t, 0, 1), t
    assert not asr.is_hallucination("这道菜特别好吃", 0, 1)
    assert not asr.is_hallucination("Let's go!", 0, 1)


def test_segment_without_voice_is_dropped():
    speech = [(10.0, 20.0)]
    assert asr.is_hallucination("好", 58.6, 60.0, speech)          # nobody speaks there
    assert not asr.is_hallucination("好", 12.0, 13.0, speech)      # inside speech
    assert not asr.is_hallucination("好", 9.5, 10.5, speech)       # half voiced is enough
    assert not asr.is_hallucination("好", 58.6, 60.0, None)        # VAD unavailable: keep
