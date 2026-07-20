# 데이터베이스 상세 명세서
본 문서는 제주올레 가이드북 RAG MVP 시스템의 관계형 데이터베이스 및 pgvector 스키마 명세를 다룹니다. 물리 DDL 스크립트는 별도 파일인 [schema.sql](../../supabase/schema.sql) 에 격리하여 관리합니다.

## 1. 개요
제주올레 코스 데이터와 휠체어 구간 데이터의 무결성을 보존하고, 검색 효율성을 극대화하기 위해 하이브리드 관계형 스키마를 구성합니다.

## 2. 테이블 상세 정의

### 2.1. 코스 테이블 (`courses`)
각 올레길 코스의 메타데이터를 저장하는 테이블입니다.
| 컬럼명 | 데이터 타입 | 제약 조건 | 설명 |
| --- | --- | --- | --- |
| `id` | SERIAL | PRIMARY KEY | 코스 식별자 |
| `course_name` | VARCHAR(100) | NOT NULL, UNIQUE | 코스 명칭 (예: "Course 01") |
| `total_distance_km` | NUMERIC(4, 1) | NOT NULL | 코스 총 거리 |
| `estimated_time_hours` | NUMERIC(3, 1) | NOT NULL | 예상 소요 시간 |
| `start_point` | VARCHAR(255) | NOT NULL | 코스 시작 지점명 |
| `end_point` | VARCHAR(255) | NOT NULL | 코스 종료 지점명 |
| `created_at` | TIMESTAMP | DEFAULT NOW() | 생성 일시 |

### 2.2. 휠체어 구간 테이블 (`wheelchair_accessible_segments`)
인포그래픽 이미지 정보를 기반으로 구조화된 휠체어 전용 구간 데이터를 저장합니다.
| 컬럼명 | 데이터 타입 | 제약 조건 | 설명 |
| --- | --- | --- | --- |
| `id` | SERIAL PRIMARY KEY | 구간 식별자 |
| `course_id` | INTEGER | REFERENCES courses(id) | 소속 코스 ID |
| `segment_name` | VARCHAR(255) | NOT NULL | 구간 명칭 (예: "종달리 옛 소금밭 ~ 성산갑문 입구 구간") |
| `start_address` | VARCHAR(255) | NOT NULL | 시작점 주소 (예: "제주시 구좌읍 종달리 814-5") |
| `distance_km` | NUMERIC(3, 1) | NOT NULL | 구간 거리 (km 수치) |
| `difficulty_level` | VARCHAR(10) | CHECK IN ('상', '중', '하') | 난이도 등급 (엄격한 규칙 적용) |
| `created_at` | TIMESTAMP | DEFAULT NOW() | 생성 일시 |

### 2.3. 코스 청크 및 벡터 테이블 (`course_chunks`)
유사도 검색 및 컨텍스트 제공을 위한 임베딩 테이블입니다.
| 컬럼명 | 데이터 타입 | 제약 조건 | 설명 |
| --- | --- | --- | --- |
| `id` | SERIAL | PRIMARY KEY | 청크 식별자 |
| `course_id` | INTEGER | REFERENCES courses(id) | 소속 코스 ID |
| `title` | VARCHAR(255) | NOT NULL | 청크 소제목 (― 기점 분류) |
| `content` | TEXT | NOT NULL | 청크 본문 |
| `embedding` | VECTOR(1536) | OpenAI 1536차원 | 본문 텍스트 벡터 임베딩 값 |
| `created_at` | TIMESTAMP | DEFAULT NOW() | 생성 일시 |

## 3. 정적 시딩 데이터 명세 (Seed Data)
휠체어 구간 데이터는 가이드북의 고정 10개 코스 정보만 존재하므로, 데이터베이스 초기화 시 [schema.sql](../../supabase/schema.sql) 스크립트를 통해 정적으로 적재됩니다.
- **적재 대상 10개 코스 데이터**:
  1. **1코스**: 종달리 옛 소금밭 ~ 성산갑문 입구 구간 (시작점: 제주시 구좌읍 종달리 814-5, 거리: 4.6km, 난이도: 중)
  2. **10-1코스**: 가파도 전 구간 (시작점: 가파도 상동포구, 거리: 4.2km, 난이도: 상)
  3. **4코스**: 해비치호텔&리조트 ~ 가마리개 쉼터 구간 (시작점: 서귀포시 표선면 표선리 40-76, 거리: 4.8km, 난이도: 중)
  4. **5코스**: 국립수산과학원 ~ 위미항 구간 (시작점: 서귀포시 남원읍 위미리 785-1, 거리: 2.7km, 난이도: 상)
  5. **6코스**: 쇠소깍 ~ 보목포구 구간 (시작점: 서귀포시 하효동 999, 거리: 2.6km, 난이도: 중)
  6. **8코스**: 논짓물 ~ 대평포구 (시작점: 서귀포시 하예동 532-3, 거리: 3.6km, 난이도: 상)
  7. **10코스**: 사계포구 ~ 송악산 주차장 구간 (시작점: 서귀포시 안덕면 사계리 2125, 거리: 2.9km, 난이도: 중)
  8. **12코스**: 엉알길 입구 ~ 자구내포구 입구 구간 (시작점: 제주시 한경면 고산리 3674-2, 거리: 1.1km, 난이도: 중)
  9. **14코스**: 일성콘도 ~ 금능해수욕장 입구 구간 (시작점: 제주시 한림읍 금능리 1621-6, 거리: 2.1km, 난이도: 중)
  10. **17코스**: 도두봉 내려오는 길 ~ 용연다리 구간 (시작점: 제주시 도두2동 1611, 거리: 4.4km, 난이도: 중)
