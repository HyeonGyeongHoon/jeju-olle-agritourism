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


class ChatRequest(BaseModel):
    query: str


def _tokenize_text(text: str, chunk_size: int = 4):
    """최종 완성 텍스트를 자연스러운 실시간 스트리밍 느낌을 주도록 쪼개어 반환합니다."""
    for i in range(0, len(text), chunk_size):
        yield text[i:i+chunk_size]


async def event_generator(query: str):
    """LangGraph 워크플로우를 실행하고 발생되는 중간 메타데이터 및 도슨트 토큰을 SSE 스트림으로 흘려보냅니다."""
    inputs = {
        "query": query,
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
    
    metadata_sent = False
    
    try:
        # LangGraph 동기 스트림을 이벤트 루프에서 실행하며 소모
        # 동기 호출이 블로킹되지 않도록 하기 위해 run_in_executor 혹은 비동기 래핑 활용
        loop = asyncio.get_event_loop()
        
        # LangGraph stream 은 동기 제네레이터이므로 헬퍼 함수로 스트림을 추출
        def run_graph():
            return list(agent_runtime.stream(inputs))
            
        events = await loop.run_in_executor(None, run_graph)

        final_response_text = None

        for event in events:
            node_name = list(event.keys())[0]
            node_output = event[node_name]

            # 1. retriever 노드가 완료된 즉시 메타데이터 정보 전송
            if node_name == "retriever" and not metadata_sent:
                metadata = {
                    "retrieved_courses": [c["course_name"] for c in node_output.get("retrieved_chunks", [])],
                    "fallback_applied": node_output.get("fallback_applied", False),
                    "fallback_reason": node_output.get("fallback_reason", ""),
                    "weather_warning": inputs.get("weather_info", {}).get("warnings", []) if inputs.get("weather_info") else []
                }
                yield f"event: metadata\ndata: {json.dumps(metadata, ensure_ascii=False)}\n\n"
                metadata_sent = True

            # 2. 최종 응답 후보 추적 (docent_generator 또는 local_recommender 가 최신값으로 갱신,
            #    의도 분류상 로컬 추천이 스킵된 경우 docent_generator 의 값이 그대로 최종 응답이 됨)
            if node_output.get("final_response"):
                final_response_text = node_output["final_response"]

        # 3. 실행이 모두 끝난 뒤 확정된 최종 답변을 토큰 단위로 스트리밍
        if final_response_text:
            for token in _tokenize_text(final_response_text):
                token_data = {"token": token}
                yield f"event: token\ndata: {json.dumps(token_data, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.01)  # 타이핑 시각 효과

        # 4. 정상 종료 알림
        yield "event: end\ndata: {}\n\n"
        
    except Exception as e:
        error_msg = f"에러가 발생했습니다: {str(e)}"
        yield f"event: error\ndata: {json.dumps({'message': error_msg}, ensure_ascii=False)}\n\n"


@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    """제주올레 도슨트 RAG SSE 실시간 답변 스트리밍 API 엔드포인트입니다."""
    return StreamingResponse(
        event_generator(request.query),
        media_type="text/event-stream"
    )


@app.get("/health")
def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
