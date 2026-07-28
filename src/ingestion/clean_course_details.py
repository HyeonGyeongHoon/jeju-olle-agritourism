import json
import os
import sys

from dotenv import load_dotenv

# 프로젝트 루트 디렉토리를 path에 추가하여 src 모듈을 정상적으로 import할 수 있도록 함
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.agent.llm_client import get_chat_completion

load_dotenv()

def clean_text_with_llm(text: str) -> str:
    system_prompt = (
        "당신은 한국어 맞춤법 및 띄어쓰기 교정 전문가입니다.\n"
        "다음은 PDF에서 텍스트를 추출하는 과정에서 줄바꿈이나 자간 문제로 인해 단어 중간에 잘못 들어간 "
        "불필요한 공백(예: '용수저수 지가' -> '용수저수지가', '자 라는' -> '자라는')이 포함된 제주 올레길 상세 소개 텍스트입니다.\n"
        "한글 맞춤법과 올바른 띄어쓰기 규칙에 맞게 이 텍스트의 불필요한 공백을 바로잡아 정제해 주세요.\n"
        "**[주의사항]**\n"
        "1. 내용을 요약하거나 문장 구조를 바꾸거나 단어를 임의로 대체하지 마세요. 오직 띄어쓰기 오류만 수정해야 합니다.\n"
        "2. 정상적으로 띄어써야 하는 부분(예: '사방으로 밭이')은 띄어쓰기를 훼손하지 마세요.\n"
        "3. 오직 교정된 순수 텍스트 결과물만 출력하고, 다른 설명이나 해설은 덧붙이지 마세요."
    )
    try:
        cleaned = get_chat_completion(system_prompt, text)
        return cleaned.strip()
    except Exception as e:
        print(f"[!] LLM 호출 실패: {e}")
        return text

def main():
    file_path = "data/extracted/course_detail_texts.json"
    if not os.path.exists(file_path):
        print(f"[!] 파일을 찾을 수 없습니다: {file_path}")
        return

    print("[*] course_detail_texts.json 로드 중...")
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    total = len(data)
    print(f"[*] 총 {total}개의 코스 텍스트 정제 시작...")

    for i, item in enumerate(data):
        course_name = item.get("course_name")
        print(f"[{i+1}/{total}] {course_name} 정제 중...")
        original_text = item.get("detail_text", "")
        if original_text:
            cleaned_text = clean_text_with_llm(original_text)
            item["detail_text"] = cleaned_text

    print("[*] 정제 완료, 파일에 쓰는 중...")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("[+] 모든 작업이 성공적으로 완료되었습니다!")

if __name__ == "__main__":
    main()
