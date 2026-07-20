import re

# 다양한 코스 표기 패턴 감지 (예: "01-1 우도 올레", "01 코스", "Course 01" 등)
COURSE_HEADER_PATTERN = r"(\b\d{1,2}(?:-\d+)?\s*코스|\b\d{1,2}-\d+\s+[가-힣]+\s*올레|\bCourse\s+\d{1,2}(?:-\d+)?)"

# 제주올레 공식 메타데이터 표준 교정 테이블 (무결성 검증용 표준 사양)
OFFICIAL_COURSE_METADATA = {
    "1코스": {"dist": 15.0, "time": 5.0, "start": "시흥초등학교", "end": "광치기해변"},
    "1-1코스": {
        "dist": 11.3,
        "time": 5.0,
        "start": "천진항/하우목동항",
        "end": "천진항/하우목동항",
    },
    "2코스": {"dist": 15.6, "time": 5.0, "start": "광치기해변", "end": "온평포구"},
    "3-A코스": {"dist": 20.9, "time": 7.0, "start": "온평포구", "end": "표선해수욕장"},
    "3-B코스": {"dist": 14.6, "time": 5.0, "start": "온평포구", "end": "표선해수욕장"},
    "3코스": {"dist": 20.9, "time": 7.0, "start": "온평포구", "end": "표선해수욕장"},
    "4코스": {"dist": 19.0, "time": 6.0, "start": "표선해수욕장", "end": "남원포구"},
    "5코스": {"dist": 13.4, "time": 4.0, "start": "남원포구", "end": "쇠소깍"},
    "6코스": {
        "dist": 11.0,
        "time": 4.0,
        "start": "쇠소깍",
        "end": "제주올레 여행자센터",
    },
    "7코스": {
        "dist": 17.6,
        "time": 6.0,
        "start": "제주올레 여행자센터",
        "end": "월평아우르메",
    },
    "7-1코스": {
        "dist": 15.7,
        "time": 5.0,
        "start": "서귀포 버스터미널",
        "end": "제주올레 여행자센터",
    },
    "8코스": {"dist": 19.6, "time": 6.0, "start": "월평아우르메", "end": "대평포구"},
    "9코스": {"dist": 11.8, "time": 4.0, "start": "대평포구", "end": "화순금모래해변"},
    "10코스": {
        "dist": 15.6,
        "time": 5.0,
        "start": "화순금모래해변",
        "end": "하모체육공원",
    },
    "10-1코스": {
        "dist": 4.2,
        "time": 2.0,
        "start": "가파도 상동포구",
        "end": "가파도 상동포구",
    },
    "11코스": {"dist": 17.3, "time": 6.0, "start": "하모체육공원", "end": "무릉외갓집"},
    "12코스": {"dist": 17.5, "time": 6.0, "start": "무릉외갓집", "end": "용수포구"},
    "13코스": {"dist": 15.9, "time": 5.0, "start": "용수포구", "end": "저지마을회관"},
    "14코스": {"dist": 19.1, "time": 6.0, "start": "저지마을회관", "end": "한림항"},
    "14-1코스": {
        "dist": 9.3,
        "time": 3.0,
        "start": "저지상권입구",
        "end": "오설록 티뮤지엄",
    },
    "15-A코스": {"dist": 16.5, "time": 6.0, "start": "한림항", "end": "고내포구"},
    "15-B코스": {"dist": 13.0, "time": 4.0, "start": "한림항", "end": "고내포구"},
    "15코스": {"dist": 16.5, "time": 6.0, "start": "한림항", "end": "고내포구"},
    "16코스": {"dist": 15.8, "time": 6.0, "start": "고내포구", "end": "광령초등학교"},
    "17코스": {"dist": 18.1, "time": 6.0, "start": "광령초등학교", "end": "관덕정분식"},
    "18코스": {"dist": 19.8, "time": 7.0, "start": "관덕정분식", "end": "조천만세동산"},
    "18-1코스": {"dist": 11.4, "time": 4.0, "start": "추자항", "end": "추자항"},
    "18-2코스": {"dist": 9.7, "time": 4.0, "start": "하추자 명도암", "end": "신양항"},
    "19코스": {"dist": 19.4, "time": 7.0, "start": "조천만세동산", "end": "김녕서포구"},
    "20코스": {
        "dist": 17.6,
        "time": 6.0,
        "start": "김녕서포구",
        "end": "제주해녀박물관",
    },
    "21코스": {"dist": 11.3, "time": 4.0, "start": "제주해녀박물관", "end": "종달바당"},
}


def split_ab_course_items(
    c_name: str,
    meta: dict,
    summary_info: str,
    map_info: str,
    detail_text: str,
    full_content: str,
) -> list[dict]:
    """A/B 코스가 결합된 메타데이터를 A코스와 B코스 2개의 독립 엔티티로 세분화 분리합니다."""
    num_part = re.search(r"\d+", c_name)
    prefix = num_part.group(0) if num_part else "3"

    open_a, open_b = meta["opening_date"], meta["opening_date"]
    m_open_ab = re.search(
        r"(?:개장|개)\s*:\s*A\s*코스\s*([^\s/]+(?:\s+[^\s/]+)*)\s*/\s*B\s*코스\s*([^\n]+)",
        summary_info,
    )
    if m_open_ab:
        open_a = m_open_ab.group(1).strip()
        open_b = m_open_ab.group(2).strip()

    time_a, time_b = meta["estimated_time_text"], meta["estimated_time_text"]
    m_time_ab = re.search(
        r"소요.*?\s*:\s*A\s*코스?\s*([^\n/]+)\s*/\s*B\s*코스?\s*([^\n]+)", summary_info
    )
    if m_time_ab:
        time_a = m_time_ab.group(1).strip()
        time_b = m_time_ab.group(2).strip()

    dist_a, dist_b = 0.0, 0.0
    m_dist_ab = re.search(
        r"(?:총\s*길이|총\s*길)\s*:\s*A\s*코스\s*([0-9\.]+)\s*km,\s*B\s*코스\s*([0-9\.]+)\s*km",
        summary_info,
        re.IGNORECASE,
    )
    if m_dist_ab:
        dist_a = float(m_dist_ab.group(1))
        dist_b = float(m_dist_ab.group(2))

    diff_a, diff_b = meta["difficulty"], meta["difficulty"]
    m_diff_ab = re.search(
        r"(?:난이도|난이)\s*:\s*A\s*코스?\s*-?\s*([^\s/]+)\s*/\s*B\s*코스?\s*-?\s*([^\n]+)",
        summary_info,
    )
    if m_diff_ab:
        diff_a = m_diff_ab.group(1).strip()
        diff_b = m_diff_ab.group(2).strip()

    off_a = OFFICIAL_COURSE_METADATA.get(f"{prefix}-A코스", {})
    off_b = OFFICIAL_COURSE_METADATA.get(f"{prefix}-B코스", {})

    final_dist_a = off_a.get("dist", dist_a if dist_a > 0 else 0.0)
    final_dist_b = off_b.get("dist", dist_b if dist_b > 0 else 0.0)

    final_time_a = off_a.get("time", 6.0)
    final_time_b = off_b.get("time", 4.0)

    start_a = off_a.get("start") or meta["start_point"] or "시작점"
    end_a = off_a.get("end") or meta["end_point"] or "종점"
    sub_a = parse_sub_segments_from_map_info(map_info, start_a, end_a, final_dist_a)

    start_b = off_b.get("start") or meta["start_point"] or "시작점"
    end_b = off_b.get("end") or meta["end_point"] or "종점"
    sub_b = parse_sub_segments_from_map_info(map_info, start_b, end_b, final_dist_b)

    item_a = {
        "course_name": f"{prefix}-A코스",
        "opening_date": open_a,
        "total_distance_km": final_dist_a,
        "estimated_time_hours": final_time_a,
        "estimated_time_text": time_a,
        "difficulty": diff_a,
        "course_description": meta["course_description"],
        "has_wheelchair_segment": meta["has_wheelchair_segment"],
        "start_point": start_a,
        "end_point": end_a,
        "stamp_locations": meta["stamp_locations"],
        "lunch_info": meta["lunch_info"],
        "sub_segments": sub_a,
        "summary_info": summary_info,
        "map_info": map_info,
        "detail_text": detail_text,
        "content": full_content,
    }

    item_b = {
        "course_name": f"{prefix}-B코스",
        "opening_date": open_b,
        "total_distance_km": final_dist_b,
        "estimated_time_hours": final_time_b,
        "estimated_time_text": time_b,
        "difficulty": diff_b,
        "course_description": meta["course_description"],
        "has_wheelchair_segment": meta["has_wheelchair_segment"],
        "start_point": start_b,
        "end_point": end_b,
        "stamp_locations": meta["stamp_locations"],
        "lunch_info": meta["lunch_info"],
        "sub_segments": sub_b,
        "summary_info": summary_info,
        "map_info": map_info,
        "detail_text": detail_text,
        "content": full_content,
    }

    return [item_a, item_b]


def parse_sub_segments_from_map_info(
    map_info: str, start_point: str, end_point: str, total_dist: float
) -> list[dict]:
    """2페이지 지도 개요 텍스트에서 거점 지명과 출발지 기준 누적 거리(km)를 정제하여 오름차순으로 파싱합니다."""
    if not map_info:
        segments = []
        if start_point:
            segments.append(
                {"segment_name": start_point, "distance_from_start_km": 0.0}
            )
        if end_point and total_dist > 0:
            segments.append(
                {"segment_name": end_point, "distance_from_start_km": total_dist}
            )
        return segments

    found_points = []

    # 1. 숫자 + km (줄바꿈/공백 허용) + 지명
    for m in re.finditer(
        r"([0-9]+(?:\.[0-9]+)?)\s*k?m?\s*[\n\s]*([가-힣]{2,15})", map_info
    ):
        dist = float(m.group(1))
        name = m.group(2).strip()
        if name not in [
            "올레",
            "코스",
            "우회로",
            "높낮이",
            "해발",
            "거리",
            "저작권",
            "공식안내소",
            "화장실",
            "구급함",
            "스탬프",
            "주변",
            "높낮이의",
        ]:
            found_points.append({"segment_name": name, "distance_from_start_km": dist})

    # 2. 지명 + 숫자 + km (줄바꿈/공백 허용)
    for m in re.finditer(
        r"([가-힣]{2,15})\s*[\n\s]*([0-9]+(?:\.[0-9]+)?)\s*k?m?", map_info
    ):
        name = m.group(1).strip()
        dist = float(m.group(2))
        if name not in [
            "총길이",
            "올레",
            "코스",
            "우회로",
            "높낮이",
            "해발",
            "거리",
            "저작권",
            "공식안내소",
            "화장실",
            "구급함",
            "스탬프",
            "주변",
            "높낮이의",
        ]:
            found_points.append({"segment_name": name, "distance_from_start_km": dist})

    valid_points = []
    seen_dists = set()
    max_limit = total_dist + 0.5 if total_dist > 0 else 30.0

    for p in sorted(found_points, key=lambda x: x["distance_from_start_km"]):
        d = p["distance_from_start_km"]
        if 0.0 <= d <= max_limit and d not in seen_dists:
            seen_dists.add(d)
            valid_points.append(p)

    # 0.0km에 시작점 추가
    if start_point:
        if not valid_points or valid_points[0]["distance_from_start_km"] > 0.5:
            valid_points.insert(
                0, {"segment_name": start_point, "distance_from_start_km": 0.0}
            )

    # total_dist에 종점 추가
    if end_point and total_dist > 0:
        if not valid_points or valid_points[-1]["distance_from_start_km"] < (
            total_dist - 0.5
        ):
            valid_points.append(
                {"segment_name": end_point, "distance_from_start_km": total_dist}
            )

    return valid_points


def normalize_course_name(name: str) -> str:
    """코스명 표기를 고유 키 형태(예: '1코스', '1-1코스')로 정규화합니다."""
    m = re.search(r"(\d{1,2}(?:-[A-Za-z0-9]+)?)", name)
    if m:
        num_part = m.group(1)
        parts = num_part.split("-")
        norm_parts = [p.lstrip("0") if p.lstrip("0") else "0" for p in parts]
        return f"{'-'.join(norm_parts)}코스"
    return name.strip()


def extract_course_name_from_first_page(lines: list[str], text_block: str) -> str:
    """간단 정보 1페이지의 텍스트에서 정확한 올레 코스명을 정규화하여 추출합니다."""
    # 1. "X코스 시작점 :" 패턴 우선 탐색 (예: "1-1코스 시작점 :", "10코스 시작점 :")
    m_start = re.search(r"(\d{1,2}(?:-[A-Za-z0-9]+)?\s*코스)\s*시작점", text_block)
    if m_start:
        return normalize_course_name(m_start.group(1))

    # 2. 상단 6줄 중 "01-1 우도 올레", "02 광치기...", "01" 등 탐색
    for l in lines[:6]:
        l_str = l.strip()
        if not l_str or l_str == "Jeju Olle Route" or "Jeju Olle Route" in l_str:
            continue
        m = re.search(
            r"(\b\d{1,2}(?:-[A-Za-z0-9]+)?(?:\s+[가-힣A-Za-z]+)?\s*올레|\b\d{1,2}(?:-[A-Za-z0-9]+)?\s*코스)",
            l_str,
        )
        if m:
            return normalize_course_name(m.group(1))
        m_num = re.match(r"^(\d{1,2}(?:-[A-Za-z0-9]+)?)$", l_str)
        if m_num:
            return normalize_course_name(m_num.group(1))

    # 3. fallback: 전체 text_block에서 첫 번째 코스명 패턴
    m_fallback = re.search(r"(\b\d{1,2}(?:-[A-Za-z0-9]+)?\s*코스)", text_block)
    if m_fallback:
        return normalize_course_name(m_fallback.group(1))

    return ""


def parse_summary_metadata(summary_info: str) -> dict:
    """1페이지 간단 정보 텍스트에서 핵심 메타데이터 필드 요소를 정규식으로 파싱합니다."""
    meta = {
        "opening_date": "정보 없음",
        "estimated_time_text": "",
        "difficulty": "정보 없음",
        "course_description": "정보 없음",
        "has_wheelchair_segment": "정보 없음",
        "stamp_locations": "정보 없음",
        "lunch_info": "정보 없음",
        "start_point": "",
        "end_point": "",
    }
    if not summary_info:
        return meta

    # 1. 개장 년도 / 날짜
    m_open = re.search(r"(?:개장|개)\s*:\s*([^\n]+)", summary_info)
    if m_open:
        meta["opening_date"] = m_open.group(1).strip()

    # 2. 소요시간 원본 텍스트 (예: "4~5시간", "5~6시간", "1~2시간")
    m_time_txt = re.search(r"(?:소요시간|소요|소요시)\s*:\s*([^\n]+)", summary_info)
    if m_time_txt:
        meta["estimated_time_text"] = m_time_txt.group(1).strip()

    # 3. 난이도
    m_diff = re.search(r"(?:난이도|난이)\s*:\s*([^\n]+)", summary_info)
    if m_diff:
        meta["difficulty"] = m_diff.group(1).strip()

    # 4. 코스 설명 (난이도 항목 아래의 특성 요약 문구)
    m_desc = re.search(
        r"(?:난이도|난이)\s*:\s*[^\n]+\n(.*?)(?=\n휠체어|\n\d+코스|\n스탬프|\Z)",
        summary_info,
        re.DOTALL,
    )
    if m_desc:
        desc_str = " ".join(
            [l.strip() for l in m_desc.group(1).split("\n") if l.strip()]
        )
        meta["course_description"] = desc_str

    # 5. 휠체어 구간 유무
    m_wheel = re.search(r"(?:휠체어\s*구간|휠체어\s*구)\s*:\s*([^\n]+)", summary_info)
    if m_wheel:
        meta["has_wheelchair_segment"] = m_wheel.group(1).strip()

    # 6. 시작점 / 종점 (1페이지 텍스트에서 추출 시도)
    m_start = re.search(
        r"(?:\d+(?:-[A-Za-z0-9]+)?\s*코스)?\s*(?:시작점|시)\s*:\s*([^\n]+)",
        summary_info,
    )
    if m_start:
        meta["start_point"] = m_start.group(1).strip()

    m_end = re.search(
        r"(?:\d+(?:-[A-Za-z0-9]+)?\s*코스)?\s*(?:종점|종)\s*:\s*([^\n]+)", summary_info
    )
    if m_end:
        meta["end_point"] = m_end.group(1).strip()

    # 7. 스탬프 찍는 곳
    m_stamp = re.search(
        r"스탬프\s*찍는\s*곳\s*\n(.*?)(?=\n점심은|\n\d+코스|\n완주|\Z)",
        summary_info,
        re.DOTALL,
    )
    if m_stamp:
        stamp_str = " ".join(
            [l.strip() for l in m_stamp.group(1).split("\n") if l.strip()]
        )
        meta["stamp_locations"] = stamp_str

    # 8. 점심은 어디에서 먹을까
    m_lunch = re.search(
        r"점심은\s*어디에서\s*먹을까\??\s*\n(.*?)(?=\n\d+코스 완주|\n완주|\n스탬프|\Z)",
        summary_info,
        re.DOTALL,
    )
    if m_lunch:
        lunch_str = " ".join(
            [l.strip() for l in m_lunch.group(1).split("\n") if l.strip()]
        )
        meta["lunch_info"] = lunch_str

    return meta


def parse_courses_structured(pages_data: list[dict]) -> list[dict]:
    """PDF 페이지 리스트를 입력받아 3단 구조 및 핵심 메타데이터 필드로 파싱합니다."""
    if not pages_data:
        return []

    # 1페이지(간단 정보) 시작 페이지 감지 (Jeju Olle Route 헤더 또는 1페이지 특유 키워드 기준)
    course_spans = []
    for i, p in enumerate(pages_data):
        text = p["text"]
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            continue

        top_lines = "\n".join(lines[:5])
        is_first_page = ("Jeju Olle Route" in top_lines) or (
            "개장 :" in text and "스탬프 찍는 곳" in text
        )

        if is_first_page:
            c_name = extract_course_name_from_first_page(lines, text)
            if c_name and c_name != "Jeju Olle Route":
                course_spans.append({"course_name": c_name, "start_idx": i})

    if not course_spans:
        full_text = "\n\n".join(p["text"] for p in pages_data)
        return parse_courses(full_text)

    parsed_results = []
    num_courses = len(course_spans)

    for idx, span in enumerate(course_spans):
        c_name = span["course_name"]
        start_p_idx = span["start_idx"]
        end_p_idx = (
            course_spans[idx + 1]["start_idx"]
            if (idx + 1) < num_courses
            else len(pages_data)
        )

        c_pages = pages_data[start_p_idx:end_p_idx]

        summary_info = c_pages[0]["text"] if len(c_pages) >= 1 else ""
        map_info = c_pages[1]["text"] if len(c_pages) >= 2 else ""
        detail_pages = c_pages[2:] if len(c_pages) >= 3 else []
        detail_text = (
            "\n\n".join(p["text"] for p in detail_pages) if detail_pages else ""
        )

        # summary_info에서 정형 필드 파싱
        meta = parse_summary_metadata(summary_info)

        # A코스/B코스 분리 기재가 확인되는 경우 (예: "A 코스", "B 코스" 등)
        is_ab_split = (
            ("A" in summary_info and "B" in summary_info)
            and ("코스" in summary_info or "올레" in summary_info)
        ) and (
            c_name in ["3코스", "15코스", "3-A코스", "15-A코스"]
            or "03-A" in summary_info
            or "15-A" in summary_info
        )

        full_content = f"### [간단 정보]\n{summary_info}\n\n### [코스 지도/개요]\n{map_info}\n\n### [상세 코스 본문]\n{detail_text}".strip()

        if is_ab_split:
            ab_items = split_ab_course_items(
                c_name, meta, summary_info, map_info, detail_text, full_content
            )
            parsed_results.extend(ab_items)
        else:
            # 공식 표준 메타데이터 교정
            official = OFFICIAL_COURSE_METADATA.get(c_name, {})

            dist_match = re.search(r"(\d+(?:\.\d+)?)\s*km", summary_info, re.IGNORECASE)
            time_match = re.search(
                r"(\d+(?:\.\d+)?)\s*(?:hours|시간)", summary_info, re.IGNORECASE
            )

            dist = float(dist_match.group(1)) if dist_match else 0.0
            time_h = float(time_match.group(1)) if time_match else 0.0

            final_dist = official.get("dist", dist if dist > 0 else 0.0)
            final_time = official.get("time", time_h if time_h > 0 else 0.0)
            final_time_text = meta.get("estimated_time_text") or (
                f"{final_time}시간" if final_time > 0 else "정보 없음"
            )
            final_start = official.get("start") or meta["start_point"] or "시작점"
            final_end = official.get("end") or meta["end_point"] or "종점"
            sub_segs = parse_sub_segments_from_map_info(
                map_info, final_start, final_end, final_dist
            )

            parsed_results.append(
                {
                    "course_name": c_name,
                    "opening_date": meta["opening_date"],
                    "total_distance_km": final_dist,
                    "estimated_time_hours": final_time,
                    "estimated_time_text": final_time_text,
                    "difficulty": meta["difficulty"],
                    "course_description": meta["course_description"],
                    "has_wheelchair_segment": meta["has_wheelchair_segment"],
                    "start_point": final_start,
                    "end_point": final_end,
                    "stamp_locations": meta["stamp_locations"],
                    "lunch_info": meta["lunch_info"],
                    "sub_segments": sub_segs,
                    "summary_info": summary_info,
                    "map_info": map_info,
                    "detail_text": detail_text,
                    "content": full_content,
                }
            )

    return parsed_results


def parse_courses(text: str) -> list[dict]:
    """가이드북 텍스트를 고유 코스 단위로 그룹화하고 제주올레 공식 메타데이터 표준과 무결성을 교정하여 파싱합니다."""
    if not text:
        return []

    splits = re.split(COURSE_HEADER_PATTERN, text, flags=re.IGNORECASE)
    if len(splits) < 2:
        return [
            {
                "course_name": "전체 코스",
                "total_distance_km": 0.0,
                "estimated_time_hours": 0.0,
                "start_point": "미정",
                "end_point": "미정",
                "summary_info": text.strip(),
                "map_info": "",
                "detail_text": text.strip(),
                "content": text.strip(),
            }
        ]

    grouped: dict[str, dict] = {}

    for i in range(1, len(splits), 2):
        raw_header = splits[i].strip()
        course_content = splits[i + 1].strip() if (i + 1) < len(splits) else ""

        norm_name = normalize_course_name(raw_header)

        dist_match = re.search(r"(\d+(?:\.\d+)?)\s*km", course_content, re.IGNORECASE)
        time_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:hours|시간)", course_content, re.IGNORECASE
        )

        dist = float(dist_match.group(1)) if dist_match else 0.0
        time_h = float(time_match.group(1)) if time_match else 0.0

        if norm_name not in grouped:
            grouped[norm_name] = {
                "course_name": norm_name,
                "total_distance_km": dist,
                "estimated_time_hours": time_h,
                "start_point": "시작점",
                "end_point": "종점",
                "content_blocks": [course_content],
            }
        else:
            if dist > 0.0 and dist < 50.0:  # 이상치(120km 등) 필터링
                grouped[norm_name]["total_distance_km"] = dist
            if time_h > 0.0:
                grouped[norm_name]["estimated_time_hours"] = time_h
            grouped[norm_name]["content_blocks"].append(course_content)

    results = []
    for c_name, data in grouped.items():
        # 공식 표준 메타데이터 교정 적용 (무결성 검증 결합)
        official = OFFICIAL_COURSE_METADATA.get(c_name, {})
        final_dist = official.get("dist", data["total_distance_km"])
        final_time = official.get("time", data["estimated_time_hours"])
        final_start = official.get("start", data["start_point"])
        final_end = official.get("end", data["end_point"])

        full_c = "\n\n".join(data["content_blocks"])
        results.append(
            {
                "course_name": c_name,
                "total_distance_km": final_dist,
                "estimated_time_hours": final_time,
                "start_point": final_start,
                "end_point": final_end,
                "summary_info": full_c[:200],
                "map_info": "",
                "detail_text": full_c,
                "content": full_c,
            }
        )

    return results


def chunk_by_subtitle(course_text: str) -> list[str]:
    """소제목(―, —, - 기호 등)을 기준으로 텍스트를 논리적 청크 단위로 분할합니다."""
    if not course_text:
        return []

    raw_chunks = re.split(r"\s*[―—\u2015\u2014]\s*", course_text)
    chunks = [c.strip() for c in raw_chunks if c.strip()]
    return chunks if chunks else [course_text.strip()]


def parse_sub_segments(course_name: str, course_text: str) -> list[dict]:
    """코스 본문 텍스트 내의 주요 경유지를 파싱하여 세부 탐방 구간 목록을 생성합니다."""
    if not course_text:
        return []

    waypoints = re.findall(
        r"([가-힣A-Za-z0-9\s]+(?:해변|포구|입구|마을회관|쉼터|정상|교차로|학교|공원|성|샘|오름))\s*(\d+(?:\.\d+)?)\s*km",
        course_text,
    )

    sub_segments = []
    if len(waypoints) >= 2:
        for i in range(len(waypoints) - 1):
            start_name = waypoints[i][0].strip()
            end_name = waypoints[i + 1][0].strip()

            try:
                start_dist = float(waypoints[i][1])
                end_dist = float(waypoints[i + 1][1])
                seg_dist = max(0.1, round(abs(end_dist - start_dist), 1))
            except ValueError:
                seg_dist = 2.5

            seg_time = round(seg_dist / 3.0, 1)

            sub_segments.append(
                {
                    "sub_segment_name": f"{course_name} 구간 ({start_name} ~ {end_name})",
                    "start_point": start_name,
                    "end_point": end_name,
                    "distance_km": seg_dist if seg_dist > 0 else 2.0,
                    "estimated_time_hours": seg_time if seg_time > 0 else 0.7,
                    "description": f"{start_name}에서 출발하여 {end_name}로 이어지는 세부 탐방 구간",
                }
            )

    return sub_segments
