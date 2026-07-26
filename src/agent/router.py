import json
import re

from src.agent.llm_client import get_chat_completion
from src.agent.prompts.loader import load_prompt
from src.models.schema import IntentCategory, RouterResult

# --- 1단계: 규칙 기반 사전 필터 (LLM 호출 없이 즉시 분류) ---
# 단순 인사말/짧은 단답형 발화나 부적절한 표현, 그리고 "기획서 생성"이 아니라 단순 코스
# "추천"만 요청하는 질의(막연한 추천이면 거절, 특정 코스를 지목한 의견 문의면
# info_lookup)는 LLM에게 물어볼 필요 없이(비용/지연 절감) 또는 LLM 분류가 흔들리는 경계
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
# 다르며, 오히려 info_lookup 입니다. 이 부류는 거절하지도 기획서 파이프라인으로 보내지도
# 않고 규칙으로 info_lookup 로 확정합니다(2026-07-25 추가 — 1차로는 "규칙에서 제외해
# LLM에게 넘기는" 방식이었지만, 라이브 QA에서 LLM이 "추천"이라는 단어에 끌려 3회 모두
# course_recommendation 으로 분류해 의견 질문에 5섹션 기획서를 생성하는 문제가
# 결정론적으로 재현되어, SYSTEM_PROMPT 보완과 함께 규칙으로도 확정하게 했습니다. 2026-07-26:
# 사전 분류 카테고리를 course_recommendation/other/info_lookup 3개로 통합하며 이 규칙이
# 반환하던 옛 course_info 값도 info_lookup 으로 흡수했습니다 — 다운스트림은 애초에
# OTHER/COURSE_RECOMMENDATION 만 구분했으므로 동작은 동일합니다).
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
# specific_course_opinion 은 범위 밖이 아니라 info_lookup 확정 사유입니다.
_RULE_BASED_REASON = {
    "greeting": "규칙 기반 1단계: 단순 인사말/짧은 응대 발화",
    "profanity": "규칙 기반 1단계: 부적절한 표현 감지",
    "plain_course_recommendation": "규칙 기반 1단계: 기획서 생성 요청이 아닌 단순 코스 추천 요청",
    "specific_course_opinion": (
        "규칙 기반 1단계: 이미 지목한 특정 코스에 대한 의견/적합성 문의(코스 정보 질의)"
    ),
    "proposal_without_recommend_word": (
        "규칙 기반 1단계: '추천' 토큰 없이 코스/올레 + 기획서·기획안 등 결과물 생성 요청이 "
        "확인됨"
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
    # 않습니다(아래 3번에서 info_lookup 로 확정).
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

    # 1-b) 결과물 생성 요청(기획서/기획안/기획해/상품 기획/상품화)이 "추천" 토큰 없이
    # "코스"/"올레" 언급과 함께 있는 질의("당근 코스 기획서 써줘")도, 위 1)이 다루는
    # "추천은 있고 기획서는 없는" 조합과 대칭되는 또 다른 경계 사례입니다. 라이브 QA에서
    # 이 조합 역시 LLM 2단계 분류가 course_recommendation/other 사이를 오가는 비결정적
    # 동작을 보였습니다(동일 질의 3회 반복 중 2회 other로 오분류) — CLAUDE.md에 이미
    # 문서화된 "코스+추천" 비결정성과 같은 원인 계열의 별개 관측입니다.
    # "추천"까지 함께 있는 조합(has_recommend_ask=True)은 이 규칙 대상이 아니라 위의
    # is_plain_course_recommendation 처리 이후 그대로 LLM 2단계로 넘어갑니다 — 그 조합은
    # 지금까지 비결정성이 보고된 적이 없고 LLM이 안정적으로 course_recommendation을
    # 반환해 왔으므로(test_route_intent_does_not_decline_when_proposal_keyword_present),
    # 불필요하게 규칙 범위를 넓히지 않습니다.
    # 특정 코스가 이미 지목된 경우("1코스로 기획서 만들어줘")도 이 규칙 범위에서 제외합니다
    # — 그 조합 역시 지금까지 LLM이 안정적으로 course_recommendation을 반환해 왔고
    # (test_route_intent_does_not_decline_proposal_for_specific_course), 이번에 보고된
    # 비결정성 사례는 특정 코스를 지목하지 않은 질의에 한정되어 있어 규칙 범위를 그만큼만
    # 좁혀 불필요한 동작 변경을 피합니다.
    if has_course_ref and has_proposal_ask and not has_recommend_ask and specific_course is None:
        return RouterResult(
            category=IntentCategory.COURSE_RECOMMENDATION,
            target_course=None,
            reason=_RULE_BASED_REASON["proposal_without_recommend_word"],
        )

    # 2) 부적절한 표현
    if any(p in stripped for p in _PROFANITY_KEYWORDS):
        return RouterResult(
            category=IntentCategory.OTHER, target_course=None, reason=_RULE_BASED_REASON["profanity"]
        )

    # 3) 이미 지목한 특정 코스에 대한 의견/적합성 문의("N코스 괜찮을지 추천해줄래?") —
    # 결과물 생성 요청이 아니므로 기획서 파이프라인이 아니라 info_lookup 로 확정합니다.
    # LLM 은 이 부류를 "추천"이라는 단어 때문에 course_recommendation 으로 오분류하는
    # 것이 라이브에서 결정론적으로 재현되었으므로(3회 반복 동일), 여기서 확정합니다.
    # target_course 는 카테고리와 무관하게 코스가 지목되면 채운다는 원칙에 따라, 정규식으로
    # 이미 알고 있으니 함께 채워 다운스트림(_fetch_course_meta_by_name /
    # _filter_course_ids_by_target_course)이 쓰게 합니다.
    if specific_course and has_recommend_ask and not has_proposal_ask:
        return RouterResult(
            category=IntentCategory.INFO_LOOKUP,
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
        raw_response = get_chat_completion(load_prompt("router.md"), query)
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
