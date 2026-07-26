---
name: jeju-olle-harness
description: "jeju-olle-docent(제주 영농-관광 상생 상품 기획서 자동 생성 시스템) 프로젝트의 LangGraph 에이전트 코드 개발을 조율하는 오케스트레이터. 버그 수정, 기능 추가, 리팩토링, QA 시나리오 실행, 라이브 서버 검증, 문서 동기화 등 이 프로젝트의 실질적인 개발 작업 요청 시 사용. 예: '~노드 버그 고쳐줘', '~로직 추가해줘', 'QA 시나리오 실행해줘', '실제로 되는지 확인해줘', '문서 최신화해줘', '회고 작성해줘'. 후속 작업(이전 수정 보완, 재검증, 추가 버그 수정, 문서만 다시 갱신 등)에도 반드시 이 스킬을 사용. 단, 코드를 스스로 이해하기 위한 단순 질문('이 함수가 뭐 하는거야?', '~로직이 있나?')은 이 스킬 없이 직접 답변한다."
---

# Jeju Olle Harness — 개발 팀 오케스트레이터

jeju-olle-docent 프로젝트의 실질적인 코드 변경 작업(버그 수정, 기능 추가, 검증, 문서화)을
3인 에이전트 팀으로 조율한다.

## 실행 모드: 에이전트 팀 (생성-검증 패턴)

이 프로젝트의 실제 개발 사이클은 "구현 → 라이브 검증 → (문제 발견 시 재구현) → 문서화"가
반복되는 생성-검증(Producer-Reviewer) 루프다. 유닛 테스트만으로는 못 잡는 경계면 버그가
실 서버 검증에서만 드러나는 경우가 많았으므로, 구현자와 검증자가 `SendMessage`로 실시간
피드백을 주고받는 팀 모드가 이 도메인에 적합하다.

## 에이전트 구성

| 팀원 | 에이전트 타입 | 역할 | 스킬 | 출력 |
|------|-------------|------|------|------|
| `jeju-node-developer` | 커스텀 (`.claude/agents/jeju-node-developer.md`) | `src/agent/` 코드 구현 + pytest 작성 | `jeju-node-development` | 수정된 `.py` 파일 + 테스트 |
| `jeju-live-qa` | 커스텀 (`.claude/agents/jeju-live-qa.md`, general-purpose 기반) | 실 서버 라이브 검증, 경계면 불일치 발견 | `jeju-live-qa-verification` | 검증 리포트 |
| `jeju-docs-sync` | 커스텀 (`.claude/agents/jeju-docs-sync.md`) | CLAUDE.md/docs/ 동기화 | `jeju-docs-sync` | 갱신된 문서 |

## 워크플로우

### Phase 0: 컨텍스트 확인 (후속 작업 지원)

1. `_workspace/` 디렉토리 존재 여부 확인.
2. 사용자 요청 성격 판별:
   - **단순 질문**("이 로직이 있나?", "이 함수가 뭐 하는거야?") → 팀을 만들지 않고 직접 코드를
     읽어 답변한다. 이 오케스트레이터는 실제 변경/검증/문서화 작업에만 사용한다.
   - **`_workspace/` 미존재 + 실질적 개발 요청** → 초기 실행. Phase 1로.
   - **`_workspace/` 존재 + "이전 수정 보완/재검증/문서만 다시" 등 부분 요청** → 부분 재실행.
     해당 역할의 팀원만 다시 호출하고, `_workspace/`의 이전 산출물(버그 리포트, 검증 결과)을
     프롬프트에 포함해 이어서 작업하게 한다.
   - **`_workspace/` 존재 + 완전히 새로운 요청** → 기존 `_workspace/`를
     `_workspace_{YYYYMMDD_HHMMSS}/`로 이동한 뒤 Phase 1로.

### Phase 1: 준비
1. 사용자 요청에서 대상 노드/파일, 증상(있다면), 재현 질의(있다면)를 파악한다.
2. `_workspace/` 생성(신규 실행 시) 또는 유지(부분 재실행 시).
3. 요청 규모 판단: 한 노드의 단순 수정이면 팀 없이 `jeju-node-developer`만 서브 에이전트로
   호출해도 된다(Phase 2-대안 참고) — 팀 구성 오버헤드가 정당화되는 건 "구현→검증에서 새 버그
   발견→재구현"이 반복될 가능성이 있는 경우다.

### Phase 2: 팀 구성

```
TeamCreate(
  team_name: "jeju-olle-dev-team",
  members: [
    { name: "jeju-node-developer", agent_type: "jeju-node-developer", model: "opus",
      prompt: "<사용자 요청 요약, 대상 파일, 알려진 증상>" },
    { name: "jeju-live-qa", agent_type: "jeju-live-qa", model: "opus",
      prompt: "<검증할 시나리오/질의, 기대 동작>" },
    { name: "jeju-docs-sync", agent_type: "jeju-docs-sync", model: "opus",
      prompt: "대기 — jeju-live-qa의 검증 완료 통지를 받으면 문서 동기화 시작" }
  ]
)

TaskCreate(tasks: [
  { title: "코드 수정 구현", description: "<상세>", assignee: "jeju-node-developer" },
  { title: "라이브 검증", description: "<재현 질의>", assignee: "jeju-live-qa",
    depends_on: ["코드 수정 구현"] },
  { title: "문서 동기화", description: "검증 완료된 변경 사항 반영", assignee: "jeju-docs-sync",
    depends_on: ["라이브 검증"] }
])
```

**Phase 2-대안 (경량 서브 에이전트 모드)**: 요청이 명백히 단순하고(한 함수의 명확한 버그, 재현
불필요) 반복 루프가 예상되지 않으면, 팀을 만들지 않고 `Agent(subagent_type: "jeju-node-developer")`
를 직접 호출한다. 이후 검증이 필요해지면 그때 팀으로 전환해도 된다.

### Phase 3: 구현 ↔ 검증 루프

**실행 방식**: 팀원 자체 조율 (SendMessage 기반 생성-검증 루프)

1. `jeju-node-developer`가 구현 완료 시 `jeju-live-qa`에게 SendMessage로 "이 질의로
   검증해달라"고 구체적인 재현 질의와 함께 요청한다.
2. `jeju-live-qa`는 서버를 (재)시작하고 실제로 검증한 뒤:
   - 통과 → `jeju-node-developer`와 리더에게 통과 보고, `jeju-docs-sync`에게 확정 통지
   - 새 버그 발견 → `jeju-node-developer`에게 재현 질의 + 실제 결과 + 원인 추정 위치를 구체적으로
     전달, 루프 반복 (Phase 4의 재시도 한도 참고)
3. 리더는 `TaskGet`으로 진행 상황을 모니터링하고, 팀원이 유휴 상태가 되면 개입한다.

**산출물 저장**: `_workspace/03_qa_report.md`(검증 리포트), `_workspace/03_bugs_found.md`(발견된
추가 버그, 있다면 누적 기록).

### Phase 4: 문서화
1. `jeju-live-qa`의 확정 통지를 `TaskGet`/메시지로 확인.
2. `jeju-docs-sync`가 CLAUDE.md/docs/ 갱신 (동시에 여러 문서를 건드릴 수 있음 — 이 안에서는
   병렬 처리, 팀 통신 불필요).
3. `CLAUDE.md`가 수정됐다면 리더가 사용자에게 "커밋이 필요하다"고 명시(자동 커밋 금지 — 이
   프로젝트는 사용자가 명시적으로 요청할 때만 커밋/푸시).

### Phase 5: 정리
1. 팀원들에게 종료 요청 (SendMessage).
2. `TeamDelete`.
3. `_workspace/` 보존.
4. 사용자에게 요약 보고: 무엇을 고쳤는지, 어떻게 검증했는지(재현 질의 포함), 어떤 문서를
   갱신했는지, 커밋 필요 여부.

## 데이터 흐름

```
[리더] → TeamCreate → [jeju-node-developer] ←SendMessage→ [jeju-live-qa]
                              │                                   │
                       수정된 .py/tests                     검증 리포트
                              │                                   │
                              └───────── 확정 통지 ────────────────┘
                                              ↓
                                       [jeju-docs-sync]
                                              ↓
                                    CLAUDE.md / docs/ 갱신
                                              ↓
                                        [리더: 요약 보고]
```

## 에러 핸들링

| 상황 | 전략 |
|------|------|
| `jeju-live-qa`가 재현 실패(3회 반복 후에도) | "재현 실패"로 기록하고 다음 작업 진행, 사용자에게 명시 |
| 구현↔검증 루프가 3회 이상 반복 | 리더가 개입 — 사용자에게 현황 보고 후 계속 여부 확인 (무한 루프 방지) |
| `jeju-live-qa`가 실 서버 장애 재현을 요청받음 | `.env` 자격 증명 무효화 대신 유닛 테스트 정밀 목킹으로 전환 지시 |
| 팀원 1명 응답 없음 | 리더가 SendMessage로 상태 확인 → 재시작 또는 해당 역할 서브 에이전트로 대체 |
| Gate B(실 API 호출) 규모가 큼(시나리오 10개+) | 진행 전 사용자에게 개수/예상 비용 고지 |

## 테스트 시나리오

### 정상 흐름
1. 사용자: "휠체어가 필요한데 코스가 없으면 대체 추천 대신 이유만 답하게 해줘"
2. Phase 1: 대상 노드(`retrieve_rag_node`, `generate_report_node`) 식별
3. Phase 2: 3인 팀 구성, 작업 3건 등록
4. Phase 3: `jeju-node-developer`가 구현 → `jeju-live-qa`에게 "휠체어가 필요한 2코스 기획서
   만들어줘"로 검증 요청 → 검증 통과
5. Phase 4: `jeju-docs-sync`가 CLAUDE.md/project_architecture.md 갱신
6. Phase 5: 팀 정리, "CLAUDE.md 수정됨 — 커밋 필요" 보고

### 에러 흐름
1. Phase 3에서 `jeju-live-qa`가 검증 중 새로운 경계면 버그 발견(예: 수정한 필터가 다른 케이스를
   오매칭)
2. `jeju-node-developer`에게 재현 질의 + 원인 위치 전달
3. `jeju-node-developer`가 재수정, 재검증 요청
4. 2회째도 유사 증상 재현 → 리더 개입, 사용자에게 "이 접근 방식 자체를 재검토할지" 확인
5. 사용자 승인 후 계속 진행 또는 범위 축소하여 Phase 4로
