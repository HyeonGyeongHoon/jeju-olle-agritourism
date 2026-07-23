import json
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn

load_dotenv()

from src.agent.graph import agent_runtime

app = FastAPI(
    title="제주올레 도슨트 RAG 에이전트 API",
    description="LangGraph 기반 자율 순환 RAG 및 날씨 안전 우회, 로컬 매장 추천을 결합한 에이전트 서비스",
    version="1.0.0"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReportRequest(BaseModel):
    query: str


# 그래프 노드 완료 시 B2B 기획서 생성 진행 상황을 사용자에게 노출하기 위한 라벨 매핑
# (100% 내부 문서/DB 기반 - 실시간 외부 API 호출 없음)
NODE_PROGRESS_LABELS = {
    "intent_router": "🔍 자연어 질의 분류 중...",
    "intent_parser": "🔍 자연어 키워드 분석 중... (시기·작물·지역·테마)",
    "market_location_resolver": "📊 방문객 빅데이터 기준 지역 자동 검색 중...",
    "safety_evaluator": "🌤️ 계절별 기후 리스크 분석 중...",
    "retriever": "📚 올레 코스 & 밭담문화 DB 하이브리드 검색 중...",
    "docent_generator": "✍️ 로컬 영농·문화 도슨트 포인트 작성 중...",
    "local_recommender": "☕ 로컬 소개 정보 기반 제휴 아이디어 구상 중...",
    "quality_checker": "🛡️ Self-RAG 신뢰도 검증 중...",
    "query_rewriter": "🔁 검색 조건 재보정 중...",
}


async def report_event_generator(query: str):
    """자연어 질의(방문 시기/작물/지역/테마 등 자유 서술)로 LangGraph 를 실제 실시간으로 순회하며
    노드 진행 상황(event: node_progress)과 최종 B2B 기획서(event: report)를 SSE 로 흘려보냅니다.
    기존 /chat/stream 과 달리 그래프 전체 실행을 기다렸다가 재생하지 않고, 백그라운드 스레드에서
    agent_runtime.stream() 을 실제로 순회하며 asyncio.Queue 로 각 노드 완료 시점에 즉시 전달합니다.
    """
    inputs = {
        "query": query,
        "loop_count": 0,
        "parsed_constraints": None,
        # 자연어 질의는 항상 B2B 기획서 파이프라인을 완주해야 하므로 코스 추천 의도로 고정
        # (target_course 는 미리 세팅하지 않음 - 자연어라 특정 코스가 선지정되지 않고 검색이 찾아냄)
        "intent_category": "course_recommendation",
        "target_course": None,
        "b2b_params": None,
        "weather_info": None,
        "safety_check": None,
        "retrieved_chunks": [],
        "culture_chunks": [],
        "fallback_applied": False,
        "fallback_reason": None,
        "market_insight": None,
        "docent_answer": None,
        "recommendations": [],
        "final_response": None,
        "quality_report": None
    }

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def run_graph():
        try:
            for event in agent_runtime.stream(inputs):
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, {"__error__": str(e)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    # 백그라운드 스레드에서 그래프를 실제로 순회 시작 (완료를 기다리지 않고 큐로 즉시 전달받음)
    loop.run_in_executor(None, run_graph)

    try:
        final_response_text = None

        while True:
            event = await queue.get()
            if event is None:
                break
            if "__error__" in event:
                raise RuntimeError(event["__error__"])

            node_name = list(event.keys())[0]
            node_output = event[node_name]

            label = NODE_PROGRESS_LABELS.get(node_name, node_name)
            progress = {"node": node_name, "label": label}
            yield f"event: node_progress\ndata: {json.dumps(progress, ensure_ascii=False)}\n\n"

            if node_output.get("final_response"):
                final_response_text = node_output["final_response"]

        if final_response_text:
            yield f"event: report\ndata: {json.dumps({'report': final_response_text}, ensure_ascii=False)}\n\n"

        yield "event: end\ndata: {}\n\n"

    except Exception as e:
        error_msg = f"에러가 발생했습니다: {str(e)}"
        yield f"event: error\ndata: {json.dumps({'message': error_msg}, ensure_ascii=False)}\n\n"


@app.post("/api/v1/report/generate")
async def report_generate(request: ReportRequest):
    """자연어 질의를 받아 B2B 관광 상품 기획서를 SSE 로 생성하는 엔드포인트입니다."""
    return StreamingResponse(
        report_event_generator(request.query),
        media_type="text/event-stream"
    )


@app.get("/health")
def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
