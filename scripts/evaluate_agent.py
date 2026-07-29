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

# pyrefly: ignore [missing-import]
from src.agent.graph import agent_runtime

# 1. 환경 변수 로드 (.env)
load_dotenv()

def main():
    # LangSmith 필수 설정 확인
    if not os.getenv("LANGCHAIN_API_KEY"):
        print("[!] LANGCHAIN_API_KEY 가 설정되어 있지 않습니다. .env 파일을 확인해 주세요.")
        return

    client = Client()
    dataset_name = "jeju-olle-docent-eval-dataset-v4"

    # 2. 평가용 데이터셋 존재 여부 확인 및 생성
    try:
        dataset = client.read_dataset(dataset_name=dataset_name)
        print(f"[+] 기존 데이터셋 '{dataset_name}' 을 로드했습니다.")
    except Exception:
        print(f"[-] 데이터셋 '{dataset_name}' 이 없어 새로 생성합니다...")
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="제주 올레 도슨트 기획서 자동 생성 및 기상 악화 반려 검증용 (v4)"
        )
        
        # 테스트 케이스 입력 (질문 및 기대 모범 답안)
        inputs = [
            {"question": "가을에 평탄한 코스로 기획서 써줘"},
            {"question": "태풍 불 때 올레길 1코스 걷는 것 괜찮을까?"},
            {"question": "구좌읍 3월 방문객 통계 알려줘"},
            {"question": "여름철 올레길 안전 수칙 알려줘"},
            {"question": "가을에 당근밭 풍경 보면서 걷기 좋은 평지 코스 기획서 써줘"},
            {"question": "겨울에 감귤 농장 체험할 수 있는 코스 기획서"},
            {"question": "9월에 갈만한 코스 기획해줘"},
            {"question": "태풍 오는데도 갈 수 있는 코스 기획해줘"},
            {"question": "구좌읍 쪽 코스로 기획서 써줘"},
            {"question": "휠체어로도 갈 수 있는 코스로 기획해줘"},
            {"question": "서울 맛집 추천해줘"},
            {"question": "2030 청년층 방문객 비중이 높은 지역에서 힐링 코스 기획해줘"},
            {"question": "감자밭 볼 수 있는 코스 추천해줘"},
            {"question": "7코스 초보자한테 추천할 만해?"},
            {"question": "당근 코스 추천해줘"},
            {"question": "올레길 준비물 뭐가 있어?"},
            {"question": "구좌읍 3월 방문객 수는?"},
            {"question": "1코스 소요시간 알려줘"},
            {"question": "올레길 조난 시 행동 요령 알려줘"},
            {"question": "1코스 추천해줘"},
            {"question": "소요시간 2시간 이내인데 최상 난이도 코스 기획서 써줘"},
            {"question": "대정읍에서 수박 농가 체험이 가능하고 휠체어 접근이 가능한 코스 기획서 써줘"},
            {"question": "1코스 난이도가 어떻게 되나요?"},
            {"question": "현재 태풍 경보 상황인데 올레길 10코스 걷는 기획서 가능해?"},
            {"question": "구좌읍 밭담 문화에 대해 알려줘"}
        ]
        outputs = [
            {"reference": "## 1. 📊 B2B 상품 개요 & 스펙... 이 포함된 5개 섹션 기획서 형식"},
            {"reference": "기상 악화로 인해 안전을 위해 올레길 B2B 상품 기획서를 작성할 수 없습니다. (반려)"},
            {"reference": "구좌읍 지역의 3월 방문객 통계 데이터 수치 명시"},
            {"reference": "여름철 온열질환 예방 등 안전 수칙 설명"},
            {"reference": "## 1. 📊 B2B 상품 개요 & 스펙... 구좌읍 당근밭 풍경 및 재배 역사, 겨울 수확 등의 내용이 반영된 5개 섹션 B2B 기획서"},
            {"reference": "## 1. 📊 B2B 상품 개요 & 스펙... 감귤 수확기 정보 반영 및 겨울철 한파/강풍 기후 리스크(WARNING)가 명시된 5개 섹션 B2B 기획서"},
            {"reference": "## 1. 📊 B2B 상품 개요 & 스펙... 9월 태풍 기후 리스크(WARNING) 및 우회 동선 Plan B가 명시된 5개 섹션 B2B 기획서"},
            {"reference": "기상 악화로 인해 안전을 위해 올레길 B2B 상품 기획서를 작성할 수 없습니다. (반려)"},
            {"reference": "## 1. 📊 B2B 상품 개요 & 스펙... 구좌읍과 실제로 겹치는 코스(21코스 등)를 하드 필터링하여 작성한 5개 섹션 B2B 기획서"},
            {"reference": "## 1. 📊 B2B 상품 개요 & 스펙... 휠체어 이동 가능 코스(가파도 10-1코스 등)만 매칭하여 구성한 5개 섹션 B2B 기획서"},
            {"reference": "죄송하지만 이 질문에는 답변드리기 어렵습니다. 이 서비스는 제주 올레길 B2B 기획 및 관련 정보 안내만을 제공하며, 개별 코스 추천이나 일반 정보 안내는 제공하지 않습니다."},
            {"reference": "## 1. 📊 B2B 상품 개요 & 스펙... 2030 청년 비중이 가장 높은 외도동(17코스)을 매칭하여 작성한 5개 섹션 B2B 기획서"},
            {"reference": "현재 7월 기준 파종기 준비 단계 혹은 파종 전 휴경지 경관 등의 비제철 서술이 반영된 5개 섹션 B2B 기획서"},
            {"reference": "7코스는 총 17.6km로 중난이도에 소요시간 5~6시간이 소요되므로 초보자에게는 다소 어려울 수 있다는 DB 실측 근거 기반의 상세 답변 (기획서 형식 아님)"},
            {"reference": "죄송하지만 이 질문에는 답변드리기 어렵습니다. 이 서비스는 제주 올레길 B2B 기획 및 관련 정보 안내만을 제공하며, 개별 코스 추천이나 일반 정보 안내는 제공하지 않습니다."},
            {"reference": "올레길 탐방에 필요한 신발, 우비, 모자, 선크림, 물병 등 준비물 목록 안내"},
            {"reference": "2026년 3월 구좌읍 방문객 수는 460,038명 등 정확한 DB 실측 통계 수치 기반의 상세 답변"},
            {"reference": "영농, 밭담, 상생 등 농가 테마 관련 키워드가 없으므로 문화지식 RAG 조회 없이 1코스 소요시간(약 4~5시간)만 기획자 개조식 톤으로 단순 조회 답변 제공"},
            {"reference": "실제 안전/에티켓 가이드 DB와 연동되어 조난 시 행동 수칙(112/119 신고, 위치 표지판 확인 등)을 기획자/운영진 매뉴얼 톤으로 기술"},
            {"reference": "특정 코스 지목 후 단순 추천 성격의 질의이므로 기획서 생성을 하지 않고, info_lookup 또는 other(범용 안내) 경로로 우회되어 1코스의 기본 정보 및 탐방 조건만 설명 제공"},
            {"reference": "난이도 및 소요시간 제약의 상충(모순)을 감지하여 기획서 생성을 실패시키고, 조건 완화 불가에 대한 반려 메시지를 출력하며 Fail-Fast 조기 종료"},
            {"reference": "대정읍 수박 재배 코스 중 휠체어 접근 가능한 코스가 DB에 0건이므로, 임의로 다른 조건으로 완화하거나 다른 코스를 대체 추천하지 않고 즉시 반려 메시지를 출력"},
            {"reference": "1코스 난이도는 중입니다. 라는 일반 답변 대신, 실무 매뉴얼화 가능한 기획자 톤(~ 필요, ~ 수립, ~ 권장 등)의 개조식 문체로 작성"},
            {"reference": "기상 정보가 DANGER(태풍 경보 등)로 판단되므로 RAG 검색(retriever)을 우회하여 즉시 B2B 기획서 생성을 중단하고 반려 문구(is_exit_early)를 출력하며 조기 종료"},
            {"reference": "밭담(상생 테마) 키워드가 있으므로 문화지식 RAG를 조회하여 구좌읍의 고유한 밭담 문화와 인문학적 배경을 기획자 톤으로 기술"}
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
