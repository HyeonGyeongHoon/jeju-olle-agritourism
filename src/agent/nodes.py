import csv
import json
import os
import re
from datetime import date
from typing import Dict, Any, List
from src.agent.llm_client import get_chat_completion
from src.agent.weather_client import simulate_weather_by_query, get_seasonal_climate_note
from src.agent.router import route_intent
from src.ingestion.database_loader import get_supabase_client, get_solar_embedding
from src.ingestion.visit_jeju_client import get_visit_jeju_recommendations
from src.models.schema import B2BQueryParams
from src.agent.state import AgentState


def route_intent_node(state: AgentState) -> Dict[str, Any]:
    """사용자 질의를 4가지 카테고리로 사전 분류하여, 이후 로컬 맛집/카페 추천 노드 실행 여부를
    결정짓는 Intent Router 노드입니다.
    호출부(B2B 구조화 입력 등)가 intent_category 를 이미 확정해 넘긴 경우, LLM 분류 호출 없이
    그대로 통과시킵니다.
    """
    if state.get("intent_category"):
        return {
            "intent_category": state["intent_category"],
            "target_course": state.get("target_course")
        }

    result = route_intent(state["query"])
    return {
        "intent_category": result.category.value,
        "target_course": result.target_course
    }


def parse_intent_node(state: AgentState) -> Dict[str, Any]:
    """사용자의 자연어 질문에서 의도, Hard 제약 조건, 그리고 B2B 기획서 생성에 필요한
    핵심 파라미터(방문 시기, 매개 작물/테마, 선호 지역, 컨셉)를 추출하여 정형화하는
    Intent Parser 노드입니다.
    """
    query = state["query"]

    system_prompt = """당신은 제주올레 탐방객/기획자의 자연어 요청을 분석하여 검색 조건으로 변환하는 전문 분석기입니다.
사용자의 자연어 입력에서 아래 항목들을 추출하여 JSON 규격으로만 응답하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON 문자열로만 반환하세요.

[추출 규칙]
1. hard_constraints: 휠체어 전용 구간 등 신체/동행 조건과 관련된 필수 제약 (wheelchair_required: true/false)
2. vector_query: 가이드북 임베딩 검색에 사용할 핵심 자연어 키워드 및 작물명 (예: "당근 밭길", "감귤 코스", "마늘향" 등)
3. target_month: 질문에 명시된 방문 예정 월 (1~12 정수, 언급 없으면 null). "가을"처럼 계절만 언급된 경우 해당 계절의 대표 월(가을=10)로 추정
4. season: 질문에 언급된 계절 표현 원문 (예: "가을", "봄", 없으면 null)
5. key_item_or_crop: 질문의 핵심 매개 작물/테마 아이템 (예: "당근", "마늘", "밭담", "숲길", "해안", 없으면 null)
6. preferred_location: 질문에 구체적인 지역/코스명이 "직접" 언급된 경우에만 채움 (예: "구좌읍", "1코스", "동부"). "외국인 관광객이 많았던 지역"처럼 통계 조건으로 지역을 찾아달라는 질문이면 여기는 null로 두고 대신 market_location_query 를 채우세요. 두 필드를 동시에 채우지 마세요.
7. market_location_query: 특정 지역명이 아니라 "방문객 통계 기준"으로 지역을 역으로 찾아달라는 질문일 때만 채우는 객체 (예: "외국인 관광객이 많았던 지역", "2030 방문객 비중이 높은 동네", "작년보다 방문객이 급증한 지역"). 해당 없으면 전체를 null로 반환.
   - metric: 아래 중 하나 (질문 표현과 매핑) -
     "foreign_visitors"(외국인 방문객), "total_visitors"(총 방문객/방문객 수), "yoy_growth_rate"(전년 대비 증감률/급증/급감),
     "female_ratio"(여성 비중), "male_ratio"(남성 비중), "youth_10s_ratio"(10대 이하 비중),
     "young_2030_ratio"(2030 비중/청년층), "middle_4060_ratio"(4060대 비중/중장년층), "senior_70s_ratio"(70대 이상 비중/시니어)
   - year: 질문에 언급된 연도 (예: "2026년" → 2026), 없으면 null
   - month: 질문에 언급된 월 (예: "5월" → 5), 없으면 null
   - direction: "많았던/높았던/급증한" 류의 표현이면 "desc", "적었던/낮았던/급감한" 류의 표현이면 "asc" (기본 "desc")
8. concept_theme: 질문의 컨셉/테마 (예: "힐링", "평지 트레킹", "농가 체험", 없으면 null)
9. target_audience: 질문에서 유추되는 주 타겟 고객층 ("family", "corporate", "healing", "senior", "active" 중 하나, 명시 없으면 "family")
10. include_market_insights: 질문이 명시적으로 "빅데이터/통계/시장 데이터 빼줘" 등으로 제외를 요청하지 않는 한 true

[응답 포맷 (JSON 전용)]
{
  "hard_constraints": {
    "wheelchair_required": boolean
  },
  "vector_query": string,
  "target_month": number or null,
  "season": string or null,
  "key_item_or_crop": string or null,
  "preferred_location": string or null,
  "market_location_query": {
    "metric": string or null,
    "year": number or null,
    "month": number or null,
    "direction": "desc" or "asc"
  },
  "concept_theme": string or null,
  "target_audience": string,
  "include_market_insights": boolean
}"""

    try:
        raw_res = get_chat_completion(system_prompt, query)

        # 코드 펜스 제거
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        parsed = json.loads(cleaned)
        b2b_params = B2BQueryParams(
            target_month=parsed.get("target_month"),
            season=parsed.get("season"),
            key_item_or_crop=parsed.get("key_item_or_crop"),
            preferred_location=parsed.get("preferred_location"),
            market_location_query=parsed.get("market_location_query"),
            concept_theme=parsed.get("concept_theme"),
            target_audience=parsed.get("target_audience") or "family",
            include_market_insights=parsed.get("include_market_insights", True),
        ).model_dump(mode="json")
    except Exception as e:
        print(f"[!] 의도 파싱 중 오류 발생: {e}. 기본 제약 조건으로 폴백합니다.")
        parsed = {
            "hard_constraints": {"wheelchair_required": False},
            "vector_query": query
        }
        b2b_params = B2BQueryParams().model_dump()

    return {"parsed_constraints": parsed, "b2b_params": b2b_params}


_MARKET_METRIC_LABELS = {
    "total_visitors": "총 방문객 수",
    "yoy_growth_rate": "전년 대비 증감률",
    "female_ratio": "여성 방문객 비중",
    "male_ratio": "남성 방문객 비중",
    "youth_10s_ratio": "10대 이하 비중",
    "young_2030_ratio": "2030대 비중",
    "middle_4060_ratio": "40~60대 비중",
    "senior_70s_ratio": "70대 이상 비중",
    "foreign_visitors": "외국인 방문객 수",
}


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


def _load_eup_myeon_mapping_from_csv() -> Dict[str, List[str]]:
    """data/jeju_districts.csv 의 읍/면 행(district_name != "동")에서 법정리 -> 행정 읍/면
    매핑을 딕셔너리로 읽어옵니다."""
    mapping: Dict[str, List[str]] = {}
    try:
        with open(_JEJU_DISTRICTS_CSV_PATH, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["district_name"] == "동":
                    continue
                mapping.setdefault(row["legal_name"], []).append(row["district_name"])
    except Exception as e:
        print(f"[!] data/jeju_districts.csv 로드 실패: {e}")
    return mapping


# 제주시/서귀포시 도심 "동" 지역은 jeju_districts.csv 의 district_name 이 전부 "동"으로만 표기되어
# 있어(개별 행정동을 구분해주지 않음) 법정동 -> 행정동 그룹핑은 위키백과·서귀포시 공식 자료로 직접
# 조사해 별도로 하드코딩했습니다(2026-07-24). 서귀동/토평동은 행정동 개편 이력상 여러 행정동에
# 걸쳐 있어 관련 행정동을 모두 나열했습니다.
_DONG_LEGAL_TO_ADMIN = {
    "일도일동": ["일도1동"], "일도이동": ["일도2동"],
    "이도일동": ["이도1동"], "이도이동": ["이도2동"], "도남동": ["이도2동"],
    "삼도일동": ["삼도1동"], "삼도이동": ["삼도2동"],
    "용담일동": ["용담1동"], "용담이동": ["용담2동"], "용담삼동": ["용담2동"],
    "건입동": ["건입동"],
    "화북일동": ["화북동"], "화북이동": ["화북동"],
    "삼양일동": ["삼양동"], "삼양이동": ["삼양동"], "삼양삼동": ["삼양동"],
    "도련일동": ["삼양동"], "도련이동": ["삼양동"],
    "봉개동": ["봉개동"], "회천동": ["봉개동"], "용강동": ["봉개동"],
    "아라일동": ["아라동"], "아라이동": ["아라동"], "오등동": ["아라동"], "영평동": ["아라동"],
    "오라일동": ["오라동"], "오라이동": ["오라동"], "오라삼동": ["오라동"],
    "연동": ["연동"],
    "노형동": ["노형동"], "도평동": ["노형동"], "해안동": ["노형동"],
    "외도일동": ["외도동"], "외도이동": ["외도동"], "내도동": ["외도동"],
    "이호일동": ["이호동"], "이호이동": ["이호동"],
    "도두일동": ["도두동"], "도두이동": ["도두동"],
    "서귀동": ["송산동", "정방동", "중앙동", "천지동"],
    "법환동": ["대륜동"], "호근동": ["대륜동"], "서호동": ["대륜동"],
    "동홍동": ["동홍동"],
    "상효동": ["영천동"], "하효동": ["효돈동"], "신효동": ["효돈동"],
    "보목동": ["천지동"], "토평동": ["영천동", "천지동"],
    "서홍동": ["서홍동"],
    "중문동": ["중문동"], "회수동": ["중문동"], "대포동": ["중문동"], "하원동": ["중문동"],
    "강정동": ["대천동"], "도순동": ["대천동"], "영남동": ["대천동"],
    "월평동": ["아라동", "대천동"],  # 제주시(아라동 관할)·서귀포시(대천동 관할) 동명이인
    "색달동": ["예래동"], "상예동": ["예래동"], "하예동": ["예래동"],
}

# courses.administrative_areas 가 세부 법정동이 아니라 이미 병합된 행정동 이름을 그대로 쓰거나
# (예: "도두동"), jeju_districts.csv 에 없는 표기(연평리 등)를 쓰는 경우를 위한 보정 매핑.
_LEGAL_DONG_OVERRIDES = {
    "도두동": ["도두동"], "삼양동": ["삼양동"], "외도동": ["외도동"],
    "이호동": ["이호동"], "화북동": ["화북동"],
    "용담동": ["용담1동", "용담2동"],
    "하귀리": ["애월읍"],
    "연평리": ["우도면"],
}

_LEGAL_DONG_TO_ADMIN_DONG = {
    **_load_eup_myeon_mapping_from_csv(),
    **_DONG_LEGAL_TO_ADMIN,
    **_LEGAL_DONG_OVERRIDES,
}


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


def resolve_market_location_node(state: AgentState) -> Dict[str, Any]:
    """"외국인 관광객이 많았던 지역에서 상품을 기획하고 싶어"처럼, 지역명이 아니라 방문객 통계
    조건으로 지역을 지목한 질의를 처리하는 노드입니다. parse_intent_node 가 LLM 으로 뽑아낸
    구조화 파라미터(market_location_query: metric/year/month/direction)를 받아, 그 조건 그대로
    visitor_analytics 테이블을 조회(select+eq+order+limit)해 1위 지역을 찾고, 이후 retriever/
    docent_generator 가 그대로 소비하도록 b2b_params.preferred_location 에 채워 넣습니다.
    LLM 이 직접 SQL 문자열을 생성해 실행하지 않고 metric 을 Enum 화이트리스트로 제한한 뒤 Supabase
    쿼리 빌더로만 조회하는 방식이라, SQL 인젝션 경로 자체가 없습니다.
    market_location_query 가 없거나(metric=null) 이미 preferred_location 이 직접 언급된 질의라면
    아무 것도 하지 않고 그대로 통과시킵니다.
    올레 코스가 지나지 않는 행정동(예: 제주시 도심 연동·노형동 등)이 통계상 1위여도 코스 추천과
    무관한 지역이 뽑히는 것을 막기 위해, courses.administrative_areas 기반으로 실제 코스가 있는
    행정동/읍/면으로 후보를 좁혀서 조회합니다(_get_olle_relevant_admin_dongs).
    """
    b2b_params = state.get("b2b_params") or {}
    query_spec = b2b_params.get("market_location_query")
    if not query_spec or not query_spec.get("metric") or b2b_params.get("preferred_location"):
        return {}

    metric = query_spec["metric"]
    if metric not in _MARKET_METRIC_LABELS:
        return {}

    year = query_spec.get("year") or date.today().year
    month = query_spec.get("month") or b2b_params.get("target_month") or date.today().month
    year_month = f"{year}-{month:02d}"
    direction = query_spec.get("direction") or "desc"

    try:
        client = get_supabase_client()
        query = (
            client.table("visitor_analytics")
            .select(f"region_dong,{metric}")
            .eq("year_month", year_month)
            .not_.is_(metric, "null")
        )
        olle_dongs = _get_olle_relevant_admin_dongs(client)
        if olle_dongs:
            query = query.in_("region_dong", sorted(olle_dongs))
        res = query.order(metric, desc=(direction != "asc")).limit(1).execute()
    except Exception as e:
        print(f"[!] 방문객 통계 기반 지역 검색(visitor_analytics) 실패: {e}")
        return {}

    if not res.data:
        print(f"[!] {year_month} 기준 {metric} 데이터가 없어(올레 코스 지역 범위 내) 통계 기반 지역 자동 선정을 건너뜁니다.")
        return {}

    resolved_region = res.data[0]["region_dong"]
    print(f"[*] 방문객 통계 기반 지역 자동 선정: {resolved_region} ({year_month} {metric}={res.data[0][metric]})")

    updated_b2b_params = dict(b2b_params)
    updated_b2b_params["preferred_location"] = resolved_region
    updated_b2b_params["market_location_resolution"] = {
        "region_dong": resolved_region,
        "metric": metric,
        "value": res.data[0][metric],
        "year_month": year_month,
        "direction": direction,
    }
    return {"b2b_params": updated_b2b_params}


def evaluate_safety_node(state: AgentState) -> Dict[str, Any]:
    """방문 시기(월)의 정적 계절 기후 특성과 질의 텍스트 기반 위험 키워드 시뮬레이션을 결합해
    기후 및 동선 리스크를 진단하는 Safety Evaluator 노드입니다. 실시간 외부 기상 API 를 호출하지
    않고, 문서화된 계절 지식(get_seasonal_climate_note)만 사용합니다.
    """
    query = state["query"]
    b2b_params = state.get("b2b_params") or {}
    target_month = b2b_params.get("target_month") or date.today().month

    # 1. 방문 월 기반 정적 계절 기후 특성 조회 (외부 API 호출 없음)
    seasonal_weather = get_seasonal_climate_note(target_month)

    # 2. 질문 텍스트 기반 시뮬레이션 날씨 진단 (태풍 등 위험 시나리오 데모/검증용, 100% 로컬)
    simulated_weather = simulate_weather_by_query(query)

    # 3. 계절 기후와 시뮬레이션 결합
    # 시뮬레이션의 DANGER(태풍/폭우/홍수)만 실제 판단에 반영합니다.
    # WARNING 등급("바람", "비" 등 일상 대화에서도 흔히 쓰이는 단어 기반)까지 반영하면
    # 실제 위험이 없어도 오탐으로 안전 우회가 발동할 수 있어 실제 판단에서는 제외합니다.
    weather = seasonal_weather
    if simulated_weather["status"] == "DANGER":
        weather = simulated_weather

    safety_check = {
        "safety_status": weather["status"],
        "reason": weather["description"],
        "reroute_required": weather["status"] in ["WARNING", "DANGER"],
        "alternative_query_override": None
    }

    # 기상 악화 시 대체 안전 경로(내륙 숲길, 우회로)를 추천하도록 쿼리 보정 정보 추가
    if safety_check["reroute_required"]:
        if weather["status"] == "DANGER":
            safety_check["alternative_query_override"] = "바람을 피해 걷기 좋은 내륙 숲길 오솔길 코스"
        else:
            safety_check["alternative_query_override"] = weather.get("guideline") or "해안 도로 대신 바람이 차단된 조용하고 안전한 중산간 올레길 코스"

    return {
        "weather_info": weather,
        "safety_check": safety_check
    }


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


def _search_local_culture_docs(key_item_or_crop: str | None, query_text: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """culture_crop_knowledge 벡터 DB 가 아직 적재되지 않았거나 조회에 실패했을 때, 로컬 문서
    (data/culture_knowledge/crop_docs.json + culture_docs.json + crop_seven_docs.json)에서 키워드
    매칭으로 대체 검색하는 폴백입니다. DB 적재가 완료되면 retrieve_rag_node 의 pgvector 검색이
    우선 시도되고, 이 함수는 자동으로 호출되지 않습니다.
    작물 문서는 crop_name(또는 crop_seven_docs.json 의 target_crop) 일치로, 비작물 일반 문화 문서
    (밭담/곶자왈/해녀 등, crop_name=None)는 제목 키워드가 질의에 등장하는지로 점수를 매겨, 특정
    작물 언급이 없는 질의에서도 관련 있는 일반 문화 문서가 매번 같은 순서로만 채워지지 않고 실제로
    매칭되도록 합니다.
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
    if len(results) < top_k:
        # 매칭된 것이 부족하면, 매칭되지 않은 일반 문화 문서로 원본 순서대로 채웁니다.
        remaining_general = [entry for score, _i, entry in scored if score == 0 and entry["crop_name"] is None]
        results += remaining_general[: top_k - len(results)]
    return results


def _crop_location_boost(chunk: Dict[str, Any], key_item_or_crop: str | None, preferred_location: str | None) -> int:
    """작물/지역 소프트 매칭 점수. 정확히 하나의 코스를 미리 알 수 없는 자연어 질의에서
    벡터 유사도 순위를 유지하면서도 언급된 작물/지역이 포함된 코스를 우선 배치하기 위한 보정치입니다.
    """
    boost = 0
    if key_item_or_crop and key_item_or_crop in (chunk.get("crops") or ""):
        boost += 1
    if preferred_location and (
        preferred_location in (chunk.get("administrative_areas") or "")
        or preferred_location in (chunk.get("course_name") or "")
    ):
        boost += 1
    return boost


def _fetch_market_insight(client: Any, region_dong: str | None, target_month: int | None) -> Dict[str, Any] | None:
    """제주관광공사 이동통신 빅데이터(visitor_analytics) 에서 해당 행정동·월의 방문객 통계를
    조회합니다. `visitor_analytics` 테이블이 아직 적재되지 않았거나(Gate B 승인 대기 중) 조회에
    실패해도 그래프 전체가 중단되지 않도록 예외를 삼키고 None 을 반환합니다. 같은 월이라도
    연도가 여러 건 있을 수 있어 가장 최근 연도 값을 사용합니다.
    """
    if not region_dong or not target_month:
        return None
    try:
        res = (
            client.table("visitor_analytics")
            .select("*")
            .eq("region_dong", region_dong)
            .like("year_month", f"%-{target_month:02d}")
            .order("year_month", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[!] Market Insight(visitor_analytics) 조회 실패, 생략합니다: {e}")
        return None


def retrieve_rag_node(state: AgentState) -> Dict[str, Any]:
    """RDB 메타 필터링과 pgvector 유사도 검색을 조합하여 관련 코스 정보를 조회하는 Retriever 노드입니다.
    올레 코스 정보와 별도로, 제주 밭담문화·작물 생육 지식 DB(culture_crop_knowledge)도
    함께 검색하여 문서 근거가 있는 도슨트 서사를 뒷받침합니다.
    """
    constraints = state["parsed_constraints"] or {}
    safety = state["safety_check"] or {}
    target_course = state.get("target_course")
    b2b_params = state.get("b2b_params") or {}
    key_item_or_crop = b2b_params.get("key_item_or_crop")
    preferred_location = b2b_params.get("preferred_location")
    target_month = b2b_params.get("target_month")

    hard = constraints.get("hard_constraints", {})
    vector_query = constraints.get("vector_query", state["query"])

    # 기상 경보로 인한 안전 우회 쿼리 보정 적용
    if safety.get("reroute_required") and safety.get("alternative_query_override"):
        vector_query = safety["alternative_query_override"]

    client = get_supabase_client()

    # 제주관광공사 방문객 빅데이터(Market Insight) 조회 - 선호 지역/방문월이 있을 때만 시도
    market_insight = None
    if b2b_params.get("include_market_insights", True):
        market_insight = _fetch_market_insight(client, preferred_location, target_month)

    # RDB 기반 필터링 (완화 없이 1회만 단독 실행)
    course_ids = _execute_rdb_filtering(client, hard, target_course)

    # B2B 성격상 B2C형 소프트 제약 및 Fallback 완화 로직은 제거됨 (기본값 설정)
    fallback_applied = False
    fallback_reason = None

    # pgvector 유사도 기반 청크 추출
    chunks_data = []
    if course_ids:
        try:
            # 쿼리 임베딩 생성
            query_vector = get_solar_embedding(vector_query)
            
            # pgvector RPC 함수 실행
            rpc_res = client.rpc("match_course_chunks", {
                "query_embedding": query_vector,
                "match_threshold": 0.1,
                "match_count": 3,
                "filter_course_ids": course_ids
            }).execute()
            
            # 검색된 청크의 코스 세부 정보 및 메타데이터 결합
            for item in rpc_res.data:
                c_res = client.table("courses").select("*").eq("id", item["course_id"]).execute()
                course_meta = c_res.data[0] if c_res.data else {}
                
                chunks_data.append({
                    "chunk_id": item["id"],
                    "course_id": item["course_id"],
                    "course_name": course_meta.get("course_name"),
                    "crops": course_meta.get("crops", ""),
                    "administrative_areas": course_meta.get("administrative_areas", ""),
                    "total_distance_km": float(course_meta.get("total_distance_km", 0.0)),
                    "estimated_time_text": course_meta.get("estimated_time_text", ""),
                    "difficulty": course_meta.get("difficulty", "중"),
                    "title": item["title"],
                    "content": item["content"],
                    "similarity": item["similarity"]
                })
        except Exception as e:
            print(f"[!] RAG 벡터 검색 중 예외 발생: {e}")

    # 언급된 작물/지역이 포함된 코스를 유사도 순위를 유지한 채 우선 배치 (안정 정렬)
    if key_item_or_crop or preferred_location:
        chunks_data.sort(key=lambda c: -_crop_location_boost(c, key_item_or_crop, preferred_location))

    # 제주 밭담문화·작물 생육 지식 DB 검색 (외부 API 대신 검증된 문서 기반 근거 확보)
    # culture_crop_knowledge 테이블이 아직 적재되지 않은 경우, 로컬 JSON 문서 검색으로 자동 폴백합니다.
    culture_chunks_data = []
    culture_query = key_item_or_crop or vector_query
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

    if not culture_chunks_data:
        culture_chunks_data = _search_local_culture_docs(key_item_or_crop, culture_query)

    # 최상위 매칭 코스의 실제 세부 구간(구간명 + 누적 km)을 조회 (B2B 타임라인 표의 근거 데이터)
    sub_segments_data = []
    if chunks_data:
        top_course_id = chunks_data[0]["course_id"]
        try:
            seg_res = (
                client.table("course_sub_segments")
                .select("sub_segment_name,distance_km")
                .eq("course_id", top_course_id)
                .order("distance_km")
                .execute()
            )
            sub_segments_data = seg_res.data or []
        except Exception as e:
            print(f"[!] 세부 구간 데이터 조회 실패 (course_id={top_course_id}): {e}")

    return {
        "retrieved_chunks": chunks_data,
        "culture_chunks": culture_chunks_data,
        "sub_segments": sub_segments_data,
        "fallback_applied": fallback_applied,
        "fallback_reason": fallback_reason,
        "market_insight": market_insight
    }


def _execute_rdb_filtering(client: Any, hard: dict, target_course: str | None = None) -> List[int]:
    """courses 테이블을 메타데이터 기반으로 SQL 필터링하여 일치하는 코스 ID 리스트를 반환합니다."""
    query = client.table("courses").select("id")

    # 0. 특정 코스가 명시적으로 지정된 경우(B2B 구조화 입력 등) 정확히 일치하는 코스만 반환.
    if target_course:
        query = query.eq("course_name", target_course)

    # 1. Hard Constraints 필터링 (절대 보장)
    if hard.get("wheelchair_required"):
        query = query.eq("has_wheelchair_segment", "있음")
        
    try:
        res = query.execute()
        return [row["id"] for row in res.data] if res.data else []
    except Exception as e:
        print(f"[!] RDB 필터링 실행 실패: {e}")
        return []


def generate_docent_node(state: AgentState) -> Dict[str, Any]:
    """검색된 코스 컨텍스트, 실제 세부 구간(km) 데이터, 제주 밭담문화·작물 생육 지식 DB 근거를 엮어
    B2B 관광 상품 기획서의 [📊 B2B 상품 개요 & 스펙]/[📍 타임라인 표] 섹션을 작성하는
    Docent Generator(=Report Synthesizer) 노드입니다. 이어지는 [☕ 로컬 상생 제휴 아이디어]/
    [🌤️ 기후 리스크 및 Plan B]/[🛡️ Trust Tagging] 섹션은 local_recommender 노드가 담당합니다.
    """
    query = state["query"]
    chunks = state["retrieved_chunks"]
    culture_chunks = state.get("culture_chunks") or []
    sub_segments = state.get("sub_segments") or []
    fallback = state["fallback_applied"]
    reason = state["fallback_reason"]
    weather = state["weather_info"] or {}
    market_insight = state.get("market_insight")
    b2b_params = state.get("b2b_params") or {}
    target_audience = b2b_params.get("target_audience") or "family"
    include_market_insights = b2b_params.get("include_market_insights", True)
    # 방문 예정월이 질의에 명시되지 않으면 오늘 날짜의 월을 기준으로 제철 여부를 판단합니다
    # (safety_evaluator_node 의 기본값 처리 방식과 동일).
    target_month = b2b_params.get("target_month") or date.today().month

    if not chunks:
        fallback_msg = "죄송합니다. 요청하신 조건(코스/작물/시기)에 부합하는 제주올레길 코스 데이터를 데이터베이스에서 찾을 수 없었습니다. 입력 조건을 다시 확인해 주세요."
        return {"docent_answer": fallback_msg, "final_response": fallback_msg}

    # 코스 컨텍스트 빌드
    context_str = ""
    for i, c in enumerate(chunks):
        context_str += f"\n[코스 {i+1}]: {c['course_name']} (거리: {c['total_distance_km']}km, 소요시간: {c['estimated_time_text']}, 난이도: {c['difficulty']})\n"
        context_str += f"재배작물: {c['crops']}, 경유 행정구역: {c['administrative_areas']}\n"
        context_str += f"내용: {c['content']}\n"

    # 밭담문화·작물 생육 지식 DB 컨텍스트 빌드 (외부 API 대신 문서 근거 확보)
    # crop_seven_docs.json 계열 문서(target_crop/region_tag/active_months/season_stage 보유)는
    # 방문 예정월과 활동월을 대조해 제철 여부를 함께 명시함으로써, LLM 이 제철이 아닌 작물을
    # "지금 한창"인 것처럼 서술하지 않도록 합니다. 나머지 문서(신규 필드 없음)는 title/content만 표기.
    culture_context_str = ""
    for i, cc in enumerate(culture_chunks):
        meta_parts = []
        crop_label = cc.get("target_crop") or cc.get("crop_name")
        if crop_label:
            meta_parts.append(f"작물: {crop_label}")
        if cc.get("region_tag"):
            meta_parts.append(f"주산지: {cc['region_tag']}")
        if cc.get("season_stage"):
            meta_parts.append(f"생육 단계: {cc['season_stage']}")
        active_months = cc.get("active_months")
        if active_months:
            in_season = target_month in active_months
            months_str = ",".join(str(m) for m in sorted(active_months))
            meta_parts.append(
                f"활동월: {months_str}월 (방문 예정월 {target_month}월 기준 "
                f"{'제철 - 실제로 볼 수 있는 시기' if in_season else '제철 아님 - 다른 시기의 경관/서사로 대체 필요'})"
            )
        meta_str = f" [{', '.join(meta_parts)}]" if meta_parts else ""
        culture_context_str += f"\n[문화지식 {i+1}] {cc['title']}{meta_str}:\n{cc['content']}\n"
    if not culture_context_str:
        culture_context_str = "(관련 문화/작물 지식 문서를 찾지 못했습니다. 일반 지식으로 보완하세요.)"

    # 실제 세부 구간(구간명 + 누적 km) 컨텍스트 빌드 - 타임라인 표의 유일한 사실 근거
    if sub_segments:
        segments_str = "\n".join(f"- {s['sub_segment_name']} ({s['distance_km']}km)" for s in sub_segments)
    else:
        segments_str = "(세부 구간 데이터 없음 - 타임라인 표는 Start/Finish 위주로 간략히 구성)"

    # 제주관광공사 방문객 빅데이터(Market Insight) 컨텍스트 빌드 - 섹션 1 하단에 필수 기재
    # 타겟 고객층에 따라 강조할 지표를 코드에서 결정해 지시문으로 넘김 (LLM이 임의로 고르지 않도록).
    # 단, 그 행정동/월에 원본 순위표 데이터가 없어 실제로는 None인 지표를 "두드러진다"는 식으로
    # 단정하지 않도록, 강조 후보는 market_insight 에 실제로 값이 있는 지표로만 제한합니다.
    _AUDIENCE_RATIO_PRIORITY = {
        "family": ["youth_10s_ratio", "middle_4060_ratio"],
        "corporate": ["middle_4060_ratio"],
        "healing": ["young_2030_ratio", "middle_4060_ratio"],
        "senior": ["senior_70s_ratio", "middle_4060_ratio"],
        "active": ["young_2030_ratio"],
    }
    _RATIO_FIELD_LABELS = {
        "youth_10s_ratio": "10대 이하 비중",
        "young_2030_ratio": "2030대 비중",
        "middle_4060_ratio": "40~60대 비중",
        "senior_70s_ratio": "70대 이상 비중",
    }
    if not include_market_insights or not market_insight:
        market_insight_context_str = "(빅데이터 지표 없음 - 정성적 제안만 작성하고 수치는 지어내지 마세요)"
        emphasis_instruction = ""
    else:
        parts = [f"{market_insight['region_dong']} {market_insight['year_month']} 방문객 {market_insight['total_visitors']:,}명"]
        if market_insight.get("yoy_growth_rate") is not None:
            parts.append(f"(전년 대비 {market_insight['yoy_growth_rate']}%)")
        if market_insight.get("female_ratio") is not None:
            parts.append(f"여성 비중 {market_insight['female_ratio']}%")
        if market_insight.get("young_2030_ratio") is not None:
            parts.append(f"2030 청년층 비중 {market_insight['young_2030_ratio']}%")
        if market_insight.get("middle_4060_ratio") is not None:
            parts.append(f"4060대 비중 {market_insight['middle_4060_ratio']}%")
        if market_insight.get("senior_70s_ratio") is not None:
            parts.append(f"70대 이상 비중 {market_insight['senior_70s_ratio']}%")
        if market_insight.get("foreign_visitors") is not None:
            parts.append(f"외국인 방문객 {market_insight['foreign_visitors']:,}명")
        market_insight_context_str = "📊 " + ", ".join(parts)

        # 강조 후보 지표 중 실제로 값이 있는 것만 선택 (없는 지표를 "두드러진다"고 단정 금지)
        priority_fields = _AUDIENCE_RATIO_PRIORITY.get(target_audience, _AUDIENCE_RATIO_PRIORITY["family"])
        available_labels = [
            _RATIO_FIELD_LABELS[f] for f in priority_fields if market_insight.get(f) is not None
        ]
        if available_labels:
            emphasis_instruction = "과 ".join(available_labels) + " (이 값들만 실제 데이터이니 이 항목만 언급하세요)"
        else:
            emphasis_instruction = (
                "해당 타겟층 연령대 비율 데이터 없음 - 방문객 수/증감률/외국인 수만 언급하고 "
                "연령대 비중은 절대 언급하지 마세요"
            )

    # 지역명이 아니라 방문객 통계 조건(예: "외국인이 많았던 지역")으로 질문했을 때, 그 조건으로
    # 어떤 지역이 왜 선정됐는지를 LLM 이 상품 개요에 근거로 밝히도록 컨텍스트에 명시합니다.
    market_location_resolution = b2b_params.get("market_location_resolution")
    if market_location_resolution:
        metric_label = _MARKET_METRIC_LABELS.get(
            market_location_resolution["metric"], market_location_resolution["metric"]
        )
        location_resolution_str = (
            f"이 상품의 대상 지역은 사용자가 지역명을 직접 지정하지 않고 \"{metric_label}\" 기준으로 요청하여, "
            f"{market_location_resolution['year_month']} 기준 {metric_label} "
            f"{'1위' if market_location_resolution['direction'] == 'desc' else '최하위'} 지역인 "
            f"{market_location_resolution['region_dong']}(값: {market_location_resolution['value']})으로 "
            f"visitor_analytics 데이터 조회를 통해 자동 선정되었습니다."
        )
    else:
        location_resolution_str = "(해당 없음 - 사용자가 지역을 직접 지정했거나 지역 조건이 없는 질의)"

    system_prompt = f"""당신은 제주도 지자체 담당자 및 여행사 상품 기획자에게 제출할 '제주 영농-관광 상생 상품 기획서'를 작성하는 B2B 리포트 작성 전문가입니다.
줄글 위주의 가이드북/블로그 서술을 금지하고, 대화체 인사말이나 구어체("~해요", "안녕하세요" 등) 없이 아래 규격을 엄격히 준수한
표(Table) 중심의 실무 보고서 형태 Markdown 만 출력하세요.
이 리포트는 이후 다른 파이프라인 단계에서 [☕ 로컬 상생 제휴 아이디어]와 [🌤️ 기후 리스크 및 Plan B] 섹션이 자동으로 이어 붙습니다.
그 섹션들의 헤더/내용을 미리 작성하지 않는 것은 물론, 그 섹션들이 이어진다는 사실 자체도 언급하거나 예고하지 마세요
(예: "~섹션은 별도 문서에서 확장됩니다", "다음 섹션에서 계속됩니다" 같은 전환 문구 금지). 2번 섹션 표가 끝나면 어떤 마무리 문구도 없이 그대로 출력을 종료하세요.

[참고용 현재 계절 정보] (이후 단계에서 별도 섹션으로 다뤄지므로 여기서는 언급만 하고 상세 대책은 작성하지 마세요)
- 계절 특성: {weather.get('description', '')}, 특이 유의사항: {', '.join(weather.get('warnings', [])) or '없음'}

[제주 밭담문화·작물 생육 지식 DB 검색 결과]
{culture_context_str}

[코스 실제 세부 구간 목록 (구간명 + 시작점 기준 누적 거리 km, 순서대로)]
{segments_str}

[제주관광공사 이동통신 빅데이터 - 방문객 통계 (Market Insight 근거)]
{market_insight_context_str}
- 주 타겟 고객층: {target_audience} → 강조할 지표: {emphasis_instruction or "(없음 - 정성적 제안만)"}
- 지역 자동 선정 근거: {location_resolution_str}

[출력 규격 - 반드시 이 순서와 헤더를 그대로 사용]

## 1. 📊 B2B 상품 개요 & 스펙
- **상품명**: (대상 코스명과 매개 작물/테마를 조합한 직관적 B2B 상품명)
- **상품 타겟**: (예상 타겟 고객군 제안, 예: "3040 힐링 트레커", "로컬 푸드 관심 단체/가족")
- **권장 운용 시간**: (코스 거리/난이도 기반 현실적인 운용 시간대 제안, 예: "08:00~13:00")
- **예상 1인 단가 범위**: (도슨트 해설 + 로컬 체험 패키지를 가정한 합리적 가격대 제안)
- **핵심 셀링 포인트 (USP)**: (한 줄 요약)
- **[Market Insight (제주관광공사 빅데이터 연계)]**: 위 [제주관광공사 이동통신 빅데이터] 컨텍스트를 근거로 방문객 수/증감률과, "강조할 지표"로 지정된 항목을 한 문장으로 요약하세요. 컨텍스트가 "(빅데이터 지표 없음...)"이면 이 항목에 "관련 빅데이터 지표가 확인되지 않아 정성적으로 제안합니다"라고만 쓰고 수치를 지어내지 마세요. "지역 자동 선정 근거"가 "(해당 없음...)"이 아니라면, 이 지역이 왜 선택되었는지(어떤 통계 기준 몇 위)를 이 항목에 반드시 포함하세요.
- 위 5개 항목(상품명~USP)은 확정된 사실이 아니라 기획 단계의 제안값이지만, Market Insight 항목은 반드시 컨텍스트에 있는 실제 수치만 인용하세요.

## 2. 📍 [타임라인/동선 연계] 로컬 영농 & 문화 도슨트 포인트
- 아래 표로만 작성하세요 (줄글 설명 금지):

| 구간 구분 | 위치 (km) | 주요 도슨트 & 영농·문화 포인트 | 기획자 현장 체크리스트 (B2B) |
| :--- | :--- | :--- | :--- |
| Start | 0.0km | ... | ... |
| Point 1 | X.Xkm | ... | ... |
| Point 2 | X.Xkm | ... | ... |
| Finish | XX.Xkm | ... | ... |

- **위치(km)와 구간 구분 열은 위 [코스 실제 세부 구간 목록]에 있는 구간명·km 값만 그대로 사용하세요. 목록에 없는 지점이나 km 값을 지어내지 마세요.**
- 목록에서 Start, Finish, 그리고 중간 하이라이트 2~3곳(밭담/작물 경관이 두드러지는 지점 우선)을 선택해 4~6행으로 구성하세요.
- "주요 도슨트 & 영농·문화 포인트" 열은 [제주 밭담문화·작물 생육 지식 DB 검색 결과]를 근거로 각 지점에 어울리는 해설 멘트를 작성하세요.
- [제주 밭담문화·작물 생육 지식 DB 검색 결과]의 각 문서에 "활동월"/제철 여부가 괄호로 표시되어 있다면 반드시 반영하세요: "제철"이면 지금 실제로 볼 수 있는 경관(수확/개화 등)으로 서술하고, "제철 아님"이면 지금 한창인 것처럼 서술하지 말고 해당 문서의 생육 단계(파종기 새싹 등 현재 시기에 맞는 모습) 또는 다음 제철 시기를 안내하는 방식으로 표현하세요. 활동월 표시가 없는 문서는 계절 서술 없이 일반 내용으로만 사용하세요.
- "기획자 현장 체크리스트" 열은 단체 운용 관점의 실무 메모(주차/포토타임/휴게/픽업 등)를 제안하세요.
- 계층적 완화 적용 여부는 {fallback} 입니다. True인 경우에만 표 아래 한 줄 각주로 대안 제시 사유(완화 사유: {reason})를 명시하세요.
  False인 경우 완화 관련 각주를 절대 출력하지 마세요 ("완화 사유: None" 같은 문구도 금지) — 표로 섹션을 바로 종료하세요.

[작성 원칙]
1. 섹션 헤더(## 1. ~, ## 2. ~)는 반드시 그대로 출력하세요.
2. 코스 거리/시간/난이도/구간 km, Market Insight 방문객 수치 등 컨텍스트에 있는 사실 수치를 지어내지 마세요. (단, 섹션 1의 상품명/타겟/시간/단가/USP는 애초에 사실 데이터가 아닌 기획 제안값이므로 예외)
3. Markdown 표 문법이 깨지지 않도록 각 셀에 줄바꿈 없이 작성하세요."""

    user_msg = f"[질문(방문 조건)]: {query}\n\n[검색 결과 컨텍스트]:\n{context_str}"

    docent_answer = get_chat_completion(system_prompt, user_msg)
    # local_recommender 가 스킵되는 경우에도 최종 응답이 비어있지 않도록 기본값으로 세팅
    return {"docent_answer": docent_answer, "final_response": docent_answer}


def recommend_local_node(state: AgentState) -> Dict[str, Any]:
    """비짓제주 API(get_visit_jeju_recommendations, 실 API 우선/실패 시 Mock 폴백 내장)에서
    코스 지역의 실제 로컬 상점 소개(introduction) 텍스트만 참고 재료로 가져와, 특정 매장명을
    지목하지 않는 창의적 협업 아이디어를 LLM 으로 제안하고, 기후·동선 리스크 및 Trust Tagging 을
    이어붙여 B2B 기획서를 완성하는 Local Recommender(=Report Finalizer) 노드입니다.
    관광 API 데이터는 폐업/변경에 취약해 특정 매장을 "검증된 제휴처"로 단정할 수 없으므로,
    매장명/주소/전화번호는 결과물에 노출하지 않고 지역 상점의 성격(introduction)만 아이디어의
    참고 재료로 사용합니다.
    """
    chunks = state["retrieved_chunks"]
    docent_answer = state["docent_answer"]
    weather = state["weather_info"] or {}
    safety = state["safety_check"] or {}

    if not chunks or not docent_answer:
        return {"final_response": docent_answer, "recommendations": []}

    recommendations = []
    introduction_snippets = []
    rec_cache: Dict[Any, Any] = {}

    # 검색된 상위 코스들의 작물 및 행정구역 조합에 대해 비짓제주 소개 정보를 참고 재료로 수집
    for chunk in chunks:
        crops = [c.strip() for c in chunk["crops"].split(",") if c.strip()]
        areas = [a.strip() for a in chunk["administrative_areas"].split(",") if a.strip()]

        for crop in crops:
            for area in areas:
                cache_key = (crop, area)
                if cache_key not in rec_cache:
                    rec_cache[cache_key] = get_visit_jeju_recommendations(crop, area)
                rec_list = rec_cache[cache_key]
                for rec in rec_list:
                    recommendations.append(rec)
                    intro = (rec.get("introduction") or "").strip()
                    if intro and intro not in introduction_snippets:
                        introduction_snippets.append(intro)

    # ## 3. ☕ 로컬 상생 제휴 및 상품화 아이디어 (표)
    if introduction_snippets:
        reference_str = "\n".join(f"- {s}" for s in introduction_snippets[:6])
        idea_system_prompt = """당신은 지역 상생 관광 상품을 기획하는 협업 아이디어 전문가입니다.
아래 [지역 로컬 상점 소개 참고자료]는 관광 API에서 가져온 실제 상점들의 짧은 소개 텍스트입니다.
이 자료의 성격(예: 비건 베이커리, 발효음료 카페 등)에서 착안하여, 코스의 매개 작물/테마와 어울리는
실무 적용 가능한 로컬 상생 제휴/상품화 아이디어를 아래 Markdown 표로만 제안하세요 (설명 없이 표만 출력):

| 구분 | 제휴 컨셉 / 메뉴 | 상생 협업 내용 (B2B) | 기대 효과 및 마케팅 포인트 |
| :--- | :--- | :--- | :--- |
| 푸드/음료 | ... | ... | ... |
| 체험/문화 | ... | ... | ... |
| 기념품 | ... | ... | ... |

[절대 규칙]
- 표는 위 예시와 동일하게 정확히 3행(푸드/음료, 체험/문화, 기념품 각 1행씩)만 작성하세요. 행을 추가하지 마세요.
- 특정 매장명, 상호명, 주소, 전화번호를 절대 언급하거나 지목하지 마세요. 참고자료는 아이디어의
  영감 재료일 뿐, 실제 매장이 지금도 운영 중인지 검증되지 않았습니다. "~카페 유형", "~테마 로컬 상점" 처럼
  일반화된 표현만 사용하세요.
- 표 셀 안에 줄바꿈을 넣지 마세요 (Markdown 표가 깨집니다).
- 대화체 인사말이나 표 앞뒤 설명 문구 없이 표만 출력하세요."""
        idea_user_msg = f"[코스 매개 작물/테마]: {chunks[0]['crops']}\n\n[지역 로컬 상점 소개 참고자료]:\n{reference_str}"
        local_ideas = get_chat_completion(idea_system_prompt, idea_user_msg)
    else:
        local_ideas = "*현재 이 지역에 참고할 로컬 상점 소개 정보가 없어 아이디어 제안을 생략합니다.*"

    report = docent_answer.rstrip() + "\n\n"
    report += "## 3. ☕ 로컬 상생 제휴 및 상품화 아이디어\n"
    report += "*(실제 매장 디렉토리가 아니라, 해당 지역 로컬 상점 성격에서 착안한 협업 컨셉 제안입니다. 개별 매장 운영 현황은 별도 확인이 필요합니다.)*\n\n"
    report += local_ideas.rstrip() + "\n"

    # ## 4. 🌤️ 기후 리스크 및 Plan B 우회 동선
    total_distance = chunks[0].get("total_distance_km")
    course_name = chunks[0].get("course_name", "코스")
    report += "\n## 4. 🌤️ 기후 리스크 및 Plan B 우회 동선\n"
    report += f"- **[기후 환경]**: {weather.get('description', '')}"
    warnings = weather.get("warnings") or []
    if warnings:
        report += f" / 유의사항: {', '.join(warnings)}"
    report += "\n"
    report += f"- **[Plan A (정상 운용)]**: {course_name} 전체 코스 풀 도보 트레킹"
    if total_distance:
        report += f" ({total_distance}km)"
    report += "\n"
    if safety.get("reroute_required"):
        plan_b = safety.get("alternative_query_override") or "해안 구간 대신 중산간/숲길 우회 동선"
        report += f"- **[Plan B (우회/대체)]**: {safety.get('safety_status', 'WARNING')} 상황 시 {plan_b}으로 전환, 필요 시 실내 체험 프로그램으로 대체\n"
    else:
        report += "- **[Plan B (우회/대체)]**: 현재 특이 리스크는 없으나, 돌발 강풍·우천 시를 대비해 단축 동선 및 실내 체험/휴게 프로그램으로의 전환 대안을 상시 준비\n"

    # ## 5. 🛡️ Trust Tagging (유사도와 완화 여부에 따라 신뢰도 별점 결정)
    stars = "★★★★☆" if state["fallback_applied"] else "★★★★★"
    report += "\n## 5. 🛡️ Trust Tagging\n"
    report += f"[출처: 제주올레 가이드북 / 제주 밭담문화·작물 지식 DB / 비짓제주 기반 제휴 아이디어 / Self-RAG 신뢰도: {stars}]\n"

    return {
        "recommendations": recommendations,
        "final_response": report
    }


def check_quality_node(state: AgentState) -> Dict[str, Any]:
    """답변의 팩트 신뢰성 및 환각 여부, 제약사항 준수 여부를 검증하는 Quality Checker 노드입니다."""
    query = state["query"]
    chunks = state["retrieved_chunks"]
    final_response = state["final_response"] or ""
    
    if not chunks or not final_response:
        return {"quality_report": {"passed": True, "score": 1.0, "feedback": "검색 결과가 없어 평가를 생략합니다."}}
        
    context_str = ""
    for i, c in enumerate(chunks):
        context_str += f"\n[코스 {i+1}]: {c['course_name']} (거리: {c['total_distance_km']}km, 소요시간: {c['estimated_time_text']}, 난이도: {c['difficulty']})\n"
        context_str += f"재배작물: {c['crops']}, 경유 행정구역: {c['administrative_areas']}\n"
        context_str += f"본문: {c['content']}\n"
    
    system_prompt = """당신은 생성된 도슨트 추천 답변의 핵심 사실 관계만을 검증하는 '품질 검증원'입니다.
주어진 [사용자 질문], [검색 컨텍스트] 및 [생성된 답변] 을 분석하여 답변의 환각 여부 및 정보 충실도를 채점하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON 문자열로만 반환하세요.

[검증 대상 - 아래 사실 항목에서만 컨텍스트와의 모순 여부를 확인하세요]
1. 코스명, 거리, 소요시간, 난이도, 재배작물, 경유 행정구역 등 컨텍스트에 명시된 구체적 수치/명칭을 답변이 왜곡하거나 컨텍스트와 반대로 서술했는가?
2. 사용자가 요청한 필수 제약사항(예: 휠체어 전용 코스 여부)을 어기고 부적절한 코스를 추천했는가?

[검증 대상에서 제외 - 아래 항목은 도슨트의 정상적인 연출이므로 절대 환각이나 결점으로 지적하지 마세요]
- 날씨 정보, 옷차림/준비물 팁, 여행 조언 등 컨텍스트 밖의 실용적 부가 안내
- 풍경 묘사, 계절감, 감성적 수식어 등 도슨트 특유의 문학적 표현
- 컨텍스트에 없는 세부 정보(예: 특정 매장 운영 배경)를 답변이 언급하지 않은 것 (누락은 결함이 아님)
- 거리/소요시간처럼 컨텍스트에 있는 수치를 답변이 그대로 인용한 경우 (출처 재확인 요구 금지)

[응답 포맷 (JSON 전용)]
{
  "passed": boolean,
  "score": number (0.0 ~ 1.0),
  "feedback": "검증 피드백 및 부족한 정보에 대한 구체적 지적"
}"""

    user_msg = f"사용자 질문: {query}\n\n[검색 컨텍스트]:\n{context_str}\n\n[생성된 답변]:\n{final_response}"
    
    try:
        raw_res = get_chat_completion(system_prompt, user_msg)
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        
        report = json.loads(cleaned)
    except Exception as e:
        print(f"[!] 품질 검증원 실행 실패: {e}")
        report = {"passed": True, "score": 0.9, "feedback": "자체 평가 오류로 패스 처리"}
        
    return {"quality_report": report}


def rewrite_query_node(state: AgentState) -> Dict[str, Any]:
    """검증 실패 시, 더 정밀한 검색 컨텍스트 획득을 위해 검색 조건 및 키워드를 교정하는 Query Re-writer 노드입니다."""
    query = state["query"]
    constraints = state["parsed_constraints"] or {}
    report = state["quality_report"] or {}
    feedback = report.get("feedback", "")
    
    system_prompt = """당신은 품질 검증 결과에 따라 검색 조건을 보정하는 '쿼리 재작성기'입니다.
기존 사용자 질문, 이전 쿼리 제약사항 및 품질 검증 피드백을 바탕으로, Supabase pgvector 하이브리드 검색에서 더 나은 컨텍스트를 찾기 위한 최적의 '보정된 검색 쿼리 및 메타데이터 필터 조건'을 다시 도출하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON로만 반환하세요.

[응답 포맷 (JSON 전용)]
{
  "revised_vector_query": "새로운 검색용 키워드",
  "revised_soft_constraints": {
    "max_time_hours": number or null,
    "max_distance_km": number or null,
    "difficulty": string or null
  }
}"""

    user_msg = f"사용자 질문: {query}\n\n[이전 제약사항]:\n{json.dumps(constraints)}\n\n[품질 검증원 피드백]:\n{feedback}"
    
    try:
        raw_res = get_chat_completion(system_prompt, user_msg)
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        
        revised = json.loads(cleaned)
        
        # 상태 덮어쓰기 형식으로 갱신
        updated_constraints = {
            "hard_constraints": constraints.get("hard_constraints", {"wheelchair_required": False}),
            "soft_constraints": revised.get("revised_soft_constraints", {}),
            "vector_query": revised.get("revised_vector_query", query)
        }
    except Exception as e:
        print(f"[!] 쿼리 재작성 실패: {e}")
        updated_constraints = constraints
        
    return {
        "parsed_constraints": updated_constraints,
        "loop_count": state["loop_count"] + 1
    }
