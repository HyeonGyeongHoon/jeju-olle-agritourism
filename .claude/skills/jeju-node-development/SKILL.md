---
name: jeju-node-development
description: "jeju-olle-docent의 src/agent/ 코드(nodes.py, graph.py, router.py, state.py)를 수정하거나 확장할 때 반드시 로드. 이 프로젝트의 fail-closed/fail-soft 설계 원칙, 테스트 작성 패턴(가짜 Supabase 클라이언트 픽스처), 흔한 경계면 버그 패턴을 담고 있다. LangGraph 노드 구현, 하드 제약 처리, 라우팅 로직 수정 시 트리거."
---

# Jeju Node Development

jeju-olle-docent는 자연어 질의를 B2B 관광 상품 기획서로 변환하는 11노드 LangGraph 상태 그래프다.
핵심 로직은 `src/agent/nodes.py` 하나에 몰려 있고(1500줄+), `graph.py`가 라우팅을,
`router.py`가 의도 분류를 담당한다. 이 스킬은 이 코드베이스를 고칠 때 반드시 알아야 하는,
과거 세션들에서 실제로 겪은 실수와 그 교훈을 담는다.

## 설계 철학 — Fail-Closed vs Fail-Soft

이 둘을 혼동하면 정반대 방향으로 잘못 고치게 된다.

**Fail-Closed** (확신 없으면 아예 하지 않는다):
- `resolve_market_location_node`: 올레 코스 경유 행정동 후보를 확인할 수 없으면, 무제한 검색으로
  풀어주는 대신 통계 기반 지역 자동 선정 자체를 건너뛴다. "확신 없이 무언가를 하는 것"보다
  "확신 없으면 안 하는 것"이 안전하기 때문.
- `parse_intent_node`: LLM 파싱이 실패해도 `wheelchair_required`를 조용히 `False`로 초기화하지
  않고 원본 질의의 "휠체어" 키워드로 재확인한다.
- **하드 제약(휠체어 등)이 실존하는 대상에서 실패로 확인되면**, 다른 대상으로 조용히 대체하지
  않고 왜 안 되는지 정직하게 답하고 종료한다(2026-07-25 정책 — 사용자 명시 요청).

**Fail-Soft** (조건이 안 맞으면 그 조건만 풀고 계속 진행):
- `_filter_course_ids_by_location`/`_filter_course_ids_by_crop`/`_filter_course_ids_by_target_course`:
  지역/작물/코스명이 겹치는 후보가 없으면, 그 조건만 해제하고 전체에서 계속 검색하되
  `fallback_applied`/`fallback_reason`에 사유를 남겨 리포트 각주로 노출한다.
- **단, 하드 제약 위반이 원인으로 확인되면 fail-soft가 아니라 위 fail-closed 정책이 우선한다** —
  "겹치는 코스가 없다"와 "그 코스는 있지만 하드 제약을 못 만족한다"는 다른 상황이다.

판단 기준: 새 필터/조건을 추가할 때 "이게 안전 관련 하드 제약인가, 아니면 검색 범위를 좁히는
소프트 힌트인가"를 먼저 자문하라.

## `fallback_applied`/`fallback_reason`은 죽은 필드가 아니다

`AgentState`에 이 두 필드가 있는데, 예전 B2C 시절 소프트 완화(시간/거리/난이도) 메커니즘을 위해
만들어졌다가 그 메커니즘 자체는 삭제됐다. **하지만 필드 자체는 지역/작물/코스명 하드 필터의
fail-soft 폴백 용도로 재사용 중인 살아있는 필드다.** 이 필드를 "죽었다"고 잘못 서술한 문서
때문에 실제 QA 리뷰에서 혼란이 있었다 — 코드를 직접 확인하지 않고 문서/주석만 믿지 마라.

## LLM 분류의 비결정성을 인지하라

`router.py`의 `route_intent()`는 같은 입력에도 실행마다 다른 카테고리를 반환할 수 있다(실제
관측: "당근 코스 추천해줘"가 실행별로 `other`/`course_recommendation`을 오감). 서비스 정책상
반드시 결정론적으로 처리돼야 하는 경계 사례(예: "기획서 요청이 아닌 순수 코스 추천은 거절")를
발견하면, LLM 프롬프트를 아무리 다듬어도 완전히 신뢰할 수 없다는 것을 전제하고 `router.py`의
`_rule_based_precheck()`에 규칙을 추가해 LLM 호출 전에 확정하라. 이 방식은 비용/지연도
줄인다(해당 입력은 LLM을 아예 안 부름).

규칙을 추가할 때는 오탐(false positive)을 막는 대조군도 같이 넣어라 — 예: "코스 추천" 규칙은
"기획서"/"기획안" 같은 결과물 생성 키워드가 있으면 트리거하지 않게 했다.

## 노드가 빈 상태 변경(`{}`)을 반환할 수 있다면

LangGraph는 노드가 `{}`를 반환하면 스트림 이벤트 값을 `None`으로 넘긴다(dict가 아니라!). 이
그래프에서 그런 노드를 추가/수정할 때는 `src/main.py`의 SSE 처리 코드가
`node_output = event[node_name] or {}` 방어를 유지하고 있는지 확인하라. 이 가드 없이
`.get("final_response")`를 호출하면 `AttributeError`로 SSE 응답 전체가 죽는다(실제 프로덕션
장애 사례).

## 검색/필터링 텍스트 매칭 시 완전 일치 vs 부분 일치를 신중히 선택하라

`_filter_course_ids_by_target_course`가 원래 `in`(부분 문자열 포함) 검사였을 때, "1코스"가
"10-1코스"/"7-1코스"/"14-1코스"/"18-1코스"처럼 접두사+하이픈+번호로 끝에 "1코스"를 우연히
포함하는 전혀 다른 코스와 오매칭됐다. **코스명처럼 유한하고 정확히 알려진 값 집합**은 완전
일치(`==`)를 쓰고, **행정구역명처럼 표기가 여러 단위로 나뉘는 값**(법정리 vs 행정동)은 부분
일치가 적절하다. 새 매칭 로직을 추가하기 전에 실제 데이터에 비슷한 접두사/접미사 충돌 패턴이
있는지 확인하라(예: `data/` 하위 코스명 목록).

## 노드가 다른 노드의 결정을 뒤엎지 않게 하라

`quick_responder_node`가 "이 요청은 서비스 범위 밖"이라고 결정해도, 그 뒤에 오는
`tool_agent_node`가 `b2b_params`만 보고 독자적으로 도구를 호출해버리면 앞선 결정이
무의미해진다(실제 발견 사례). 그래프에 여러 노드가 같은 `state`의 파생 값(`b2b_params` 등)을
각자 해석해 행동을 결정하는 구조라면, 상류 노드의 결정(예: `intent_category`)을 하류 노드도
반드시 다시 확인하게 하라.

## 테스트 작성 패턴

### Supabase 클라이언트 목킹
`get_supabase_client()`를 `patch.object(nodes, "get_supabase_client", return_value=client)`로
목킹한다. `client`는 실제 체이닝 호출 패턴(`.table().select().eq().execute()` 등)을 모사하는
가짜 객체가 필요하다 — 단순 `MagicMock()`으로 충분한 경우도 있지만(단일 호출 패턴), 같은
`courses` 테이블에 여러 다른 `select` 컬럼 조합으로 호출되는 함수(`retrieve_rag_node` 등)는
`select()`에 전달된 컬럼 문자열로 분기하는 전용 `_Fake*Table` 클래스를 작성하라
(`tests/test_retrieve_rag_node.py`의 `_FakeCoursesTable*` 클래스들 참고).

### LLM/임베딩 목킹
`patch.object(nodes, "get_chat_completion", return_value=...)`,
`patch.object(nodes, "get_solar_embedding", return_value=[0.1])`로 목킹한다. 실제 API를 호출하는
통합 테스트(`test_agent_graph.py`, `test_external_apis.py` 일부)는 의도적으로 목킹하지 않는
예외이니, 새 통합 테스트를 추가하는 게 아니라면 반드시 목킹하라.

### 회귀 테스트 명명
버그를 고칠 때는 테스트 함수명에 시나리오를 명시하고(`test_x_does_not_y_when_z`), 독스트링에
"회귀 방지: 예전엔 ~했는데 ~한 문제가 있었다"를 적는다. 이 관례 덕분에 나중에 왜 이 테스트가
있는지 코드만 보고도 알 수 있다.

## Gate C — 완료 전 필수 확인
```
python -m pytest -q
ruff check .
```
새로 추가한 파일/함수에 대해서만이 아니라 전체 스위트를 돌려라 — 이 프로젝트는 노드 간 상태
공유가 많아 한 노드의 변경이 다른 노드의 테스트를 깨뜨리는 경우가 실제로 여러 번 있었다.
