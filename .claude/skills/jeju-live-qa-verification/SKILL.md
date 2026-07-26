---
name: jeju-live-qa-verification
description: "jeju-olle-docent 실 서버(Uvicorn/FastAPI)를 띄워 자연어 질의를 라이브로 검증할 때 반드시 로드. 서버 기동/종료 절차, SSE 스트림 파싱 스크립트, 노드 함수 직접 진단 패턴, Gate B(실 API 호출) 주의사항을 담고 있다. 'QA 시나리오 실행', '실제로 되는지 확인', '라이브 검증' 시 트리거."
---

# Jeju Live QA Verification

`docs/qa_test_scenarios.md`의 시나리오나 새로 발견된 회귀를 실제 서버로 검증하는 절차.
유닛 테스트만으로는 못 잡는 경계면 버그(라우팅↔노드, 파싱↔소비)를 찾아내는 게 목적이다 —
`retrieve_rag_node`의 기존 유닛 테스트가 전부 `target_course=None`으로만 검증하고 있어서, 실제
"1코스" vs "10-1코스" 오매칭 버그는 유닛 테스트를 다 통과한 채로 숨어 있다가 라이브 검증에서만
드러났다.

## 서버 기동/종료

**기동:**
```bash
cd <project_root>
(python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 > /tmp/uvicorn.log 2>&1 &)
sleep 3
curl -s http://localhost:8000/health
```

**코드 수정 후 재시작 (필수 — uvicorn은 자동 리로드하지 않는다):**
```bash
netstat -ano | grep ":8000" | grep LISTENING   # PID 확인
taskkill //PID <PID> //F                        # Windows
sleep 2
# 위 기동 커맨드 재실행
```
재시작을 빼먹고 "이미 고쳤는데 왜 안 되지"라고 헤매는 경우가 실제로 있었다 — 코드를 고친 뒤
검증할 때는 항상 서버를 재시작했는지 먼저 확인하라.

## SSE 질의 실행 스크립트

`/api/v1/report/generate`는 SSE로 응답한다. 아래 패턴으로 파싱한다(반복 사용되는 헬퍼이므로
그대로 재사용하라):

```python
import json, requests

def call(query):
    r = requests.post('http://localhost:8000/api/v1/report/generate',
                       json={'query': query}, stream=True, timeout=180)
    r.encoding = 'utf-8'
    event_type = 'message'; data_lines = []; report = None; error = None; nodes = []
    for line in r.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line == '':
            if data_lines:
                d = json.loads('\n'.join(data_lines))
                if event_type == 'node_progress':
                    nodes.append(d.get('node'))
                elif event_type == 'report':
                    report = d.get('report')
                elif event_type == 'error':
                    error = d.get('message')
            event_type = 'message'; data_lines = []
            continue
        if line.startswith('event:'):
            event_type = line[6:].strip()
        elif line.startswith('data:'):
            data_lines.append(line[5:].strip())
    return report, error, nodes
```

`nodes` 리스트(어떤 노드를 거쳤는지)를 항상 같이 수집하라 — 최종 답변만 봐서는
`course_recommendation` 전체 파이프라인을 탄 건지 `quick_responder` 경량 경로를 탄 건지 알 수
없는데, 이 구분 자체가 버그 진단의 핵심 단서인 경우가 많다.

**콘솔 출력 시 한글 인코딩 주의**: Windows Bash에서 Python 결과를 직접 `print`하면 한글이
깨질 수 있다. 결과를 UTF-8 JSON 파일로 저장한 뒤 파일을 읽는 방식을 써라(`print` 대신
`json.dump(..., ensure_ascii=False)` 후 Read 도구로 확인).

## 중간 상태 직접 진단

전체 파이프라인을 다시 돌리지 않고, 특정 노드의 실제 출력만 빠르게 확인하고 싶을 때:

```python
import json
from src.agent.nodes import classify_intent_node, parse_intent_node

state = {'query': '<검증할 질의>'}
r1 = classify_intent_node(state)
state2 = {**state, **r1}
r2 = parse_intent_node(state2)
# r1['intent_category'], r1['target_course'], r2['b2b_params'] 확인
```

`classify_intent_node`/`parse_intent_node`는 실제 LLM을 호출하므로(목킹 안 함), 비결정성이
의심되면 3회 반복해 값이 흔들리는지 확인하라. 같은 질의를 3번 돌려서 값이 매번 다르면, 그건
LLM 프롬프트를 아무리 다듬어도 근본적으로 해결 안 되는 경우가 많다 — `jeju-node-developer`에게
"규칙 기반 사전 필터가 필요하다"고 전달하라.

## Gate B — 실 API 호출 비용/리스크 인지

- 시나리오가 10개 이상이면 사전에 대략적인 개수와 예상 소요 시간(질의당 10~50초, 재작성 루프
  발생 시 최대 1~2분)을 알리고 진행한다.
- **`.env`의 실 자격 증명(SUPABASE_KEY 등)을 직접 무효화해서 장애를 재현하지 마라.** 의도한
  것보다 훨씬 넓은 범위가 함께 망가진다 — "culture DB 조회만 실패시키고 싶다"고 SUPABASE_KEY를
  건드리면 코스 검색/Market Insight까지 전부 죽어서, 정작 검증하려던 "그 부분만 실패했을 때
  나머지는 정상 동작하는가"를 확인할 수 없게 된다. 이런 선택적 장애 재현은 유닛 테스트 레벨의
  정밀 목킹(해당 함수만 예외를 던지게)으로 대신하고, `jeju-node-developer`에게 위임하라.
- 서버는 검증이 끝나면 종료한다(`taskkill`). 켜둔 채로 세션을 넘기지 않는다.

## 결과 판정 시 주의

- **최종 응답만 보지 말고 `nodes` 시퀀스도 같이 판정하라.** 예상보다 `query_rewriter`가
  여러 번 등장하면 quality_checker 재검증에 실패하고 있다는 뜻이다 — 답변 자체는 그럴듯해
  보여도 Trust Tagging의 Self-RAG 별점을 확인하면 낮게 매겨져 있을 수 있다(`★☆☆☆☆` 등).
- 같은 질의를 반복 실행했을 때 매번 다른 코스/카테고리가 나오면 "일단 통과"로 넘기지 말고
  비결정성 자체를 버그로 리포트하라 — 특히 정책상 결정론적이어야 하는 요청(서비스 범위 밖
  거절 등)이라면 더더욱.
