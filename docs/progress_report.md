# 제주올레 RAG MVP 프로젝트 진행 현황 보고서
본 문서는 제주올레 도슨트 하이브리드 RAG 에이전트 시스템 MVP 구축 프로젝트의 현재 진행 상황과 완료된 작업 내역을 정리한 보고서입니다.

## 1. 프로젝트 기본 정보
- **프로젝트 명**: 제주올레 도슨트 RAG 에이전트 MVP
- **전체 기간**: 2주 (14일)
- **현재 시점**: 1일차 종료 시점
- **진행율**: 약 25% (인프라 뼈대 구축 및 설계 단계 완료)

## 2. 완료된 작업 내역 (1일차)
오늘 진행 완료된 태스크 목록입니다.
- **MVP 구축 기획서 보완**: 2주 개발 마일스톤에 맞추어 태스크 일정을 전면 수정 및 최적화하였습니다. ([hybrid_rag_mvp_plan.md](./hybrid_rag_mvp_plan.md) )
- **개발 작업환경 구축**: Ruff, Pytest 설정을 비롯해 GitHub Actions CI 파이프라인 연동용 워크플로우 구성을 완료하였습니다.
- **Pydantic 데이터 검증 모델 정의**: DDL 및 API 스키마 규격에 따라 데이터 정합성을 검증할 [schema.py](../src/models/schema.py) 의 구조를 최종 정의하였습니다.
- **물리 데이터베이스 설계**: Supabase 용 DDL 물리 스크립트를 독립적으로 구성하였습니다. ([schema.sql](../supabase/schema.sql) )
- **데이터베이스 스키마 명세**: 기획 문서 격리 원칙에 따라 설계서를 전용 폴더로 구성하였습니다. ([database_schema.md](./db/database_schema.md) )
- **RAG 백엔드 API 명세**: 에이전트 질의용 search API 및 인제스천 ingest API 규격을 정의하였습니다. ([api_spec.md](./api/api_spec.md) )
- **레거시 파일 정리**: 폴더 경로 이동 후 남은 구버전 템플릿 임시 파일들을 완전히 삭제하였습니다.

## 3. 남은 작업 내역 및 2일차 계획
다음 개발 세션에서 이어갈 계획입니다.
- Supabase Cloud 인스턴스 생성 및 [schema.sql](../supabase/schema.sql) 스크립트 실행 검증
- 가이드북 PDF 원천 데이터를 `data/` 경로에 배치하고 PDFPlumber 로드 확인
- 영문 코스 헤더 패턴 및 소제목 청킹 파서 모듈 개발 착수

## 4. 작업 산출물 목록
- 기획서: [hybrid_rag_mvp_plan.md](./hybrid_rag_mvp_plan.md)
- 스키마 명세: [database_schema.md](./db/database_schema.md)
- DDL 스크립트: [schema.sql](../supabase/schema.sql)
- API 명세: [api_spec.md](./api/api_spec.md)
- Pydantic 스키마: [schema.py](../src/models/schema.py)
- 체크리스트: [checklist.md](./checklist.md)
