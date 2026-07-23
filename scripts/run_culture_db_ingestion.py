"""
scripts/run_culture_db_ingestion.py
=====================================
data/culture_knowledge/crop_docs.json(작물별 생육/문화 지식), culture_docs.json(밭담·곶자왈·
해녀 등 비작물 농업문화 지식), crop_seven_docs.json(마늘/당근/감귤/양배추/브로콜리/양파/월동무
7종의 실 자료 - knowledge_id/category/target_crop/region_tag/active_months/season_stage 포함한
확장 스키마)에 작성된 문서를 Solar Embedding(4096차원)으로 임베딩하여 Supabase
`culture_crop_knowledge` 테이블에 적재합니다. 세 파일은 로컬 관리 편의상 분리되어 있을 뿐, DB
테이블/RPC는 구분 없이 하나로 적재합니다(crop_name/target_crop 등은 nullable).

주의 (Gate B): 이 스크립트는 임베딩 API 호출 및 DB 적재를 수행하는 비가역적 작업입니다.
사용자의 사전 승인 없이 자동 실행하지 마세요. 실행 전 supabase/schema.sql 의
`culture_crop_knowledge` 테이블(8-1 섹션의 ALTER 포함) 및 `match_culture_chunks` 함수가 이미
Supabase SQL 에디터에서 생성되어 있어야 합니다.
"""

import json
import os
import re
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingestion.database_loader import get_supabase_client, get_solar_embedding

_CULTURE_KNOWLEDGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "culture_knowledge"
)
DOCS_PATHS = [
    os.path.join(_CULTURE_KNOWLEDGE_DIR, "crop_docs.json"),
    os.path.join(_CULTURE_KNOWLEDGE_DIR, "culture_docs.json"),
    os.path.join(_CULTURE_KNOWLEDGE_DIR, "crop_seven_docs.json"),
]

_CITATION_MARKER_RE = re.compile(r"\[cite:[^\]]*\]")


def _strip_citation_markers(content: str) -> str:
    """PDF 추출본에 남아있는 '[cite: 3, 4]' 형태의 인용 각주 아티팩트를 제거합니다."""
    return _CITATION_MARKER_RE.sub("", content)


def run():
    docs = []
    for path in DOCS_PATHS:
        with open(path, "r", encoding="utf-8") as f:
            file_docs = json.load(f)
        print(f"[*] {os.path.basename(path)}: {len(file_docs)}건 로드")
        docs.extend(file_docs)

    print(f"[*] 적재 대상 문서 총 {len(docs)}건 로드 완료")

    client = get_supabase_client()

    for doc in docs:
        title = doc["title"]
        content = _strip_citation_markers(doc["content"])
        target_crop = doc.get("target_crop") or doc.get("crop_name")

        row = {
            "crop_name": target_crop,
            "title": title,
            "content": content,
            "knowledge_id": doc.get("knowledge_id"),
            "category": doc.get("category"),
            "target_crop": target_crop,
            "region_tag": doc.get("region_tag"),
            "active_months": doc.get("active_months"),
            "season_stage": doc.get("season_stage"),
        }

        # title 기준으로 기존 적재 여부를 확인해, 있으면 내용을 갱신(update)하고 없으면 새로 적재(insert)
        existing = (
            client.table("culture_crop_knowledge")
            .select("id")
            .eq("title", title)
            .execute()
        )

        print(f"[*] 임베딩 중: {title}")
        embedding_vector = get_solar_embedding(content)
        row["embedding"] = embedding_vector

        if existing.data:
            existing_id = existing.data[0]["id"]
            print(f"[*] 기존 문서 갱신: {title}")
            client.table("culture_crop_knowledge").update(row).eq("id", existing_id).execute()
        else:
            print(f"[*] 신규 적재: {title}")
            client.table("culture_crop_knowledge").insert(row).execute()

    print("\n[OK] 제주 밭담문화·작물 지식 DB 적재가 완료되었습니다!")


if __name__ == "__main__":
    run()
