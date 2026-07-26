"""`src/services/price_estimation_service.py` 단위 테스트.

이 모듈은 DB/외부 API/LLM 을 전혀 호출하지 않는 순수 함수 모음이라
목킹 없이 직접 호출해 검증합니다(2026-07-26, nodes.py 에서 분리하면서 추가).
기대값은 상수를 하드코딩하지 않고 모듈 상수로부터 산식을 그대로 재계산해
비교하므로, 요금 가정값(현재 전부 placeholder)이 나중에 조정되어도 이
테스트는 산식 자체의 회귀만 잡습니다.
"""

import pytest

from src.services.price_estimation_service import (
    _PRICE_ADDON_MAX_COMBOS,
    _PRICE_ADDON_PER_COMBO,
    _PRICE_BASE_FLAT,
    _PRICE_BASE_RATE_PER_HOUR,
    _PRICE_DIFFICULTY_MULTIPLIER,
    _PRICE_GROUP_SIZE_BY_AUDIENCE,
    _build_price_breakdown_str,
    _compute_price_breakdown,
    _estimate_price_range,
    _resolve_effective_crops,
)


def _course(hours=4.0, difficulty="중"):
    return {"estimated_time_hours": hours, "difficulty": difficulty}


def _expected_per_person(hours, difficulty, audience, num_combos):
    """테스트 쪽에서 산식을 독립적으로 재구현해 구현과 교차 검증합니다."""
    mult = _PRICE_DIFFICULTY_MULTIPLIER[difficulty]
    group_size = _PRICE_GROUP_SIZE_BY_AUDIENCE[audience]
    combos_used = min(num_combos, _PRICE_ADDON_MAX_COMBOS)
    group_cost = _PRICE_BASE_FLAT + hours * _PRICE_BASE_RATE_PER_HOUR * mult
    return group_cost / group_size + combos_used * _PRICE_ADDON_PER_COMBO


# --- 그룹 사이즈(target_audience)별 1인 분담 ---------------------------------


def test_compute_price_breakdown_divides_group_cost_by_audience_group_size():
    """도슨트 해설비는 그룹 단위 비용이므로 반드시 target_audience 별
    인원수로 나뉘어야 합니다.
    """
    for audience, group_size in _PRICE_GROUP_SIZE_BY_AUDIENCE.items():
        b = _compute_price_breakdown(_course(), audience, num_local_combos=0)
        assert b["group_size"] == group_size
        assert b["per_person_group_cost"] == b["group_cost"] / group_size
        assert b["per_person"] == _expected_per_person(4.0, "중", audience, 0)


def test_estimate_price_range_is_cheaper_for_larger_group_audiences():
    """같은 코스라도 인원이 많은 타겟(corporate 15인)은 1인 단가가 소수
    인원(healing 2인)보다 낮아야 합니다 — 그룹 고정비/해설비를 더 많은
    사람이 나눠 부담하기 때문입니다.
    """
    course = _course()
    ordered = ["healing", "family", "active", "senior", "corporate"]
    prices = {
        audience: _compute_price_breakdown(course, audience, 0)["per_person"]
        for audience in ordered
    }
    assert prices["healing"] > prices["corporate"]
    values = [prices[a] for a in ordered]
    assert values == sorted(values, reverse=True)


def test_compute_price_breakdown_falls_back_to_family_size_for_unknown_audience():
    """알 수 없는/미지정 target_audience 는 예외 대신 family(4인) 기준으로
    fail-soft 합니다.
    """
    b = _compute_price_breakdown(_course(), "unknown_audience", num_local_combos=0)
    assert b["group_size"] == _PRICE_GROUP_SIZE_BY_AUDIENCE["family"]


# --- 난이도 배수 -------------------------------------------------------------


def test_compute_price_breakdown_applies_difficulty_multiplier():
    """난이도 상/중/하 배수가 해설비에 그대로 곱해져야 합니다."""
    for difficulty, mult in _PRICE_DIFFICULTY_MULTIPLIER.items():
        course = _course(difficulty=difficulty)
        b = _compute_price_breakdown(course, "family", num_local_combos=0)
        assert b["difficulty"] == difficulty
        assert b["difficulty_mult"] == mult
        assert b["guide_cost"] == 4.0 * _PRICE_BASE_RATE_PER_HOUR * mult


def test_compute_price_breakdown_difficulty_order_is_monotonic():
    """난이도가 높을수록 1인 단가가 높아야 합니다(하 < 중 < 상)."""
    low = _compute_price_breakdown(_course(difficulty="하"), "family", 0)
    mid = _compute_price_breakdown(_course(difficulty="중"), "family", 0)
    high = _compute_price_breakdown(_course(difficulty="상"), "family", 0)
    assert low["per_person"] < mid["per_person"] < high["per_person"]


def test_compute_price_breakdown_falls_back_to_medium_difficulty_when_unknown():
    """difficulty 가 None 이거나 등급표에 없는 값이면 '중' 배수로 fail-soft 합니다."""
    expected = _PRICE_DIFFICULTY_MULTIPLIER["중"]
    no_key = _compute_price_breakdown({"estimated_time_hours": 4.0}, "family", 0)
    none_value = _compute_price_breakdown(_course(difficulty=None), "family", 0)
    unknown = _compute_price_breakdown(_course(difficulty="최상"), "family", 0)
    assert no_key["difficulty_mult"] == expected
    assert none_value["difficulty_mult"] == expected
    assert unknown["difficulty_mult"] == expected


# --- estimated_time_hours 결측 fail-soft (1시간 floor) ------------------------


def test_compute_price_breakdown_uses_one_hour_floor_when_hours_missing_or_zero():
    """회귀 방지: estimated_time_hours 는 나중에 chunks_data 에 추가된 컬럼이라
    결측/0 일 수 있는데, 예전엔 이 값으로 바로 곱셈을 하면 해설비가 0원이 되거나
    None 곱셈으로 터질 수 있었습니다. 계산을 포기하지 않고 1시간으로 간주해
    최소 해설비가 반영되어야 합니다(fail-soft).
    """
    mult = _PRICE_DIFFICULTY_MULTIPLIER["중"]
    expected_guide = 1.0 * _PRICE_BASE_RATE_PER_HOUR * mult
    for hours in (0, 0.0, None):
        b = _compute_price_breakdown(_course(hours=hours), "family", 0)
        assert b["hours"] == 1.0
        assert b["guide_cost"] == expected_guide


def test_compute_price_breakdown_uses_one_hour_floor_when_hours_key_absent():
    """estimated_time_hours 키 자체가 없는 코스 dict 도 예외 없이 1시간으로
    처리해야 합니다.
    """
    b = _compute_price_breakdown({"difficulty": "중"}, "family", num_local_combos=0)
    assert b["hours"] == 1.0


# --- 로컬 체험 add-on 캡 -----------------------------------------------------


def test_compute_price_breakdown_caps_local_addon_at_max_combos():
    """로컬 체험 add-on 은 조합 수가 아무리 많아도 _PRICE_ADDON_MAX_COMBOS
    건까지만 반영됩니다.
    """
    cap = _PRICE_ADDON_MAX_COMBOS
    at_cap = _compute_price_breakdown(_course(), "family", num_local_combos=cap)
    over_cap = _compute_price_breakdown(_course(), "family", num_local_combos=cap + 7)
    assert at_cap["combos_used"] == cap
    assert over_cap["combos_used"] == cap
    assert at_cap["addon_total"] == over_cap["addon_total"]
    assert at_cap["per_person"] == over_cap["per_person"]
    assert _estimate_price_range(_course(), "family", cap) == _estimate_price_range(
        _course(), "family", cap + 7
    )


def test_compute_price_breakdown_addon_is_per_person_not_divided_by_group():
    """로컬 체험비는 참가자 개인이 소비하는 항목이므로 그룹 인원으로 나누면
    안 됩니다 — 조합 1건 증가분이 1인 단가에 그대로 _PRICE_ADDON_PER_COMBO
    만큼 더해져야 합니다.
    """
    zero = _compute_price_breakdown(_course(), "corporate", 0)["per_person"]
    one = _compute_price_breakdown(_course(), "corporate", 1)["per_person"]
    # 부동소수점 나눗셈(그룹비 ÷ 15인)이 끼어 있어 == 비교는 표현오차로 실패
    assert one - zero == pytest.approx(_PRICE_ADDON_PER_COMBO)


# --- 최종 범위 문자열 / 산정 근거 텍스트 -------------------------------------


def test_estimate_price_range_returns_rounded_band_around_median():
    """최종 범위는 1인 단가 중간값의 -10%~+15% 를 1,000원 단위로 반올림한
    값이어야 합니다.
    """
    course = _course(hours=5.0, difficulty="상")
    b = _compute_price_breakdown(course, "senior", num_local_combos=2)
    expected_low = round(b["per_person"] * 0.9 / 1_000) * 1_000
    expected_high = round(b["per_person"] * 1.15 / 1_000) * 1_000
    expected = f"{expected_low:,}원 ~ {expected_high:,}원"
    assert _estimate_price_range(course, "senior", 2) == expected
    assert b["low"] < b["high"]


def test_estimate_price_range_is_deterministic_for_same_inputs():
    """같은 조건이면 항상 같은 범위 — LLM 이 매번 지어내던 값을 결정론적
    계산으로 대체한 이유입니다.
    """
    course = _course(hours=3.5, difficulty="하")
    first = _estimate_price_range(course, "active", 1)
    for _ in range(5):
        assert _estimate_price_range(course, "active", 1) == first


def test_build_price_breakdown_str_lists_every_calculation_step():
    """산정 근거 텍스트는 해설비/그룹 고정비/1인 분담액/로컬 체험/최종 범위
    5단계를 모두 노출해야 합니다(기획서 독자가 계산식을 그대로 검산할 수
    있어야 하므로).
    """
    course = _course(hours=4.0, difficulty="중")
    breakdown = _build_price_breakdown_str(course, "family", num_local_combos=2)
    lines = breakdown.split("\n")
    assert len(lines) == 5
    assert all(line.startswith("  - ") for line in lines)
    assert "도슨트 해설비" in lines[0]
    assert "그룹 고정비" in lines[1]
    assert "1인 분담액" in lines[2]
    assert "로컬 체험 연계" in lines[3]
    assert "최종 범위" in lines[4]
    assert _estimate_price_range(course, "family", 2) in lines[4]


def test_build_price_breakdown_str_uses_korean_audience_label_and_group_size():
    """그룹 단위 설명에는 영문 enum 값이 아니라 한글 라벨과 실제 인원수가
    나와야 합니다.
    """
    breakdown = _build_price_breakdown_str(_course(), "corporate", 0)
    assert "기업 단위 15인 기준" in breakdown
    assert "corporate" not in breakdown


def test_build_price_breakdown_str_falls_back_to_family_label_when_unknown():
    """알 수 없는 target_audience 도 예외 없이 '가족' 라벨로 fail-soft 합니다."""
    breakdown = _build_price_breakdown_str(_course(), "unknown_audience", 0)
    assert "가족 단위" in breakdown


# --- _resolve_effective_crops (strict_single_crop) ----------------------------


def test_resolve_effective_crops_returns_all_crops_when_not_strict():
    """배타 표현 없는 단순 작물 지정("당근 코스 기획서 써줘")은 코스의 전체
    재배작물을 그대로 노출해야 합니다 — 사용자가 확정한 제약이며, 더 좁게
    필터링하는 것은 버그입니다.
    """
    chunk = {"crops": "감자,당근,무"}
    params = {"strict_single_crop": False, "key_item_or_crop": "당근"}
    assert _resolve_effective_crops(chunk, params) == ["감자", "당근", "무"]


def test_resolve_effective_crops_narrows_to_one_crop_when_strict_and_present():
    """"당근만"/"오직 마늘만" 처럼 배타적 단일 작물 지정이고 그 작물이 실제
    crops 에 있으면, 공동 재배 작물을 완전히 제외하고 타겟 작물 1종만 남겨야
    합니다.
    """
    chunk = {"crops": "감자,당근,무"}
    params = {"strict_single_crop": True, "key_item_or_crop": "당근"}
    assert _resolve_effective_crops(chunk, params) == ["당근"]


def test_resolve_effective_crops_fails_soft_when_crop_not_grown_on_course():
    """회귀 방지: 지정 작물이 이 코스의 실제 crops 에 없으면(예: 녹차 코스에
    "오직 한라봉만") 가드를 적용하지 않고 전체 crops 를 유지해야 합니다
    (fail-soft). 강제 필터링하면 combos 가 0건이 되어 로컬 제휴/단가 산정
    섹션이 통째로 비어버립니다.
    """
    chunk = {"crops": "녹차,감귤"}
    params = {"strict_single_crop": True, "key_item_or_crop": "한라봉"}
    assert _resolve_effective_crops(chunk, params) == ["녹차", "감귤"]


def test_resolve_effective_crops_fails_soft_when_target_crop_missing_or_blank():
    """strict_single_crop 이 True 라도 key_item_or_crop 이 없거나 공백뿐이면
    전체 crops 를 유지합니다.
    """
    chunk = {"crops": "녹차,감귤"}
    for target in (None, "", "   "):
        params = {"strict_single_crop": True, "key_item_or_crop": target}
        assert _resolve_effective_crops(chunk, params) == ["녹차", "감귤"]


def test_resolve_effective_crops_trims_whitespace_and_skips_empty_tokens():
    """crops 는 정규화되지 않은 콤마 구분 문자열이라 공백/빈 토큰이 섞일 수
    있습니다.
    """
    chunk = {"crops": " 감귤 , , 당근 ,"}
    assert _resolve_effective_crops(chunk, {}) == ["감귤", "당근"]


def test_resolve_effective_crops_returns_empty_list_when_crops_absent():
    """crops 키가 없거나 빈 문자열이면 예외 대신 빈 목록을 반환합니다."""
    assert _resolve_effective_crops({}, {}) == []
    assert _resolve_effective_crops({"crops": ""}, {"strict_single_crop": True}) == []


def test_resolve_effective_crops_returns_empty_list_when_crops_is_none():
    """회귀 방지: courses.crops 가 SQL NULL 로 조회되면 값은 키 부재가 아니라
    None 입니다. `.get("crops", "")` 는 키가 있으면 그 None 값을 그대로
    돌려주므로 `.split(",")` 호출 시 AttributeError 가 났었습니다 — `or ""`
    로 None/누락/빈 문자열을 모두 동일하게 흡수해야 합니다.
    """
    assert _resolve_effective_crops({"crops": None}, {}) == []
    assert (
        _resolve_effective_crops({"crops": None}, {"strict_single_crop": True, "key_item_or_crop": "당근"})
        == []
    )


def test_resolve_effective_crops_matches_target_crop_exactly_not_by_substring():
    """부분 문자열 매칭으로 좁히면 안 됩니다 — "귤" 지정이 "감귤"을 단독
    선택하게 되면 사용자가 지정하지 않은 작물로 상품을 한정해버리므로,
    정확히 일치할 때만 좁히고 아니면 fail-soft 합니다.
    """
    chunk = {"crops": "감귤,당근"}
    params = {"strict_single_crop": True, "key_item_or_crop": "귤"}
    assert _resolve_effective_crops(chunk, params) == ["감귤", "당근"]
