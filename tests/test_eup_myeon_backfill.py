"""scripts/backfill_eup_myeon_dong_areas.py 의 순수 해소 함수 회귀 테스트.

배경(회귀 방지): courses.administrative_areas 는 법정리/법정동 단위인데, 같은 법정리 이름이
지리적으로 전혀 다른 읍/면에 동시에 존재합니다(예: "세화리"가 구좌읍과 표선면에 각각). 예전엔
읍/면 질의를 법정리 후보로 역확장해 부분 문자열로 찾았기 때문에 "구좌읍" 질의가 표선면 코스
(4코스: 표선리,세화리,토산리)를 잘못 매칭했습니다. 이 함수는 같은 코스 안의 모호하지 않은
토큰(anchor)들과의 교집합으로 그 모호성을 해소하고, 해소할 수 없으면 추측하지 않고 미해소로
보고합니다.
"""

from scripts.backfill_eup_myeon_dong_areas import (
    resolve_eup_myeon_for_course,
    split_area_tokens,
)
from src.services.db_service import _LEGAL_DONG_TO_ADMIN_DONG

# 실제 data/jeju_districts.csv 의 관련 부분만 뽑은 고정 매핑(테스트를 CSV 변경으로부터 격리).
_MAPPING = {
    "표선리": ["표선면"],
    "토산리": ["표선면"],
    "세화리": ["구좌읍", "표선면"],
    "김녕리": ["구좌읍"],
    "행원리": ["구좌읍"],
    "평대리": ["구좌읍"],
    "상도리": ["구좌읍"],
    "하도리": ["구좌읍"],
    "종달리": ["구좌읍"],
    "저지리": ["한경면"],
    "서광리": ["우도면", "안덕면"],
}


def test_resolve_sehwa_ri_to_pyoseon_myeon_for_course_4():
    """4코스(표선리,세화리,토산리): 표선리/토산리가 표선면 단독 소속(anchor)이므로 모호한
    세화리도 표선면으로 확정돼야 하고, 구좌읍은 결과에 들어가면 안 됨."""
    resolved, unresolved = resolve_eup_myeon_for_course(
        ["표선리", "세화리", "토산리"], _MAPPING
    )

    assert resolved == ["표선면"]
    assert unresolved == []
    assert "구좌읍" not in resolved


def test_resolve_sehwa_ri_to_gujwa_eup_for_courses_20_and_21():
    """20코스/21코스의 세화리는 같은 코스의 나머지 토큰이 전부 구좌읍 단독 소속이므로
    구좌읍으로 확정돼야 함(표선면이 섞여 들어오면 안 됨)."""
    resolved_20, unresolved_20 = resolve_eup_myeon_for_course(
        ["김녕리", "행원리", "평대리", "세화리"], _MAPPING
    )
    resolved_21, unresolved_21 = resolve_eup_myeon_for_course(
        ["세화리", "상도리", "하도리", "종달리"], _MAPPING
    )

    assert resolved_20 == ["구좌읍"]
    assert unresolved_20 == []
    assert resolved_21 == ["구좌읍"]
    assert unresolved_21 == []


def test_resolve_reports_unresolved_when_anchor_intersection_is_empty():
    """14-1코스(저지리,서광리): anchor 는 {한경면}인데 서광리 후보는 {우도면,안덕면}이라
    교집합이 공집합 — 추측하지 않고 후보 전체를 남기며 미해소로 보고해야 함."""
    resolved, unresolved = resolve_eup_myeon_for_course(["저지리", "서광리"], _MAPPING)

    assert unresolved == ["서광리"]
    assert resolved == ["한경면", "우도면", "안덕면"]


def test_resolve_reports_unresolved_when_no_anchor_exists():
    """모든 토큰이 모호한 코스는 아무것도 좁힐 수 없으므로 전부 미해소로 보고해야 함."""
    resolved, unresolved = resolve_eup_myeon_for_course(["세화리", "서광리"], _MAPPING)

    assert unresolved == ["세화리", "서광리"]
    assert resolved == ["구좌읍", "표선면", "우도면", "안덕면"]


def test_resolve_unmapped_token_is_reported_and_not_used_as_anchor():
    """CSV 매핑에 없는 토큰은 상위 행정구역을 알 수 없으므로 원본 이름을 그대로 남기고
    미해소로 보고하며, 다른 모호한 토큰을 해소하는 anchor 로는 쓰이지 않아야 함(fail-closed)."""
    resolved, unresolved = resolve_eup_myeon_for_course(["가상리", "세화리"], _MAPPING)

    assert unresolved == ["가상리", "세화리"]
    # "가상리"가 anchor 로 쓰였다면 세화리 후보가 잘못 좁혀졌을 것이므로 후보 둘 다 남아야 한다.
    assert resolved == ["가상리", "구좌읍", "표선면"]


def test_resolve_empty_tokens():
    assert resolve_eup_myeon_for_course([], _MAPPING) == ([], [])
    assert resolve_eup_myeon_for_course(["", "  "], _MAPPING) == ([], [])


def test_resolve_is_deterministic_and_deduplicated():
    """같은 읍/면을 가리키는 토큰이 여러 개여도 결과는 중복 없이, 입력 순서대로 나와야 함."""
    resolved, unresolved = resolve_eup_myeon_for_course(
        ["김녕리", "행원리", "김녕리"], _MAPPING
    )

    assert resolved == ["구좌읍"]
    assert unresolved == []


def test_split_area_tokens_strips_whitespace_and_empties():
    assert split_area_tokens("표선리, 세화리 ,토산리,") == ["표선리", "세화리", "토산리"]
    assert split_area_tokens(None) == []
    assert split_area_tokens("") == []


def test_real_csv_mapping_still_has_the_ambiguous_names_this_logic_exists_for():
    """실제 data/jeju_districts.csv 에서 세화리/서광리가 여전히 여러 읍/면에 걸쳐 있는지 확인.
    CSV가 나중에 정리돼 모호성이 사라지면 이 백필 로직의 전제가 바뀌므로 알려주는 감시 테스트."""
    assert len(_LEGAL_DONG_TO_ADMIN_DONG.get("세화리", [])) == 2
    assert set(_LEGAL_DONG_TO_ADMIN_DONG["세화리"]) == {"구좌읍", "표선면"}
    assert set(_LEGAL_DONG_TO_ADMIN_DONG.get("서광리", [])) == {"우도면", "안덕면"}
