from unittest.mock import MagicMock

import pytest

from src.agent import router
from src.models.schema import IntentCategory

# --- 1단계 규칙 기반 사전 필터 (LLM 호출 없이 즉시 분류) ---

def test_route_intent_declines_plain_course_recommendation_without_llm_call(monkeypatch):
    """사용자 요청: "당근 코스 추천해줘"처럼 "코스"+"추천"은 있지만 "기획서/기획안" 요청이
    아닌 질의는, LLM 분류가 실행마다 다르게 나올 수 있으므로(비결정적 관측됨) 규칙 기반으로
    확정하고 LLM을 아예 호출하지 않아야 합니다."""
    mock_llm = MagicMock()
    monkeypatch.setattr(router, "get_chat_completion", mock_llm)

    result = router.route_intent("당근 코스 추천해줘")

    mock_llm.assert_not_called()
    assert result.category == IntentCategory.OTHER
    assert "규칙 기반" in result.reason


def test_route_intent_declines_plain_recommendation_regardless_of_theme(monkeypatch):
    mock_llm = MagicMock()
    monkeypatch.setattr(router, "get_chat_completion", mock_llm)

    result = router.route_intent("가족이랑 갈만한 코스 추천해줘")

    mock_llm.assert_not_called()
    assert result.category == IntentCategory.OTHER


def test_route_intent_does_not_decline_when_proposal_keyword_present(monkeypatch):
    """"코스"+"추천"이 있어도 "기획서" 같은 결과물 생성 요청 키워드가 함께 있으면
    규칙으로 가로채지 않고 정상적으로 LLM 분류로 넘어가야 합니다."""
    fake_response = """{
        "category": "course_recommendation",
        "target_course": null,
        "reason": "테마 기획서 생성 요청"
    }"""
    mock_llm = MagicMock(return_value=fake_response)
    monkeypatch.setattr(router, "get_chat_completion", mock_llm)

    result = router.route_intent("당근 코스 추천해서 기획서 만들어줘")

    mock_llm.assert_called_once()
    assert result.category == IntentCategory.COURSE_RECOMMENDATION


def test_route_intent_does_not_decline_query_without_recommend_word(monkeypatch):
    """"코스"는 있지만 "추천"이 없는 질의(예: 순수 코스 정보 문의)는 규칙에 걸리지 않고
    LLM로 넘어가야 합니다."""
    fake_response = """{
        "category": "course_recommendation",
        "target_course": null,
        "reason": "휠체어 가능한 코스 문의"
    }"""
    mock_llm = MagicMock(return_value=fake_response)
    monkeypatch.setattr(router, "get_chat_completion", mock_llm)

    result = router.route_intent("휠체어로 갈 수 있는 코스 알려줘")

    mock_llm.assert_called_once()
    assert result.category == IntentCategory.COURSE_RECOMMENDATION


@pytest.mark.parametrize(
    ("query", "expected_course"),
    [
        ("1코스 괜찮을지 추천해줄래?", "1코스"),
        ("1코스 가볼 만한지 추천 좀 해줘", "1코스"),
        ("7코스 초보자한테 추천할 만해?", "7코스"),
        ("10-1코스 추천할 만한지 알려줘", "10-1코스"),
        ("3-A코스 추천해줄 만해?", "3-A코스"),
        ("올레 18-2코스 단체로 추천할 만한가요?", "18-2코스"),
        # 구어 표기도 DB 의 course_name 표기로 정규화되어야 합니다.
        ("1번 코스 추천해줄래?", "1코스"),
        ("10-1 코스 추천할 만해?", "10-1코스"),
        ("3-a코스 추천해줄래?", "3-A코스"),
    ],
)
def test_route_intent_classifies_opinion_about_specific_course_as_course_info(
    monkeypatch, query, expected_course
):
    """회귀 방지 2단(2026-07-25 라이브 QA):
    (1) 예전엔 "코스"+"추천"만 보고 무조건 거절해서 "1코스 괜찮을지 추천해줄래?"처럼
        이미 특정 코스를 지목한 의견 질의까지 "코스 추천은 서비스 범위 밖"이라고
        거절했습니다.
    (2) 그 거절만 풀고 LLM 에 맡겼더니, LLM 이 "추천"이라는 단어에 끌려 3회 반복 모두
        course_recommendation 으로 분류해 의견 질문에 5섹션 기획서를 생성했습니다.
    따라서 이 부류는 규칙으로 course_info 로 확정하고, LLM 이 자주 비워 보내던
    target_course 까지 정규식으로 채워야 합니다."""
    mock_llm = MagicMock()
    monkeypatch.setattr(router, "get_chat_completion", mock_llm)

    result = router.route_intent(query)

    mock_llm.assert_not_called()
    assert result.category == IntentCategory.COURSE_INFO
    assert result.target_course == expected_course
    assert "규칙 기반" in result.reason


def test_route_intent_specific_course_opinion_is_not_out_of_scope_reason(monkeypatch):
    """규칙이 course_info 로 확정할 때 "단순 코스 추천 요청"(거절) 사유 문구를 쓰면
    로그/QA 에서 거절로 오독되므로, 서로 다른 사유 문구여야 합니다."""
    monkeypatch.setattr(router, "get_chat_completion", MagicMock())

    result = router.route_intent("7코스 초보자한테 추천할 만해?")

    assert "단순 코스 추천 요청" not in result.reason
    assert "의견/적합성" in result.reason


@pytest.mark.parametrize(
    "query",
    [
        "당근 코스 추천해줘",
        "제주 올레 코스 하나 추천해줘",
        "가파도 코스 추천해줘",
        "2박 3일 코스 추천해줘",
        "10km 정도 되는 코스 추천해줘",
    ],
)
def test_route_intent_still_declines_vague_recommendation_without_specific_course(
    monkeypatch, query
):
    """특정 코스 지목 예외를 추가한 뒤에도, 코스를 지목하지 않은 막연한 추천 요청은
    여전히 규칙으로 거절해야 합니다. "2박 3일"/"10km"처럼 숫자가 들어 있어도 코스
    번호가 아니므로 예외로 인정되면 안 됩니다."""
    mock_llm = MagicMock()
    monkeypatch.setattr(router, "get_chat_completion", mock_llm)

    result = router.route_intent(query)

    mock_llm.assert_not_called()
    assert result.category == IntentCategory.OTHER
    assert "단순 코스 추천" in result.reason


@pytest.mark.parametrize(
    "query",
    [
        "1코스 말고 다른 코스 추천해줘",
        "7코스 빼고 추천해줘",
        "10-1코스 제외하고 코스 추천해줘",
    ],
)
def test_route_intent_declines_when_specific_course_is_only_excluded(
    monkeypatch, query
):
    """반례 검토: 코스 번호가 "제외 대상"으로만 언급된 질의는 특정 코스를 지목한 게
    아니라 여전히 막연한 추천 요청이므로 거절되어야 합니다."""
    mock_llm = MagicMock()
    monkeypatch.setattr(router, "get_chat_completion", mock_llm)

    result = router.route_intent(query)

    mock_llm.assert_not_called()
    assert result.category == IntentCategory.OTHER


def test_route_intent_does_not_decline_proposal_for_specific_course(monkeypatch):
    """재확인: "1코스로 기획서 만들어줘" 류는 결과물 생성 키워드가 있어 원래도 거절되지
    않았고, 특정 코스 예외 추가 후에도 그대로여야 합니다."""
    fake_response = """{
        "category": "course_recommendation",
        "target_course": "1코스",
        "reason": "특정 코스 기반 기획서 생성 요청"
    }"""
    mock_llm = MagicMock(return_value=fake_response)
    monkeypatch.setattr(router, "get_chat_completion", mock_llm)

    result = router.route_intent("1코스로 기획서 만들어줘")

    mock_llm.assert_called_once()
    assert result.category == IntentCategory.COURSE_RECOMMENDATION
    assert result.target_course == "1코스"


# --- target_course 정규식 폴백 (LLM 이 null 로 비워 보내는 경우 대비) ---

def test_route_intent_fills_target_course_when_llm_returns_null(monkeypatch):
    """회귀 방지: 라이브에서 route_intent 가 "7코스" 질의에 대해 3회 모두
    target_course=None 을 반환해, 다운스트림에서 코스를 특정하지 못했습니다. LLM 이
    비워 보내도 질의의 "N코스" 표기로 채워야 합니다."""
    fake_response = """{
        "category": "course_info",
        "target_course": null,
        "reason": "7코스 스탬프 위치 문의"
    }"""
    monkeypatch.setattr(
        router, "get_chat_completion", lambda sys_prompt, query: fake_response
    )

    result = router.route_intent("7코스 스탬프 위치가 어디야?")

    assert result.category == IntentCategory.COURSE_INFO
    assert result.target_course == "7코스"


def test_route_intent_prefers_llm_target_course_over_regex_fallback(monkeypatch):
    """폴백은 LLM 이 값을 주지 않았을 때만 쓰여야 합니다(LLM 값을 덮어쓰면 안 됨)."""
    fake_response = """{
        "category": "course_info",
        "target_course": "10-1코스",
        "reason": "가파도 코스 문의"
    }"""
    monkeypatch.setattr(
        router, "get_chat_completion", lambda sys_prompt, query: fake_response
    )

    result = router.route_intent("가파도 코스 스탬프 위치가 어디야?")

    assert result.target_course == "10-1코스"


def test_route_intent_fills_target_course_on_llm_exception(monkeypatch):
    """LLM 예외 폴백(COURSE_RECOMMENDATION) 경로에서도 코스명은 유지되어야 합니다."""
    monkeypatch.setattr(
        router,
        "get_chat_completion",
        lambda sys_prompt, query: "올바르지 않은 JSON 응답입니다.",
    )

    result = router.route_intent("1코스로 기획서 만들어줘")

    assert result.category == IntentCategory.COURSE_RECOMMENDATION
    assert result.target_course == "1코스"


def test_route_intent_does_not_fill_target_course_without_course_mention(monkeypatch):
    """코스 표기가 없는 질의에는 폴백이 아무 값도 만들어내지 않아야 합니다."""
    monkeypatch.setattr(
        router,
        "get_chat_completion",
        lambda sys_prompt, query: "올바르지 않은 JSON 응답입니다.",
    )

    result = router.route_intent("바다가 예쁜 코스")

    assert result.target_course is None


def test_route_intent_handles_greeting_without_llm_call(monkeypatch):
    mock_llm = MagicMock()
    monkeypatch.setattr(router, "get_chat_completion", mock_llm)

    result = router.route_intent("안녕")

    mock_llm.assert_not_called()
    assert result.category == IntentCategory.OTHER
    assert "인사말" in result.reason


def test_route_intent_handles_profanity_without_llm_call(monkeypatch):
    mock_llm = MagicMock()
    monkeypatch.setattr(router, "get_chat_completion", mock_llm)

    result = router.route_intent("너 바보야?")

    mock_llm.assert_not_called()
    assert result.category == IntentCategory.OTHER
    assert "부적절한 표현" in result.reason


# --- 2단계 LLM 문맥 분류 ---

def test_route_intent_course_info(monkeypatch):
    fake_response = """{
        "category": "course_info",
        "target_course": "1코스",
        "reason": "1코스 소요시간 문의"
    }"""
    monkeypatch.setattr(
        router, "get_chat_completion", lambda sys_prompt, query: fake_response
    )

    result = router.route_intent("1코스 총 소요시간이 얼마나 돼?")
    assert result.category == IntentCategory.COURSE_INFO
    assert result.target_course == "1코스"


def test_route_intent_course_recommendation(monkeypatch):
    fake_response = """{
        "category": "course_recommendation",
        "target_course": null,
        "reason": "휠체어 가능한 코스 추천 요청"
    }"""
    monkeypatch.setattr(
        router, "get_chat_completion", lambda sys_prompt, query: fake_response
    )

    result = router.route_intent("휠체어로 갈 수 있는 코스 알려줘")
    assert result.category == IntentCategory.COURSE_RECOMMENDATION
    assert result.target_course is None


def test_route_intent_olle_general_info(monkeypatch):
    fake_response = """{
        "category": "olle_general_info",
        "target_course": null,
        "reason": "올레길 준비물 안내 질문"
    }"""
    monkeypatch.setattr(
        router, "get_chat_completion", lambda sys_prompt, query: fake_response
    )

    result = router.route_intent("올레길 탐방할 때 필수 준비물이 뭐야?")
    assert result.category == IntentCategory.OLLE_GENERAL_INFO


def test_route_intent_other(monkeypatch):
    fake_response = """{
        "category": "other",
        "target_course": null,
        "reason": "제주 올레길과 관련 없는 날씨 질문"
    }"""
    monkeypatch.setattr(
        router, "get_chat_completion", lambda sys_prompt, query: fake_response
    )

    result = router.route_intent("오늘 서울 날씨 어때?")
    assert result.category == IntentCategory.OTHER


def test_route_intent_info_lookup(monkeypatch):
    fake_response = """{
        "category": "info_lookup",
        "target_course": null,
        "reason": "제주 밭담문화 정보 자체를 묻는 질문"
    }"""
    monkeypatch.setattr(
        router, "get_chat_completion", lambda sys_prompt, query: fake_response
    )

    result = router.route_intent("제주 밭담문화가 뭐야?")
    assert result.category == IntentCategory.INFO_LOOKUP
    assert result.target_course is None


def test_route_intent_strips_markdown_code_fence(monkeypatch):
    fenced_response = """```json
    {
        "category": "course_info",
        "target_course": "7코스",
        "reason": "7코스 난이도 문의"
    }
    ```"""
    monkeypatch.setattr(
        router, "get_chat_completion", lambda sys_prompt, query: fenced_response
    )

    result = router.route_intent("7코스 난이도는 어떤가요?")
    assert result.category == IntentCategory.COURSE_INFO
    assert result.target_course == "7코스"


def test_route_intent_fallback_on_exception(monkeypatch):
    monkeypatch.setattr(
        router,
        "get_chat_completion",
        lambda sys_prompt, query: "올바르지 않은 JSON 응답입니다.",
    )

    result = router.route_intent("바다가 예쁜 코스")
    assert result.category == IntentCategory.COURSE_RECOMMENDATION
    assert "기본 추천 파이프라인으로 전환" in result.reason
