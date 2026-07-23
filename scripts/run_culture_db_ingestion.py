"""
scripts/run_culture_db_ingestion.py
=====================================
data/culture_knowledge/crop_docs.json(작물별 생육/문화 지식) 과 culture_docs.json(밭담·곶자왈·
해녀 등 비작물 농업문화 지식) 에 작성된 문서를 Solar Embedding(4096차원)으로 임베딩하여
Supabase `culture_crop_knowledge` 테이블에 적재합니다. 두 파일은 로컬 관리 편의상 분리되어
있을 뿐, DB 테이블/RPC는 구분 없이 하나로 적재합니다(crop_name 컬럼이 nullable).

주의 (Gate B): 이 스크립트는 임베딩 API 호출 및 DB 적재를 수행하는 비가역적 작업입니다.
사용자의 사전 승인 없이 자동 실행하지 마세요. 실행 전 supabase/schema.sql 의
`culture_crop_knowledge` 테이블 및 `match_culture_chunks` 함수가 이미 생성되어 있어야 합니다.
"""

import json
import os
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
]


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
        crop_name = doc.get("crop_name")
        title = doc["title"]
        content = doc["content"]

        # 이미 동일 제목의 문서가 적재되어 있으면 건너뜀 (재실행 시 중복 방지)
        existing = (
            client.table("culture_crop_knowledge")
            .select("id")
            .eq("title", title)
            .execute()
        )
        if existing.data:
            print(f"[-] 이미 적재됨, 건너뜀: {title}")
            continue

        print(f"[*] 임베딩 및 적재 중: {title}")
        embedding_vector = get_solar_embedding(content)
        client.table("culture_crop_knowledge").insert({
            "crop_name": crop_name,
            "title": title,
            "content": content,
            "embedding": embedding_vector,
        }).execute()

    print("\n[OK] 제주 밭담문화·작물 지식 DB 적재가 완료되었습니다!")


if __name__ == "__main__":
    run()
