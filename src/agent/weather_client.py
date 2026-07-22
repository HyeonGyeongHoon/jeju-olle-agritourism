from typing import Dict, Any

# 월별 제주도 계절 기후 특성 정적 참고 테이블입니다. 실시간 외부 기상 API 호출 없이,
# 문서화된 계절 지식만으로 기후 및 동선 리스크를 판단하기 위해 사용합니다.
_SEASONAL_CLIMATE_NOTES = {
    12: {"status": "WARNING", "description": "한파와 북서풍이 강한 초겨울", "warnings": ["한파·강풍 유의 시기"],
         "guideline": "방풍 장비를 갖추고 해안보다 중산간 코스를 우선 고려하세요."},
    1: {"status": "WARNING", "description": "연중 가장 추운 한겨울", "warnings": ["한파·강풍 유의 시기"],
        "guideline": "방풍 장비를 갖추고 해안보다 중산간 코스를 우선 고려하세요."},
    2: {"status": "WARNING", "description": "늦겨울, 강풍이 잦은 시기", "warnings": ["강풍 유의 시기"],
        "guideline": "해안 구간은 강풍에 대비하고, 중산간 우회 동선을 함께 안내하세요."},
    3: {"status": "SAFE", "description": "포근하고 쾌청한 초봄", "warnings": [],
        "guideline": "야외 도보 여행에 가장 쾌적한 시기입니다."},
    4: {"status": "SAFE", "description": "온화하고 화창한 봄", "warnings": [],
        "guideline": "특별한 리스크 없이 전 코스 탐방에 적합합니다."},
    5: {"status": "SAFE", "description": "초여름 문턱의 쾌청한 늦봄", "warnings": [],
        "guideline": "자외선 대비 외에 특별한 리스크는 없습니다."},
    6: {"status": "WARNING", "description": "장마가 시작되는 시기", "warnings": ["장마철 집중호우 가능 시기"],
        "guideline": "우천 시를 대비해 실내 체험 위주의 대안 동선을 함께 안내하세요."},
    7: {"status": "WARNING", "description": "한여름 폭염기", "warnings": ["폭염 유의 시기"],
        "guideline": "이른 아침·늦은 오후 시간대 탐방과 그늘 구간 위주 동선을 권장하세요."},
    8: {"status": "WARNING", "description": "폭염과 열대야가 이어지는 한여름", "warnings": ["폭염 유의 시기"],
        "guideline": "이른 아침·늦은 오후 시간대 탐방과 그늘 구간 위주 동선을 권장하세요."},
    9: {"status": "WARNING", "description": "태풍 영향이 잦은 초가을", "warnings": ["태풍 영향 가능 시기"],
        "guideline": "기상 특보 발효 시 해안 코스 대신 중산간·숲길 우회 동선을 준비하세요."},
    10: {"status": "SAFE", "description": "선선하고 쾌청한 가을", "warnings": [],
         "guideline": "단풍과 억새 풍경을 즐기기 좋은 시기입니다."},
    11: {"status": "SAFE", "description": "선선한 늦가을·초겨울 초입", "warnings": [],
         "guideline": "일교차에 대비한 방한 준비를 권장합니다."},
}


def _safe_default(description: str) -> Dict[str, Any]:
    return {
        "status": "SAFE",
        "temperature": 22.5,
        "precipitation_mm": 0.0,
        "wind_speed_ms": 3.2,
        "warnings": [],
        "description": description
    }


def get_seasonal_climate_note(month: int) -> Dict[str, Any]:
    """제주도의 월별 계절 기후 특성을 담은 정적 참고 테이블을 조회합니다.
    실시간 기상청 API 를 호출하지 않고, 문서화된 계절 지식만으로 기후 및 동선 리스크를 판단합니다.
    """
    note = _SEASONAL_CLIMATE_NOTES.get(month)
    if not note:
        return _safe_default("맑음")

    return {
        "status": note["status"],
        "temperature": 22.0,
        "precipitation_mm": 0.0,
        "wind_speed_ms": 3.0,
        "warnings": note["warnings"],
        "description": note["description"],
        "guideline": note["guideline"],
    }


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
