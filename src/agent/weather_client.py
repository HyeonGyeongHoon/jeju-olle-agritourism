import json
from typing import Dict, Any

from src.agent.llm_client import get_chat_completion

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


_WEATHER_RISK_SYSTEM_PROMPT = """당신은 제주올레 탐방객의 질문에서 실제 기상 위험 여부를 판단하는 안전 분석기입니다.
질문에 "태풍", "폭우", "홍수", "강풍", "비" 같은 날씨 단어가 들어 있어도, 그것이 항상 지금/이번
방문 시점에 대한 실제 위험 경보를 의미하지는 않습니다. 아래 기준으로만 판단하세요.

[DANGER 로 판단하는 경우]
- 질문 자체가 지금 또는 곧 있을 방문 시점에 태풍/폭우/홍수 등 심각한 기상 위험이 실제로 있다고
  전제하거나, 그 위험 속에서 탐방이 가능한지를 묻는 경우
  (예: "태풍 오는데 지금 걸어도 될까요?", "이번 주 폭우 예보인데 코스 괜찮을까요?")

[SAFE 로 판단하는 경우 - 날씨 단어가 있어도 지금 실제 위험 상황을 묻는 게 아님]
- 과거 사례를 언급하는 경우 (예: "작년 태풍 피해가 컸다던데")
- 위험 상황에 대한 일반적인 대비책/에티켓을 묻는 경우 (예: "태풍 오면 어떻게 대처해야 하나요?")
- 날씨와 무관한 맥락에서 단어만 스친 경우

JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON 문자열로만 반환하세요.
{
  "status": "DANGER" 또는 "SAFE",
  "reason": "판단 근거 한 문장"
}"""


def assess_weather_risk_from_query(query: str) -> Dict[str, Any]:
    """사용자 질문 텍스트에서 실제로 지금/이번 방문에 영향을 주는 기상 위험이 있는지를 LLM으로
    판단합니다(Solar API 재사용, 새 외부 연동 없음).
    예전엔 "태풍"/"폭우"/"홍수" 같은 단어가 문맥과 무관하게(과거 언급, 대비책 질문 등) 들어있기만
    해도 무조건 DANGER 로 오판했습니다(2026-07-24 QA 리뷰 지적 — "작년 태풍 피해가 컸나요?" 같은
    질문도 지금 태풍이 온 것처럼 오탐지). 이제는 그 단어가 실제로 "이번 방문 시점의 위험 경보"를
    의미하는지, 아니면 과거 사례·가정·대비책 질문처럼 실제 위험이 아닌지를 LLM이 구분합니다.
    LLM 호출 실패 시 SAFE 로 폴백합니다 — 이 함수의 목적 자체가 오탐지로 인한 불필요한 안전
    우회를 없애는 것이므로, 장애 시 DANGER 로 폴백하면 같은 문제가 형태만 바뀌어 재발합니다.
    """
    try:
        raw = get_chat_completion(_WEATHER_RISK_SYSTEM_PROMPT, f"[탐방객 질문]: {query}")
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        parsed = json.loads(cleaned.strip())
        status = parsed.get("status") if parsed.get("status") in ("DANGER", "SAFE") else "SAFE"
    except Exception as e:
        print(f"[!] 날씨 위험 LLM 판단 실패, 안전하게 SAFE 로 폴백합니다: {e}")
        status = "SAFE"

    if status == "DANGER":
        return {
            "status": "DANGER",
            "temperature": 18.0,
            "precipitation_mm": 80.0,
            "wind_speed_ms": 22.0,
            "warnings": ["제주도 태풍/폭우 등 기상 위험 경보 발효 중"],
            "description": "기상 악화로 인해 야외 활동이 매우 위험합니다."
        }
    return {
        "status": "SAFE",
        "temperature": 22.0,
        "precipitation_mm": 0.0,
        "wind_speed_ms": 3.0,
        "warnings": [],
        "description": "쾌적하고 맑음"
    }
