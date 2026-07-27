"""DB 조회 서비스 레이어(src/services/db_service.py) 분리에 대한 회귀 방지 테스트.

2026-07-26 에 `src/agent/nodes.py` 에 있던 순수 DB/데이터 조회 헬퍼들을
`src/services/db_service.py` 로 옮겼습니다(로직 변경 없는 순수 코드 이동).
이 파일은 그 이동이 조용히 깨질 수 있는 세 지점을 고정합니다 — 각 헬퍼의
동작 자체는 기존 test_visitor_analytics_node.py /
test_search_culture_knowledge.py / test_crop_filter.py 등이 계속 검증합니다.
"""

import ast
import os
from types import SimpleNamespace

from src.agent import nodes
from src.services import db_service

# nodes.py 가 자기 네임스페이스로 가져와 노드 함수 본문에서 호출하는 이름들.
# (from ... import 바인딩이라, 기존 테스트의 patch.object(nodes, "_xxx")
#  목킹 패턴이 그대로 유효한 것도 이 바인딩 덕분입니다.)
_REEXPORTED_NAMES = (
    "_ADMIN_DONG_TO_LEGAL_DONGS",
    "_execute_rdb_filtering",
    "_fetch_course_meta_by_name",
    "_fetch_market_insight",
    "_get_known_crop_tags",
    "_get_latest_available_year_month",
    "_get_olle_relevant_admin_dongs",
    "_looks_like_course_name",
    "_normalize_admin_tier_name",
    "_resolve_stats_region_from_areas",
    "_search_culture_knowledge",
)


def test_nodes_still_resolves_moved_helpers_to_the_service_layer_objects():
    """회귀 방지: 헬퍼를 db_service 로 옮긴 뒤에도 nodes 네임스페이스에서
    **같은 객체로** 해석되어야 한다. 예전엔 nodes.py 안에 정의되어 있었고
    노드 함수 본문의 호출부는 이동 과정에서 한 줄도 바꾸지 않았으므로(순수
    코드 이동), 이 바인딩이 끊기면 노드가 NameError 로 죽거나
    patch.object(nodes, ...) 목킹이 조용히 무력화된다.
    """
    for name in _REEXPORTED_NAMES:
        assert hasattr(nodes, name), f"nodes 에서 {name} 이(가) 사라졌습니다"
        assert getattr(nodes, name) is getattr(db_service, name), (
            f"{name} 이(가) db_service 의 객체와 다릅니다 "
            "— 중복 정의가 되살아났을 수 있습니다"
        )


def test_service_layer_does_not_depend_on_the_agent_graph_layer():
    """db_service 는 LangGraph 상태/노드를 몰라야 한다(단방향 의존:
    nodes -> db_service). 반대 방향 import 가 들어오면 순환 import 가 되고,
    레이어 분리의 의미도 사라진다. router 의 코스명 정규식만 예외적으로
    재사용한다(중복 정의 방지 목적, 2026-07-25 부터).
    """
    source = open(db_service.__file__, encoding="utf-8").read()
    imported_modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)

    assert "src.agent.nodes" not in imported_modules
    assert "src.agent.state" not in imported_modules
    assert "src.agent.graph" not in imported_modules
    assert imported_modules == {
        "csv",
        "json",
        "os",
        "re",
        "typing",
        "src.agent.router",
        "src.ingestion.database_loader",
    }


def test_data_file_paths_survive_the_module_move():
    """회귀 방지: 두 경로 상수는 `__file__` 기준 3단계 상위를 리포 루트로
    가정해 계산한다. src/agent/nodes.py -> src/services/db_service.py 는
    루트로부터 같은 깊이라 그대로 동작하지만(그래서 문자열을 수정하지
    않았다), 이 파일이 더 깊거나 얕은 디렉터리로 다시 옮겨지면 조용히
    깨지므로 실제 파일 존재로 고정한다.
    """
    assert os.path.isfile(db_service._JEJU_DISTRICTS_CSV_PATH)
    assert os.path.isdir(db_service._LOCAL_CULTURE_KNOWLEDGE_DIR)
    # CSV 가 실제로 읽혀 매핑이 채워졌는지
    # (경로만 맞고 내용이 비면 지역 매칭이 전부 죽는다)
    assert db_service._LEGAL_DONG_TO_ADMIN_DONG.get("표선리") == ["표선면"]


# --- _execute_rdb_filtering 시간/거리/난이도 상한 하드 필터 (2026-07-27 추가) ---
# .eq/.lte/.in_ 호출 인자를 기록만 하고, 실제 DB 필터링은 시뮬레이션하지 않는 가짜 쿼리
# 빌더로 _execute_rdb_filtering 이 hard_constraints 를 올바른 조건으로 옮기는지만 검증한다.


class _RecordingCoursesQuery:
    def __init__(self, ids):
        self._ids = ids
        self.eq_calls = []
        self.lte_calls = []
        self.in_calls = []

    def select(self, cols):
        return self

    def eq(self, col, value):
        self.eq_calls.append((col, value))
        return self

    def lte(self, col, value):
        self.lte_calls.append((col, value))
        return self

    def in_(self, col, values):
        self.in_calls.append((col, values))
        return self

    def execute(self):
        return SimpleNamespace(data=[{"id": i} for i in self._ids])


class _RecordingClient:
    def __init__(self, query):
        self._query = query

    def table(self, name):
        return self._query


def test_execute_rdb_filtering_applies_max_time_hours_as_lte():
    query = _RecordingCoursesQuery(ids=[1, 2])
    client = _RecordingClient(query)

    result = db_service._execute_rdb_filtering(client, {"max_time_hours": 3.0})

    assert result == [1, 2]
    assert query.lte_calls == [("estimated_time_hours", 3.0)]


def test_execute_rdb_filtering_applies_max_distance_km_as_lte():
    query = _RecordingCoursesQuery(ids=[5])
    client = _RecordingClient(query)

    db_service._execute_rdb_filtering(client, {"max_distance_km": 5.0})

    assert query.lte_calls == [("total_distance_km", 5.0)]


def test_execute_rdb_filtering_applies_allowed_difficulties_as_exact_in_filter():
    """회귀 방지: 예전 max_difficulty 는 "상한(이하 모두 허용)" 이라 "상"을 요청해도 하/중까지
    통과해서, "2시간 이내인데 난이도 상" 같은 모순 요청을 RDB 단계에서 걸러내지 못했습니다.
    allowed_difficulties 는 허용 목록이므로 지정한 난이도만 그대로 in_() 에 들어가야 합니다."""
    cases = [
        (["하"], ["하"]),
        (["중"], ["중"]),
        (["상"], ["상"]),
        (["하", "중"], ["하", "중"]),
    ]
    for allowed, expected in cases:
        query = _RecordingCoursesQuery(ids=[])
        client = _RecordingClient(query)
        db_service._execute_rdb_filtering(client, {"allowed_difficulties": allowed})
        assert query.in_calls == [("difficulty", expected)]


def test_execute_rdb_filtering_normalizes_allowed_difficulties_order_and_duplicates():
    """LLM 이 넘긴 순서/중복과 무관하게 항상 쉬운 순서(하<중<상)로 정규화돼야 합니다 —
    반려 사유 문구의 표기가 실행마다 달라지지 않게 하기 위함입니다."""
    query = _RecordingCoursesQuery(ids=[])
    client = _RecordingClient(query)

    db_service._execute_rdb_filtering(client, {"allowed_difficulties": ["상", "하", "상"]})

    assert query.in_calls == [("difficulty", ["하", "상"])]


def test_execute_rdb_filtering_ignores_invalid_or_empty_allowed_difficulties():
    """allowed_difficulties 가 None/빈 리스트이거나 DB 에 없는 값만 담고 있으면 난이도 조건이
    없는 것으로 취급해야 합니다 — 유효하지 않은 값을 그대로 in_() 에 넘기면 사용자가 지정하지도
    않은 조건 때문에 0건 반려가 나기 때문입니다."""
    for hard in ({"allowed_difficulties": None}, {"allowed_difficulties": []},
                 {"allowed_difficulties": ["아주 어려움"]}, {"allowed_difficulties": "상"}):
        query = _RecordingCoursesQuery(ids=[1, 2, 3])
        client = _RecordingClient(query)
        result = db_service._execute_rdb_filtering(client, hard)
        assert result == [1, 2, 3]
        assert query.in_calls == []


def test_normalize_allowed_difficulties_returns_canonical_list():
    assert db_service._normalize_allowed_difficulties({"allowed_difficulties": ["중"]}) == ["중"]
    assert db_service._normalize_allowed_difficulties(
        {"allowed_difficulties": ["상", "하"]}
    ) == ["하", "상"]
    assert db_service._normalize_allowed_difficulties({}) == []


def test_execute_rdb_filtering_combines_all_hard_constraints():
    query = _RecordingCoursesQuery(ids=[1])
    client = _RecordingClient(query)

    db_service._execute_rdb_filtering(
        client,
        {
            "wheelchair_required": True,
            "max_time_hours": 2.0,
            "max_distance_km": 5.0,
            "allowed_difficulties": ["하", "중"],
        },
    )

    assert query.eq_calls == [("has_wheelchair_segment", "있음")]
    assert query.lte_calls == [("estimated_time_hours", 2.0), ("total_distance_km", 5.0)]
    assert query.in_calls == [("difficulty", ["하", "중"])]


def test_execute_rdb_filtering_skips_unset_constraints():
    """회귀 방지: hard_constraints 가 비어 있으면(기존 동작) lte/in_ 호출이 전혀 없어야
    한다 — 새 필터 추가가 wheelchair-only 질의의 기존 동작을 바꾸면 안 된다."""
    query = _RecordingCoursesQuery(ids=[1, 2, 3])
    client = _RecordingClient(query)

    result = db_service._execute_rdb_filtering(client, {})

    assert result == [1, 2, 3]
    assert query.eq_calls == []
    assert query.lte_calls == []
    assert query.in_calls == []
    assert db_service._ADMIN_DONG_TO_LEGAL_DONGS.get("표선면")
