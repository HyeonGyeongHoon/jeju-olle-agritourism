from unittest.mock import MagicMock

from src.agent.nodes import _describe_target_course_mismatch, _filter_course_ids_by_target_course


def test_filter_course_ids_matches_literal_course_name():
    client = MagicMock()
    client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"id": 1, "administrative_areas": "성산읍", "course_name": "1코스"},
        {"id": 2, "administrative_areas": "구좌읍", "course_name": "20코스"},
    ]

    result_ids, matched = _filter_course_ids_by_target_course(client, [1, 2], "1코스")

    assert matched is True
    assert result_ids == [1]


def test_filter_course_ids_does_not_false_positive_match_hyphenated_course_names():
    """회귀 방지: target_course('1코스')가 부분 문자열 포함 검사였을 때 "10-1코스"/"7-1코스"/
    "14-1코스"/"18-1코스"처럼 뒤에 "1코스"로 끝나는 전혀 다른 코스와 잘못 매칭됐습니다(실 서버
    QA 테스트 중 "1코스"를 요청했는데 "10-1코스"(가파도)가 선택되는 것으로 재현). course_name은
    이제 완전 일치만 인정해야 하므로, "10-1코스"는 매칭되면 안 되고 "1코스" 자신만 매칭돼야 함."""
    client = MagicMock()
    client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"id": 1, "administrative_areas": "종달리", "course_name": "1코스"},
        {"id": 2, "administrative_areas": "가파리", "course_name": "10-1코스"},
    ]

    result_ids, matched = _filter_course_ids_by_target_course(client, [1, 2], "1코스")

    assert matched is True
    assert result_ids == [1]


def test_filter_course_ids_matches_via_administrative_areas():
    """target_course 가 정식 course_name 과 다르더라도(예: 섬 이름), administrative_areas 에
    등장하면 매칭돼야 함."""
    client = MagicMock()
    client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"id": 3, "administrative_areas": "가파리", "course_name": "10-1코스"},
    ]

    result_ids, matched = _filter_course_ids_by_target_course(client, [3], "가파리")

    assert matched is True
    assert result_ids == [3]


def test_filter_course_ids_releases_filter_when_no_course_overlaps():
    """회귀 방지: target_course('가파도 코스')가 실제 course_name('10-1코스')이나
    administrative_areas('가파리')와 문자열이 전혀 안 겹치는 경우, 예전엔 이 조건이
    _execute_rdb_filtering 안에서 완전 일치(.eq())로 하드 필터링되어 후보가 0개가 되고 그 뒤
    벡터 검색조차 시도되지 않은 채 곧바로 "코스를 찾을 수 없다"는 완전 폴백으로 빠졌습니다
    (2026-07-24 QA 시나리오 테스트에서 실제 재현). 이제는 겹치는 코스가 없으면 원래 course_ids
    를 그대로 반환하고 matched=False 를 반환해, 호출부가 조건을 해제하고 계속 진행할 수 있게
    합니다."""
    client = MagicMock()
    client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"id": 3, "administrative_areas": "가파리", "course_name": "10-1코스"},
    ]

    result_ids, matched = _filter_course_ids_by_target_course(client, [3], "가파도 코스")

    assert matched is False
    assert result_ids == [3]


def test_filter_course_ids_short_circuits_without_target_course_or_ids():
    client = MagicMock()

    assert _filter_course_ids_by_target_course(client, [1, 2], "") == ([1, 2], False)
    assert _filter_course_ids_by_target_course(client, [], "1코스") == ([], False)
    client.table.assert_not_called()


def test_filter_course_ids_releases_filter_on_query_exception():
    client = MagicMock()
    client.table.return_value.select.return_value.in_.return_value.execute.side_effect = Exception(
        "relation does not exist"
    )

    result_ids, matched = _filter_course_ids_by_target_course(client, [1, 2], "1코스")

    assert matched is False
    assert result_ids == [1, 2]


# --- _describe_target_course_mismatch ---

def test_describe_target_course_mismatch_names_the_missing_wheelchair_segment():
    """사용자 요청: "휠체어가 필요한 2코스"처럼 실존하는 코스를 지정했는데 그 코스에 휠체어
    구간이 없으면, "코스를 못 찾았다"는 일반 문구 대신 정확히 "2코스에는 휠체어 구간이
    없다"는 사실을 밝혀야 함."""
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"course_name": "2코스", "has_wheelchair_segment": "없음"},
    ]

    reason = _describe_target_course_mismatch(client, "2코스", {"wheelchair_required": True})

    assert reason is not None
    assert "2코스" in reason
    assert "휠체어" in reason


def test_describe_target_course_mismatch_returns_none_when_course_has_wheelchair_access():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"course_name": "1코스", "has_wheelchair_segment": "있음"},
    ]

    assert _describe_target_course_mismatch(client, "1코스", {"wheelchair_required": True}) is None


def test_describe_target_course_mismatch_returns_none_when_course_does_not_exist():
    """target_course 자체가 DB에 없는 코스명이면(예: "가파도") 구체적 사유를 알아낼 수 없으므로
    None을 반환해 호출부가 일반 폴백 문구를 쓰게 함."""
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

    assert _describe_target_course_mismatch(client, "가파도 코스", {"wheelchair_required": True}) is None


def test_describe_target_course_mismatch_returns_none_when_wheelchair_not_required():
    client = MagicMock()

    assert _describe_target_course_mismatch(client, "2코스", {"wheelchair_required": False}) is None
    client.table.assert_not_called()


def test_describe_target_course_mismatch_returns_none_on_query_exception():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.side_effect = Exception(
        "relation does not exist"
    )

    assert _describe_target_course_mismatch(client, "2코스", {"wheelchair_required": True}) is None
