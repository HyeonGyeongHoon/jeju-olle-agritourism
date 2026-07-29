# 제주 영농-관광 상생 상품 기획서 도슨트 (Jeju Olle B2B Docent)

제주올레길 코스 데이터, 밭담문화·작물 생육 지식, 로컬 상점 정보, 방문객 통계 빅데이터를 결합해 지자체 담당자·여행사 기획자를 위한 **B2B 관광 상품 기획서를 자연어 질의만으로 자동 생성** 하는 LangGraph 기반 에이전트 서비스입니다.

> 원래는 탐방객 대상 대화형(B2C) 코스 추천 챗봇으로 시작했으나, 멘토링 피드백을 반영해 지자체/여행사 실무자를 위한 B2B 기획서 자동 생성 도구로 방향을 전환했습니다. 도메인(제주 올레길 + 지역 작물/상점)과 데이터 인프라는 그대로 유지한 채 출력 형태와 타겟만 바뀌었습니다.

## 1. 프로젝트 개요
"가을에 당근밭 풍경 보면서 걷기 좋은 평지 코스 기획서 써줘" 같은 자연어 질의를 입력하면, 약 15~30초 내에 아래 5단 구조의 실무용 B2B 기획서를 생성합니다.

1. 📊 **B2B 상품 개요 & 스펙** — 상품명/타겟/운용시간/단가범위/USP
2. 📍 **구간별 타임라인 표** — 실제 코스 세부구간(km) 데이터 기반 도슨트 포인트 + 현장 체크리스트
3. ☕ **로컬 상생 제휴 아이디어 표** — 매장명 노출 없이, 지역 상점 성격에서 착안한 협업 컨셉 제안
4. 🌤️ **기후 리스크 및 Plan A/B 우회 동선**
5. 🛡️ **Trust Tagging** — 데이터 출처 및 신뢰도 표기

## 2. 핵심 특징
- **LangGraph 9노드 자율 순환 에이전트**: 의도 분류 → 안전 평가 → 하이브리드 검색 → 리포트 합성 → 로컬 아이디어 생성 → Self-RAG 품질 검증(실패 시 최대 3회 자동 재작성) / 단순 질의는 quick_responder → tool_agent 경량 경로로 처리
- **방문객 통계 빅데이터 기반 지역 자동 추론**: 읍·면·동별 탐방객 통계 데이터를 Supabase에 적재하고, 자연어 지역 힌트가 없는 질의에도 `market_location_resolver` 가 방문객 수 기준으로 최적 지역을 자동 보정
- **문서/DB 근거 우선**: 코스 거리·시간·난이도·구간별 km은 전부 Supabase 실 데이터를 그대로 사용하고 지어내지 않음. 작물·밭담문화 서사는 직접 작성한 지식 문서(`data/culture_knowledge/`)를 벡터 검색해 근거로 활용
- **외부 API 의존성 최소화**: 실시간 기상청 API는 완전히 제거하고 정적 월별 계절 테이블로 대체. 비짓제주 API는 매장 소개 텍스트만 아이디어의 참고 재료로 사용하고 특정 매장명은 결과물에 노출하지 않음(관광 API 데이터의 폐업/변경 리스크 회피)
- **실시간 진행 상황 스트리밍**: FastAPI SSE(`/api/v1/report/generate`)가 노드별 진행 상황을 실시간으로 흘려보내고, Streamlit `st.status` 가 이를 렌더링
- **인증 및 속도 제한 내장**: `REPORT_API_KEY` 설정 시 X-API-Key 헤더 인증 요구. 클라이언트(키 또는 IP)당 분당 5건 속도 제한으로 비용형 남용 방지

## 3. 아키텍처
전체 파이프라인, 노드별 데이터 소스, 실 데이터/폴백 현황은 [docs/architecture/](./docs/architecture/) 에 정리되어 있습니다. Claude Code 등 AI 에이전트로 이 저장소에서 작업할 때 참고할 명령어/하네스 규칙은 [CLAUDE.md](./CLAUDE.md) 를 확인하세요.

```
[ 자연어 질의 (Streamlit st.chat_input) ]
       │
       ▼
[ LangGraph 9노드 에이전트 (src/agent/) ]
       │  ├─ courses / course_chunks / course_sub_segments (Supabase pgvector)
       │  ├─ visitor_analytics (읍·면·동별 방문객 통계, Supabase)
       │  ├─ culture_crop_knowledge (문화·작물 지식, 벡터 검색 또는 JSON 폴백)
       │  └─ 비짓제주 API (로컬 상점 소개 텍스트, 참고 재료)
       ▼
[ FastAPI SSE 스트리밍 (src/main.py) ]
  POST /api/v1/report/generate  ·  GET /health
       │
       ▼
[ B2B 기획서 (5단 Markdown, Streamlit 채팅창 렌더링) ]
```

### LangGraph 노드 구성 (9개)

| 노드 | 역할 |
|---|---|
| `intent_analyzer` | 의도 분류(course_recommendation / info_lookup / other) + 제약조건 파싱 + 지역 자동 추론 |
| `safety_evaluator` | 월별 정적 기후 테이블 기반 계절 리스크 평가 |
| `retriever` | 코스·문화지식 하이브리드 검색, DB 매칭 0건 시 Fail-Fast 반려 |
| `report_generator` | 5단 B2B 기획서 합성 |
| `quality_checker` | Self-RAG 신뢰도 검증 (★ 기반 Pass/Fail) |
| `query_rewriter` | 품질 실패 시 검색 조건 전면 재작성 |
| `quick_responder` | 단순 정보 조회·범위 이탈 질의 경량 응답 |
| `tool_agent` | LLM 기반 도구 호출 결정 및 최종 답변 작성 |
| `tool_executor` | 실제 도구 실행 (코스 조회, 방문객 통계 등) |

## 4. 디렉토리 구조
```
jeju-olle-docent/
├── app.py                            # Streamlit 챗봇 UI
├── CLAUDE.md                         # AI 에이전트용 저장소 가이드
├── Dockerfile                        # 컨테이너 이미지 빌드
├── docker-compose.yml                # 로컬 개발용 컴포즈
├── docker-compose.prod.yml           # 운영 배포용 컴포즈 (api + web 서비스 분리)
├── requirements.txt
├── pyproject.toml
├── .env.example                      # 환경변수 예시
├── data/
│   ├── culture_knowledge/            # 밭담문화·작물 지식 문서 (JSON)
│   ├── visitor_analytics/            # 읍·면·동별 방문객 통계 JSON
│   ├── raw_data/                     # 원천 PDF·CSV 등 비가공 데이터
│   ├── extracted/                    # 전처리 완료 데이터
│   ├── schema/                       # 데이터 스키마 문서
│   └── jeju_districts.csv            # 제주 행정구역 목록
├── docs/                             # 기획서/아키텍처/API 명세/QA 시나리오
│   ├── architecture/
│   ├── api/
│   ├── deliverables/
│   ├── guides/
│   ├── checklists/
│   ├── qa/
│   ├── db/
│   ├── retrospectives/
│   └── archive/
├── scripts/
│   ├── run_db_ingestion.py               # 코스/구간/청크 Supabase 적재
│   ├── run_culture_db_ingestion.py       # 문화·작물 지식 문서 임베딩 적재
│   ├── run_visitor_analytics_ingestion.py # 방문객 통계 데이터 Supabase 적재
│   ├── extract_visitor_analytics_to_json.py # PDF→JSON 방문객 통계 추출
│   ├── backfill_eup_myeon_dong_areas.py  # 읍·면·동 행정구역 보정 스크립트
│   └── evaluate_agent.py                 # 에이전트 품질 평가 스크립트
├── src/
│   ├── main.py                       # FastAPI 앱 (SSE 스트리밍, 인증, 속도제한)
│   ├── agent/
│   │   ├── graph.py                  # LangGraph StateGraph 빌드 및 라우팅
│   │   ├── state.py                  # AgentState TypedDict 정의
│   │   ├── router.py                 # 의도 분류 라우터
│   │   ├── llm_client.py             # Upstage Solar LLM 클라이언트
│   │   ├── weather_client.py         # 정적 월별 계절 기후 테이블
│   │   ├── tools.py                  # LangGraph Tool 정의
│   │   ├── nodes/                    # 9개 노드 서브모듈
│   │   │   ├── analyzer.py           # intent_analyzer (분류+파싱+지역추론 통합)
│   │   │   ├── safety.py             # evaluate_safety_node
│   │   │   ├── retriever.py          # retrieve_rag_node
│   │   │   ├── reporter.py           # generate_report_node / quick_responder_node
│   │   │   ├── quality.py            # check_quality_node / rewrite_query_node
│   │   │   └── tools.py              # tool_agent_node / tool_executor_node
│   │   └── prompts/                  # 노드별 프롬프트 마크다운 파일
│   │       ├── parse_intent.md
│   │       ├── router.md
│   │       ├── generate_report.md
│   │       ├── local_ideas.md
│   │       ├── quick_responder.md
│   │       ├── safety_guide.md
│   │       ├── tool_agent.md
│   │       └── tool_agent_fallback.md
│   ├── ingestion/
│   │   ├── database_loader.py        # Supabase 클라이언트 & Solar 임베딩
│   │   ├── visit_jeju_client.py      # 비짓제주 API 클라이언트 (Mock 폴백 포함)
│   │   ├── parse_visitor_pdf.py      # 방문객 통계 PDF 파서
│   │   └── clean_course_details.py   # 코스 데이터 전처리
│   ├── services/
│   │   ├── db_service.py             # Supabase 쿼리 서비스 (코스·통계·문화지식)
│   │   └── price_estimation_service.py # B2B 단가 산출 서비스
│   └── models/
│       └── schema.py                 # Pydantic 스키마 (AgentState, B2BQueryParams 등)
├── supabase/
│   └── schema.sql                    # DDL + pgvector RPC 함수
└── tests/
    ├── agent/
    │   ├── nodes/
    │   │   ├── test_check_quality_node.py
    │   │   ├── test_generate_report_node.py
    │   │   ├── test_market_location_resolver.py
    │   │   ├── test_quick_responder_node.py
    │   │   ├── test_retrieve_rag_node.py
    │   │   ├── test_rewrite_query_node.py
    │   │   ├── test_tool_agent.py
    │   │   └── test_visitor_analytics_node.py
    │   ├── test_agent_graph.py
    │   ├── test_graph_routing.py
    │   ├── test_llm_client.py
    │   ├── test_parse_intent_fallback.py
    │   ├── test_quality_stars.py
    │   └── test_router.py
    └── ingestion/
        └── (비짓제주·DB 로더 테스트)
```

## 5. 로컬 개발 및 시작 가이드

### 1) Windows 파이썬 실행 오류 해결 (앱 실행 별칭 비활성화)
윈도우 환경에서 `python`/`python3` 명령 실행 시 Microsoft Store 가 열리거나 비정상 종료되는 문제 해결:
1. Windows 검색창에 **앱 실행 별칭 관리** 입력 → 설정 창 진입
2. `python.exe`/`python3.exe` (앱 설치 관리자) 항목을 **끔** 으로 변경
3. PowerShell 재시작

### 2) Python 런타임 설치
- **버전**: Python 3.10.x 이상 ([공식 다운로드](https://www.python.org/downloads/))
- 설치 시 **Add python.exe to PATH** 옵션 필수 체크

### 3) 가상환경 구축 및 의존성 설치
```powershell
python -m venv .venv
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4) 환경 변수 (`.env`)
루트에 `.env` 파일을 생성하고 아래 값을 채웁니다 (예시는 [.env.example](./.env.example) 참고).
```env
SUPABASE_URL=your_supabase_project_url_here
SUPABASE_KEY=your_supabase_service_role_key_here
UPSTAGE_API_KEY=your_upstage_solar_api_key_here
VISIT_JEJU_API_KEY=your_visit_jeju_api_key_here

# 선택 사항: 설정하면 POST /api/v1/report/generate 가 X-API-Key 헤더 인증을 요구합니다.
REPORT_API_KEY=
```
> `VISIT_JEJU_API_KEY` 없이도 동작합니다(사전 큐레이션된 Mock 데이터로 자동 폴백). 기상청 API 키는 더 이상 사용하지 않습니다.

### 5) 서버 실행
```powershell
# 백엔드 (FastAPI + SSE)
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000

# 프론트엔드 (Streamlit 챗봇 UI, 별도 터미널)
streamlit run app.py
```
브라우저에서 `http://localhost:8501` 접속 후 자연어로 질의하면 됩니다.

### 6) 단위 테스트 및 코드 품질 검사
```powershell
python -m pytest
ruff check .
ruff format .
```

### 7) DB 재적재 (선택, 데이터 변경 시에만)
```powershell
# 코스/구간/청크 Supabase 적재
python scripts/run_db_ingestion.py

# 밭담문화·작물 지식 문서 임베딩 적재
python scripts/run_culture_db_ingestion.py

# 방문객 통계 PDF → JSON 추출 후 Supabase 적재
python scripts/extract_visitor_analytics_to_json.py
python scripts/run_visitor_analytics_ingestion.py

# 읍·면·동 행정구역 보정 (기적재 데이터에 eup_myeon_dong 컬럼 보정 필요 시)
python scripts/backfill_eup_myeon_dong_areas.py
```

### 8) 에이전트 품질 평가
```powershell
python scripts/evaluate_agent.py
```

## 6. Docker 배포

### 로컬 컴포즈 (개발)
```powershell
docker-compose up --build
```

### 운영 배포 (api + web 서비스 분리)
```powershell
docker-compose -f docker-compose.prod.yml up --build -d
```
- `api` 서비스: FastAPI 백엔드, 포트 8000
- `web` 서비스: Streamlit 프론트엔드, 포트 8501
- 운영 환경에서는 `.env` 에 `REPORT_API_KEY` 를 반드시 설정해 무단 호출로 인한 비용 소모를 막을 것

## 7. API 명세
| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/v1/report/generate` | 자연어 질의 → B2B 기획서 SSE 스트리밍 생성 |
| `GET` | `/health` | 헬스체크 |

**요청 예시 (`POST /api/v1/report/generate`)**
```json
{ "query": "가을에 당근밭 풍경이 있는 평지 코스로 B2B 기획서 써줘" }
```

**SSE 이벤트 타입**
- `node_progress` — 노드 실행 중 진행 상황 레이블
- `report` — 최종 B2B 기획서 마크다운 본문
- `error` — 처리 오류 메시지
- `end` — 스트림 종료 신호
