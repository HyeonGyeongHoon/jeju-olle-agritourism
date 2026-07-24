from unittest.mock import patch

from src.agent import nodes
from src.agent.nodes import parse_intent_node


def test_parse_intent_fallback_preserves_wheelchair_requirement_from_query():
    """의도 파싱(LLM) 자체가 실패해도, 휠체어 하드 제약조건은 절대 완화되면 안 되므로 원본
    질의에 '휠체어'가 있으면 기본값 False 로 조용히 덮어써지지 않고 True 로 재확인되어야 합니다."""
    state = {"query": "휠체어로 갈 수 있는 코스 알려줘"}

    with patch.object(nodes, "get_chat_completion", side_effect=Exception("LLM 호출 실패")):
        result = parse_intent_node(state)

    assert result["parsed_constraints"]["hard_constraints"]["wheelchair_required"] is True


def test_parse_intent_fallback_defaults_to_false_without_wheelchair_keyword():
    state = {"query": "가을 감귤 테마 코스 추천해줘"}

    with patch.object(nodes, "get_chat_completion", side_effect=Exception("LLM 호출 실패")):
        result = parse_intent_node(state)

    assert result["parsed_constraints"]["hard_constraints"]["wheelchair_required"] is False


def test_parse_intent_fallback_on_malformed_json_also_checks_keyword():
    """예외가 아니라 JSON 파싱 실패(마크다운 펜스 처리 후에도 유효하지 않은 JSON)로 인한
    폴백에서도 동일하게 키워드 안전망이 적용되어야 합니다."""
    state = {"query": "휠체어 이용객도 참여 가능한 코스로 상품 기획해줘"}

    with patch.object(nodes, "get_chat_completion", return_value="이건 JSON이 아닙니다"):
        result = parse_intent_node(state)

    assert result["parsed_constraints"]["hard_constraints"]["wheelchair_required"] is True
