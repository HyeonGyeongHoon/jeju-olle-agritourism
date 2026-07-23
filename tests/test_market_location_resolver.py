from unittest.mock import MagicMock, patch

from src.agent.nodes import resolve_market_location_node


def _make_client(courses_rows=None, visitor_rows=None, visitor_error=None):
    """courses / visitor_analytics 두 테이블 호출을 독립적으로 목킹한 Supabase 클라이언트를
    만듭니다. courses_rows 를 비워두면(기본값) 코스와 무관하게 지역 필터링 없이 조회됩니다."""
    courses_table = MagicMock()
    courses_table.select.return_value.execute.return_value.data = courses_rows or []

    visitor_table = MagicMock()
    visitor_chain = (
        visitor_table.select.return_value
        .eq.return_value
        .not_.is_.return_value
    )
    final_step = visitor_chain.in_.return_value if courses_rows else visitor_chain
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


def test_resolve_market_location_falls_back_unfiltered_when_courses_lookup_fails():
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
        courses_rows=None,  # courses.select() 자체가 빈 결과 -> 필터 없이 진행
        visitor_rows=[{"region_dong": "연동", "foreign_visitors": 45823}],
    )
    courses_table.select.side_effect = Exception("relation \"courses\" does not exist")

    with patch("src.agent.nodes.get_supabase_client", return_value=client):
        result = resolve_market_location_node(state)

    assert result["b2b_params"]["preferred_location"] == "연동"


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
    client, _, _ = _make_client(courses_rows=None, visitor_rows=[])

    with patch("src.agent.nodes.get_supabase_client", return_value=client):
        assert resolve_market_location_node(state) == {}


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
        courses_rows=None, visitor_error=Exception("relation does not exist")
    )

    with patch("src.agent.nodes.get_supabase_client", return_value=client):
        assert resolve_market_location_node(state) == {}
