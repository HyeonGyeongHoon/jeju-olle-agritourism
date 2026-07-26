"""
scripts/backfill_eup_myeon_dong_areas.py
=============================================
courses.administrative_areas(법정리/법정동 단위, 쉼표 구분)를 읍/면/동 단위로 확정해
courses.eup_myeon_dong_areas 컬럼(supabase/schema.sql 섹션 10)에 백필합니다.

왜 필요한가:
    같은 법정리 이름이 지리적으로 전혀 다른 읍/면에 동시에 존재합니다(data/jeju_districts.csv
    기준 9건 — 예: "세화리"가 구좌읍(제주시)과 표선면(서귀포시)에 각각 존재). 이 때문에
    "읍/면 이름 -> 법정리 후보 역확장 -> administrative_areas 부분 문자열 검사" 방식은
    엉뚱한 지역의 코스를 매칭시켰습니다(라이브 재현: "구좌읍 감귤" 질의가 표선면/남원읍의
    4코스를 "세화리" 문자열 충돌로 구좌읍 코스로 오인).

해소 알고리즘(anchor-consistency, `resolve_eup_myeon_for_course`):
    같은 코스 안의 "모호하지 않은"(후보 읍/면이 정확히 1개인) 법정리 토큰들이 가리키는
    읍/면 집합(anchor_set)과, 모호한 토큰의 후보 집합의 교집합을 취합니다. 예:
      - 4코스(표선리,세화리,토산리): 표선리/토산리 -> {표선면}(anchor) => 세화리도 표선면 확정
      - 20코스(김녕리,행원리,평대리,세화리): 나머지 전부 {구좌읍} => 세화리도 구좌읍 확정
    교집합이 공집합이면 **추측하지 않습니다** — 후보를 그대로 전부 남기고(즉 기존 동작만큼
    관대한 상태를 유지) 그 토큰을 "미해소"로 보고해 사람이 검토하게 합니다. 예:
      - 14-1코스(저지리,서광리): 저지리 -> {한경면}(anchor) 인데 서광리 후보는 {우도면,안덕면}
        이라 교집합이 공집합 => 서광리는 미해소로 보고.

Gate B (주의):
    `--apply` 는 실 Supabase DB에 UPDATE 를 수행하는 비가역적 작업입니다. 사용자의 사전 승인
    없이 실행하지 마세요. 인자 없이 실행하면 읽기 전용 DRY RUN(계산 결과 표만 출력)입니다.
    실행 전 supabase/schema.sql 섹션 10 의 ALTER 문이 Supabase SQL 에디터에서 수동으로
    적용되어 있어야 합니다.

사용법:
    python scripts/backfill_eup_myeon_dong_areas.py           # DRY RUN (읽기 전용)
    python scripts/backfill_eup_myeon_dong_areas.py --apply   # 실제 UPDATE (Gate B)
"""

import argparse
import os
import sys
from typing import Any, Dict, List, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 법정리/법정동 -> 행정 읍/면/동 매핑은 nodes.py 가 data/jeju_districts.csv 에서 이미 로드해 둔
# 것을 그대로 재사용합니다(중복 구현 시 두 곳이 어긋날 위험이 있어 의도적으로 import 합니다).
from src.agent.nodes import _LEGAL_DONG_TO_ADMIN_DONG  # noqa: E402
from src.ingestion.database_loader import get_supabase_client  # noqa: E402

EUP_MYEON_COLUMN = "eup_myeon_dong_areas"


def _append_unique(target: List[str], values: List[str]) -> None:
    """등장 순서를 유지하면서 중복 없이 추가합니다."""
    for value in values:
        if value not in target:
            target.append(value)


def split_area_tokens(raw: str | None) -> List[str]:
    """쉼표 구분 문자열을 공백 제거된 토큰 리스트로 변환합니다."""
    return [token.strip() for token in (raw or "").split(",") if token.strip()]


def resolve_eup_myeon_for_course(
    tokens: List[str], legal_to_admin: Dict[str, List[str]]
) -> Tuple[List[str], List[str]]:
    """한 코스의 법정리/법정동 토큰 목록을 읍/면/동 단위로 해소합니다.

    반환값: (해소된 읍/면/동 이름 리스트, 미해소 토큰 리스트)

    1) 각 토큰의 후보를 legal_to_admin 으로 조회하고, 후보가 정확히 1개인 토큰들의 후보를
       모아 anchor_set(합집합)을 만듭니다.
    2) 후보가 2개 이상인(모호한) 토큰은 후보 ∩ anchor_set 을 채택합니다. 교집합이 공집합이면
       추측하지 않고 후보 전체를 남기고 미해소로 보고합니다(anchor 가 아예 없는 코스도
       자연히 이 경로를 타 전부 미해소가 됩니다).
    3) legal_to_admin 에 아예 없는 토큰(CSV 미수록 신규 법정리 등)은 상위 행정구역을 알 수
       없으므로 원본 이름을 그대로 남기고 미해소로 보고합니다. 검증된 읍/면 이름이 아니므로
       다른 토큰을 해소하는 anchor 로는 쓰지 않습니다(fail-closed).

    반환 리스트는 입력 토큰 순서를 따라 중복 없이 구성되어, 같은 입력에 항상 같은 결과를
    돌려줍니다(백필 결과의 재현성 확보).
    """
    cleaned = [token.strip() for token in tokens if token and token.strip()]

    candidates_by_token: Dict[str, List[str]] = {}
    anchor_set = set()
    for token in cleaned:
        candidates = list(dict.fromkeys(legal_to_admin.get(token, [])))
        candidates_by_token[token] = candidates
        if len(candidates) == 1:
            anchor_set.add(candidates[0])

    resolved: List[str] = []
    unresolved: List[str] = []
    for token in cleaned:
        candidates = candidates_by_token[token]
        if not candidates:
            _append_unique(resolved, [token])
            unresolved.append(token)
            continue
        if len(candidates) == 1:
            _append_unique(resolved, candidates)
            continue
        narrowed = [c for c in candidates if c in anchor_set]
        if narrowed:
            _append_unique(resolved, narrowed)
        else:
            _append_unique(resolved, candidates)
            unresolved.append(token)

    return resolved, list(dict.fromkeys(unresolved))


def _fetch_courses(client: Any) -> List[Dict[str, Any]]:
    res = (
        client.table("courses")
        .select("id,course_name,administrative_areas")
        .order("id")
        .execute()
    )
    return res.data or []


def _compute_rows(courses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    computed = []
    for course in courses:
        raw_areas = course.get("administrative_areas") or ""
        resolved, unresolved = resolve_eup_myeon_for_course(
            split_area_tokens(raw_areas), _LEGAL_DONG_TO_ADMIN_DONG
        )
        computed.append(
            {
                "id": course.get("id"),
                "course_name": course.get("course_name") or "",
                "administrative_areas": raw_areas,
                EUP_MYEON_COLUMN: ",".join(resolved),
                "unresolved": unresolved,
            }
        )
    return computed


def _print_dry_run_table(rows: List[Dict[str, Any]]) -> None:
    header = (
        "course_name",
        "administrative_areas (법정리)",
        f"{EUP_MYEON_COLUMN} (계산값)",
        "미해소 토큰",
    )
    widths = (12, 44, 34, 20)
    print("=" * 120)
    print(" | ".join(h.ljust(w) for h, w in zip(header, widths)))
    print("-" * 120)
    for row in rows:
        cells = (
            row["course_name"],
            row["administrative_areas"],
            row[EUP_MYEON_COLUMN],
            ",".join(row["unresolved"]) or "-",
        )
        print(" | ".join(str(c).ljust(w) for c, w in zip(cells, widths)))
    print("=" * 120)

    unresolved_rows = [r for r in rows if r["unresolved"]]
    print(f"\n[*] 코스 {len(rows)}건 계산 완료, 미해소 토큰이 있는 코스 {len(unresolved_rows)}건")
    for row in unresolved_rows:
        print(
            f"    - {row['course_name']}: 미해소 {row['unresolved']} "
            f"(후보를 좁히지 못해 후보 전체를 그대로 남겼습니다 — 사람이 검토해야 합니다)"
        )


def _apply_rows(client: Any, rows: List[Dict[str, Any]]) -> None:
    print("\n[*] --apply: courses 테이블 UPDATE 를 시작합니다 (Gate B 대상, 비가역적 쓰기 작업)")
    for row in rows:
        try:
            (
                client.table("courses")
                .update({EUP_MYEON_COLUMN: row[EUP_MYEON_COLUMN]})
                .eq("id", row["id"])
                .execute()
            )
            print(f"    [OK] {row['course_name']} <- '{row[EUP_MYEON_COLUMN]}'")
        except Exception as e:
            print(f"    [!] {row['course_name']} UPDATE 실패: {e}")
    print("\n[OK] eup_myeon_dong_areas 백필이 완료되었습니다!")


def run(apply: bool = False) -> None:
    client = get_supabase_client()
    try:
        courses = _fetch_courses(client)
    except Exception as e:
        print(f"[!] courses 조회 실패: {e}")
        return
    if not courses:
        print("[!] courses 테이블에 행이 없습니다.")
        return

    rows = _compute_rows(courses)
    _print_dry_run_table(rows)

    if not apply:
        print("\n[i] DRY RUN 이므로 DB에 아무것도 쓰지 않았습니다. 실제 반영은 --apply 로 실행하세요")
        print("    (Gate B — 사용자 승인 후에만).")
        return

    _apply_rows(client, rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="courses.eup_myeon_dong_areas 백필 (기본: 읽기 전용 DRY RUN)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="계산 결과를 실제로 courses 테이블에 UPDATE 합니다 (Gate B: 사용자 승인 필요).",
    )
    args = parser.parse_args()
    run(apply=args.apply)
