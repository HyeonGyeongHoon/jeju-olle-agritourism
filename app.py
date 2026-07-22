import json
import os

import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="제주 영농-관광 상생 기획서 도슨트", page_icon="🌾", layout="centered")

st.title("🌾 제주 영농-관광 상생 상품 기획서 도슨트")
st.caption(
    "테마, 장소, 작물, 계절 등을 자연어로 입력하면 지자체/여행사 담당자용 B2B 기획서를 자동 생성합니다.\n\n"
    "예: \"가을에 당근밭 풍경 보면서 걷기 좋은 평지 코스 기획서 써줘\""
)

if "messages" not in st.session_state:
    st.session_state.messages = []

# 기존 대화 이력 렌더링
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


def _call_report_backend(query: str, status_box):
    """백엔드 SSE 스트림을 순회하며 status_box 를 실시간으로 갱신하고, 최종 리포트/에러 텍스트를 반환합니다."""
    report_text = None
    error_text = None

    try:
        with requests.post(
            f"{BACKEND_URL}/api/v1/report/generate",
            json={"query": query},
            stream=True,
            timeout=180,
        ) as resp:
            resp.raise_for_status()
            resp.encoding = "utf-8"

            event_type = "message"
            data_lines = []

            for raw_line in resp.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue

                if raw_line == "":
                    # 빈 줄 = SSE 이벤트 블록 종료 지점
                    if data_lines:
                        data_str = "\n".join(data_lines)
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            data = {}

                        if event_type == "node_progress":
                            label = data.get("label", "")
                            status_box.update(label=label)
                            status_box.write(label)
                        elif event_type == "report":
                            report_text = data.get("report")
                        elif event_type == "error":
                            error_text = data.get("message")

                    event_type = "message"
                    data_lines = []
                    continue

                if raw_line.startswith("event:"):
                    event_type = raw_line[len("event:"):].strip()
                elif raw_line.startswith("data:"):
                    data_lines.append(raw_line[len("data:"):].strip())

    except requests.exceptions.RequestException as e:
        error_text = f"백엔드 연결 실패: {e} ({BACKEND_URL} 에서 서버가 실행 중인지 확인하세요.)"

    return report_text, error_text


user_query = st.chat_input("예: 가을에 당근밭 풍경 보면서 걷기 좋은 평지 코스 기획서 써줘")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        status_box = st.status("기획서 생성을 시작합니다...", expanded=True)
        report_text, error_text = _call_report_backend(user_query, status_box)

        if error_text:
            status_box.update(label="오류 발생", state="error")
            st.error(error_text)
            st.session_state.messages.append({"role": "assistant", "content": f"⚠️ {error_text}"})
        elif report_text:
            status_box.update(label="✅ 기획서 생성 완료", state="complete")
            st.markdown(report_text)
            st.session_state.messages.append({"role": "assistant", "content": report_text})
        else:
            status_box.update(label="응답 없음", state="error")
            st.warning("백엔드로부터 기획서를 받지 못했습니다.")
