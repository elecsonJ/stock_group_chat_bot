import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from data_fetcher.premium_crawler import PremiumCrawler
from db_manager import DBManager


class ControlledCrawler(PremiumCrawler):
    def __init__(self, db: DBManager, archive_dir: str, top_rows=None, search_rows=None, rss_rows=None):
        super().__init__(db=db, archive_dir=archive_dir)
        self._top_rows = list(top_rows or [])
        self._search_rows = list(search_rows or [])
        self._rss_rows = list(rss_rows or [])

    async def _fetch_nyt_topstories(self, min_published_utc=None):
        self.db.record_news_ingest_attempt(
            "NYT-TopStories",
            status="success" if self._top_rows else "no_data",
            item_count=len(self._top_rows),
            success_at=datetime.now(timezone.utc).isoformat() if self._top_rows else None,
        )
        return list(self._top_rows)

    async def _fetch_nyt_articlesearch(self, window_start_utc, window_end_utc):
        self.db.record_news_ingest_attempt(
            "NYT-ArticleSearch",
            status="success" if self._search_rows else "no_data",
            item_count=len(self._search_rows),
            success_at=datetime.now(timezone.utc).isoformat() if self._search_rows else None,
        )
        return list(self._search_rows)

    async def _fetch_rss_articles(self, min_published_utc=None):
        self.db.record_news_ingest_attempt(
            "RSS",
            status="success" if self._rss_rows else "no_data",
            item_count=len(self._rss_rows),
            success_at=datetime.now(timezone.utc).isoformat() if self._rss_rows else None,
        )
        return list(self._rss_rows)


class PremiumCrawlerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.archive_dir = os.path.join(self.tmpdir.name, "archive")
        self.db = DBManager(db_path=self.db_path)

    def tearDown(self):
        self.db.conn.close()
        self.tmpdir.cleanup()

    def _article(self, *, title: str, url: str, summary: str, published_at: str, source: str = "NYT"):
        crawler = PremiumCrawler(db=self.db, archive_dir=self.archive_dir)
        published_dt = crawler._parse_dt(published_at)
        return crawler._normalize_article(
            source=source,
            source_type="api",
            section="test",
            title=title,
            url=url,
            summary=summary,
            published_dt=published_dt,
            raw_json={},
            fetched_at_utc=datetime.now(timezone.utc),
        )

    def test_pipeline_does_not_advance_success_checkpoint_when_no_articles(self):
        self.db.save_news_ingest_checkpoint(
            "news_pipeline",
            "2026-04-05T10:00:00+00:00",
            {"saved_articles": 3},
        )
        crawler = ControlledCrawler(db=self.db, archive_dir=self.archive_dir)

        asyncio.run(crawler.execute_daily_scrape())

        checkpoint = self.db.get_news_ingest_checkpoint("news_pipeline")
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["last_success_at"], "2026-04-05T10:00:00+00:00")
        self.assertEqual(checkpoint["last_status"], "no_data")
        self.assertEqual(checkpoint["last_item_count"], 0)

    def test_dedup_articles_uses_content_hash_second_pass(self):
        crawler = PremiumCrawler(db=self.db, archive_dir=self.archive_dir)
        first = self._article(
            title="NVIDIA wins AI contract",
            url="https://example.com/a",
            summary="NVIDIA wins major AI contract",
            published_at="2026-04-05T10:00:00+00:00",
            source="NYT",
        )
        second = self._article(
            title="NVIDIA wins AI contract",
            url="https://mirror.example.com/a",
            summary="NVIDIA wins major AI contract",
            published_at="2026-04-05T10:05:00+00:00",
            source="Reuters-Markets",
        )

        rows = crawler._dedup_articles([first, second])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "Reuters-Markets")

    def test_cluster_reuses_existing_event_key_for_similar_event(self):
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EXISTINGKEY1234",
                    "date": "2026-04-05",
                    "title": "NVIDIA wins major AI server contract",
                    "summary": "NVIDIA major AI server contract sources=2 articles=2 keywords=nvidia,contract,server",
                    "source_count": 2,
                    "article_count": 2,
                    "confidence": 0.81,
                    "sample_urls": ["https://www.reuters.com/example"],
                }
            ]
        )
        crawler = PremiumCrawler(db=self.db, archive_dir=self.archive_dir)
        article = self._article(
            title="NVIDIA wins major AI server contract amid demand surge",
            url="https://example.com/new",
            summary="NVIDIA secures AI server contract as demand jumps",
            published_at="2026-04-06T00:20:00+00:00",
            source="NYT",
        )

        events, _articles = crawler._cluster_events([article])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_key"], "EXISTINGKEY1234")


if __name__ == "__main__":
    unittest.main()
