from unittest.mock import MagicMock, patch

from src.agent.nodes import resolve_market_location_node


def _make_client(courses_rows=None, visitor_rows=None, visitor_error=None):
    """courses / visitor_analytics 두 테이블 호출을 독립적으로 목킹한 Supabase 클라이언트를
    만듭니다. courses_rows 에 실제 코스 데이터를 채워야만(올레 코스 경유 행정동이 확인되어야만)
    resolve_market_location_node 가 visitor_analytics 조회 단계까지 진행합니다 — courses_rows
    가 비어있거나 courses 조회 자체가 실패하면 fail-closed 로 조기 반환하므로 visitor_table 은
    아예 호출되지 않습니다."""
    courses_table = MagicMock()
    courses_table.select.return_value.execute.return_value.data = courses_rows or []

    visitor_table = MagicMock()
    final_step = (
        visitor_table.select.return_value
        .eq.return_value
        .not_.is_.return_value
        .in_.return_value
    )
    if visitor_error is not None:
        final_step.order.return_value.limit.return_value.execute.side_effect = visitor_error
    else:
        final_step.order.return_value.limit.return_value.execute.return_value.data = (
            visitor_rows or []
        )

    client = MagicMock()
    client.table.side_effect = lambda name: {
        "courses": courses_table,
        "visitor_analytics": visitor_table,
    }[name]
    return client, courses_table, visitor_table


def test_resolve_market_location_short_circuits_without_metric():
    state = {"b2b_params": {"market_location_query": {"metric": None}}}

    with patch("src.agent.nodes.get_supabase_client") as mock_get_client:
        assert resolve_market_location_node(state) == {}
        mock_get_client.assert_not_called()


def test_resolve_market_location_short_circuits_when_location_already_given():
    state = {
        "b2b_params": {
            "preferred_location": "구좌읍",
            "market_location_query": {"metric": "foreign_visitors"},
        }
    }

    with patch("src.agent.nodes.get_supabase_client") as mock_get_client:
        assert resolve_market_location_node(state) == {}
        mock_get_client.assert_not_called()


def test_resolve_market_location_rejects_unknown_metric():
    state = {"b2b_params": {"market_location_query": {"metric": "not_a_real_column"}}}

    with patch("src.agent.nodes.get_supabase_client") as mock_get_client:
        assert resolve_market_location_node(state) == {}
        mock_get_client.assert_not_called()


def test_resolve_market_location_success_filters_to_olle_dongs_and_sets_preferred_location():
    state = {
        "b2b_params": {
            "target_month": None,
            "market_location_query": {
                "metric": "foreign_visitors",
                "year": 2026,
                "month": 5,
                "direction": "desc",
            },
        }
    }
    # 코스가 '김녕리'(구좌읍 소속)를 관할구역으로 갖고 있다고 가정 -> 후보는 구좌읍으로 좁혀져야 함
    client, courses_table, visitor_table = _make_client(
        courses_rows=[{"administrative_areas": "김녕리,종달리"}],
        visitor_rows=[{"region_dong": "구좌읍", "foreign_visitors": 12000}],
    )

    with patch("src.agent.nodes.get_supabase_client", return_value=client):
        result = resolve_market_location_node(state)

    assert result["b2b_params"]["preferred_location"] == "구좌읍"
    assert result["b2b_params"]["market_location_resolution"] == {
        "region_dong": "구좌읍",
        "metric": "foreign_visitors",
        "value": 12000,
        "year_month": "2026-05",
        "direction": "desc",
    }
    visitor_table.select.assert_called_with("region_dong,foreign_visitors")
    visitor_table.select.return_value.eq.assert_called_with("year_month", "2026-05")
    in_call_args = (
        visitor_table.select.return_value.eq.return_value.not_.is_.return_value.in_.call_args
    )
    assert in_call_args.args[0] == "region_dong"
    assert set(in_call_args.args[1]) == {"구좌읍"}


def test_resolve_market_location_fails_closed_when_courses_lookup_fails():
    """courses 테이블 조회 자체가 실패하면(올레 코스 경유 행정동을 확인할 수 없음), 코스와 무관한
    지역이 통계 1위라는 이유만으로 선정되지 않도록 통계 기반 지역 자동 선정을 통째로 건너뛰어야
    합니다(fail-closed). 과거에는 이 경우 무제한 검색으로 느슨하게 폴백해 코스가 하나도 없는
    '연동' 같은 지역이 선정될 수 있었는데, 이는 도메인 규칙 위반이라 고쳐졌습니다."""
    state = {
        "b2b_params": {
            "market_location_query": {
                "metric": "foreign_visitors",
                "year": 2026,
                "month": 5,
                "direction": "desc",
            }
        }
    }
    client, courses_table, visitor_table = _make_client(
        visitor_rows=[{"region_dong": "연동", "foreign_visitors": 45823}],
    )
    courses_table.select.side_effect = Exception("relation \"courses\" does not exist")

    with patch("src.agent.nodes.get_supabase_client", return_value=client):
        result = resolve_market_location_node(state)

    assert result == {}
    visitor_table.select.assert_not_called()


def test_resolve_market_location_fails_closed_when_no_olle_courses_exist():
    """courses 테이블 조회는 성공했지만 등록된 코스가 하나도 없어(올레 코스 경유 행정동 후보가
    비어있음) 후보를 전혀 확인할 수 없는 경우에도, 무제한 검색으로 폴백하지 않고 통계 기반 지역
    자동 선정을 건너뛰어야 합니다."""
    state = {
        "b2b_params": {
            "market_location_query": {
                "metric": "foreign_visitors",
                "year": 2026,
                "month": 5,
                "direction": "desc",
            }
        }
    }
    client, courses_table, visitor_table = _make_client(
        courses_rows=[],
        visitor_rows=[{"region_dong": "연동", "foreign_visitors": 45823}],
    )

    with patch("src.agent.nodes.get_supabase_client", return_value=client):
        result = resolve_market_location_node(state)

    assert result == {}
    visitor_table.select.assert_not_called()


def test_resolve_market_location_returns_empty_on_no_data():
    state = {
        "b2b_params": {
            "market_location_query": {
                "metric": "foreign_visitors",
                "year": 2026,
                "month": 5,
                "direction": "desc",
            }
        }
    }
    client, _, _ = _make_client(
        courses_rows=[{"administrative_areas": "김녕리,종달리"}], visitor_rows=[]
    )

    with patch("src.agent.nodes.get_supabase_client", return_value=client):
        assert resolve_market_location_node(state) == {}


def test_resolve_market_location_defaults_to_latest_available_month_when_unspecified():
    """year/month 가 둘 다 없으면 "오늘 날짜"가 아니라 visitor_analytics 에 실제로 있는
    가장 최근 year_month 를 써야 합니다 (오늘 날짜엔 데이터가 없어 조용히 no-op 되던 버그 수정)."""
    state = {
        "b2b_params": {
            "target_month": None,
            "market_location_query": {
                "metric": "foreign_visitors",
                "year": None,
                "month": None,
                "direction": "desc",
            },
        }
    }

    courses_table = MagicMock()
    courses_table.select.return_value.execute.return_value.data = [
        {"administrative_areas": "김녕리,종달리"}
    ]

    visitor_table = MagicMock()

    def visitor_select_side_effect(cols):
        mock_query = MagicMock()
        if cols == "year_month":
            mock_query.order.return_value.limit.return_value.execute.return_value.data = [
                {"year_month": "2026-05"}
            ]
        else:
            chain = mock_query.eq.return_value.not_.is_.return_value.in_.return_value
            chain.order.return_value.limit.return_value.execute.return_value.data = [
                {"region_dong": "구좌읍", "foreign_visitors": 12000}
            ]
        return mock_query

    visitor_table.select.side_effect = visitor_select_side_effect

    client = MagicMock()
    client.table.side_effect = lambda name: {
        "courses": courses_table,
        "visitor_analytics": visitor_table,
    }[name]

    with patch("src.agent.nodes.get_supabase_client", return_value=client):
        result = resolve_market_location_node(state)

    assert result["b2b_params"]["preferred_location"] == "구좌읍"
    assert result["b2b_params"]["market_location_resolution"]["year_month"] == "2026-05"


def test_resolve_market_location_respects_explicit_month_even_without_data():
    """사용자가 명시적으로 월을 지정했다면, 그 달에 데이터가 없어도 최신 달로 몰래 바꿔치기하지
    않고 그대로 조회해야 합니다(지정한 조건을 존중)."""
    state = {
        "b2b_params": {
            "target_month": None,
            "market_location_query": {
                "metric": "foreign_visitors",
                "year": 2026,
                "month": 10,
                "direction": "desc",
            },
        }
    }
    client, courses_table, visitor_table = _make_client(
        courses_rows=[{"administrative_areas": "김녕리,종달리"}], visitor_rows=[]
    )

    with patch("src.agent.nodes.get_supabase_client", return_value=client):
        assert resolve_market_location_node(state) == {}

    visitor_table.select.return_value.eq.assert_called_with("year_month", "2026-10")


def test_resolve_market_location_returns_empty_on_query_exception():
    state = {
        "b2b_params": {
            "market_location_query": {
                "metric": "foreign_visitors",
                "year": 2026,
                "month": 5,
                "direction": "desc",
            }
        }
    }
    client, _, _ = _make_client(
        courses_rows=[{"administrative_areas": "김녕리,종달리"}],
        visitor_error=Exception("relation does not exist"),
    )

    with patch("src.agent.nodes.get_supabase_client", return_value=client):
        assert resolve_market_location_node(state) == {}
