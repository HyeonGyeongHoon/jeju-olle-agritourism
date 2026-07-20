# 제주올레 도슨트 MVP 구축 체크리스트
본 체크리스트는 2주 일정의 **하이브리드 RAG 에이전트 시스템 MVP** 개발 과정을 관리하기 위한 문서입니다.

## 1주차 (1~3일) - 환경 구축 및 데이터베이스 설계
인프라의 뼈대를 생성하고 데이터를 담을 저장소를 설계하는 단계입니다.
- [x] 프로젝트 디렉터리 구조 및 뼈대 코드 생성
- [x] Ruff 및 Pytest 기본 설정 완료
- [x] GitHub Actions CI 워크플로우 구성 (`ci.yml`)
- [x] 데이터베이스 DDL 설계 및 schema.sql 분리 저장 완료
- [x] 기획 분류에 맞춘 database_schema.md 상세 설계 명세서 작성 완료
- [x] API 사양 설계 및 docs/api/api_spec.md 작성 완료
- [x] Pydantic 기반의 데이터 검증 모델 스키마 작성 완료 (`schema.py`)
- [x] GCP VM / Docker 배포 환경 파일 검증 완료 (`Dockerfile`, `docker-compose.yml`)
- [x] Supabase Cloud 인스턴스 생성 및 pgvector 익스텐션 활성화 (`schema.sql`)
- [x] 데이터베이스 테이블 DDL 실제 실행 및 연동 완료 (`VECTOR(4096)`)

## 1주차 (4~7일) - PDF 파싱 및 데이터 전처리 파이프라인
가이드북 PDF 원천 데이터로부터 유의미한 정보를 정제하고 중간 아티팩트를 파싱하는 데이터 전처리 단계입니다.
- [x] 원천 PDF 파일 (`jeju_olle_guidebook.pdf`) 획득 및 `data/` 배치 완료
- [x] PDF 텍스트 추출 모듈 구현 (`pdf_extractor.py`) - 31~134페이지 지정 추출 기능 포함
- [x] 코스 헤더 및 한국어 올레 표기 정규식 파서 고도화 (`parser.py`)
- [x] 소제목(―) 기점 청킹 모듈 구현 (`parser.py`)
- [x] 부분 탐방 큐레이션용 세부 구간(Sub-segment) 파싱 모듈 구현 (`parser.py`)
- [x] 안전수칙, 에티켓, 추천 준비물 데이터 파싱 및 정제 (`build_safety_and_etiquette_data.py`)
- [x] 중간 정제 파일 생성 모듈 구축 (`convert_artifacts.py` -> MD, JSON, CSV 4종 아티팩트 자동 내보내기)
- [x] '제주올레 휠체어 구간' 고정 Seed SQL 스크립트 작성 및 `supabase/schema.sql` 연동
- [x] 데이터 전처리 및 파서 단위 테스트 작성 및 Pytest 통과 (13/13 통과)

## 2주차 (8~11일) - DB 임베딩 적재 및 RAG 검색 연동
임베딩 데이터를 생성하고 Supabase에 적재하여 하이브리드 검색을 연계하는 단계입니다.
- [x] 한국어 특화 Upstage Solar Embedding API 호출 모듈 작성 및 4096차원 벡터 생성 (`database_loader.py`)
- [x] API 레이트 리밋 예외 처리 (Exponential Backoff 재시도) 적용 완료
- [x] Supabase pgvector 클린 DB 재적재 - 메인 코스, 세부 구간, 안전수칙/준비물 지식 100% 실적재 완료
- [ ] 3단계 에이전트 프롬프트 워크플로우 연동 (`agent_prompt_workflow.md` 기반)
- [ ] 텍스트 유사도 기반의 Retrieval 모듈 구현
- [ ] RDB 메타데이터와 pgvector 벡터 검색을 조합한 하이브리드 검색 구현

## 2주차 (12~14일) - 인프라 배포 및 통합 검증
GCP VM 환경에 컨테이너 기반으로 배포하고 전체 시스템의 데이터 정합성을 검증하는 단계입니다.
- [ ] Dockerfile 및 docker-compose.yml 동작 검증
- [ ] GitHub Actions CI/CD 파이프라인 최종 연동
- [ ] GCP VM 환경에 Docker 이미지 배포 및 서비스 실행
- [ ] 전체 파이프라인 시나리오 테스트 및 무결성 검증
