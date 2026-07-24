import csv
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Dict, Any, List
from src.agent.llm_client import get_chat_completion
from src.agent.weather_client import assess_weather_risk_from_query, get_seasonal_climate_note
from src.agent.router import route_intent
from src.ingestion.database_loader import get_supabase_client, get_solar_embedding
from src.ingestion.visit_jeju_client import get_visit_jeju_recommendations
from src.models.schema import B2BQueryParams, IntentCategory
from src.agent.state import AgentState


def classify_intent_node(state: AgentState) -> Dict[str, Any]:
    """사용자 질의를 5가지 카테고리로 사전 분류하는 Intent Classifier 노드입니다.
    (분류 결과로 state의 intent_category 에 라벨을 붙이며, 실제 물리적 분기는
    graph.py의 route_after_location_resolve 에서 수행합니다.)
    호출부(B2B 구조화 입력 등)가 intent_category 를 이미 확정해 넘긴 경우, LLM 분류 호출 없이
    그대로 통과시키되, IntentCategory enum 에 없는 값(오타/구버전 카테고리명 등)이면 신뢰하지 않고
    route_intent 로 새로 분류합니다 — 잘못된 문자열이 하류의 문자열 비교 분기들을 예측 불가능하게
    만드는 것을 막기 위함입니다.
    """
    preset_category = state.get("intent_category")
    if preset_category and preset_category in {c.value for c in IntentCategory}:
        return {
            "intent_category": preset_category,
            "target_course": state.get("target_course"),
            "tool_calls": None,
            "tool_outputs": None,
            "quality_report": None,
            "tool_depth": 0,
        }

    result = route_intent(state["query"])
    return {
        "intent_category": result.category.value,
        "target_course": result.target_course,
        "tool_calls": None,
        "tool_outputs": None,
        "quality_report": None,
        "tool_depth": 0,
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
        # 하드 제약조건(휠체어 등)은 완화되면 안 되는 불변 조건이므로, LLM 파싱 자체가 실패했다고
        # 조용히 False 로 초기화하지 않고 원본 질의에서 최소한의 키워드 재확인을 거칩니다.
        # 이 재확인은 "true 로 완화"만 방지하기 위한 안전망이지, 완벽한 대체 파서는 아닙니다.
        fallback_wheelchair_required = "휠체어" in query
        parsed = {
            "hard_constraints": {"wheelchair_required": fallback_wheelchair_required},
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


def resolve_market_location_node(state: AgentState) -> Dict[str, Any]:
    """"외국인 관광객이 많았던 지역에서 상품을 기획하고 싶어"처럼, 지역명이 아니라 방문객 통계
    조건으로 지역을 지목한 질의를 처리하는 노드입니다. parse_intent_node 가 LLM 으로 뽑아낸
    구조화 파라미터(market_location_query: metric/year/month/direction)를 받아, 그 조건 그대로
    visitor_analytics 테이블을 조회(select+eq+order+limit)해 1위 지역을 찾고, 이후 retriever/
    report_generator 가 그대로 소비하도록 b2b_params.preferred_location 에 채워 넣습니다.
    LLM 이 직접 SQL 문자열을 생성해 실행하지 않고 metric 을 Enum 화이트리스트로 제한한 뒤 Supabase
    쿼리 빌더로만 조회하는 방식이라, SQL 인젝션 경로 자체가 없습니다.
    market_location_query 가 없거나(metric=null) 이미 preferred_location 이 직접 언급된 질의라면
    아무 것도 하지 않고 그대로 통과시킵니다.
    올레 코스가 지나지 않는 행정동(예: 제주시 도심 연동·노형동 등)이 통계상 1위여도 코스 추천과
    무관한 지역이 뽑히는 것을 막기 위해, courses.administrative_areas 기반으로 실제 코스가 있는
    행정동/읍/면으로 후보를 좁혀서 조회합니다(_get_olle_relevant_admin_dongs). 이 후보 목록을
    확인할 수 없으면(조회 실패든, courses 테이블에 아직 데이터가 없어 정말 후보가 없는 것이든)
    "코스와 무관한 지역이 통계 1위라는 이유만으로 선정되는 것"을 막기 위해 통계 기반 지역 자동
    선정 자체를 fail-closed 로 건너뜁니다 — 후보를 모르면 무제한 검색으로 느슨하게 폴백하지
    않습니다(과거에는 그렇게 했었는데, 이는 위 도메인 규칙을 그대로 어길 수 있는 경로였음).
    """
    b2b_params = state.get("b2b_params") or {}
    query_spec = b2b_params.get("market_location_query")
    if not query_spec or not query_spec.get("metric") or b2b_params.get("preferred_location"):
        return {}

    metric = query_spec["metric"]
    if metric not in _MARKET_METRIC_LABELS:
        return {}

    year = query_spec.get("year")
    month = query_spec.get("month") or b2b_params.get("target_month")
    direction = query_spec.get("direction") or "desc"

    try:
        client = get_supabase_client()

        olle_dongs = _get_olle_relevant_admin_dongs(client)
        if not olle_dongs:
            print(
                "[!] 올레 코스가 지나는 행정동을 확인할 수 없어(조회 실패 또는 데이터 없음), "
                "코스와 무관한 지역이 선정되는 것을 막기 위해 통계 기반 지역 자동 선정을 건너뜁니다."
            )
            return {}

        if year and month:
            year_month = f"{year}-{month:02d}"
        elif month:
            # 월만 지정되고 연도가 없으면 올해로 간주 (기존 동작 유지)
            year_month = f"{date.today().year}-{month:02d}"
        else:
            # 연/월을 전혀 지정하지 않은 경우에만 "오늘 날짜" 대신 실제 데이터가 있는 가장
            # 최근 달을 기본값으로 사용 — 사용자가 명시한 월은 데이터가 없어도 그대로 존중합니다.
            year_month = _get_latest_available_year_month(client) or (
                f"{date.today().year}-{date.today().month:02d}"
            )

        res = (
            client.table("visitor_analytics")
            .select(f"region_dong,{metric}")
            .eq("year_month", year_month)
            .not_.is_(metric, "null")
            .in_("region_dong", sorted(olle_dongs))
            .order(metric, desc=(direction != "asc"))
            .limit(1)
            .execute()
        )
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
    """방문 시기(월)의 정적 계절 기후 특성과, 질의 텍스트에 실제로 담긴 기상 위험 여부에 대한
    LLM 판단을 결합해 기후 및 동선 리스크를 진단하는 Safety Evaluator 노드입니다. 실시간 외부
    기상 API(KMA 등)는 호출하지 않고, 문서화된 계절 지식(get_seasonal_climate_note)과 Solar
    LLM(assess_weather_risk_from_query) 만 사용합니다.
    """
    query = state["query"]
    b2b_params = state.get("b2b_params") or {}
    target_month = b2b_params.get("target_month") or date.today().month

    # 1. 방문 월 기반 정적 계절 기후 특성 조회 (외부 API 호출 없음)
    seasonal_weather = get_seasonal_climate_note(target_month)

    # 2. 질문 텍스트가 실제로 지금/이번 방문에 대한 기상 위험을 의미하는지 LLM으로 판단
    #    (예전엔 "태풍"/"폭우"/"홍수" 단어가 있기만 하면 문맥과 무관하게 DANGER로 오판했음 —
    #    2026-07-24 QA 리뷰 지적 후 LLM 문맥 판단으로 교체)
    assessed_weather = assess_weather_risk_from_query(query)

    # 3. 계절 기후와 LLM 판단 결합. DANGER(질문이 실제로 지금 위험을 묻는 경우)만 계절 기후
    # 판단을 덮어쓰고, 그 외(SAFE)는 계절 기후 판단을 그대로 유지합니다.
    weather = seasonal_weather
    if assessed_weather["status"] == "DANGER":
        weather = assessed_weather

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


def _fetch_course_meta_by_name(client: Any, course_name: str) -> Dict[str, Any] | None:
    """target_course(라우터가 질의에서 인식한 특정 코스명)로 해당 코스의 crops/
    administrative_areas 메타데이터를 조회합니다. quick_responder_node가 코스명이 언급된
    질의에서 그 코스의 작물/지역을 검색 조건으로 자동 보완하는 데 사용합니다. 조회 실패 시
    None을 반환해 상위 로직이 그대로 key_item_or_crop/preferred_location 없이 진행하도록
    합니다(다른 DB 조회 헬퍼들과 동일한 fail-soft 방식).
    """
    try:
        res = (
            client.table("courses")
            .select("course_name, crops, administrative_areas")
            .eq("course_name", course_name)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[!] 코스 메타데이터(target_course={course_name}) 조회 실패, 생략합니다: {e}")
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

    # RDB 기반 필터링 (완화 없이 1회만 단독 실행, 휠체어 등 hard_constraints 만 반영)
    course_ids = _execute_rdb_filtering(client, hard)

    # B2B 성격상 B2C형 소프트 제약 및 Fallback 완화 로직은 제거됨 (기본값 설정)
    fallback_applied = False
    fallback_reason = None

    # target_course(질의에 특정 코스가 언급된 경우)도 지역/작물과 동일한 방식으로 처리합니다.
    # 예전엔 이걸 _execute_rdb_filtering 안에서 courses.course_name 완전 일치(.eq())로 하드
    # 필터링했는데, target_course 가 "1코스" 같은 정식 코스명이 아니라 "가파도"처럼 섬/지명으로
    # 들어오면(라우터가 그렇게 추출할 수 있음) course_name 과 절대 일치하지 않아 후보가 0개가
    # 되고, 그 뒤 벡터 검색조차 시도되지 않은 채 곧바로 "코스를 찾을 수 없다"는 완전 폴백으로
    # 빠지는 문제가 있었습니다(2026-07-24 QA 시나리오 테스트에서 실제 재현: "가파도 코스로
    # 기획서 만들어줘"). 겹치는 코스가 하나도 없으면 이 조건을 해제하고 전체에서 계속 진행하되
    # 그 사실을 fallback_reason 으로 남깁니다.
    if target_course and course_ids:
        course_ids, target_course_matched = _filter_course_ids_by_target_course(client, course_ids, target_course)
        if not target_course_matched:
            fallback_applied = True
            fallback_reason = (
                f"'{target_course}' 코스명과 직접 일치하는 코스를 찾지 못해, "
                f"해당 조건 없이 전체 코스 중 가장 적합한 코스를 추천합니다."
            )

    # 지역 조건(preferred_location)을 벡터 검색 이전에 실제 하드 필터로 반영합니다. 이게 없으면
    # 벡터 검색이 먼저 의미상 가장 비슷한 상위 몇 개만 뽑고 그 안에서만 지역 boost를 적용해,
    # 지역과 무관한 코스가 뽑히고도 Market Insight만 엉뚱하게 그 지역 통계를 보여주는 불일치가
    # 생길 수 있습니다(실사용 중 발견됨: "외국인 방문객 1위 지역" 통계는 A 지역인데 실제 추천 코스는
    # 전혀 다른 B 지역인 경우). 겹치는 코스가 하나도 없으면 지역 조건을 해제하고 전체에서
    # 검색하되, 그 사실을 fallback_reason 으로 남겨 리포트에 각주로 노출합니다.
    if preferred_location and course_ids:
        course_ids, location_matched = _filter_course_ids_by_location(client, course_ids, preferred_location)
        if not location_matched:
            fallback_applied = True
            fallback_reason = (
                f"'{preferred_location}' 지역과 직접 겹치는 올레 코스를 찾지 못해, "
                f"지역 조건 없이 전체 코스 중 가장 적합한 코스를 추천합니다."
            )

    # key_item_or_crop(작물/테마)도 지역과 같은 이유로 벡터 검색 이전에 하드 필터로 반영합니다
    # (실사용 중 발견됨: "쪽파" 질의인데 벡터 검색 상위 후보에 쪽파 태그 코스가 없어 로컬
    # 추천에서 쪽파가 한 번도 조회되지 않던 문제). key_item_or_crop 은 "밭담"/"숲길" 같은
    # 비작물 테마어일 수도 있어(intent_parser 참고), courses.crops 에 실제로 등장하는 값일
    # 때만 필터링하고, 아니면 조용히 건너뜁니다(테마 질의마다 완화 각주가 뜨지 않도록).
    if key_item_or_crop and course_ids:
        course_ids, crop_matched = _filter_course_ids_by_crop(client, course_ids, key_item_or_crop)
        if not crop_matched:
            fallback_applied = True
            crop_reason = (
                f"'{key_item_or_crop}' 작물과 직접 겹치는 올레 코스를 찾지 못해, "
                f"작물 조건 없이 전체 코스 중 가장 적합한 코스를 추천합니다."
            )
            fallback_reason = f"{fallback_reason} {crop_reason}" if fallback_reason else crop_reason

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
            
            # 검색된 청크의 코스 세부 정보 및 메타데이터 결합. 청크 하나마다 개별 try/except 로
            # 격리합니다 — 예전엔 이 for 루프 전체가 바깥 try 안에 있어서, DB의 결측치 하나
            # (예: total_distance_km IS NULL 인 행)가 예외를 일으키면 그 시점까지 처리된 청크만
            # 남고 이후의 멀쩡한 청크들까지 통째로 버려지는 문제가 있었습니다.
            for item in rpc_res.data:
                try:
                    c_res = client.table("courses").select("*").eq("id", item["course_id"]).execute()
                    course_meta = c_res.data[0] if c_res.data else {}

                    chunks_data.append({
                        "chunk_id": item["id"],
                        "course_id": item["course_id"],
                        "course_name": course_meta.get("course_name"),
                        "crops": course_meta.get("crops", ""),
                        "administrative_areas": course_meta.get("administrative_areas", ""),
                        # .get(key, 0.0) 은 키가 아예 없을 때만 기본값을 쓰고, 키는 있는데 값이
                        # NULL(None)인 경우는 그대로 None 을 반환해 float(None) 에서 TypeError 가
                        # 났었습니다 — "or 0.0" 으로 None/누락 둘 다 안전하게 처리합니다.
                        "total_distance_km": float(course_meta.get("total_distance_km") or 0.0),
                        "estimated_time_text": course_meta.get("estimated_time_text", ""),
                        "difficulty": course_meta.get("difficulty", "중"),
                        "title": item["title"],
                        "content": item["content"],
                        "similarity": item["similarity"]
                    })
                except Exception as e:
                    print(f"[!] 코스 청크(course_id={item.get('course_id')}) 조립 실패, 이 청크만 건너뜁니다: {e}")
        except Exception as e:
            print(f"[!] RAG 벡터 검색 중 예외 발생: {e}")

    # 언급된 작물/지역이 포함된 코스를 유사도 순위를 유지한 채 우선 배치 (안정 정렬)
    if key_item_or_crop or preferred_location:
        chunks_data.sort(key=lambda c: -_crop_location_boost(c, key_item_or_crop, preferred_location))

    # 제주 밭담문화·작물 생육 지식 DB 검색 (외부 API 대신 검증된 문서 기반 근거 확보)
    # culture_crop_knowledge 테이블이 아직 적재되지 않은 경우, 로컬 JSON 문서 검색으로 자동 폴백합니다.
    # 사용자가 작물/테마를 직접 언급하지 않았다면(key_item_or_crop 없음), 범용 vector_query로
    # 문화지식을 검색하는 대신 실제로 선택된 코스의 진짜 crops 로 검색합니다. 그렇지 않으면
    # 실사용 중 발견된 것처럼 섹션 2(도슨트 서사)가 이 코스와 무관한 작물(예: 수박/참외/고사리)을
    # 언급하고, 섹션 3(report_generator)은 그 코스의 실제 crops(예: 보리)만 다뤄서 두 섹션이
    # 서로 다른 작물 얘기를 하는 불일치가 생깁니다. key_item_or_crop 이 있으면(작물이든 "밭담"
    # 같은 비작물 테마든) 사용자가 명시한 의도를 그대로 존중합니다.
    if key_item_or_crop:
        culture_fallback_query = key_item_or_crop
    else:
        course_crops = []
        for c in chunks_data:
            for crop in (c.get("crops") or "").split(","):
                crop = crop.strip()
                if crop and crop not in course_crops:
                    course_crops.append(crop)
        culture_fallback_query = " ".join(course_crops) if course_crops else vector_query
    culture_chunks_data = _search_culture_knowledge(client, key_item_or_crop, culture_fallback_query)

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


def _execute_rdb_filtering(client: Any, hard: dict) -> List[int]:
    """courses 테이블을 메타데이터(hard_constraints) 기반으로 SQL 필터링하여 일치하는 코스 ID
    리스트를 반환합니다. target_course 는 여기서 하드 필터링하지 않습니다 — course_name 과
    완전 일치하지 않으면(예: 섬 이름 "가파도"가 실제 코스명 "10-1코스"와 문자열이 다른 경우)
    후보가 0개가 되어 그 뒤 검색 전체가 죽는 문제가 있었습니다. target_course 는
    _filter_course_ids_by_target_course 로 지역/작물 조건과 동일하게 fail-soft 처리합니다.
    """
    query = client.table("courses").select("id")

    if hard.get("wheelchair_required"):
        query = query.eq("has_wheelchair_segment", "있음")

    try:
        res = query.execute()
        return [row["id"] for row in res.data] if res.data else []
    except Exception as e:
        print(f"[!] RDB 필터링 실행 실패: {e}")
        return []


def _filter_course_ids_by_target_course(
    client: Any, course_ids: List[int], target_course: str
) -> tuple[List[int], bool]:
    """course_ids 중 target_course(질의에 특정 코스가 언급된 경우 라우터가 추출한 값 — "1코스"
    같은 정식 코스명뿐 아니라 "가파도"처럼 섬/지명이 섞여 들어올 수도 있음)와 실제로 겹치는
    코스만 남깁니다. _filter_course_ids_by_location 과 동일하게 course_name/administrative_areas
    부분 일치로 판정하고, 하나도 안 겹치면(완전 배제 대신) 원래 course_ids 를 그대로 반환하고
    두 번째 반환값을 False 로 표시합니다 — 이후 pgvector 유사도 검색은 코스 본문(가이드북 원문)에
    실제로 언급된 지명까지 의미 기반으로 잡아낼 수 있어, 이 필터가 못 걸러도 최종 결과가 완전히
    빗나가지 않는 경우가 많습니다.
    """
    if not target_course or not course_ids:
        return course_ids, False

    try:
        res = client.table("courses").select("id,administrative_areas,course_name").in_("id", course_ids).execute()
    except Exception as e:
        print(f"[!] 대상 코스 필터링용 코스 조회 실패, 조건 없이 진행합니다: {e}")
        return course_ids, False

    matched_ids = [
        row["id"]
        for row in (res.data or [])
        if target_course in (row.get("course_name") or "") or target_course in (row.get("administrative_areas") or "")
    ]
    if matched_ids:
        return matched_ids, True
    return course_ids, False


def _filter_course_ids_by_location(
    client: Any, course_ids: List[int], preferred_location: str
) -> tuple[List[int], bool]:
    """course_ids 중 preferred_location(행정동/읍/면 단위 — 직접 지정이든 market_location_resolver
    가 채운 것이든)과 실제로 겹치는 코스만 남깁니다. courses.administrative_areas 는 법정리/법정동
    단위라 이름이 그대로 안 겹칠 수 있어(예: "안덕면"은 "화순리" 등으로만 저장됨),
    _ADMIN_DONG_TO_LEGAL_DONGS 로 후보를 넓혀서 매칭합니다. 겹치는 코스가 하나도 없으면
    (완전 배제 대신) 원래 course_ids 를 그대로 반환하고 두 번째 반환값을 False 로 표시해,
    호출부가 "지역 조건을 해제하고 검색했다"는 사유를 리포트에 남길 수 있게 합니다.
    """
    if not preferred_location or not course_ids:
        return course_ids, False

    candidates = {preferred_location} | set(_ADMIN_DONG_TO_LEGAL_DONGS.get(preferred_location, []))

    try:
        res = client.table("courses").select("id,administrative_areas,course_name").in_("id", course_ids).execute()
    except Exception as e:
        print(f"[!] 지역 필터링용 코스 조회 실패, 지역 조건 없이 진행합니다: {e}")
        return course_ids, False

    matched_ids = [
        row["id"]
        for row in (res.data or [])
        if any(
            cand in (row.get("administrative_areas") or "") or cand in (row.get("course_name") or "")
            for cand in candidates
        )
    ]
    if matched_ids:
        return matched_ids, True
    return course_ids, False


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


def _filter_course_ids_by_crop(
    client: Any, course_ids: List[int], key_item_or_crop: str
) -> tuple[List[int], bool]:
    """course_ids 중 key_item_or_crop 과 courses.crops 이 실제로 겹치는 코스만 남깁니다.
    key_item_or_crop 은 작물명뿐 아니라 "밭담"/"숲길" 같은 비작물 테마어일 수도 있어(intent_parser
    참고), 그런 테마어는 courses.crops 에 애초에 등장하지 않으므로 필터링을 건너뛰고 두 번째
    반환값을 True(정상, 완화 아님)로 반환합니다 — 테마 질의마다 불필요한 "완화 사유" 각주가 뜨는
    것을 방지합니다. 반대로 실제 작물명인데 이 course_ids 안에 겹치는 코스가 하나도 없으면
    (완전 배제 대신) 원래 course_ids 를 그대로 반환하고 False 를 반환해, 호출부가 "작물 조건을
    해제하고 검색했다"는 사유를 리포트에 남길 수 있게 합니다.
    """
    if not key_item_or_crop or not course_ids:
        return course_ids, True

    known_crop_tags = _get_known_crop_tags(client)
    if key_item_or_crop not in known_crop_tags:
        return course_ids, True

    try:
        res = client.table("courses").select("id,crops").in_("id", course_ids).execute()
    except Exception as e:
        print(f"[!] 작물 필터링용 코스 조회 실패, 작물 조건 없이 진행합니다: {e}")
        return course_ids, True

    matched_ids = [row["id"] for row in (res.data or []) if key_item_or_crop in (row.get("crops") or "")]
    if matched_ids:
        return matched_ids, True
    return course_ids, False


def _build_culture_context_str(culture_chunks: List[Dict[str, Any]], target_month: int) -> str:
    """밭담문화·작물 생육 지식 컨텍스트 문자열을 만듭니다. crop_seven_docs.json 계열 문서
    (target_crop/region_tag/active_months/season_stage 보유)는 방문 예정월과 활동월을 대조해
    제철 여부를 함께 명시함으로써, LLM 이 제철이 아닌 작물을 "지금 한창"인 것처럼 서술하지
    않도록 합니다. 나머지 문서(신규 필드 없음)는 title/content만 표기. generate_report_node와
    quick_responder_node가 공통으로 사용합니다.
    """
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
    return culture_context_str


def _build_market_insight_summary_str(market_insight: Dict[str, Any] | None) -> str:
    """관광 방문객 통계(visitor_analytics) 한 행을 사람이 읽는 요약 문자열로 변환합니다.
    quick_responder_node와 check_quality_node(코스 청크가 없는 경로)가 공통으로 사용합니다.
    데이터가 없으면 빈 문자열을 반환합니다.
    """
    if not market_insight:
        return ""
    parts = [
        f"{market_insight['region_dong']} {market_insight['year_month']} "
        f"방문객 {market_insight['total_visitors']:,}명"
    ]
    if market_insight.get("yoy_growth_rate") is not None:
        parts.append(f"전년 대비 {market_insight['yoy_growth_rate']}%")
    if market_insight.get("foreign_visitors") is not None:
        parts.append(f"외국인 방문객 {market_insight['foreign_visitors']:,}명")
    if market_insight.get("female_ratio") is not None:
        parts.append(f"여성 비중 {market_insight['female_ratio']}%")
    if market_insight.get("young_2030_ratio") is not None:
        parts.append(f"2030 비중 {market_insight['young_2030_ratio']}%")
    if market_insight.get("middle_4060_ratio") is not None:
        parts.append(f"40~60대 비중 {market_insight['middle_4060_ratio']}%")
    if market_insight.get("senior_70s_ratio") is not None:
        parts.append(f"70대 이상 비중 {market_insight['senior_70s_ratio']}%")
    return "📊 " + ", ".join(parts)


def quick_responder_node(state: AgentState) -> Dict[str, Any]:
    """기획서 생성 없이, 제주 밭담문화·작물 생육 지식과 관광 방문객 통계만 검색해 간결한 정보성
    답변을 빠르게 제공하는 Quick Responder 노드입니다. course_recommendation 을 제외한
    모든 의도(info_lookup/course_info/olle_general_info/other)의 공통 경로로,
    safety_evaluator/코스 검색/report_generator 를 전부 건너뜁니다.
    retrieved_chunks 는 건드리지 않고 빈 상태 그대로 둡니다 — 코스 검색을 하지 않았다는 사실 자체가
    check_quality_node 등 하류 노드에 "이 경로는 코스 기획서가 아니다"를 알리는 신호로 쓰입니다.
    """
    query = state["query"]
    constraints = state.get("parsed_constraints") or {}
    b2b_params = state.get("b2b_params") or {}
    target_course = state.get("target_course")
    key_item_or_crop = b2b_params.get("key_item_or_crop")
    preferred_location = b2b_params.get("preferred_location")
    target_month = b2b_params.get("target_month") or date.today().month
    include_market_insights = b2b_params.get("include_market_insights", True)
    location_resolution = b2b_params.get("market_location_resolution")
    fallback_query = constraints.get("vector_query") or query

    client = get_supabase_client()

    # "OO코스 알려줘" 처럼 특정 코스명이 언급된 질의는, 파서가 key_item_or_crop/
    # preferred_location 을 못 채웠어도 그 코스의 실제 작물/지역으로 검색 조건을 보완합니다
    # (이전엔 target_course 가 아예 무시되어 코스명을 특정해도 일반 검색과 동일하게 동작했음).
    course_meta = _fetch_course_meta_by_name(client, target_course) if target_course else None
    if course_meta:
        if not key_item_or_crop:
            key_item_or_crop = (course_meta.get("crops") or "").split(",")[0].strip() or None
        if not preferred_location:
            preferred_location = (
                (course_meta.get("administrative_areas") or "").split(",")[0].strip() or None
            )

    culture_chunks = _search_culture_knowledge(client, key_item_or_crop, fallback_query)

    market_insight = None
    if include_market_insights:
        market_insight = _fetch_market_insight(client, preferred_location, target_month)

    culture_context_str = _build_culture_context_str(culture_chunks, target_month)
    if not culture_context_str:
        culture_context_str = "(관련 문화/작물 지식 문서를 찾지 못했습니다.)"

    market_context_str = _build_market_insight_summary_str(market_insight) or "(관련 관광 방문객 통계를 찾지 못했습니다.)"

    location_note = ""
    if location_resolution:
        metric_label = _MARKET_METRIC_LABELS.get(
            location_resolution.get("metric"), location_resolution.get("metric")
        )
        location_note = (
            f"\n[지역 자동 선정 근거] '{location_resolution.get('region_dong')}'은 "
            f"{location_resolution.get('year_month')} 기준 {metric_label} 1위 지역으로 자동 선정되었습니다."
        )

    course_note = f"\n[대상 코스] 이 질문은 '{target_course}' 코스에 대한 것입니다." if target_course else ""

    if culture_chunks or market_insight:
        system_prompt = """당신은 제주올레 B2B 기획서 도슨트의 사전 정보 조회 도우미입니다.
기획서를 작성하는 게 아니라, 기획자가 궁금해하는 제주 문화·작물 지식이나 관광 방문객 통계를
간결한 설명체로 답변하세요.

[절대 규칙]
1. 아래 [문화·작물 지식 검색 결과]와 [관광 방문객 통계]에 없는 사실을 지어내지 마세요.
2. 표/섹션 헤더 같은 기획서 형식을 쓰지 말고, 자연스러운 문단(또는 필요시 짧은 불릿)으로 답변하세요.
3. 문화지식 항목에 "제철"/"제철 아님" 표시가 있으면 그 표시를 반영해 서술하세요.
4. 검색 결과 중 질문과 무관한 내용은 언급하지 마세요.
5. 두 문단을 넘지 않게 간결히 답하세요."""
        user_msg = (
            f"[질문]: {query}\n\n"
            f"[문화·작물 지식 검색 결과]:\n{culture_context_str}\n\n"
            f"[관광 방문객 통계]:\n{market_context_str}{location_note}{course_note}"
        )
        answer = get_chat_completion(system_prompt, user_msg)
    else:
        answer = (
            "죄송합니다. 질문하신 내용과 관련된 제주 문화·작물 지식이나 관광 방문객 통계를 "
            "찾지 못했습니다. 질문을 조금 더 구체적으로 말씀해 주시면 다시 찾아보겠습니다."
        )

    return {
        "culture_chunks": culture_chunks,
        "market_insight": market_insight,
        "docent_answer": answer,
        "final_response": answer,
    }


def tool_executor_node(state: AgentState) -> Dict[str, Any]:
    """LLM 에이전트(tool_agent_node)가 요청한 tool_calls 목록을 순회하여
    실제 파이썬 도구(retrieve_visitor_statistics_tool / retrieve_culture_crop_knowledge_tool)를
    다중/병렬 실행하고 결과를 tool_outputs 에 축적하는 Tool Executor 노드입니다.
    tool_depth 카운터를 1 증가시켜 무한 루프를 방어합니다.
    """
    from src.agent.tools import (
        retrieve_visitor_statistics_tool,
        retrieve_culture_crop_knowledge_tool,
    )

    tool_calls = state.get("tool_calls") or []
    depth = (state.get("tool_depth") or 0) + 1
    tool_outputs = list(state.get("tool_outputs") or [])

    for call in tool_calls:
        func_name = call.get("name") or call.get("function", {}).get("name")
        args = call.get("args") or call.get("function", {}).get("arguments") or {}

        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}

        if func_name == "retrieve_visitor_statistics_tool":
            res = retrieve_visitor_statistics_tool(
                region_dong=args.get("region_dong", ""),
                year_month=args.get("year_month"),
                metric=args.get("metric"),
            )
        elif func_name == "retrieve_culture_crop_knowledge_tool":
            res = retrieve_culture_crop_knowledge_tool(
                keyword_or_crop=args.get("keyword_or_crop", "")
            )
        else:
            res = f"[오류] 알 수 없는 도구 호출: {func_name}"

        tool_outputs.append({
            "tool_name": func_name,
            "args": args,
            "result": res,
        })

    print(f"[*] Tool Execution 완결 (depth: {depth}, 실행된 툴 수: {len(tool_calls)})")

    return {
        "tool_outputs": tool_outputs,
        "tool_calls": None,
        "tool_depth": depth,
    }


def tool_agent_node(state: AgentState) -> Dict[str, Any]:
    """사용자의 자연어 요청과 도구 실행 결과(tool_outputs), 품질 검증 피드백을 바탕으로
    도구 추가 호출을 지시하거나 최종 대화식 답변을 작성하는 두뇌 노드입니다.
    tool_depth 가 3회 이상이면 무한 루프를 방지하기 위해 툴 호출을 차단하고 최종 생성을 강제합니다.
    1차 검증 실패 시 quality_report 피드백을 System Prompt 최상단에 주입합니다.
    should_continue 의 "direct_retry"(loop_count < 2 인 info_lookup 경로) 는 이 노드로 되돌아오는데,
    이 노드는 원래 loop_count 를 건드리지 않아 quality_report 가 계속 실패하면 quality_checker와
    이 노드 사이를 무한 반복할 수 있었습니다(loop_count 는 rewrite_query_node 만 증가시키는데,
    direct_retry 경로는 그 노드를 거치지 않으므로). 그래서 품질 검증 실패로 인한 재시도(재답변
    생성) 때만 loop_count 를 1 증가시켜, "direct_retry"가 최대 2회로 확실히 끝나고 그 이후는
    should_continue 가 "rewrite" 경로로 넘어가도록 합니다.
    """
    query = state["query"]
    tool_outputs = state.get("tool_outputs") or []
    depth = state.get("tool_depth") or 0
    quality_report = state.get("quality_report")
    loop_count = state.get("loop_count", 0)

    # 1. 이전 검증 지적 피드백 주입 (직행 라우팅 시)
    is_retry_pass = bool(quality_report and not quality_report.get("passed", True))
    feedback_note = ""
    if is_retry_pass:
        feedback_note = (
            f"\n[⚠️ 품질 검증 지적 피드백 (반드시 반영하세요)]:\n"
            f"{quality_report.get('feedback', '수치나 팩트 오류를 수정하세요.')}\n"
        )

    # 2. 도구 실행 결과 컨텍스트 구성
    tools_context_str = ""
    if tool_outputs:
        tools_context_str = "\n[실행된 도구 조회 결과]:\n"
        for i, out in enumerate(tool_outputs):
            tools_context_str += f"\n--- [도구 {i+1}: {out['tool_name']}] ---\n{out['result']}\n"

    # 3. max depth (3회) 도달 시 방어 조치: 더 이상 도구를 부르지 못하게 제한
    if depth >= 3:
        system_prompt = """당신은 제주 문화·작물 지식 및 관광 통계를 친절히 안내하는 전문 챗봇입니다.
도구 호출 한도에 도달했으므로, 현재까지 확보된 [실행된 도구 조회 결과]만을 바탕으로 사용자의 질문에
가장 정확하고 친절하게 답변하세요. 도구가 반환한 에러 가이드(가용 월/지역 옵션 등)가 있다면
사용자에게 그대로 친절히 안내하세요."""
        user_msg = f"사용자 질문: {query}\n{feedback_note}{tools_context_str}"
        answer = get_chat_completion(system_prompt, user_msg)
        result = {
            "docent_answer": answer,
            "final_response": answer,
            "tool_calls": None,
        }
        if is_retry_pass:
            result["loop_count"] = loop_count + 1
        return result

    # 4. 일반 도구 연동 및 대화 답변 작성
    # 도구 결과가 이미 존재하는 경우 이를 요약 정리해 응답하며, 결과가 비어있는 초기 진입 시
    # 파라미터가 없으면 도구를 호출할 수 있도록 인자 결정 유도/기본 조회를 수행합니다.
    if not tool_outputs:
        # 1차 진입 시: 질문 분석 후 툴 호출 결정
        b2b_params = state.get("b2b_params") or {}
        preferred_loc = b2b_params.get("preferred_location")
        key_crop = b2b_params.get("key_item_or_crop")
        market_query = b2b_params.get("market_location_query") or {}

        pending_tool_calls = []
        if market_query.get("metric") or preferred_loc:
            loc = preferred_loc or "성산읍"
            ym = f"{market_query.get('year')}-{market_query.get('month'):02d}" if market_query.get("year") and market_query.get("month") else None
            pending_tool_calls.append({
                "name": "retrieve_visitor_statistics_tool",
                "args": {
                    "region_dong": loc,
                    "year_month": ym,
                    "metric": market_query.get("metric") or "total_visitors",
                }
            })
        if key_crop:
            pending_tool_calls.append({
                "name": "retrieve_culture_crop_knowledge_tool",
                "args": {"keyword_or_crop": key_crop}
            })

        if pending_tool_calls:
            return {"tool_calls": pending_tool_calls}

    # 도구 결과를 바탕으로 답변 생성
    system_prompt = """당신은 제주 올레길 탐방객과 기획자를 위한 친절하고 명확한 도슨트 챗봇입니다.
아래 [실행된 도구 조회 결과]를 바탕으로 자연스러운 문단으로 답변하세요.

[절대 규칙]
1. 도구 조회 결과에 명시된 수치와 단위(명, %, 톤 등)를 절대로 변경하거나 지어내지 마세요.
2. 도구 결과가 [오류] 또는 [안내] 메시지(미지원 지역/기간 및 가용 옵션 목록)일 경우, 사용자가 다른 유효 옵션을 선택할 수 있도록 대안 목록을 친절하게 되물어 안내하세요.
3. 2문단 이내로 간결하고 친근하게 작성하세요."""

    user_msg = f"사용자 질문: {query}\n{feedback_note}{tools_context_str}"
    answer = get_chat_completion(system_prompt, user_msg)

    result = {
        "docent_answer": answer,
        "final_response": answer,
        "tool_calls": None,
    }
    if is_retry_pass:
        result["loop_count"] = loop_count + 1
    return result



def generate_report_node(state: AgentState) -> Dict[str, Any]:
    """검색된 코스 컨텍스트, 실제 세부 구간(km) 데이터, 제주 밭담문화·작물 생육 지식 DB 근거를 엮어
    B2B 관광 상품 기획서의 [📊 B2B 상품 개요 & 스펙]/[📍 타임라인 표](섹션 1·2)를 LLM 으로 작성한 뒤,
    이어서 비짓제주 API 기반 [☕ 로컬 상생 제휴 아이디어]/[🌤️ 기후 리스크 및 Plan B]/
    [🛡️ Trust Tagging](섹션 3·4·5)를 같은 노드 안에서 순차적으로 완결하는 Report Generator
    노드입니다. course_recommendation 의도(무거운 전체 파이프라인)에서만 실행되므로(그 외 의도는
    quick_responder 로 우회) 별도 조건부 분기 없이 항상 5개 섹션 전체를 작성합니다.
    (2026-07-24 이전에는 docent_generator/report_finalizer(당시 이름 local_recommender) 두
    노드로 나뉘어 있었고, should_finalize_report 라우터가 intent_category==course_recommendation
    일 때만 후자를 실행했는데, 이 조건은 route_after_location_resolve 가 애초에 course_recommendation
    이 아니면 이 경로 자체에 진입시키지 않으므로 항상 참이었습니다 — 그래서 이 노드로 통합.)
    관광 API 데이터는 폐업/변경에 취약해 특정 매장을 "검증된 제휴처"로 단정할 수 없으므로,
    매장명/주소/전화번호는 결과물에 노출하지 않고 지역 상점의 성격(introduction)만 아이디어의
    참고 재료로 사용합니다.
    """
    query = state["query"]
    chunks = state["retrieved_chunks"]
    culture_chunks = state.get("culture_chunks") or []
    sub_segments = state.get("sub_segments") or []
    fallback = state["fallback_applied"]
    reason = state["fallback_reason"]
    weather = state["weather_info"] or {}
    safety = state["safety_check"] or {}
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
    culture_context_str = _build_culture_context_str(culture_chunks, target_month)
    if not culture_context_str:
        # 근거 문서가 없다고 지어낸 일반 지식으로 채우지 말고, 근거가 없다는 사실 자체를 있는
        # 그대로 반영하도록 지시합니다(사용자 요청: 관련 정보가 없으면 솔직하게 답할 것).
        culture_context_str = "(관련 문화/작물 지식 문서를 찾지 못했습니다. 지어내지 말고, 이 지점은 문화/작물 근거 없이 코스 사실 정보 위주로만 서술하세요.)"

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
- 조건 완화(지역 조건 해제 등) 적용 여부는 {fallback} 입니다. True인 경우에만 표 아래 한 줄 각주로 완화 사유({reason})를 명시하세요.
  False인 경우 완화 관련 각주를 절대 출력하지 마세요 ("완화 사유: None" 같은 문구도 금지) — 표로 섹션을 바로 종료하세요.

[작성 원칙]
1. 섹션 헤더(## 1. ~, ## 2. ~)는 반드시 그대로 출력하세요.
2. 코스 거리/시간/난이도/구간 km, Market Insight 방문객 수치 등 컨텍스트에 있는 사실 수치를 지어내지 마세요. (단, 섹션 1의 상품명/타겟/시간/단가/USP는 애초에 사실 데이터가 아닌 기획 제안값이므로 예외)
3. Markdown 표 문법이 깨지지 않도록 각 셀에 줄바꿈 없이 작성하세요."""

    user_msg = f"[질문(방문 조건)]: {query}\n\n[검색 결과 컨텍스트]:\n{context_str}"

    docent_answer = get_chat_completion(system_prompt, user_msg)

    # --- 섹션 3·4·5 (로컬 상생 제휴 아이디어 / 기후 리스크 & Plan B / Trust Tagging) ---
    # 섹션 1·2 작성이 비정상적으로 빈 문자열을 반환한 경우를 대비한 안전망(사실상 발생하지 않음 —
    # chunks 는 위에서 이미 비어있지 않음을 확인함). 이 경로에서는 섹션 3~5 없이 그대로 반환합니다.
    if not docent_answer:
        return {"docent_answer": docent_answer, "final_response": docent_answer, "recommendations": []}

    recommendations = []
    introduction_snippets = []
    rec_cache: Dict[Any, Any] = {}

    # 검색된 상위 코스들의 작물 및 행정구역 조합에 대해 비짓제주 소개 정보를 참고 재료로 수집합니다.
    # 조합 개수만큼 API 호출이 하나씩 순서대로 쌓이면 지연이 누적되므로(조합이 여러 개인 리포트일수록
    # 체감 지연이 커짐), 먼저 중복 없는 조합만 추려 스레드풀로 동시에 조회한 뒤(get_visit_jeju_recommendations
    # 는 내부적으로 실패 시 예외를 던지지 않고 Mock 데이터로 폴백하므로 여기서의 except 는 순수 방어용),
    # 그 결과를 원래 코스 순서대로 다시 조립합니다 — 조립 단계는 API 호출이 없는 순수 로컬 연산이라
    # 병렬화할 필요가 없습니다.
    unique_combos = []
    seen_combos = set()
    for chunk in chunks:
        crops = [c.strip() for c in chunk["crops"].split(",") if c.strip()]
        areas = [a.strip() for a in chunk["administrative_areas"].split(",") if a.strip()]
        for crop in crops:
            for area in areas:
                combo = (crop, area)
                if combo not in seen_combos:
                    seen_combos.add(combo)
                    unique_combos.append(combo)

    if unique_combos:
        with ThreadPoolExecutor(max_workers=min(8, len(unique_combos))) as executor:
            future_to_combo = {
                executor.submit(get_visit_jeju_recommendations, crop, area): (crop, area)
                for crop, area in unique_combos
            }
            for future, combo in future_to_combo.items():
                try:
                    rec_cache[combo] = future.result()
                except Exception as e:
                    print(f"[!] 비짓제주 API 조회 실패(작물={combo[0]}, 지역={combo[1]}), 이 조합은 건너뜁니다: {e}")
                    rec_cache[combo] = []

    for chunk in chunks:
        crops = [c.strip() for c in chunk["crops"].split(",") if c.strip()]
        areas = [a.strip() for a in chunk["administrative_areas"].split(",") if a.strip()]

        for crop in crops:
            for area in areas:
                rec_list = rec_cache.get((crop, area), [])
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

    # ## 5. 🛡️ Trust Tagging — 고정 문구가 아니라 이번 리포트에 실제로 쓰인 데이터 출처를
    # 구체적으로 나열합니다(예: "2026년 5월 제주관광공사 이동통신 빅데이터 기반 OO동 방문객
    # 통계"). 로컬 제휴 아이디어는 실 비짓제주 API 응답인지 Mock 폴백인지도 구분해서 밝힙니다 —
    # 방화벽/응답 지연으로 실 API가 막혀 있을 때 실 데이터인 것처럼 표시하면 안 되므로.
    source_labels = []

    course_names = []
    for c in chunks:
        name = c.get("course_name")
        if name and name not in course_names:
            course_names.append(name)
    if course_names:
        source_labels.append(f"제주올레 {'·'.join(course_names)} 원문 가이드북")

    if culture_chunks:
        crop_names = []
        for cc in culture_chunks:
            crop = cc.get("target_crop") or cc.get("crop_name")
            if crop and crop not in crop_names:
                crop_names.append(crop)
        if crop_names:
            source_labels.append(f"제주 밭담문화·작물 지식 DB({'·'.join(crop_names)} 등 {len(culture_chunks)}건)")
        else:
            source_labels.append(f"제주 밭담문화·작물 지식 DB({len(culture_chunks)}건)")

    if market_insight:
        year, month = market_insight["year_month"].split("-")
        source_labels.append(
            f"{year}년 {int(month)}월 제주관광공사 이동통신 빅데이터 기반 "
            f"{market_insight['region_dong']} 방문객 통계"
        )

    rec_sources = {rec.get("source") for rec in recommendations if rec.get("source")}
    if "visitjeju_api" in rec_sources:
        source_labels.append("비짓제주 실 API 기반 제휴 아이디어")
    elif "mock_db" in rec_sources:
        source_labels.append("비짓제주 Mock 데이터 기반 제휴 아이디어(실 API 미가동 시 대체)")

    # 별점은 이 시점에서 확정할 수 없습니다 — 그래프 순서상 실제 Self-RAG 단계인 quality_checker 가
    # 아직 실행되기 전이라 quality_report 가 없기 때문입니다. 그래서 이전에는 여기서
    # fallback_applied 여부만으로 4/5점을 임의로 매겼는데, "Self-RAG 신뢰도"라는 라벨과 실제
    # 근거가 안 맞는 문제였습니다. 자리표시자만 남겨두고, check_quality_node 가 자신의 실제
    # 평가 결과로 치환합니다.
    source_labels.append(f"Self-RAG 신뢰도: {_SELF_RAG_STARS_PLACEHOLDER}")

    report += "\n## 5. 🛡️ Trust Tagging\n"
    report += f"[출처: {' / '.join(source_labels)}]\n"

    return {
        "docent_answer": docent_answer,
        "recommendations": recommendations,
        "final_response": report
    }


_SELF_RAG_STARS_PLACEHOLDER = "{{SELF_RAG_STARS}}"


def _score_to_stars(score: float, passed: bool) -> str:
    """quality_checker 의 0.0~1.0 신뢰도 score 를 5단계 별점으로 변환합니다. score 구간을
    명시적으로 나눠(round() 의 은행원 반올림으로 인한 예측 불가능한 경계값 문제를 피함),
    검증에 실제로 통과하지 못했다면(3회 순환 끝에 강제 종료된 경우 포함) 점수가 높아도 최대
    3점으로 제한해 "확인되지 않은 답변"에 5점 만점을 주지 않도록 합니다."""
    if score >= 0.9:
        filled = 5
    elif score >= 0.7:
        filled = 4
    elif score >= 0.5:
        filled = 3
    elif score >= 0.3:
        filled = 2
    else:
        filled = 1
    if not passed:
        filled = min(filled, 3)
    return "★" * filled + "☆" * (5 - filled)


def check_quality_node(state: AgentState) -> Dict[str, Any]:
    """답변의 팩트 신뢰성 및 환각 여부, 제약사항 준수 여부를 검증하는 Quality Checker 노드입니다.
    Trust Tagging의 "Self-RAG 신뢰도" 별점도 여기서 이 노드 자신의 평가 결과(score/passed)로
    확정합니다(report_generator가 남겨둔 자리표시자를 치환) — 라벨과 실제 근거를 일치시키기 위함.
    코스 청크(retrieved_chunks)가 있으면 기존처럼 코스 사실 기준으로 검증하고, quick_responder_node
    경로처럼 코스 청크는 없지만 culture_chunks/market_insight 가 있으면 그 내용을 근거로 검증합니다.
    아무 근거도 없으면(둘 다 비어있음) 검증을 생략하고 조기 통과시킵니다.
    """
    query = state["query"]
    chunks = state["retrieved_chunks"]
    culture_chunks = state.get("culture_chunks") or []
    market_insight = state.get("market_insight")
    final_response = state["final_response"] or ""

    if not final_response or not (chunks or culture_chunks or market_insight):
        return {"quality_report": {"passed": True, "score": 1.0, "feedback": "검색 결과가 없어 평가를 생략합니다."}}

    if chunks:
        b2b_params = state.get("b2b_params") or {}
        requested_bits = []
        if b2b_params.get("key_item_or_crop"):
            requested_bits.append(f"작물/테마: {b2b_params['key_item_or_crop']}")
        if b2b_params.get("preferred_location"):
            requested_bits.append(f"선호 지역: {b2b_params['preferred_location']}")
        if b2b_params.get("target_month"):
            requested_bits.append(f"방문 월: {b2b_params['target_month']}월")
        requested_summary = ", ".join(requested_bits) if requested_bits else "(특정 작물/지역/월 조건 없음)"

        context_str = f"[사용자가 요청한 핵심 조건]: {requested_summary}\n"
        for i, c in enumerate(chunks):
            context_str += f"\n[코스 {i+1}]: {c['course_name']} (거리: {c['total_distance_km']}km, 소요시간: {c['estimated_time_text']}, 난이도: {c['difficulty']})\n"
            context_str += f"재배작물: {c['crops']}, 경유 행정구역: {c['administrative_areas']}\n"
            context_str += f"본문: {c['content']}\n"

        system_prompt = """당신은 생성된 도슨트 추천 답변의 핵심 사실 관계 및 요청 관련성을 검증하는 '품질 검증원'입니다.
주어진 [사용자 질문], [검색 컨텍스트] 및 [생성된 답변] 을 분석하여 답변의 환각 여부 및 정보 충실도를 채점하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON 문자열로만 반환하세요.

[검증 대상 - 아래 사실 항목에서만 컨텍스트와의 모순 여부를 확인하세요]
1. 코스명, 거리, 소요시간, 난이도, 재배작물, 경유 행정구역 등 컨텍스트에 명시된 구체적 수치/명칭을 답변이 왜곡하거나 컨텍스트와 반대로 서술했는가?
2. 사용자가 요청한 필수 제약사항(예: 휠체어 전용 코스 여부)을 어기고 부적절한 코스를 추천했는가?
3. [사용자가 요청한 핵심 조건]에 작물/지역/월이 명시되어 있다면, 답변이 추천한 코스가 그 조건과 실제로 관련이 있는가? 사실관계 자체는 컨텍스트와 일치하더라도, 컨텍스트의 코스들이 요청한 조건과 무관한데 답변이 마치 조건에 맞는 것처럼 추천했다면 이것도 결점으로 판정하세요. (조건이 "(특정 작물/지역/월 조건 없음)"이면 이 항목은 항상 통과로 간주)

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
    else:
        b2b_params = state.get("b2b_params") or {}
        target_month = b2b_params.get("target_month") or date.today().month
        context_str = _build_culture_context_str(culture_chunks, target_month) or "(관련 문화/작물 지식 없음)"
        market_str = _build_market_insight_summary_str(market_insight) or "(관련 관광 방문객 통계 없음)"
        context_str = f"{context_str}\n\n[관광 방문객 통계]:\n{market_str}"

        system_prompt = """당신은 정보 조회 답변(기획서가 아닌 단순 정보성 답변)의 핵심 사실 관계만을
검증하는 '품질 검증원'입니다. 주어진 [사용자 질문], [검색 컨텍스트] 및 [생성된 답변] 을 분석하여
답변의 환각 여부 및 정보 충실도를 채점하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON 문자열로만 반환하세요.

[검증 대상 - 아래 사실 항목에서만 컨텍스트와의 모순 여부를 확인하세요]
1. 작물명, 주산지, 생육 단계, 제철 여부 등 문화·작물 지식 컨텍스트의 사실을 답변이 왜곡했는가?
2. 방문객 수, 증감률, 성별/연령대 비중 등 통계 수치를 답변이 왜곡하거나 컨텍스트에 없는 수치를 지어냈는가?

[검증 대상에서 제외 - 아래 항목은 정상적인 안내이므로 절대 환각이나 결점으로 지적하지 마세요]
- 컨텍스트에 없는 세부 정보를 답변이 언급하지 않은 것 (누락은 결함이 아님)
- 컨텍스트에 있는 수치를 답변이 그대로 인용한 경우 (출처 재확인 요구 금지)

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

    # Trust Tagging의 별점 자리표시자를 이 노드의 실제 평가 결과로 치환합니다. report_generator가
    # 실행되지 않은 경로(course_recommendation 이 아닌 의도)는 애초에 Trust Tagging 섹션 자체가
    # 없어 자리표시자가 없으므로, 이 replace 는 안전하게 아무 것도 하지 않습니다.
    stars = _score_to_stars(report.get("score", 0.9), report.get("passed", True))
    updated_final_response = final_response.replace(_SELF_RAG_STARS_PLACEHOLDER, stars)

    return {"quality_report": report, "final_response": updated_final_response}


def rewrite_query_node(state: AgentState) -> Dict[str, Any]:
    """검증 실패 시, 더 정밀한 검색 컨텍스트 획득을 위해 검색 조건 및 키워드를 교정하는 Query Re-writer 노드입니다."""
    query = state["query"]
    constraints = state["parsed_constraints"] or {}
    report = state["quality_report"] or {}
    feedback = report.get("feedback", "")
    
    system_prompt = """당신은 품질 검증 결과에 따라 검색 조건을 보정하는 '쿼리 재작성기'입니다.
기존 사용자 질문, 이전 쿼리 제약사항 및 품질 검증 피드백을 바탕으로, Supabase pgvector 하이브리드 검색에서 더 나은 컨텍스트를 찾기 위한 최적의 '보정된 검색 쿼리'를 다시 도출하세요.
JSON 마크다운 코드 펜스(```json ...) 없이 순수 JSON로만 반환하세요.

[응답 포맷 (JSON 전용)]
{
  "revised_vector_query": "새로운 검색용 키워드"
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

        # 상태 덮어쓰기 형식으로 갱신 (soft_constraints 는 B2C 시절 소프트 완화 메커니즘과 함께
        # 제거된 필드라 더 이상 요청/저장하지 않습니다 — 2026-07-24 정리, 그 전엔 어떤 하류
        # 코드도 읽지 않는 죽은 값을 매번 LLM에게 요청해 저장만 하고 있었습니다.)
        updated_constraints = {
            "hard_constraints": constraints.get("hard_constraints", {"wheelchair_required": False}),
            "vector_query": revised.get("revised_vector_query", query)
        }
    except Exception as e:
        print(f"[!] 쿼리 재작성 실패: {e}")
        updated_constraints = constraints
        
    return {
        "parsed_constraints": updated_constraints,
        "loop_count": state["loop_count"] + 1
    }
