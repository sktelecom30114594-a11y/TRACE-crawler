import unittest

from bs4 import BeautifulSoup

import api_grandculture
import crawler_encykorea
import crawler_folkency
import crawler_nculture


class EncyKoreaParserTests(unittest.TestCase):
    def test_extracts_title_and_legend_section(self):
        soup = BeautifulSoup(
            """
            <html><head><meta property="og:title" content="여수시"></head>
            <body>
              <section class="content_section">
                <div class="section_tit"><h3 class="tit">역사</h3></div>
              </section>
              <section class="content_section">
                <div class="section_tit"><h3 class="tit">설화 · 민요</h3></div>
                <div class="detail"><p>첫 문단</p><p>둘째 문단</p></div>
              </section>
            </body></html>
            """,
            "lxml",
        )

        section = crawler_encykorea.find_legend_section(soup)
        self.assertEqual(crawler_encykorea.extract_article_title(soup), "여수시")
        self.assertEqual(
            crawler_encykorea.extract_section_text(section), "첫 문단\n\n둘째 문단"
        )


class FolkEncyParserTests(unittest.TestCase):
    def test_extracts_topic_sequence_from_localized_url(self):
        url = "https://folkency.nfm.go.kr/KR/topic/detail/6570"
        self.assertEqual(crawler_folkency.extract_topic_seq(url), "6570")

    def test_rejects_url_without_sequence(self):
        self.assertIsNone(
            crawler_folkency.extract_topic_seq("https://folkency.nfm.go.kr/topic")
        )

    def test_crawl_excludes_bibliography_section(self):
        class Response:
            from_cache = True

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "result_code": "200",
                    "data": {
                        "topic": {"dic_subject_kr": "여수 설화"},
                        "content": [
                            {
                                "dic_subname": "내용",
                                "dic_content_plain_text": "여수의 이야기다.",
                            },
                            {
                                "dic_subname": "참고문헌",
                                "dic_content_plain_text": "저장하면 안 되는 참고문헌",
                            },
                        ],
                    },
                }

        class Session:
            def post(self, *args, **kwargs):
                return Response()

        result = crawler_folkency.crawl_one(
            Session(), "https://folkency.nfm.go.kr/topic/detail/1"
        )

        self.assertIn("여수의 이야기다.", result["본문"])
        self.assertNotIn("참고문헌", result["본문"])
        self.assertNotIn("저장하면 안 되는", result["본문"])


class NCultureParserTests(unittest.TestCase):
    def test_parses_story_body_and_skips_duplicate_heading_and_image_marker(self):
        title, body = crawler_nculture.parse_story_detail(
            {
                "title": "오동도 이야기",
                "body1": """
                    <h2>오동도 이야기</h2>
                    <p>{{ image0:align=one }}</p>
                    <h2>유래</h2>
                    <p>여수에 전해지는 이야기다.</p>
                """,
            }
        )

        self.assertEqual(title, "오동도 이야기")
        self.assertEqual(body, "[유래]\n\n여수에 전해지는 이야기다.")


class GrandCultureConfigurationTests(unittest.TestCase):
    def test_skeleton_refuses_to_send_request(self):
        with self.assertRaises(NotImplementedError):
            api_grandculture._ensure_configured()


if __name__ == "__main__":
    unittest.main()
