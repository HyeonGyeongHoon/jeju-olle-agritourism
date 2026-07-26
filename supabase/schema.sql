-- Supabase 데이터베이스 초기화 및 리셋 구문 (기존 테이블 삭제)
DROP TABLE IF EXISTS local_recommendations CASCADE;
DROP TABLE IF EXISTS safety_etiquette_guide CASCADE;
DROP TABLE IF EXISTS course_chunks CASCADE;
DROP TABLE IF EXISTS wheelchair_accessible_segments CASCADE;
DROP TABLE IF EXISTS course_sub_segments CASCADE;
DROP TABLE IF EXISTS courses CASCADE;

-- pgvector 활성화
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. 코스 메타데이터 테이블 (부모 테이블 - 14개 주요 메타데이터 포함)
CREATE TABLE IF NOT EXISTS courses (
    id SERIAL PRIMARY KEY,
    course_name VARCHAR(100) NOT NULL UNIQUE,
    opening_date VARCHAR(50),
    total_distance_km NUMERIC(4, 1) NOT NULL,
    estimated_time_hours NUMERIC(3, 1) NOT NULL,
    estimated_time_text VARCHAR(50),
    difficulty VARCHAR(20) DEFAULT '중',
    course_description TEXT,
    has_wheelchair_segment VARCHAR(20) DEFAULT '없음',
    start_point VARCHAR(255) NOT NULL,
    end_point VARCHAR(255) NOT NULL,
    stamp_locations TEXT,
    lunch_info TEXT,
    crops VARCHAR(255),               -- 코스별 대표 재배 작물 (쉼표 구분)
    administrative_areas VARCHAR(255), -- 코스 경유 행정구역 읍·면·리 목록 (쉼표 구분)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. 세부 구간 분할 메타데이터 테이블 (부분 탐방 큐레이션용 테이블)
CREATE TABLE IF NOT EXISTS course_sub_segments (
    id SERIAL PRIMARY KEY,
    course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
    sub_segment_name VARCHAR(255) NOT NULL,
    start_point VARCHAR(255) NOT NULL,
    end_point VARCHAR(255) NOT NULL,
    distance_km NUMERIC(4, 1) NOT NULL,
    estimated_time_hours NUMERIC(3, 1) NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. 휠체어 보행 가능 구간 테이블
CREATE TABLE IF NOT EXISTS wheelchair_accessible_segments (
    id SERIAL PRIMARY KEY,
    course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
    segment_name VARCHAR(255) NOT NULL,
    start_address VARCHAR(255) NOT NULL,
    distance_km NUMERIC(3, 1) NOT NULL,
    difficulty_level VARCHAR(10) NOT NULL CHECK (difficulty_level IN ('상', '중', '하')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. 로컬 상점 추천 테이블 (비짓제주 Open API 연계 데이터 적재)
CREATE TABLE IF NOT EXISTS local_recommendations (
    id SERIAL PRIMARY KEY,
    course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
    crop_tag VARCHAR(100) NOT NULL,    -- 연계 작물 (예: 당근, 마늘, 감귤)
    title VARCHAR(255) NOT NULL,       -- 음식점/카페 이름
    address VARCHAR(255),              -- 지번 주소
    road_address VARCHAR(255),         -- 도로명 주소
    phone VARCHAR(50),                 -- 전화번호
    introduction TEXT,                 -- 소개글
    latitude NUMERIC(10, 8),           -- 위도
    longitude NUMERIC(11, 8),          -- 경도
    administrative_area VARCHAR(100),  -- 소재 행정구역 (읍·면·리)
    metadata JSONB DEFAULT '{}'::jsonb, -- 영업시간, 대표메뉴 등 상세 정보
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. 안전 수칙, 에티켓, 준비물 및 탐방 팁 가이드 테이블
CREATE TABLE IF NOT EXISTS safety_etiquette_guide (
    id SERIAL PRIMARY KEY,
    category VARCHAR(100) NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 6. 코스 본문 청크 및 벡터 테이블 (Upstage Solar Embedding 4096차원)
CREATE TABLE IF NOT EXISTS course_chunks (
    id SERIAL PRIMARY KEY,
    course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(4096),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 7. pgvector 하이브리드 코사인 유사도 검색용 Postgres RPC 함수 정의
CREATE OR REPLACE FUNCTION match_course_chunks (
  query_embedding VECTOR(4096),
  match_threshold FLOAT,
  match_count INT,
  filter_course_ids INT[]
)
RETURNS TABLE (
  id INT,
  course_id INT,
  title VARCHAR,
  content TEXT,
  similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    course_chunks.id,
    course_chunks.course_id,
    course_chunks.title,
    course_chunks.content,
    1 - (course_chunks.embedding <=> query_embedding) AS similarity
  FROM course_chunks
  WHERE (filter_course_ids IS NULL OR course_chunks.course_id = ANY(filter_course_ids))
    AND 1 - (course_chunks.embedding <=> query_embedding) > match_threshold
  ORDER BY course_chunks.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- 8. 제주 밭담문화·작물 생육 지식 벡터 테이블 (신규 - 외부 API 대체용 검증 문서 DB)
-- 주의: 기존 5개 테이블과 달리 DROP 구문이 없습니다. 이 블록만 단독으로 실행하세요 —
-- 파일 상단부터 전체를 재실행하면 기존 테이블(및 데이터)이 모두 삭제됩니다.
CREATE TABLE IF NOT EXISTS culture_crop_knowledge (
    id SERIAL PRIMARY KEY,
    crop_name VARCHAR(100),
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(4096),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 8-1. crop_seven_docs.json 스키마 반영 (2026-07-23, 신규 - 기존 컬럼은 그대로 유지하는 추가 전용
-- 마이그레이션). 주의: 이 ALTER 블록은 Supabase SQL 에디터에서 반드시 먼저(수동으로) 실행해야
-- scripts/run_culture_db_ingestion.py 가 새 필드를 적재할 수 있습니다. crop_name 컬럼은 하위 호환을
-- 위해 계속 유지하며, 신규 문서는 crop_name/target_crop 양쪽에 동일 값을 채웁니다.
ALTER TABLE culture_crop_knowledge ADD COLUMN IF NOT EXISTS knowledge_id VARCHAR(50);
ALTER TABLE culture_crop_knowledge ADD COLUMN IF NOT EXISTS category VARCHAR(50);
ALTER TABLE culture_crop_knowledge ADD COLUMN IF NOT EXISTS target_crop VARCHAR(100);
ALTER TABLE culture_crop_knowledge ADD COLUMN IF NOT EXISTS region_tag VARCHAR(255);
ALTER TABLE culture_crop_knowledge ADD COLUMN IF NOT EXISTS active_months INTEGER[];
ALTER TABLE culture_crop_knowledge ADD COLUMN IF NOT EXISTS season_stage VARCHAR(255);

CREATE OR REPLACE FUNCTION match_culture_chunks (
  query_embedding VECTOR(4096),
  match_threshold FLOAT,
  match_count INT
)
RETURNS TABLE (
  id INT,
  crop_name VARCHAR,
  title VARCHAR,
  content TEXT,
  similarity FLOAT,
  knowledge_id VARCHAR,
  category VARCHAR,
  target_crop VARCHAR,
  region_tag VARCHAR,
  active_months INTEGER[],
  season_stage VARCHAR
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    culture_crop_knowledge.id,
    culture_crop_knowledge.crop_name,
    culture_crop_knowledge.title,
    culture_crop_knowledge.content,
    1 - (culture_crop_knowledge.embedding <=> query_embedding) AS similarity,
    culture_crop_knowledge.knowledge_id,
    culture_crop_knowledge.category,
    culture_crop_knowledge.target_crop,
    culture_crop_knowledge.region_tag,
    culture_crop_knowledge.active_months,
    culture_crop_knowledge.season_stage
  FROM culture_crop_knowledge
  WHERE 1 - (culture_crop_knowledge.embedding <=> query_embedding) > match_threshold
  ORDER BY culture_crop_knowledge.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- 9. 제주관광공사 이동통신 빅데이터 기반 행정동별 방문객 통계 테이블 (신규 - Market Insight 연계용)
-- 주의: 기존 테이블들과 달리 DROP 구문이 없습니다. 이 블록만 단독으로 실행하세요.
CREATE TABLE IF NOT EXISTS visitor_analytics (
    id SERIAL PRIMARY KEY,
    year_month VARCHAR(7) NOT NULL,          -- 예: '2026-05'
    region_dong VARCHAR(50) NOT NULL,        -- 예: '구좌읍', '애월읍'
    total_visitors INT NOT NULL,             -- 당월 총 방문객 수
    yoy_growth_rate NUMERIC(5, 2),           -- 전년 대비 증감률 (%)
    female_ratio NUMERIC(5, 2),              -- 여성 방문객 비율 (%)
    male_ratio NUMERIC(5, 2),                -- 남성 방문객 비율 (%)
    youth_10s_ratio NUMERIC(5, 2),           -- 10대 이하 비율 (%)
    young_2030_ratio NUMERIC(5, 2),          -- 2030대 비율 (%)
    middle_4060_ratio NUMERIC(5, 2),         -- 4060대 비율 (%)
    senior_70s_ratio NUMERIC(5, 2),          -- 70대 이상 비율 (%)
    foreign_visitors INT,                    -- 외국인 방문객 수
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(year_month, region_dong)
);

CREATE INDEX IF NOT EXISTS idx_visitor_analytics_lookup
ON visitor_analytics (year_month, region_dong);


-- 10. courses 의 읍/면/동 단위 행정구역 컬럼 (2026-07-25, 신규 - 기존 컬럼은 그대로 유지하는
-- 추가 전용 마이그레이션).
-- 왜 필요한가: courses.administrative_areas 는 법정리/법정동 단위(예: "표선리,세화리,토산리")인데,
-- 같은 법정리 이름이 지리적으로 전혀 다른 읍/면에 동시에 존재합니다(data/jeju_districts.csv 기준
-- 9건 — 예: "세화리"가 구좌읍(제주시)과 표선면(서귀포시)에 각각 존재). 그래서 질의의
-- preferred_location(읍/면/동 단위)을 법정리 후보로 역확장해 administrative_areas 에 부분 문자열로
-- 찾는 기존 방식은 동명이인 충돌로 엉뚱한 지역의 코스를 매칭시켰습니다(라이브 재현: "구좌읍 감귤"
-- 질의가 표선면/남원읍의 4코스를 "세화리" 문자열 충돌로 구좌읍 코스로 오인).
-- 이 컬럼은 코스별로 실제 소속이 확정된 읍/면/동 이름만 쉼표로 담아 그 충돌을 제거합니다.
-- 채우는 방법: scripts/backfill_eup_myeon_dong_areas.py (anchor-consistency 알고리즘 — 같은 코스
-- 안의 모호하지 않은 법정리 토큰들이 가리키는 읍/면과 교집합을 취해 모호한 토큰을 해소).
-- 소비하는 곳: src/agent/nodes.py 의 _filter_course_ids_by_location (읍/면/동 단위 질의는 이 컬럼과
-- 완전 토큰 일치로만 매칭). 아직 백필되지 않은(NULL/빈 문자열) 행은 기존 법정리 역확장 방식으로
-- 폴백하므로, 이 ALTER 만 실행하고 백필을 미뤄도 동작은 유지됩니다.
-- 주의: 이 블록은 Supabase SQL 에디터에서 수동으로 단독 실행하세요(파일 상단부터 재실행하면
-- 기존 테이블과 데이터가 모두 삭제됩니다).
ALTER TABLE courses ADD COLUMN IF NOT EXISTS eup_myeon_dong_areas TEXT;
