# 하이브리드 RAG 검색 API 명세서
본 문서는 제주올레 가이드북 RAG MVP 시스템이 제공하는 API 엔드포인트의 입력 포맷, 출력 포맷 및 통신 규격을 다룹니다.

## 1. 개요
에이전트 클라이언트가 올레길 코스 정보 및 휠체어 이용 가능 구간을 질의하고, 관리자가 PDF 파이프라인을 수동 트리거할 수 있는 백엔드 API 명세입니다.

## 2. API 엔드포인트 명세

### 2.1. 하이브리드 RAG 검색 API
사용자의 질문과 필터를 조합하여 최적의 추천 컨텍스트 및 원천 데이터를 반환합니다.
- **HTTP Method**: `POST`
- **Path**: `/api/v1/search`
- **Content-Type**: `application/json`

#### Request Body
```json
{
  "query": "휠체어로 갈 수 있는 난이도 중 코스 추천해줘",
  "session_id": "session-abc-123",
  "top_k": 3
}
```
| 필드명 | 타입 | 필수 여부 | 설명 |
| --- | --- | --- | --- |
| `query` | String | 필수 | 사용자의 자연어 질문 |
| `session_id` | String | 필수 | 멀티턴 대화 세션 유지를 위한 고유 식별자 |
| `top_k` | Integer | 선택 | 유사도 상위 청크 반환 개수 (기본값: 3) |

#### 챗봇 자연어 분석 및 라우팅 규칙 (Chatbot NLP & Query Routing Rules)
본 시스템은 챗봇 기반 대화형 인터페이스를 제공하므로, 백엔드는 클라이언트로부터 받은 자연어 `query` 를 직접 분석하여 의도를 파악하고 동적 필터를 생성합니다.
- **1단계: 자연어 의도 분석 (Intent Classification)**: 백엔드는 입력된 `query` 텍스트 내에서 휠체어 코스 검색 의도(예: '휠체어', 'barrier-free', '보행기', '몸이 불편한' 등 지시어 감지)가 포함되어 있는지 LLM 또는 NLP 라우터를 사용하여 판별합니다.
- **2단계: 동적 개체명 추출 (Entity Extraction)**: 의도가 휠체어 검색으로 분류되면, 질문 내에서 난이도 정보('상', '중', '하') 및 수치적 거리 정보('~km 이하/미만')를 파싱하여 내부 `filters` 객체를 동적으로 빌드합니다.
- **3단계: 파이프라인 분기 및 하이브리드 검색**:
  - **휠체어 검색 의도 감지 시**: 동적 추출된 `filters` 제약 조건에 따라 `wheelchair_accessible_segments` RDB 데이터를 타겟으로 구조화된 SQL 쿼리를 실행합니다.
  - **일반 검색 의도 판별 시**: `course_chunks` 벡터 테이블을 대상으로 pgvector 코사인 유사도 검색을 수행하여 텍스트 컨텍스트를 로드합니다.

#### 멀티턴 대화 및 세션 관리 정책 (Multi-turn Session Management Policy)
동일한 대화 세션 내에서 사용자의 이전 의도 및 대화 맥락을 보존하기 위해 아래 세션 정책을 구현합니다.
- **세션 캐싱**: 백엔드는 전달받은 `session_id` 를 식별자로 사용하여, 해당 세션의 최근 필터 상태(예: 난이도, 선택 코스 등)를 서버 메모리(Cache)에 유지합니다.
- **컨텍스트 상속 (Context Inheritance)**:
  - 사용자가 이전 질문에서 "난이도가 상인 휠체어 코스가 있어?" 라고 질의하여 `difficulty_level = '상'` 필터가 생성된 후, 연이어 "거기 거리는 어떻게 돼?" 와 같이 대화 맥락이 연속되는 후속 질문을 던지면, 백엔드는 이전 턴의 필터 상태를 자동으로 상속(Merge)하여 `wheelchair_accessible_segments` 데이터를 검색합니다.
  - 대화의 맥락이 완전히 다른 새로운 올레길 질문으로 전환될 시에는 기존 세션 필터를 리셋(Reset)합니다.

#### Response Body
```json
{
  "answer_context": "[1코스] 종달리 옛 소금밭 ~ 성산갑문 입구 구간(구간 거리 4.6km, 난이도 중)은 휠체어 보행이 원활한 평탄한 코스로 구성되어 있습니다.",
  "sources": [
    {
      "course_name": "1코스",
      "segment_name": "종달리 옛 소금밭 ~ 성산갑문 입구 구간",
      "start_address": "제주시 구좌읍 종달리 814-5",
      "distance_km": 4.6,
      "difficulty_level": "중"
    }
  ]
}
```
| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| `answer_context` | String | 프롬프트 조립을 위해 추출된 최종 컨텍스트 텍스트 |
| `sources` | Array | 컨텍스트를 형성하는 데 참조된 실제 RDB 데이터 정보 목록 |

---

### 2.2. PDF 인제스천 수동 트리거 API
관리자가 PDF 가이드북 문서를 파싱하고 벡터 데이터베이스(Supabase)에 인제스천하는 과정을 즉시 작동시킵니다.
- **HTTP Method**: `POST`
- **Path**: `/api/v1/ingest`
- **Content-Type**: `application/json`

#### Request Body
```json
{
  "pdf_filename": "jeju_olle_guidebook.pdf",
  "force_reload": true
}
```
| 필드명 | 타입 | 필수 여부 | 설명 |
| --- | --- | --- | --- |
| `pdf_filename` | String | 필수 | `data/` 내 파싱 대상 파일명 |
| `force_reload` | Boolean | 선택 | 기존 적재 데이터를 초기화하고 덮어쓸지 여부 (기본값: false) |

#### Response Body
```json
{
  "status": "success",
  "parsed_courses_count": 27,
  "parsed_wheelchair_segments_count": 10,
  "message": "가이드북 파싱 및 Supabase 적재가 성공적으로 완료되었습니다."
}
```
