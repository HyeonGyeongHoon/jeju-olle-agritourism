"""예상 1인 단가 산정 / 유효 작물 결정 서비스 레이어.

`src/agent/nodes.py` 의 `generate_report_node` 본문과 섞여 있던 단가 산정
상수/헬퍼와, 그 단가 산정에 들어가는 작물×행정구역 조합 수를 결정하는
`_resolve_effective_crops` 를 분리한 모듈입니다(2026-07-26 리팩토링 —
`db_service.py` 분리의 후속, 산술 공식 변경 없는 순수 코드 이동).

이 모듈의 함수들은 LangGraph 상태(AgentState)도 DB/외부 API도 전혀 모릅니다.
인자로 받은 plain dict(`course`/`chunk` = courses 행,
`b2b_params` = B2BQueryParams dump)만 읽는 순수 함수라서, 목킹 없이 직접
호출해 검증할 수 있습니다(`tests/test_price_estimation_service.py`).

`_resolve_effective_crops` 는 이름상 가격과 무관해 보이지만, 실제로는 단가
산정의 입력인 조합 수(`_price_combo_set`)를 좌우하는 같은 계산의 앞단입니다
— `generate_report_node` 가 이 함수를 한 번 호출해
`_effective_top_course_crops` 로 캐싱한 뒤 (1) 단가 조합 수, (2) 섹션 3 로컬
제휴 조회/아이디어 프롬프트, (3) strict_single_crop 절대 규칙 판정 3곳에서
재사용하므로, 같은 모듈에 두어 "이 세 소비자가 같은 판단을 공유한다"는
제약을 한곳에서 읽을 수 있게 했습니다.

주의(테스트 목킹): `nodes.py` 는 이 모듈의 이름들을 `from ... import` 로 자기
네임스페이스에 바인딩해 쓰므로, 노드 동작을 목킹할 때는 기존과 동일하게
`patch.object(nodes, "_xxx")` 가 유효합니다. 반대로 이 모듈 안에서 서로를
호출하는 경로(`_estimate_price_range` / `_build_price_breakdown_str` ->
`_compute_price_breakdown`)를 가로채려면
`patch.object(price_estimation_service, "_compute_price_breakdown")` 로
패치해야 합니다.
"""

from typing import Any, Dict, List

# 예상 1인 단가 범위 산정 상수 — 코드베이스/DB 어디에도 실제 도슨트 투어 요금표가 없어 전부
# 가정값입니다(2026-07-25, 사용자 확인 완료). 시간당 해설비·고정비·난이도 배수는 "코스 1회 운용"
# 단위(그룹 전체) 비용이고, 로컬 체험 add-on 은 참가자 개인이 소비하는 항목이라 이미 1인 단가
# 성격이라는 점이 이 산식의 핵심 구분입니다 — 그룹 비용을 그대로 1인 단가로 부르면 과대 산정됩니다.
_PRICE_BASE_FLAT = 10_000
_PRICE_BASE_RATE_PER_HOUR = 15_000
_PRICE_DIFFICULTY_MULTIPLIER = {"하": 1.0, "중": 1.15, "상": 1.3}
_PRICE_ADDON_PER_COMBO = 3_000
_PRICE_ADDON_MAX_COMBOS = 3
_PRICE_GROUP_SIZE_BY_AUDIENCE = {
    "family": 4,
    "healing": 2,
    "active": 6,
    "senior": 10,
    "corporate": 15,
}
_PRICE_AUDIENCE_LABEL = {
    "family": "가족",
    "healing": "힐링",
    "active": "액티브",
    "senior": "시니어",
    "corporate": "기업",
}


def _compute_price_breakdown(
    course: Dict[str, Any], target_audience: str, num_local_combos: int
) -> Dict[str, Any]:
    """예상 1인 단가 산정의 모든 중간값을 계산합니다. `_estimate_price_range`(최종 범위 문자열)와
    `_build_price_breakdown_str`(가독성 있는 산정 근거 텍스트)이 같은 계산을 두 번 하지 않도록
    공유하는 내부 헬퍼입니다. estimated_time_hours 가 없거나 0 이하인 결측 데이터는 계산을
    포기하지 않고 difficulty 배수만으로 최소한의 해설비가 반영되도록 1시간으로 간주합니다(fail-soft).
    """
    hours = course.get("estimated_time_hours") or 1.0
    difficulty = course.get("difficulty") or "중"
    difficulty_mult = _PRICE_DIFFICULTY_MULTIPLIER.get(difficulty, _PRICE_DIFFICULTY_MULTIPLIER["중"])
    group_size = _PRICE_GROUP_SIZE_BY_AUDIENCE.get(target_audience, _PRICE_GROUP_SIZE_BY_AUDIENCE["family"])
    combos_used = min(num_local_combos, _PRICE_ADDON_MAX_COMBOS)

    guide_cost = hours * _PRICE_BASE_RATE_PER_HOUR * difficulty_mult
    group_cost = _PRICE_BASE_FLAT + guide_cost
    per_person_group_cost = group_cost / group_size
    addon_total = combos_used * _PRICE_ADDON_PER_COMBO
    per_person = per_person_group_cost + addon_total

    return {
        "hours": hours,
        "difficulty": difficulty,
        "difficulty_mult": difficulty_mult,
        "target_audience": target_audience,
        "group_size": group_size,
        "combos_used": combos_used,
        "guide_cost": guide_cost,
        "group_cost": group_cost,
        "per_person_group_cost": per_person_group_cost,
        "addon_total": addon_total,
        "per_person": per_person,
        "low": round(per_person * 0.9 / 1_000) * 1_000,
        "high": round(per_person * 1.15 / 1_000) * 1_000,
    }


def _estimate_price_range(course: Dict[str, Any], target_audience: str, num_local_combos: int) -> str:
    """대표 코스(course)의 실측 소요시간/난이도와 로컬 제휴 조합 수를 근거로 예상 1인 단가
    범위를 결정론적으로 계산합니다. 실제 요금 데이터가 없어 가정값 기반이지만, 매 실행마다
    LLM 이 임의로 지어내던 것과 달리 같은 조건이면 항상 같은 범위를 반환합니다.
    """
    b = _compute_price_breakdown(course, target_audience, num_local_combos)
    return f"{b['low']:,}원 ~ {b['high']:,}원"


def _build_price_breakdown_str(course: Dict[str, Any], target_audience: str, num_local_combos: int) -> str:
    """단가 산정 근거를 기획서 독자(지자체 담당자/여행사 기획자)가 바로 이해할 수 있도록,
    "시간×단가×난이도배수 → 그룹 비용 → 인원 분담 → 로컬 체험 add-on → 최종 범위" 순서의
    들여쓴 하위 목록 텍스트로 만듭니다. LLM 에게 이 근거를 설명하라고 맡기지 않고 그대로
    옮겨 적게 하는 것은, 계산식 자체는 매번 동일해야 하는 사실 데이터이기 때문입니다.
    """
    b = _compute_price_breakdown(course, target_audience, num_local_combos)
    audience_label = _PRICE_AUDIENCE_LABEL.get(target_audience, "가족")
    lines = [
        f"  - 도슨트 해설비: {b['hours']:g}시간 × {_PRICE_BASE_RATE_PER_HOUR:,}원 × "
        f"난이도 배수({b['difficulty']}) {b['difficulty_mult']:g} = {round(b['guide_cost']):,}원",
        f"  - 그룹 고정비: {_PRICE_BASE_FLAT:,}원 → 그룹 비용 합계 {round(b['group_cost']):,}원 "
        f"({audience_label} 단위 {b['group_size']}인 기준)",
        f"  - 1인 분담액: {round(b['group_cost']):,}원 ÷ {b['group_size']}인 = "
        f"{round(b['per_person_group_cost']):,}원",
        f"  - 로컬 체험 연계 {b['combos_used']}건 × {_PRICE_ADDON_PER_COMBO:,}원 = "
        f"{round(b['addon_total']):,}원",
        f"  - 1인 단가(중간값) {round(b['per_person']):,}원 → 최종 범위(±10~15%) "
        f"{b['low']:,}원 ~ {b['high']:,}원",
    ]
    return "\n".join(lines)


def _resolve_effective_crops(chunk: Dict[str, Any], b2b_params: Dict[str, Any]) -> List[str]:
    """1순위 코스(chunk)의 재배작물 목록 중, 로컬 제휴 추천/단가 산정에 실제로 반영할 작물
    목록을 결정합니다. 사용자가 "당근만"/"오직 마늘만"처럼 배타적으로 단일 작물을 지정한
    경우(`strict_single_crop=True`) 그 코스의 다른 공동 재배 작물(예: 감자, 무)을 완전히
    제외하고 타겟 작물 1종으로만 강제 제한합니다.

    단, `key_item_or_crop`이 이 코스의 실제 crops 목록에 없다면(사용자가 착각했거나, 이 코스에서
    실제로는 재배되지 않는 작물을 지정한 경우) 이 가드를 적용하지 않고 원래 crops 목록을 그대로
    반환합니다(fail-soft) — 존재하지 않는 작물로 강제 필터링하면 combos 가 0건이 되어 로컬 제휴
    아이디어/단가 산정 섹션 전체가 망가지므로, "겹치는 게 없으면 조건을 풀고 계속 진행"하는
    지역/작물 필터와 동일한 원칙을 적용합니다(하드 제약이 아니라 검색 범위를 좁히는 사용자
    선호 조건이므로 fail-closed 로 전체 리포트를 반려할 대상이 아님).
    """
    crops = [c.strip() for c in (chunk.get("crops") or "").split(",") if c.strip()]
    strict_single_crop = bool(b2b_params.get("strict_single_crop"))
    target_crop = (b2b_params.get("key_item_or_crop") or "").strip()
    if strict_single_crop and target_crop and target_crop in crops:
        return [target_crop]
    return crops
