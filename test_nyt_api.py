import os
import requests
from dotenv import load_dotenv

def test_nyt_api_sections():
    # .env 파일에서 API 키 로드
    load_dotenv()
    api_key = os.getenv("NYT_API_KEY")
    
    if not api_key:
        print("API 키를 찾을 수 없습니다.")
        return

    print("=== NYT Top Stories API 가용 섹션 및 기사 수 확인 ===\n")

    # NYT Top Stories API가 지원하는 알려진 전체 섹션 목록
    all_sections = [
        "home", "business", "technology", "science", "world", 
        "politics", "us", "nyregion", "opinion", "upshot", 
        "health", "realestate", "automobiles", "arts", "books/review",
        "movies", "theater", "sports", "fashion", "food", "travel"
    ]
    
    print(f"테스트할 전체 공식 섹션 수: {len(all_sections)}개\n")

    total_articles_found = 0

    for section in all_sections:
        url = f"https://api.nytimes.com/svc/topstories/v2/{section}.json?api-key={api_key}"
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                count = len(results)
                total_articles_found += count
                
                print(f"[{section.upper():<12}] -> 현재 수집 가능한 기사 수: {count:2}개")
                
                # 첫 번째 기사의 제목만 살짝 엿보기
                if count > 0:
                    first_title = results[0].get('title', '제목 없음')
                    print(f"    └ 예시: {first_title[:60]}...")
            else:
                print(f"[{section}] 섹션 조회 실패 (HTTP {response.status_code})")
        except Exception as e:
            print(f"[{section}] 섹션 에러: {e}")
            
    print(f"\n==============================================")
    print(f"모든 섹션을 합쳤을 때 지금 당장 수집 가능한 총 기사 수: {total_articles_found}개")
    print(f"==============================================")

if __name__ == "__main__":
    test_nyt_api_sections()
