import asyncio
import datetime
import hashlib
import json
import os
import re
from collections import Counter
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

try:
    import requests
except Exception:  # pragma: no cover
    requests = None
try:
    import feedparser
except Exception:  # pragma: no cover
    feedparser = None

from db_manager import DBManager


class PremiumCrawler:
    """
    고품질 뉴스 수집 파이프라인:
    1) 다중 소스 수집(NYT API + RSS)
    2) URL/본문 정규화 및 중복 제거
    3) 이벤트 클러스터링
    4) DB(news_articles/news_events) + 파일(txt/json) 동시 저장
    """

    def __init__(self, db: DBManager | None = None, archive_dir: str | None = None):
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.news_archive_dir = archive_dir or os.path.join(root, "news_archive")
        os.makedirs(self.news_archive_dir, exist_ok=True)
        self.db = db or DBManager()

        self.max_per_source = max(5, int(os.getenv("NEWS_MAX_PER_SOURCE", "20")))
        self.max_events = max(5, int(os.getenv("NEWS_MAX_EVENTS", "25")))
        self.lookback_hours = max(12, int(os.getenv("NEWS_LOOKBACK_HOURS", "72")))
        self.poll_overlap_min = max(30, int(os.getenv("NEWS_POLL_OVERLAP_MIN", "120")))
        self.backfill_hours = max(24, int(os.getenv("NEWS_BACKFILL_HOURS", "48")))
        self.nyt_rate_limit_sec = max(1.0, float(os.getenv("NYT_RATE_LIMIT_SECONDS", "12.5")))
        self.request_timeout = max(5.0, float(os.getenv("NEWS_REQUEST_TIMEOUT_SEC", "12")))
        self.nyt_sections = {
            "home": "홈(종합)",
            "business": "비즈니스",
            "technology": "테크놀로지",
            "world": "세계",
            "politics": "미국정치",
            "science": "과학",
            "health": "건강",
        }
        self.source_priority = {
            "SEC-PressRelease": 5,
            "FED-Press": 5,
            "NYT": 4,
            "Reuters-Markets": 4,
            "Reuters-Business": 4,
            "Reuters-Technology": 4,
            "Reuters-World": 4,
            "NYT-Search": 3,
        }

    def _canonicalize_url(self, raw_url: str) -> str:
        if not raw_url:
            return ""
        try:
            p = urlparse(raw_url.strip())
            q = []
            for k, v in parse_qsl(p.query, keep_blank_values=True):
                lk = k.lower()
                if lk.startswith("utm_") or lk in {"gclid", "fbclid", "mc_cid", "mc_eid"}:
                    continue
                q.append((k, v))
            p = p._replace(query=urlencode(q), fragment="")
            path = re.sub(r"/+$", "", p.path or "")
            p = p._replace(path=path or "/")
            return urlunparse(p)
        except Exception:
            return raw_url

    def _hash(self, text: str) -> str:
        return hashlib.sha1((text or "").encode("utf-8")).hexdigest()

    def _article_rank(self, row: dict) -> tuple[int, str, int]:
        return (
            int(self.source_priority.get(str(row.get("source", "")).strip(), 1)),
            str(row.get("published_at", "") or ""),
            len(str(row.get("summary", "") or "")),
        )

    def _normalize_text(self, text: str) -> str:
        t = re.sub(r"<[^>]+>", " ", text or "")
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _parse_dt(self, value: str) -> datetime.datetime | None:
        if not value:
            return None
        s = str(value).strip()
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s.replace("Z", "+00:00")
            return datetime.datetime.fromisoformat(s)
        except Exception:
            pass
        try:
            return parsedate_to_datetime(s)
        except Exception:
            return None

    def _in_lookback(self, dt: datetime.datetime | None) -> bool:
        if not dt:
            return True
        now = datetime.datetime.now(datetime.timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        delta = now - dt.astimezone(datetime.timezone.utc)
        return delta.total_seconds() <= self.lookback_hours * 3600

    def _to_iso(self, dt: datetime.datetime | None) -> tuple[str, str]:
        if not dt:
            now = datetime.datetime.now(datetime.timezone.utc)
            return now.isoformat(), now.strftime("%Y-%m-%d")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        utc = dt.astimezone(datetime.timezone.utc)
        return utc.isoformat(), utc.strftime("%Y-%m-%d")

    def _parse_iso_utc(self, value: str | None) -> datetime.datetime | None:
        if not value:
            return None
        try:
            s = str(value).strip().replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
        except Exception:
            return None

    def _event_similarity(self, left: dict, right: dict) -> float:
        left_tokens = set(self._tokenize(f"{left.get('title', '')} {left.get('summary', '')}")[:16])
        right_tokens = set(self._tokenize(f"{right.get('title', '')} {right.get('summary', '')}")[:16])
        if not left_tokens or not right_tokens:
            return 0.0
        inter = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens) or 1
        sim = inter / union
        if inter >= 3:
            sim += 0.1
        return sim

    def _published_within_cluster_window(
        self,
        article_dt: datetime.datetime | None,
        cluster_dt: datetime.datetime | None,
        window_hours: int = 36,
    ) -> bool:
        if not article_dt or not cluster_dt:
            return True
        if article_dt.tzinfo is None:
            article_dt = article_dt.replace(tzinfo=datetime.timezone.utc)
        if cluster_dt.tzinfo is None:
            cluster_dt = cluster_dt.replace(tzinfo=datetime.timezone.utc)
        delta = abs((cluster_dt.astimezone(datetime.timezone.utc) - article_dt.astimezone(datetime.timezone.utc)).total_seconds())
        return delta <= window_hours * 3600

    def _align_with_existing_event_keys(self, events: list[dict]) -> list[dict]:
        existing = self.db.get_latest_news_events(limit=200)
        if not existing:
            return events

        for event in events:
            new_dt = self._parse_dt(str(event.get("date", "") or ""))
            best_match = None
            best_score = 0.0
            for old in existing:
                old_dt = self._parse_dt(str(old.get("date", "") or ""))
                if new_dt and old_dt:
                    if abs((new_dt - old_dt).days) > 2:
                        continue
                score = self._event_similarity(event, old)
                if score > best_score:
                    best_score = score
                    best_match = old
            if best_match and best_score >= 0.42:
                event["event_key"] = best_match.get("event_key", event.get("event_key"))
        return events

    def _resolve_poll_window(self) -> tuple[datetime.datetime, datetime.datetime]:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        default_start = now_utc - datetime.timedelta(hours=self.backfill_hours)
        ck = self.db.get_news_ingest_checkpoint("news_pipeline")
        last_success = self._parse_iso_utc((ck or {}).get("last_success_at", ""))
        if not last_success:
            return default_start, now_utc
        overlap_start = last_success - datetime.timedelta(minutes=self.poll_overlap_min)
        if overlap_start < default_start:
            overlap_start = default_start
        if overlap_start > now_utc:
            overlap_start = now_utc - datetime.timedelta(minutes=10)
        return overlap_start, now_utc

    def _calc_ingest_delay_sec(
        self,
        fetched_at_utc: datetime.datetime,
        published_dt: datetime.datetime | None,
    ) -> int:
        if not published_dt:
            return 0
        if published_dt.tzinfo is None:
            published_dt = published_dt.replace(tzinfo=datetime.timezone.utc)
        delta = fetched_at_utc - published_dt.astimezone(datetime.timezone.utc)
        return max(0, int(delta.total_seconds()))

    def _normalize_article(
        self,
        *,
        source: str,
        source_type: str,
        section: str,
        title: str,
        url: str,
        summary: str,
        published_dt: datetime.datetime | None,
        raw_json: dict,
        fetched_at_utc: datetime.datetime | None = None,
        min_published_utc: datetime.datetime | None = None,
        enforce_lookback: bool = False,
    ) -> dict | None:
        clean_title = self._normalize_text(title)
        clean_summary = self._normalize_text(summary)
        clean_url = self._canonicalize_url(url)
        if not clean_title or not clean_url:
            return None
        if enforce_lookback and not self._in_lookback(published_dt):
            return None
        if min_published_utc and published_dt:
            pdt = published_dt
            if pdt.tzinfo is None:
                pdt = pdt.replace(tzinfo=datetime.timezone.utc)
            if pdt.astimezone(datetime.timezone.utc) < min_published_utc:
                return None

        published_iso, date_str = self._to_iso(published_dt)
        content_hash = self._hash(f"{clean_title}|{clean_summary}")
        article_key = self._hash(f"{clean_url}|{clean_title}|{published_iso[:10]}")
        fetched_dt = fetched_at_utc or datetime.datetime.now(datetime.timezone.utc)
        fetched_iso = fetched_dt.astimezone(datetime.timezone.utc).isoformat()
        ingest_delay_sec = self._calc_ingest_delay_sec(fetched_dt, published_dt)

        return {
            "article_key": article_key,
            "date": date_str,
            "source": source,
            "source_type": source_type,
            "section": section,
            "title": clean_title,
            "url": url,
            "canonical_url": clean_url,
            "published_at": published_iso,
            "summary": clean_summary,
            "content_hash": content_hash,
            "raw_json": raw_json,
            "event_key": "",
            "fetched_at": fetched_iso,
            "ingest_delay_sec": ingest_delay_sec,
        }

    async def _fetch_nyt_topstories(self, min_published_utc: datetime.datetime | None = None) -> list[dict]:
        nyt_api_key = os.getenv("NYT_API_KEY", "").strip()
        if not nyt_api_key:
            self.db.record_news_ingest_attempt(
                "NYT-TopStories",
                status="skipped",
                error="missing_nyt_api_key",
            )
            return []
        if requests is None:
            self.db.record_news_ingest_attempt(
                "NYT-TopStories",
                status="skipped",
                error="missing_requests",
            )
            return []

        articles: list[dict] = []
        fetched_at = datetime.datetime.now(datetime.timezone.utc)
        for sec_eng, sec_kor in self.nyt_sections.items():
            url = f"https://api.nytimes.com/svc/topstories/v2/{sec_eng}.json?api-key={nyt_api_key}"
            source_name = f"NYT-Top-{sec_eng}"
            try:
                response = await asyncio.to_thread(requests.get, url, timeout=self.request_timeout)
                if response.status_code != 200:
                    self.db.record_news_ingest_attempt(
                        source_name,
                        status="error",
                        error=f"http_{response.status_code}",
                        attempted_at=fetched_at.isoformat(),
                    )
                    await asyncio.sleep(self.nyt_rate_limit_sec)
                    continue
                payload = response.json()
                rows = payload.get("results", [])[: self.max_per_source]
                added = 0
                for row in rows:
                    item = self._normalize_article(
                        source="NYT",
                        source_type="api",
                        section=sec_kor,
                        title=row.get("title", ""),
                        url=row.get("url", ""),
                        summary=row.get("abstract", "") or row.get("snippet", ""),
                        published_dt=self._parse_dt(row.get("published_date", "")),
                        raw_json={
                            "byline": row.get("byline", ""),
                            "item_type": row.get("item_type", ""),
                            "section": sec_eng,
                        },
                        fetched_at_utc=fetched_at,
                        min_published_utc=min_published_utc,
                        enforce_lookback=True,
                    )
                    if item:
                        articles.append(item)
                        added += 1
                self.db.record_news_ingest_attempt(
                    source_name,
                    status="success" if added > 0 else "no_data",
                    item_count=added,
                    cursor={"section": sec_eng},
                    success_at=fetched_at.isoformat() if added > 0 else None,
                    attempted_at=fetched_at.isoformat(),
                )
            except Exception:
                self.db.record_news_ingest_attempt(
                    source_name,
                    status="error",
                    error="exception",
                    attempted_at=fetched_at.isoformat(),
                )
            # NYT free tier rate limit 보호
            await asyncio.sleep(self.nyt_rate_limit_sec)
        self.db.record_news_ingest_attempt(
            "NYT-TopStories",
            status="success" if articles else "no_data",
            item_count=len(articles),
            success_at=fetched_at.isoformat() if articles else None,
            attempted_at=fetched_at.isoformat(),
        )
        return articles

    async def _fetch_nyt_articlesearch(
        self,
        window_start_utc: datetime.datetime,
        window_end_utc: datetime.datetime,
    ) -> list[dict]:
        nyt_api_key = os.getenv("NYT_API_KEY", "").strip()
        if not nyt_api_key:
            self.db.record_news_ingest_attempt(
                "NYT-ArticleSearch",
                status="skipped",
                error="missing_nyt_api_key",
            )
            return []
        if requests is None:
            self.db.record_news_ingest_attempt(
                "NYT-ArticleSearch",
                status="skipped",
                error="missing_requests",
            )
            return []

        start_date = window_start_utc.strftime("%Y%m%d")
        end_date = window_end_utc.strftime("%Y%m%d")
        pages = max(1, min(3, (self.max_per_source + 9) // 10))
        fetched_at = datetime.datetime.now(datetime.timezone.utc)
        all_articles: list[dict] = []
        had_error = False

        for page in range(pages):
            url = "https://api.nytimes.com/svc/search/v2/articlesearch.json"
            params = {
                "api-key": nyt_api_key,
                "begin_date": start_date,
                "end_date": end_date,
                "sort": "newest",
                "page": page,
            }
            try:
                response = await asyncio.to_thread(
                    requests.get,
                    url,
                    params=params,
                    timeout=self.request_timeout,
                )
                if response.status_code != 200:
                    had_error = True
                    await asyncio.sleep(self.nyt_rate_limit_sec)
                    continue
                docs = ((response.json() or {}).get("response") or {}).get("docs", [])
                for d in docs:
                    headline = ((d.get("headline") or {}).get("main")) or ""
                    summary = d.get("abstract") or d.get("lead_paragraph") or d.get("snippet") or ""
                    pub_dt = self._parse_dt(d.get("pub_date", ""))
                    section = (
                        self._normalize_text(d.get("section_name", ""))
                        or self._normalize_text(d.get("news_desk", ""))
                        or "NYT-Search"
                    )
                    item = self._normalize_article(
                        source="NYT-Search",
                        source_type="api",
                        section=section,
                        title=headline,
                        url=d.get("web_url", ""),
                        summary=summary,
                        published_dt=pub_dt,
                        raw_json={
                            "section_name": d.get("section_name", ""),
                            "news_desk": d.get("news_desk", ""),
                            "type_of_material": d.get("type_of_material", ""),
                            "byline": (d.get("byline") or {}).get("original", ""),
                        },
                        fetched_at_utc=fetched_at,
                        min_published_utc=window_start_utc,
                        enforce_lookback=True,
                    )
                    if item:
                        all_articles.append(item)
            except Exception:
                had_error = True
            await asyncio.sleep(self.nyt_rate_limit_sec)
        self.db.record_news_ingest_attempt(
            "NYT-ArticleSearch",
            status="success" if all_articles else ("error" if had_error else "no_data"),
            item_count=len(all_articles),
            cursor={
                "window_start": window_start_utc.isoformat(),
                "window_end": window_end_utc.isoformat(),
                "pages": pages,
            },
            success_at=fetched_at.isoformat() if all_articles else None,
            attempted_at=fetched_at.isoformat(),
            error="partial_failure" if had_error and all_articles else ("all_pages_failed" if had_error else ""),
        )
        return all_articles

    async def _fetch_rss_articles(self, min_published_utc: datetime.datetime | None = None) -> list[dict]:
        if feedparser is None:
            print("[news] feedparser 미설치로 RSS 소스는 건너뜁니다.")
            self.db.record_news_ingest_attempt(
                "RSS",
                status="skipped",
                error="missing_feedparser",
            )
            return []
        rss_sources = [
            ("Reuters-Business", "rss", "Business", "https://feeds.reuters.com/reuters/businessNews"),
            ("Reuters-Technology", "rss", "Technology", "https://feeds.reuters.com/reuters/technologyNews"),
            ("Reuters-World", "rss", "World", "https://feeds.reuters.com/reuters/worldNews"),
            ("Reuters-Markets", "rss", "Markets", "https://feeds.reuters.com/reuters/marketsNews"),
            ("SEC-PressRelease", "rss", "Regulation", "https://www.sec.gov/news/pressreleases.rss"),
            ("FED-Press", "rss", "Macro", "https://www.federalreserve.gov/feeds/press_all.xml"),
        ]

        async def parse_feed(source_name: str, source_type: str, section: str, url: str) -> list[dict]:
            def _run():
                return feedparser.parse(url)

            out: list[dict] = []
            fetched_at = datetime.datetime.now(datetime.timezone.utc)
            try:
                feed = await asyncio.to_thread(_run)
                entries = list(getattr(feed, "entries", []))[: self.max_per_source]
                bozo_error = getattr(feed, "bozo_exception", None)
                for e in entries:
                    published = (
                        e.get("published")
                        or e.get("updated")
                        or e.get("pubDate")
                        or ""
                    )
                    item = self._normalize_article(
                        source=source_name,
                        source_type=source_type,
                        section=section,
                        title=e.get("title", ""),
                        url=e.get("link", ""),
                        summary=e.get("summary", "") or e.get("description", ""),
                        published_dt=self._parse_dt(published),
                        raw_json={
                            "author": e.get("author", ""),
                            "tags": [t.get("term", "") for t in e.get("tags", [])] if e.get("tags") else [],
                        },
                        fetched_at_utc=fetched_at,
                        min_published_utc=min_published_utc,
                        enforce_lookback=True,
                    )
                    if item:
                        out.append(item)
                self.db.record_news_ingest_attempt(
                    source_name,
                    status="success" if out else ("error" if bozo_error else "no_data"),
                    item_count=len(out),
                    success_at=fetched_at.isoformat() if out else None,
                    attempted_at=fetched_at.isoformat(),
                    error=str(bozo_error)[:500] if bozo_error else "",
                )
            except Exception:
                self.db.record_news_ingest_attempt(
                    source_name,
                    status="error",
                    error="exception",
                    attempted_at=fetched_at.isoformat(),
                )
                return []
            return out

        tasks = [parse_feed(*cfg) for cfg in rss_sources]
        all_rows = await asyncio.gather(*tasks, return_exceptions=False)
        merged = []
        for rows in all_rows:
            merged.extend(rows)
        self.db.record_news_ingest_attempt(
            "RSS",
            status="success" if merged else "no_data",
            item_count=len(merged),
            success_at=datetime.datetime.now(datetime.timezone.utc).isoformat() if merged else None,
        )
        return merged

    def _dedup_articles(self, rows: list[dict]) -> list[dict]:
        uniq: dict[str, dict] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            k = r.get("article_key", "")
            if not k:
                continue
            old = uniq.get(k)
            if old is None or self._article_rank(r) > self._article_rank(old):
                uniq[k] = r

        secondary: dict[str, dict] = {}
        for r in uniq.values():
            content_hash = str(r.get("content_hash", "")).strip()
            if not content_hash:
                secondary[self._hash(str(r))] = r
                continue
            sec_key = f"{r.get('date', '')}|{content_hash}"
            old = secondary.get(sec_key)
            if old is None or self._article_rank(r) > self._article_rank(old):
                secondary[sec_key] = r
        return list(secondary.values())

    def _tokenize(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}|[가-힣]{2,}", text.lower())
        stop = {
            "news", "update", "says", "said", "amid", "after", "before", "about",
            "with", "from", "this", "that", "into", "over", "under", "across",
            "today", "yesterday", "latest", "report", "reports",
            "시장", "뉴스", "관련", "대한", "최근", "이슈", "분석", "전망", "가능성",
        }
        out = []
        for t in tokens:
            if t in stop:
                continue
            if len(t) < 3 and not re.search(r"[가-힣]", t):
                continue
            out.append(t)
        return out

    def _cluster_events(self, articles: list[dict]) -> tuple[list[dict], list[dict]]:
        # 최신 기사 우선
        rows = sorted(
            [a for a in articles if isinstance(a, dict)],
            key=lambda x: str(x.get("published_at", "")),
            reverse=True,
        )
        clusters: list[dict] = []

        for a in rows:
            title = a.get("title", "")
            summary = a.get("summary", "")
            date = a.get("date", "")
            published_at = self._parse_iso_utc(str(a.get("published_at", "") or ""))
            kws = set(self._tokenize(f"{title} {summary}")[:20])
            source = a.get("source", "")

            best_idx = -1
            best_score = 0.0
            for idx, c in enumerate(clusters):
                if not self._published_within_cluster_window(published_at, c.get("latest_published_at")):
                    continue
                inter = len(kws & c["keywords"])
                union = len(kws | c["keywords"]) or 1
                jaccard = inter / union
                score = jaccard + (0.08 if inter >= 2 else 0.0)
                if score > best_score:
                    best_score = score
                    best_idx = idx

            if best_idx >= 0 and best_score >= 0.22:
                c = clusters[best_idx]
                c["articles"].append(a)
                c["keywords"].update(kws)
                c["sources"].add(source)
                c["title_counter"][title] += 1
                if published_at and (
                    c.get("latest_published_at") is None
                    or published_at > c.get("latest_published_at")
                ):
                    c["latest_published_at"] = published_at
                    c["date"] = published_at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d")
            else:
                clusters.append(
                    {
                        "date": date,
                        "articles": [a],
                        "keywords": set(kws),
                        "sources": {source},
                        "title_counter": Counter([title]),
                        "latest_published_at": published_at,
                    }
                )

        events: list[dict] = []
        for c in clusters:
            articles_in = c["articles"]
            if not articles_in:
                continue
            article_count = len(articles_in)
            source_count = len(c["sources"])
            rep_title = c["title_counter"].most_common(1)[0][0]
            top_terms = [t for t, _ in Counter(list(c["keywords"])).most_common(6)]
            event_seed = f"{c['date']}|{rep_title}|{'/'.join(sorted(top_terms))}"
            event_key = self._hash(event_seed)[:16]
            confidence = min(0.99, 0.45 + 0.07 * source_count + 0.03 * min(article_count, 8))
            sample_urls = [a.get("canonical_url") for a in articles_in[:3] if a.get("canonical_url")]
            summary = (
                f"{rep_title} | sources={source_count}, articles={article_count}, "
                f"keywords={', '.join(top_terms[:4])}"
            )

            for a in articles_in:
                a["event_key"] = event_key

            events.append(
                {
                    "event_key": event_key,
                    "date": c["date"],
                    "title": rep_title,
                    "summary": summary,
                    "source_count": source_count,
                    "article_count": article_count,
                    "confidence": round(confidence, 3),
                    "sample_urls": sample_urls,
                }
            )

        events = self._align_with_existing_event_keys(events)
        events = sorted(
            events,
            key=lambda x: (x.get("date", ""), float(x.get("confidence", 0.0)), int(x.get("article_count", 0))),
            reverse=True,
        )[: self.max_events]

        valid_keys = {e["event_key"] for e in events}
        filtered_articles = [a for a in articles if a.get("event_key") in valid_keys]
        return events, filtered_articles

    def _render_text_brief(self, events: list[dict], articles: list[dict]) -> str:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        delays = [int(a.get("ingest_delay_sec", 0) or 0) for a in articles if int(a.get("ingest_delay_sec", 0) or 0) > 0]
        avg_delay_min = round((sum(delays) / len(delays)) / 60.0, 1) if delays else 0.0
        p90_delay_min = 0.0
        if delays:
            sorted_delays = sorted(delays)
            idx = min(len(sorted_delays) - 1, int(0.9 * (len(sorted_delays) - 1)))
            p90_delay_min = round(sorted_delays[idx] / 60.0, 1)
        lines = [
            f"--- 업데이트 일시: {now} ---",
            "[고품질 뉴스 브리핑: NYT + Reuters + SEC + FED]",
            f"- event_count: {len(events)}",
            f"- article_count: {len(articles)}",
            f"- freshness(avg_delay_min/p90_delay_min): {avg_delay_min}/{p90_delay_min}",
            "",
            "=== 주요 이벤트 ===",
        ]
        for idx, e in enumerate(events[:20], 1):
            lines.append(
                f"{idx}. {e.get('title')} ({e.get('date')}) "
                f"[confidence={e.get('confidence')} / sources={e.get('source_count')} / articles={e.get('article_count')}]"
            )
            lines.append(f"   요약: {e.get('summary')}")
            for u in (e.get("sample_urls") or [])[:2]:
                lines.append(f"   - {u}")
        lines.append("")
        lines.append("=== 소스 분포 ===")
        source_counter = Counter([a.get("source", "unknown") for a in articles])
        for src, cnt in source_counter.most_common(12):
            lines.append(f"- {src}: {cnt}")
        return "\n".join(lines)

    async def _execute_scrape_for_window(
        self,
        *,
        window_start_utc: datetime.datetime,
        window_end_utc: datetime.datetime,
        mode: str,
    ) -> str:
        if window_start_utc.tzinfo is None:
            window_start_utc = window_start_utc.replace(tzinfo=datetime.timezone.utc)
        if window_end_utc.tzinfo is None:
            window_end_utc = window_end_utc.replace(tzinfo=datetime.timezone.utc)

        top_rows, search_rows, rss_rows = await asyncio.gather(
            self._fetch_nyt_topstories(min_published_utc=window_start_utc),
            self._fetch_nyt_articlesearch(window_start_utc=window_start_utc, window_end_utc=window_end_utc),
            self._fetch_rss_articles(min_published_utc=window_start_utc),
            return_exceptions=False,
        )
        merged = self._dedup_articles([*top_rows, *search_rows, *rss_rows])
        events, filtered_articles = self._cluster_events(merged)

        self.db.save_news_events_bulk(events)
        self.db.save_news_articles_bulk(filtered_articles)

        run_finished_utc = datetime.datetime.now(datetime.timezone.utc)
        pipeline_cursor = {
            "mode": mode,
            "window_start": window_start_utc.isoformat(),
            "window_end": window_end_utc.isoformat(),
            "saved_articles": len(filtered_articles),
            "merged_articles": len(merged),
            "clustered_events": len(events),
        }
        self.db.record_news_ingest_attempt(
            "news_pipeline",
            status="success" if filtered_articles else "no_data",
            item_count=len(filtered_articles),
            cursor=pipeline_cursor,
            success_at=run_finished_utc.isoformat() if filtered_articles else None,
            attempted_at=run_finished_utc.isoformat(),
            error="no_articles_saved" if not filtered_articles else "",
        )
        if filtered_articles:
            self.db.save_news_ingest_checkpoint(
                "news_pipeline",
                run_finished_utc.isoformat(),
                pipeline_cursor,
            )

        today_str = datetime.datetime.now().strftime("%Y%m%d")
        run_stamp = run_finished_utc.strftime("%H%M%S")
        txt_path = os.path.join(self.news_archive_dir, f"premium_news_{today_str}_{run_stamp}_{mode}.txt")
        json_path = os.path.join(self.news_archive_dir, f"premium_news_{today_str}_{run_stamp}_{mode}.json")
        latest_txt_path = os.path.join(self.news_archive_dir, f"premium_news_{today_str}.txt")
        latest_json_path = os.path.join(self.news_archive_dir, f"premium_news_{today_str}.json")

        text_brief = self._render_text_brief(events, filtered_articles)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text_brief)
        with open(latest_txt_path, "w", encoding="utf-8") as f:
            f.write(text_brief)

        payload = {
            "generated_at": run_finished_utc.isoformat(),
            "meta": {
                "mode": mode,
                "window_start": window_start_utc.isoformat(),
                "window_end": window_end_utc.isoformat(),
                "nyt_top_articles": len(top_rows),
                "nyt_search_articles": len(search_rows),
                "rss_articles": len(rss_rows),
                "merged_articles": len(merged),
                "clustered_events": len(events),
                "saved_articles": len(filtered_articles),
            },
            "events": events,
            "articles": filtered_articles,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        with open(latest_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        ended = datetime.datetime.now()
        print(f"[{ended}] 뉴스 저장 완료: {txt_path}")
        print(f"[{ended}] 구조화 JSON 저장 완료: {json_path}")
        if not filtered_articles:
            print(f"[{ended}] 경고: 이번 실행은 저장된 기사가 없어 pipeline checkpoint는 전진하지 않았습니다.")
        return text_brief

    async def execute_daily_scrape(self):
        """
        10분 폴링용 뉴스 수집 엔트리.
        - 이전 성공 시점 기준 overlap 재조회
        - 인덱싱 지연 흡수
        - DB + 파일 저장
        """
        started = datetime.datetime.now()
        poll_start_utc, poll_end_utc = self._resolve_poll_window()
        print(
            f"[{started}] 고품질 뉴스 스크래핑 시작..."
            f" (mode=poll10m, window={poll_start_utc.isoformat()}~{poll_end_utc.isoformat()})"
        )
        return await self._execute_scrape_for_window(
            window_start_utc=poll_start_utc,
            window_end_utc=poll_end_utc,
            mode="poll10m",
        )

    async def execute_backfill_scrape(self, backfill_hours: int | None = None):
        """
        일 1회 보정용 백필 엔트리.
        - 최근 N시간 재조회해 늦게 색인된 기사 보강
        """
        hours = int(backfill_hours or self.backfill_hours)
        hours = max(24, hours)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        start_utc = now_utc - datetime.timedelta(hours=hours)
        started = datetime.datetime.now()
        print(
            f"[{started}] 백필 스크래핑 시작..."
            f" (mode=backfill, hours={hours}, window={start_utc.isoformat()}~{now_utc.isoformat()})"
        )
        return await self._execute_scrape_for_window(
            window_start_utc=start_utc,
            window_end_utc=now_utc,
            mode=f"backfill_{hours}h",
        )
