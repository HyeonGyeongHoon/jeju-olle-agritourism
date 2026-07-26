from types import SimpleNamespace
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


def test_fetch_market_insight_short_circuits_without_region():
    mock_client = MagicMock()

    assert _fetch_market_insight(mock_client, None, 3) is None
    mock_client.table.assert_not_called()


def test_fetch_market_insight_without_month_returns_latest_row_for_region():
    """사용자 요청: "최근 방문객 수는?"처럼 질의가 특정 월을 지정하지 않으면(target_month=None),
    월로 필터링하지 않고 해당 지역의 가장 최근 데이터를 그대로 반환해야 합니다 — 예전엔 이 경우
    바로 None을 반환해, DB에 최신 데이터가 있어도 "통계를 찾지 못했다"고 오답하는 원인이었습니다."""
    mock_client = MagicMock()
    no_month_chain = (
        mock_client.table.return_value
        .select.return_value
        .eq.return_value
        .order.return_value
        .limit.return_value
    )
    no_month_chain.execute.return_value.data = [
        {"region_dong": "외도동", "year_month": "2026-05", "total_visitors": 115205}
    ]

    result = _fetch_market_insight(mock_client, "외도동", None)

    assert result == {"region_dong": "외도동", "year_month": "2026-05", "total_visitors": 115205}
    # 월 필터(.like)는 호출되지 않아야 함 - 이 mock 체인에는 .like 가 아예 없으므로, 체인이
    # 끊기지 않고 여기까지 도달했다는 것 자체가 .like 를 거치지 않았다는 증거입니다.
    mock_client.table.return_value.select.return_value.eq.return_value.order.assert_called_with(
        "year_month", desc=True
    )


# --- 읍/면/동 접미사 표기 차이 대응 (2026-07-25) ---
# visitor_analytics 는 월×행정동 단위의 작은 테이블이므로(215행/43지역),
# region_dong 컬럼만 전량 조회해 정식 표기를 찾는 경로가 있습니다. 아래 가짜
# 클라이언트는 select 컬럼 문자열로 "*"(실데이터 조회) / "region_dong"(지역명
# 목록 조회)을 분기해 그 두 경로를 모두 모사합니다.


class _FakeVisitorAnalyticsQuery:
    def __init__(self, rows, columns):
        self._rows = rows
        self._columns = columns
        self._region = None
        self._month_suffix = None
        self._desc = False
        self._limit = None

    def eq(self, column, value):
        assert column == "region_dong"
        self._region = value
        return self

    def like(self, column, pattern):
        assert column == "year_month"
        self._month_suffix = pattern.lstrip("%")
        return self

    def order(self, column, desc=False):
        assert column == "year_month"
        self._desc = desc
        return self

    def limit(self, count):
        self._limit = count
        return self

    def execute(self):
        if self._columns == "region_dong":
            return SimpleNamespace(
                data=[{"region_dong": row["region_dong"]} for row in self._rows]
            )
        rows = [row for row in self._rows if row["region_dong"] == self._region]
        if self._month_suffix:
            rows = [
                row for row in rows if row["year_month"].endswith(self._month_suffix)
            ]
        rows.sort(key=lambda row: row["year_month"], reverse=self._desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class _FakeVisitorAnalyticsClient:
    def __init__(self, rows):
        self.rows = rows
        self.select_calls = []

    def table(self, name):
        assert name == "visitor_analytics"
        return self

    def select(self, columns):
        self.select_calls.append(columns)
        return _FakeVisitorAnalyticsQuery(self.rows, columns)


_HANLIM_ROWS = [
    {"region_dong": "한림읍", "year_month": "2026-03", "total_visitors": 401234},
    {"region_dong": "한림읍", "year_month": "2026-05", "total_visitors": 559007},
    {"region_dong": "애월읍", "year_month": "2026-05", "total_visitors": 812345},
]


def test_fetch_market_insight_matches_region_without_admin_tier_suffix():
    """회귀 방지: 예전엔 region_dong 을 `.eq()` 완전 일치로만 조회해서,
    intent_parser 가 "한림 기획서 만들어줘"에서 뽑은 preferred_location='한림' 이
    DB 값 '한림읍' 과 달라 통계가 None 이 됐다. 코스 매칭
    (_filter_course_ids_by_location)은 같은 질의에서 _normalize_admin_tier_name 으로
    접미사를 떼고 비교해 정상 매칭됐으므로, 코스는 잡히는데 Market Insight 만 비는
    경계면 불일치였다(실측: 한림읍 2026-05 방문객 559,007명 행이 실존).
    """
    client = _FakeVisitorAnalyticsClient(_HANLIM_ROWS)

    result = _fetch_market_insight(client, "한림", None)

    assert result == {
        "region_dong": "한림읍",
        "year_month": "2026-05",
        "total_visitors": 559007,
    }
    # 완전 일치 실패 후에만 지역명 목록을 조회해야 함
    # (정상 경로에 추가 쿼리를 얹지 않기 위함).
    assert client.select_calls == ["*", "region_dong", "*"]


def test_fetch_market_insight_keeps_month_filter_after_suffix_normalization():
    """접미사 정규화로 정식 표기를 찾아 재조회할 때도 target_month 필터가 유지되어야
    합니다(정식 표기 해석이 월 조건을 조용히 삼키면 엉뚱한 달의 통계를 인용하게 됨)."""
    client = _FakeVisitorAnalyticsClient(_HANLIM_ROWS)

    result = _fetch_market_insight(client, "한림", 3)

    assert result == {
        "region_dong": "한림읍",
        "year_month": "2026-03",
        "total_visitors": 401234,
    }


def test_fetch_market_insight_exact_region_name_does_not_trigger_extra_lookup():
    """회귀 방지: DB 표기와 정확히 같은 지역명('한림읍')은 예전과 동일하게 단 한 번의
    조회로 끝나야 합니다 — 접미사 정규화는 완전 일치가 0건일 때만 동작하는
    폴백입니다."""
    client = _FakeVisitorAnalyticsClient(_HANLIM_ROWS)

    result = _fetch_market_insight(client, "한림읍", 5)

    assert result == {
        "region_dong": "한림읍",
        "year_month": "2026-05",
        "total_visitors": 559007,
    }
    assert client.select_calls == ["*"]


def test_fetch_market_insight_returns_none_for_region_absent_from_table():
    """정규화해도 대응되는 지역이 없으면(통계 미적재 지역/오타) None 을 반환합니다."""
    client = _FakeVisitorAnalyticsClient(_HANLIM_ROWS)

    assert _fetch_market_insight(client, "성산읍", None) is None
    assert client.select_calls == ["*", "region_dong"]


def test_fetch_market_insight_fails_closed_when_normalization_is_ambiguous():
    """접미사를 뗀 표기가 여러 지역에 대응되면(예: '신촌읍'/'신촌면') 어느 지역
    통계인지 특정할 수 없으므로 fail-closed 로 None 을 반환합니다 — 기획서에 그대로
    인용되는 수치라서, 틀릴 수도 있는 지역의 통계를 붙이는 것보다 통계 없이 진행하는
    게 안전합니다."""
    client = _FakeVisitorAnalyticsClient(
        [
            {"region_dong": "신촌읍", "year_month": "2026-05", "total_visitors": 1},
            {"region_dong": "신촌면", "year_month": "2026-05", "total_visitors": 2},
        ]
    )

    assert _fetch_market_insight(client, "신촌", None) is None
