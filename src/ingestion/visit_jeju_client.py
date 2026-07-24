import os
import requests
import time
from typing import List, Dict, Any

# certifi 번들에 없는 사내/캠퍼스 네트워크 프록시 루트 인증서 문제 대응을 위해
# OS 신뢰 저장소(Windows/macOS 인증서 스토어)를 사용하도록 전환
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

VISIT_JEJU_SEARCH_URL = "https://api.visitjeju.net/vsjApi/contents/searchList"


def get_visit_jeju_recommendations(crop_tag: str, administrative_area: str) -> List[Dict[str, Any]]:
    """비짓제주 Open API (category=c4) 를 활용하여 행정구역 및 작물 태그에 부합하는 로컬 카페/음식점을 검색 및 수집합니다.
    API Key 가 설정되지 않았거나 호출 실패 시 로컬 Mock 데이터를 반환합니다.
    """
    api_key = os.getenv("VISIT_JEJU_API_KEY")
    
    # 1. API 키가 없거나 비어있는 경우 Mock 데이터로 즉시 폴백
    if not api_key:
        return _get_mock_recommendations(crop_tag, administrative_area)
        
    params = {
        "apiKey": api_key,
        "locale": "kr",
        "category": "c4",  # 음식점/카페 카테고리
    }
    
    # 비짓제주 API 는 상세 검색 키워드를 제공하지 않는 경우가 많으므로 전체 조회를 돌며 필터링하거나,
    # API 호출을 보완합니다. 여기서는 API 를 직접 호출하되 에러 발생 시 Mock 으로 안전하게 처리합니다.
    # timeout/재시도 횟수는 일부러 짧게 잡았습니다 — 방화벽 차단이나 API 서버 응답 지연으로 매번
    # 실패하는 상황(2026-07-24 확인)에서 기존 timeout=10초 x 3회 재시도(최대 약 36초/호출)로는
    # 크롭×지역 조합이 여러 개인 리포트 하나 생성에 지연이 크게 누적됐습니다.
    max_retries = 2
    for attempt in range(max_retries):
        try:
            response = requests.get(VISIT_JEJU_SEARCH_URL, params=params, timeout=4)
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                
                # API 데이터 중 행정구역(address)과 작물 키워드(title/introduction) 매칭 필터링
                filtered_recommendations = []
                for item in items:
                    title = item.get("title") or ""
                    address = item.get("address") or ""
                    road_address = item.get("roadaddress") or ""
                    introduction = item.get("introduction") or ""
                    
                    # 행정구역 매칭 확인 (읍·면·리 단위가 주소에 포함되는지 확인)
                    # 예: administrative_area 가 '시흥리' 이고 주소에 '시흥리' 가 있는지
                    area_match = administrative_area in address or administrative_area in road_address
                    
                    # 작물 매칭 확인 (작물 키워드가 상점명이나 소개글에 들어있는지 확인)
                    crop_match = crop_tag in title or crop_tag in introduction
                    
                    if area_match and crop_match:
                        filtered_recommendations.append({
                            "crop_tag": crop_tag,
                            "title": title,
                            "address": address,
                            "road_address": road_address,
                            "phone": item.get("phoneno", ""),
                            "introduction": introduction,
                            "latitude": float(item.get("latitude", 0.0)) if item.get("latitude") else 0.0,
                            "longitude": float(item.get("longitude", 0.0)) if item.get("longitude") else 0.0,
                            "administrative_area": administrative_area,
                            "source": "visitjeju_api",
                            "metadata": {
                                "reccenter": item.get("reccenter", ""),
                                "imgpath": item.get("imgpath", "")
                            }
                        })
                
                # 만약 실 API 에서 매칭된 결과가 없다면, RAG 정상 동작을 위해 최적의 Mock 데이터를 대신 채워 반환
                if not filtered_recommendations:
                    return _get_mock_recommendations(crop_tag, administrative_area)
                return filtered_recommendations
                
            elif response.status_code == 429:
                time.sleep((attempt + 1) * 1)
            else:
                response.raise_for_status()
        except Exception as e:
            print(f"[!] 비짓제주 API 호출 중 예외 발생 (시도 {attempt+1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                print("[!] API 호출 실패로 인해 로컬 Mock 데이터로 폴백합니다.")
                return _get_mock_recommendations(crop_tag, administrative_area)
            time.sleep((attempt + 1) * 1)
            
    return _get_mock_recommendations(crop_tag, administrative_area)


def _get_mock_recommendations(crop_tag: str, administrative_area: str) -> List[Dict[str, Any]]:
    """로컬 테스트를 위해 사전에 정의된 작물 및 행정구역 연동 상점 데이터를 반환합니다."""
    # 대표적인 작물 및 행정구역별 Mock 데이터베이스
    mock_db = [
        # 당근 - 구좌읍/성산읍 (시흥리, 종달리, 김녕리, 행원리)
        {
            "crop_tag": "당근",
            "title": "구좌당근밭 109 카페",
            "address": "제주시 구좌읍 종달리 109-1",
            "road_address": "제주시 구좌읍 해맞이해안로 2100",
            "phone": "064-782-0109",
            "introduction": "구좌읍 당근으로 만든 100% 생과일 당근 주스와 촉촉한 수제 당근 케이크를 전문으로 하는 오션뷰 농가 카페입니다.",
            "latitude": 33.528405,
            "longitude": 126.901502,
            "administrative_area": "종달리"
        },
        {
            "crop_tag": "당근",
            "title": "종달당근소담식당",
            "address": "제주시 구좌읍 종달리 814-10",
            "road_address": "제주시 구좌읍 종달로5길 32",
            "phone": "064-783-8140",
            "introduction": "종달리 마을 주민이 직접 기른 당근을 듬뿍 넣은 당근 퓌레 돈가스와 당근 수프를 즐길 수 있는 포근한 맛집입니다.",
            "latitude": 33.527310,
            "longitude": 126.898740,
            "administrative_area": "종달리"
        },
        {
            "crop_tag": "당근",
            "title": "시흥 동네카페 당근당근",
            "address": "서귀포시 성산읍 시흥리 12-4",
            "road_address": "서귀포시 성산읍 시흥로 48",
            "phone": "064-784-0012",
            "introduction": "시흥리 밭길 입구에 위치하여 올레길 1코스 탐방객들이 쉬어가기 좋은 아기자기한 카페입니다. 직접 수확한 당근 주스가 별미입니다.",
            "latitude": 33.486250,
            "longitude": 126.908420,
            "administrative_area": "시흥리"
        },
        # 감귤 - 남원읍 (위미리), 안덕면 (사계리), 한경면 (고산리/신평리), 서귀포시 (서호동/호근동)
        {
            "crop_tag": "감귤",
            "title": "위미리 귤림원 카페",
            "address": "서귀포시 남원읍 위미리 785-5",
            "road_address": "서귀포시 남원읍 태위로 120",
            "phone": "064-764-7850",
            "introduction": "키 큰 동백나무 방풍림과 노란 감귤밭 사이에 아늑하게 숨겨진 정원 카페입니다. 풋귤 에이드와 감귤 타르트가 일품입니다.",
            "latitude": 33.278912,
            "longitude": 126.662095,
            "administrative_area": "위미리"
        },
        {
            "crop_tag": "감귤",
            "title": "고근산 귤꽃향기 다방",
            "address": "서귀포시 서호동 1245-2",
            "road_address": "서귀포시 고근산로 54",
            "phone": "064-739-1245",
            "introduction": "서호마을 감귤농원길 한가운데에 위치한 전통 찻집입니다. 겨울철에는 새콤달콤한 수제 귤피차와 귤빵을 제공합니다.",
            "latitude": 33.264850,
            "longitude": 126.521840,
            "administrative_area": "서호동"
        },
        {
            "crop_tag": "감귤",
            "title": "사계 감귤낭 아래",
            "address": "서귀포시 안덕면 사계리 2120-1",
            "road_address": "서귀포시 안덕면 사계남로 88",
            "phone": "064-792-2120",
            "introduction": "사계포구 인근 돌담귤밭 뷰를 자랑하는 브런치 카페입니다. 시그니처 귤잼 토스트와 귤카푸치노가 유명합니다.",
            "latitude": 33.234120,
            "longitude": 126.298450,
            "administrative_area": "사계리"
        },
        # 마늘 - 대정읍 (무릉리/신평리) 및 수원리
        {
            "crop_tag": "마늘",
            "title": "대정마농 오일 파스타",
            "address": "서귀포시 대정읍 신평리 34-1",
            "road_address": "서귀포시 대정읍 신평로 12",
            "phone": "064-794-3401",
            "introduction": "대정 알마늘을 아낌없이 넣은 매콤하고 알싸한 마농 파스타와 마늘 바게트가 시그니처인 아늑한 시골 레스토랑입니다.",
            "latitude": 33.284520,
            "longitude": 126.262450,
            "administrative_area": "신평리"
        },
        {
            "crop_tag": "마늘",
            "title": "수원리 마농바게트 하우스",
            "address": "제주시 한림읍 수원리 456-2",
            "road_address": "제주시 한림읍 한림로 890",
            "phone": "064-796-4562",
            "introduction": "수원리 마늘밭길가에 위치하여 고소한 구운 마늘 냄새가 끊이지 않는 베이커리입니다. 마늘 크림치즈 바게트가 조기 품절되곤 합니다.",
            "latitude": 33.424100,
            "longitude": 126.258900,
            "administrative_area": "수원리"
        },
        # 녹차 - 저지리 및 신산리
        {
            "crop_tag": "녹차",
            "title": "저지 곶자왈 녹차정원",
            "address": "제주시 한경면 저지리 2001",
            "road_address": "제주시 한경면 녹차분재로 501",
            "phone": "064-772-2001",
            "introduction": "저지 오설록 녹차밭 사잇길 초입에 있는 힐링 티하우스입니다. 유기농 말차 라떼와 말차 롤케이크를 직접 내립니다.",
            "latitude": 33.324560,
            "longitude": 126.289450,
            "administrative_area": "저지리"
        },
        {
            "crop_tag": "녹차",
            "title": "신산리 마을카페",
            "address": "서귀포시 성산읍 신산리 1130-2",
            "road_address": "서귀포시 성산읍 환해장성로 111",
            "phone": "064-784-1130",
            "introduction": "신산리 부녀회에서 운영하는 오션뷰 바닷가 카페로, 제주올레와 공동 기획한 진한 유기농 녹차 아이스크림이 대표 메뉴입니다.",
            "latitude": 33.364890,
            "longitude": 126.852410,
            "administrative_area": "신산리"
        },
        # 땅콩 / 보리 / 메밀
        {
            "crop_tag": "땅콩",
            "title": "우도 소도리 땅콩아이스크림",
            "address": "제주시 우도면 연평리 1024-5",
            "road_address": "제주시 우도면 우도해안길 320",
            "phone": "064-783-1024",
            "introduction": "우도 대표 작물인 우도 고소한 땅콩을 듬뿍 갈아 얹은 유기농 우유 땅콩 아이스크림과 땅콩 수제 쿠키를 판매합니다.",
            "latitude": 33.512450,
            "longitude": 126.958900,
            "administrative_area": "연평리"
        },
        {
            "crop_tag": "보리",
            "title": "가파도 황금보리 쉼터",
            "address": "서귀포시 대정읍 가파리 120-1",
            "road_address": "서귀포시 대정읍 가파로 45",
            "phone": "064-794-0120",
            "introduction": "가파도 청보리밭 한가운데 자리 잡은 쉼터 카페입니다. 고소한 새싹보리 미숫가루와 보리 크런치 젤라또가 피로를 씻어줍니다.",
            "latitude": 33.168450,
            "longitude": 126.271890,
            "administrative_area": "가파리"
        },
        # 무(월동무) - 성산읍/구좌읍
        {
            "crop_tag": "무",
            "title": "시흥리 월동무 해장국",
            "address": "서귀포시 성산읍 시흥리 203-1",
            "road_address": "서귀포시 성산읍 오조로 88",
            "phone": "064-784-2031",
            "introduction": "제주 화산토에서 자란 아삭한 월동무로 끓인 시원한 무해장국과 무말랭이 반찬이 일품인 마을 식당입니다.",
            "latitude": 33.462350,
            "longitude": 126.902100,
            "administrative_area": "시흥리"
        },
        # 유채 - 표선면 가시리
        {
            "crop_tag": "유채",
            "title": "가시리 노란꽃밭 카페",
            "address": "서귀포시 표선면 가시리 1717-5",
            "road_address": "서귀포시 표선면 녹산로 464",
            "phone": "064-787-1717",
            "introduction": "녹산로 유채꽃밭 초입에 자리한 카페로, 봄철엔 유채꽃 라떼와 유채나물 파스타를 선보입니다.",
            "latitude": 33.395280,
            "longitude": 126.799460,
            "administrative_area": "가시리"
        },
        # 감자 - 구좌읍 종달리 (대정 가을감자를 취급하는 동부 지역 매장으로 설정)
        {
            "crop_tag": "감자",
            "title": "종달리 감자옹심이",
            "address": "제주시 구좌읍 종달리 331-2",
            "road_address": "제주시 구좌읍 종달로 45",
            "phone": "064-783-3312",
            "introduction": "대정 가을감자를 직접 공수해 만드는 쫄깃한 감자옹심이와 감자전이 인기인 마을 식당입니다.",
            "latitude": 33.526800,
            "longitude": 126.897200,
            "administrative_area": "종달리"
        },
        # 호밀 - 우도면 연평리
        {
            "crop_tag": "호밀",
            "title": "우도 호밀밭 베이커리",
            "address": "제주시 우도면 연평리 587-3",
            "road_address": "제주시 우도면 우도해안길 158",
            "phone": "064-782-5873",
            "introduction": "우도 겨울 방풍 호밀밭 옆에 자리한 작은 베이커리로, 통호밀빵과 호밀 크래커를 직접 굽습니다.",
            "latitude": 33.507200,
            "longitude": 126.953100,
            "administrative_area": "연평리"
        },
        # 브로콜리 - 대정읍 무릉리
        {
            "crop_tag": "브로콜리",
            "title": "무릉외갓집 브로콜리밥상",
            "address": "서귀포시 대정읍 무릉리 1203",
            "road_address": "서귀포시 대정읍 신영로 456",
            "phone": "064-794-1203",
            "introduction": "애월 곽지리에서 시작된 제주 브로콜리 재배 역사를 소개하며, 브로콜리 두부무침 정식을 내는 시골 밥상입니다.",
            "latitude": 33.253600,
            "longitude": 126.253900,
            "administrative_area": "무릉리"
        },
        # 양배추 - 한림읍 협재리
        {
            "crop_tag": "양배추",
            "title": "협재 월동양배추 김치공방",
            "address": "제주시 한림읍 협재리 1552-6",
            "road_address": "제주시 한림읍 협재로 44",
            "phone": "064-796-1552",
            "introduction": "한림 소구형 월동양배추로 담근 아삭한 양배추 김치와 양배추롤을 맛볼 수 있는 체험형 공방입니다.",
            "latitude": 33.394200,
            "longitude": 126.239500,
            "administrative_area": "협재리"
        },
        # 콜라비 - 한경면 고산리
        {
            "crop_tag": "콜라비",
            "title": "고산리 콜라비 피클하우스",
            "address": "제주시 한경면 고산리 3785",
            "road_address": "제주시 한경면 고산로 12",
            "phone": "064-772-3785",
            "introduction": "이색 겨울 작물 콜라비를 새콤달콤하게 절인 콜라비 피클과 콜라비 라페를 판매하는 작은 로컬 매장입니다.",
            "latitude": 33.303100,
            "longitude": 126.163900,
            "administrative_area": "고산리"
        },
        # 수박 - 애월읍 고내리
        {
            "crop_tag": "수박",
            "title": "고내리 여름수박화채",
            "address": "제주시 애월읍 고내리 1088",
            "road_address": "제주시 애월읍 고내로 233",
            "phone": "064-799-1088",
            "introduction": "제주 서부 노지수박으로 만든 시원한 수박화채와 수박주스를 즐길 수 있는 여름 한정 카페입니다.",
            "latitude": 33.459700,
            "longitude": 126.324700,
            "administrative_area": "고내리"
        },
        # 배추 - 조천읍 조천리
        {
            "crop_tag": "배추",
            "title": "조천리 김장배추 손칼국수",
            "address": "제주시 조천읍 조천리 2337",
            "road_address": "제주시 조천읍 조천북로 27",
            "phone": "064-783-2337",
            "introduction": "가을 노지배추로 담근 겉절이와 손칼국수를 함께 내는 마을 어르신들의 정겨운 식당입니다.",
            "latitude": 33.541200,
            "longitude": 126.642800,
            "administrative_area": "조천리"
        },
        # 참외 - 조천읍 신촌리
        {
            "crop_tag": "참외",
            "title": "조천 노란참외 화채가게",
            "address": "제주시 조천읍 신촌리 464-2",
            "road_address": "제주시 조천읍 신촌북길 19",
            "phone": "064-783-4642",
            "introduction": "한여름 노지참외로 만든 샛노란 참외화채와 참외빙수를 파는 계절 한정 간식 가게입니다.",
            "latitude": 33.536800,
            "longitude": 126.665300,
            "administrative_area": "신촌리"
        },
        # 쪽파 - 구좌읍 김녕리
        {
            "crop_tag": "쪽파",
            "title": "김녕리 쪽파전 막걸리집",
            "address": "제주시 구좌읍 김녕리 1621-4",
            "road_address": "제주시 구좌읍 김녕로8길 5",
            "phone": "064-782-1621",
            "introduction": "가을 씨쪽파와 김장철 수확 쪽파로 부친 바삭한 쪽파전에 제주 막걸리를 곁들이는 동네 술집입니다.",
            "latitude": 33.556100,
            "longitude": 126.760500,
            "administrative_area": "김녕리"
        },
        # 양파 - 구좌읍 김녕리
        {
            "crop_tag": "양파",
            "title": "김녕 조생양파 스프가게",
            "address": "제주시 구좌읍 김녕리 2033",
            "road_address": "제주시 구좌읍 김녕로 120",
            "phone": "064-782-2033",
            "introduction": "제주 조생종 양파를 오래 우려낸 담백한 양파스프와 양파튀김을 내는 작은 브런치 가게입니다.",
            "latitude": 33.554700,
            "longitude": 126.759800,
            "administrative_area": "김녕리"
        }
    ]
    
    # 전달받은 행정구역 및 작물 태그와 부합하는 Mock 매장을 필터링하여 반환
    results = []
    for item in mock_db:
        # 행정구역 매칭 (동/리 단위 포함 여부)
        area_match = administrative_area in item["address"] or administrative_area in item["administrative_area"] or administrative_area == ""
        # 작물 매칭
        crop_match = crop_tag in item["crop_tag"] or crop_tag in item["title"] or crop_tag in item["introduction"]
        
        if area_match and crop_match:
            results.append({
                "crop_tag": crop_tag,
                "title": item["title"],
                "address": item["address"],
                "road_address": item["road_address"],
                "phone": item["phone"],
                "introduction": item["introduction"],
                "latitude": item["latitude"],
                "longitude": item["longitude"],
                "administrative_area": item["administrative_area"],
                "source": "mock_db",
                "metadata": {}
            })

    # 매칭된 것이 없을 경우, 작물 태그가 같은 매장들을 행정구역 제약 없이 전체 추천으로 Fallback
    if not results:
        for item in mock_db:
            if crop_tag in item["crop_tag"]:
                results.append({
                    "crop_tag": crop_tag,
                    "title": item["title"],
                    "address": item["address"],
                    "road_address": item["road_address"],
                    "phone": item["phone"],
                    "introduction": item["introduction"],
                    "latitude": item["latitude"],
                    "longitude": item["longitude"],
                    "administrative_area": item["administrative_area"],
                    "source": "mock_db",
                    "metadata": {}
                })
                
    return results[:3]  # 최대 3개 추천
