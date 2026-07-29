import os
from typing import List, Tuple, Optional

# langchain_openai 또는 langchain.chat_models 로부터 ChatOpenAI를 안전하게 모듈 로드 시점에 임포트 시도
try:
    from langchain_openai import ChatOpenAI
    is_third_party = True
except ImportError:
    try:
        from langchain.chat_models import ChatOpenAI
        is_third_party = False
    except ImportError:
        ChatOpenAI = None
        is_third_party = False

DEFAULT_SOLAR_CHAT_MODEL = "solar-pro2"


def _build_messages_for_langchain(system_prompt: str, user_message: str):
    # langchain expects Message objects; build them lazily to avoid import-time errors
    try:
        from langchain.schema import SystemMessage, HumanMessage
    except Exception:
        return None
    return [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]


def get_chat_completion(
    system_prompt: str, user_message: str, model: str = DEFAULT_SOLAR_CHAT_MODEL
) -> str:
    """
    Call an available ChatOpenAI class to obtain a chat completion.
    Tries the original `langchain_openai` package first,
    then falls back to LangChain's `langchain.chat_models.ChatOpenAI` if available.

    Raises a helpful error if neither provider is installed.
    """
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        raise ValueError("UPSTAGE_API_KEY environment variable is not set.")

    if ChatOpenAI is None:
        raise ModuleNotFoundError(
            "Neither 'langchain_openai' nor 'langchain' (with chat_models) is installed. "
            "Install one of them, e.g. `pip install langchain-openai` or `pip install langchain`."
        )

    llm = ChatOpenAI(
        openai_api_key=api_key,
        openai_api_base="https://api.upstage.ai/v1",
        model_name=model,
        max_retries=5,
        timeout=30,
        temperature=0,
    )

    if is_third_party:
        messages = [("system", system_prompt), ("user", user_message)]
        response = llm.invoke(messages)
        return response.content
    else:
        # Try canonical langchain __call__ method using Message objects
        lc_messages = _build_messages_for_langchain(system_prompt, user_message)
        if lc_messages is not None:
            resp = llm(lc_messages)
            # Try common response shapes:
            if hasattr(resp, "content"):
                return resp.content
            # LLMResult with generations:
            if hasattr(resp, "generations"):
                try:
                    return resp.generations[0][0].text
                except Exception:
                    pass
            # If result is an AIMessage object
            try:
                return str(resp)
            except Exception:
                pass

        # fallback to raising if we couldn't parse response
        raise RuntimeError("Unable to obtain text from langchain chat model response.")
