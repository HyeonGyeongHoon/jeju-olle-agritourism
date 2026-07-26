from unittest.mock import MagicMock

from src.agent.nodes import _filter_course_ids_by_location


def _client_with_rows(rows):
    """courses select().in_().execute() 한 가지 패턴만 쓰는 _filter_course_ids_by_location 용
    가짜 클라이언트. select 컬럼 문자열과 무관하게 같은 행을 돌려줍니다."""
    client = MagicMock()
    execute = client.table.return_value.select.return_value.in_.return_value.execute
    execute.return_value.data = rows
    return client


def test_filter_course_ids_matches_admin_dong_via_eup_myeon_column():
    """preferred_location이 '안덕면'(읍/면/동 단위)이면, 백필된 eup_myeon_dong_areas 토큰과의
    완전 일치로 매칭돼야 함(courses.administrative_areas는 '화순리' 같은 법정리 단위라 이름이
    그대로 겹치지 않음)."""
    client = _client_with_rows(
        [
            {
                "id": 1,
                "administrative_areas": "화순리,사계리",
                "course_name": "9코스",
                "eup_myeon_dong_areas": "안덕면",
            },
            {
                "id": 2,
                "administrative_areas": "김녕리,종달리",
                "course_name": "20코스",
                "eup_myeon_dong_areas": "구좌읍",
            },
        ]
    )

    result_ids, matched = _filter_course_ids_by_location(client, [1, 2], "안덕면")

    assert matched is True
    assert result_ids == [1]


def test_filter_course_ids_does_not_match_other_eup_myeon_sharing_a_legal_ri_name():
    """회귀 방지: 예전엔 preferred_location('구좌읍')을 _ADMIN_DONG_TO_LEGAL_DONGS로 법정리
    후보로 역확장해 administrative_areas에서 부분 문자열로 찾았기 때문에, '세화리'가 구좌읍과
    표선면 양쪽에 존재한다는 이유로 표선면/남원읍 코스인 4코스(표선리,세화리,토산리)가 구좌읍
    코스로 잘못 편입됐다(라이브 재현 2026-07-25 "구좌읍 감귤": 4코스가 crops에 감귤을 가지고
    있어 작물 단계의 무조건 반려까지 우회하고 엉뚱한 지역 기획서가 생성됨). 이제 읍/면/동 질의는
    확정된 eup_myeon_dong_areas 토큰과만 완전 일치해야 한다."""
    client = _client_with_rows(
        [
            {
                "id": 4,
                "administrative_areas": "표선리,세화리,토산리",
                "course_name": "4코스",
                "eup_myeon_dong_areas": "표선면,남원읍",
            },
            {
                "id": 20,
                "administrative_areas": "김녕리,행원리,평대리,세화리",
                "course_name": "20코스",
                "eup_myeon_dong_areas": "구좌읍",
            },
        ]
    )

    result_ids, matched = _filter_course_ids_by_location(client, [4, 20], "구좌읍")

    assert matched is True
    assert result_ids == [20]


def test_filter_course_ids_rejects_all_when_only_colliding_course_exists():
    """같은 상황에서 진짜 구좌읍 코스가 후보에 없으면, '세화리' 충돌로 표선면 코스를 매칭시키는
    대신 matched=False를 돌려줘야 한다(호출부가 무조건 반려로 처리)."""
    client = _client_with_rows(
        [
            {
                "id": 4,
                "administrative_areas": "표선리,세화리,토산리",
                "course_name": "4코스",
                "eup_myeon_dong_areas": "표선면,남원읍",
            },
        ]
    )

    result_ids, matched = _filter_course_ids_by_location(client, [4], "구좌읍")

    assert matched is False
    assert result_ids == [4]


def test_filter_course_ids_matches_legal_ri_named_directly():
    """유지해야 할 기존 동작: 사용자가 법정리 이름 자체를 직접 지목한 경우('세화리')는
    administrative_areas 토큰과의 완전 일치로 계속 매칭돼야 한다(이 경우엔 이름만으로 읍/면을
    구분할 수 없으므로 양쪽 코스 모두 후보로 남는 것이 맞다)."""
    client = _client_with_rows(
        [
            {
                "id": 4,
                "administrative_areas": "표선리,세화리,토산리",
                "course_name": "4코스",
                "eup_myeon_dong_areas": "표선면,남원읍",
            },
            {
                "id": 20,
                "administrative_areas": "김녕리,행원리,평대리,세화리",
                "course_name": "20코스",
                "eup_myeon_dong_areas": "구좌읍",
            },
            {
                "id": 9,
                "administrative_areas": "화순리,사계리",
                "course_name": "9코스",
                "eup_myeon_dong_areas": "안덕면",
            },
        ]
    )

    result_ids, matched = _filter_course_ids_by_location(client, [4, 20, 9], "세화리")

    assert matched is True
    assert result_ids == [4, 20]


def test_filter_course_ids_matches_eup_myeon_name_without_suffix():
    """유지해야 할 기존 동작(부분 일치의 관대함): 접미사를 생략한 '한림'도 '한림읍' 토큰과
    매칭돼야 함. 예전 부분 문자열 매칭이 사실상 이 정도는 허용했고, 무조건 반려 정책에서는
    표기 차이가 곧 반려로 이어지므로 이 경로는 남긴다. 단 접미사를 떼도 다른 읍/면과 충돌하지
    않아야 하며('구좌'가 '표선면'을 매칭시키면 안 됨) 그 점을 함께 검증한다."""
    client = _client_with_rows(
        [
            {
                "id": 15,
                "administrative_areas": "한림리,대림리,한동리",
                "course_name": "15-A코스",
                "eup_myeon_dong_areas": "한림읍,구좌읍",
            },
            {
                "id": 4,
                "administrative_areas": "표선리,세화리,토산리",
                "course_name": "4코스",
                "eup_myeon_dong_areas": "표선면,남원읍",
            },
        ]
    )

    assert _filter_course_ids_by_location(client, [15, 4], "한림") == ([15], True)
    assert _filter_course_ids_by_location(client, [15, 4], "구좌") == ([15], True)

    # 표선면 코스만 후보에 있을 때 '구좌'는 아무것도 매칭시키지 못해야 한다(접미사 생략 표기가
    # 세화리류 동명이인 충돌을 되살리지 않는지 확인).
    pyoseon_only = _client_with_rows(
        [
            {
                "id": 4,
                "administrative_areas": "표선리,세화리,토산리",
                "course_name": "4코스",
                "eup_myeon_dong_areas": "표선면,남원읍",
            },
        ]
    )
    assert _filter_course_ids_by_location(pyoseon_only, [4], "구좌") == ([4], False)


def test_filter_course_ids_matches_literal_admin_dong_name():
    """preferred_location이 courses.administrative_areas에 그대로 등장하는 경우(예: '외도동')도
    정상 매칭돼야 함."""
    client = _client_with_rows(
        [
            {
                "id": 5,
                "administrative_areas": "외도동,이호동,도두동,용담동",
                "course_name": "17코스",
                "eup_myeon_dong_areas": "외도동,이호동,도두동,용담1동,용담2동",
            },
        ]
    )

    result_ids, matched = _filter_course_ids_by_location(client, [5], "외도동")

    assert matched is True
    assert result_ids == [5]


def test_filter_course_ids_matches_course_name():
    """유지해야 할 기존 동작: preferred_location이 course_name에 등장하면 매칭한다(지역 필드에
    코스명이 새어 들어온 경우는 상류 _looks_like_course_name 가드가 먼저 걸러내지만, 이 계층
    자체는 그대로 남겨 둔다)."""
    client = _client_with_rows(
        [
            {
                "id": 7,
                "administrative_areas": "월평동,호근동",
                "course_name": "7코스",
                "eup_myeon_dong_areas": "대륜동,대천동",
            },
        ]
    )

    result_ids, matched = _filter_course_ids_by_location(client, [7], "7코스")

    assert matched is True
    assert result_ids == [7]


def test_filter_course_ids_falls_back_to_legal_expansion_for_unbackfilled_rows():
    """eup_myeon_dong_areas가 아직 비어 있는(백필 전/신규) 행에 한해서는 예전 법정리 역확장
    부분 일치로 폴백해야 한다 — 컬럼만 추가하고 백필을 미룬 과도기에 지역 질의가 통째로
    반려되지 않게 하는 방어적 경로."""
    client = _client_with_rows(
        [
            {
                "id": 1,
                "administrative_areas": "화순리,사계리",
                "course_name": "9코스",
                "eup_myeon_dong_areas": None,
            },
            {
                "id": 2,
                "administrative_areas": "김녕리,종달리",
                "course_name": "20코스",
                "eup_myeon_dong_areas": "",
            },
        ]
    )

    result_ids, matched = _filter_course_ids_by_location(client, [1, 2], "안덕면")

    assert matched is True
    assert result_ids == [1]


def test_filter_course_ids_falls_back_when_column_missing_from_schema():
    """ALTER가 아직 실행되지 않아 eup_myeon_dong_areas 컬럼 조회가 실패하면, 그 컬럼을 뺀
    레거시 select로 재시도해 예전 매칭 방식으로 강등돼야 한다(조회 실패 = 0건 = 무조건 반려로
    모든 지역 질의가 죽는 것을 방지)."""
    client = MagicMock()
    legacy_rows = [
        {"id": 1, "administrative_areas": "화순리,사계리", "course_name": "9코스"},
        {"id": 2, "administrative_areas": "김녕리,종달리", "course_name": "20코스"},
    ]

    def _select(cols):
        table = MagicMock()
        if "eup_myeon_dong_areas" in cols:
            table.in_.return_value.execute.side_effect = Exception(
                'column courses.eup_myeon_dong_areas does not exist'
            )
        else:
            table.in_.return_value.execute.return_value.data = legacy_rows
        return table

    client.table.return_value.select.side_effect = _select

    result_ids, matched = _filter_course_ids_by_location(client, [1, 2], "안덕면")

    assert matched is True
    assert result_ids == [1]


def test_filter_course_ids_releases_filter_when_no_course_overlaps():
    """겹치는 코스가 하나도 없으면 원래 course_ids를 그대로 반환하고 matched=False를 반환해,
    호출부가 지역 조건 불일치를 처리(현재 정책: 무조건 반려)할 수 있게 함."""
    client = _client_with_rows(
        [
            {
                "id": 1,
                "administrative_areas": "김녕리,종달리",
                "course_name": "20코스",
                "eup_myeon_dong_areas": "구좌읍",
            },
        ]
    )

    result_ids, matched = _filter_course_ids_by_location(client, [1], "노형동")

    assert matched is False
    assert result_ids == [1]


def test_filter_course_ids_short_circuits_without_location_or_ids():
    client = MagicMock()

    assert _filter_course_ids_by_location(client, [1, 2], "") == ([1, 2], False)
    assert _filter_course_ids_by_location(client, [], "구좌읍") == ([], False)
    client.table.assert_not_called()


def test_filter_course_ids_releases_filter_on_query_exception():
    """두 번의 조회(신규 컬럼 포함 / 레거시)가 모두 실패하면 지역 조건 없이 진행한다."""
    client = MagicMock()
    execute = client.table.return_value.select.return_value.in_.return_value.execute
    execute.side_effect = Exception("relation does not exist")

    result_ids, matched = _filter_course_ids_by_location(client, [1, 2], "구좌읍")

    assert matched is False
    assert result_ids == [1, 2]
