"""
crawler_folkency.py
=====================
한국민속대백과사전(folkency.nfm.go.kr, 국립민속박물관) 크롤러.

[중요] 원래 계획을 바꾼 이유
----------------------------
처음에는 "requests로는 빈 본문만 나온다 -> Playwright로 브라우저를 띄워서 긁자"는
계획이었습니다. 실제로 페이지 HTML(view-source)을 보면 <div id="app"></div> 뿐이고
콘텐츠는 전부 자바스크립트(Vue.js)가 그려주는 SPA(Single Page Application)입니다.

그런데 그 자바스크립트 번들(main.js)을 직접 열어보니, 화면에 있는 Vue 컴포넌트가
사실은 아래의 "내부 API"를 호출해서 JSON을 받아온 뒤 화면에 그려주고 있었습니다.

    POST https://folkency.nfm.go.kr/api/topic/detail
    Content-Type: application/json
    Body: {"topicSeq": 6570}

이 API는 브라우저가 페이지를 그릴 때 실제로 호출하는 "화면에 보이는 것과 완전히
동일한 공개 데이터"를 돌려줍니다. 로그인/인증이 전혀 필요 없고, robots.txt도
모든 경로(/)를 허용하고 있어(Disallow 없음) 접근에 문제가 없습니다.

이 방식이 Playwright(헤드리스 브라우저)보다 나은 점:
- 무거운 브라우저를 띄우지 않아도 되어 훨씬 빠르고 가볍습니다.
  (Playwright는 크로미움 브라우저 바이너리(수백MB)를 따로 설치해야 함)
- 응답이 이미 정제된 JSON이라 HTML 태그를 걷어낼 필요가 없습니다.
- 서버 입장에서도 CSS/JS/이미지까지 전부 내려받는 완전한 페이지 로딩보다
  필요한 데이터 하나만 요청하는 쪽이 훨씬 가벼운 요청입니다.

만약 이 API가 나중에 막히거나 응답 구조가 바뀐다면(개편 등), 브라우저 개발자
도구(F12) > Network 탭을 열고 folkency 항목 페이지를 새로고침해서 어떤 요청이
나가는지 다시 확인하거나, Playwright로 우회하는 방법을 쓰면 됩니다.

[API 응답 구조] (topic_seq=6570 "문배" 항목으로 확인)
{
  "result_code": "200",
  "data": {
    "topic": {
      "dic_subject_kr": "문배",      # 항목명(제목)
      "category_name": "민화",
      "dic_name": "한국민속예술사전",   # 어떤 사전(하위 백과)에서 나온 항목인지
      ...
    },
    "content": [                    # 본문은 여러 하위 섹션으로 나뉘어 있음
      {
        "dic_subname": "정의",             # 섹션 이름(정의/내용/유래 등, 항목마다 다름)
        "dic_content_plain_text": "..."    # 이미 HTML 태그가 제거된 순수 텍스트
      },
      ...
    ]
  }
}

[찾아낸 것] 여기도 진짜 검색 API가 있었습니다
------------------------------------------------
main.js 번들을 더 살펴보니 사이트 검색창이 실제로 호출하는 Elasticsearch 기반
검색 API도 찾았습니다.

    POST https://folkency.nfm.go.kr/api/v1/_search/{index}
    Body: {"keyword": "여수", "page": 1, "size": 50}

인덱스는 두 종류를 쓸 수 있습니다.
- nfmw_topic_2025   : 표제어(제목) 자체에 검색어가 있는 항목만. 적지만(여수/순천
                      합쳐 10건 안팎) 항목 자체가 그 지역 것이라 정확도가 높습니다.
- nfmw_content_2025 : 본문 어디든(정의/유래/참고문헌/지역사례 비교 등) 검색어가
                      있으면 매칭. 200건 넘게 나오지만 상당수가 "사처방"(혼례
                      용어), "논매는 소리"(농요) 같은 전국 공통 민속 개념이
                      "[지역사례]" 절에서 여러 지역과 나란히 "여수 지역에서는~"
                      한 줄 언급된 것뿐이라, 실제로는 여수/순천 고유 항목이
                      아닙니다. 참고문헌 섹션을 본문/필터에서 제외해도 이 문제는
                      해결되지 않습니다(지역사례 언급 자체가 본문 안에 있으므로).

그래서 기본값은 정확도가 높은 nfmw_topic_2025(표제어)만 씁니다. 재현율을
높이고 싶다면(전국구 개념 속 지역사례 언급까지 원한다면) 아래 SEARCH_INDEXES에
"nfmw_content_2025"를 추가하세요 - 다만 그 경우 결과를 사람이 다시 훑어봐야
할 가능성이 높습니다.

응답의 data.hits[].topic_seq 로 매칭된 항목의 id를 얻고, 그 id로 기존
/api/topic/detail을 호출해 전문을 받으면 됩니다. 마지막에는 항상
filter_by_region()으로 실제 제목+본문에 "여수"/"순천"이 있는 것만 남깁니다.

사용법:
    `py -3 crawler_folkency.py` 실행하면 REGION_KEYWORDS(config.py)로 자동
    검색 -> 상세 수집 -> 필터링 -> 저장까지 전부 수행합니다. 특정 항목만 다시
    받고 싶으면 TARGET_URLS에 URL을 채워 넣으면 자동 검색 대신 그 목록만
    사용합니다.
"""

import re
import sys

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

SOURCE_NAME = "한국민속대백과사전"

# 공식 자료 이용 안내에서 텍스트의 공공누리 제2유형과 출처 형식을 확인했습니다.
# 사진·동영상은 개별 저작권이 다르므로 이 크롤러에서는 수집하지 않습니다.
LICENSE_NOTE_TEMPLATE = (
    "공공누리 제2유형(출처표시+상업적 이용금지) "
    "[출처: {title}–국립민속박물관 한국민속대백과사전]"
)

API_URL = "https://folkency.nfm.go.kr/api/topic/detail"
SEARCH_API_URL_TMPL = "https://folkency.nfm.go.kr/api/v1/_search/{index}"
# 표제어(제목) 인덱스만 기본으로 사용합니다. nfmw_content_2025(본문 전체)를 추가하면
# 후보가 훨씬 많아지지만, "사처방"처럼 전국구 개념이 [지역사례] 절에서 여수/순천을
# 다른 지역과 나란히 스쳐 언급한 것까지 섞여 들어와 정확도가 크게 떨어집니다.
SEARCH_INDEXES = ("nfmw_topic_2025",)
SEARCH_PAGE_SIZE = 50
MAX_SEARCH_PAGES = 200

# 본문에는 포함하지 않는 섹션. "참고문헌"은 서술 내용이 아니라 인용 도서 목록이라
# 예를 들어 "여수・순천 민요집"처럼 무관한 전국구 항목의 참고문헌에 지명이 우연히
# 들어가는 경우가 많습니다. 이 섹션을 그대로 포함하면 filter_by_region()이
# 참고문헌 인용만 보고도 통과시켜버려 사실상 안전장치가 무력화됩니다.
EXCLUDED_SECTIONS = {"참고문헌"}

# topic_seq 추출용 정규식: URL 어디에 있든 "/detail/숫자" 패턴을 찾음
TOPIC_SEQ_PATTERN = re.compile(r"/detail/(\d+)")

# 비워두면 REGION_KEYWORDS로 자동 검색합니다. 특정 항목만 다시 수집하고 싶을 때만
# 여기에 URL을 직접 채워 넣으세요(자동 검색을 건너뛰고 이 목록만 사용합니다).
#   "https://folkency.nfm.go.kr/topic/detail/6570",  # 구조 확인용 샘플 (문배)
TARGET_URLS: list[str] = []


def discover_topic_seqs(session, keywords: tuple[str, ...] = REGION_KEYWORDS) -> list[int]:
    """
    SEARCH_INDEXES에 설정된 인덱스에서 키워드를 검색해 매칭되는 topic_seq를
    모두 모읍니다(중복 제거). 기본값은 정확도가 높은 표제어 인덱스 하나입니다.
    본문 인덱스를 추가하면 지역 사례가 잠깐 언급된 전국 공통 항목까지 포함될 수
    있으므로 결과를 별도로 검수해야 합니다.
    """
    seqs: set[int] = set()

    for index in SEARCH_INDEXES:
        url = SEARCH_API_URL_TMPL.format(index=index)
        for keyword in keywords:
            page = 1
            while page <= MAX_SEARCH_PAGES:
                resp = session.post(
                    url,
                    json={"keyword": keyword, "page": page, "size": SEARCH_PAGE_SIZE},
                    headers={"Content-Type": "application/json"},
                    timeout=15,
                )
                resp.raise_for_status()
                if not getattr(resp, "from_cache", False):
                    polite_sleep()

                data = resp.json().get("data", {})
                try:
                    total = int(data.get("total", 0))
                except (TypeError, ValueError):
                    total = 0
                hits = data.get("hits", [])

                for hit in hits:
                    seq = hit.get("topic_seq")
                    # 참고문헌 섹션만 매칭된 경우는 후보에서 제외합니다(같은 topic_seq가
                    # 다른 섹션에서도 매칭되면 그 히트를 통해 정상적으로 포함됩니다).
                    if seq is not None and hit.get("dic_subname") not in EXCLUDED_SECTIONS:
                        seqs.add(seq)

                if not hits or page * SEARCH_PAGE_SIZE >= total:
                    break
                page += 1
            else:
                raise RuntimeError(
                    f"검색 페이지 안전 상한({MAX_SEARCH_PAGES})에 도달했습니다: "
                    f"index={index}, keyword={keyword}"
                )

    return sorted(seqs)


def extract_topic_seq(url: str) -> str | None:
    m = TOPIC_SEQ_PATTERN.search(url)
    return m.group(1) if m else None


def crawl_one(session, url: str) -> dict | None:
    topic_seq = extract_topic_seq(url)
    if topic_seq is None:
        print(f"  [건너뜀] URL에서 topic_seq를 찾을 수 없음: {url}")
        return None

    # 이 API는 GET이 아니라 POST + JSON 바디로 topicSeq를 넘겨야 합니다.
    resp = session.post(
        API_URL,
        json={"topicSeq": int(topic_seq)},
        headers={
            "Content-Type": "application/json",
            # 사이트 자체 프론트엔드처럼 보이도록 Referer를 원본 페이지로 지정
            "Referer": url,
        },
        timeout=15,
    )
    resp.raise_for_status()

    if not getattr(resp, "from_cache", False):
        polite_sleep()

    payload = resp.json()
    if str(payload.get("result_code")) != "200":
        print(f"  [오류] API가 실패 응답을 반환함: {url} -> {payload.get('message')}")
        return None

    topic = payload["data"]["topic"]
    title = topic.get("dic_subject_kr", "")

    # content는 "정의/내용/유래" 등 여러 하위 섹션의 리스트입니다.
    # 섹션 이름과 본문을 함께 이어붙여, 어느 섹션에서 나온 내용인지 알아볼 수 있게 합니다.
    sections = payload["data"].get("content", [])
    parts = []
    for sec in sections:
        subname = (sec.get("dic_subname") or "").strip()
        text = (sec.get("dic_content_plain_text") or "").strip()
        if not text or subname in EXCLUDED_SECTIONS:
            continue
        parts.append(f"[{subname}]\n{text}" if subname else text)

    body = "\n\n".join(parts)
    if not body:
        print(f"  [건너뜀] 본문이 비어있음: {title} ({url})")
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
    # 이 API들은 POST를 쓰지만 조회 전용(검색/상세)이므로 POST 요청도 캐시 대상에 포함시킵니다.
    install_cache(extra_allowable_methods=("POST",))
    session = new_session()

    target_urls = TARGET_URLS
    if not target_urls:
        print(f"[안내] 검색 API로 {', '.join(REGION_KEYWORDS)} 관련 항목을 자동 탐색합니다...")
        try:
            topic_seqs = discover_topic_seqs(session)
        except Exception as e:
            print(f"[오류] 검색 단계에서 문제가 발생했습니다: {e}")
            sys.exit(1)
        print(f"  -> {len(topic_seqs)}개의 후보 항목 발견")
        target_urls = [
            f"https://folkency.nfm.go.kr/topic/detail/{seq}" for seq in topic_seqs
        ]

    records = []
    failed = 0
    for i, url in enumerate(target_urls, start=1):
        print(f"[{i}/{len(target_urls)}] 수집 중: {url}")
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

    # 상세 수집이 하나라도 실패했으면 기존 정상 결과를 보존하며 병합합니다.
    # 모두 성공한 전체 검색 실행만 스냅샷으로 교체해 오래된 항목을 정리합니다.
    out_path = save_records(
        kept, "folkency.json", merge_existing=failed > 0
    )
    report_and_exit(out_path, len(kept), failed)


if __name__ == "__main__":
    main()
