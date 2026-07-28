import os
import pytest
from src.agent.tools import retrieve_visitor_statistics_tool

# dummy 환경 변수 설정 시 실제 Supabase DB 조회가 필요한 통계 테스트 건너뛰기
is_dummy = "dummy" in os.getenv("SUPABASE_URL", "")
if is_dummy:
    pytestmark = pytest.mark.skip("실제 Supabase DB 연결이 필요한 통계 도구 테스트이므로 건너뜁니다.")


def test_retrieve_visitor_statistics_tool_success():
    # 성산읍은 올레길이 지나는 대표적인 유효 행정동입니다.
    # 수집 데이터가 있는 연월로 정상 조회를 시도합니다.
    res = retrieve_visitor_statistics_tool(
        region_dong="성산읍",
        year_month="2026-05",
        metric="total_visitors"
    )
    assert "[조회 성공]" in res
    assert "성산읍" in res
    assert "2026-05" in res
    assert "총 방문객 수" in res

def test_retrieve_visitor_statistics_tool_invalid_metric():
    res = retrieve_visitor_statistics_tool(
        region_dong="성산읍",
        year_month="2026-05",
        metric="invalid_metric"
    )
    assert "[오류]" in res
    assert "지원하지 않습니다" in res
    assert "total_visitors" in res

def test_retrieve_visitor_statistics_tool_invalid_region():
    res = retrieve_visitor_statistics_tool(
        region_dong="노형동",  # 노형동은 올레길 코스가 지나지 않는 지역
        year_month="2026-05",
        metric="total_visitors"
    )
    assert "[오류]" in res
    assert "올레 코스 경유 지역이 아니어서" in res
    assert "성산읍" in res  # 가용 지역 리스트에 성산읍 등이 들어있음

def test_retrieve_visitor_statistics_tool_invalid_month():
    res = retrieve_visitor_statistics_tool(
        region_dong="성산읍",
        year_month="2028-12",  # 수집 범위를 벗어난 미래 월
        metric="total_visitors"
    )
    assert "[오류]" in res
    assert "데이터는 존재하지 않습니다" in res
