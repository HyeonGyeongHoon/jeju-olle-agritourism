import json

from src.agent.llm_client import get_chat_completion
from src.models.schema import IntentCategory, RouterResult

SYSTEM_PROMPT = """당신은 제주올레 영농-관광 B2B 기획서 도슨트에 들어온 자연어 질의를 분석하여 의도를 5가지 카테고리 중 하나로 정확히 분류하는 사전 라우터입니다.

[카테고리 분기 지침]
1. "course_info": 특정 올레길 코스의 구체적인 메타데이터(거리, 난이도, 소요시간, 시작/종점, 스탬프 위치 등)에 대한 질문
   (예: "1코스 길이나 소요시간이 어떻게 돼?", "7코스 시작점이 어디야?", "10-1코스 난이도 알려줘")
2. "course_recommendation": 방문 시기/매개 작물·테마/지역/제약 조건에 맞는 코스 기반 B2B 상품 기획서 "생성"을 요청하는 질문
   (예: "10월 감귤 테마로 구좌읍 코스 기획서 만들어줘", "휠체어 이용객도 참여 가능한 코스로 상품 기획해줘", "밭담문화를 살린 동부 코스 기획안 필요해")
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
