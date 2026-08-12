"""
crawler_nculture.py
=====================
지역N문화(ncms.nculture.org) 크롤러.

[찾아낸 것] 진짜 검색 API가 있었습니다
--------------------------------------
개별 설화 페이지(예: https://ncms.nculture.org/traditional-stories/story/748)는
겉보기엔 자바스크립트로 그려지는 SPA이지만, 그 화면이 실제로 내부적으로 호출하는
공개 JSON API가 두 개 있습니다.

1) 검색:  POST https://ncms.nculture.org/api/v1/stories
          쿼리 파라미터: reSearchKeyword(검색어), page, pageSize, pageIndexSize,
                       theme=traditional-stories
          -> 응답의 list_data.total_count / list_data.list 에서 검색어에 걸리는
             이야기들의 id 목록을 얻을 수 있습니다.
          (참고: list_data.list는 항목이 페이지 경계에 걸치면 PHP 배열이 JSON
           객체로 직렬화되어 {"3": {...}, "4": {...}} 형태로 올 때가 있습니다.
           그래서 파싱할 때 dict/list 둘 다 처리합니다.)

2) 상세:  GET https://ncms.nculture.org/api/v1/story/{id}
          -> 응답의 content.title / content.body1(HTML)에 이야기 전문이 들어있습니다.

즉 "여수"/"순천" 키워드로 1)을 호출해 관련 이야기 id를 모두 찾아낸 뒤, 각 id마다
2)를 호출해 본문을 받아오면 사람이 태그 URL이나 이야기 URL을 일일이 찾아 넣지
않아도 전체 과정이 자동으로 끝납니다. 로그인이 필요 없고 robots.txt도 전체
허용(Disallow 없음)이라 접근에 문제가 없습니다.

검색은 제목뿐 아니라 본문 전체를 대상으로 하므로 완도·신안처럼 무관한 지역이
섞여 나올 수 있습니다(예: 다른 지역 이야기의 참고문헌에 "여수시사"가 인용된 경우).
그래서 저장 직전에 항상 filter_by_region()으로 제목+본문에 "여수"/"순천"이
실제로 있는 레코드만 남깁니다.

사용법:
    `py -3 crawler_nculture.py` 실행하면 REGION_KEYWORDS(config.py)에 정의된
    "여수"/"순천" 키워드로 자동 검색 -> 상세 수집 -> 필터링 -> 저장까지 전부
    수행합니다. 별도로 URL을 채워 넣을 필요가 없습니다.
"""

import sys

from bs4 import BeautifulSoup

from config import (
    REGION_KEYWORDS,
    filter_by_region,
    install_cache,
    make_record,
    new_session,
    polite_sleep,
    report_and_exit,
    save_records,
)

SOURCE_NAME = "지역N문화"
THEME = "traditional-stories"

# TODO: 정확한 라이선스/이용조건은 nculture.org 하단의 저작권 안내를 확인해 교체하세요.
LICENSE_NOTE_TEMPLATE = (
    "[이용조건 개별 확인 필요] "
    "[출처 : {title} - 지역N문화(한국문화원연합회)]"
)

STORIES_SEARCH_URL = "https://ncms.nculture.org/api/v1/stories"
STORY_DETAIL_URL_TMPL = "https://ncms.nculture.org/api/v1/story/{id}"
STORY_PAGE_URL_TMPL = "https://ncms.nculture.org/traditional-stories/story/{id}"

SEARCH_PAGE_SIZE = 50
MAX_SEARCH_PAGES = 200


def discover_story_ids(session, keywords: tuple[str, ...] = REGION_KEYWORDS) -> list[int]:
    """
    검색 키워드마다 /api/v1/stories를 페이지네이션하며 호출해 매칭되는
    이야기 id를 전부 모읍니다(중복은 자동 제거).
    """
    ids: set[int] = set()

    for keyword in keywords:
        page = 1
        while page <= MAX_SEARCH_PAGES:
            resp = session.post(
                STORIES_SEARCH_URL,
                params={
                    "reSearchKeyword": keyword,
                    "page": page,
                    "pageSize": SEARCH_PAGE_SIZE,
                    "pageIndexSize": 10,
                    "theme": THEME,
                },
                timeout=15,
            )
            resp.raise_for_status()
            if not getattr(resp, "from_cache", False):
                polite_sleep()

            list_data = resp.json().get("list_data", {})
            try:
                total = int(list_data.get("total_count", 0))
            except (TypeError, ValueError):
                total = 0
            items = list_data.get("list", {})
            # 페이지 경계에 걸치면 PHP -> JSON 직렬화 특성상 리스트가 아니라
            # {"3": {...}, "4": {...}} 형태의 객체로 올 수 있어 둘 다 처리합니다.
            items = items.values() if isinstance(items, dict) else items

            found_this_page = 0
            for item in items:
                story_id = item.get("id")
                if story_id is not None:
                    ids.add(story_id)
                    found_this_page += 1

            if found_this_page == 0 or page * SEARCH_PAGE_SIZE >= total:
                break
            page += 1
        else:
            raise RuntimeError(
                f"검색 페이지 안전 상한({MAX_SEARCH_PAGES})에 도달했습니다: "
                f"keyword={keyword}"
            )

    return sorted(ids)


def parse_story_detail(content: dict) -> tuple[str, str]:
    """
    /api/v1/story/{id} 응답의 content 객체에서 (제목, 본문)을 뽑아냅니다.
    body1은 <h2>소제목</h2><p>문단</p>... 형태의 HTML입니다. 첫 <h2>는 보통
    이야기 제목과 같아서(제목 필드와 중복) 건너뛰고, 그 외 소제목은
    "[소제목]" 형태로 표시해 어느 절에서 나온 내용인지 알아볼 수 있게 합니다.
    이미지 삽입 자리표시자({{ image0:align=one }})나 빈 문단(&nbsp;만 있는 등)은
    건너뜁니다.
    """
    title = (content.get("title") or "").strip()
    html = content.get("body1") or ""
    soup = BeautifulSoup(html, "lxml")

    parts = []
    skipped_title_heading = False
    for el in soup.find_all(["h2", "p"]):
        text = el.get_text(strip=True).replace("\xa0", " ").strip()
        if not text or (text.startswith("{{") and text.endswith("}}")):
            continue

        if el.name == "h2":
            if not skipped_title_heading and text == title:
                skipped_title_heading = True
                continue
            parts.append(f"[{text}]")
        else:
            parts.append(text)

    return title, "\n\n".join(parts)


def crawl_one_story(session, story_id: int) -> dict | None:
    resp = session.get(STORY_DETAIL_URL_TMPL.format(id=story_id), timeout=15)
    resp.raise_for_status()

    if not getattr(resp, "from_cache", False):
        polite_sleep()

    content = resp.json().get("content")
    if not content:
        return None

    title, body = parse_story_detail(content)
    if not title or not body:
        return None

    return make_record(
        title=title,
        body=body,
        source_url=STORY_PAGE_URL_TMPL.format(id=story_id),
        source_name=SOURCE_NAME,
        license_note=LICENSE_NOTE_TEMPLATE.format(title=title),
        data_type="text",
    )


def main():
    install_cache(extra_allowable_methods=("POST",))  # 검색 API가 POST + 쿼리파라미터 방식
    session = new_session()

    print(f"[안내] 검색 API로 {', '.join(REGION_KEYWORDS)} 관련 설화를 자동 탐색합니다...")
    try:
        story_ids = discover_story_ids(session)
    except Exception as e:
        print(f"[오류] 검색 단계에서 문제가 발생했습니다: {e}")
        sys.exit(1)
    print(f"  -> {len(story_ids)}개의 후보 이야기 발견")

    all_records: list[dict] = []
    failed = 0
    for i, story_id in enumerate(story_ids, start=1):
        print(f"[{i}/{len(story_ids)}] 이야기 상세 수집 중: story/{story_id}")
        try:
            record = crawl_one_story(session, story_id)
            if record:
                all_records.append(record)
        except Exception as e:
            failed += 1
            print(f"  [오류] story/{story_id} 처리 중 문제 발생: {e}")

    kept, dropped = filter_by_region(all_records)
    if dropped:
        print(f"  [필터] 여수/순천과 무관한 {dropped}건은 저장하지 않았습니다.")

    out_path = save_records(
        kept, "nculture.json", merge_existing=failed > 0
    )
    report_and_exit(out_path, len(kept), failed)


if __name__ == "__main__":
    main()
