import os
# pyrefly: ignore [missing-import]
from langchain_openai import ChatOpenAI

DEFAULT_SOLAR_CHAT_MODEL = "solar-pro2"


def get_chat_completion(
    system_prompt: str, user_message: str, model: str = DEFAULT_SOLAR_CHAT_MODEL
) -> str:
    """LangChain ChatOpenAI 인터페이스를 사용하여 Upstage Solar API를 호출합니다.

    LangChain 의 invoke 를 타게 됨으로써 LangSmith 에 자동으로 LLM 호출이 트레이싱되며,
    입/출력 토큰 개수가 누락 없이 정상적으로 수집되어 대시보드에 표시됩니다.
    기본적으로 max_retries 를 통한 지수 백오프 재시도 및 타임아웃 처리가 내장되어 있습니다.
    """
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        raise ValueError("UPSTAGE_API_KEY 환경 변수가 설정되지 않았습니다.")

    llm = ChatOpenAI(
        openai_api_key=api_key,
        openai_api_base="https://api.upstage.ai/v1",
        model_name=model,
        max_retries=5,
        timeout=30,
        temperature=0,
    )

    messages = [
        ("system", system_prompt),
        ("user", user_message),
    ]

    response = llm.invoke(messages)
    return response.content

