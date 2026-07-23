from unittest.mock import MagicMock

from src.agent.nodes import _fetch_market_insight


def _chain(mock_client):
    return (
        mock_client.table.return_value
        .select.return_value
        .eq.return_value
        .like.return_value
        .order.return_value
        .limit.return_value
    )


def test_fetch_market_insight_returns_row_on_success():
    mock_client = MagicMock()
    _chain(mock_client).execute.return_value.data = [
        {"region_dong": "구좌읍", "year_month": "2026-03", "total_visitors": 460038}
    ]

    result = _fetch_market_insight(mock_client, "구좌읍", 3)

    assert result == {"region_dong": "구좌읍", "year_month": "2026-03", "total_visitors": 460038}
    mock_client.table.assert_called_with("visitor_analytics")
    mock_client.table.return_value.select.return_value.eq.assert_called_with("region_dong", "구좌읍")
    mock_client.table.return_value.select.return_value.eq.return_value.like.assert_called_with(
        "year_month", "%-03"
    )


def test_fetch_market_insight_returns_none_when_no_rows():
    mock_client = MagicMock()
    _chain(mock_client).execute.return_value.data = []

    assert _fetch_market_insight(mock_client, "구좌읍", 3) is None


def test_fetch_market_insight_returns_none_on_query_exception():
    mock_client = MagicMock()
    _chain(mock_client).execute.side_effect = Exception("relation \"visitor_analytics\" does not exist")

    assert _fetch_market_insight(mock_client, "구좌읍", 3) is None


def test_fetch_market_insight_short_circuits_without_region_or_month():
    mock_client = MagicMock()

    assert _fetch_market_insight(mock_client, None, 3) is None
    assert _fetch_market_insight(mock_client, "구좌읍", None) is None
    mock_client.table.assert_not_called()
