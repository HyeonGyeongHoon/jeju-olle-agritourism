-- pgvector 활성화
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. 코스 메타데이터 테이블
CREATE TABLE IF NOT EXISTS courses (
    id SERIAL PRIMARY KEY,
    course_name VARCHAR(100) NOT NULL UNIQUE,
    total_distance_km NUMERIC(4, 1) NOT NULL,
    estimated_time_hours NUMERIC(3, 1) NOT NULL,
    start_point VARCHAR(255) NOT NULL,
    end_point VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. 휠체어 보행 가능 구간 테이블 (정적 시딩 데이터용 구조)
CREATE TABLE IF NOT EXISTS wheelchair_accessible_segments (
    id SERIAL PRIMARY KEY,
    course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
    segment_name VARCHAR(255) NOT NULL,
    start_address VARCHAR(255) NOT NULL,
    distance_km NUMERIC(3, 1) NOT NULL,
    difficulty_level VARCHAR(10) NOT NULL CHECK (difficulty_level IN ('상', '중', '하')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. 코스 본문 청크 및 벡터 테이블
CREATE TABLE IF NOT EXISTS course_chunks (
    id SERIAL PRIMARY KEY,
    course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(1536),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 휠체어 구간 고정 Seed 데이터 삽입 (하이브리드 참조 매핑)
-- 이 데이터는 고정 데이터이므로 인제스천 파이프라인에서 동적으로 파싱하지 않고 DDL 셋업 시 정적으로 적재합니다.
INSERT INTO wheelchair_accessible_segments (course_id, segment_name, start_address, distance_km, difficulty_level)
VALUES 
((SELECT id FROM courses WHERE course_name = '1코스' LIMIT 1), '1코스 휠체어 구간 (종달리 옛 소금밭 ~ 성산갑문 입구 구간)', '제주시 구좌읍 종달리 814-5', 4.6, '중'),
((SELECT id FROM courses WHERE course_name = '10-1코스' LIMIT 1), '10-1코스 휠체어 구간 (가파도 전 구간)', '가파도 상동포구', 4.2, '상'),
((SELECT id FROM courses WHERE course_name = '4코스' LIMIT 1), '4코스 휠체어 구간 (해비치호텔&리조트 ~ 가마리개 쉼터 구간)', '서귀포시 표선면 표선리 40-76', 4.8, '중'),
((SELECT id FROM courses WHERE course_name = '5코스' LIMIT 1), '5코스 휠체어 구간 (국립수산과학원 ~ 위미항 구간)', '서귀포시 남원읍 위미리 785-1', 2.7, '상'),
((SELECT id FROM courses WHERE course_name = '6코스' LIMIT 1), '6코스 휠체어 구간 (쇠소깍 ~ 보목포구 구간)', '서귀포시 하효동 999', 2.6, '중'),
((SELECT id FROM courses WHERE course_name = '8코스' LIMIT 1), '8코스 휠체어 구간 (논짓물 ~ 대평포구)', '서귀포시 하예동 532-3', 3.6, '상'),
((SELECT id FROM courses WHERE course_name = '10코스' LIMIT 1), '10코스 휠체어 구간 (사계포구 ~ 송악산 주차장 구간)', '서귀포시 안덕면 사계리 2125', 2.9, '중'),
((SELECT id FROM courses WHERE course_name = '12코스' LIMIT 1), '12코스 휠체어 구간 (엉알길 입구 ~ 자구내포구 입구 구간)', '제주시 한경면 고산리 3674-2', 1.1, '중'),
((SELECT id FROM courses WHERE course_name = '14코스' LIMIT 1), '14코스 휠체어 구간 (일성콘도 ~ 금능해수욕장 입구 구간)', '제주시 한림읍 금능리 1621-6', 2.1, '중'),
((SELECT id FROM courses WHERE course_name = '17코스' LIMIT 1), '17코스 휠체어 구간 (도두봉 내려오는 길 ~ 용연다리 구간)', '제주시 도두2동 1611', 4.4, '중')
ON CONFLICT DO NOTHING;
