from src.agent.nodes import (
    _QUALITY_COMMENT_PLACEHOLDER,
    _SELF_RAG_STARS_PLACEHOLDER,
    _build_quality_comment,
    _score_to_stars,
)


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


# --- _build_quality_comment (Trust Tagging 품질 평가 한 줄 평, 2026-07-27 추가) ---


def test_build_quality_comment_passed_true():
    assert _build_quality_comment({"passed": True, "feedback": "ok"}) == "사실성 검증 완료 및 제약조건 만족"


def test_build_quality_comment_defaults_to_passed_when_key_missing():
    """_score_to_stars 호출부와 동일한 관례: report.get("passed", True) 처럼 passed 키가
    없으면 통과로 간주합니다(정상 경로가 json.loads(llm_output) 그대로라 키 보장이 없음)."""
    assert _build_quality_comment({}) == "사실성 검증 완료 및 제약조건 만족"


def test_build_quality_comment_extracts_first_sentence_on_failure():
    feedback = "코스 거리가 요청 조건과 다릅니다. 추가 확인이 필요합니다."
    assert _build_quality_comment({"passed": False, "feedback": feedback}) == (
        "주의 - 코스 거리가 요청 조건과 다릅니다."
    )


def test_build_quality_comment_does_not_split_on_decimal_point():
    """회귀 방지: 이 코드베이스의 feedback에는 "4.2km"/"6.0시간" 같은 소수점 숫자가 흔히
    등장합니다. 단순 split(".")였다면 "코스 거리가 4"에서 잘렸을 것입니다."""
    feedback = "코스 거리가 4.2km로 요청하신 3.0km 이내 조건을 초과합니다. 다른 코스를 확인하세요."
    result = _build_quality_comment({"passed": False, "feedback": feedback})
    assert result == "주의 - 코스 거리가 4.2km로 요청하신 3.0km 이내 조건을 초과합니다."


def test_build_quality_comment_handles_empty_feedback():
    assert _build_quality_comment({"passed": False, "feedback": ""}) == (
        "주의 - 세부 사유가 기록되지 않았습니다"
    )
    assert _build_quality_comment({"passed": False}) == "주의 - 세부 사유가 기록되지 않았습니다"


def test_build_quality_comment_truncates_long_single_sentence():
    long_sentence = "코스" + "가" * 100 + " 조건과 다릅니다"  # 마침표 없는 긴 단일 문장
    result = _build_quality_comment({"passed": False, "feedback": long_sentence})
    assert result.startswith("주의 - 코스")
    assert result.endswith("…")
    assert len(result) < len(long_sentence)


def test_quality_comment_placeholder_constant_is_a_distinctive_marker():
    assert _QUALITY_COMMENT_PLACEHOLDER == "{{QUALITY_COMMENT}}"
