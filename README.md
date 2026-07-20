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
[ PDF 가이드북 ] 
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
├── .agents/                    # 프롬프트 및 에이전트 규칙 설정
├── data/                       # 가이드북 원본 PDF 및 추출 아티팩트
│   ├── extracted/              # 정제 완료된 MD, JSON, CSV 파일
│   └── map_images/             # 추출된 코스 지도 이미지
├── docs/                       # 기획서, 가이드, 진행 보고서
│   ├── hybrid_rag_mvp_plan.md  # MVP 하이브리드 RAG 상세 기획서
│   ├── progress_report.md      # 데이터 전처리 & DB 적재 진행 보고서
│   └── setup_guide.md          # 로컬 개발 및 실행 환경 구축 가이드
├── src/                        # 소스 코드
│   ├── ingestion/              # PDF 파싱, 비전 추출, DB 적재 모듈
│   └── models/                 # Pydantic 스키마 정의
├── supabase/                   # 데이터베이스 스키마 및 Vector index
│   └── schema.sql              # Supabase pgvector 테이블 정의 SQL
└── tests/                      # 파서 및 DB 로더 단위 테스트
```

## 5. 시작 가이드
자세한 개발 환경 구축 및 가이드 사항은 [설정 가이드](./docs/setup.md) 를 참고하시기 바랍니다.

### 1) 환경 변수 설정
프로젝트 루트 경로에 `.env` 파일 을 생성하고 아래 환경 변수를 입력합니다.
```env
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_anon_or_service_key
SOLAR_API_KEY=your_upstage_solar_api_key
OPENAI_API_KEY=your_openai_api_key
```
템플릿 파일은 [.env.example](./.env.example) 을 참조하세요.

### 2) 데이터 파싱 및 아티팩트 변환
```powershell
python -m src.ingestion.convert_artifacts
```

### 3) 단위 테스트 실행
```powershell
python -m pytest
```

### 4) 코드 스타일 검사 (Linting)
```powershell
ruff check .
```

## 6. 개발 및 하네스 제약 규칙
- **DB 적재 사전 승인**: 데이터베이스 적재 및 Embedding API 호출 작업은 사용자의 사전 승인 후 실행합니다.
- **Git Push 사전 승인**: 원격 저장소 `git push` 명령어는 반드시 사용자의 승인을 구한 후 진행합니다.
