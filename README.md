# 제주 영농-관광 상생 상품 기획서 도슨트 (Jeju Olle B2B Docent)

제주올레길 코스 데이터, 밭담문화·작물 생육 지식, 로컬 상점 정보를 결합해 지자체 담당자·여행사 기획자를 위한 **B2B 관광 상품 기획서를 자연어 질의만으로 자동 생성**하는 LangGraph 기반 에이전트 서비스입니다.

> 원래는 탐방객 대상 대화형(B2C) 코스 추천 챗봇으로 시작했으나, 멘토링 피드백을 반영해 지자체/여행사 실무자를 위한 B2B 기획서 자동 생성 도구로 방향을 전환했습니다. 도메인(제주 올레길 + 지역 작물/상점)과 데이터 인프라는 그대로 유지한 채 출력 형태와 타겟만 바뀌었습니다.

## 1. 프로젝트 개요
"가을에 당근밭 풍경 보면서 걷기 좋은 평지 코스 기획서 써줘" 같은 자연어 질의를 입력하면, 약 15~30초 내에 아래 5단 구조의 실무용 B2B 기획서를 생성합니다.

1. 📊 **B2B 상품 개요 & 스펙** — 상품명/타겟/운용시간/단가범위/USP
2. 📍 **구간별 타임라인 표** — 실제 코스 세부구간(km) 데이터 기반 도슨트 포인트 + 현장 체크리스트
3. ☕ **로컬 상생 제휴 아이디어 표** — 매장명 노출 없이, 지역 상점 성격에서 착안한 협업 컨셉 제안
4. 🌤️ **기후 리스크 및 Plan A/B 우회 동선**
5. 🛡️ **Trust Tagging** — 데이터 출처 및 신뢰도 표기

## 2. 핵심 특징
- **LangGraph 8노드 자율 순환 에이전트**: 의도 분류 → 제약조건/B2B 파라미터 추출 → 계절 리스크 평가 → 하이브리드 검색 → 리포트 합성 → 로컬 아이디어 생성 → Self-RAG 품질 검증(실패 시 최대 3회 자동 재작성)
- **문서/DB 근거 우선**: 코스 거리·시간·난이도·구간별 km은 전부 Supabase 실 데이터를 그대로 사용하고 지어내지 않음. 작물·밭담문화 서사는 직접 작성한 지식 문서(`data/culture_knowledge/`)를 벡터 검색해 근거로 활용
- **외부 API 의존성 최소화**: 실시간 기상청 API는 완전히 제거하고 정적 월별 계절 테이블로 대체. 비짓제주 API는 매장 소개 텍스트만 아이디어의 참고 재료로 사용하고 특정 매장명은 결과물에 노출하지 않음(관광 API 데이터의 폐업/변경 리스크 회피)
- **실시간 진행 상황 스트리밍**: Streamlit `st.status`가 SSE로 노드별 진행 상황을 실제 실시간으로 표시

## 3. 아키텍처
전체 파이프라인, 노드별 데이터 소스, 실 데이터/폴백 현황은 [docs/project_architecture.md](./docs/project_architecture.md)에 정리되어 있습니다. Claude Code 등 AI 에이전트로 이 저장소에서 작업할 때 참고할 명령어/하네스 규칙은 [CLAUDE.md](./CLAUDE.md)를 확인하세요.

```
[ 자연어 질의 (Streamlit st.chat_input) ]
       │
       ▼
[ LangGraph 8노드 에이전트 (src/agent/) ]
       │  ├─ courses / course_chunks / course_sub_segments (Supabase pgvector)
       │  ├─ culture_crop_knowledge (문화·작물 지식, 벡터 검색 또는 JSON 폴백)
       │  └─ 비짓제주 API (로컬 상점 소개 텍스트, 참고 재료)
       ▼
[ FastAPI SSE 스트리밍 (src/main.py) ]
       │
       ▼
[ B2B 기획서 (5단 Markdown, Streamlit 채팅창 렌더링) ]
```

## 4. 디렉토리 구조
```
jeju-olle-docent/
├── app.py                       # Streamlit 챗봇 UI
├── CLAUDE.md                    # AI 에이전트용 저장소 가이드
├── data/
│   └── culture_knowledge/       # 밭담문화·작물 지식 문서 (JSON)
├── docs/                        # 기획서/체크리스트/아키텍처/QA 시나리오 (Git 비추적)
├── scripts/
│   ├── run_db_ingestion.py         # 코스/구간/청크 Supabase 적재
│   └── run_culture_db_ingestion.py # 문화·작물 지식 문서 임베딩 적재
├── src/
│   ├── agent/                   # LangGraph 노드/그래프/상태/LLM/기후 클라이언트
│   ├── ingestion/                # DB 로더, 비짓제주 클라이언트
│   └── models/                  # Pydantic 스키마
├── supabase/schema.sql          # DDL + pgvector RPC 함수
└── tests/                       # pytest 스위트
```

## 5. 로컬 개발 및 시작 가이드

### 1) Windows 파이썬 실행 오류 해결 (앱 실행 별칭 비활성화)
윈도우 환경에서 `python`/`python3` 명령 실행 시 Microsoft Store 가 열리거나 비정상 종료되는 문제 해결:
1. Windows 검색창에 **앱 실행 별칭 관리** 입력 → 설정 창 진입
2. `python.exe`/`python3.exe` (앱 설치 관리자) 항목을 **끔**으로 변경
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
python scripts/run_db_ingestion.py          # 코스/구간/청크
python scripts/run_culture_db_ingestion.py  # 밭담문화·작물 지식 문서 임베딩 적재
```
