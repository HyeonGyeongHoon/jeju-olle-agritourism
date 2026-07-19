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
- [x] Pydantic 기반의 데이터 검증 모델 스키마 작성 완료 (schema.py)
- [ ] GCP VM 인스턴스 확인 및 Docker 환경 검증
- [ ] Supabase Cloud 인스턴스 생성 및 pgvector 익스텐션 활성화
- [ ] 데이터베이스 테이블 DDL 실제 실행 및 연동

## 1주차 (4~7일) - PDF 파싱 및 인제스천 구현
가이드북 PDF 원천 데이터로부터 유의미한 정보를 정제하여 파싱하는 단계입니다.
- [ ] 원천 PDF 파일 (`jeju_olle_guidebook.pdf`) 획득 및 `data/` 배치
- [ ] PDF 텍스트 추출 모듈 구현 (`pdf_extractor.py`)
- [ ] 영문 코스 헤더 패턴 매칭 구현 (`parser.py`)
- [ ] 소제목(―) 기점 청킹 모듈 구현 (`parser.py`)
- [ ] '제주올레 휠체어 구간' 고정 Seed SQL 스크립트 작성 및 supabase/schema.sql 연동
- [ ] 코스 파서 동작 및 휠체어 정적 데이터 무결성 검증을 위한 Pytest 단위 테스트 통과

## 2주차 (8~11일) - RAG 검색 에이전트 연동
임베딩 데이터를 생성하고 Supabase 에 적재하여 하이브리드 검색을 연계하는 단계입니다.
- [ ] OpenAI Embedding API 호출 모듈 작성 및 1536차원 벡터 생성
- [ ] Supabase pgvector 데이터 적재 로직 구현 (`database_loader.py`)
- [ ] 텍스트 유사도 기반의 Retrieval 모듈 구현
- [ ] RDB 메타데이터와 pgvector 벡터 검색을 조합한 하이브리드 검색 구현
- [ ] API 레이트 리밋 예외 처리(Exponential Backoff 재시도) 적용

## 2주차 (12~14일) - 인프라 배포 및 통합 검증
GCP VM 환경에 컨테이너 기반으로 배포하고 전체 시스템의 데이터 정합성을 검증하는 단계입니다.
- [ ] Dockerfile 및 docker-compose.yml 동작 검증
- [ ] GitHub Actions CI/CD 파이프라인 최종 연동
- [ ] GCP VM 환경에 Docker 이미지 배포 및 서비스 실행
- [ ] 전체 파이프라인 시나리오 테스트 및 무결성 검증
