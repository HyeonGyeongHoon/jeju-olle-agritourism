"""
scripts/run_visitor_analytics_ingestion.py
=============================================
data/raw_data/reports/*.pdf (제주관광공사 월간 이동통신 빅데이터 기반 방문 패턴 분석 보고서)를
파싱하여 Supabase `visitor_analytics` 테이블에 upsert 합니다.

주의 (Gate B): 이 스크립트는 실 Supabase DB 적재를 수행하는 비가역적 작업입니다.
사용자의 사전 승인 없이 자동 실행하지 마세요. 실행 전 supabase/schema.sql (또는
data/schema/visitor_analytics.sql)의 `visitor_analytics` 테이블이 이미 생성되어 있어야 합니다.
"""

import glob
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingestion.database_loader import get_supabase_client
from src.ingestion.parse_visitor_pdf import parse_visitor_pdf, upsert_visitor_analytics

REPORTS_GLOB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "raw_data", "reports", "*.pdf"
)


def run():
    pdf_paths = sorted(glob.glob(REPORTS_GLOB))
    if not pdf_paths:
        print(f"[!] 적재 대상 PDF를 찾지 못했습니다: {REPORTS_GLOB}")
        return

    client = get_supabase_client()

    for pdf_path in pdf_paths:
        print(f"[*] 파싱 중: {os.path.basename(pdf_path)}")
        records = parse_visitor_pdf(pdf_path)
        if not records:
            print(f"[!] '{pdf_path}' 파싱 결과가 없어 건너뜁니다.")
            continue

        year_month = records[0]["year_month"]
        print(f"[*] {year_month} 기준 {len(records)}개 행정동 레코드 upsert 중...")
        upsert_visitor_analytics(client, records)

    print("\n[OK] 제주 관광 빅데이터(visitor_analytics) 적재가 완료되었습니다!")


if __name__ == "__main__":
    run()
