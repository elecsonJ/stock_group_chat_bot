import asyncio
import os
from playwright.async_api import async_playwright, BrowserContext
from bs4 import BeautifulSoup
import datetime

# WSJ 및 NYT 등 프리미엄 매체를 크롤링하기 위한 전용 클래스
class PremiumCrawler:
    def __init__(self):
        # 봇의 최상위 폴더에 있는 chrome_data 폴더를 프로필/쿠키 저장소로 씁니다.
        self.user_data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
            "chrome_data"
        )
        # 스크랩한 기사를 저장할 폴더
        self.news_archive_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
            "news_archive"
        )
        os.makedirs(self.news_archive_dir, exist_ok=True)

    async def _setup_browser(self, p, site_url=""):
        """
        일반 시크릿 브라우저를 열되, 사용자 쿠키 JSON 파일이 있다면 밀어넣습니다.
        """
        context = await p.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=True,
            java_script_enabled=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        
        # 쿠키 파일 읽어와서 주입하기
        cookie_file = os.path.join(os.path.dirname(__file__), "cookies.json")
        if os.path.exists(cookie_file):
            import json
            with open(cookie_file, "r", encoding="utf-8") as f:
                cookies = json.load(f)
                
                # Playwright 형식에 맞게 SameSite 에러 수정 등 쿠키 정제
                for cookie in cookies:
                    if 'sameSite' in cookie:
                        val = cookie['sameSite'].lower()
                        if val == 'no_restriction' or val == 'unspecified':
                            cookie['sameSite'] = 'None'
                        elif val == 'lax':
                            cookie['sameSite'] = 'Lax'
                        elif val == 'strict':
                            cookie['sameSite'] = 'Strict'
                    # StoreId 등 불필요한 필드는 삭제
                    cookie.pop('storeId', None)
                    cookie.pop('id', None)
                    
                await context.add_cookies(cookies)
                
        return context



    async def fetch_nyt_sections(self) -> str:
        """
        NYT API를 이용하여 비즈니스, 테크, 세계, 정치 등 다양한 섹션의 기사를 수집합니다.
        가장 강력하게 방어되는 페이월을 우회하는 우아한 방법입니다.
        """
        from dotenv import load_dotenv
        import requests
        
        load_dotenv()
        nyt_api_key = os.getenv("NYT_API_KEY")
        
        if not nyt_api_key or nyt_api_key.strip() == "":
            return "[NYT API에러] .env 파일에 NYT_API_KEY 값이 없습니다. API 키를 등록해주세요."

        # AI 토론과 예측에 가장 최적화된 핵심 7대 분야로 대폭 확장
        sections = {
            "홈(종합)": "home",
            "비즈니스": "business",
            "테크놀로지": "technology",
            "세계 뉴스": "world",
            "미국 정치": "politics",
            "과학": "science",
            "건강/의료": "health"
        }
        
        result_text = "[NYT (New York Times) 심층 프리미엄 뉴스 (공식 API)]\n"
        
        for sec_kor, sec_eng in sections.items():
            result_text += f"\n==================\n[{sec_kor}] 분야 주요 뉴스 요약\n==================\n"
            url = f"https://api.nytimes.com/svc/topstories/v2/{sec_eng}.json?api-key={nyt_api_key}"
            
            try:
                # 비동기 블로킹 방지를 위해 thread 사용
                response = await asyncio.to_thread(requests.get, url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    
                    # 기술, 과학 관련은 미래 전망에 매우 중요하므로 더 많은 수량(15개) 할당. 나머지도 10개씩.
                    limit = 20 if sec_eng in ["technology", "science", "business"] else 12
                    
                    for idx, art in enumerate(results[:limit], 1):
                        title = art.get('title', '제목 없음')
                        url_link = art.get('url', '')
                        abstract = art.get('abstract', '요약 정보 없음')
                        pub_date = art.get('published_date', '')[:10]  # 날짜 추출
                        
                        # API에서 빈 요약이 터지는 경우 스킵
                        if not abstract.strip():
                            continue
                            
                        result_text += f"\n{idx}. 제목: {title} ({pub_date})\n"
                        result_text += f"   링크: {url_link}\n"
                        result_text += f"   요약(Abstract): {abstract}\n"
                elif response.status_code == 429:
                    result_text += f"   [API 호출 실패 - NYT 요금제 제한(Too Many Requests)]\n"
                else:
                    result_text += f"   [API 호출 실패] HTTP {response.status_code}\n"
            except Exception as e:
                result_text += f"   [API 에러] {e}\n"
                
            # NYT 무료 API는 '1분에 5회(12초당 1회)' 호출이라는 엄격한 Rate Limit(429)이 있습니다.
            # 429 에러 방지를 위해 요청과 요청 사이에 12.5초의 강제 휴식을 부여합니다.
            await asyncio.sleep(12.5)
            
        return result_text

    async def execute_daily_scrape(self):
        """
        매일 새벽 실행될 스크래핑 묶음 함수.
        가져온 뉴스 전문/리스트를 로컬 폴더에 텍스트로 저장합니다.
        """
        print(f"[{datetime.datetime.now()}] 프리미엄 뉴스 크롤링(NYT) 시작...")
        
        nyt_summary = await self.fetch_nyt_sections()
        
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        file_path = os.path.join(self.news_archive_dir, f"premium_news_{today_str}.txt")
        
        final_corpus = f"--- 업데이트 일시: {datetime.datetime.now()} ---\n\n{nyt_summary}"
        
        # 파일 저장
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(final_corpus)
            
        print(f"[{datetime.datetime.now()}] 프리미엄 뉴스 저장 완료: {file_path}")
        return final_corpus
