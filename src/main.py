import json
import asyncio
import os
import secrets
import threading
import time
from collections import deque
from typing import Iterator, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
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
# allow_credentials=True 는 쿠키/세션 기반 인증에나 필요한데(이 API 는 X-API-Key 헤더로만
# 인증하므로 불필요) allow_origins=["*"] 와 함께 쓰면 스펙상 문제 있는 조합(브라우저가 거부하거나,
# 향후 쿠키 인증을 붙이는 순간 그대로 취약점이 됨)이라 꺼둡니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReportRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)


# --- 인증 (선택적 API 키) ---
# REPORT_API_KEY 환경변수가 설정되어 있으면 X-API-Key 헤더로 인증을 요구합니다. 설정하지 않으면
# (로컬 개발 등) 인증 없이 통과시킵니다 - 이 엔드포인트는 요청 1건당 유료 Upstage LLM/임베딩
# API 를 여러 번(최악의 경우 품질 검증 실패 재작성 루프까지 포함해 10회 이상) 호출하므로, 운영
# 환경에서는 반드시 REPORT_API_KEY 를 설정해 무단 호출로 인한 비용 소모를 막아야 합니다.
def _verify_api_key(x_api_key: Optional[str]) -> None:
    expected_key = os.getenv("REPORT_API_KEY")
    if not expected_key:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected_key):
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키입니다.")


# --- 속도 제한 (인메모리, 단일 프로세스 기준) ---
# 요청 1건당 유료 API 호출이 여러 번 발생하므로, 클라이언트(API 키가 있으면 키 기준, 없으면 IP
# 기준)당 짧은 시간 창 내 요청 수를 제한해 비용형 남용을 막습니다. 여러 워커/프로세스로 수평
# 확장하면 각 프로세스가 별도 카운터를 가지므로 이 인메모리 구현으로는 정확한 제한이 안 됩니다
# (Redis 등 공유 저장소 기반으로 바꿔야 함). 오래된 클라이언트의 빈 큐는 자동으로 청소되지 않아
# 장기 실행 시 서로 다른 클라이언트 수만큼 딕셔너리가 조금씩 늘어날 수 있는데, 소규모 내부 도구
# 규모에서는 무시할 만한 수준이라 별도 정리 스레드는 두지 않았습니다.
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_REQUESTS = 5
_rate_limit_lock = threading.Lock()
_rate_limit_log: dict[str, deque] = {}


def _check_rate_limit(client_id: str) -> None:
    now = time.monotonic()
    with _rate_limit_lock:
        log = _rate_limit_log.setdefault(client_id, deque())
        while log and now - log[0] > _RATE_LIMIT_WINDOW_SECONDS:
            log.popleft()
        if len(log) >= _RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"요청이 너무 잦습니다. {_RATE_LIMIT_WINDOW_SECONDS}초에 "
                    f"{_RATE_LIMIT_MAX_REQUESTS}건까지만 허용됩니다."
                ),
            )
        log.append(now)


def _client_identity(request: Request, x_api_key: Optional[str]) -> str:
    if x_api_key:
        return f"key:{x_api_key}"
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


def _stream_until_cancelled(stream_iter: Iterator, cancel_event: threading.Event) -> Iterator:
    """cancel_event 가 설정되면 다음 노드 결과부터 즉시 소비를 중단하는 제너레이터 래퍼입니다.
    이미 실행 중인 노드 내부의 개별 HTTP 호출(requests.post 등)을 중간에 끊을 수는 없지만, 최소한
    남은 노드/재작성 루프가 계속 유료 API 를 호출하며 도는 것은 막습니다.
    """
    for event in stream_iter:
        if cancel_event.is_set():
            print("[!] 클라이언트 연결 종료(또는 응답 소비 중단) 감지, 그래프 실행을 다음 노드부터 중단합니다.")
            break
        yield event


# 그래프 노드 완료 시 B2B 기획서 생성 진행 상황을 사용자에게 노출하기 위한 라벨 매핑
# (100% 내부 문서/DB 기반 - 실시간 외부 API 호출 없음)
NODE_PROGRESS_LABELS = {
    "intent_classifier": "🔍 자연어 질의 분류 중...",
    "intent_parser": "🔍 자연어 키워드 분석 중... (시기·작물·지역·테마)",
    "market_location_resolver": "📊 방문객 빅데이터 기준 지역 자동 검색 중...",
    "quick_responder": "📖 문화·작물·관광 정보 조회 중...",
    "safety_evaluator": "🌤️ 계절별 기후 리스크 분석 중...",
    "retriever": "📚 올레 코스 & 밭담문화 DB 하이브리드 검색 중...",
    "report_generator": "✍️ B2B 올레 도슨트 상품 기획서(섹션 1~5) 작성 중...",
    "quality_checker": "🛡️ Self-RAG 신뢰도 검증 중...",
    "query_rewriter": "🔁 검색 조건 재보정 중...",
}


async def report_event_generator(query: str, cancel_event: Optional[threading.Event] = None):
    """자연어 질의(방문 시기/작물/지역/테마 등 자유 서술)로 LangGraph 를 실제 실시간으로 순회하며
    노드 진행 상황(event: node_progress)과 최종 B2B 기획서(event: report)를 SSE 로 흘려보냅니다.
    기존 /chat/stream 과 달리 그래프 전체 실행을 기다렸다가 재생하지 않고, 백그라운드 스레드에서
    agent_runtime.stream() 을 실제로 순회하며 asyncio.Queue 로 각 노드 완료 시점에 즉시 전달합니다.
    클라이언트가 연결을 끊으면(브라우저 탭 종료 등) Starlette 가 이 제너레이터에 GeneratorExit/
    CancelledError 를 던지는데, 그 시점에 cancel_event 를 세팅해 백그라운드 스레드가 남은 노드
    실행(및 그에 딸린 유료 API 호출)을 더 진행하지 않도록 합니다. cancel_event 는 테스트에서
    직접 주입해 검증할 수 있도록 인자로도 받을 수 있게 했습니다(기본은 매 호출마다 새로 생성).
    """
    cancel_event = cancel_event or threading.Event()
    inputs = {
        "query": query,
        "loop_count": 0,
        "parsed_constraints": None,
        # intent_category 를 미리 고정하지 않고 intent_classifier 의 실제 LLM 분류에 맡깁니다.
        # (예전에는 "자연어 질의는 항상 기획서 파이프라인을 완주해야 한다"는 이유로 여기서
        # course_recommendation 으로 하드코딩했었는데, 그러면 route_intent_node 가 이미 세팅된
        # intent_category 를 보고 LLM 분류 자체를 건너뛰어서 info_lookup 같은 실제 분기가 전혀
        # 발동할 수 없었습니다 — 프로덕션에서 발견됨. target_course 도 미리 세팅하지 않음(자연어라
        # 특정 코스가 선지정되지 않고 검색이 찾아냄).
        "intent_category": None,
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
            for event in _stream_until_cancelled(agent_runtime.stream(inputs), cancel_event):
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
            # LangGraph 는 노드가 상태 변경 없이 빈 dict({})를 반환하면 스트림 이벤트 값을 None 으로
            # 넘겨줍니다(예: market_location_resolver 가 통계 기반 지역 조건이 없어 아무 것도 안 할
            # 때). node_output.get(...) 호출 전에 None 을 빈 dict 로 방어해야 합니다.
            node_output = event[node_name] or {}

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
    finally:
        # 정상 종료/예외뿐 아니라 클라이언트 연결 끊김(GeneratorExit/CancelledError)에도 반드시
        # 실행되어, 백그라운드 스레드에 "더 이상 소비하는 사람이 없다"는 신호를 보냅니다.
        cancel_event.set()


@app.post("/api/v1/report/generate")
async def report_generate(
    payload: ReportRequest,
    http_request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    """자연어 질의를 받아 B2B 관광 상품 기획서를 SSE 로 생성하는 엔드포인트입니다.
    REPORT_API_KEY 가 설정된 환경에서는 X-API-Key 헤더 인증이 필요하고, 클라이언트(키 또는 IP)당
    짧은 시간 창 내 요청 수가 제한됩니다 — 요청 1건이 여러 유료 API 호출을 유발하기 때문입니다.
    """
    _verify_api_key(x_api_key)
    _check_rate_limit(_client_identity(http_request, x_api_key))
    return StreamingResponse(
        report_event_generator(payload.query),
        media_type="text/event-stream"
    )


@app.get("/health")
def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
