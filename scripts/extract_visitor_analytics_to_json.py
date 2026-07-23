"""
scripts/extract_visitor_analytics_to_json.py
=================================================
data/raw_data/reports/*.pdf 를 파싱만 하고 Supabase에는 적재하지 않은 채,
결과를 로컬 JSON 파일(data/visitor_analytics/extracted_all_months.json)로 저장합니다.
Gate B(실 DB 적재) 이전에 파싱 결과를 로컬에서 직접 검토/검증하기 위한 스크립트입니다.
"""

import glob
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingestion.parse_visitor_pdf import parse_visitor_pdf

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_GLOB = os.path.join(REPO_ROOT, "data", "raw_data", "reports", "*.pdf")
OUTPUT_PATH = os.path.join(REPO_ROOT, "data", "visitor_analytics", "extracted_all_months.json")


def run():
    pdf_paths = sorted(glob.glob(REPORTS_GLOB))
    if not pdf_paths:
        print(f"[!] 적재 대상 PDF를 찾지 못했습니다: {REPORTS_GLOB}")
        return

    all_records = []
    for pdf_path in pdf_paths:
        print(f"[*] 파싱 중: {os.path.basename(pdf_path)}")
        records = parse_visitor_pdf(pdf_path)
        if not records:
            print(f"[!] '{pdf_path}' 파싱 결과가 없어 건너뜁니다.")
            continue
        print(f"    -> {records[0]['year_month']} 기준 {len(records)}개 행정동")
        all_records.extend(records)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] 총 {len(all_records)}개 레코드를 {OUTPUT_PATH} 에 저장했습니다.")


if __name__ == "__main__":
    run()
