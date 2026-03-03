import asyncio
import sys
import os

# 파이썬이 src 디렉토리의 모듈을 제대로 인식할 수 있도록 경로 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from data_fetcher.premium_crawler import PremiumCrawler

async def run_scraper():
    print("=== 고품질 뉴스 백그라운드 스크래퍼 (Windows 스케줄러 연동용) ===")
    crawler = PremiumCrawler()
    backfill_hours = None
    if "--backfill" in sys.argv:
        idx = sys.argv.index("--backfill")
        if idx + 1 < len(sys.argv):
            try:
                backfill_hours = int(sys.argv[idx + 1])
            except Exception:
                backfill_hours = None
    try:
        if backfill_hours:
            await crawler.execute_backfill_scrape(backfill_hours=backfill_hours)
            print("✅ 뉴스 백필 스크래핑이 성공적으로 완료되었습니다.")
        else:
            await crawler.execute_daily_scrape()
            print("✅ 10분 폴링 뉴스 스크래핑이 성공적으로 완료되었습니다.")
    except Exception as e:
        print(f"❌ 뉴스 스크래핑 중 오류 발생: {e}")

if __name__ == "__main__":
    asyncio.run(run_scraper())
