import threading
import time
from unittest.mock import patch

from src.agent import nodes
from src.agent.nodes import _build_price_breakdown_str, _estimate_price_range, generate_report_node


def _base_state(**overrides):
    state = {
        "query": "10월 감귤 테마로 구좌읍 코스 기획서 만들어줘",
        "retrieved_chunks": [
            {
                "course_name": "1코스",
                "total_distance_km": 15.0,
                "estimated_time_text": "5시간",
                "difficulty": "중",
                "crops": "감귤",
                "administrative_areas": "종달리",
                "content": "종달리를 지나는 해안 코스",
            }
        ],
        "culture_chunks": [],
        "sub_segments": [],
        "fallback_applied": False,
        "fallback_reason": None,
        "weather_info": {"description": "선선하고 쾌청한 가을", "warnings": []},
        "safety_check": {"reroute_required": False},
        "market_insight": None,
        "b2b_params": {"target_audience": "family", "include_market_insights": True, "target_month": 10},
    }
    state.update(overrides)
    return state


def test_generate_report_node_returns_early_apology_when_no_chunks():
    state = _base_state(retrieved_chunks=[])

    with patch.object(nodes, "get_chat_completion") as mock_llm:
        result = generate_report_node(state)

    mock_llm.assert_not_called()
    assert "찾을 수 없었습니다" in result["final_response"]
    assert result["docent_answer"] == result["final_response"]


def test_generate_report_node_surfaces_specific_hard_constraint_reason_instead_of_substituting():
    """사용자 요청: 지정한 코스가 하드 제약(휠체어 등)을 만족 못 하면 다른 코스로 조용히
    대체 추천하지 말고, 왜 기획서를 작성할 수 없는지 구체적인 이유만 답하고 종료해야 함.
    fallback_applied/fallback_reason 이 채워진 상태로 chunks=[] 가 들어오면 그 사유를 일반
    "찾을 수 없었습니다" 문구 대신 그대로 노출해야 함.
    (2026-07-25 무조건 반려 정책 전환 이후, 이 사유를 만들어내는 실제 경로는
    retrieve_rag_node → route_after_retriever → quick_responder_node 로 바뀌어 이 노드는 그
    경우 아예 실행되지 않습니다. 이 분기 자체는 chunks=[] 로 이 노드에 도달하는 다른 경로
    (course_ids 는 있었지만 벡터 검색이 0건)를 위한 안전망으로 남아 있어 계속 검증합니다.)"""
    state = _base_state(
        retrieved_chunks=[],
        fallback_applied=True,
        fallback_reason="'2코스'에는 휠체어로 이용 가능한 구간이 없습니다.",
    )

    with patch.object(nodes, "get_chat_completion") as mock_llm:
        result = generate_report_node(state)

    mock_llm.assert_not_called()
    assert "2코스" in result["final_response"]
    assert "휠체어" in result["final_response"]
    assert "찾을 수 없었습니다" not in result["final_response"]


def test_generate_report_node_produces_all_five_sections_in_one_call():
    """docent_generator(섹션 1·2)와 report_finalizer(섹션 3·4·5)가 하나로 통합됐으므로,
    generate_report_node 한 번 호출로 5개 섹션이 모두 포함된 완성된 기획서가 나와야 한다."""
    docent_llm_answer = "## 1. 📊 B2B 상품 개요 & 스펙\n...내용...\n\n## 2. 📍 [타임라인/동선 연계] 로컬 영농 & 문화 도슨트 포인트\n|표|표|표|표|"
    mock_recommendations = [
        {
            "crop_tag": "감귤",
            "title": "테스트 카페",
            "introduction": "감귤 디저트 전문 카페",
            "source": "mock_db",
        }
    ]

    with patch.object(nodes, "get_chat_completion", side_effect=[docent_llm_answer, "| 푸드/음료 | ... | ... | ... |"]) as mock_llm, \
         patch.object(nodes, "get_visit_jeju_recommendations", return_value=mock_recommendations):
        result = generate_report_node(_base_state())

    assert mock_llm.call_count == 2  # 섹션 1·2용 1회 + 섹션 3 아이디어용 1회
    report = result["final_response"]
    for header in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5."):
        assert header in report, f"{header} 섹션이 통합 리포트에 없습니다."
    assert result["recommendations"] == mock_recommendations
    assert result["docent_answer"] == docent_llm_answer


def test_generate_report_node_skips_section_3_ideas_when_no_local_recommendations():
    with patch.object(nodes, "get_chat_completion", return_value="## 1. ...\n## 2. ...") as mock_llm, \
         patch.object(nodes, "get_visit_jeju_recommendations", return_value=[]):
        result = generate_report_node(_base_state())

    mock_llm.assert_called_once()  # 소개 참고자료가 없으면 아이디어 LLM 호출 자체를 생략
    assert "아이디어 제안을 생략합니다" in result["final_response"]
    assert "## 5. 🛡️ Trust Tagging" in result["final_response"]


def test_generate_report_node_calls_visit_jeju_api_concurrently_not_sequentially():
    """회귀 방지: 예전엔 작물x지역 조합마다 비짓제주 API를 하나씩 순서대로 호출해서,
    조합 수만큼 지연이 그대로 누적됐습니다(조합 N개 x 개별 호출 시간). 이제는 스레드풀로
    동시에 조회하므로, 조합이 여러 개여도 총 소요 시간이 순차 합산보다 훨씬 짧아야 합니다."""
    call_log = []
    lock = threading.Lock()

    def slow_recommendation(crop, area):
        time.sleep(0.2)
        with lock:
            call_log.append((crop, area))
        return [{
            "crop_tag": crop, "title": f"{crop}-{area} 매장",
            "introduction": f"{crop} 전문점", "source": "mock_db",
        }]

    state = _base_state(retrieved_chunks=[
        {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
         "difficulty": "중", "crops": "감귤,당근", "administrative_areas": "종달리", "content": "..."},
        {"course_name": "2코스", "total_distance_km": 10.0, "estimated_time_text": "3시간",
         "difficulty": "하", "crops": "마늘", "administrative_areas": "신평리", "content": "..."},
    ])

    with patch.object(
        nodes, "get_chat_completion",
        side_effect=["## 1. ...\n## 2. ...", "| 푸드/음료 | ... | ... | ... |"],
    ), patch.object(nodes, "get_visit_jeju_recommendations", side_effect=slow_recommendation):
        start = time.monotonic()
        result = generate_report_node(state)
        elapsed = time.monotonic() - start

    # (감귤,종달리), (당근,종달리) - 1순위 코스(chunks[0])의 중복 없는 조합 2개만 호출되어야 함
    assert len(call_log) == 2
    # 순차 호출이었다면 2 x 0.2초 = 0.4초 이상 걸렸어야 하지만, 동시 호출이면 훨씬 짧아야 함
    assert elapsed < 0.35
    assert len(result["recommendations"]) == 2


def test_generate_report_node_prices_only_top_course_combos_not_other_chunks():
    """회귀 방지: 섹션 3(로컬 제휴 아이디어)이 1순위 코스(chunks[0])로만 제한된 뒤에도, 섹션 1의
    예상 1인 단가 산정에 쓰이는 조합 수가 여전히 검색된 모든 코스를 세면, 실제로 섹션 3에 나오지
    않는(2순위 코스의) 조합까지 가격에 반영되는 불일치가 생깁니다. 1순위 코스(crops=감귤,당근 x
    종달리 = 조합 2건)만 세야 하고, 2순위 코스(마늘 x 신평리)의 조합은 가격 산정에 포함되면
    안 됩니다."""
    state = _base_state(retrieved_chunks=[
        {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
         "difficulty": "중", "crops": "감귤,당근", "administrative_areas": "종달리", "content": "..."},
        {"course_name": "2코스", "total_distance_km": 10.0, "estimated_time_text": "3시간",
         "difficulty": "하", "crops": "마늘", "administrative_areas": "신평리", "content": "..."},
    ])
    expected_price = _estimate_price_range(state["retrieved_chunks"][0], "family", num_local_combos=2)

    with patch.object(
        nodes, "get_chat_completion",
        side_effect=["## 1. ...\n## 2. ...", "| 표 | ... | ... | ... |"],
    ) as mock_llm, patch.object(
        nodes, "get_visit_jeju_recommendations",
        return_value=[{"crop_tag": "x", "title": "t", "introduction": "i", "source": "mock_db"}],
    ):
        generate_report_node(state)

    first_call_system_prompt = mock_llm.call_args_list[0].args[0]
    assert expected_price in first_call_system_prompt


def test_estimate_price_range_uses_group_size_and_difficulty_and_addons():
    """가족(4인) 타겟, 5.5시간, 난이도 중, 로컬 제휴 조합 2건 기준 예상 범위를 검증합니다.
    group_cost = 10,000 + 5.5*15,000*1.15 = 104,875, addon = 2*3,000 = 6,000
    per_person = 104,875/4 + 6,000 = 32,218.75 -> low=29,000, high=37,000 (1,000원 반올림)."""
    course = {"estimated_time_hours": 5.5, "difficulty": "중"}
    result = _estimate_price_range(course, "family", num_local_combos=2)
    assert result == "29,000원 ~ 37,000원"


def test_estimate_price_range_varies_by_target_audience_group_size():
    """동일한 코스라도 target_audience 에 따른 가정 그룹 인원이 달라 1인 단가가 달라져야 함
    (그룹 비용을 인원수로 나누는 구조이므로, 그룹이 클수록 1인 단가는 낮아짐)."""
    course = {"estimated_time_hours": 5.5, "difficulty": "중"}
    family_price = _estimate_price_range(course, "family", num_local_combos=0)
    corporate_price = _estimate_price_range(course, "corporate", num_local_combos=0)
    assert family_price != corporate_price


def test_estimate_price_range_caps_local_addon_at_max_combos():
    """로컬 제휴 조합이 상한(3건)을 넘어도 4번째부터는 단가에 추가로 반영되지 않아야 함."""
    course = {"estimated_time_hours": 5.5, "difficulty": "중"}
    at_cap = _estimate_price_range(course, "family", num_local_combos=3)
    over_cap = _estimate_price_range(course, "family", num_local_combos=10)
    assert at_cap == over_cap


def test_estimate_price_range_falls_back_to_one_hour_when_hours_missing():
    """estimated_time_hours 결측(0/None)이어도 예외 없이 최소 1시간으로 간주해 계산해야 함
    (fail-soft — 가격 산정 자체를 포기하지 않음)."""
    course = {"estimated_time_hours": None, "difficulty": "중"}
    result = _estimate_price_range(course, "family", num_local_combos=0)
    assert "원 ~" in result


def test_build_price_breakdown_str_shows_each_calculation_step_readably():
    """단가 산정 근거가 해설비/그룹고정비/인원분담/로컬addon/최종범위 5줄로, 가독성 있게
    (들여쓰기 + 각 계산 단계의 숫자가 그대로) 노출되어야 합니다."""
    course = {"estimated_time_hours": 5.5, "difficulty": "중"}
    breakdown = _build_price_breakdown_str(course, "family", num_local_combos=2)
    lines = breakdown.split("\n")

    assert len(lines) == 5
    assert all(line.startswith("  -") for line in lines)
    assert "도슨트 해설비" in lines[0] and "94,875원" in lines[0]
    assert "그룹 고정비" in lines[1] and "104,875원" in lines[1] and "4인" in lines[1]
    assert "1인 분담액" in lines[2] and "26,219원" in lines[2]
    assert "로컬 체험 연계 2건" in lines[3] and "6,000원" in lines[3]
    assert "32,219원" in lines[4] and "29,000원 ~ 37,000원" in lines[4]


def test_generate_report_node_injects_computed_price_and_breakdown_into_prompt():
    """report_generator 가 "예상 1인 단가 범위"를 LLM에게 자유 추정시키지 않고, 결정론적으로
    계산한 최종 범위와 산정 근거(가독성 있는 하위 목록)를 프롬프트에 사실로 주입해 그대로
    인용/전재하도록 지시하는지 검증합니다."""
    docent_llm_answer = "## 1. ...\n## 2. ..."
    # _base_state()의 retrieved_chunks[0]에는 estimated_time_hours 키가 없어 1시간으로
    # 폴백되고(fail-soft), 작물 1종 x 행정구역 1곳 = 조합 1건입니다.
    price_course = {"estimated_time_hours": None, "difficulty": "중"}
    expected_price = _estimate_price_range(price_course, "family", num_local_combos=1)
    expected_breakdown = _build_price_breakdown_str(price_course, "family", num_local_combos=1)

    with patch.object(
        nodes, "get_chat_completion", side_effect=[docent_llm_answer, "| 표 | ... | ... | ... |"]
    ) as mock_llm, patch.object(
        nodes,
        "get_visit_jeju_recommendations",
        return_value=[{"crop_tag": "감귤", "title": "t", "introduction": "i", "source": "mock_db"}],
    ):
        generate_report_node(_base_state())

    first_call_system_prompt = mock_llm.call_args_list[0].args[0]
    assert expected_price in first_call_system_prompt
    assert expected_breakdown in first_call_system_prompt
    assert "직접 추정하지 말고" in first_call_system_prompt
    assert "그대로 옮겨 적으세요" in first_call_system_prompt


def test_generate_report_node_pins_crop_attribution_to_top_matched_course_only():
    """회귀 방지: 질의가 특정 작물을 지정하지 않으면(key_item_or_crop 없음), 예전엔 검색된
    여러 코스의 crops 가 어느 코스 소속인지 프롬프트에 구조적으로 고정되지 않아, LLM이 최상위
    매칭 코스(15-B코스, crops=마늘)를 서술하면서 다른 코스(14코스, crops=감귤,양배추)의 crops를
    섞어 쓰는 문제가 실사용 중 확인됨. 프롬프트(user_msg의 코스 컨텍스트)가 코스 1을 "최상위
    매칭 코스"로 명시하고, 코스 2 이후를 "참고용 코스(상품화 대상 아님)"로 구분하며, system_prompt
    에 다른 코스의 crops를 가져다 쓰지 말라는 절대 규칙이 포함되어야 함."""
    state = _base_state(
        query="올레 코스 기획서 만들어줘",
        retrieved_chunks=[
            {"course_name": "15-B코스", "total_distance_km": 12.0, "estimated_time_text": "4시간",
             "difficulty": "중", "crops": "마늘", "administrative_areas": "저지리", "content": "..."},
            {"course_name": "14코스", "total_distance_km": 19.0, "estimated_time_text": "6시간",
             "difficulty": "중", "crops": "감귤,양배추", "administrative_areas": "월령리", "content": "..."},
        ],
        b2b_params={"target_audience": "family", "include_market_insights": True},
    )

    with patch.object(
        nodes, "get_chat_completion", side_effect=["## 1. ...\n## 2. ...", "| 표 | ... | ... | ... |"]
    ) as mock_llm, patch.object(nodes, "get_visit_jeju_recommendations", return_value=[]):
        generate_report_node(state)

    system_prompt, user_msg = mock_llm.call_args_list[0].args

    # 코스 1(최상위 매칭)과 코스 2(참고용)의 역할이 컨텍스트에 명시적으로 구분되어야 함
    assert "[코스 1 - ★ 최상위 매칭 코스" in user_msg
    assert "[코스 2 - 참고용 코스" in user_msg
    # 각 코스의 crops 가 그 코스 이름과 문장 단위로 묶여 있어야 함 (뭉친 목록이 아니라)
    assert "15-B코스의 재배작물: 마늘" in user_msg
    assert "14코스의 재배작물: 감귤,양배추" in user_msg
    # system_prompt 의 절대 규칙에 다른 코스 crops 를 섞지 말라는 지시가 있어야 함
    assert "참고용 코스" in system_prompt
    assert "코스 1" in system_prompt


def test_generate_report_node_excludes_co_grown_crops_when_strict_single_crop():
    """strict_single_crop=True + key_item_or_crop="당근"(1순위 코스의 실제 재배작물 목록에
    포함)이면, 그 코스의 다른 공동 재배 작물(감자)에 대한 로컬 제휴 조회/추천과 단가 산정용
    조합 수 가산 요인에서 완전히 제외되어야 한다 (독점 작물 한정 옵션)."""
    call_log = []

    def recording_recommendation(crop, area):
        call_log.append((crop, area))
        return [{
            "crop_tag": crop, "title": f"{crop}-{area} 매장",
            "introduction": f"{crop} 전문점", "source": "mock_db",
        }]

    state = _base_state(
        query="당근만을 활용한 코스 기획서 써줘",
        retrieved_chunks=[
            {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
             "difficulty": "중", "crops": "당근,감자", "administrative_areas": "구좌읍", "content": "..."},
        ],
        b2b_params={
            "target_audience": "family", "include_market_insights": True,
            "key_item_or_crop": "당근", "strict_single_crop": True,
        },
    )
    # 당근 x 구좌읍 조합 1건만 반영되어야 하므로(감자 조합은 제외), 조합 수 1건 기준 가격.
    expected_price = _estimate_price_range(state["retrieved_chunks"][0], "family", num_local_combos=1)

    with patch.object(
        nodes, "get_chat_completion",
        side_effect=["## 1. ...\n## 2. ...", "| 표 | ... | ... | ... |"],
    ) as mock_llm, patch.object(
        nodes, "get_visit_jeju_recommendations", side_effect=recording_recommendation,
    ):
        result = generate_report_node(state)

    # 감자 조합에 대한 비짓제주 API 호출 자체가 발생하지 않아야 함
    assert call_log == [("당근", "구좌읍")]
    # 추천 결과에도 감자 관련 항목이 섞여 있으면 안 됨
    assert all(rec["crop_tag"] == "당근" for rec in result["recommendations"])
    # 단가 산정에 반영된 조합 수도 감자 조합을 제외한 1건 기준이어야 함
    first_call_system_prompt = mock_llm.call_args_list[0].args[0]
    assert expected_price in first_call_system_prompt
    # 섹션 1·2 작성 LLM에게도 "당근" 단일 작물 한정 절대 규칙이 전달되어야 함
    assert "당근" in first_call_system_prompt and "배타적으로 한정" in first_call_system_prompt


def test_generate_report_node_keeps_all_co_grown_crops_when_not_strict_single_crop():
    """회귀 방지: strict_single_crop=False(기본값)이면, 단순히 작물을 지정만 한 경우(예:
    "당근 코스 기획서 써줘")는 기존 동작대로 코스의 모든 고유 재배 작물(당근+감자)이 로컬 제휴
    추천/단가 가산에 그대로 반영되어야 한다 - 이 회귀는 절대 허용되지 않음(사용자 명시 확인)."""
    call_log = []

    def recording_recommendation(crop, area):
        call_log.append((crop, area))
        return [{
            "crop_tag": crop, "title": f"{crop}-{area} 매장",
            "introduction": f"{crop} 전문점", "source": "mock_db",
        }]

    state = _base_state(
        query="당근 코스 기획서 써줘",
        retrieved_chunks=[
            {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
             "difficulty": "중", "crops": "당근,감자", "administrative_areas": "구좌읍", "content": "..."},
        ],
        b2b_params={
            "target_audience": "family", "include_market_insights": True,
            "key_item_or_crop": "당근", "strict_single_crop": False,
        },
    )
    expected_price = _estimate_price_range(state["retrieved_chunks"][0], "family", num_local_combos=2)

    with patch.object(
        nodes, "get_chat_completion",
        side_effect=["## 1. ...\n## 2. ...", "| 표 | ... | ... | ... |"],
    ) as mock_llm, patch.object(
        nodes, "get_visit_jeju_recommendations", side_effect=recording_recommendation,
    ):
        result = generate_report_node(state)

    # 당근/감자 두 조합 모두 조회되어야 함 (기존 동작 유지)
    assert set(call_log) == {("당근", "구좌읍"), ("감자", "구좌읍")}
    assert {rec["crop_tag"] for rec in result["recommendations"]} == {"당근", "감자"}
    first_call_system_prompt = mock_llm.call_args_list[0].args[0]
    assert expected_price in first_call_system_prompt


def test_generate_report_node_does_not_apply_strict_single_crop_when_crop_not_in_course():
    """fail-soft: strict_single_crop=True 이지만 key_item_or_crop("한라봉")이 1순위 코스의 실제
    재배작물 목록(당근,감자)에 없는 경우, 존재하지 않는 작물로 강제 필터링해 combos 를 0건으로
    만들지 않고 기존 동작(모든 재배 작물 반영)을 그대로 유지해야 한다."""
    state = _base_state(
        query="오직 한라봉만을 활용한 코스 기획서 써줘",
        retrieved_chunks=[
            {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
             "difficulty": "중", "crops": "당근,감자", "administrative_areas": "구좌읍", "content": "..."},
        ],
        b2b_params={
            "target_audience": "family", "include_market_insights": True,
            "key_item_or_crop": "한라봉", "strict_single_crop": True,
        },
    )

    with patch.object(
        nodes, "get_chat_completion",
        side_effect=["## 1. ...\n## 2. ...", "| 표 | ... | ... | ... |"],
    ), patch.object(
        nodes, "get_visit_jeju_recommendations",
        return_value=[{"crop_tag": "x", "title": "t", "introduction": "i", "source": "mock_db"}],
    ):
        result = generate_report_node(state)

    # 감자/당근 조합 둘 다 그대로 반영되어 recommendations 가 2건이어야 함(0건으로 붕괴되지 않음)
    assert len(result["recommendations"]) == 2


def test_generate_report_node_deduplicates_visit_jeju_calls_across_chunks():
    """같은 (작물, 지역) 조합이 여러 코스에서 반복되면, 비짓제주 API는 조합당 딱 한 번만
    호출되어야 합니다(스레드풀로 바꾸면서도 기존 캐싱 동작이 깨지지 않았는지 확인).
    현재는 최상위 1순위 코스(chunks[0])에 대해서만 로컬 제휴 아이디어를 수집하므로
    assert 기댓값은 1건입니다."""
    call_count = {"n": 0}

    def counting_recommendation(crop, area):
        call_count["n"] += 1
        return [{"crop_tag": crop, "title": "매장", "introduction": "소개", "source": "mock_db"}]

    state = _base_state(retrieved_chunks=[
        {"course_name": "1코스", "total_distance_km": 15.0, "estimated_time_text": "5시간",
         "difficulty": "중", "crops": "감귤", "administrative_areas": "종달리", "content": "..."},
        {"course_name": "2코스", "total_distance_km": 10.0, "estimated_time_text": "3시간",
         "difficulty": "하", "crops": "감귤", "administrative_areas": "종달리", "content": "..."},
    ])

    with patch.object(nodes, "get_chat_completion", side_effect=["## 1. ...\n## 2. ...", "| 표 | ... | ... | ... |"]), \
         patch.object(nodes, "get_visit_jeju_recommendations", side_effect=counting_recommendation):
        result = generate_report_node(state)

    assert call_count["n"] == 1
    assert len(result["recommendations"]) == 1  # 1순위 코스에 캐시된 결과만 반영됨
