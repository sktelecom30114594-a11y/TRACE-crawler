"""
여수 이순신광장·순천 낙안읍성 관광 콘텐츠 수집기.

온라인 방탈출 게임 기획에 필요한 정보를 다음 세 갈래로 모읍니다.

1. 지자체·공공기관의 선별된 페이지
   역사, 공간 구조, 상징물, 관람 동선, 체험, 행사, 교통을 수집합니다.
2. OpenStreetMap Overpass의 반경 검색
   두 관광지 주변 음식점·상점·숙박·인접 명소를 거리와 좌표와 함께 수집합니다.
3. 기존 TRACE 결과 재분류
   이미 수집한 설화·민속 자료 중 두 장소와 직접 관련된 항목을 합칩니다.

결과는 output/destinations.json에 저장됩니다. 음식점 영업시간, 전화번호, 행사와
요금 같은 정보는 변동될 수 있으므로 레코드에 검수 상태를 별도로 표시합니다.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from config import (
    BASE_DIR,
    install_cache,
    make_record,
    new_session,
    polite_sleep,
    report_and_exit,
    save_records,
)


OSM_PAGE_URL = "https://www.openstreetmap.org/{type}/{id}"
OVERPASS_ENDPOINTS = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://ethiopia.overpass.openplaceguide.org/api/interpreter",
)

OSM_LICENSE_NOTE = (
    "© OpenStreetMap contributors, ODbL 1.0. "
    "이용조건: https://www.openstreetmap.org/copyright"
)
NEARBY_CATEGORY_LIMITS = {
    "주변 지역 먹거리·가게": 150,
    "주변 상점·시장": 100,
    "주변 숙박": 40,
    "주변 명소·문화시설": 80,
}
IRRELEVANT_SHOP_TYPES = {
    "beauty",
    "car",
    "car_repair",
    "electronics",
    "funeral_directors",
    "hairdresser",
    "massage",
    "optician",
    "telecommunication",
    "tyres",
}


@dataclass(frozen=True)
class Destination:
    name: str
    region: str
    latitude: float
    longitude: float
    radius_m: int
    match_terms: tuple[str, ...]


@dataclass(frozen=True)
class PageSource:
    destination: str
    category: str
    url: str
    source_name: str
    title_hint: str
    required_terms: tuple[str, ...]
    volatile: bool = False


DESTINATIONS = {
    "여수 이순신광장": Destination(
        name="여수 이순신광장",
        region="여수",
        latitude=34.7399013049,
        longitude=127.7357348686,
        radius_m=1500,
        match_terms=("이순신광장", "전라좌수영", "거북선", "진남관", "고소대"),
    ),
    "순천 낙안읍성": Destination(
        name="순천 낙안읍성",
        region="순천",
        latitude=34.9060980993,
        longitude=127.3417429744,
        radius_m=2000,
        match_terms=("낙안읍성", "낙안 읍성", "임경업", "김빈길"),
    ),
}


# 검색엔진 결과를 자동 수집하지 않고, 사람이 확인한 공식·공공 페이지만 요청합니다.
# URL을 추가하면 같은 파서와 공통 스키마로 다음 실행부터 함께 수집됩니다.
PAGE_SOURCES = (
    PageSource(
        "여수 이순신광장",
        "핵심 개요·공간",
        "https://www.yeosu.go.kr/tour/travel/info_each_area?idx=257&mode=view",
        "여수관광문화",
        "이순신광장 공식 관광안내",
        ("이순신광장",),
        True,
    ),
    PageSource(
        "여수 이순신광장",
        "역사·인물·상징물",
        "https://www.yeosu.go.kr/tour/leisure/yisunsin_tour/yisunsin_trace",
        "여수관광문화",
        "충무공 이순신의 발자취",
        ("이순신", "거북선"),
    ),
    PageSource(
        "여수 이순신광장",
        "관광 동선·경관",
        "https://www.yeosu.go.kr/tour/leisure/bicycle/course1",
        "여수관광문화",
        "여수 자전거 1코스와 이순신광장",
        ("이순신",),
    ),
    PageSource(
        "여수 이순신광장",
        "체험·게임 소재",
        "https://www.yeosu.go.kr/tour/leisure/yisunsin_tour",
        "여수관광문화",
        "이순신 스토리텔링 낮달산책투어",
        ("이순신",),
        True,
    ),
    PageSource(
        "여수 이순신광장",
        "관광 동선·지역 먹거리",
        "https://www.yeosu.go.kr/tour/leisure/city_tour/course_01",
        "여수관광문화",
        "여수 낭만버스와 좌수영 음식특화거리",
        ("이순신", "좌수영"),
        True,
    ),
    PageSource(
        "여수 이순신광장",
        "축제·행사·게임 소재",
        "https://www.yeosu.go.kr/tour/culture_festa/geobukseon/information",
        "여수관광문화",
        "여수거북선축제 행사안내",
        ("거북선", "이순신광장"),
        True,
    ),
    PageSource(
        "여수 이순신광장",
        "인접 역사유적",
        "https://www.yeosu.go.kr/tour/travel/info_each_area?idx=246&mode=view",
        "여수관광문화",
        "고소대",
        ("고소대", "이순신"),
    ),
    PageSource(
        "여수 이순신광장",
        "인접 역사유적·비문",
        "https://www.yeosu.go.kr/tour/travel/info_each_area?idx=271&mode=view",
        "여수관광문화",
        "여수 통제이공 수군대첩비",
        ("수군대첩비",),
    ),
    PageSource(
        "여수 이순신광장",
        "인접 역사유적·비문",
        "https://www.yeosu.go.kr/tour/travel/info_each_area?idx=360&mode=view",
        "여수관광문화",
        "동령소갈비",
        ("동령소갈비",),
    ),
    PageSource(
        "여수 이순신광장",
        "거북선·조선 기술",
        "https://www.yeosu.go.kr/tour/travel/info_each_area?idx=254&mode=view",
        "여수관광문화",
        "여수 선소유적",
        ("선소", "거북선"),
    ),
    PageSource(
        "순천 낙안읍성",
        "역사·공간·인물",
        "https://www.suncheon.go.kr/nagan/page01/0002/",
        "순천 낙안읍성",
        "순천 낙안읍성 소개",
        ("낙안읍성",),
    ),
    PageSource(
        "순천 낙안읍성",
        "관람·요금",
        "https://www.suncheon.go.kr/nagan/page03/0001/",
        "순천 낙안읍성",
        "입장료와 관람시간",
        ("입장", "관람"),
        True,
    ),
    PageSource(
        "순천 낙안읍성",
        "지역 먹거리·가게·편의시설",
        "https://www.suncheon.go.kr/nagan/page03/0002/",
        "순천 낙안읍성",
        "식당 및 편의시설",
        ("식당", "편의"),
        True,
    ),
    PageSource(
        "순천 낙안읍성",
        "공간 구조·관람 동선",
        "https://www.suncheon.go.kr/nagan/page04/0001/",
        "순천 낙안읍성",
        "위치 및 관람코스",
        ("관람", "낙안"),
        True,
    ),
    PageSource(
        "순천 낙안읍성",
        "체험·게임 소재",
        "https://www.suncheon.go.kr/nagan/page04/0002/",
        "순천 낙안읍성",
        "일일상설체험",
        ("체험",),
        True,
    ),
    PageSource(
        "순천 낙안읍성",
        "세시풍속·민속",
        "https://www.suncheon.go.kr/nagan/page02/0001/",
        "순천 낙안읍성",
        "정월대보름 민속한마당",
        ("정월", "민속"),
        True,
    ),
    PageSource(
        "순천 낙안읍성",
        "축제·공연·민속",
        "https://www.suncheon.go.kr/nagan/page02/0002/",
        "순천 낙안읍성",
        "낙안읍성 민속문화축제",
        ("민속문화축제", "낙안"),
        True,
    ),
    PageSource(
        "순천 낙안읍성",
        "교통·접근",
        "https://www.suncheon.go.kr/nagan/page05/0001/",
        "순천 낙안읍성",
        "교통정보",
        ("교통", "낙안"),
        True,
    ),
    PageSource(
        "순천 낙안읍성",
        "핵심 개요·관광 팁",
        "https://www.suncheon.go.kr/tour/suncheontour/0009/0025/",
        "순천여행",
        "순천 대표관광지 낙안읍성",
        ("낙안읍성",),
        True,
    ),
    PageSource(
        "순천 낙안읍성",
        "체험·게임 소재",
        "https://www.suncheon.go.kr/tour/tourist/0020/0005/",
        "순천여행",
        "낙안읍성 체험프로그램",
        ("낙안읍성", "체험"),
        True,
    ),
)


GENERIC_CONTENT_SELECTORS = (
    "main",
    "#contents",
    "#content",
    ".contents",
    ".content",
    ".board_view",
    ".view_content",
    ".detail_cont",
    ".wrap_contView",
    "article",
    "[role='main']",
)

BLOCK_TAGS = {"h1", "h2", "h3", "h4", "p", "li", "tr", "dt", "dd"}
REMOVE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "header",
    "footer",
    "form",
    ".pagination",
    ".sns",
    ".share",
    ".recommend",
    ".comment",
)


def normalize_space(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text or "").strip()


def _is_nested_block(element: Tag, root: Tag) -> bool:
    parent = element.parent
    while isinstance(parent, Tag) and parent is not root:
        if parent.name in BLOCK_TAGS:
            return True
        parent = parent.parent
    return False


def block_text(root: Tag) -> str:
    """본문 블록을 문단 단위로 직렬화하고 표의 셀은 ` | `로 구분합니다."""
    parts: list[str] = []
    for element in root.find_all(list(BLOCK_TAGS)):
        if _is_nested_block(element, root):
            continue

        if element.name == "tr":
            cells = [normalize_space(cell.get_text(" ", strip=True)) for cell in element.find_all(["th", "td"])]
            text = " | ".join(cell for cell in cells if cell)
        else:
            text = normalize_space(element.get_text(" ", strip=True))
            if element.name in {"h1", "h2", "h3", "h4"} and text:
                text = f"[{text}]"
            elif element.name == "li" and text:
                text = f"- {text}"

        if text and (not parts or parts[-1] != text):
            parts.append(text)

    if not parts:
        fallback = normalize_space(root.get_text("\n", strip=True))
        return fallback
    return "\n\n".join(parts)


def extract_main_text(html: str, required_terms: tuple[str, ...] = ()) -> str:
    soup = BeautifulSoup(html, "lxml")
    for selector in REMOVE_SELECTORS:
        for element in soup.select(selector):
            element.decompose()

    candidates: list[Tag] = []
    seen: set[int] = set()
    for selector in GENERIC_CONTENT_SELECTORS:
        for element in soup.select(selector):
            marker = id(element)
            if marker not in seen:
                seen.add(marker)
                candidates.append(element)
    if soup.body is not None:
        candidates.append(soup.body)

    acceptable: list[tuple[int, str]] = []
    for candidate in candidates:
        text = block_text(candidate)
        # 표 두세 줄짜리 요금·체험 안내도 유효한 자료다. 필수 키워드 검사를
        # 별도로 하므로 짧다는 이유만으로 40자 이상의 공식 안내를 버리지 않는다.
        if len(text) < 40:
            continue
        hits = sum(term in text for term in required_terms)
        if required_terms and hits == 0:
            continue
        # 같은 조건이면 메뉴까지 포함한 큰 body보다 짧고 집중된 본문을 택합니다.
        score = hits * 1_000_000 - len(text)
        acceptable.append((score, text))

    if not acceptable:
        return ""
    return max(acceptable, key=lambda item: item[0])[1]


def extract_page_title(html: str, fallback: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    og_title = soup.select_one("meta[property='og:title']")
    if og_title and og_title.get("content"):
        return normalize_space(og_title["content"])
    heading = soup.find(["h1", "h2"])
    if heading:
        title = normalize_space(heading.get_text(" ", strip=True))
        if title:
            return title
    if soup.title:
        title = normalize_space(soup.title.get_text(" ", strip=True))
        if title:
            return title
    return fallback


def detect_license(html: str, source: PageSource) -> str:
    soup = BeautifulSoup(html, "lxml")
    text = normalize_space(soup.get_text(" ", strip=True))
    image_alts = " ".join(img.get("alt", "") for img in soup.find_all("img"))
    haystack = f"{text} {image_alts}"
    for number in (1, 2, 3, 4):
        if re.search(rf"공공누리\s*(?:제\s*)?{number}\s*유형", haystack):
            return f"[공공누리 제{number}유형 - 원문 표시 조건 준수] 출처: {source.source_name}"
    return f"[이용조건 개별 확인 필요] 출처: {source.source_name} ({source.url})"


def game_tags(category: str) -> list[str]:
    tags = []
    mapping = {
        "역사": ("연표", "인물", "암호문"),
        "비문": ("한자", "탁본", "문자해독"),
        "공간": ("지도", "동선", "방향"),
        "체험": ("행동미션", "도구", "역할극"),
        "축제": ("의례", "행렬", "시간순서"),
        "민속": ("세시풍속", "생활도구", "의례"),
        "먹거리": ("메뉴조합", "지역상권", "쿠폰연계"),
        "가게": ("지역상권", "쿠폰연계", "현장방문"),
        "교통": ("경로", "좌표", "시간"),
        "경관": ("사진단서", "랜드마크", "관찰"),
        "건축": ("구조", "형태", "공간추리"),
    }
    for keyword, values in mapping.items():
        if keyword in category:
            tags.extend(values)
    return list(dict.fromkeys(tags or ["스토리소재", "현장단서"]))


def enrich_record(record: dict, *, destination: Destination, category: str, volatile: bool, method: str) -> dict:
    record.update(
        {
            "관광지": destination.name,
            "지역": destination.region,
            "카테고리": category,
            "정보성격": "변동 가능 정보" if volatile else "역사·문화 중심 정보",
            "수집방식": method,
            "게임활용태그": game_tags(category),
            "검수상태": "방문·제휴 전 공식 채널 재확인 필요" if volatile else "원문 대조 필요",
        }
    )
    return record


def crawl_page(session, source: PageSource) -> dict | None:
    response = session.get(source.url, timeout=30)
    response.raise_for_status()
    if not getattr(response, "from_cache", False):
        polite_sleep()

    body = extract_main_text(response.text, source.required_terms)
    if not body:
        return None
    destination = DESTINATIONS[source.destination]
    record = make_record(
        title=f"{destination.name} | {source.category} | {source.title_hint}",
        body=body,
        source_url=response.url,
        source_name=source.source_name,
        license_note=detect_license(response.text, source),
    )
    return enrich_record(
        record,
        destination=destination,
        category=source.category,
        volatile=source.volatile,
        method="선별 공식·공공 페이지 본문 수집",
    )


def build_nearby_query(destination: Destination) -> str:
    """주변 상권·관광 요소만 가져오는 읽기 전용 Overpass QL을 만듭니다."""
    around = (
        f"around:{destination.radius_m},"
        f"{destination.latitude},{destination.longitude}"
    )
    return f"""
[out:json][timeout:45];
(
  nwr({around})[name][amenity~"restaurant|cafe|fast_food|bar|pub|marketplace"];
  nwr({around})[name][shop];
  nwr({around})[name][tourism];
  nwr({around})[name][historic];
  nwr({around})[name][craft];
);
out center;
""".strip()


def element_coordinates(element: dict) -> tuple[float | None, float | None]:
    lat = element.get("lat")
    lon = element.get("lon")
    if lat is None or lon is None:
        center = element.get("center") or {}
        lat = center.get("lat")
        lon = center.get("lon")
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def distance_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """두 WGS84 좌표의 대권거리를 미터 단위로 반올림합니다."""
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return round(radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def classify_nearby(tags: dict) -> tuple[str, bool]:
    amenity = tags.get("amenity", "")
    if amenity in {"restaurant", "cafe", "fast_food", "bar", "pub"}:
        return "주변 지역 먹거리·가게", True
    if amenity == "marketplace" or tags.get("shop"):
        return "주변 상점·시장", True
    if tags.get("tourism") in {"hotel", "motel", "guest_house", "hostel", "apartment", "camp_site"}:
        return "주변 숙박", True
    return "주변 명소·문화시설", bool(tags.get("opening_hours"))


def format_osm_address(tags: dict) -> str:
    full = tags.get("addr:full")
    if full:
        return normalize_space(full)
    values = [
        tags.get("addr:province"),
        tags.get("addr:city"),
        tags.get("addr:district"),
        tags.get("addr:street"),
        tags.get("addr:housenumber"),
    ]
    return normalize_space(" ".join(value for value in values if value))


def nearby_record(destination: Destination, element: dict) -> dict | None:
    tags = element.get("tags") or {}
    name = tags.get("name:ko") or tags.get("name")
    osm_type = element.get("type")
    osm_id = element.get("id")
    lat, lon = element_coordinates(element)
    if not name or osm_type not in {"node", "way", "relation"} or osm_id is None:
        return None
    if lat is None or lon is None:
        return None

    category, volatile = classify_nearby(tags)
    distance = distance_metres(
        destination.latitude,
        destination.longitude,
        lat,
        lon,
    )
    subtype = ", ".join(
        f"{key}={tags[key]}"
        for key in ("amenity", "shop", "tourism", "historic", "craft", "cuisine")
        if tags.get(key)
    )
    phone = tags.get("contact:phone") or tags.get("phone")
    website = tags.get("contact:website") or tags.get("website")
    description = tags.get("description:ko") or tags.get("description") or tags.get("inscription")
    fields = [
        ("설명", description),
        ("OpenStreetMap 분류", subtype),
        ("기준 관광지에서 직선거리", f"{distance}m"),
        ("주소", format_osm_address(tags)),
        ("대표 메뉴·종류", tags.get("cuisine")),
        ("전화", phone),
        ("웹사이트", website),
        ("영업시간", tags.get("opening_hours")),
        ("휠체어 접근", tags.get("wheelchair")),
        ("좌표", f"{lat}, {lon}"),
    ]
    body = "\n\n".join(f"[{label}]\n{value}" for label, value in fields if value)

    record = make_record(
        title=f"{destination.name} | {category} | {normalize_space(name)}",
        body=body,
        source_url=OSM_PAGE_URL.format(type=osm_type, id=osm_id),
        source_name="OpenStreetMap",
        license_note=OSM_LICENSE_NOTE,
    )
    record["거리_m"] = distance
    record["위도"] = lat
    record["경도"] = lon
    record = enrich_record(
        record,
        destination=destination,
        category=category,
        volatile=volatile,
        method=f"OpenStreetMap Overpass {destination.radius_m}m 반경 검색",
    )
    record["검수상태"] = "사용자 기여 지도정보: 방문·제휴 전 공식 채널과 현장 재확인 필요"
    return record


def crawl_nearby(session, destination: Destination) -> list[dict]:
    last_error: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = session.get(
                endpoint,
                params={"data": build_nearby_query(destination)},
                headers={"Accept": "application/json"},
                timeout=60,
            )
            response.raise_for_status()
            if not getattr(response, "from_cache", False):
                polite_sleep()
            elements = response.json().get("elements", [])
            records = []
            seen: set[tuple[str, int]] = set()
            for element in elements:
                key = (element.get("type"), element.get("id"))
                if key in seen:
                    continue
                seen.add(key)
                tags = element.get("tags") or {}
                # 프랜차이즈 편의점이 지역 명물 후보를 밀어내지 않게 제외합니다.
                # 시장·전문점·독립 상점은 그대로 남깁니다.
                if tags.get("shop") == "convenience" or tags.get("shop") in IRRELEVANT_SHOP_TYPES:
                    continue
                record = nearby_record(destination, element)
                if record:
                    records.append(record)

            records.sort(key=lambda record: record.get("거리_m", 0))
            selected = []
            counts: dict[str, int] = {}
            for record in records:
                category = record.get("카테고리", "")
                limit = NEARBY_CATEGORY_LIMITS.get(category, 50)
                if counts.get(category, 0) >= limit:
                    continue
                counts[category] = counts.get(category, 0) + 1
                selected.append(record)
            return selected
        except Exception as error:
            last_error = error
            print(f"    [경고] Overpass 인스턴스 실패, 다음 주소로 전환: {endpoint} ({error})")
    if last_error:
        raise last_error
    return []


def collect_existing_trace_records(base_dir: Path = BASE_DIR) -> list[dict]:
    """기존 설화·민속 결과를 읽어 두 관광지와 직접 관련된 항목만 재분류합니다."""
    records: list[dict] = []
    for filename in ("nculture.json", "folkency.json", "encykorea.json"):
        path = base_dir / "output" / filename
        if not path.exists():
            continue
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"  [경고] 기존 결과를 읽지 못했습니다: {path} ({error})")
            continue

        for original in existing:
            haystack = f"{original.get('제목', '')}\n{original.get('본문', '')}"
            for destination in DESTINATIONS.values():
                if not any(term in haystack for term in destination.match_terms):
                    continue
                copied = dict(original)
                copied["제목"] = f"{destination.name} | 설화·민속·관련 역사 | {original.get('제목', '')}"
                enrich_record(
                    copied,
                    destination=destination,
                    category="설화·민속·관련 역사",
                    volatile=False,
                    method=f"기존 TRACE {filename} 결과 재분류",
                )
                records.append(copied)
                break
    return records


def main() -> None:
    install_cache()
    session = new_session()
    records: list[dict] = []
    failed = 0

    print(f"[1/3] 선별한 공식·공공 페이지 {len(PAGE_SOURCES)}건을 수집합니다...")
    for index, source in enumerate(PAGE_SOURCES, start=1):
        print(f"  [{index}/{len(PAGE_SOURCES)}] {source.title_hint}")
        try:
            record = crawl_page(session, source)
            if record:
                records.append(record)
            else:
                failed += 1
                print("    [경고] 본문 또는 필수 키워드를 찾지 못했습니다.")
        except Exception as error:
            failed += 1
            print(f"    [오류] {error}")

    print("[2/3] OpenStreetMap에서 주변 가게·음식·명소를 찾습니다...")
    for destination in DESTINATIONS.values():
        try:
            nearby = crawl_nearby(session, destination)
            records.extend(nearby)
            print(f"  -> {destination.name}: 반경 {destination.radius_m}m 내 {len(nearby)}건")
        except Exception as error:
            failed += 1
            print(f"  [오류] {destination.name} 반경 검색 실패: {error}")

    print("[3/3] 기존 TRACE 설화·민속 결과에서 직접 관련 자료를 합칩니다...")
    reused = collect_existing_trace_records()
    records.extend(reused)
    print(f"  -> {len(reused)}건 재분류")

    out_path = save_records(
        records,
        "destinations.json",
        # 어느 출처든 실패하면 지난 정상 레코드를 보존합니다.
        merge_existing=failed > 0,
    )
    report_and_exit(out_path, len(records), failed, min_success=2)


if __name__ == "__main__":
    main()
