-- Supabase 데이터베이스 초기화 및 리셋 구문 (기존 테이블 삭제)
DROP TABLE IF EXISTS safety_etiquette_guide CASCADE;
DROP TABLE IF EXISTS course_chunks CASCADE;
DROP TABLE IF EXISTS wheelchair_accessible_segments CASCADE;
DROP TABLE IF EXISTS course_sub_segments CASCADE;
DROP TABLE IF EXISTS courses CASCADE;

-- pgvector 활성화
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. 코스 메타데이터 테이블 (부모 테이블 - 12개 주요 메타데이터 포함)
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

-- 4. 안전 수칙, 에티켓, 준비물 및 탐방 팁 가이드 테이블
CREATE TABLE IF NOT EXISTS safety_etiquette_guide (
    id SERIAL PRIMARY KEY,
    category VARCHAR(100) NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. 코스 본문 청크 및 벡터 테이블 (Upstage Solar Embedding 4096차원)
CREATE TABLE IF NOT EXISTS course_chunks (
    id SERIAL PRIMARY KEY,
    course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(4096),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
