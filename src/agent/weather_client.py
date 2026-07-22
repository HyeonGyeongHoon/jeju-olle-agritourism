import os
import requests
from typing import Dict, Any

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass


# 기상특보 조회서비스(getWthrWrnList) 의 stnId 는 기상청 관측지점코드를 사용합니다.
# 제주도는 행정구역상 제주시/서귀포시 두 권역으로 나뉘므로 각 권역의 대표 지점코드로 매핑합니다.
_JEJU_SI_STN_ID = "184"
_SEOGWIPO_SI_STN_ID = "189"
_SEOGWIPO_AREA_TOKENS = ["성산", "표선", "남원", "대정", "안덕"]

_HAZARD_KEYWORDS = ["강풍", "호우", "태풍", "대설", "한파", "폭염", "황사", "건조", "안개", "풍랑", "폭풍해일", "지진해일"]


def _resolve_stn_id(administrative_area: str) -> str:
    """제주도 내 행정구역명을 기상특보 조회 지점코드(stnId)로 매핑합니다."""
    if any(token in administrative_area for token in _SEOGWIPO_AREA_TOKENS):
        return _SEOGWIPO_SI_STN_ID
    return _JEJU_SI_STN_ID


def _safe_default(description: str) -> Dict[str, Any]:
    return {
        "status": "SAFE",
        "temperature": 22.5,
        "precipitation_mm": 0.0,
        "wind_speed_ms": 3.2,
        "warnings": [],
        "description": description
    }


def get_current_weather(administrative_area: str = "제주") -> Dict[str, Any]:
    """기상청 기상특보 조회서비스(getWthrWrnList) 를 연동하여 제주도 내 해당 권역의
    실시간 기상 특보 발효 여부를 수집합니다.
    API Key 가 설정되지 않았거나 호출 실패 시 기본 SAFE 상태를 반환합니다.
    """
    api_key = os.getenv("KMA_API_KEY")

    # 1. API 키가 설정되지 않은 경우, SAFE 상태를 기본으로 하는 Mock 데이터 반환
    if not api_key:
        return _safe_default("맑음 (Mock 데이터)")

    # 공공데이터 포털 기상청_기상특보 조회서비스 (지점코드 기반 조회)
    url = "http://apis.data.go.kr/1360000/WthrWrnInfoService/getWthrWrnList"
    params = {
        "ServiceKey": api_key,
        "pageNo": "1",
        "numOfRows": "10",
        "dataType": "JSON",
        "stnId": _resolve_stn_id(administrative_area)
    }

    try:
        response = requests.get(url, params=params, timeout=3.0)
        if response.status_code != 200:
            print(f"[!] 기상청 API 호출 실패 (HTTP {response.status_code}): {response.text[:200]}")
            return _safe_default("맑음")

        data = response.json()
        header = data.get("response", {}).get("header", {})
        result_code = header.get("resultCode")

        # 03 = 해당 지점에 발효 중인 특보 없음 (정상 케이스)
        if result_code == "03":
            return _safe_default("맑음")
        if result_code != "00":
            print(f"[!] 기상청 API 오류 응답: resultCode={result_code}, resultMsg={header.get('resultMsg')}")
            return _safe_default("맑음")

        items_container = data.get("response", {}).get("body", {}).get("items", {})
        item_list = items_container.get("item", []) if isinstance(items_container, dict) else (items_container or [])
        if isinstance(item_list, dict):
            item_list = [item_list]

        # 특보 제목에는 지역명이 포함되지 않으므로(stnId 로 이미 지역 필터링됨),
        # 재해 유형 키워드 매칭 + 이미 해제된 특보 제외로 현재 유효한 특보만 추출합니다.
        active_warnings = []
        for item in item_list:
            title = item.get("title", "")
            if "해제" in title:
                continue
            if any(keyword in title for keyword in _HAZARD_KEYWORDS):
                active_warnings.append(title)

        if active_warnings:
            return {
                "status": "DANGER" if any("경보" in w or "태풍" in w for w in active_warnings) else "WARNING",
                "temperature": 20.0,
                "precipitation_mm": 10.0,
                "wind_speed_ms": 14.0,
                "warnings": active_warnings,
                "description": "기상 특보 발효 중"
            }

    except Exception as e:
        print(f"[!] 기상청 API 호출 중 오류 발생: {e}. SAFE 상태로 폴백 처리합니다.")

    return _safe_default("맑음")


def simulate_weather_by_query(query: str) -> Dict[str, Any]:
    """사용자 질문 텍스트에 기상 위험 키워드가 있을 경우, 
    Safety Evaluator 노드가 올바르게 대처하는지 테스트하기 위한 날씨 시뮬레이션 유틸리티입니다.
    """
    if "태풍" in query or "폭우" in query or "홍수" in query:
        return {
            "status": "DANGER",
            "temperature": 18.0,
            "precipitation_mm": 80.0,
            "wind_speed_ms": 22.0,
            "warnings": ["제주도 태풍경보 발효 중"],
            "description": "태풍으로 인해 야외 활동이 매우 위험합니다."
        }
    elif "비" in query or "강풍" in query or "바람" in query:
        return {
            "status": "WARNING",
            "temperature": 19.5,
            "precipitation_mm": 15.0,
            "wind_speed_ms": 12.5,
            "warnings": ["제주도 강풍주의보 발효 중"],
            "description": "강풍 및 강우로 인해 해안가 코스는 위험할 수 있습니다."
        }
    return {
        "status": "SAFE",
        "temperature": 22.0,
        "precipitation_mm": 0.0,
        "wind_speed_ms": 3.0,
        "warnings": [],
        "description": "쾌적하고 맑음"
    }
