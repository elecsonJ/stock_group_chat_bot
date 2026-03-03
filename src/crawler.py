import yfinance as yf
import feedparser
import urllib.parse
from datetime import datetime, timedelta

class InvestmentCrawler:
    def __init__(self):
        pass

    def get_stock_data(self, ticker: str) -> dict:
        """
        [난이도 하] yfinance를 이용해 종목의 기본 및 최신 정보(가격 흐름 등)를 가져옵니다.
        """
        try:
            target = yf.Ticker(ticker)
            info = target.info
            hist = target.history(period="1mo")
            
            return {
                "ticker": ticker,
                "current_price": info.get("currentPrice", "N/A"),
                "forward_pe": info.get("forwardPE", "N/A"),
                "market_cap": info.get("marketCap", "N/A"),
                "recent_trend": f"최근 1달 최저 {hist['Low'].min():.2f} / 최고 {hist['High'].max():.2f}"
            }
        except Exception as e:
            return {"error": str(e)}

    def get_news_rss(self, keyword: str) -> list:
        """
        [난이도 중] 구글 뉴스 RSS 피드를 사용하여 특정 키워드에 대한 최신 뉴스 헤드라인을 수집합니다.
        """
        encoded_keyword = urllib.parse.quote(keyword)
        # 한국어 구글 뉴스 RSS
        url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=ko&gl=KR&ceid=KR:ko"
        feed = feedparser.parse(url)
        
        news_list = []
        for entry in feed.entries[:5]:  # 상위 5개 최신 기사만 수집 (토큰 절약)
            news_list.append({
                "title": entry.title,
                "published": entry.published,
                "link": entry.link
            })
            
        return news_list

if __name__ == "__main__":
    crawler = InvestmentCrawler()
    print("=== 테슬라(TSLA) 주식 정보 ===")
    print(crawler.get_stock_data("TSLA"))
    print("\n=== '엔비디아' 최신 뉴스 ===")
    for n in crawler.get_news_rss("엔비디아"):
        print(f"- {n['title']} ({n['published']})")
