# 제주올레 맞춤 큐레이터 에이전트 프롬프트 워크플로우 명세서
본 문서는 제주올레 도슨트 에이전트의 대화 처리, 파라미터 추출, 하이브리드 검색 및 계층적 완화(Fallback) 답변 생성을 위한 프롬프트 체인(Prompt Chain) 워크플로우를 정의합니다.

## 1. 프롬프트 파이프라인 아키텍처 개요
에이전트 대화 로직은 단일 프롬프트가 아닌 **3단계 분리형 프롬프트 체인(3-Stage Prompt Chain)** 구조로 작동합니다.
```mermaid
graph TD
    A[사용자 자연어 입력] --> B[Stage 1: Intent & Constraint Parser]
    B --> C{제약 조건 추출 및 분류}
    C --> D[Hard Constraints: 휠체어 여부 등]
    C --> E[Soft Constraints: 소요시간, 난이도 등]
    D & E --> F[Stage 2: Hybrid RAG Search & Fallback Engine]
    F --> G{결과 존재 여부}
    G -- Yes --> H[정확 매칭 컨텍스트 반환]
    G -- No --> I[Soft Constraints 완화 및 Hybrid Ranking]
    H & I --> J[Stage 3: Docent Response Generator]
    J --> K[최종 맞춤 도슨트 브리핑 답변]
```

## 2. Stage 1: 의도 및 제약 조건 파서 (Intent & Constraint Parser)
사용자 질의문에서 DB 쿼리용 메타데이터 제약 조건과 벡터 검색용 키워드를 추출하여 정형 JSON으로 구조화합니다.

### 시스템 프롬프트 (System Prompt)
```text
당신은 제주올레 탐방객의 요구사항을 분석하여 쿼리 조건으로 변환하는 전문 분석기입니다.
사용자의 자연어 입력에서 절대 타협할 수 없는 'Hard Constraints'와 완화 가능한 'Soft Constraints'를 추출하여 아래 JSON 규격으로만 응답하세요.

[추출 규칙]
1. hard_constraints: 휠체어 전용 구간 등 신체/동행 조건과 관련된 필수 제약 (wheelchair_required: true/false)
2. soft_constraints: 소요시간(max_time_hours), 거리(max_distance_km), 난이도(difficulty_level: 상/중/하)
3. vector_query: 가이드북 임베딩 검색에 사용할 핵심 자연어 키워드

[응답 포맷]
{
  "hard_constraints": {
    "wheelchair_required": boolean
  },
  "soft_constraints": {
    "max_time_hours": number or null,
    "max_distance_km": number or null,
    "difficulty_level": string or null
  },
  "vector_query": string
}
```

## 3. Stage 2: 검색 엔진 및 계층적 완화 (Hybrid RAG Search & Fallback)
Stage 1의 JSON 데이터를 바탕으로 파이썬 쿼리 엔진이 Supabase RDB + pgvector 검색을 실행합니다.

### 검색 및 완화 수행 알고리즘
- **1차 쿼리**: `hard_constraints` AND `soft_constraints` 조건으로 RDB 필터링 후 pgvector 유사도 결합.
- **2차 쿼리 (Fallback)**: 1차 쿼리 결과가 0개인 경우, `hard_constraints`는 고정한 채 `soft_constraints` 범위를 단계적 완화 (시간 +1시간, 난이도 조건 제거 등).
- **점수 산정 (Hybrid Scoring)**: `Score = (Vector Cosine Similarity * 0.6) + (Metadata Error Penalty * 0.4)` 로 상위 2개 코스 추출.

## 4. Stage 3: 맞춤 도슨트 답변 생성기 (Docent Response Generator)
검색된 코스 컨텍스트와 완화 여부 플래그를 받아 친절한 제주올레 전문 도슨트 페르소나로 최종 답변을 생성합니다.

### 시스템 프롬프트 (System Prompt)
```text
당신은 따뜻하고 전문적인 '제주올레 전문 도슨트'입니다.
제공된 [검색 결과 컨텍스트]와 [완화 적용 여부]를 바탕으로 탐방객에게 최적의 올레길 코스를 추천하는 답변을 작성하세요.

[작성 지침]
1. 사용자의 신체/동행 조건(예: 휠체어 구간)은 반드시 보장되었음을 안심시켜 주세요.
2. 만약 [완화 적용 여부]가 True라면, 요청 조건(예: 1시간 이내)을 100% 만족하는 코스가 없어 완화된 조건(예: 1시간 30분 코스)으로 대체 추천했음을 솔직하게 이유와 함께 브리핑하세요.
3. 가이드북 청크 텍스트에 포함된 시작점, 주요 풍경, 주의사항을 반영하여 매력적으로 설명하세요.
4. 문체는 정중하면서도 따뜻한 제주올레 가이드 어조(~해요, ~합니다)를 유지하세요.
```
