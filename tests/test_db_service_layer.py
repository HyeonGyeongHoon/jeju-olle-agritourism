"""DB 조회 서비스 레이어(src/services/db_service.py) 분리에 대한 회귀 방지 테스트.

2026-07-26 에 `src/agent/nodes.py` 에 있던 순수 DB/데이터 조회 헬퍼들을
`src/services/db_service.py` 로 옮겼습니다(로직 변경 없는 순수 코드 이동).
이 파일은 그 이동이 조용히 깨질 수 있는 세 지점을 고정합니다 — 각 헬퍼의
동작 자체는 기존 test_visitor_analytics_node.py /
test_search_culture_knowledge.py / test_crop_filter.py 등이 계속 검증합니다.
"""

import ast
import os

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
    assert db_service._ADMIN_DONG_TO_LEGAL_DONGS.get("표선면")
