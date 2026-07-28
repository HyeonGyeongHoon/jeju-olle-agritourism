"""DB/데이터 조회 서비스 레이어.

`src/agent/nodes.py` 에 노드 함수와 섞여 있던 순수 데이터 조회/보정 헬퍼들을 분리한
모듈입니다(2026-07-26 리팩토링 — 로직 변경 없는 순수 코드 이동).

이 모듈의 함수들은 LangGraph 상태(AgentState)를 전혀 모릅니다.
Supabase 클라이언트를 **인자로 받아서** 씁니다 —
`get_supabase_client()` 호출은 여전히 노드 쪽 책임입니다.
어느 노드가 어느 시점에 클라이언트를 만드는지가 그래프 흐름의
일부라서, 시그니처를 그대로 두는 것이 호출부와 테스트 목킹
패턴을 모두 유지하는 가장 안전한 선택이었습니다.
조회 실패 시에는 예외를 삼키고 None/빈 값으로 fail-soft 하거나
(대부분), 확신할 수 없으면 None 으로 fail-closed 합니다
(`_resolve_canonical_region_dong`,
`_resolve_stats_region_from_areas`) — 각 함수 독스트링의 판단
근거를 참고하세요.

주의(테스트 목킹): `nodes.py` 는 이 모듈의 이름들을
`from ... import` 로 자기 네임스페이스에 바인딩해 쓰므로, 노드
동작을 목킹할 때는 기존과 동일하게 `patch.object(nodes, "_xxx")`
가 유효합니다. 반대로 이 모듈 안에서 서로를 호출하는 경로
(예: `_search_culture_knowledge` -> `_search_local_culture_docs`)
를 가로채려면 `patch.object(db_service, "_xxx")` 로 패치해야
합니다.

파일 경로 상수(`_JEJU_DISTRICTS_CSV_PATH`,
`_LOCAL_CULTURE_KNOWLEDGE_DIR`)는 `__file__` 기준 3단계
상위(=리포지토리 루트)로 계산합니다. `src/agent/nodes.py` 와
`src/services/db_service.py` 는 루트로부터 같은 깊이라 이동
후에도 동일한 경로를 가리킵니다.
"""

import csv
import json
import os
import re
from typing import Any, Dict, List

from src.agent.router import _SPECIFIC_COURSE_PATTERN
from src.ingestion.database_loader import get_solar_embedding

# courses.administrative_areas 는 법정리/법정동(마을 단위, 예: "김녕리") 을, visitor_analytics.
# region_dong 은 행정동/읍/면(그 마을들을 묶는 상위 행정구역, 예: "구좌읍") 을 씁니다. 두 단위는
# 서로 다른 행정 위계라 이름이 대부분 일치하지 않습니다(43개 중 우연히 이름이 같은 7개 제외).
# 읍/면 지역의 법정리 -> 행정 읍/면 매핑은 data/jeju_districts.csv(city_name/district_name/
# legal_name)에서 그대로 읽어옵니다 — 이 CSV의 district_name 컬럼이 곧 visitor_analytics.
# region_dong 과 같은 단위입니다. 같은 법정리 이름이 서로 다른 읍/면에 동시에 존재하는 경우
# (예: "서광리"가 안덕면과 우도면에 각각 있음, "세화리"가 구좌읍과 표선면에 각각 있음)는 원문
# 만으로 구분이 불가능하므로 둘 다 후보에 남겨둡니다.
_JEJU_DISTRICTS_CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "jeju_districts.csv"
)


def _load_legal_to_admin_mapping_from_csv() -> Dict[str, List[str]]:
    """data/jeju_districts.csv 로부터 법정동/리 -> 행정동/읍/면 매핑을 딕셔너리로 읽어옵니다.
    하나의 법정동이 여러 행정동에 걸쳐있거나 예외 표기가 있는 경우 모두 리스트 형태로 통합합니다."""
    mapping: Dict[str, List[str]] = {}
    try:
        with open(_JEJU_DISTRICTS_CSV_PATH, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                legal_name = row.get("legal_name", "").strip()
                admin_name = row.get("district_name", "").strip()
                if legal_name and admin_name:
                    dongs = mapping.setdefault(legal_name, [])
                    if admin_name not in dongs:
                        dongs.append(admin_name)
    except Exception as e:
        print(f"[!] data/jeju_districts.csv 로드 실패: {e}")
    return mapping


_LEGAL_DONG_TO_ADMIN_DONG = _load_legal_to_admin_mapping_from_csv()

# retriever 가 preferred_location(행정동/읍/면 단위 — 직접 지정이든 market_location_resolver 가
# 채운 것이든)으로 courses.administrative_areas(법정리/법정동 단위)를 직접 필터링할 수 있도록,
# 위 매핑을 뒤집어 행정동 -> 법정리 목록을 만듭니다.
_ADMIN_DONG_TO_LEGAL_DONGS: Dict[str, List[str]] = {}
for _legal_name, _admin_dongs in _LEGAL_DONG_TO_ADMIN_DONG.items():
    for _admin_dong in _admin_dongs:
        _ADMIN_DONG_TO_LEGAL_DONGS.setdefault(_admin_dong, []).append(_legal_name)


def _get_olle_relevant_admin_dongs(client: Any) -> set:
    """courses.administrative_areas 에 실제로 등장하는 법정리/법정동을 _LEGAL_DONG_TO_ADMIN_DONG
    으로 행정동/읍/면 단위로 변환해, visitor_analytics.region_dong 과 비교 가능한 집합으로
    반환합니다. 매핑에 없는 이름(신규 코스 추가로 아직 반영되지 않은 법정리)은 원본 이름 그대로도
    후보에 포함시켜, 매핑 누락으로 지역이 통째로 제외되지 않도록 관대하게 처리합니다.
    """
    try:
        res = client.table("courses").select("administrative_areas").execute()
    except Exception as e:
        print(f"[!] courses.administrative_areas 조회 실패, 지역 필터링 없이 진행합니다: {e}")
        return set()

    relevant = set()
    for row in res.data or []:
        raw = row.get("administrative_areas") or ""
        for area in raw.split(","):
            area = area.strip()
            if area:
                relevant.update(_LEGAL_DONG_TO_ADMIN_DONG.get(area, [area]))
    return relevant


def _get_latest_available_year_month(client: Any) -> str | None:
    """visitor_analytics 에 실제로 존재하는 가장 최근 year_month 를 조회합니다. 사용자가 연/월을
    전혀 지정하지 않았을 때, "오늘 날짜"보다 안전한 기본값으로 씁니다(적재된 데이터가 항상 이번
    달까지 커버한다는 보장이 없어, 오늘 날짜를 기본값으로 쓰면 매칭 0건으로 조용히 no-op 되는
    문제가 있었음)."""
    try:
        res = (
            client.table("visitor_analytics")
            .select("year_month")
            .order("year_month", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        print(f"[!] visitor_analytics 최신 year_month 조회 실패: {e}")
        return None
    return res.data[0]["year_month"] if res.data else None


# 읍/면/동 접미사. "한림"처럼 접미사를 생략해 말한 지역명도 "한림읍" 토큰과 매칭시키기 위해
# 양쪽에서 이 접미사를 떼고 비교합니다. data/jeju_districts.csv 의 행정동/읍/면 이름 43개는
# 접미사를 떼어도 서로 충돌하지 않음을 확인했습니다(2026-07-25) — 즉 이 관대함이 세화리류
# 동명이인 충돌을 되살리지는 않습니다(예: "구좌읍"->"구좌" vs "표선면"->"표선").
_ADMIN_TIER_SUFFIXES = ("읍", "면", "동")


def _normalize_admin_tier_name(name: str) -> str:
    """읍/면/동 접미사를 제거한 비교용 표기를 반환합니다("한림읍" -> "한림")."""
    for suffix in _ADMIN_TIER_SUFFIXES:
        if len(name) > 1 and name.endswith(suffix):
            return name[: -len(suffix)]
    return name


_LOCAL_CULTURE_KNOWLEDGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "culture_knowledge"
)
# 작물별 문서(crop_docs.json)와 비작물 일반 농업문화 문서(culture_docs.json, crop_name=null),
# 그리고 마늘/당근/감귤/양배추/브로콜리/양파/월동무 7종의 실 자료(crop_seven_docs.json, target_crop/
# region_tag/active_months/season_stage 포함한 확장 스키마)를 별도 파일로 분리 관리합니다. Supabase
# culture_crop_knowledge 테이블/RPC는 세 종류를 구분하지 않고(crop_name/target_crop이 nullable)
# 그대로 하나의 테이블에 적재합니다.
_LOCAL_CROP_DOCS_PATH = os.path.join(_LOCAL_CULTURE_KNOWLEDGE_DIR, "crop_docs.json")
_LOCAL_GENERAL_CULTURE_DOCS_PATH = os.path.join(_LOCAL_CULTURE_KNOWLEDGE_DIR, "culture_docs.json")
_LOCAL_CROP_SEVEN_DOCS_PATH = os.path.join(_LOCAL_CULTURE_KNOWLEDGE_DIR, "crop_seven_docs.json")


_TITLE_TRAILING_PARTICLES = ("와", "과", "은", "는", "이", "가", "을", "를", "의", "에서", "에", "으로", "로")


def _title_keywords(title: str) -> List[str]:
    """제목에서 매칭용 키워드 후보를 뽑습니다. 조사가 붙은 토큰("화산회토와")은 어간("화산회토")만
    남기고, 범용 단어("문화", "개론")는 제외해 실제 주제어만 남깁니다."""
    tokens = re.split(r"[\s'\"()·\-]+", title)
    keywords = []
    for token in tokens:
        if not token or token in ("문화", "개론"):
            continue
        for particle in sorted(_TITLE_TRAILING_PARTICLES, key=len, reverse=True):
            if token.endswith(particle) and len(token) - len(particle) >= 2:
                token = token[: -len(particle)]
                break
        if len(token) >= 2:
            keywords.append(token)
    return keywords


def _load_local_culture_docs() -> List[Dict[str, Any]]:
    """작물 문서(crop_docs.json), 비작물 일반 농업문화 문서(culture_docs.json), 7종 실 자료
    (crop_seven_docs.json)를 합쳐 반환합니다."""
    docs: List[Dict[str, Any]] = []
    for path in (_LOCAL_CROP_DOCS_PATH, _LOCAL_GENERAL_CULTURE_DOCS_PATH, _LOCAL_CROP_SEVEN_DOCS_PATH):
        try:
            with open(path, "r", encoding="utf-8") as f:
                docs.extend(json.load(f))
        except Exception as e:
            print(f"[!] 로컬 문화 지식 문서 로드 실패({path}): {e}")
    return docs


_CITATION_MARKER_RE = re.compile(r"\[cite:[^\]]*\]")


def _search_local_culture_docs(
    key_item_or_crop: str | None,
    query_text: str,
    top_k: int = 3,
    allow_general_fallback: bool = True,
) -> List[Dict[str, Any]]:
    """culture_crop_knowledge 벡터 DB 가 아직 적재되지 않았거나 조회에 실패했을 때, 로컬 문서
    (data/culture_knowledge/crop_docs.json + culture_docs.json + crop_seven_docs.json)에서 키워드
    매칭으로 대체 검색하는 폴백입니다. DB 적재가 완료되면 retrieve_rag_node 의 pgvector 검색이
    우선 시도되고, 이 함수는 자동으로 호출되지 않습니다.
    작물 문서는 crop_name(또는 crop_seven_docs.json 의 target_crop) 일치로, 비작물 일반 문화 문서
    (밭담/곶자왈/해녀 등, crop_name=None)는 제목 키워드가 질의에 등장하는지로 점수를 매겨, 특정
    작물 언급이 없는 질의에서도 관련 있는 일반 문화 문서가 매번 같은 순서로만 채워지지 않고 실제로
    매칭되도록 합니다.
    allow_general_fallback=False 이면(호출부가 key_item_or_crop 이 실제 작물명임을 이미 확인한
    경우), 매칭된 작물 문서가 부족해도 무관한 일반 문화 문서로 채우지 않습니다 — 특정 작물을
    물어봤는데 근거 없는 일반 배경지식을 마치 답인 것처럼 섞어 보여주지 않기 위함입니다.
    """
    docs = _load_local_culture_docs()
    if not docs:
        return []

    search_text = f"{key_item_or_crop or ''} {query_text or ''}"

    scored = []
    for i, doc in enumerate(docs):
        crop_name = doc.get("crop_name") or doc.get("target_crop")
        entry = {
            "id": i,
            "crop_name": crop_name,
            "title": doc["title"],
            "content": _CITATION_MARKER_RE.sub("", doc["content"]),
            "similarity": 1.0,
            "knowledge_id": doc.get("knowledge_id"),
            "category": doc.get("category"),
            "target_crop": doc.get("target_crop") or crop_name,
            "region_tag": doc.get("region_tag"),
            "active_months": doc.get("active_months"),
            "season_stage": doc.get("season_stage"),
        }
        score = 0
        if crop_name:
            if key_item_or_crop and (key_item_or_crop in crop_name or crop_name in key_item_or_crop):
                score += 3
            if crop_name in (query_text or ""):
                score += 2
        else:
            score += sum(1 for kw in _title_keywords(doc["title"]) if kw in search_text)
        scored.append((score, i, entry))

    matched = [entry for score, _i, entry in scored if score > 0]
    results = matched[:top_k]
    if allow_general_fallback and len(results) < top_k:
        # 매칭된 것이 부족하면, 매칭되지 않은 일반 문화 문서로 원본 순서대로 채웁니다.
        remaining_general = [entry for score, _i, entry in scored if score == 0 and entry["crop_name"] is None]
        results += remaining_general[: top_k - len(results)]
    return results


def _query_visitor_analytics_row(
    client: Any, region_dong: str, target_month: int | None
) -> Dict[str, Any] | None:
    """visitor_analytics 에서 region_dong 표기 그대로(완전 일치) 한 행을 조회합니다.
    `visitor_analytics` 테이블이 아직 적재되지 않았거나(Gate B 승인 대기 중) 조회에 실패해도
    그래프 전체가 중단되지 않도록 예외를 삼키고 None 을 반환합니다.
    """
    try:
        query = client.table("visitor_analytics").select("*").eq("region_dong", region_dong)
        if target_month:
            query = query.like("year_month", f"%-{target_month:02d}")
        res = query.order("year_month", desc=True).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[!] Market Insight(visitor_analytics) 조회 실패, 생략합니다: {e}")
        return None


def _resolve_canonical_region_dong(client: Any, region_dong: str) -> str | None:
    """visitor_analytics.region_dong 에 실제로 존재하는 표기 중, region_dong 과 읍/면/동 접미사
    유무만 다른 값을 찾아 그 **정식 표기**를 반환합니다("한림" -> "한림읍").

    `_filter_course_ids_by_location`(코스 매칭)은 이미 `_normalize_admin_tier_name` 으로 접미사를
    떼고 비교해 "한림" == "한림읍" 을 허용하는데, 통계 조회만 `.eq()` 완전 일치로 남아 있어서
    같은 질의에서 코스는 정상 매칭되고 Market Insight 만 0건이 되는 경계면 불일치가 있었습니다
    (2026-07-25 라이브 QA: "한림 기획서 만들어줘" → intent_parser 가 preferred_location='한림'
    으로 추출 → 한림읍 2026-05 방문객 559,007명 행이 실존하는데도 기획서에는 "관련 빅데이터
    지표가 확인되지 않아 정성적으로 제안합니다"로 출력).

    region_dong 컬럼 한 개만 전량 조회합니다 — visitor_analytics 는 월×행정동 단위라 규모가
    작아(2026-07-25 기준 215행 / 고유 지역 43개) 전량 스캔이 과하지 않고, 정식 표기를 알아낸 뒤
    기존 `.eq()` 조회 경로를 그대로 재사용할 수 있어 월 필터/정렬 로직을 중복 구현하지 않아도
    됩니다.

    정규화 결과가 같은 지역이 둘 이상이면 어느 지역의 통계인지 특정할 수 없으므로 **None 으로
    fail-closed** 합니다 — 기획서에 그대로 인용되는 수치라서, 틀릴 수도 있는 지역의 통계를
    붙이는 것보다 통계 없이 진행하는 게 안전합니다(`_resolve_stats_region_from_areas` 와 동일한
    판단). 현재 데이터의 행정동/읍/면 43개는 접미사를 떼도 서로 충돌하지 않음을 확인했습니다.
    """
    try:
        res = client.table("visitor_analytics").select("region_dong").execute()
        available = {
            str(row["region_dong"]).strip()
            for row in (res.data or [])
            if isinstance(row, dict) and row.get("region_dong")
        }
    except Exception as e:
        print(f"[!] visitor_analytics.region_dong 목록 조회 실패, 지역명 정규화를 생략합니다: {e}")
        return None

    normalized = _normalize_admin_tier_name(region_dong.strip())
    candidates = sorted(
        value for value in available if _normalize_admin_tier_name(value) == normalized
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        print(
            f"[!] '{region_dong}' 이(가) 여러 행정동({', '.join(candidates)})에 대응해 통계 지역을 "
            f"특정할 수 없어 Market Insight 를 생략합니다."
        )
    return None


def _fetch_market_insight(client: Any, region_dong: str | None, target_month: int | None) -> Dict[str, Any] | None:
    """제주관광공사 이동통신 빅데이터(visitor_analytics) 에서 해당 행정동·월의 방문객 통계를
    조회합니다. 같은 월이라도 연도가 여러 건 있을 수 있어 가장 최근 연도 값을 사용합니다.
    target_month 가 없으면("최근 방문객 수는?" 처럼 질의가 특정 월을 지정하지 않은 경우) 월로
    필터링하지 않고 해당 지역의 가장 최근 데이터를 그대로 반환합니다 — 예전엔 이 경우 바로 None을
    반환했는데, 호출부(quick_responder_node)가 target_month 를 "오늘 날짜의 달"로 기본값
    처리하고 있어서, DB에 적재된 데이터가 이번 달까지 커버하지 않으면(2026-07-25 라이브 QA: DB는
    2026-05까지만 적재, 오늘은 2026-07) 실제로는 최근 데이터가 있는데도 "통계를 찾지 못했다"고
    답하고, 그 실패가 quality_checker 재시도 루프까지 불필요하게 불러일으켰습니다.
    region_dong 표기가 DB 값과 정확히 같으면(대부분의 경우) 예전과 똑같이 단 한 번만 조회하고,
    0건일 때에만 `_resolve_canonical_region_dong` 으로 접미사 표기 차이("한림" vs "한림읍")를
    해소해 정식 표기로 한 번 더 조회합니다(추가 조회는 실패 경로에서만 발생).
    """
    if not region_dong:
        return None

    row = _query_visitor_analytics_row(client, region_dong, target_month)
    if row is not None:
        return row

    canonical = _resolve_canonical_region_dong(client, region_dong)
    if not canonical or canonical == region_dong:
        return None
    print(
        f"[i] Market Insight 지역명 '{region_dong}' 을(를) "
        f"DB 정식 표기 '{canonical}' 로 해석했습니다."
    )
    return _query_visitor_analytics_row(client, canonical, target_month)


def _crop_label_matches(
    crop_label: str | None, key_item_or_crop: str | None, key_is_known_crop: bool
) -> bool:
    """문서의 crop_label(target_crop 또는 crop_name)이 요청한 key_item_or_crop과 실제로
    관련 있는지 판정합니다.
    - key_item_or_crop이 없으면(작물을 특정하지 않은 질의) 필터링할 이유가 없으므로 항상
      관련 있다고 간주합니다.
    - crop_label이 있으면(문서가 특정 작물을 표방함) key_item_or_crop과 겹치는지로 판정합니다.
    - crop_label이 없는(특정 작물을 표방하지 않는 일반 문화 문서) 경우: key_item_or_crop이
      "밭담"/"숲길" 같은 비작물 테마어라면 바로 그 문서가 정답일 수 있으므로 관련 있다고
      간주하지만, key_item_or_crop이 실제 작물명(key_is_known_crop=True)이라면 관련 없다고
      간주합니다 — 특정 작물을 물어봤는데 근거 없는 일반 배경지식을 답으로 대체하지 않기 위함
      (사용자가 명시적으로 요청: 관련 정보를 못 찾았으면 지어내지 말고 솔직히 답할 것).
    """
    if not key_item_or_crop:
        return True
    if crop_label:
        return key_item_or_crop in crop_label or crop_label in key_item_or_crop
    return not key_is_known_crop


# _fetch_course_meta_by_name 이 조회하는 컬럼. crops/administrative_areas 는 검색 조건 보완용,
# 나머지 실측 수치는 quick_responder_node 가 "이 코스 괜찮아?/초보자한테 적합해?" 같은 의견·
# 적합성 질의에 DB 근거로 답하기 위해 필요합니다(2026-07-25 라이브 QA: 이 수치들이 없던 탓에
# LLM 이 "7코스는 비교적 평탄해 초보자도 좋다"처럼 근거 없는 추측을 답변에 넣었음. 실제 7코스는
# 17.6km / 6시간 / 난이도 중).
_COURSE_META_COLUMNS = (
    "course_name, crops, administrative_areas, total_distance_km, estimated_time_hours, "
    "estimated_time_text, difficulty, start_point, end_point"
)


def _fetch_course_meta_by_name(client: Any, course_name: str) -> Dict[str, Any] | None:
    """target_course(라우터가 질의에서 인식한 특정 코스명)로 해당 코스의 메타데이터를
    조회합니다(작물/경유 행정구역 + 거리·소요시간·난이도·시작/종점 실측치).
    quick_responder_node가 코스명이 언급된 질의에서 그 코스의 작물/지역을 검색 조건으로
    자동 보완하고, 의견·적합성 질의에 실측 수치를 근거로 답하는 데 사용합니다. 조회 실패 시
    None을 반환해 상위 로직이 그대로 key_item_or_crop/preferred_location 없이 진행하도록
    합니다(다른 DB 조회 헬퍼들과 동일한 fail-soft 방식).
    """
    try:
        res = (
            client.table("courses")
            .select(_COURSE_META_COLUMNS)
            .eq("course_name", course_name)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[!] 코스 메타데이터(target_course={course_name}) 조회 실패, 생략합니다: {e}")
        return None


def _looks_like_course_name(value: str | None) -> bool:
    """값이 지역명이 아니라 코스명 표기("1코스"/"10-1코스"/"3-A코스")인지 판별합니다.
    intent_parser 의 preferred_location 은 "질문에 지역/코스명이 직접 언급된 경우" 채우도록
    지시되어 있어, 코스명이 지역 필드로 들어오는 경우가 실제로 발생합니다(2026-07-25 라이브
    QA: "1코스 괜찮을지 추천해줄래?" → preferred_location='1코스'). 코스명을 지역으로 쓰면
    통계 조회가 실패하거나(도구가 "조회 가능한 지역: ..." 목록을 반환해 LLM 이 그대로 다른
    지역을 추천하는 정책 위반 경로가 됨) 지역 필터가 "10-1코스" 같은 다른 코스에 부분
    일치하는 위험이 있습니다. 라우터의 코스명 표기 패턴을 재사용해(중복 정의 방지) 값 전체가
    코스명인지 검사합니다.
    """
    if not value:
        return False
    return _SPECIFIC_COURSE_PATTERN.fullmatch(value.strip()) is not None


def _resolve_stats_region_from_areas(client: Any, administrative_areas: str | None) -> str | None:
    """코스의 administrative_areas(법정리/법정동 단위, 쉼표 구분)에서 방문객 통계 조회에
    실제로 쓸 수 있는 행정동/읍/면 하나를 골라 반환합니다. courses 와 visitor_analytics 는
    행정 단위 계층이 달라서(법정리 vs 행정동) 앞의 값을 그대로 쓰면 통계 조회가 실패하고,
    그 실패 메시지가 "조회 가능한 지역" 목록을 노출해 다른 지역 추천으로 이어집니다.
    후보를 하나도 검증할 수 없으면 None 을 반환합니다 — 확신 없는 지역을 넘기는 것보다
    지역 없이 진행하는 게 안전하기 때문입니다(fail-closed).
    """
    if not administrative_areas:
        return None
    try:
        olle_dongs = _get_olle_relevant_admin_dongs(client)
    except Exception as e:
        print(f"[!] 올레 경유 행정동 목록 조회 실패, 통계 지역 없이 진행합니다: {e}")
        return None
    if not olle_dongs:
        return None
    for area in administrative_areas.split(","):
        area = area.strip()
        if not area:
            continue
        for candidate in _LEGAL_DONG_TO_ADMIN_DONG.get(area, [area]):
            if candidate in olle_dongs:
                return candidate
    return None


def _search_culture_knowledge(
    client: Any, key_item_or_crop: str | None, fallback_query: str
) -> List[Dict[str, Any]]:
    """제주 밭담문화·작물 생육 지식 DB(culture_crop_knowledge)를 검색합니다 (외부 API 대신 검증된
    문서 기반 근거 확보). key_item_or_crop 이 있으면 그 값으로, 없으면 fallback_query 로 검색어를
    정합니다. culture_crop_knowledge 테이블이 아직 적재되지 않았거나 RPC 조회에 실패/빈 결과이면
    로컬 JSON 문서 검색(_search_local_culture_docs)으로 자동 폴백합니다. retrieve_rag_node와
    quick_responder_node가 공통으로 사용합니다.
    """
    culture_query = key_item_or_crop or fallback_query
    culture_chunks_data = []
    try:
        culture_vector = get_solar_embedding(culture_query)
        culture_rpc_res = client.rpc("match_culture_chunks", {
            "query_embedding": culture_vector,
            "match_threshold": 0.1,
            "match_count": 3
        }).execute()
        for item in culture_rpc_res.data:
            culture_chunks_data.append({
                "id": item["id"],
                "crop_name": item.get("crop_name"),
                "title": item["title"],
                "content": item["content"],
                "similarity": item["similarity"],
                "knowledge_id": item.get("knowledge_id"),
                "category": item.get("category"),
                "target_crop": item.get("target_crop") or item.get("crop_name"),
                "region_tag": item.get("region_tag"),
                "active_months": item.get("active_months"),
                "season_stage": item.get("season_stage"),
            })
    except Exception as e:
        print(f"[!] 밭담문화·작물 지식 DB 검색 실패, 로컬 문서로 폴백합니다: {e}")

    # match_culture_chunks RPC 는 작물 하드 필터 없이 임계치(0.1)만 낮게 건 순수 유사도 검색이라,
    # 요청한 작물과 다른 작물을 표방하는 문서가 섞여 들어올 수 있습니다(실사용 중 발견: "당근"
    # 질문에 "마늘" 문서가 섞여 나오는 문제 — 사용자 경험 저하). key_item_or_crop이 실제 작물명일
    # 때는(known_crop_tags 로 확인) 다른 작물 문서는 물론, 특정 작물을 표방하지 않는 일반
    # 배경지식 문서로도 대체하지 않습니다 — 관련 정보를 못 찾았으면 있는 그대로 "못 찾았다"고
    # 답해야지, 무관한 일반 지식을 답인 것처럼 꾸며 보여주면 안 되기 때문입니다. key_item_or_crop
    # 이 "밭담"/"숲길" 같은 비작물 테마어라면 일반 문서가 바로 정답일 수 있으므로 그대로 둡니다.
    key_is_known_crop = bool(key_item_or_crop) and key_item_or_crop in _get_known_crop_tags(client)
    if key_item_or_crop:
        culture_chunks_data = [
            cc for cc in culture_chunks_data
            if _crop_label_matches(
                cc.get("target_crop") or cc.get("crop_name"), key_item_or_crop, key_is_known_crop
            )
        ]

    if not culture_chunks_data:
        # 이 필터링으로 전부 걸러졌든 RPC 자체가 실패/빈 결과였든, 로컬 폴백으로 넘어갑니다.
        # key_is_known_crop 이면 로컬 폴백도 무관한 일반 문서로 채우지 않도록 지시합니다.
        culture_chunks_data = _search_local_culture_docs(
            key_item_or_crop, culture_query, allow_general_fallback=not key_is_known_crop
        )

    return culture_chunks_data


def _get_known_crop_tags(client: Any) -> set:
    """courses.crops(콤마 구분)에 실제로 등장하는 모든 작물 태그 집합을 반환합니다.
    key_item_or_crop 이 이 집합에 없으면 "밭담"/"숲길" 같은 비작물 테마어로 간주해
    _filter_course_ids_by_crop 이 하드 필터링을 건너뜁니다."""
    try:
        res = client.table("courses").select("crops").execute()
    except Exception as e:
        print(f"[!] courses.crops 조회 실패: {e}")
        return set()

    tags = set()
    for row in res.data or []:
        raw = row.get("crops") or ""
        for tag in raw.split(","):
            tag = tag.strip()
            if tag:
                tags.add(tag)
    return tags


# courses.difficulty 의 유효 값 전체를 쉬운 순서(하 < 중 < 상)로 나열한 것. 예전에는
# max_difficulty(상한) 를 인덱스로 잘라 허용 집합을 만드는 데 썼지만, 난이도 조건이
# allowed_difficulties(허용 목록) 로 바뀐 뒤로는 (1) LLM 이 넘긴 값이 실제 DB 값인지
# 검증하고 (2) 사유 문구에 쓸 때 표기 순서를 일정하게 맞추는 용도로 씁니다.
_DIFFICULTY_ORDER = ["하", "중", "상"]


def _normalize_allowed_difficulties(hard: dict) -> List[str]:
    """hard_constraints 의 allowed_difficulties 를 "DB 에 실제로 존재하는 난이도 값만, 쉬운
    순서대로, 중복 없이" 정리해 반환합니다. 지정이 없거나(None) 유효 값이 하나도 없으면 빈
    리스트를 반환하고, 그 경우 호출부는 난이도 조건 자체가 없는 것으로 취급합니다 — LLM 이
    엉뚱한 문자열("아주 어려움" 등)을 넣었다고 해서 그것을 그대로 in_() 에 넘겨 전 코스를
    0건으로 만들어 버리면, 사용자가 지정하지도 않은 조건 때문에 반려되기 때문입니다
    (기존 max_difficulty 시절의 `if max_difficulty in _DIFFICULTY_ORDER` 가드와 동일한 취지).

    표기 순서를 _DIFFICULTY_ORDER 기준으로 고정하는 이유는 반려 사유 문구("난이도 '하' 또는
    '중'인 …")가 LLM 이 나열한 순서에 따라 들쭉날쭉해지지 않게 하기 위함입니다.
    """
    raw = hard.get("allowed_difficulties")
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [level for level in _DIFFICULTY_ORDER if level in raw]


def _execute_rdb_filtering(client: Any, hard: dict) -> List[int]:
    """courses 테이블을 메타데이터(hard_constraints) 기반으로 SQL 필터링하여 일치하는 코스 ID
    리스트를 반환합니다. target_course 는 여기서 하드 필터링하지 않습니다 — course_name 과
    완전 일치하지 않으면(예: 섬 이름 "가파도"가 실제 코스명 "10-1코스"와 문자열이 다른 경우)
    후보가 0개가 되어 그 뒤 검색 전체가 죽는 문제가 있었습니다. target_course 는
    _filter_course_ids_by_target_course 로 지역/작물 조건과 동일하게 fail-soft 처리합니다.

    max_time_hours/max_distance_km/allowed_difficulties(2026-07-27 추가)는 wheelchair_required와
    동일하게 여기서 하드 필터링합니다 — 사용자가 명시한 시간/거리 상한과 허용 난이도 목록의
    교집합에 해당하는 코스가 courses 에 하나도 없으면 이 함수가 빈 리스트를 반환하고,
    retrieve_rag_node 가 그 즉시 무조건 반려(is_exit_early)합니다. 난이도가 상한
    (max_difficulty) 이 아니라 허용 목록인 이유는, "2시간 이내인데 난이도 상" 처럼 시간/거리
    조건과 난이도 조건이 서로 모순되는 요청을 상한 해석으로는(상한 "상" = 전부 허용) 걸러낼 수
    없었기 때문입니다.
    """
    query = client.table("courses").select("id")

    if hard.get("wheelchair_required"):
        query = query.eq("has_wheelchair_segment", "있음")

    max_time_hours = hard.get("max_time_hours")
    if max_time_hours is not None:
        query = query.lte("estimated_time_hours", max_time_hours)

    max_distance_km = hard.get("max_distance_km")
    if max_distance_km is not None:
        query = query.lte("total_distance_km", max_distance_km)

    allowed_difficulties = _normalize_allowed_difficulties(hard)
    if allowed_difficulties:
        query = query.in_("difficulty", allowed_difficulties)

    try:
        res = query.execute()
        return [row["id"] for row in res.data] if res.data else []
    except Exception as e:
        print(f"[!] RDB 필터링 실행 실패: {e}")
        return []


def _fetch_safety_etiquette_guide(client: Any) -> List[Dict[str, Any]]:
    """DB의 safety_etiquette_guide 테이블에서 안전 수칙, 에티켓, 추천 장비, 탐방 팁 등의 전체 가이드 정보를 조회합니다.
    데이터가 존재하지 않거나 에러 발생 시 빈 리스트를 반환합니다.
    """
    try:
        res = client.table("safety_etiquette_guide").select("category,content").execute()
        return res.data or []
    except Exception as e:
        print(f"[!] safety_etiquette_guide 조회 실패: {e}")
        return []
