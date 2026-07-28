"""LangSmith 를 활용한 제주 올레 도슨트 에이전트 오프라인 평가(Evaluation) 스크립트.

이 스크립트는 다음 단계를 거쳐 에이전트의 답변 품질을 일괄 평가합니다.
1. 테스트용 질문-모범답안 데이터셋 정의 및 LangSmith 플랫폼 업로드
2. LangGraph 에이전트 호출용 래퍼 함수 매핑
3. evaluate() API 를 통한 배치 테스트 실행 및 결과 로깅

실행 방법:
    python scripts/evaluate_agent.py
"""

import os
import sys
from dotenv import load_dotenv

# 프로젝트 루트 경로를 Python Path 에 추가하여 src 모듈을 정상적으로 인식하게 함
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langsmith import Client
from langsmith.evaluation import evaluate
from src.agent.graph import agent_runtime

# 1. 환경 변수 로드 (.env)
load_dotenv()

def main():
    # LangSmith 필수 설정 확인
    if not os.getenv("LANGCHAIN_API_KEY"):
        print("[!] LANGCHAIN_API_KEY 가 설정되어 있지 않습니다. .env 파일을 확인해 주세요.")
        return

    client = Client()
    dataset_name = "jeju-olle-docent-eval-dataset"

    # 2. 평가용 데이터셋 존재 여부 확인 및 생성
    try:
        dataset = client.read_dataset(dataset_name=dataset_name)
        print(f"[+] 기존 데이터셋 '{dataset_name}' 을 로드했습니다.")
    except Exception:
        print(f"[-] 데이터셋 '{dataset_name}' 이 없어 새로 생성합니다...")
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="제주 올레 도슨트 기획서 자동 생성 및 기상 악화 반려 검증용"
        )
        
        # 테스트 케이스 입력 (질문 및 기대 모범 답안)
        inputs = [
            {"question": "가을에 평탄한 코스로 기획서 써줘"},
            {"question": "태풍 불 때 올레길 1코스 걷는 것 괜찮을까?"},
            {"question": "구좌읍 3월 방문객 통계 알려줘"},
            {"question": "여름철 올레길 안전 수칙 알려줘"}
        ]
        outputs = [
            {"reference": "## 1. 📊 B2B 상품 개요 & 스펙... 이 포함된 5개 섹션 기획서 형식"},
            {"reference": "기상 악화로 인해 안전을 위해 올레길 B2B 상품 기획서를 작성할 수 없습니다. (반려)"},
            {"reference": "구좌읍 지역의 3월 방문객 통계 데이터 수치 명시"},
            {"reference": "여름철 온열질환 예방 등 안전 수칙 설명"}
        ]
        
        client.create_examples(
            inputs=inputs,
            outputs=outputs,
            dataset_id=dataset.id
        )
        print(f"[+] '{dataset_name}' 에 {len(inputs)} 개의 테스트 케이스를 등록했습니다.")

    # 3. 에이전트 그래프를 타겟 런 함수로 래핑
    def predict(inputs: dict) -> dict:
        state_input = {
            "query": inputs["question"],
            "loop_count": 0,
            "parsed_constraints": None,
            "weather_info": None,
            "safety_check": None,
            "retrieved_chunks": [],
            "fallback_applied": False,
            "fallback_reason": None,
            "docent_answer": None,
            "recommendations": [],
            "final_response": None,
            "quality_report": None
        }
        
        # 에이전트 동기 실행
        result = agent_runtime.invoke(state_input)
        
        # 최종 답변 필드를 반환
        return {
            "output": result.get("final_response") or result.get("docent_answer") or "답변을 생성하지 못했습니다."
        }

    # 4. 일괄 평가 및 실험(Experiment) 시작
    print("[*] LangSmith 일괄 테스트 평가를 시작합니다...")
    results = evaluate(
        predict,
        data=dataset_name,
        experiment_prefix="olle-docent-batch-test",
    )
    
    print("\n[+] 평가 실행이 완료되었습니다!")
    print(f"[+] 다음 링크에서 결과를 확인하실 수 있습니다: {results.url}")

if __name__ == "__main__":
    main()
