import json
import os
import sys

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingestion.convert_artifacts import (
    ensure_extracted_dir,
    export_to_csv,
    export_to_json,
    export_to_markdown,
)
from src.ingestion.parser import (
    chunk_by_subtitle,
    parse_courses_structured,
    parse_sub_segments,
)
from src.ingestion.pdf_extractor import extract_pages_from_pdf


def run():
    pdf_path = "data/jeju_olle_guidebook.pdf"
    print(f"[*] PDF 페이지별 텍스트 추출 시작 (27~134 페이지): {pdf_path}")
    pages_data = extract_pages_from_pdf(pdf_path, start_page=27, end_page=134)
    total_len = sum(len(p["text"]) for p in pages_data)
    print(
        f"[+] 총 추출 페이지 수: {len(pages_data)} 개, 총 텍스트 길이: {total_len} 자"
    )

    print(
        "[*] 코스 데이터 3단 구조(1페이지 간단정보/2페이지 지도개요/3페이지이후 본문) 파싱 중..."
    )
    courses = parse_courses_structured(pages_data)

    total_chunks = 0
    for c in courses:
        if not c.get("sub_segments"):
            c["sub_segments"] = parse_sub_segments(
                c["course_name"], c.get("detail_text") or c.get("content", "")
            )
        c["chunks"] = chunk_by_subtitle(c.get("detail_text") or c.get("content", ""))
        total_chunks += len(c["chunks"])

    print(f"[+] 파싱된 총 코스 수: {len(courses)} 개")
    print(f"[+] 생성된 총 청크 수: {total_chunks} 개")

    ensure_extracted_dir("data/extracted")

    md_path = export_to_markdown(courses, "data/extracted/courses_full.md")
    print(f"[+] 마크다운 저장 완료: {md_path}")

    json_path = export_to_json(courses, "data/extracted/courses_metadata.json")
    print(f"[+] 메타데이터 JSON 저장 완료: {json_path}")

    chunks_csv, wheelchair_csv = export_to_csv(
        courses,
        "data/extracted/course_chunks.csv",
        "data/extracted/wheelchair_segments.csv",
    )
    print(f"[+] 청크 CSV 저장 완료: {chunks_csv}")
    print(f"[+] 휠체어 구간 CSV 저장 완료: {wheelchair_csv}")

    # parsed_courses_summary.json 생성
    summary_path = "data/parsed_courses_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(courses, f, ensure_ascii=False, indent=2)
    print(f"[+] 전체 상세 요약 JSON 저장 완료: {summary_path}")

    print("\n[OK] PDF 데이터 추출 및 extracted 아티팩트 생성 완료!")


if __name__ == "__main__":
    run()
