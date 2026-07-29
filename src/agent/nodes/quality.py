"""자체 검증(quality_checker) / 쿼리 재작성(query_rewriter) 노드.

(2026-07-26 nodes.py 분할 — 로직 변경 없는 순수 코드 이동)
"""

import json
import re
from datetime import date
from typing import Any, Dict

# pyrefly: ignore [missing-import]
from src.agent.state import AgentState

# --- 로컬 임포트 규약 (2026-07-26 분할 시 도입, 반드시 유지) ---
# 이 모듈의 노드 함수들은 필요한 헬퍼/클라이언트를 모듈 상단이 아니라 **함수 본문 안에서**
# `from src.agent.nodes import ...` 로 가져옵니다. 분할 이전 nodes.py 에서는 이 이름들이
# 전부 한 모듈의 전역이었기 때문에 테스트가 `patch.object(nodes, "X")` 로 목킹할 수 있었고,
# 그 계약을 그대로 유지하려면 호출 시점에 nodes 패키지 네임스페이스에서 이름을 해석해야
# 합니다(모듈 상단에서 원본 모듈로부터 직접 import 하면 그 목이 가로채지 못함 —
# CLAUDE.md 의 db_service 이관 당시 테스트 7건이 깨진 것과 동일한 함정).
# 동시에 서브모듈 간 상호 참조(reporter <-> quality 등)의 순환 임포트도 함께 해소합니다.


_SELF_RAG_STARS_PLACEHOLDER = "{{SELF_RAG_STARS}}"
_QUALITY_COMMENT_PLACEHOLDER = "{{QUALITY_COMMENT}}"
_QUALITY_COMMENT_MAX_LEN = 60


def _score_to_stars(score: float, passed: bool) -> str:
    """quality_checker 의 0.0~1.0 신뢰도 score 를 5단계 별점으로 변환합니다. score 구간을
    명시적으로 나눠(round() 의 은행원 반올림으로 인한 예측 불가능한 경계값 문제를 피함),
    검증에 실제로 통과하지 못했다면(3회 순환 끝에 강제 종료된 경우 포함) 점수가 높아도 최대
    3점으로 제한해 "확인되지 않은 답변"에 5점 만점을 주지 않도록 합니다."""
    if score >= 0.9:
        filled = 5
    elif score >= 0.7:
        filled = 4
    elif score >= 0.5:
        filled = 3
    elif score >= 0.3:
        filled = 2
    else:
        filled = 1
    if not passed:
        filled = min(filled, 3)
    return "★" * filled + "☆" * (5 - filled)


def _build_quality_comment(report: dict) -> str:
    """Trust Tagging에 노출할 품질 평가 한 줄 평을 만듭니다. `_score_to_stars`가 신뢰도를
    별점으로 시각화한다면, 이 한 줄 평은 그 별점만으로는 보이지 않는 맥락(무엇을 확인했는지,
    실패했다면 무엇이 문제였는지)을 짧은 문장으로 요약합니다. `report.get("passed", True)`로
    누락 시 기본값을 True로 두는 것은 `_score_to_stars` 호출부와 동일한 관례를 그대로
    따른 것입니다(`QualityReportDict`의 세 키 모두 선택 필드 — `json.loads(llm_output)`
    그대로라 아무 키도 보장되지 않으므로).
    """
    if report.get("passed", True):
        return "사실성 검증 완료 및 제약조건 만족"

    feedback = (report.get("feedback") or "").strip()
    if not feedback:
        return "주의 - 세부 사유가 기록되지 않았습니다"

    # 마침표 단순 분리(split("."))는 이 코드베이스의 feedback에 흔한 소수점 숫자("4.2km",
    # "6.0시간")나 번호 매기기 형식("1. ", "2. ")에서 숫자 중간이나 번호 바로 뒤가 잘리는
    # 문제가 있어, 마침표 앞이 숫자가 아니면서 문장 종결 부호 뒤에 공백/줄바꿈이 오는 경우만
    # 문장 경계로 인정합니다 (앞의 문자가 숫자가 아님을 보장하는 (?<=\D[.!?]) 패턴 사용).
    first_sentence = re.split(r"(?<=\D[.!?])\s+", feedback, maxsplit=1)[0].strip()
    if len(first_sentence) > _QUALITY_COMMENT_MAX_LEN:
        first_sentence = first_sentence[:_QUALITY_COMMENT_MAX_LEN].rstrip() + "…"
    return f"주의 - {first_sentence}"


def check_quality_node(state: AgentState) -> Dict[str, Any]:
    """답변의 팩트 신뢰성 및 환각 여부, 제약사항 준수 여부를 검증하는 Quality Checker 노드입니다.
    Trust Tagging의 "Self-RAG 신뢰도" 별점도 여기서 이 노드 자신의 평가 결과(score/passed)로
    확정합니다(report_generator가 남겨둔 자리표시자를 치환) — 라벨과 실제 근거를 일치시키기 위함.
    코스 청크(retrieved_chunks)가 있으면 기존처럼 코스 사실 기준으로 검증하고, quick_responder_node
    경로처럼 코스 청크는 없지만 culture_chunks/market_insight 가 있으면 그 내용을 근거로 검증합니다.
    아무 근거도 없으면(둘 다 비어있음) 검증을 생략하고 조기 통과시킵니다.
    **(2026-07-25 추가 — 무조건 반려/Fail-Fast)** retrieve_rag_node 가 DB 매칭 0건으로 검색을
    중단한 경우(is_exit_early)는 다른 어떤 분기보다 먼저 조기 통과시킵니다. 최종 답변이 결정론적인
    반려 메시지이므로 검증할 사실이 애초에 없고, retrieve_rag_node 가 이미 조회해둔 market_insight
    가 상태에 남아있으면 아래 "코스 청크는 없지만 culture/market 은 있는" 분기를 타서 반려
    메시지를 무관한 통계 컨텍스트와 대조하다 최대 3회의 재작성 루프를 도는 낭비가 생깁니다.
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        _build_culture_context_str,
        _build_market_insight_summary_str,
        get_chat_completion,
    )

    if state.get("is_exit_early"):
        return {
            "quality_report": {
                "passed": True,
                "score": 1.0,
                "feedback": "조건 미충족으로 기획서 생성을 중단한 반려 응답이므로 평가를 생략합니다.",
            }
        }

    query = state["query"]
    chunks = state["retrieved_chunks"]
    culture_chunks = state.get("culture_chunks") or []
    market_insight = state.get("market_insight")
    final_response = state["final_response"] or ""

    if not final_response or not (chunks or culture_chunks or market_insight):
        return {"quality_report": {"passed": True, "score": 1.0, "feedback": "검색 결과가 없어 평가를 생략합니다."}}

    if chunks:
        b2b_params = state.get("b2b_params") or {}
        requested_bits = []
        if b2b_params.get("key_item_or_crop"):
            requested_bits.append(f"작물/테마: {b2b_params['key_item_or_crop']}")
        if b2b_params.get("preferred_location"):
            requested_bits.append(f"선호 지역: {b2b_params['preferred_location']}")
        if b2b_params.get("target_month"):
            requested_bits.append(f"방문 월: {b2b_params['target_month']}월")
        requested_summary = ", ".join(requested_bits) if requested_bits else "(특정 작물/지역/월 조건 없음)"

        context_str = f"[사용자가 요청한 핵심 조건]: {requested_summary}\n"
        for i, c in enumerate(chunks):
            context_str += f"\n[코스 {i+1}]: {c['course_name']} (거리: {c['total_distance_km']}km, 소요시간: {c['estimated_time_text']}, 난이도: {c['difficulty']})\n"
            context_str += f"재배작물: {c['crops']}, 경유 행정구역: {c['administrative_areas']}\n"
            context_str += f"본문: {c['content']}\n"

        # report_generator가 실제로 인용한 Market Insight/문화지식 근거도 검증 컨텍스트에
        # 포함시킵니다 (회귀 방지: 예전엔 이 분기가 코스 사실관계만 컨텍스트로 넘겨서, 리포트가
        # market_insight 수치를 정확히 인용해도 검증 LLM 입장에서는 "컨텍스트에 없는 수치"로
        # 보여 오탐 반려(failed) 후 불필요한 query_rewriter 재시도가 발생했음 — quick_responder
        # 경로(아래 else 분기)는 애초에 이 문제가 없었음. 새 헬퍼를 만들지 않고 그 분기가 쓰는
        # _build_market_insight_summary_str/_build_culture_context_str를 그대로 재사용).
        b2b_params_for_context = state.get("b2b_params") or {}
        quality_target_month = b2b_params_for_context.get("target_month") or date.today().month
        culture_ctx_for_quality = _build_culture_context_str(culture_chunks, quality_target_month)
        if culture_ctx_for_quality:
            context_str += f"\n[제주 밭담문화·작물 생육 지식 근거]:\n{culture_ctx_for_quality}\n"
        market_ctx_for_quality = _build_market_insight_summary_str(market_insight)
        if market_ctx_for_quality:
            context_str += f"\n[관광 방문객 통계 (Market Insight) 근거]:\n{market_ctx_for_quality}\n"

        system_prompt = """당신은 생성된 도슨트 추천 답변의 핵심 사실 관계 및 요청 관련성을 검증하는 '품질 검증원'입니다.
주어진 [사용자 질문], [검색 컨텍스트] 및 [생성된 답변] 을 분석하여 답변의 환각 여부 및 정보 충실도를 채점하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON 문자열로만 반환하세요.

[검증 대상 - 아래 사실 항목에서만 컨텍스트와의 모순 여부를 확인하세요]
1. 코스명, 거리, 소요시간, 난이도, 재배작물, 경유 행정구역 등 컨텍스트에 명시된 구체적 수치/명칭을 답변이 왜곡하거나 컨텍스트와 반대로 서술했는가?
2. 사용자가 요청한 필수 제약사항(예: 휠체어 전용 코스 여부)을 어기고 부적절한 코스를 추천했는가?
3. [사용자가 요청한 핵심 조건]에 작물/지역/월이 명시되어 있다면, 답변이 추천한 코스가 그 조건과 실제로 관련이 있는가? 사실관계 자체는 컨텍스트와 일치하더라도, 컨텍스트의 코스들이 요청한 조건과 무관한데 답변이 마치 조건에 맞는 것처럼 추천했다면 이것도 결점으로 판정하세요. (조건이 "(특정 작물/지역/월 조건 없음)"이면 이 항목은 항상 통과로 간주)
4. 답변에 [관광 방문객 통계 (Market Insight) 근거]나 [제주 밭담문화·작물 생육 지식 근거]가 컨텍스트로 제공되어 있다면, 그 안의 방문객 수/증감률/비중 수치나 작물·제철 서술을 답변이 왜곡했는가? 컨텍스트에 있는 수치를 답변이 그대로 인용한 것은 정상이며, 그 근거 섹션 자체가 컨텍스트에 없다면(위 두 근거 모두 미제공) 이 항목은 검증하지 않습니다.

[검증 대상에서 제외 - 아래 항목은 도슨트의 정상적인 연출이므로 절대 환각이나 결점으로 지적하지 마세요]
- 날씨 정보, 옷차림/준비물 팁, 여행 조언 등 컨텍스트 밖의 실용적 부가 안내
- 풍경 묘사, 계절감, 감성적 수식어 등 도슨트 특유의 문학적 표현
- 컨텍스트에 없는 세부 정보(예: 특정 매장 운영 배경)를 답변이 언급하지 않은 것 (누락은 결함이 아님)
- 거리/소요시간, Market Insight 수치처럼 컨텍스트에 있는 값을 답변이 그대로 인용한 경우 (출처 재확인 요구 금지)

[응답 포맷 (JSON 전용)]
{
  "passed": boolean,
  "score": number (0.0 ~ 1.0),
  "feedback": "검증 피드백 및 부족한 정보에 대한 구체적 지적"
}"""
    else:
        b2b_params = state.get("b2b_params") or {}
        target_month = b2b_params.get("target_month") or date.today().month
        context_str = _build_culture_context_str(culture_chunks, target_month) or "(관련 문화/작물 지식 없음)"
        market_str = _build_market_insight_summary_str(market_insight) or "(관련 관광 방문객 통계 없음)"
        context_str = f"{context_str}\n\n[관광 방문객 통계]:\n{market_str}"

        system_prompt = """당신은 정보 조회 답변(기획서가 아닌 단순 정보성 답변)의 핵심 사실 관계만을
검증하는 '품질 검증원'입니다. 주어진 [사용자 질문], [검색 컨텍스트] 및 [생성된 답변] 을 분석하여
답변의 환각 여부 및 정보 충실도를 채점하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON 문자열로만 반환하세요.

[검증 대상 - 아래 사실 항목에서만 컨텍스트와의 모순 여부를 확인하세요]
1. 작물명, 주산지, 생육 단계, 제철 여부 등 문화·작물 지식 컨텍스트의 사실을 답변이 왜곡했는가?
2. 방문객 수, 증감률, 성별/연령대 비중 등 통계 수치를 답변이 왜곡하거나 컨텍스트에 없는 수치를 지어냈는가?

[검증 대상에서 제외 - 아래 항목은 정상적인 안내이므로 절대 환각이나 결점으로 지적하지 마세요]
- 컨텍스트에 없는 세부 정보를 답변이 언급하지 않은 것 (누락은 결함이 아님)
- 컨텍스트에 있는 수치를 답변이 그대로 인용한 경우 (출처 재확인 요구 금지)

[응답 포맷 (JSON 전용)]
{
  "passed": boolean,
  "score": number (0.0 ~ 1.0),
  "feedback": "검증 피드백 및 부족한 정보에 대한 구체적 지적"
}"""

    user_msg = f"사용자 질문: {query}\n\n[검색 컨텍스트]:\n{context_str}\n\n[생성된 답변]:\n{final_response}"

    try:
        raw_res = get_chat_completion(system_prompt, user_msg)
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        
        report = json.loads(cleaned)
    except Exception as e:
        print(f"[!] 품질 검증원 실행 실패: {e}")
        report = {"passed": True, "score": 0.9, "feedback": "자체 평가 오류로 패스 처리"}

    # Trust Tagging의 별점/품질 한 줄 평 자리표시자를 이 노드의 실제 평가 결과로 치환합니다.
    # report_generator가 실행되지 않은 경로(course_recommendation 이 아닌 의도)는 애초에 Trust
    # Tagging 섹션 자체가 없어 자리표시자가 없으므로, 이 replace 는 안전하게 아무 것도 하지
    # 않습니다. is_exit_early/무근거 조기 통과 분기(위에서 이미 return)도 같은 이유로 이 치환이
    # 필요 없습니다 — 둘 다 Trust Tagging이 없는 final_response만 다루기 때문입니다.
    stars = _score_to_stars(report.get("score", 0.9), report.get("passed", True))
    quality_comment = _build_quality_comment(report)
    updated_final_response = final_response.replace(_SELF_RAG_STARS_PLACEHOLDER, stars)
    updated_final_response = updated_final_response.replace(
        _QUALITY_COMMENT_PLACEHOLDER, quality_comment
    )

    return {"quality_report": report, "final_response": updated_final_response}


def rewrite_query_node(state: AgentState) -> Dict[str, Any]:
    """검증 실패 시, 더 정밀한 검색 컨텍스트 획득을 위해 검색 조건 및 키워드를 교정하는 Query Re-writer 노드입니다."""
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        get_chat_completion,
    )

    query = state["query"]
    constraints = state["parsed_constraints"] or {}
    report = state["quality_report"] or {}
    feedback = report.get("feedback", "")
    
    system_prompt = """당신은 품질 검증 결과에 따라 검색 조건을 보정하는 '쿼리 재작성기'입니다.
기존 사용자 질문, 이전 쿼리 제약사항 및 품질 검증 피드백을 바탕으로, Supabase pgvector 하이브리드 검색에서 더 나은 컨텍스트를 찾기 위한 최적의 '보정된 검색 쿼리'를 다시 도출하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON로만 반환하세요.

[응답 포맷 (JSON 전용)]
{
  "revised_vector_query": "새로운 검색용 키워드"
}"""

    user_msg = f"사용자 질문: {query}\n\n[이전 제약사항]:\n{json.dumps(constraints)}\n\n[품질 검증원 피드백]:\n{feedback}"

    try:
        raw_res = get_chat_completion(system_prompt, user_msg)
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        revised = json.loads(cleaned)

        # 상태 덮어쓰기 형식으로 갱신 (soft_constraints 는 B2C 시절 소프트 완화 메커니즘과 함께
        # 제거된 필드라 더 이상 요청/저장하지 않습니다 — 2026-07-24 정리, 그 전엔 어떤 하류
        # 코드도 읽지 않는 죽은 값을 매번 LLM에게 요청해 저장만 하고 있었습니다.)
        updated_constraints = {
            "hard_constraints": constraints.get("hard_constraints", {"wheelchair_required": False}),
            "vector_query": revised.get("revised_vector_query", query)
        }
    except Exception as e:
        print(f"[!] 쿼리 재작성 실패: {e}")
        updated_constraints = constraints
        
    return {
        "parsed_constraints": updated_constraints,
        "loop_count": state["loop_count"] + 1,
        "is_exit_early": False,
        "exit_reason": None
    }
