from services.symptom_lexicon import (
    extract_features,
    extract_symptoms,
    keywords,
    normalize_text,
)


def test_symptom_aliases_collapse_to_one_label() -> None:
    cases = {
        "打开直播间一直转圈": "转圈",
        "点进去就白屏了": "白屏",
        "登录不上，一直提示账号异常": "登录失败",
        "付款后订单金额不对": "数据异常",
    }

    for text, expected in cases.items():
        assert expected in extract_symptoms(text)


def test_normalize_text_strips_filler_and_punctuation() -> None:
    raw = "麻烦看下，我这边打开就白屏了！！！谢谢"
    cleaned = normalize_text(raw)

    assert "麻烦" not in cleaned
    assert "谢谢" not in cleaned
    assert "白屏" in cleaned
    assert "！" not in cleaned


def test_feature_and_keywords_extraction() -> None:
    text = "直播间开播后黑屏，重启也没用"

    assert "直播" in extract_features(text)
    words = keywords(text, limit=4)
    assert "黑屏" in words
    assert len(words) <= 4
