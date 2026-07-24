from unittest.mock import MagicMock

from src.agent.nodes import _filter_course_ids_by_crop, _get_known_crop_tags


def _make_client(all_crops_rows, filtered_rows):
    """courses 테이블에 대한 두 가지 다른 select 호출(전체 crops 조회 / id+crops 조회)을
    select() 인자값 기준으로 독립적으로 목킹합니다."""
    courses_table = MagicMock()

    def select_side_effect(cols):
        mock_query = MagicMock()
        if cols == "crops":
            mock_query.execute.return_value.data = all_crops_rows
        else:
            mock_query.in_.return_value.execute.return_value.data = filtered_rows
        return mock_query

    courses_table.select.side_effect = select_side_effect

    client = MagicMock()
    client.table.return_value = courses_table
    return client


def test_get_known_crop_tags_splits_and_dedupes():
    client = MagicMock()
    client.table.return_value.select.return_value.execute.return_value.data = [
        {"crops": "감자,당근,무"},
        {"crops": "귤,감귤"},
        {"crops": ""},
        {"crops": "당근"},
    ]

    tags = _get_known_crop_tags(client)

    assert tags == {"감자", "당근", "무", "귤", "감귤"}


def test_filter_course_ids_by_crop_matches_known_crop():
    client = _make_client(
        all_crops_rows=[{"crops": "쪽파,양파"}, {"crops": "감귤"}],
        filtered_rows=[
            {"id": 1, "crops": "쪽파,양파"},
            {"id": 2, "crops": "감귤"},
        ],
    )

    result_ids, matched = _filter_course_ids_by_crop(client, [1, 2], "쪽파")

    assert matched is True
    assert result_ids == [1]


def test_filter_course_ids_by_crop_skips_theme_word_silently():
    """'밭담'처럼 courses.crops 에 등장하지 않는 비작물 테마어는 필터링을 건너뛰고 원래
    course_ids 를 그대로 반환해야 함(완화 각주가 뜨면 안 됨)."""
    client = _make_client(
        all_crops_rows=[{"crops": "쪽파,양파"}, {"crops": "감귤"}],
        filtered_rows=[],
    )

    result_ids, matched = _filter_course_ids_by_crop(client, [1, 2], "밭담")

    assert matched is True
    assert result_ids == [1, 2]


def test_filter_course_ids_by_crop_releases_when_known_crop_has_no_overlap():
    """실제 작물명인데 이 course_ids 안엔 겹치는 코스가 없으면 matched=False 로 완화 사유를
    남길 수 있게 함."""
    client = _make_client(
        all_crops_rows=[{"crops": "쪽파,양파"}, {"crops": "감귤"}],
        filtered_rows=[{"id": 2, "crops": "감귤"}],
    )

    result_ids, matched = _filter_course_ids_by_crop(client, [2], "쪽파")

    assert matched is False
    assert result_ids == [2]


def test_filter_course_ids_by_crop_short_circuits_without_crop_or_ids():
    client = MagicMock()

    assert _filter_course_ids_by_crop(client, [1, 2], "") == ([1, 2], True)
    assert _filter_course_ids_by_crop(client, [], "마늘") == ([], True)
    client.table.assert_not_called()


def test_filter_course_ids_by_crop_releases_on_query_exception():
    """'마늘'은 알려진 작물 태그라 필터링을 시도하지만, 실제 필터링 쿼리 자체가 실패하면
    작물 조건 없이(matched=True) 원래 course_ids 를 그대로 반환해야 함."""
    courses_table = MagicMock()

    def select_side_effect(cols):
        mock_query = MagicMock()
        if cols == "crops":
            mock_query.execute.return_value.data = [{"crops": "마늘,당근"}]
        else:
            mock_query.in_.return_value.execute.side_effect = Exception("relation does not exist")
        return mock_query

    courses_table.select.side_effect = select_side_effect
    client = MagicMock()
    client.table.return_value = courses_table

    result_ids, matched = _filter_course_ids_by_crop(client, [1, 2], "마늘")

    assert matched is True
    assert result_ids == [1, 2]
