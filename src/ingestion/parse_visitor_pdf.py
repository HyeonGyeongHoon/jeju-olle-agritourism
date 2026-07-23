"""제주관광공사 월간 이동통신 빅데이터 기반 '제주 관광객 방문 패턴 분석 보고서' PDF에서
행정동별 방문객 통계를 파싱하여 Supabase `visitor_analytics` 테이블 적재용 레코드로 변환합니다.

파싱 대상과 커버리지 (실제 보고서 구조 확인 결과):
- 총 방문객 수 / 전년대비 증감률 / 외국인 방문객 수: 제주시+서귀포시 전체 43개 행정동에 대해
  매달 완전하게 존재 ("행정동별 (외국인) 방문객 – 전체 행정동" 표, Chapter 2/3).
- 성별 비율(female_ratio/male_ratio) / 연령대 비율(youth_10s_ratio 등): 그 지표 기준 "상위 10위/
  상위 5위" 랭킹표(Chapter 4)에 등장하는 행정동에 대해서만 존재. 나머지 행정동은 그 달 데이터
  자체가 없으므로, 다른 범위(시군 평균 등)로 대체하지 않고 None 으로 남깁니다.
- 각 보고서 파일명의 "O월호"는 발행월일 뿐 실제 분석 대상월과 다를 수 있어(2개월 시차 발행 확인),
  1페이지 본문의 "분석 년 월: YYYY년MM월" 텍스트에서 실제 year_month 를 파싱합니다.
"""

import json
import os
import re

import pdfplumber
from pydantic import ValidationError

from src.models.schema import VisitorAnalyticsSchema

_BACKUP_JSON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "visitor_analytics", "backup_stats.json"
)

_ANALYSIS_MONTH_RE = re.compile(r"분석\s*년\s*월\s*[:：]\s*(\d{4})년\s*(\d{1,2})월")

# "1위 애월읍 701,096 10.7 798,109 10.6 13.8" 형태의 "행정동별 (외국인) 방문객 – 전체 행정동"
# 표 로우. 순위(N위)/행정동명/전년 인구/전년 비율/당년 인구/당년 비율/증감률 순.
# extract_tables() 는 이 표의 마지막(증감률) 컬럼을 신뢰성 있게 추출하지 못해(셀 테두리 미검출
# 확인됨) 텍스트 라인 정규식으로 파싱합니다. 표 아래쪽 지도 범례 텍스트("높음/낮음", "※ ...")나
# 시군명 구분 라벨("서귀포시 9위 대륜동 ...")이 줄 앞에 붙는 경우가 있어 줄 시작이 아닌
# search 로 패턴을 찾습니다.
_RANKED_VISITOR_LINE_RE = re.compile(
    r"(\d+)위\s+(\S+)\s+[\d,]+\s+-?[\d.]+\s+([\d,]+)\s+-?[\d.]+\s+(-?[\d.]+)"
)


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_analysis_year_month(pdf: "pdfplumber.PDF") -> str:
    """1페이지 텍스트의 "분석 년 월: YYYY년MM월"에서 실제 분석 대상월을 파싱합니다.
    보고서 파일명(예: "2026년 7월호")은 발행월일 뿐이라 실제 데이터 기준월과 다를 수 있어
    (2개월 시차 발행 확인됨: "7월호"의 실제 데이터는 "2026년05월") 파일명이 아닌 본문을 사용합니다.
    """
    first_page_text = pdf.pages[0].extract_text() or ""
    m = _ANALYSIS_MONTH_RE.search(first_page_text)
    if not m:
        raise ValueError("PDF에서 '분석 년 월' 텍스트를 찾지 못했습니다.")
    return f"{m.group(1)}-{int(m.group(2)):02d}"


def _parse_ranked_visitor_lines(page_text: str) -> dict[str, tuple[int, float]]:
    """"행정동별 (외국인) 방문객 – 전체 행정동" 표의 텍스트를 {행정동명: (당년 인구, 증감률)} 로 반환."""
    result: dict[str, tuple[int, float]] = {}
    for line in page_text.split("\n"):
        m = _RANKED_VISITOR_LINE_RE.search(line.strip())
        if not m:
            continue
        _rank, region_dong, population_raw, growth_raw = m.groups()
        population = _to_float(population_raw)
        growth = _to_float(growth_raw)
        if population is None:
            continue
        result[region_dong] = (int(population), growth)
    return result


def _is_gender_ratio_table_header(row0: list) -> bool:
    return (
        len(row0) == 8
        and row0[0] == "순위"
        and row0[1] == "시군명"
        and row0[2] == "행정동명"
        and row0[3] == "남성"
    )


def _is_age_ratio_table_header(row0: list) -> bool:
    return (
        len(row0) == 12
        and row0[2] == "행정동명"
        and row0[3] is not None
        and "청소년층" in row0[3]
    )


def _row_to_gender_ratio(row: list) -> tuple[str, float, float] | None:
    if len(row) < 8:
        return None
    region_dong = (row[2] or "").strip()
    male_ratio = _to_float(row[4])
    female_ratio = _to_float(row[6])
    if not region_dong or male_ratio is None or female_ratio is None:
        return None
    return region_dong, male_ratio, female_ratio


def _row_to_age_ratio(row: list) -> tuple[str, float, float, float, float] | None:
    if len(row) < 12:
        return None
    region_dong = (row[2] or "").strip()
    youth = _to_float(row[4])
    young = _to_float(row[6])
    middle = _to_float(row[8])
    senior = _to_float(row[10])
    if not region_dong or None in (youth, young, middle, senior):
        return None
    return region_dong, youth, young, middle, senior


def parse_visitor_pdf(pdf_path: str) -> list[dict]:
    """PDF 1건을 파싱해 `visitor_analytics` 적재용 레코드(dict) 리스트를 반환합니다.
    파싱이 전체적으로 실패하면(섹션을 찾지 못하는 등) 백업 JSON 폴백 데이터를 반환합니다.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            year_month = _extract_analysis_year_month(pdf)

            visitors: dict[str, tuple[int, float | None]] = {}
            foreigners: dict[str, int] = {}
            gender_ratios: dict[str, tuple[float, float]] = {}
            age_ratios: dict[str, tuple[float, float, float, float]] = {}

            in_dong_chapter = False
            for page in pdf.pages:
                page_text = page.extract_text() or ""

                # Chapter. 04(행정동 단위) ~ Chapter. 05(블록 단위) 사이만 성별/연령대 랭킹표 대상.
                # 두 챕터가 동일한 표 헤더 모양을 쓰므로 페이지 범위로 구분해야 합니다.
                if "Chapter. 04" in page_text:
                    in_dong_chapter = True
                elif "Chapter. 05" in page_text:
                    in_dong_chapter = False

                if "전체 행정동" in page_text:
                    parsed_lines = _parse_ranked_visitor_lines(page_text)
                    if "외국인" in page_text:
                        for region_dong, (count, _growth) in parsed_lines.items():
                            foreigners[region_dong] = count
                    else:
                        for region_dong, (count, growth) in parsed_lines.items():
                            visitors[region_dong] = (count, growth)

                if in_dong_chapter:
                    for table in page.extract_tables():
                        if not table or len(table) < 3:
                            continue
                        header = table[0]
                        if _is_gender_ratio_table_header(header):
                            for row in table[2:]:
                                parsed = _row_to_gender_ratio(row)
                                if parsed:
                                    region_dong, male_ratio, female_ratio = parsed
                                    gender_ratios[region_dong] = (male_ratio, female_ratio)
                        elif _is_age_ratio_table_header(header):
                            for row in table[2:]:
                                parsed = _row_to_age_ratio(row)
                                if parsed:
                                    region_dong, youth, young, middle, senior = parsed
                                    age_ratios[region_dong] = (youth, young, middle, senior)

            if not visitors:
                raise ValueError("'행정동별 방문객 – 전체 행정동' 표를 찾지 못했습니다.")

            records = []
            for region_dong, (total_visitors, yoy_growth_rate) in visitors.items():
                male_ratio, female_ratio = gender_ratios.get(region_dong, (None, None))
                youth, young, middle, senior = age_ratios.get(
                    region_dong, (None, None, None, None)
                )
                try:
                    record = VisitorAnalyticsSchema(
                        year_month=year_month,
                        region_dong=region_dong,
                        total_visitors=total_visitors,
                        yoy_growth_rate=yoy_growth_rate,
                        female_ratio=female_ratio,
                        male_ratio=male_ratio,
                        youth_10s_ratio=youth,
                        young_2030_ratio=young,
                        middle_4060_ratio=middle,
                        senior_70s_ratio=senior,
                        foreign_visitors=foreigners.get(region_dong),
                    ).model_dump()
                    records.append(record)
                except ValidationError as e:
                    print(f"[!] '{region_dong}' 레코드 검증 실패, 건너뜀: {e}")

            return records
    except Exception as e:
        print(f"[!] PDF 파싱 실패({pdf_path}), 백업 JSON으로 폴백합니다: {e}")
        return _load_backup_stats()


def _load_backup_stats() -> list[dict]:
    try:
        with open(_BACKUP_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[!] 백업 JSON 로드 실패: {e}")
        return []


def upsert_visitor_analytics(client, records: list[dict]) -> bool:
    """파싱된 레코드를 Supabase `visitor_analytics` 테이블에 upsert 합니다
    (year_month + region_dong 복합 유니크 키 기준)."""
    if not records:
        return True
    try:
        client.table("visitor_analytics").upsert(
            records, on_conflict="year_month,region_dong"
        ).execute()
    except Exception as e:
        print(f"[!] visitor_analytics DB 적재 건너뜀 (테이블 확인 필요): {e}")
    return True
