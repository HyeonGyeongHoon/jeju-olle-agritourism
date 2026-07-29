"""함수 호출 에이전트 루프(tool_agent / tool_executor) 노드.

(2026-07-26 nodes.py 분할 — 로직 변경 없는 순수 코드 이동)
"""

import json
from datetime import date
from typing import Any, Dict

# pyrefly: ignore [missing-import]
from src.agent.prompts.loader import load_prompt
# pyrefly: ignore [missing-import]
from src.agent.state import AgentState
# pyrefly: ignore [missing-import]
from src.models.schema import IntentCategory

# --- 로컬 임포트 규약 (2026-07-26 분할 시 도입, 반드시 유지) ---
# 이 모듈의 노드 함수들은 필요한 헬퍼/클라이언트를 모듈 상단이 아니라 **함수 본문 안에서**
# `from src.agent.nodes import ...` 로 가져옵니다. 분할 이전 nodes.py 에서는 이 이름들이
# 전부 한 모듈의 전역이었기 때문에 테스트가 `patch.object(nodes, "X")` 로 목킹할 수 있었고,
# 그 계약을 그대로 유지하려면 호출 시점에 nodes 패키지 네임스페이스에서 이름을 해석해야
# 합니다(모듈 상단에서 원본 모듈로부터 직접 import 하면 그 목이 가로채지 못함 —
# CLAUDE.md 의 db_service 이관 당시 테스트 7건이 깨진 것과 동일한 함정).
# 동시에 서브모듈 간 상호 참조(reporter <-> quality 등)의 순환 임포트도 함께 해소합니다.


def tool_executor_node(state: AgentState) -> Dict[str, Any]:
    """LLM 에이전트(tool_agent_node)가 요청한 tool_calls 목록을 순회하여
    실제 파이썬 도구(retrieve_visitor_statistics_tool / retrieve_culture_crop_knowledge_tool)를
    다중/병렬 실행하고 결과를 tool_outputs 에 축적하는 Tool Executor 노드입니다.
    tool_depth 카운터를 1 증가시켜 무한 루프를 방어합니다.
    """
    # pyrefly: ignore [missing-import]
    from src.agent.tools import (
        retrieve_culture_crop_knowledge_tool,
        retrieve_visitor_statistics_tool,
    )

    tool_calls = state.get("tool_calls") or []
    depth = (state.get("tool_depth") or 0) + 1
    tool_outputs = list(state.get("tool_outputs") or [])

    for call in tool_calls:
        func_name = call.get("name") or call.get("function", {}).get("name")
        args = call.get("args") or call.get("function", {}).get("arguments") or {}

        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}

        if func_name == "retrieve_visitor_statistics_tool":
            res = retrieve_visitor_statistics_tool(
                region_dong=args.get("region_dong", ""),
                year_month=args.get("year_month"),
                metric=args.get("metric"),
            )
        elif func_name == "retrieve_culture_crop_knowledge_tool":
            res = retrieve_culture_crop_knowledge_tool(
                keyword_or_crop=args.get("keyword_or_crop", "")
            )
        else:
            res = f"[오류] 알 수 없는 도구 호출: {func_name}"

        tool_outputs.append({
            "tool_name": func_name,
            "args": args,
            "result": res,
        })

    print(f"[*] Tool Execution 완결 (depth: {depth}, 실행된 툴 수: {len(tool_calls)})")

    return {
        "tool_outputs": tool_outputs,
        "tool_calls": None,
        "tool_depth": depth,
    }


def tool_agent_node(state: AgentState) -> Dict[str, Any]:
    """사용자의 자연어 요청과 도구 실행 결과(tool_outputs), 품질 검증 피드백을 바탕으로
    도구 추가 호출을 지시하거나 최종 대화식 답변을 작성하는 두뇌 노드입니다.
    tool_depth 가 3회 이상이면 무한 루프를 방지하기 위해 툴 호출을 차단하고 최종 생성을 강제합니다.
    1차 검증 실패 시 quality_report 피드백을 System Prompt 최상단에 주입합니다.
    should_continue 의 "direct_retry"(loop_count < 2 인 info_lookup 경로) 는 이 노드로 되돌아오는데,
    이 노드는 원래 loop_count 를 건드리지 않아 quality_report 가 계속 실패하면 quality_checker와
    이 노드 사이를 무한 반복할 수 있었습니다(loop_count 는 rewrite_query_node 만 증가시키는데,
    direct_retry 경로는 그 노드를 거치지 않으므로). 그래서 품질 검증 실패로 인한 재시도(재답변
    생성) 때만 loop_count 를 1 증가시켜, "direct_retry"가 최대 2회로 확실히 끝나고 그 이후는
    should_continue 가 "rewrite" 경로로 넘어가도록 합니다.
    **(2026-07-25 추가)** `intent_category == "other"` 인 경우, quick_responder_node 가 이미
    서비스 범위 안내(코스 추천 미제공) 최종 답변을 만들어뒀습니다. 이 노드는 b2b_params 에
    key_item_or_crop/preferred_location 등이 있으면 그 값만 보고 자동으로 도구를 호출하는데,
    "other"를 이 검사 없이 그대로 통과시키면 quick_responder_node 의 범위 안내 결정을 무시하고
    엉뚱하게 그 작물/지역 정보를 검색해 실질적으로 "코스 추천"에 가까운 답을 다시 생성해버립니다
    (사용자 요청으로 발견: "당근 코스 추천해줘"). 그래서 "other"는 도구 호출 없이 그대로 종료합니다.
    **(2026-07-25 추가 — 무조건 반려/Fail-Fast)** is_exit_early 도 같은 이유로 조기 종료 대상입니다.
    quick_responder_node 가 만든 반려 메시지("...기획서를 작성할 수 없습니다")를, b2b_params 에
    그대로 남아있는 preferred_location/key_item_or_crop 때문에 이 노드가 통계·작물 도구를 재호출해
    덮어쓰면 반려가 대체 정보 안내로 변질됩니다. 이 두 검사는 반드시 아래 pending_tool_calls 계산
    **보다 먼저** 있어야 합니다(has_grounded_answer 검사가 그 뒤에 있어 죽은 코드가 됐던 과거
    버그와 동일한 함정 — 반려 케이스는 곧 필터 값이 b2b_params 에 살아있는 케이스입니다).
    """
    # pyrefly: ignore [missing-import]
    from src.agent.nodes import (
        _build_course_meta_context_str,
        get_chat_completion,
    )

    if state.get("is_exit_early") and not (state.get("tool_outputs") or []):
        return {"tool_calls": None}

    if state.get("intent_category") == IntentCategory.OTHER.value and not (state.get("tool_outputs") or []):
        return {"tool_calls": None}

    query = state["query"]
    tool_outputs = state.get("tool_outputs") or []
    depth = state.get("tool_depth") or 0
    quality_report = state.get("quality_report")
    loop_count = state.get("loop_count", 0)

    # 1. 이전 검증 지적 피드백 주입 (직행 라우팅 시)
    is_retry_pass = bool(quality_report and not quality_report.get("passed", True))
    feedback_note = ""
    if is_retry_pass:
        feedback_note = (
            f"\n[⚠️ 품질 검증 지적 피드백 (반드시 반영하세요)]:\n"
            f"{quality_report.get('feedback', '수치나 팩트 오류를 수정하세요.')}\n"
        )

    # 2. 도구 실행 결과 컨텍스트 구성
    tools_context_str = ""
    if tool_outputs:
        tools_context_str = "\n[실행된 도구 조회 결과]:\n"
        for i, out in enumerate(tool_outputs):
            tools_context_str += f"\n--- [도구 {i+1}: {out['tool_name']}] ---\n{out['result']}\n"

    # 2-1. 특정 코스가 지목된 질의는 그 코스의 DB 실측 메타데이터를 근거로 함께 넘깁니다.
    # 최종 사용자 답변은 quick_responder_node 가 아니라 이 노드가 쓰는 경우가 많은데(도구를
    # 한 번이라도 호출하면 이 노드가 final_response 를 덮어씀), 예전엔 이 노드의 프롬프트에
    # 도구 결과만 들어가서 코스 실측치가 최종 답변에 전달되지 않았습니다. 게다가 아래 절대규칙
    # 2번("가용 옵션 목록을 대안으로 되물어 안내")이 특정 코스 질의에서는 "대신 다른 지역을
    # 추천"으로 변질돼 코스/지역 추천 금지 정책을 우회하는 경로가 됐습니다(2026-07-25 라이브 QA).
    course_scope_note = ""
    target_course = state.get("target_course")
    if target_course:
        query_for_agro_check = state.get("query") or ""
        agro_query_keywords = [
            "작물", "감귤", "감자", "당근", "마늘", "양파", "무", "파", "배추", "보리", "밭담",
            "농가", "상생", "영농", "수확", "재배", "파종", "체험", "제철", "로컬", "농업"
        ]
        _is_query_agro_related = any(kw in query_for_agro_check for kw in agro_query_keywords)
        
        course_scope_note = f"\n[대상 코스] 이 질문은 '{target_course}' 코스에 대한 것입니다."
        course_meta_context_str = _build_course_meta_context_str(
            state.get("course_meta"), include_crops=_is_query_agro_related
        )
        if course_meta_context_str:
            course_scope_note += (
                f"\n[대상 코스 DB 실측 메타데이터 (적합성·난이도 판단은 이 수치만 근거로 인용)]\n"
                f"{course_meta_context_str}"
            )
        course_scope_note += (
            "\n[내부 지시 사항 - 이 대괄호 라벨과 아래 문장은 답변에 절대 옮기거나 언급하지 "
            "말 것] 이 질문은 특정 코스에 대한 것이므로 다른 코스나 다른 지역을 대안으로 "
            "추천하지 마세요. 도구 결과가 특정 지역/기간을 조회할 수 없다는 안내라면 그 사실만 "
            "간단히 밝히고, 위 실측 메타데이터를 근거로 답하세요. 데이터에 없는 지형·체력 판단을 "
            "추측으로 덧붙이지 마세요.\n"
        )

    # 3. max depth (3회) 도달 시 방어 조치: 더 이상 도구를 부르지 못하게 제한
    if depth >= 3:
        system_prompt = load_prompt("tool_agent_fallback.md")
        user_msg = f"사용자 질문: {query}\n{feedback_note}{course_scope_note}{tools_context_str}"
        answer = get_chat_completion(system_prompt, user_msg)
        result = {
            "docent_answer": answer,
            "final_response": answer,
            "tool_calls": None,
        }
        if is_retry_pass:
            result["loop_count"] = loop_count + 1
        return result

    # 4. 일반 도구 연동 및 대화 답변 작성
    # 도구 결과가 이미 존재하는 경우 이를 요약 정리해 응답하며, 결과가 비어있는 초기 진입 시
    # 파라미터가 없으면 도구를 호출할 수 있도록 인자 결정 유도/기본 조회를 수행합니다.
    if not tool_outputs:
        # 1차 진입 시: 질문 분석 후 툴 호출 결정
        b2b_params = state.get("b2b_params") or {}
        preferred_loc = b2b_params.get("preferred_location")
        key_crop = b2b_params.get("key_item_or_crop")
        market_query = b2b_params.get("market_location_query") or {}

        has_grounded_answer = bool(
            state.get("course_meta") or state.get("culture_chunks") or state.get("market_insight")
        )
        is_other_intent = state.get("intent_category") == "other"
        if not is_retry_pass and (state.get("skip_quality_check") or is_other_intent or (has_grounded_answer and state.get("final_response"))):
            # quality_checker(Self-RAG LLM 검증)를 건너뜁니다.
            # 이미 상류 노드에서 반려/외부 질문으로 처리되었거나 근거 기반 답변이 구성되었으므로 재검증이 불필요합니다.
            return {"tool_calls": None, "skip_quality_check": True}

        pending_tool_calls = []
        if market_query.get("metric") or preferred_loc:
            loc = preferred_loc or "성산읍"
            # market_location_query(연/월)이 없으면 일반 질의가 직접 지정한 target_month로
            # 보완합니다(연도가 없으면 올해로 간주 - resolve_market_location_node와 동일 관례).
            # 이게 없으면 "OO동 3월 방문객 수는?"처럼 target_month만 있는 질의가 이 경로를 타게
            # 됐을 때(quick_responder가 못 찾은 예외 케이스) year_month=None으로 호출되어 도구가
            # 최신월로 조용히 대체해버립니다.
            year = market_query.get("year")
            month = market_query.get("month") or b2b_params.get("target_month")
            if year and month:
                ym = f"{year}-{month:02d}"
            elif month:
                ym = f"{date.today().year}-{month:02d}"
            else:
                ym = None
            pending_tool_calls.append({
                "name": "retrieve_visitor_statistics_tool",
                "args": {
                    "region_dong": loc,
                    "year_month": ym,
                    "metric": market_query.get("metric") or "total_visitors",
                }
            })
        if key_crop:
            pending_tool_calls.append({
                "name": "retrieve_culture_crop_knowledge_tool",
                "args": {"keyword_or_crop": key_crop}
            })

        # 도구를 큐잉하기 전에 먼저 확인합니다: quick_responder_node 가 이미 이 질의의 근거를
        # 확보해 답변을 만들어뒀거나, 호출할 도구(pending_tool_calls)가 전혀 없고 기존 답변(final_response)이
        # 존재한다면 굳이 텅 빈 컨텍스트로 LLM 을 다시 불러 답변을 재생성(환각 오염 우려)하지 않고 그대로 종료합니다.
        # (단, RAG/DB 조회 실패로 인한 거절 답변인 "찾지 못했습니다" 문구가 포함된 경우는 일반 지식 답변으로 재생성하도록 가드를 우회합니다.)
        is_declined = "찾지 못했습니다" in (state.get("final_response") or "")
        if not is_retry_pass and not pending_tool_calls and state.get("final_response") and not is_declined:
            return {"tool_calls": None}

        if pending_tool_calls:
            return {"tool_calls": pending_tool_calls}

    # 도구 결과를 바탕으로 답변 생성
    system_prompt = load_prompt("tool_agent.md")

    user_msg = f"사용자 질문: {query}\n{feedback_note}{course_scope_note}{tools_context_str}"
    answer = get_chat_completion(system_prompt, user_msg)

    result = {
        "docent_answer": answer,
        "final_response": answer,
        "tool_calls": None,
    }
    if is_retry_pass:
        result["loop_count"] = loop_count + 1
    return result
