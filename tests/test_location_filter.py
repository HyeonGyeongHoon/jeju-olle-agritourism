from unittest.mock import MagicMock

from src.agent.nodes import _filter_course_ids_by_location


def test_filter_course_ids_matches_admin_dong_via_legal_dong_expansion():
    """preferred_location이 '안덕면'(행정동/읍/면 단위)이어도, courses.administrative_areas가
    '화순리'(법정리 단위)로만 저장돼 있으면 _ADMIN_DONG_TO_LEGAL_DONGS 확장을 통해 매칭돼야 함."""
    client = MagicMock()
    client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"id": 1, "administrative_areas": "화순리,사계리", "course_name": "9코스"},
        {"id": 2, "administrative_areas": "김녕리,종달리", "course_name": "20코스"},
    ]

    result_ids, matched = _filter_course_ids_by_location(client, [1, 2], "안덕면")

    assert matched is True
    assert result_ids == [1]


def test_filter_course_ids_matches_literal_admin_dong_name():
    """preferred_location이 courses.administrative_areas에 그대로 등장하는 경우(예: '외도동')도
    정상 매칭돼야 함."""
    client = MagicMock()
    client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"id": 5, "administrative_areas": "외도동,이호동,도두동,용담동", "course_name": "17코스"},
    ]

    result_ids, matched = _filter_course_ids_by_location(client, [5], "외도동")

    assert matched is True
    assert result_ids == [5]


def test_filter_course_ids_releases_filter_when_no_course_overlaps():
    """겹치는 코스가 하나도 없으면 원래 course_ids를 그대로 반환하고 matched=False를 반환해,
    호출부가 지역 조건을 해제했다는 사유를 남길 수 있게 함."""
    client = MagicMock()
    client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"id": 1, "administrative_areas": "김녕리,종달리", "course_name": "20코스"},
    ]

    result_ids, matched = _filter_course_ids_by_location(client, [1], "노형동")

    assert matched is False
    assert result_ids == [1]


def test_filter_course_ids_short_circuits_without_location_or_ids():
    client = MagicMock()

    assert _filter_course_ids_by_location(client, [1, 2], "") == ([1, 2], False)
    assert _filter_course_ids_by_location(client, [], "구좌읍") == ([], False)
    client.table.assert_not_called()


def test_filter_course_ids_releases_filter_on_query_exception():
    client = MagicMock()
    client.table.return_value.select.return_value.in_.return_value.execute.side_effect = Exception(
        "relation does not exist"
    )

    result_ids, matched = _filter_course_ids_by_location(client, [1, 2], "구좌읍")

    assert matched is False
    assert result_ids == [1, 2]
