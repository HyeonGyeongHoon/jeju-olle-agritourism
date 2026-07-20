# 제주올레 도슨트 (Jeju Olle Docent) RAG 에이전트
제주올레길 공식 가이드북 PDF 데이터를 기반으로 지식 데이터베이스(Supabase pgvector) 를 구축하고, 하이브리드 검색 및 조건 완화 에이전트 알고리즘을 통해 탐방객에게 최적의 올레길 코스를 추천하는 AI 도슨트 서비스입니다.

## 1. 프로젝트 개요
본 **프로젝트** 는 제주올레길 27개 전체 코스(메인 코스 21개 및 서브 코스 6개) 의 본문 정보, 구간별 상세 메타데이터, 휠체어 보행 가능 구간 10개, 안전수칙/에티켓/준비물 가이드를 자동 파싱 및 정제하여 벡터 데이터베이스에 적재하고 사용자 맞춤형 큐레이션 답변을 제공합니다.

## 2. 핵심 기능 및 특징
- **다층 데이터 파이프라인 정제**: PyMuPDF, OCR 및 비전 모델을 결합하여 27개 코스 전체 마크다운([courses_full.md](./data/extracted/courses_full.md)), 세부 메타데이터([courses_metadata.json](./data/extracted/courses_metadata.json)), 본문 청크([course_chunks.csv](./data/extracted/course_chunks.csv)), 휠체어 구간([wheelchair_segments.csv](./data/extracted/wheelchair_segments.csv)), 안전수칙 가이드([safety_etiquette_guide.json](./data/extracted/safety_etiquette_guide.json)) 아티팩트 생성 완료.
- **Supabase pgvector DB 연동**: Solar Embedding (4096차원) 기반 본문 청크 벡터 검색 및 Relational Metadata filtering 구조 구현.
- **절대적/완화 가능 제약 조건 분류 검색**: 휠체어 이용 가능 여부 등 신체/동행 조건은 **절대적 제약 조건** (Hard Constraint) 으로 절대 유지하고, 난이도/소요시간/거리 등은 **완화 가능한 제약 조건** (Soft Constraint) 으로 계층적 조건 완화(Relaxed Search Fallback) 를 수행하여 빈 검색 결과를 방지.
- **안전성 및 무결성 테스트**: `pytest` 단위 테스트 13개 및 `ruff` 코드 린팅 무결성 100% 통과.

## 3. 주요 아키텍처 및 데이터 흐름
```
[ PDF 가이드북 (data/raw_data/) ] 
       │
       ▼ (PyMuPDF & Parser)
[ 4개 핵심 아티팩트 (MD, JSON, CSV) ]
       │
       ▼ (Solar Embedding & DB Loader)
[ Supabase pgvector DB (courses, chunks, wheelchair) ]
       │
       ▼ (Hybrid RAG & Relaxed Search Agent)
[ 챗봇 API & 사용자 응답 ]
```

## 4. 디렉토리 구조
```
jeju-olle-docent/
├── .agents/                    # 프롬프트 및 에이전트 규칙 설정 (Git 제외)
├── data/                       # 가이드북 정제 데이터 및 로컬 데이터
│   ├── extracted/              # 정제 완료된 MD, JSON, CSV 파일 (Git 추적)
│   └── raw_data/               # 원본 PDF 및 추출 지도 이미지 (Git 제외)
├── src/                        # 소스 코드
│   ├── ingestion/              # PDF 파싱, 비전 추출, DB 적재 모듈
│   └── models/                 # Pydantic 스키마 정의
├── supabase/                   # 데이터베이스 스키마 및 Vector index
│   └── schema.sql              # Supabase pgvector 테이블 정의 SQL
└── tests/                      # 파서 및 DB 로더 단위 테스트
```

## 5. 로컬 개발 및 시작 가이드

### 1) Windows 파이썬 실행 오류 해결 (앱 실행 별칭 비활성화)
윈도우 환경에서 `python` 이나 `python3` 명령 실행 시 Microsoft Store 가 열리거나 `Python` 이라는 메시지만 출력된 채 비정상 종료(Exit Code 1) 되는 문제를 해결합니다.
1. Windows 작업 표시줄 검색창에 **앱 실행 별칭 관리** (또는 '앱 실행 별칭') 를 입력하여 설정 창으로 진입합니다.
2. 목록 내에서 `python.exe` 와 `python3.exe` (앱 설치 관리자) 항목 을 찾아 **끔** (Off) 으로 변경합니다.
3. 설정 완료 후 PowerShell 창을 완전히 닫고 다시 실행합니다.

### 2) Python 런타임 설치
- **설치 버전**: **Python 3.10.x** 이상 권장 (Target Version 3.10)
- **설치 링크**: [Python 공식 다운로드 페이지](https://www.python.org/downloads/)
- **설치 시 주의 사항**: 설치 마법사(Installer) 첫 화면 하단에 있는 **Add python.exe to PATH** 옵션 을 반드시 체크해야 합니다.

### 3) 프로젝트 가상환경 구축 및 의존성 설치
Windows PowerShell 환경에서 가상환경(venv) 을 만들고 의존성 라이브러리를 설치합니다.
```powershell
# 가상환경 생성
python -m venv .venv

# PowerShell 실행 정책 변경 (최초 1회 필수)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process

# 가상환경 활성화
.venv\Scripts\Activate.ps1

# 의존성 라이브러리 설치
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4) Supabase Cloud 및 API 연동 (.env)
루트 디렉토리에 `.env` 파일 을 신규 생성하고 필요한 키를 작성합니다.
```env
SUPABASE_URL=your_supabase_project_url_here
SUPABASE_KEY=your_supabase_anon_or_service_key_here
SOLAR_API_KEY=your_upstage_solar_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
```
참고용 예시 템플릿은 [.env.example](./.env.example) 파일 을 참고하세요.

### 5) 데이터 파싱 및 아티팩트 변환
```powershell
python -m src.ingestion.convert_artifacts
```

### 6) 단위 테스트 및 코드 품질 검사 (Ruff)
```powershell
# 휠체어 구간 및 파서 단위 테스트 실행
python -m pytest

# 코드 오류 및 컨벤션 검사 (Ruff)
ruff check .

# 코드 포맷 자동 교정
ruff format .
```