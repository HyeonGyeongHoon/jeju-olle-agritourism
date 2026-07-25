import json
import re

from src.agent.llm_client import get_chat_completion
from src.models.schema import IntentCategory, RouterResult

# --- 1단계: 규칙 기반 사전 필터 (LLM 호출 없이 즉시 분류) ---
# 단순 인사말/짧은 단답형 발화나 부적절한 표현, 그리고 "기획서 생성"이 아니라 단순 코스
# "추천"만 요청하는 질의(막연한 추천이면 거절, 특정 코스를 지목한 의견 문의면
# course_info)는 LLM에게 물어볼 필요 없이(비용/지연 절감) 또는 LLM 분류가 흔들리는 경계
# 사례이므로, 규칙으로 먼저 확정합니다. 여기서 걸러지지 않은 나머지 비정형 자연어만
# 2단계 LLM 문맥 분류로 넘어갑니다.

_GREETING_KEYWORDS = ("안녕", "반가워", "하이", "hi", "hello", "고마워", "감사", "수고", "잘가", "바이")

# 최소한의 대표 예시일 뿐 완전한 비속어 목록이 아닙니다 — 실제 운영 전환 시 전문 콘텐츠
# 모더레이션 API/라이브러리로 교체하는 것을 권장합니다.
_PROFANITY_KEYWORDS = ("바보", "멍청이", "미친", "씨발", "개새끼", "병신")

# course_recommendation 은 "기획서/기획안"처럼 결과물 생성을 요청하는 질의여야 합니다.
# 아래 키워드가 하나도 없이 "코스"/"올레" + "추천"만 있으면, 기획서가 아니라 단순 코스
# 추천을 요청하는 것으로 간주합니다(이 서비스는 코스 추천 자체는 제공하지 않음).
_PROPOSAL_KEYWORDS = ("기획서", "기획안", "기획해", "상품 기획", "상품화")
_COURSE_REFERENCE_KEYWORDS = ("코스", "올레")

# 위 거절 규칙의 예외 — 이미 특정 코스를 지목한 상태에서 그 코스에 대한 의견/적합성을
# 묻는 질의(예: "1코스 괜찮을지 추천해줄래?")는 "아무 코스나 추천해달라"는 요청과 성격이
# 다르며, 오히려 course_info 입니다. 이 부류는 거절하지도 기획서 파이프라인으로 보내지도
# 않고 규칙으로 course_info 로 확정합니다(2026-07-25 추가 — 1차로는 "규칙에서 제외해
# LLM에게 넘기는" 방식이었지만, 라이브 QA에서 LLM이 "추천"이라는 단어에 끌려 3회 모두
# course_recommendation 으로 분류해 의견 질문에 5섹션 기획서를 생성하는 문제가
# 결정론적으로 재현되어, SYSTEM_PROMPT 보완과 함께 규칙으로도 확정하게 했습니다).
# 실제 course_name 값은 전부 "1코스"/"1-1코스"/"3-A코스"/"10-1코스" 형태(숫자 + 선택적
# 하이픈+숫자/영문자 + "코스")이므로, 특정 코스명을 하드코딩하지 않고 이 표기 패턴으로
# 판별합니다. "1번 코스"/"10-1 코스"처럼 공백이나 "번"이 끼는 구어 표기도 허용합니다.
_SPECIFIC_COURSE_PATTERN = re.compile(r"\d+\s*(?:-\s*(?:\d+|[A-Za-z]))?\s*번?\s*코스")

# 단, 코스명이 "제외 대상"으로만 언급된 경우(예: "1코스 말고 다른 코스 추천해줘")는 특정
# 코스를 지목한 게 아니라 여전히 막연한 추천 요청이므로 예외로 인정하지 않습니다. 코스명
# 바로 뒤 짧은 구간에 아래 배제 표현이 붙어 있는지로 판별합니다.
_COURSE_EXCLUSION_MARKERS = (
    "말고", "빼고", "외에", "이외", "제외", "아닌", "아니고", "대신",
)
_COURSE_EXCLUSION_LOOKAHEAD = 8

# 규칙 기반 1단계가 확정한 분류의 사유 문구. 대부분은 서비스 범위 밖(OTHER) 판정이지만
# specific_course_opinion 은 범위 밖이 아니라 course_info 확정 사유입니다.
_RULE_BASED_REASON = {
    "greeting": "규칙 기반 1단계: 단순 인사말/짧은 응대 발화",
    "profanity": "규칙 기반 1단계: 부적절한 표현 감지",
    "plain_course_recommendation": "규칙 기반 1단계: 기획서 생성 요청이 아닌 단순 코스 추천 요청",
    "specific_course_opinion": (
        "규칙 기반 1단계: 이미 지목한 특정 코스에 대한 의견/적합성 문의(코스 정보 질의)"
    ),
}


def _normalize_course_name(raw: str) -> str:
    """정규식이 잡아낸 코스 표기를 DB courses.course_name 형식으로 정규화합니다
    ("1번 코스"/"10-1 코스"/"3-a코스" → "1코스"/"10-1코스"/"3-A코스"). 다운스트림
    _filter_course_ids_by_target_course 가 course_name 과 완전 일치(==) 비교를 하므로
    이 정규화가 없으면 구어 표기가 매칭되지 않습니다.
    """
    return re.sub(r"[\s번]", "", raw).upper()


def _extract_specific_course(query: str) -> str | None:
    """질의가 지목한 특정 코스명을 정규화해 반환합니다(없으면 None). 배제 표현이
    뒤따르는 언급(예: "1코스 말고")은 지목으로 보지 않습니다. LLM 이 target_course 를
    null 로 비워 반환하는 경우가 라이브에서 관측되어(같은 질의를 3회 반복해도 재현),
    이 값을 결정론적 폴백으로도 사용합니다.
    """
    for match in _SPECIFIC_COURSE_PATTERN.finditer(query):
        following = query[match.end() : match.end() + _COURSE_EXCLUSION_LOOKAHEAD]
        if any(marker in following for marker in _COURSE_EXCLUSION_MARKERS):
            continue
        return _normalize_course_name(match.group())
    return None


def _rule_based_precheck(query: str) -> RouterResult | None:
    """LLM 호출 없이 규칙만으로 확정 분류할 수 있는 질의를 즉시 처리합니다. 매칭되면
    RouterResult 를, 아니면 None 을 반환해 2단계 LLM 문맥 분류로 넘어가게 합니다.
    (2026-07-25 추가 — 계기: "당근 코스 추천해줘" 같은 질의가 LLM 분류에서 실행마다
    course_recommendation/other 사이를 오가는 비결정적 동작이 실사용 중 관측되어, 이 경계
    사례만큼은 규칙으로 확정해야 했음.)
    """
    stripped = query.strip()
    if not stripped:
        return None

    # 1) 순수 코스 추천 요청(기획서 생성 요청 아님) — 사용자 명시 정책: 코스 추천은
    # 서비스 범위 밖이므로, LLM 분류 노이즈와 무관하게 결정론적으로 처리합니다.
    has_course_ref = any(kw in stripped for kw in _COURSE_REFERENCE_KEYWORDS)
    has_recommend_ask = "추천" in stripped
    has_proposal_ask = any(kw in stripped for kw in _PROPOSAL_KEYWORDS)
    # 특정 코스를 이미 지목한 질의는 "막연한 코스 추천 요청"이 아니므로 거절하지
    # 않습니다(아래 3번에서 course_info 로 확정).
    specific_course = _extract_specific_course(stripped)
    is_plain_course_recommendation = (
        has_course_ref
        and has_recommend_ask
        and not has_proposal_ask
        and specific_course is None
    )
    if is_plain_course_recommendation:
        return RouterResult(
            category=IntentCategory.OTHER,
            target_course=None,
            reason=_RULE_BASED_REASON["plain_course_recommendation"],
        )

    # 2) 부적절한 표현
    if any(p in stripped for p in _PROFANITY_KEYWORDS):
        return RouterResult(
            category=IntentCategory.OTHER, target_course=None, reason=_RULE_BASED_REASON["profanity"]
        )

    # 3) 이미 지목한 특정 코스에 대한 의견/적합성 문의("N코스 괜찮을지 추천해줄래?") —
    # 결과물 생성 요청이 아니므로 기획서 파이프라인이 아니라 course_info 로 확정합니다.
    # LLM 은 이 부류를 "추천"이라는 단어 때문에 course_recommendation 으로 오분류하는
    # 것이 라이브에서 결정론적으로 재현되었으므로(3회 반복 동일), 여기서 확정합니다.
    # target_course 도 정규식으로 이미 알고 있으니 함께 채워 다운스트림
    # (_fetch_course_meta_by_name / _filter_course_ids_by_target_course)이 쓰게 합니다.
    if specific_course and has_recommend_ask and not has_proposal_ask:
        return RouterResult(
            category=IntentCategory.COURSE_INFO,
            target_course=specific_course,
            reason=_RULE_BASED_REASON["specific_course_opinion"],
        )

    # 4) 짧은 인사말/응대성 발화 (실질적인 질문 내용이 없는 1~2단어 수준)
    word_count = len(stripped.split())
    if word_count <= 2 and any(g in stripped for g in _GREETING_KEYWORDS):
        return RouterResult(
            category=IntentCategory.OTHER, target_course=None, reason=_RULE_BASED_REASON["greeting"]
        )

    return None


SYSTEM_PROMPT = """당신은 제주올레 영농-관광 B2B 기획서 도슨트에 들어온 자연어 질의를 분석하여 의도를 5가지 카테고리 중 하나로 정확히 분류하는 사전 라우터입니다.

[카테고리 분기 지침]
1. "course_info": 질의에 특정 올레길 코스("1코스", "10-1코스", "3-A코스" 등)가 이미 지목되어 있고,
   그 코스에 대해 묻는 질문. 다음 두 종류 모두 포함합니다.
   1-a) 구체적인 메타데이터(거리, 난이도, 소요시간, 시작/종점, 스탬프 위치 등) 조회
        (예: "1코스 길이나 소요시간이 어떻게 돼?", "7코스 시작점이 어디야?", "10-1코스 난이도 알려줘")
   1-b) 지목된 그 코스에 대한 의견·평가·적합성 문의. **질의에 "추천"이라는 단어가 들어 있어도,
        새로 코스를 골라달라는 요청이 아니라 이미 지목한 그 코스가 괜찮은지/특정 대상에게
        적합한지를 묻는 것이면 2번이 아니라 이 카테고리입니다.**
        (예: "1코스 괜찮을지 추천해줄래?", "1코스 가볼 만한지 추천 좀 해줘",
         "7코스 초보자한테 추천할 만해?", "9코스 아이랑 가도 될까?", "12코스 어때?")
2. "course_recommendation": 방문 시기/매개 작물·테마/지역/제약 조건에 맞는 코스 기반 B2B 상품 기획서 "생성"을 요청하는 질문
   (예: "10월 감귤 테마로 구좌읍 코스 기획서 만들어줘", "휠체어 이용객도 참여 가능한 코스로 상품 기획해줘", "밭담문화를 살린 동부 코스 기획안 필요해")
   판별 기준: "기획서/기획안/상품화"처럼 **결과물을 만들어달라는 요청**이 있어야 합니다.
   특정 코스가 지목되어 있어도 결과물 생성 요청이면 2번이고(예: "1코스로 기획서 만들어줘"),
   결과물 생성 요청 없이 그 코스에 대한 의견만 묻는다면 1번입니다.
3. "olle_general_info": 제주올레길 전반의 준비물, 패스포트/스탬프 운영, 안전 수칙 등 기획서 부속 안내자료에 참고할 일반 정보 질문
   (예: "올레길 준비물 뭐가 있어?", "패스포트/스탬프는 어떻게 운영되나요?", "여름철 올레길 안전 수칙 알려줘")
4. "other": 제주 올레길 영농-관광 상품 기획과 직접적 관련이 없는 질문
   (예: "오늘 서울 날씨 알려줘", "안녕")
5. "info_lookup": 기획서/상품 "생성"을 요청하는 게 아니라, 기획서 작성에 참고하기 위해 제주 문화·작물 지식이나
   관광 방문객 통계 "정보 자체"를 가볍게 물어보는 질문. 코스 기획안이나 상품화 결과물을 만들어달라는 요청이면
   2번(course_recommendation)이고, 단순히 알고 싶은 질문이면 이 카테고리입니다.
   (예: "제주 밭담문화가 뭐야?", "감귤 수확 시기가 언제야?", "요즘 구좌읍 외국인 방문객 통계 어때?",
   "2030 방문객 비중이 높은 동네가 어디야?", "마늘 파종 시기 알려줘")

[응답 포맷 (JSON 전용)]
{
  "category": "course_info" | "course_recommendation" | "olle_general_info" | "other" | "info_lookup",
  "target_course": "1코스" 또는 null,
  "reason": "의도 분류 사유"
}

target_course 는 질의에 코스가 지목되어 있으면 카테고리와 무관하게 반드시 채우고
("1코스"/"10-1코스"/"3-A코스"처럼 DB 표기 형식으로), 지목된 코스가 없을 때만 null 로 두세요."""


def _strip_markdown_code_fence(text: str) -> str:
    """LLM 응답이 ```json ... ``` 코드 블록으로 감싸져 있는 경우 이를 제거합니다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        if stripped.endswith("```"):
            stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def route_intent(query: str) -> RouterResult:
    """사용자 질의의 사전 의도 카테고리를 분류합니다. 1단계로 규칙 기반 사전 필터
    (_rule_based_precheck)를 먼저 거쳐, 단순 인사말/부적절한 표현/기획서 요청이 아닌 단순
    코스 추천처럼 확정적으로 판정 가능한 질의는 LLM 호출 없이 즉시 분류합니다. 여기서
    걸러지지 않은 나머지 비정형 자연어만 2단계 LLM 문맥 분류로 넘어갑니다.
    LLM 분류 실패 시 기본적으로 코스 추천(COURSE_RECOMMENDATION) 분기로 폴백합니다.
    LLM 이 target_course 를 비워 반환하거나(라이브에서 관측됨) 예외로 실패해도, 질의에
    "N코스" 표기가 있으면 정규식으로 뽑은 코스명을 폴백으로 채워 다운스트림 노드가
    코스를 특정할 수 있게 합니다.
    """
    precheck_result = _rule_based_precheck(query)
    if precheck_result is not None:
        return precheck_result

    fallback_course = _extract_specific_course(query)

    try:
        raw_response = get_chat_completion(SYSTEM_PROMPT, query)
        cleaned_response = _strip_markdown_code_fence(raw_response)
        data = json.loads(cleaned_response)

        return RouterResult(
            category=IntentCategory(data["category"]),
            target_course=data.get("target_course") or fallback_course,
            reason=data.get("reason", ""),
        )
    except Exception as e:
        return RouterResult(
            category=IntentCategory.COURSE_RECOMMENDATION,
            target_course=fallback_course,
            reason=f"라우터 예외 발생으로 기본 추천 파이프라인으로 전환되었습니다: {e}",
        )
