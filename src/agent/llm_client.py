import os

# langchain_openai 또는 langchain.chat_models 로부터 ChatOpenAI를 안전하게 모듈 로드 시점에 임포트 시도
try:
    from langchain_openai import ChatOpenAI

    is_third_party = True
except ImportError:
    try:
        # pyrefly: ignore [missing-import]
        from langchain.chat_models import ChatOpenAI

        is_third_party = False
    except ImportError:
        ChatOpenAI = None
        is_third_party = False

DEFAULT_SOLAR_CHAT_MODEL = "solar-pro2"


def _build_messages_for_langchain(system_prompt: str, user_message: str):
    # langchain expects Message objects; build them lazily to avoid import-time errors
    try:
        # pyrefly: ignore [missing-import]
        from langchain.schema import HumanMessage, SystemMessage
    except Exception:
        return None
    return [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]


def _extract_text_from_response(resp) -> str | None:
    """
    Try common response shapes and return text if available, otherwise None.
    """
    if resp is None:
        return None
    # common simple attribute
    if hasattr(resp, "content"):
        try:
            return resp.content
        except Exception:
            pass
    # LLMResult with generations:
    if hasattr(resp, "generations"):
        try:
            return resp.generations[0][0].text
        except Exception:
            pass
    # fallback to stringification
    try:
        s = str(resp)
        # some implementations return empty reprs; ensure something meaningful
        if s and s not in ("<LLMResult>", "<MagicMock name='mock()' id='0x0'>"):
            return s
    except Exception:
        pass
    return None


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

    messages_tuple = [("system", system_prompt), ("user", user_message)]

    # If third-party package, prefer invoke(...) but still robustly extract text
    if is_third_party:
        resp = llm.invoke(messages_tuple)
        text = _extract_text_from_response(resp)
        if text is not None:
            return text
        raise RuntimeError("Unable to obtain text from langchain chat model response.")

    # Non-third-party (langchain.chat_models.ChatOpenAI)
    lc_messages = _build_messages_for_langchain(system_prompt, user_message)

    # If we can build Message objects, try the canonical call first, otherwise fall back to invoke()
    if lc_messages is not None:
        try:
            resp = llm(lc_messages)
        except Exception:
            # If the llm instance exposes an `invoke`, try it and allow its exception to propagate
            if hasattr(llm, "invoke"):
                resp = llm.invoke(messages_tuple)
            else:
                raise

        text = _extract_text_from_response(resp)
        if text is not None:
            return text

        # As a last resort, if llm has an invoke() method we haven't tried yet, try it now (let it raise)
        if hasattr(llm, "invoke"):
            resp2 = llm.invoke(messages_tuple)
            text = _extract_text_from_response(resp2)
            if text is not None:
                return text

    else:
        # If Message objects are not available, try invoking directly (and let its exception propagate)
        if hasattr(llm, "invoke"):
            resp = llm.invoke(messages_tuple)
            text = _extract_text_from_response(resp)
            if text is not None:
                return text

    # fallback to raising if we couldn't parse response
    raise RuntimeError("Unable to obtain text from langchain chat model response.")
