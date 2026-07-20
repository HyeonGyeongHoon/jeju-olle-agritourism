"""
Upstage Document Parse API를 사용하여 코스 지도 이미지에서
sub_segments (거점명 + 누적 거리) 데이터를 추출하는 모듈입니다.
"""

import os
import re

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))
if not os.getenv("UPSTAGE_API_KEY"):
    load_dotenv()  # fallback: 현재 디렉토리

UPSTAGE_API_KEY = os.getenv("UPSTAGE_API_KEY", "")
UPSTAGE_DOCUMENT_PARSE_URL = "https://api.upstage.ai/v1/document-digitization"

# 노이즈 필터 목록 (지명이 아닌 토큰)
_NOISE_TOKENS = {
    "km",
    "S",
    "F",
    "N",
    "E",
    "W",
    "A",
    "B",
    "I",
    "II",
    "IT",
    "T",
    "코스",
    "올레",
    "시간",
    "해발",
    "line",
    "경로",
    "제주올레",
    "etuoR",
    "ellO",
    "ujeJ",
    "저작권법에",
}


def extract_sub_segments_from_image(image_path: str, course_name: str) -> list[dict]:
    """이미지 파일을 Upstage Document Parse API로 분석하여 sub_segments를 추출합니다."""
    if not UPSTAGE_API_KEY:
        raise ValueError("UPSTAGE_API_KEY가 설정되지 않았습니다.")

    headers = {"Authorization": f"Bearer {UPSTAGE_API_KEY}"}
    with open(image_path, "rb") as f:
        files = {"document": (os.path.basename(image_path), f, "image/png")}
        data = {"model": "document-parse"}
        response = requests.post(
            UPSTAGE_DOCUMENT_PARSE_URL,
            headers=headers,
            files=files,
            data=data,
            timeout=60,
        )

    response.raise_for_status()
    result = response.json()

    # 1. <img alt="..."> 속성에서 OCR 텍스트 추출 (최우선)
    html_text = result.get("content", {}).get("html", "")
    alt_texts = re.findall(r'alt="(.*?)"(?:\s+data-coord)?', html_text, re.DOTALL)
    ocr_text = "\n".join(alt_texts)

    # 2. alt가 없으면 elements의 text 필드 사용
    if not ocr_text.strip():
        for element in result.get("elements", []):
            txt = element.get("content", {}).get("text", "")
            if txt:
                ocr_text += txt + "\n"

    return parse_sub_segments_from_ocr_text(ocr_text, course_name)


def parse_sub_segments_from_ocr_text(ocr_text: str, course_name: str) -> list[dict]:
    """OCR로 추출된 텍스트에서 거점명과 누적 거리를 파싱합니다.

    지도 OCR 특성상 거점명과 거리가 인접 줄에 위치하므로
    줄 단위로 순서대로 읽어 km 토큰 앞/뒤 지명을 연관시킵니다.
    """
    lines = [l.strip() for l in ocr_text.split("\n") if l.strip()]
    found_points = []

    km_pat = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*km$", re.IGNORECASE)
    km_inline_pat = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*km")
    korean_pat = re.compile(r"[가-힣]{2,}")

    def is_valid_name(name: str) -> bool:
        name = name.strip()
        if not name or len(name) < 2 or len(name) > 30:
            return False
        if name in _NOISE_TOKENS:
            return False
        if re.fullmatch(r"[0-9.\s/A-Za-z]+", name):  # 숫자/영문만
            return False
        if not korean_pat.search(name):  # 한국어 없음
            return False
        return True

    def clean_name(name: str) -> str:
        # 앞뒤 숫자·기호 제거
        name = re.sub(r"^[\d.\s/A-Z]+", "", name).strip()
        name = re.sub(r"[\d.\s/A-Z]+$", "", name).strip()
        return name

    # 줄 단위 파싱: km 값 줄 앞/뒤 지명 연관
    for i, line in enumerate(lines):
        # 케이스 1: 줄이 순수 km 값인 경우
        m = km_pat.match(line)
        if m:
            dist = float(m.group(1))
            if not (0.0 <= dist <= 100.0):
                continue
            # 앞 줄 또는 뒤 줄에서 지명 탐색
            candidates = []
            if i > 0:
                candidates.append(lines[i - 1])
            if i < len(lines) - 1:
                candidates.append(lines[i + 1])
            for cand in candidates:
                # 인라인 km 없는 지명 줄
                if not km_inline_pat.search(cand):
                    cleaned = clean_name(cand)
                    if is_valid_name(cleaned):
                        found_points.append(
                            {
                                "segment_name": cleaned,
                                "distance_from_start_km": round(dist, 1),
                            }
                        )
                        break
            continue

        # 케이스 2: "지명 km" 또는 "km 지명" 인라인 패턴
        # "지명 Xkm" 패턴
        m2 = re.match(r"^([가-힣][^0-9]{1,20}?)\s+([0-9]+(?:\.[0-9]+)?)\s*km", line)
        if m2:
            name = clean_name(m2.group(1))
            dist = float(m2.group(2))
            if is_valid_name(name) and 0.0 <= dist <= 100.0:
                found_points.append(
                    {"segment_name": name, "distance_from_start_km": round(dist, 1)}
                )
            continue

        # "Xkm 지명" 패턴
        m3 = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*km\s+([가-힣].{1,20}?)$", line)
        if m3:
            dist = float(m3.group(1))
            name = clean_name(m3.group(2))
            if is_valid_name(name) and 0.0 <= dist <= 100.0:
                found_points.append(
                    {"segment_name": name, "distance_from_start_km": round(dist, 1)}
                )
            continue

    # 중복 제거: 같은 거리값은 한국어 포함 비율 높은 것 우선
    seen: dict[float, dict] = {}
    for p in found_points:
        d = p["distance_from_start_km"]
        name = p["segment_name"]
        if d not in seen:
            seen[d] = p
        else:
            # 한국어 글자 수가 더 많은 것 선택
            existing_korean = len(re.findall(r"[가-힣]", seen[d]["segment_name"]))
            new_korean = len(re.findall(r"[가-힣]", name))
            if new_korean > existing_korean:
                seen[d] = p

    return sorted(seen.values(), key=lambda x: x["distance_from_start_km"])
