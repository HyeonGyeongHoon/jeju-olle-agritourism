from src.agent.nodes import _score_to_stars, _SELF_RAG_STARS_PLACEHOLDER


def test_score_to_stars_full_marks_for_high_score_and_passed():
    assert _score_to_stars(1.0, True) == "★★★★★"
    assert _score_to_stars(0.9, True) == "★★★★★"


def test_score_to_stars_bucket_thresholds():
    assert _score_to_stars(0.89, True) == "★★★★☆"
    assert _score_to_stars(0.7, True) == "★★★★☆"
    assert _score_to_stars(0.69, True) == "★★★☆☆"
    assert _score_to_stars(0.5, True) == "★★★☆☆"
    assert _score_to_stars(0.49, True) == "★★☆☆☆"
    assert _score_to_stars(0.3, True) == "★★☆☆☆"
    assert _score_to_stars(0.29, True) == "★☆☆☆☆"
    assert _score_to_stars(0.0, True) == "★☆☆☆☆"


def test_score_to_stars_caps_at_three_when_not_passed():
    """검증에 실제로 통과하지 못했다면(3회 순환 끝에 강제 종료된 경우 포함) 점수가 높아도
    최대 3점으로 제한해야 함."""
    assert _score_to_stars(1.0, False) == "★★★☆☆"
    assert _score_to_stars(0.9, False) == "★★★☆☆"
    assert _score_to_stars(0.5, False) == "★★★☆☆"
    # 원래 점수가 3점 미만이면 그대로 유지(더 낮춰지지 않음)
    assert _score_to_stars(0.2, False) == "★☆☆☆☆"


def test_placeholder_constant_is_a_distinctive_marker():
    assert _SELF_RAG_STARS_PLACEHOLDER == "{{SELF_RAG_STARS}}"
