import os
from dotenv import load_dotenv

def main():
    load_dotenv()
    print("==================================================")
    print("제주올레 도슨트 RAG 인제스천 파이프라인 MVP 구동")
    print("==================================================")
    
    # 1. PDF 로드 및 텍스트 추출
    # 2. 정규식 기반 데이터 파싱 및 스키마 검증
    # 3. OpenAI Embedding API 호출 & Supabase 적재
    
    print("인제스천 파이프라인 뼈대 실행 완료.")

if __name__ == "__main__":
    main()
