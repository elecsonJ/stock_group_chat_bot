import asyncio
import datetime
import hashlib
import json
import os
import re
from collections import Counter
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
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

    def __init__(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.news_archive_dir = os.path.join(root, "news_archive")
        os.makedirs(self.news_archive_dir, exist_ok=True)
        self.db = DBManager()

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
    ) -> dict | None:
        clean_title = self._normalize_text(title)
        clean_summary = self._normalize_text(summary)
        clean_url = self._canonicalize_url(url)
        if not clean_title or not clean_url:
            return None
        if not self._in_lookback(published_dt):
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
            return []

        articles: list[dict] = []
        fetched_at = datetime.datetime.now(datetime.timezone.utc)
        for sec_eng, sec_kor in self.nyt_sections.items():
            url = f"https://api.nytimes.com/svc/topstories/v2/{sec_eng}.json?api-key={nyt_api_key}"
            try:
                response = await asyncio.to_thread(requests.get, url, timeout=self.request_timeout)
                if response.status_code != 200:
                    await asyncio.sleep(self.nyt_rate_limit_sec)
                    continue
                payload = response.json()
                rows = payload.get("results", [])[: self.max_per_source]
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
                    )
                    if item:
                        articles.append(item)
            except Exception:
                pass
            # NYT free tier rate limit 보호
            await asyncio.sleep(self.nyt_rate_limit_sec)
        return articles

    async def _fetch_nyt_articlesearch(
        self,
        window_start_utc: datetime.datetime,
        window_end_utc: datetime.datetime,
    ) -> list[dict]:
        nyt_api_key = os.getenv("NYT_API_KEY", "").strip()
        if not nyt_api_key:
            return []

        start_date = window_start_utc.strftime("%Y%m%d")
        end_date = window_end_utc.strftime("%Y%m%d")
        pages = max(1, min(3, (self.max_per_source + 9) // 10))
        fetched_at = datetime.datetime.now(datetime.timezone.utc)
        all_articles: list[dict] = []

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
                    )
                    if item:
                        all_articles.append(item)
            except Exception:
                pass
            await asyncio.sleep(self.nyt_rate_limit_sec)
        return all_articles

    async def _fetch_rss_articles(self, min_published_utc: datetime.datetime | None = None) -> list[dict]:
        if feedparser is None:
            print("[news] feedparser 미설치로 RSS 소스는 건너뜁니다.")
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
                    )
                    if item:
                        out.append(item)
            except Exception:
                return []
            return out

        tasks = [parse_feed(*cfg) for cfg in rss_sources]
        all_rows = await asyncio.gather(*tasks, return_exceptions=False)
        merged = []
        for rows in all_rows:
            merged.extend(rows)
        return merged

    def _dedup_articles(self, rows: list[dict]) -> list[dict]:
        uniq: dict[str, dict] = {}
        for r in rows:
            k = r.get("article_key", "")
            if not k:
                continue
            if k not in uniq:
                uniq[k] = r
                continue
            # 최신 published_at 우선
            old = uniq[k]
            if str(r.get("published_at", "")) > str(old.get("published_at", "")):
                uniq[k] = r
        return list(uniq.values())

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
        rows = sorted(articles, key=lambda x: str(x.get("published_at", "")), reverse=True)
        clusters: list[dict] = []

        for a in rows:
            title = a.get("title", "")
            summary = a.get("summary", "")
            date = a.get("date", "")
            kws = set(self._tokenize(f"{title} {summary}")[:20])
            source = a.get("source", "")

            best_idx = -1
            best_score = 0.0
            for idx, c in enumerate(clusters):
                if c["date"] != date:
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
            else:
                clusters.append(
                    {
                        "date": date,
                        "articles": [a],
                        "keywords": set(kws),
                        "sources": {source},
                        "title_counter": Counter([title]),
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
        self.db.save_news_ingest_checkpoint(
            "news_pipeline",
            run_finished_utc.isoformat(),
            {
                "mode": mode,
                "window_start": window_start_utc.isoformat(),
                "window_end": window_end_utc.isoformat(),
                "saved_articles": len(filtered_articles),
            },
        )

        today_str = datetime.datetime.now().strftime("%Y%m%d")
        txt_path = os.path.join(self.news_archive_dir, f"premium_news_{today_str}.txt")
        json_path = os.path.join(self.news_archive_dir, f"premium_news_{today_str}.json")

        text_brief = self._render_text_brief(events, filtered_articles)
        with open(txt_path, "w", encoding="utf-8") as f:
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

        ended = datetime.datetime.now()
        print(f"[{ended}] 뉴스 저장 완료: {txt_path}")
        print(f"[{ended}] 구조화 JSON 저장 완료: {json_path}")
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
