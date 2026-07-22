import os
import time

import requests

UPSTAGE_CHAT_COMPLETIONS_URL = "https://api.upstage.ai/v1/chat/completions"
DEFAULT_SOLAR_CHAT_MODEL = "solar-pro2"


def get_chat_completion(
    system_prompt: str, user_message: str, model: str = DEFAULT_SOLAR_CHAT_MODEL
) -> str:
    """Upstage Solar Chat Completions API를 호출하여 답변 텍스트를 반환합니다.

    HTTP 429 지수 백오프 재시도가 탑재되어 있습니다.
    """
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        raise ValueError("UPSTAGE_API_KEY 환경 변수가 설정되지 않았습니다.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.post(
                UPSTAGE_CHAT_COMPLETIONS_URL, headers=headers, json=payload, timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            elif response.status_code == 429:
                sleep_time = (attempt + 1) * 2
                time.sleep(sleep_time)
            else:
                response.raise_for_status()
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise RuntimeError(
                    f"Upstage Chat Completions API 호출 실패 (재시도 초과): {e}"
                )
            time.sleep((attempt + 1) * 2)

    raise RuntimeError("Upstage Chat Completions API 호출 실패 (최대 재시도 횟수 초과)")
