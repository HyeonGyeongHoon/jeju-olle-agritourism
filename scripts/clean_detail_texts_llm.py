"""
scripts/clean_detail_texts_llm.py
==================================
course_detail_texts.json 의 각 코스 raw detail_text 를
Upstage Solar LLM 을 통해 정제합니다.

PDF 2단 레이아웃으로 인해 좌·우 컬럼 텍스트가 줄 단위로 혼재되어 있고,
네모 박스 안의 부가 정보(축제 안내, 체험 프로그램, 명소 소개 박스 등)도 포함되어 있음.
LLM 이 이를 정리하여 코스 본문 서술 텍스트만 깔끔하게 재구성합니다.

출력:
  data/extracted/course_detail_texts_cleaned.json   - 정제된 JSON
  data/extracted/course_detail_texts_cleaned.csv    - 검수용 CSV (utf-8-sig)
"""

import csv
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

INPUT_JSON = "data/extracted/course_detail_texts.json"
OUTPUT_JSON = "data/extracted/course_detail_texts_cleaned.json"
OUTPUT_CSV = "data/extracted/course_detail_texts_cleaned.csv"

UPSTAGE_API_URL = "https://api.upstage.ai/v1/chat/completions"
MODEL = "solar-pro"

SYSTEM_PROMPT = """당신은 제주올레 가이드북 편집 전문가입니다.
주어진 텍스트는 PDF 2단 레이아웃을 OCR한 결과물로, 다음과 같은 문제가 있습니다:
1. 왼쪽 컬럼과 오른쪽 컬럼의 텍스트가 줄 단위로 뒤섞여 있어 문장이 중간에 끊기고 서로 이어져 있음
2. 네모 박스 안의 부가 정보(축제 일정, 체험 프로그램 안내, 명소 소개 박스, 제주어 학습 코너, 덤 하나/덤 둘 등 사이드바 콘텐츠)가 본문에 혼재되어 있음

당신의 역할:
- 혼재된 2단 텍스트를 자연스러운 한국어 문단으로 재구성하세요
- 코스를 걷는 경로와 풍경, 역사, 지형 설명에 해당하는 본문 텍스트만 유지하세요
- 아래 유형의 내용은 완전히 제거하세요:
  * 축제/이벤트 안내 (기간, 장소, 내용 형식의 박스)
  * 체험 프로그램 홍보 문구
  * 제주어 학습 코너 (반갑수다예, 고맙수다예 등)
  * "+ 덤 하나", "+ 덤 둘", "+ 덤 셋" 사이드바
  * 시설/박물관 입장료·연락처·웹사이트 정보
  * 페이지 번호, 저작권 문구, 워터마크
- 출력은 정제된 본문 텍스트만 반환하세요. JSON이나 마크다운 포맷 없이 순수 텍스트로 반환하세요."""

USER_PROMPT_TEMPLATE = """다음은 제주올레 {course_name}의 3페이지 이후 상세 코스 정보 텍스트입니다.
위 지침에 따라 2단 레이아웃 혼재 문제를 해결하고 본문 텍스트만 깔끔하게 재구성해 주세요.

---
{raw_text}
---

정제된 본문 텍스트:"""


def call_solar_llm(course_name: str, raw_text: str, api_key: str) -> str:
    """Upstage Solar LLM API를 호출하여 텍스트를 정제합니다."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 3000자 초과 시 분할하지 않고 그대로 전달 (solar-pro 는 충분한 컨텍스트 지원)
    user_content = USER_PROMPT_TEMPLATE.format(
        course_name=course_name,
        raw_text=raw_text,
    )

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,  # 낮은 온도 = 일관성 있는 정제
        "max_tokens": 4096,
    }

    max_retries = 4
    for attempt in range(max_retries):
        try:
            response = requests.post(
                UPSTAGE_API_URL, headers=headers, json=payload, timeout=60
            )
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()
            elif response.status_code == 429:
                sleep_time = (attempt + 1) * 3
                print(f"    [!] Rate limit (429). {sleep_time}초 대기 후 재시도...")
                time.sleep(sleep_time)
            else:
                print(f"    [!] API 오류 {response.status_code}: {response.text[:200]}")
                response.raise_for_status()
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"Upstage API 호출 실패 (재시도 초과): {e}")
            wait = (attempt + 1) * 2
            print(f"    [!] 요청 오류. {wait}초 대기 후 재시도... ({e})")
            time.sleep(wait)

    raise RuntimeError("Upstage API 호출 실패 (최대 재시도 횟수 초과)")


def run(test_course: str = None):
    """
    전체 코스 또는 단일 코스 테스트 모드로 실행합니다.

    Args:
        test_course: 테스트할 코스명 (예: "1코스"). None이면 전체 실행.
    """
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        print("[ERROR] UPSTAGE_API_KEY 환경 변수가 설정되지 않았습니다.")
        sys.exit(1)

    if not os.path.exists(INPUT_JSON):
        print(f"[ERROR] 입력 파일을 찾을 수 없습니다: {INPUT_JSON}")
        sys.exit(1)

    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        courses = json.load(f)

    # 테스트 모드: 단일 코스만 처리
    if test_course:
        courses = [c for c in courses if c["course_name"] == test_course]
        if not courses:
            print(f"[ERROR] '{test_course}' 코스를 찾을 수 없습니다.")
            sys.exit(1)
        print(f"[*] 테스트 모드: '{test_course}' 단일 코스만 처리합니다.")

    total = len(courses)
    print(f"[*] 총 {total}개 코스 LLM 정제 시작 (모델: {MODEL})")
    print(f"    입력 파일: {INPUT_JSON}\n")

    results = []

    for idx, course in enumerate(courses, 1):
        course_name = course["course_name"]
        raw_text = course.get("detail_text", "")

        if not raw_text.strip():
            print(f"  [{idx}/{total}] {course_name}: 텍스트 없음 — 건너뜀")
            results.append({
                "course_name": course_name,
                "title": course["title"],
                "detail_text": "",
            })
            continue

        print(f"  [{idx}/{total}] {course_name}: {len(raw_text)}자 → LLM 정제 중...")

        try:
            cleaned = call_solar_llm(course_name, raw_text, api_key)
            char_diff = len(cleaned) - len(raw_text)
            sign = "+" if char_diff >= 0 else ""
            print(f"         완료: {len(cleaned)}자 ({sign}{char_diff}자)")

            results.append({
                "course_name": course_name,
                "title": course["title"],
                "detail_text": cleaned,
            })

            # API 과부하 방지 — 코스 간 1초 대기
            if idx < total:
                time.sleep(1)

        except Exception as e:
            print(f"         [ERROR] LLM 호출 실패: {e}")
            # 실패 시 원본 텍스트 유지
            results.append({
                "course_name": course_name,
                "title": course["title"],
                "detail_text": raw_text,  # fallback: 원본
            })

    # JSON 저장
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[+] JSON 저장 완료: {OUTPUT_JSON} ({len(results)}개 코스)")

    # CSV 저장 (검수용)
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["course_name", "title", "char_count", "detail_text"])
        for r in results:
            writer.writerow([
                r["course_name"],
                r["title"],
                len(r["detail_text"]),
                r["detail_text"],
            ])
    print(f"[+] CSV 저장 완료: {OUTPUT_CSV}")

    # 요약
    filled = sum(1 for r in results if r["detail_text"])
    print(f"\n{'='*50}")
    print(f"[ 정제 결과 요약 ]")
    print(f"  처리 코스 수: {len(results)}개 (성공: {filled}개)")
    total_chars = sum(len(r["detail_text"]) for r in results)
    print(f"  총 정제 텍스트 길이: {total_chars}자")
    print(f"{'='*50}")
    print(f"\n[OK] LLM 정제 완료!")
    print(f"     임베딩 입력 파일: {OUTPUT_JSON}")
    print(f"     검수용 CSV:       {OUTPUT_CSV}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Upstage Solar LLM으로 코스 상세 텍스트를 정제합니다."
    )
    parser.add_argument(
        "--test",
        type=str,
        default=None,
        metavar="COURSE_NAME",
        help="테스트할 코스명 (예: --test '1코스'). 미지정 시 전체 실행.",
    )
    args = parser.parse_args()

    run(test_course=args.test)
