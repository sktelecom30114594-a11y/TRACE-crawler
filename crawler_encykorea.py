"""
crawler_encykorea.py
=====================
한국민족문화대백과사전(encykorea.aks.ac.kr) 크롤러.

이 사이트는 정적 HTML로 렌더링되므로(자바스크립트 불필요) requests + BeautifulSoup
조합만으로 충분합니다. 개별 항목 페이지(/Article/{id}) 접근은 robots.txt가 허용합니다.

[중요] 이 크롤러는 nculture/folkency와 달리 자동 검색을 쓰지 않습니다
------------------------------------------------------------------
nculture.org, folkency.nfm.go.kr에는 사이트 자체 검색 API가 있어 "여수"/"순천"
키워드로 대상 항목을 자동으로 찾아내도록 만들었습니다. encykorea.aks.ac.kr도
같은 방식을 시도해봤지만, robots.txt를 확인해보니

    Disallow: /Article/Search
    Disallow: /Media/Search

로 검색 결과 페이지 자체를 명시적으로 크롤링 금지하고 있었습니다. 사이트맵
(sitemap.xml)으로 전체 항목을 순회하는 방법도 있지만, 항목이 수만 건이라 그중
극소수인 여수/순천 관련 항목을 찾자고 사이트 전체를 훑는 건 과도한 요청이라
판단했습니다. 그래서 이 크롤러는 TARGET_URLS에 넣은 개별 항목만 가져오는
방식을 유지합니다 - 대상 URL은 사람이 사이트에서 직접 찾아 채워야 합니다
(브라우저로 검색하는 것 자체는 문제 없음. 자동화된 요청만 피하는 것).

[사전에 확인한 실제 페이지 구조] (예: https://encykorea.aks.ac.kr/Article/E0036392 "여수시")
- <meta property="og:title" content="여수시"> 처럼 og:title 메타태그에 깨끗한 항목명이 들어있음
- 본문은 여러 개의 <section id="sect1" class="content_section"> ... <section id="sect10" ...>
  로 나뉘어 있고, 각 section 안에는
    <div class="section_tit"><h3 class="tit">설화 · 민요</h3>...</div>
    <div class="detail">...(<p>텍스트</p>가 여러 개)...</div>
  형태로 들어있음.
- 섹션 번호(sect1, sect2, ...)는 항목마다 다를 수 있으므로 번호로 찾지 않고
  h3.tit의 텍스트에 "설화"가 포함되는 section을 찾는 방식을 사용합니다.
- "설화 · 민요" 섹션이 아예 없는 항목(인물 항목 등)도 있으므로, 없으면 조용히 건너뜁니다.

사용법:
    1) TARGET_URLS 리스트에 수집하고 싶은 encykorea 항목 URL을 채워 넣습니다.
       (여수/순천 관련 지역 항목, 특히 "설화 · 민요" 섹션이 있는 시/군/읍/면 단위
        개관 항목을 위주로 넣으면 됩니다.)
    2) `py -3 crawler_encykorea.py` 로 실행합니다.
    3) 결과는 output/encykorea.json 에 저장됩니다.
"""

import sys

from bs4 import BeautifulSoup

from config import (
    filter_by_region,
    install_cache,
    make_record,
    new_session,
    polite_sleep,
    report_and_exit,
    save_records,
)

SOURCE_NAME = "한국민족문화대백과사전"

# 공공누리 이용조건에 따라 본문을 인용할 때 반드시 붙여야 하는 출처 표기 문구입니다.
# (사용자가 직접 확인해 알려준 형식 그대로 사용)
LICENSE_NOTE_TEMPLATE = "[출처 : {title} - 한국민족문화대백과사전]"

# TODO: 여기에 실제로 수집할 encykorea 항목(여수/순천 관련) URL을 채워 넣으세요.
# "여수시" 항목(E0036392)은 실제로 여수를 다루므로 filter_by_region을 통과하지만,
# 다른 지역 항목을 넣을 때를 대비해 필터는 항상 적용합니다.
TARGET_URLS = [
    "https://encykorea.aks.ac.kr/Article/E0036392",  # 여수시
    "https://encykorea.aks.ac.kr/Article/E0031987",  # 순천시(전라남도)
]


def find_legend_section(soup: BeautifulSoup):
    """
    <section class="content_section"> 들 중에서 제목(h3.tit)에 "설화"가 포함된
    섹션을 찾아 그 section 태그를 반환합니다. 없으면 None.
    """
    for section in soup.select("section.content_section"):
        heading = section.select_one(".section_tit h3.tit")
        if heading and "설화" in heading.get_text(strip=True):
            return section
    return None


def extract_article_title(soup: BeautifulSoup) -> str:
    """og:title 메타태그에서 깨끗한 항목명을 가져옵니다. 없으면 <title> 태그로 대체."""
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        return og_title["content"].strip()

    title_tag = soup.find("title")
    if title_tag:
        # <title>여수시 - 한국민족문화대백과사전</title> 형태이므로 ' - ' 앞부분만 사용
        return title_tag.get_text(strip=True).split(" - ")[0]

    return ""


def extract_section_text(section) -> str:
    """section 안의 div.detail에 들어있는 <p> 문단들을 이어붙여 하나의 본문 문자열로 만듭니다."""
    detail = section.select_one(".detail")
    if not detail:
        return ""
    paragraphs = [p.get_text(strip=True) for p in detail.find_all("p")]
    paragraphs = [p for p in paragraphs if p]  # 빈 문단 제거
    return "\n\n".join(paragraphs)


def crawl_one(session, url: str) -> dict | None:
    resp = session.get(url, timeout=15)
    resp.raise_for_status()

    # 캐시에서 바로 가져온 응답이면(from_cache=True) 실제로 서버에 부담을 주지
    # 않았으므로 딜레이를 줄 필요가 없습니다. 실제 네트워크 요청이 나간 경우에만 대기합니다.
    if not getattr(resp, "from_cache", False):
        polite_sleep()

    soup = BeautifulSoup(resp.text, "lxml")

    title = extract_article_title(soup)
    section = find_legend_section(soup)
    if section is None:
        print(f"  [건너뜀] '설화' 섹션이 없는 항목: {title} ({url})")
        return None

    body = extract_section_text(section)
    if not body:
        print(f"  [건너뜀] 설화 섹션은 있지만 본문이 비어있음: {title} ({url})")
        return None

    return make_record(
        title=title,
        body=body,
        source_url=url,
        source_name=SOURCE_NAME,
        license_note=LICENSE_NOTE_TEMPLATE.format(title=title),
        data_type="text",
    )


def main():
    if not TARGET_URLS:
        print(
            "[안내] TARGET_URLS가 비어 있습니다. 여수/순천 관련 encykorea 항목 URL을 "
            "채워 넣은 뒤 다시 실행하세요."
        )
        sys.exit(1)

    install_cache()
    session = new_session()

    records = []
    failed = 0
    for i, url in enumerate(TARGET_URLS, start=1):
        print(f"[{i}/{len(TARGET_URLS)}] 수집 중: {url}")
        try:
            record = crawl_one(session, url)
            if record:
                records.append(record)
        except Exception as e:
            failed += 1
            print(f"  [오류] {url} 처리 중 문제 발생: {e}")

    kept, dropped = filter_by_region(records)
    if dropped:
        print(f"  [필터] 여수/순천과 무관한 {dropped}건은 저장하지 않았습니다.")

    out_path = save_records(kept, "encykorea.json")
    report_and_exit(out_path, len(kept), failed)


if __name__ == "__main__":
    main()
