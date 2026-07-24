import os
import time

import requests

UPSTAGE_CHAT_COMPLETIONS_URL = "https://api.upstage.ai/v1/chat/completions"
DEFAULT_SOLAR_CHAT_MODEL = "solar-pro2"


def get_chat_completion(
    system_prompt: str, user_message: str, model: str = DEFAULT_SOLAR_CHAT_MODEL
) -> str:
    """Upstage Solar Chat Completions API를 호출하여 답변 텍스트를 반환합니다.

    HTTP 429/5xx 및 네트워크 자체 오류(타임아웃/연결 실패 등)에 대해서만 지수 백오프 재시도가
    탑재되어 있습니다. 429/5xx 가 아닌 4xx(예: 401/400)는 요청 자체의 문제라 재시도해도 절대
    성공하지 않으므로 즉시 실패시킵니다 — 예전에는 이런 4xx 도 재시도 대상에 포함돼 있어서,
    반드시 실패할 요청 하나에 최대 5회(최대 약 30초)를 낭비했습니다.
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
        except requests.exceptions.RequestException as e:
            # 네트워크 자체의 일시적 문제(타임아웃/연결 실패 등)일 수 있으므로 재시도합니다.
            if attempt == max_retries - 1:
                raise RuntimeError(
                    f"Upstage Chat Completions API 호출 실패 (재시도 초과): {e}"
                )
            time.sleep((attempt + 1) * 2)
            continue

        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        elif response.status_code == 429 or response.status_code >= 500:
            # 429(속도 제한)나 5xx(서버 쪽 일시적 문제)는 재시도하면 성공할 가능성이 있습니다.
            if attempt == max_retries - 1:
                response.raise_for_status()
            time.sleep((attempt + 1) * 2)
        else:
            # 그 외 4xx 는 요청 자체가 잘못된 것이라 재시도해도 절대 성공하지 않으므로 즉시 실패.
            response.raise_for_status()

    raise RuntimeError("Upstage Chat Completions API 호출 실패 (최대 재시도 횟수 초과)")
