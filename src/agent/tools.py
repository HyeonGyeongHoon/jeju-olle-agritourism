import os
import re
import csv
from datetime import date
from typing import Any, Dict, List
from src.ingestion.database_loader import get_supabase_client
from src.agent.nodes import (
    _MARKET_METRIC_LABELS,
    _get_olle_relevant_admin_dongs,
    _get_latest_available_year_month,
    _search_culture_knowledge
)


def _normalize_year_month(year_month_str: str | None) -> str | None:
    """입력받은 문자열(예: '2026-5', '2026/05', '2026년 5월')을 ISO 표준 연월 포맷('YYYY-MM')으로 정규화합니다."""
    if not year_month_str:
        return None
    cleaned = str(year_month_str).strip()
    match = re.search(r"(\d{4})[^\d]?(\d{1,2})", cleaned)
    if match:
        year = match.group(1)
        month = int(match.group(2))
        return f"{year}-{month:02d}"
    return cleaned


def retrieve_visitor_statistics_tool(
    region_dong: str,
    year_month: str | None = None,
    metric: str | None = None
) -> str:
    """제주 올레길 경유 지역의 관광 방문객 빅데이터(visitor_analytics)를 조회하는 에이전트 도구입니다.
    
    인자 검증을 수행하여 지원 불가능한 조건일 경우, 구체적인 오류 사유와 가용한 옵션 목록을 반환합니다.
    """
    client = get_supabase_client()
    
    # 1. 지표(metric) 유효성 검증
    if metric and metric not in _MARKET_METRIC_LABELS:
        valid_metrics = ", ".join(f"'{k}'({v})" for k, v in _MARKET_METRIC_LABELS.items())
        return (
            f"[오류] 입력하신 지표 '{metric}'은 지원하지 않습니다.\n"
            f"조회 가능한 지표 목록: {valid_metrics}"
        )
    
    target_metric = metric or "total_visitors"
    
    # 2. 지역(region_dong) 유효성 검증
    olle_dongs = _get_olle_relevant_admin_dongs(client)
    if region_dong not in olle_dongs:
        valid_dongs = ", ".join(sorted(list(olle_dongs)))
        return (
            f"[오류] '{region_dong}'은 올레 코스 경유 지역이 아니어서 통계를 조회할 수 없습니다.\n"
            f"조회 가능한 행정동/읍/면 목록: {valid_dongs}"
        )
        
    # 3. 기간(year_month) 유효성 및 ISO 정규화 검증
    try:
        res_dates = client.table("visitor_analytics").select("year_month").execute()
        available_months = sorted(list(set(row["year_month"] for row in res_dates.data if row.get("year_month"))))
    except Exception as e:
        return f"[오류] 데이터베이스 조회 중 오류가 발생했습니다: {e}"
        
    if not available_months:
        return "[오류] 통계 데이터베이스가 비어있습니다."
        
    normalized_ym = _normalize_year_month(year_month)
    target_ym = normalized_ym
    if not target_ym:
        target_ym = _get_latest_available_year_month(client) or available_months[-1]
    elif target_ym not in available_months:
        valid_months = ", ".join(available_months)
        return (
            f"[오류] 요청하신 기간 '{year_month}'(정규화: '{target_ym}')의 데이터는 존재하지 않습니다.\n"
            f"조회 가능한 데이터 기간: {valid_months}"
        )
        
    # 4. DB 실제 데이터 조회
    try:
        res = client.table("visitor_analytics").select(f"region_dong, year_month, {target_metric}").eq("region_dong", region_dong).eq("year_month", target_ym).execute()
        if not res.data:
            return f"[정보] {region_dong} 지역의 {target_ym} 기준 데이터가 존재하지 않습니다."
            
        val = res.data[0][target_metric]
        metric_name = _MARKET_METRIC_LABELS.get(target_metric, target_metric)
        
        # 포맷팅 처리
        if isinstance(val, (int, float)):
            if "ratio" in target_metric or "growth" in target_metric:
                formatted_val = f"{val}%"
            else:
                formatted_val = f"{val:,}명"
        else:
            formatted_val = f"{val}"
        
        return (
            f"[조회 성공]\n"
            f"- 지역: {region_dong}\n"
            f"- 기간: {target_ym}\n"
            f"- 항목: {metric_name} ({target_metric})\n"
            f"- 결과 값: {formatted_val}"
        )
    except Exception as e:
        return f"[오류] 데이터 조회 실패: {e}"


def retrieve_culture_crop_knowledge_tool(keyword_or_crop: str) -> str:
    """제주 밭담문화 및 대표 20종 작물 생육 지식 DB를 벡터 검색하여 관련 정보를 반환하는 도구입니다."""
    if not keyword_or_crop or not keyword_or_crop.strip():
        return "[오류] 검색할 작물명이나 문화 지식 키워드를 명시해 주세요."

    client = get_supabase_client()
    query = keyword_or_crop.strip()
    chunks = _search_culture_knowledge(client, query, query)
    
    if not chunks:
        valid_crops = "감귤, 당근, 보리, 마늘, 메밀, 취나물, 양배추, 브로콜리, 비트, 무, 감자, 고구마, 무화과, 키위 등"
        return (
            f"[안내] '{query}'와(과) 직접 관련된 제주 밭담/작물 지식을 찾지 못했습니다.\n"
            f"조회 가능한 대표 제주 작물 및 문화 주제 예시: {valid_crops}"
        )
        
    result_str = f"[지식 조회 성공: '{query}' 관련 {len(chunks)}건]\n"
    for i, c in enumerate(chunks):
        title = c.get("title") or "무제"
        content = c.get("content") or ""
        crop_name = c.get("crop_name") or c.get("target_crop") or ""
        months = c.get("active_months")
        season_info = f" (활동/제철 월: {months})" if months else ""
        result_str += f"\n{i+1}. [{title}]{season_info}:\n{content}\n"
        
    return result_str.strip()


# Solar LLM 호환 Function Calling Schema 정의
VISITOR_STATS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "retrieve_visitor_statistics_tool",
        "description": "제주 올레길 경유 지역의 관광 방문객 빅데이터(총 방문객, 외국인 수, 연령대 비중, 증감률)를 조회합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "region_dong": {
                    "type": "string",
                    "description": "올레길 경유 행정동/읍/면 이름 (예: '성산읍', '구좌읍', '표선면', '한림읍')"
                },
                "year_month": {
                    "type": "string",
                    "description": "조회할 연월 (YYYY-MM 형식, 예: '2026-05')"
                },
                "metric": {
                    "type": "string",
                    "description": "조회할 통계 지표 (total_visitors, foreign_visitors, yoy_growth_rate, young_2030_ratio, female_ratio 중 하나)"
                }
            },
            "required": ["region_dong"]
        }
    }
}

CULTURE_KNOWLEDGE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "retrieve_culture_crop_knowledge_tool",
        "description": "제주 밭담문화, 곶자왈, 해녀 및 대표 작물(당근, 감귤, 보리 등)의 제철 및 생육 지식을 조회합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword_or_crop": {
                    "type": "string",
                    "description": "검색할 작물명이나 문화 키워드 (예: '당근', '밭담', '감귤')"
                }
            },
            "required": ["keyword_or_crop"]
        }
    }
}

AVAILABLE_TOOLS_SCHEMA = [
    VISITOR_STATS_TOOL_SCHEMA,
    CULTURE_KNOWLEDGE_TOOL_SCHEMA
]
