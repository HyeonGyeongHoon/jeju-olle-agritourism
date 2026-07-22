import json

from src.agent.llm_client import get_chat_completion
from src.models.schema import IntentCategory, RouterResult

SYSTEM_PROMPT = """당신은 제주올레 탐방객의 자연어 질문을 분석하여 의도를 4가지 카테고리 중 하나로 정확히 분류하는 사전 라우터입니다.

[카테고리 분기 지침]
1. "course_info": 특정 올레길 코스의 구체적인 메타데이터(거리, 난이도, 소요시간, 시작/종점, 스탬프 위치 등)에 대한 질문
   (예: "1코스 길이나 소요시간이 어떻게 돼?", "7코스 시작점이 어디야?", "10-1코스 난이도 알려줘")
2. "course_recommendation": 탐방객의 신체/시간/거리/취향 등 요구 조건에 맞는 코스 추천 질문
   (예: "휠체어로 갈 수 있는 코스 추천해줘", "2시간 이내에 걸을 수 있는 쉬운 코스", "바다가 예쁜 올레길 알려줘")
3. "olle_general_info": 제주올레길 전반의 준비물, 패스포트/스탬프 구매, 안전 수칙, 이용 방법 안내 질문
   (예: "올레길 준비물 뭐가 있어?", "패스포트는 어디서 사나요?", "여름 올레길 걸을 때 주의사항")
4. "other": 제주 올레길 탐방 정보와 직접적 관련이 없는 질문
   (예: "오늘 서울 날씨 알려줘", "제주도 맛집 추천해줘", "안녕")

[응답 포맷 (JSON 전용)]
{
  "category": "course_info" | "course_recommendation" | "olle_general_info" | "other",
  "target_course": "1코스" 또는 null,
  "reason": "의도 분류 사유"
}"""


def _strip_markdown_code_fence(text: str) -> str:
    """LLM 응답이 ```json ... ``` 코드 블록으로 감싸져 있는 경우 이를 제거합니다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        if stripped.endswith("```"):
            stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def route_intent(query: str) -> RouterResult:
    """사용자 질의의 사전 의도 카테고리를 분류합니다.
    실패 시 기본적으로 코스 추천(COURSE_RECOMMENDATION) 분기로 폴백합니다.
    """
    try:
        raw_response = get_chat_completion(SYSTEM_PROMPT, query)
        cleaned_response = _strip_markdown_code_fence(raw_response)
        data = json.loads(cleaned_response)

        return RouterResult(
            category=IntentCategory(data["category"]),
            target_course=data.get("target_course"),
            reason=data.get("reason", ""),
        )
    except Exception as e:
        return RouterResult(
            category=IntentCategory.COURSE_RECOMMENDATION,
            target_course=None,
            reason=f"라우터 예외 발생으로 기본 추천 파이프라인으로 전환되었습니다: {e}",
        )
