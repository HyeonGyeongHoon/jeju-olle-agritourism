from src.agent import router
from src.models.schema import IntentCategory


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
