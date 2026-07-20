"""
29개 올레길 코스의 지도 페이지(2번째 페이지)를 PNG 이미지로 변환하고,
Upstage Document Parse API로 sub_segments 데이터를 정밀 추출하는 파이프라인 스크립트입니다.
"""

import json
import os
import sys
import time

sys.path.append("c:/Users/hgh0407/Desktop/jeju-olle-docent")

from src.ingestion.pdf_image_extractor import extract_map_page_as_image
from src.ingestion.vision_extractor import extract_sub_segments_from_image

# 코스별 지도 페이지 번호 매핑 (detect_map_pages.py 결과 기반)
COURSE_MAP_PAGES = {
    "1코스": 28,
    "1-1코스": 32,
    "2코스": 36,
    "3코스": 40,
    "4코스": 45,
    "5코스": 49,
    "6코스": 53,
    "7코스": 57,
    "7-1코스": 61,
    "8코스": 65,
    "9코스": 69,
    "10코스": 72,
    "10-1코스": 76,
    "11코스": 80,
    "12코스": 84,
    "13코스": 88,
    "14코스": 92,
    "14-1코스": 96,
    "15코스": 100,
    "16코스": 105,
    "17코스": 109,
    "18코스": 113,
    "18-1코스": 117,
    "18-2코스": 121,
    "19코스": 125,
    "20코스": 129,
    "21코스": 133,
    # A/B 코스는 3코스, 15코스 지도 페이지 공유
    "3-A코스": 40,
    "3-B코스": 40,
    "15-A코스": 100,
    "15-B코스": 100,
}

PDF_PATH = "data/jeju_olle_guidebook.pdf"
MAP_IMG_DIR = "data/map_images"
OUTPUT_JSON = "data/extracted/sub_segments_vision.json"


def run():
    print(
        f"[*] 지도 이미지 추출 및 Vision AI 분석 시작 (총 {len(COURSE_MAP_PAGES)}개 코스)"
    )
    os.makedirs(MAP_IMG_DIR, exist_ok=True)

    results = {}

    for course_name, page_num in COURSE_MAP_PAGES.items():
        safe_name = course_name.replace("/", "-").replace(" ", "_")
        img_path = os.path.join(MAP_IMG_DIR, f"{safe_name}_p{page_num:03d}.png")

        # 1. 이미지 추출 (항상 재추출)
        print(f"  [이미지 추출] {course_name} (PDF 페이지 {page_num})")
        try:
            extracted_img = extract_map_page_as_image(PDF_PATH, page_num, MAP_IMG_DIR)
            # safe_name 기준 경로로 이름 변경
            if os.path.exists(extracted_img) and extracted_img != img_path:
                if os.path.exists(img_path):
                    os.remove(img_path)
                os.rename(extracted_img, img_path)
        except Exception as e:
            print(f"  [오류] {course_name} 이미지 추출 실패: {e}")
            continue

        # 2. Vision AI 분석
        print(f"  [Vision API 호출] {course_name}")
        try:
            sub_segs = extract_sub_segments_from_image(img_path, course_name)
            results[course_name] = sub_segs
            print(f"    → {len(sub_segs)}개 거점 추출")
            for s in sub_segs[:3]:
                print(f"       {s['segment_name']} : {s['distance_from_start_km']}km")
            time.sleep(0.5)  # API rate limit 방지
        except Exception as e:
            print(f"  [오류] {course_name} Vision API 분석 실패: {e}")
            results[course_name] = []

    # 3. 결과 저장
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] sub_segments Vision 추출 완료: {OUTPUT_JSON}")
    print(
        f"     총 {len(results)}개 코스, 성공 {sum(1 for v in results.values() if v)}개"
    )


if __name__ == "__main__":
    run()
