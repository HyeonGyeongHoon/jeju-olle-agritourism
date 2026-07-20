import csv
import json
import os


def ensure_extracted_dir(dir_path: str = "data/extracted") -> str:
    """중간 아티팩트 보관 디렉터리가 존재하지 않으면 생성합니다."""
    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
    return dir_path


def export_to_markdown(
    courses: list[dict], output_path: str = "data/extracted/courses_full.md"
) -> str:
    """파싱된 코스 텍스트를 읽기 쉬운 Markdown 문서로 내보냅니다."""
    ensure_extracted_dir(os.path.dirname(output_path))
    md_lines = ["# 제주올레 가이드북 정제 마크다운 본문\n"]

    for c in courses:
        md_lines.append(f"## {c['course_name']}\n")
        md_lines.append(f"- **총 거리**: {c.get('total_distance_km', 0.0)} km")
        md_lines.append(
            f"- **예상 소요시간**: {c.get('estimated_time_hours', 0.0)} 시간"
        )
        md_lines.append(f"- **시작점**: {c.get('start_point', '미정')}")
        md_lines.append(f"- **종점**: {c.get('end_point', '미정')}\n")

        if c.get("sub_segments"):
            md_lines.append("### 주요 세부 탐방 구간")
            for sub in c["sub_segments"]:
                name = sub.get("segment_name") or sub.get("sub_segment_name", "구간")
                dist = sub.get("distance_from_start_km") or sub.get("distance_km", 0.0)
                md_lines.append(f"- **{name}**: 출발지로부터 {dist} km")
            md_lines.append("")

        if c.get("summary_info"):
            md_lines.append("### [1페이지 간단 정보]")
            md_lines.append(c["summary_info"].strip())
            md_lines.append("")

        if c.get("map_info"):
            md_lines.append("### [2페이지 코스 지도 및 개요]")
            md_lines.append(c["map_info"].strip())
            md_lines.append("")

        if c.get("detail_text"):
            md_lines.append("### [3페이지 이후 상세 코스 정보]")
            md_lines.append(c["detail_text"].strip())
            md_lines.append("")

        md_lines.append("\n---\n")

    content = "\n".join(md_lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path


def export_to_json(
    courses: list[dict], output_path: str = "data/extracted/courses_metadata.json"
) -> str:
    """코스 메타데이터를 사용자가 지정한 11개 전용 핵심 필드 포맷으로만 정형 JSON 내보냅니다."""
    ensure_extracted_dir(os.path.dirname(output_path))

    json_data = []
    for c in courses:
        json_data.append(
            {
                "course_name": c["course_name"],
                "opening_date": c.get("opening_date", "정보 없음"),
                "total_distance_km": c.get("total_distance_km", 0.0),
                "estimated_time_hours": c.get("estimated_time_hours", 0.0),
                "estimated_time_text": c.get("estimated_time_text", ""),
                "difficulty": c.get("difficulty", "정보 없음"),
                "course_description": c.get("course_description", "정보 없음"),
                "has_wheelchair_segment": c.get("has_wheelchair_segment", "정보 없음"),
                "start_point": c.get("start_point", "미정"),
                "end_point": c.get("end_point", "미정"),
                "stamp_locations": c.get("stamp_locations", "정보 없음"),
                "lunch_info": c.get("lunch_info", "정보 없음"),
                "sub_segments": c.get("sub_segments", []),
            }
        )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    return output_path


def export_to_csv(
    courses: list[dict],
    chunks_output_path: str = "data/extracted/course_chunks.csv",
    wheelchair_output_path: str = "data/extracted/wheelchair_segments.csv",
) -> tuple[str, str]:
    """본문 청크 및 휠체어 정적 데이터를 CSV 파일(utf-8-sig)로 내보냅니다."""
    ensure_extracted_dir(os.path.dirname(chunks_output_path))

    # 1. course_chunks.csv 내보내기
    with open(chunks_output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["chunk_id", "course_name", "chunk_index", "content_length", "content"]
        )

        chunk_id = 1
        for c in courses:
            for idx, chunk in enumerate(c.get("chunks", []), 1):
                writer.writerow([chunk_id, c["course_name"], idx, len(chunk), chunk])
                chunk_id += 1

    # 2. wheelchair_segments.csv 내보내기 (고정 10개 시드 데이터)
    wheelchair_seeds = [
        {
            "course_name": "1코스",
            "segment_name": "1코스 휠체어 구간 (종달리 옛 소금밭 ~ 성산갑문 입구)",
            "start_address": "제주시 구좌읍 종달리 814-5",
            "distance_km": 4.6,
            "difficulty_level": "중",
        },
        {
            "course_name": "10-1코스",
            "segment_name": "10-1코스 휠체어 구간 (가파도 전 구간)",
            "start_address": "가파도 상동포구",
            "distance_km": 4.2,
            "difficulty_level": "상",
        },
        {
            "course_name": "4코스",
            "segment_name": "4코스 휠체어 구간 (해비치호텔&리조트 ~ 가마리개 쉼터)",
            "start_address": "서귀포시 표선면 표선리 40-76",
            "distance_km": 4.8,
            "difficulty_level": "중",
        },
        {
            "course_name": "5코스",
            "segment_name": "5코스 휠체어 구간 (국립수산과학원 ~ 위미항)",
            "start_address": "서귀포시 남원읍 위미리 785-1",
            "distance_km": 2.7,
            "difficulty_level": "상",
        },
        {
            "course_name": "6코스",
            "segment_name": "6코스 휠체어 구간 (쇠소깍 ~ 보목포구)",
            "start_address": "서귀포시 하효동 999",
            "distance_km": 2.6,
            "difficulty_level": "중",
        },
        {
            "course_name": "8코스",
            "segment_name": "8코스 휠체어 구간 (논짓물 ~ 대평포구)",
            "start_address": "서귀포시 하예동 532-3",
            "distance_km": 3.6,
            "difficulty_level": "상",
        },
        {
            "course_name": "10코스",
            "segment_name": "10코스 휠체어 구간 (사계포구 ~ 송악산 주차장)",
            "start_address": "서귀포시 안덕면 사계리 2125",
            "distance_km": 2.9,
            "difficulty_level": "중",
        },
        {
            "course_name": "12코스",
            "segment_name": "12코스 휠체어 구간 (엉알길 입구 ~ 자구내포구 입구)",
            "start_address": "제주시 한경면 고산리 3674-2",
            "distance_km": 1.1,
            "difficulty_level": "중",
        },
        {
            "course_name": "14코스",
            "segment_name": "14코스 휠체어 구간 (일성콘도 ~ 금능해수욕장 입구)",
            "start_address": "제주시 한림읍 금능리 1621-6",
            "distance_km": 2.1,
            "difficulty_level": "중",
        },
        {
            "course_name": "17코스",
            "segment_name": "17코스 휠체어 구간 (도두봉 내려오는 길 ~ 용연다리)",
            "start_address": "제주시 도두2동 1611",
            "distance_km": 4.4,
            "difficulty_level": "중",
        },
    ]

    with open(wheelchair_output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "course_name",
                "segment_name",
                "start_address",
                "distance_km",
                "difficulty_level",
            ]
        )
        for seed in wheelchair_seeds:
            writer.writerow(
                [
                    seed["course_name"],
                    seed["segment_name"],
                    seed["start_address"],
                    seed["distance_km"],
                    seed["difficulty_level"],
                ]
            )

    return chunks_output_path, wheelchair_output_path
