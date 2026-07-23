import os

from src.ingestion.parse_visitor_pdf import (
    _parse_ranked_visitor_lines,
    _row_to_gender_ratio,
    _row_to_age_ratio,
    _is_gender_ratio_table_header,
    _is_age_ratio_table_header,
    parse_visitor_pdf,
)

REPORTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "raw_data", "reports",
)


# --- 순수 로직 유닛테스트 (실제 PDF 없이 가짜 텍스트/로우로 검증) ---

def test_parse_ranked_visitor_lines_basic():
    text = (
        "3.1 제주시 행정동별 방문객 – 전체 행정동\n"
        "1위 용담2동 984,999 15.1 1,495,776 19.9 51.9\n"
        "2위 애월읍 701,096 10.7 798,109 10.6 13.8\n"
        "총인구 6,525,012 100 7,522,468 100 15.3\n"
    )
    result = _parse_ranked_visitor_lines(text)
    assert result["용담2동"] == (1495776, 51.9)
    assert result["애월읍"] == (798109, 13.8)
    assert "총인구" not in result


def test_parse_ranked_visitor_lines_handles_city_prefix_and_legend_suffix():
    """일부 로우는 시군명 라벨이 줄 앞에 붙거나(예: '서귀포시 9위 대륜동 ...'),
    지도 범례 텍스트가 줄 뒤에 붙는다('...58.2 ※ 행정동방문객(등간격분류)')."""
    text = (
        "서귀포시 9위 대륜동 158,687 4.3 251,041 6.2 58.2\n"
        "25위 화북동 58,453 0.9 60,730 0.8 3.9 ※ 행정동방문객(등간격분류)\n"
    )
    result = _parse_ranked_visitor_lines(text)
    assert result["대륜동"] == (251041, 58.2)
    assert result["화북동"] == (60730, 3.9)


def test_row_to_gender_ratio():
    row = ["1", "제주시", "구좌읍", "200,555", "43.6", "259,483", "56.4", "460,038"]
    result = _row_to_gender_ratio(row)
    assert result == ("구좌읍", 43.6, 56.4)


def test_row_to_age_ratio():
    row = [None, "제주시", "봉개동", "14,539", "14.1", "21,656", "21", "58,109", "56.34", "8,828", "8.56", None]
    result = _row_to_age_ratio(row)
    assert result == ("봉개동", 14.1, 21.0, 56.34, 8.56)


def test_gender_and_age_header_detection():
    gender_header = ["순위", "시군명", "행정동명", "남성", None, "여성", None, "총 인구"]
    age_header = [
        "순위", "시군명", "행정동명",
        "10대 이하\n청소년층", None, "20대 ~ 30대\n청년층", None,
        "40대 ~ 60대\n중·장년층", None, "70대 이상\n노년층", None, "전체 방문객",
    ]
    total_visitor_header = ["순위", None, "행정동명", "2025년 03월", None, "2026년 03월", None, "증감률"]

    assert _is_gender_ratio_table_header(gender_header) is True
    assert _is_age_ratio_table_header(age_header) is True
    assert _is_gender_ratio_table_header(total_visitor_header) is False
    assert _is_age_ratio_table_header(gender_header) is False


# --- 실제 PDF 검증 (data/raw_data/reports/*.pdf, 육안 확인한 실측값과 대조) ---

def test_parse_visitor_pdf_real_may_issue():
    path = os.path.join(REPORTS_DIR, "제주 관광객 방문 패턴 분석 보고서 2026년 5월호.pdf")
    records = parse_visitor_pdf(path)

    assert len(records) == 43
    by_dong = {r["region_dong"]: r for r in records}

    # "7월호"는 발행월일 뿐, 실제 데이터는 1페이지 "분석 년 월"(2026년03월)에서 온다.
    assert records[0]["year_month"] == "2026-03"

    aewol = by_dong["애월읍"]
    assert aewol["total_visitors"] == 798109
    assert aewol["yoy_growth_rate"] == 13.8
    assert aewol["male_ratio"] == 47.8
    assert aewol["female_ratio"] == 52.2
    assert aewol["foreign_visitors"] == 9443

    # 시군명이 로우 앞에 붙어 파싱이 깨지기 쉬웠던 케이스 (회귀 방지)
    daeryun = by_dong["대륜동"]
    assert daeryun["total_visitors"] == 251041
    assert daeryun["yoy_growth_rate"] == 58.2

    # 성별/연령대 순위표에 등장하지 않는 행정동은 None 유지 (시군 평균 등으로 대체하지 않음)
    unranked_candidates = [
        r for r in records
        if r["female_ratio"] is None and r["youth_10s_ratio"] is None
    ]
    assert len(unranked_candidates) > 0

    bonggae = by_dong["봉개동"]
    assert bonggae["youth_10s_ratio"] == 14.1
    assert bonggae["young_2030_ratio"] == 21.0
    assert bonggae["middle_4060_ratio"] == 56.34
    assert bonggae["senior_70s_ratio"] == 8.56


def test_parse_visitor_pdf_year_month_uses_analysis_month_not_filename():
    """"7월호" 파일명이지만 실제 데이터는 2개월 전(5월) 기준임을 확인."""
    path = os.path.join(REPORTS_DIR, "제주 관광객 방문 패턴 분석 보고서 2026년 7월호.pdf")
    records = parse_visitor_pdf(path)
    assert records[0]["year_month"] == "2026-05"


def test_parse_visitor_pdf_fallback_on_missing_file():
    records = parse_visitor_pdf(os.path.join(REPORTS_DIR, "존재하지_않는_파일.pdf"))
    assert len(records) > 0
    assert all("region_dong" in r for r in records)
