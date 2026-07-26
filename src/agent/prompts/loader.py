"""LLM 시스템 프롬프트를 이 패키지 안의 .md 파일에서 읽어오는 로더입니다.

2026-07-26 이전에는 각 노드 함수 본문과 router.py 안에 대형 프롬프트가 삼중따옴표
리터럴로 하드코딩되어 있었습니다. 프롬프트 문구를 고칠 때마다 파이썬 코드를 건드려야
했고, 어떤 문구가 어느 노드의 것인지 코드를 열어야만 알 수 있었으므로 .md 파일로
자원화했습니다. 로직 변경은 없고, 로드된 문자열은 기존 리터럴과 바이트 단위로
동일합니다.

[파일 규약]
- 프롬프트 원문은 개행으로 끝나지 않습니다(기존 삼중따옴표 리터럴이 전부 마지막
  문장 바로 뒤에서 닫혔음). 하지만 .md 파일 자체는 POSIX 텍스트 파일 관례대로
  개행 하나로 끝내 두고, `load_prompt` 가 **맨 끝 개행 하나만** 제거해 원문을
  복원합니다. 프롬프트가 정말로
  개행으로 끝나야 한다면 .md 파일 끝에 빈 줄을 하나 더 두세요.
- f-string 으로 값을 주입하던 프롬프트(generate_report.md)는 `{placeholder}` 형태를
  그대로 남겨 두고, 호출부가 로드 후 `.format(...)` 을 적용합니다. 그런 파일에는
  치환 대상이 아닌 리터럴 중괄호가 없어야 합니다(있다면 `{{`/`}}` 로 이스케이프).
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(filename: str) -> str:
    """이 패키지(src/agent/prompts) 안의 .md 프롬프트 파일을 읽어 문자열로 반환합니다.

    경로는 이 파일 위치 기준 절대경로로 만들기 때문에, 프로세스의 현재 작업 디렉터리와
    무관하게 동작합니다(uvicorn 을 리포 루트가 아닌 곳에서 띄워도 안전).
    파일이 없으면 어떤 이름을 어디서 찾았는지 명시한 FileNotFoundError 를 던집니다 —
    조용히 빈 문자열을 돌려주면 LLM 이 시스템 프롬프트 없이 호출되어, 형식이 완전히
    깨진 답변이 사용자에게 그대로 나가기 때문입니다(fail-closed).
    """
    path = _PROMPTS_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"프롬프트 파일을 찾을 수 없습니다: {filename} (검색 위치: {_PROMPTS_DIR})"
        )
    text = path.read_text(encoding="utf-8")
    # 파일 끝 개행 하나만 제거 (위 [파일 규약] 참고)
    if text.endswith("\n"):
        text = text[:-1]
    return text
