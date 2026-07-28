from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent import nodes
from src.agent.nodes import quick_responder_node
from src.services import db_service
from src.services.db_service import _fetch_course_meta_by_name

# 패치 대상 모듈 선택 기준(2026-07-26 DB 헬퍼 서비스 레이어 분리):
# _get_olle_relevant_admin_dongs 는 db_service 에 패치해야 합니다.
# quick_responder_node 가 이 함수를 직접 부르지 않고, 같은 모듈 안의
# _resolve_stats_region_from_areas 를 통해 간접적으로만 쓰기 때문에
# nodes 네임스페이스에 패치해도 그 내부 호출은 가로채지지 않습니다.
# 반대로 노드 본문이 직접 부르는 _search_culture_knowledge /
# _fetch_market_insight / _fetch_course_meta_by_name 은 예전처럼
# nodes 에 패치하는 것이 맞습니다.


def _base_state(**b2b_overrides):
    b2b_params = {
        "key_item_or_crop": None,
        "preferred_location": None,
        "target_month": 5,
        "include_market_insights": True,
        "market_location_resolution": None,
    }
    b2b_params.update(b2b_overrides)
    return {
        "query": "제주 밭담문화와 관광객 통계가 뭐야?",
        "parsed_constraints": {"vector_query": "밭담문화"},
        "b2b_params": b2b_params,
    }


def test_quick_responder_node_returns_rejection_without_any_db_lookup_when_exit_early():
    """정책 전환(2026-07-25 무조건 반려): retrieve_rag_node 가 DB 매칭 0건으로 검색을 중단하면
    (is_exit_early=True) route_after_retriever 가 report_generator 대신 이 노드로 보냅니다.
    이 노드는 exit_reason 을 그대로 반려 메시지로 돌려주기만 하고, culture_crop_knowledge/
    visitor_analytics 조회나 LLM 호출을 전혀 하지 않아야 합니다 — 반려 사유와 무관한 문화·통계
    내용을 덧붙이면 사실상 대체 정보 추천이 되고, quality_checker 가 그 무관한 컨텍스트와 반려
    메시지를 대조하며 재작성 루프를 돌게 됩니다."""
    state = _base_state(key_item_or_crop="마늘", preferred_location="대정읍")
    state["intent_category"] = "course_recommendation"
    state["is_exit_early"] = True
    state["exit_reason"] = "'대정읍' 지역과 직접 겹치는 올레 코스를 찾지 못해 기획서를 생성할 수 없습니다."

    with patch.object(nodes, "get_supabase_client") as mock_client, \
         patch.object(nodes, "_search_culture_knowledge") as mock_search_culture, \
         patch.object(nodes, "_fetch_market_insight") as mock_fetch_market, \
         patch.object(nodes, "get_chat_completion") as mock_llm:
        result = quick_responder_node(state)

    mock_client.assert_not_called()
    mock_search_culture.assert_not_called()
    mock_fetch_market.assert_not_called()
    mock_llm.assert_not_called()
    assert "기획서를 작성할 수 없습니다" in result["final_response"]
    assert "대정읍" in result["final_response"]
    assert result["docent_answer"] == result["final_response"]


def test_quick_responder_node_uses_generic_rejection_when_exit_reason_missing():
    """exit_reason 이 비어 있어도(방어) 반려 메시지 자체는 만들어야 합니다."""
    state = _base_state()
    state["is_exit_early"] = True
    state["exit_reason"] = None

    with patch.object(nodes, "get_supabase_client") as mock_client, \
         patch.object(nodes, "get_chat_completion") as mock_llm:
        result = quick_responder_node(state)

    mock_client.assert_not_called()
    mock_llm.assert_not_called()
    assert "기획서를 작성할 수 없습니다" in result["final_response"]
    assert "찾지 못했습니다" in result["final_response"]


def test_quick_responder_node_declines_course_recommendation_for_other_intent():
    """사용자 요청: "당근 코스 추천해줘"처럼 intent_category가 "other"로 분류된 질의는
    (제주 올레와 아예 무관한 "제주도 날씨 어때?" 같은 질문이든, 기획서가 아닌 단순 코스
    "추천"을 요청하는 것이든) 문화·작물 지식이나 통계를 검색해 대신 답하지 않고, 서비스
    범위를 안내하는 보편적인 거절 메시지로 즉시 종료해야 합니다."""
    state = _base_state(key_item_or_crop="당근")
    state["intent_category"] = "other"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge") as mock_search_culture, \
         patch.object(nodes, "_fetch_market_insight") as mock_fetch_market, \
         patch.object(nodes, "get_chat_completion") as mock_llm:
        result = quick_responder_node(state)

    mock_search_culture.assert_not_called()
    mock_fetch_market.assert_not_called()
    mock_llm.assert_not_called()
    assert "코스 추천" in result["final_response"] or "추천해 드리지 않" in result["final_response"]
    assert result["culture_chunks"] == []
    assert result["market_insight"] is None


def test_quick_responder_node_builds_answer_from_culture_and_market():
    culture_chunks = [
        {
            "title": "제주 밭담문화",
            "content": "제주의 밭담은 화산석으로 쌓은 경계 담이다.",
            "target_crop": None,
            "crop_name": None,
            "region_tag": None,
            "season_stage": None,
            "active_months": None,
        }
    ]
    market_insight = {
        "region_dong": "구좌읍",
        "year_month": "2026-05",
        "total_visitors": 12000,
        "yoy_growth_rate": 0.052,
        "female_ratio": None,
        "male_ratio": None,
        "youth_10s_ratio": None,
        "young_2030_ratio": None,
        "middle_4060_ratio": None,
        "senior_70s_ratio": None,
        "foreign_visitors": None,
    }

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge", return_value=culture_chunks) as mock_search_culture, \
         patch.object(nodes, "_fetch_market_insight", return_value=market_insight):
        result = quick_responder_node(
            _base_state(preferred_location="구좌읍", key_item_or_crop="밭담")
        )

    assert result["culture_chunks"] == culture_chunks
    assert result["market_insight"] == market_insight
    assert "제주의 밭담은 화산석으로 쌓은 경계 담이다." in result["final_response"]
    assert "12,000명" in result["final_response"]
    assert "retrieved_chunks" not in result
    mock_search_culture.assert_called_once()


# --- 작물/테마 신호 없는 순수 통계 질의는 문화지식 검색 자체를 생략 (2026-07-27) ---
# _search_culture_knowledge 는 key_item_or_crop 이 없으면 fallback_query(원본 질의 텍스트)를
# 그대로 임베딩해 임계치 0.1의 낮은 유사도로 검색하므로, "OO동 방문객 수는?"처럼 작물/테마와
# 무관한 순수 통계 질의에도 유채꽃/양파 등 무관한 문서가 섞여 나올 수 있었습니다(라이브 QA 확인).


def test_quick_responder_node_skips_culture_search_when_no_crop_or_theme():
    """key_item_or_crop 도 concept_theme 도 없고 target_course 도 없으면(=순수 통계 질의),
    _search_culture_knowledge 를 아예 호출하지 않고 관광 통계만으로 답해야 합니다."""
    market_insight = {
        "region_dong": "구좌읍",
        "year_month": "2026-05",
        "total_visitors": 12000,
        "yoy_growth_rate": None,
        "female_ratio": None,
        "male_ratio": None,
        "youth_10s_ratio": None,
        "young_2030_ratio": None,
        "middle_4060_ratio": None,
        "senior_70s_ratio": None,
        "foreign_visitors": None,
    }
    state = _base_state(preferred_location="구좌읍")
    state["query"] = "구좌읍 최근 방문객 수는?"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge") as mock_search_culture, \
         patch.object(nodes, "_fetch_market_insight", return_value=market_insight):
        result = quick_responder_node(state)

    mock_search_culture.assert_not_called()
    assert result["culture_chunks"] == []
    assert "[제주 로컬 문화 및 작물 지식 가이드]" not in result["final_response"]
    assert "12,000명" in result["final_response"]


def test_quick_responder_node_runs_culture_search_when_only_concept_theme_present():
    """key_item_or_crop 은 없어도 concept_theme(예: "힐링")이 있으면 여전히 문화지식 검색을
    실행해야 합니다 — 테마 신호 자체가 없는 경우만 생략 대상입니다."""
    state = _base_state(concept_theme="영농 체험")

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]) as mock_search_culture, \
         patch.object(nodes, "_fetch_market_insight", return_value=None):
        quick_responder_node(state)

    mock_search_culture.assert_called_once()


def test_quick_responder_node_no_results_returns_apology_without_llm_call():
    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=None), \
         patch.object(nodes, "get_chat_completion") as mock_llm:
        result = quick_responder_node(_base_state())

    mock_llm.assert_not_called()
    assert "찾지 못했습니다" in result["final_response"]
    assert result["culture_chunks"] == []
    assert result["market_insight"] is None


def test_quick_responder_node_includes_market_location_resolution_note():
    market_insight = {
        "region_dong": "구좌읍",
        "year_month": "2026-05",
        "total_visitors": 12000,
        "yoy_growth_rate": None,
        "female_ratio": None,
        "male_ratio": None,
        "youth_10s_ratio": None,
        "young_2030_ratio": None,
        "middle_4060_ratio": None,
        "senior_70s_ratio": None,
        "foreign_visitors": None,
    }
    resolution = {
        "region_dong": "구좌읍",
        "metric": "foreign_visitors",
        "value": 5000,
        "year_month": "2026-05",
        "direction": "desc",
    }

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=market_insight):
        result = quick_responder_node(_base_state(
            preferred_location="구좌읍", market_location_resolution=resolution
        ))

    assert "구좌읍" in result["final_response"]
    assert "외국인 방문객" in result["final_response"]
    assert "1위 지역으로 자동 선정" in result["final_response"]


def test_quick_responder_node_passes_raw_none_month_to_market_insight_when_unspecified():
    """사용자 요청: "외도동 최근 방문객 수는?"처럼 질의에 월이 명시되지 않으면(b2b_params.
    target_month=None), quick_responder_node 는 "오늘 날짜의 달"로 대체한 값이 아니라 원본
    그대로(None)를 _fetch_market_insight 에 넘겨야 합니다."""
    state = _base_state(preferred_location="외도동", target_month=None)

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=None) as mock_fetch_market:
        quick_responder_node(state)

    assert mock_fetch_market.call_args[0][2] is None


def test_quick_responder_node_skips_market_insight_when_disabled():
    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight") as mock_fetch_market:
        quick_responder_node(_base_state(include_market_insights=False))

    mock_fetch_market.assert_not_called()


def test_quick_responder_node_scopes_search_by_target_course_when_crop_and_location_missing():
    """회귀 방지: "1코스는 무슨 작물이 유명해?" 처럼 target_course 가 인식된 질의는
    예전엔 target_course 가 완전히 무시되어 일반 키워드 검색과 동일하게 동작했습니다.
    key_item_or_crop/preferred_location 이 비어 있으면 그 코스의 실제 crops/
    administrative_areas 로 검색 조건을 자동 보완해야 합니다."""
    course_meta = {"course_name": "1코스", "crops": "감귤,한라봉", "administrative_areas": "성산읍,표선면"}
    culture_chunks = [
        {"title": "감귤 재배", "content": "감귤은 가을에 수확한다.", "target_crop": "감귤",
         "crop_name": "감귤", "region_tag": None, "season_stage": None, "active_months": None}
    ]
    state = _base_state()
    state["target_course"] = "1코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=course_meta) as mock_fetch_course, \
         patch.object(db_service, "_get_olle_relevant_admin_dongs", return_value={"성산읍", "표선면"}), \
         patch.object(nodes, "_search_culture_knowledge", return_value=culture_chunks) as mock_search_culture, \
         patch.object(nodes, "_fetch_market_insight", return_value=None) as mock_fetch_market:
        result = quick_responder_node(state)

    assert mock_fetch_course.call_args[0][1] == "1코스"
    assert mock_search_culture.call_args[0][1] == "감귤"
    assert mock_fetch_market.call_args[0][1] == "성산읍"
    assert "1코스" in result["final_response"]


# --- preferred_location 에 코스명이 들어오는 오염 방어 (2026-07-25 라이브 QA) ---

def test_quick_responder_node_replaces_course_name_location_with_real_region():
    """회귀 방지: intent_parser 가 "1코스 괜찮을지 추천해줄래?"에서 preferred_location='1코스'
    (코스명 자체)를 채우는데, 예전 가드는 "값이 truthy 면 건너뛴다"였기 때문에 코스의 실제 지역이
    끝까지 쓰이지 않았습니다. 그 결과 tool_agent 가 통계 도구를 region_dong='1코스'로 호출하고,
    도구의 검증 실패 메시지("조회 가능한 지역: ...")를 LLM 이 그대로 "대신 이 지역들을
    추천드릴 수 있어요"로 노출해 코스/지역 추천 금지 정책이 우회됐습니다."""
    # administrative_areas 는 법정리 단위(시흥리) — 통계 테이블의 행정동/읍면(성산읍)과 계층이
    # 다르므로 그대로 쓰면 안 되고 변환되어야 합니다.
    course_meta = {"course_name": "1코스", "crops": "감귤", "administrative_areas": "시흥리,종달리"}
    state = _base_state(preferred_location="1코스")
    state["target_course"] = "1코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=course_meta), \
         patch.object(db_service, "_get_olle_relevant_admin_dongs", return_value={"성산읍", "구좌읍"}), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=None) as mock_fetch_market:
        result = quick_responder_node(state)

    # 통계 조회에도, 하류 노드가 읽는 state 에도 코스명이 아니라 실제 행정 지역이 쓰여야 합니다.
    assert mock_fetch_market.call_args[0][1] == "성산읍"
    assert result["b2b_params"]["preferred_location"] == "성산읍"


def test_quick_responder_node_writes_corrected_location_back_to_state():
    """하류 tool_agent_node 는 로컬 변수가 아니라 state 의 b2b_params.preferred_location 을
    읽어 통계 도구 인자를 만듭니다. 이 노드가 정정한 값을 state 에 다시 쓰지 않으면
    tool_agent 는 여전히 오염된 코스명으로 도구를 호출합니다(경계면 버그)."""
    state = _base_state(preferred_location="10-1코스")
    state["target_course"] = "10-1코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=None), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=None):
        result = quick_responder_node(state)

    assert result["b2b_params"]["preferred_location"] is None
    # 원래 b2b_params 의 다른 키는 보존되어야 합니다.
    assert result["b2b_params"]["target_month"] == 5


def test_quick_responder_node_clears_course_name_location_when_region_unresolvable():
    """코스의 경유 행정구역을 통계 조회 가능한 행정동/읍·면으로 변환할 수 없으면, 검증되지 않은
    지역을 넘기지 않고 비워야 합니다(fail-closed) — 잘못된 지역을 넘기면 도구가 다시 "조회 가능한
    지역" 목록을 반환해 같은 정책 우회가 재발합니다."""
    course_meta = {"course_name": "9코스", "crops": "감귤", "administrative_areas": "알수없는리"}
    state = _base_state(preferred_location="9코스")
    state["target_course"] = "9코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=course_meta), \
         patch.object(db_service, "_get_olle_relevant_admin_dongs", return_value={"성산읍", "구좌읍"}), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=None) as mock_fetch_market:
        result = quick_responder_node(state)

    assert mock_fetch_market.call_args[0][1] is None
    assert result["b2b_params"]["preferred_location"] is None


def test_quick_responder_node_keeps_real_region_location_untouched():
    """대조군: preferred_location 이 실제 지역명이면(코스명 표기가 아니면) 그대로 유지해야
    합니다 — 코스명 오염 방어가 정상적인 지역 조건을 지우면 안 됩니다."""
    state = _base_state(preferred_location="구좌읍")
    state["target_course"] = "1코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name",
                      return_value={"course_name": "1코스", "crops": "감귤",
                                    "administrative_areas": "시흥리"}), \
         patch.object(db_service, "_get_olle_relevant_admin_dongs", return_value={"성산읍"}), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=None) as mock_fetch_market:
        result = quick_responder_node(state)

    assert mock_fetch_market.call_args[0][1] == "구좌읍"
    assert result["b2b_params"]["preferred_location"] == "구좌읍"


# --- 코스 의견/적합성 질의에 DB 실측치를 근거로 제공 (2026-07-25 라이브 QA) ---

_COURSE_7_META = {
    "course_name": "7코스",
    "crops": "감귤",
    "administrative_areas": "서홍동,법환동,강정동",
    "total_distance_km": 17.6,
    "estimated_time_hours": 6.0,
    "estimated_time_text": "5~6시간",
    "difficulty": "중",
    "start_point": "제주올레 여행자센터",
    "end_point": "월평 아왜낭목",
}


def test_quick_responder_node_puts_course_metrics_into_prompt():
    """회귀 방지: 예전엔 course_note 가 "이 질문은 '7코스' 코스에 대한 것입니다"라는 라벨뿐이라
    거리/소요시간/난이도가 LLM 에게 전달되지 않았고, 그 결과 "7코스는 비교적 평탄해서 초보자도
    좋다"처럼 DB 근거 없는 추측이 답변에 들어갔습니다(실제 7코스는 17.6km/6시간/난이도 중)."""
    state = _base_state(preferred_location="7코스")
    state["query"] = "7코스 초보자한테 추천할 만해?"
    state["target_course"] = "7코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=_COURSE_7_META), \
         patch.object(db_service, "_get_olle_relevant_admin_dongs", return_value={"대천동"}), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=None):
        result = quick_responder_node(state)

    assert "17.6km" in result["final_response"]
    assert "5～6시간" in result["final_response"] or "6.0시간" in result["final_response"]
    assert "난이도 '중'" in result["final_response"] or "난이도: 중" in result["final_response"]
    assert "제주올레 여행자센터" in result["final_response"]


def test_quick_responder_node_answers_from_course_meta_without_culture_or_market():
    """회귀 방지: 문화지식/통계가 둘 다 비어 있으면 예전엔 무조건 "찾지 못했습니다" 사과문으로
    끝났습니다. 코스 메타데이터만 있어도 코스 의견 질의에는 답할 수 있어야 합니다."""
    state = _base_state(preferred_location="7코스")
    state["query"] = "7코스 초보자한테 추천할 만해?"
    state["target_course"] = "7코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=_COURSE_7_META), \
         patch.object(
             db_service, "_get_olle_relevant_admin_dongs", return_value=set()
         ), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=None):
        result = quick_responder_node(state)

    assert "총 거리: 17.6km" in result["final_response"]
    assert "찾지 못했습니다" not in result["final_response"]


def test_fetch_course_meta_by_name_selects_opinion_relevant_columns():
    """회귀 방지: 예전엔 select("course_name, crops, administrative_areas") 뿐이어서, 코스
    적합성 판단에 필요한 거리/소요시간/난이도/시작·종점이 아예 조회되지 않았습니다."""
    captured = {}

    class _FakeTable:
        def select(self, cols):
            captured["cols"] = cols
            return self

        def eq(self, *args):
            return self

        def limit(self, *args):
            return self

        def execute(self):
            return SimpleNamespace(data=[{"course_name": "7코스"}])

    client = MagicMock()
    client.table.return_value = _FakeTable()

    assert _fetch_course_meta_by_name(client, "7코스") == {"course_name": "7코스"}
    for column in (
        "total_distance_km", "estimated_time_hours", "estimated_time_text",
        "difficulty", "start_point", "end_point", "crops", "administrative_areas",
    ):
        assert column in captured["cols"]


def test_quick_responder_node_omits_missing_course_metric_fields():
    """DB 에 값이 없는 필드는 프롬프트에 넣지 않아야 합니다(빈 라벨을 보고 LLM 이 추측으로
    메우는 것을 막기 위함)."""
    course_meta = {
        "course_name": "1코스", "crops": "감귤", "administrative_areas": "시흥리",
        "total_distance_km": 15.1, "estimated_time_hours": None,
        "estimated_time_text": None, "difficulty": "", "start_point": None, "end_point": None,
    }
    state = _base_state()
    state["target_course"] = "1코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=course_meta), \
         patch.object(db_service, "_get_olle_relevant_admin_dongs", return_value={"성산읍"}), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_fetch_market_insight", return_value=None):
        result = quick_responder_node(state)

    assert "15.1km" in result["final_response"]
    assert "소요시간:" not in result["final_response"]
    assert "난이도: " not in result["final_response"]
    assert "시작점: " not in result["final_response"]


def test_quick_responder_node_does_not_override_explicit_crop_with_target_course():
    """key_item_or_crop 이 이미 명시적으로 채워져 있으면, target_course 의 재배작물로
    덮어쓰지 않고 사용자가 실제로 물어본 작물을 그대로 검색 조건으로 써야 합니다."""
    course_meta = {"course_name": "1코스", "crops": "감귤,한라봉", "administrative_areas": "성산읍"}
    state = _base_state(key_item_or_crop="당근")
    state["target_course"] = "1코스"

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=course_meta), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]) as mock_search_culture, \
         patch.object(nodes, "_fetch_market_insight", return_value=None):
        quick_responder_node(state)

    assert mock_search_culture.call_args[0][1] == "당근"


def test_quick_responder_node_skips_market_insight_when_no_stats_keyword():
    """질문에 통계 키워드가 전혀 포함되어 있지 않은 경우, include_market_insights가 True여도
    _fetch_market_insight 및 _resolve_stats_region_from_areas 호출을 건너뛰고 
    [관광 방문객 통계] 분석 파트를 결과물에서 제외하는지 확인합니다."""
    state = _base_state()
    state["query"] = "1코스 소요시간 알려줘"
    state["target_course"] = "1코스"
    course_meta = {"course_name": "1코스", "crops": "감귤", "administrative_areas": "시흥리,종달리"}

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=course_meta), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]), \
         patch.object(nodes, "_resolve_stats_region_from_areas") as mock_resolve_region, \
         patch.object(nodes, "_fetch_market_insight") as mock_fetch_market:
        
        result = quick_responder_node(state)

    # 1. DB 매핑 및 통계 조회가 모두 스킵되어야 함
    mock_resolve_region.assert_not_called()
    mock_fetch_market.assert_not_called()
    
    # 2. 결과물에 통계 분석 파트가 없어야 함
    assert "[방문객 빅데이터 및 트래픽 분석]" not in result["final_response"]
    assert "[대상 코스 상세 스펙]" in result["final_response"]


def test_quick_responder_node_pure_course_spec_query_fast_path():
    """질문에 작물/농업/통계 관련 키워드가 없는 순수 코스 스펙(예: 소요시간) 질의인 경우,
    오직 코스 메타 DB 조회 1회만 수행하고 RAG 검색, 지역 매핑, 통계 조회를 모두 스킵하는지 확인합니다."""
    state = _base_state()
    state["query"] = "1코스 소요시간 알려줘"
    state["target_course"] = "1코스"
    course_meta = {
        "course_name": "1코스",
        "crops": "감자",
        "administrative_areas": "시흥리,종달리,오조리",
        "total_distance_km": 15.0,
        "estimated_time_text": "5시간",
        "difficulty": "중",
        "content": "시흥초등학교에서 출발해 광치기해변까지 이어지는 올레길의 첫 단추 코스"
    }

    with patch.object(nodes, "get_supabase_client", return_value=MagicMock()), \
         patch.object(nodes, "_fetch_course_meta_by_name", return_value=course_meta) as mock_fetch_course, \
         patch.object(nodes, "_search_culture_knowledge") as mock_search_culture, \
         patch.object(nodes, "_resolve_stats_region_from_areas") as mock_resolve_region, \
         patch.object(nodes, "_fetch_market_insight") as mock_fetch_market:
        
        result = quick_responder_node(state)

    # 1. 코스 메타 DB 조회는 정확히 1회만 수행됨
    assert mock_fetch_course.call_count == 1

    # 2. 문화지식 RAG 검색, 지역 변환, 통계 조회는 모두 생략됨
    mock_search_culture.assert_not_called()
    mock_resolve_region.assert_not_called()
    mock_fetch_market.assert_not_called()
    
    # 3. 조립된 텍스트 결과에 스펙만 포함됨
    assert "[대상 코스 상세 스펙]" in result["final_response"]
    assert "[방문객 빅데이터 및 트래픽 분석]" not in result["final_response"]
    assert "[제주 로컬 문화 및 작물 지식 가이드]" not in result["final_response"]
    assert "[안전 탐방 및 준비물 관리 수칙]" not in result["final_response"]


