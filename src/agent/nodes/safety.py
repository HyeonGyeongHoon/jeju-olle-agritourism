"""기후/동선 리스크 진단(Safety Evaluator) 노드.

(2026-07-26 nodes.py 분할 — 로직 변경 없는 순수 코드 이동)
"""

from datetime import date
from typing import Any, Dict

from src.agent.state import AgentState

# --- 로컬 임포트 규약 (2026-07-26 분할 시 도입, 반드시 유지) ---
# 이 모듈의 노드 함수들은 필요한 헬퍼/클라이언트를 모듈 상단이 아니라 **함수 본문 안에서**
# `from src.agent.nodes import ...` 로 가져옵니다. 분할 이전 nodes.py 에서는 이 이름들이
# 전부 한 모듈의 전역이었기 때문에 테스트가 `patch.object(nodes, "X")` 로 목킹할 수 있었고,
# 그 계약을 그대로 유지하려면 호출 시점에 nodes 패키지 네임스페이스에서 이름을 해석해야
# 합니다(모듈 상단에서 원본 모듈로부터 직접 import 하면 그 목이 가로채지 못함 —
# CLAUDE.md 의 db_service 이관 당시 테스트 7건이 깨진 것과 동일한 함정).
# 동시에 서브모듈 간 상호 참조(reporter <-> quality 등)의 순환 임포트도 함께 해소합니다.


def evaluate_safety_node(state: AgentState) -> Dict[str, Any]:
    """방문 시기(월)의 정적 계절 기후 특성과, 질의 텍스트에 실제로 담긴 기상 위험 여부에 대한
    LLM 판단을 결합해 기후 및 동선 리스크를 진단하는 Safety Evaluator 노드입니다. 실시간 외부
    기상 API(KMA 등)는 호출하지 않고, 문서화된 계절 지식(get_seasonal_climate_note)과 Solar
    LLM(assess_weather_risk_from_query) 만 사용합니다.
    """
    from src.agent.nodes import (
        assess_weather_risk_from_query,
        get_seasonal_climate_note,
    )

    query = state["query"]
    b2b_params = state.get("b2b_params") or {}
    target_month = b2b_params.get("target_month") or date.today().month

    # 1. 방문 월 기반 정적 계절 기후 특성 조회 (외부 API 호출 없음)
    seasonal_weather = get_seasonal_climate_note(target_month)

    # 2. 질문 텍스트가 실제로 지금/이번 방문에 대한 기상 위험을 의미하는지 LLM으로 판단
    #    (예전엔 "태풍"/"폭우"/"홍수" 단어가 있기만 하면 문맥과 무관하게 DANGER로 오판했음 —
    #    2026-07-24 QA 리뷰 지적 후 LLM 문맥 판단으로 교체)
    assessed_weather = assess_weather_risk_from_query(query)

    # 3. 계절 기후와 LLM 판단 결합. DANGER(질문이 실제로 지금 위험을 묻는 경우)만 계절 기후
    # 판단을 덮어쓰고, 그 외(SAFE)는 계절 기후 판단을 그대로 유지합니다.
    weather = seasonal_weather
    if assessed_weather["status"] == "DANGER":
        weather = assessed_weather

    safety_check = {
        "safety_status": weather["status"],
        "reason": weather["description"],
        "reroute_required": weather["status"] in ["WARNING", "DANGER"],
        "alternative_query_override": None
    }

    # 기상 악화 시 대체 안전 경로(내륙 숲길, 우회로)를 추천하도록 쿼리 보정 정보 추가 또는 반려 설정
    is_exit_early = False
    exit_reason = None

    if safety_check["reroute_required"]:
        if weather["status"] == "DANGER":
            # DANGER 등급 기상 악화 시, 우회 코스를 찾지 않고 무조건 반려(Fail-Fast) 처리합니다.
            is_exit_early = True
            exit_reason = f"기상 악화({weather['description']})로 인해 안전을 위해 올레길 B2B 상품 기획서를 작성할 수 없습니다."
        else:
            safety_check["alternative_query_override"] = weather.get("guideline") or "해안 도로 대신 바람이 차단된 조용하고 안전한 중산간 올레길 코스"

    print(f"[DEBUG] evaluate_safety_node 리턴 - is_exit_early: {is_exit_early}, exit_reason: {exit_reason}")
    return {
        "weather_info": weather,
        "safety_check": safety_check,
        "is_exit_early": is_exit_early,
        "exit_reason": exit_reason,
    }
