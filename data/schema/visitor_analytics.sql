-- 제주관광공사 이동통신 빅데이터 기반 행정동별 방문객 통계 테이블
-- 이 DDL은 supabase/schema.sql (프로젝트 스키마 원본)에도 동일하게 포함되어 있습니다.
-- 두 파일 중 하나만 실행하면 되며, 이미 적용된 상태에서 중복 실행해도 안전합니다 (IF NOT EXISTS).

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
