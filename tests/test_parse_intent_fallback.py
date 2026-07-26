import json
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


def test_parse_intent_maps_strict_single_crop_true_from_llm_json_into_b2b_params():
    """독점 작물 한정 옵션: LLM이 "당근만을 활용한" 같은 배타적 단일 작물 표현을 감지해
    strict_single_crop=true 를 반환하면, 그 값이 B2BQueryParams(b2b_params)에 그대로
    매핑되어야 한다."""
    state = {"query": "당근만을 활용한 코스 기획서 써줘"}
    llm_json = json.dumps({
        "hard_constraints": {"wheelchair_required": False},
        "vector_query": "당근 밭길",
        "target_month": None,
        "season": None,
        "key_item_or_crop": "당근",
        "preferred_location": None,
        "market_location_query": None,
        "concept_theme": None,
        "target_audience": "family",
        "include_market_insights": True,
        "strict_single_crop": True,
    }, ensure_ascii=False)

    with patch.object(nodes, "get_chat_completion", return_value=llm_json):
        result = parse_intent_node(state)

    assert result["b2b_params"]["strict_single_crop"] is True
    assert result["b2b_params"]["key_item_or_crop"] == "당근"


def test_parse_intent_defaults_strict_single_crop_false_when_llm_omits_it():
    """회귀 방지: 단순히 작물을 지정만 한 질의("당근 코스 기획서 써줘")에서 LLM이 배타적 한정
    의도가 없다고 판단해 strict_single_crop 을 false 로 반환하면(또는 필드를 생략하면),
    b2b_params 에도 false 로 반영되어 기존 동작(모든 작물 노출)이 유지되어야 한다."""
    state = {"query": "당근 코스 기획서 써줘"}
    llm_json = json.dumps({
        "hard_constraints": {"wheelchair_required": False},
        "vector_query": "당근 코스",
        "target_month": None,
        "season": None,
        "key_item_or_crop": "당근",
        "preferred_location": None,
        "market_location_query": None,
        "concept_theme": None,
        "target_audience": "family",
        "include_market_insights": True,
    }, ensure_ascii=False)

    with patch.object(nodes, "get_chat_completion", return_value=llm_json):
        result = parse_intent_node(state)

    assert result["b2b_params"]["strict_single_crop"] is False
