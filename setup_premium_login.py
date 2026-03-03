import asyncio
import os
from playwright.async_api import async_playwright

async def setup_login():
    """
    NYT, WSJ 같은 프리미엄 사이트의 페이월(Paywall)을 뚫기 위해
    봇 전용 크롬 프로필에 사용자가 직접 1회 수동 로그인하는 스크립트.
    """
    user_data_dir = os.path.join(os.path.dirname(__file__), "chrome_data")
    os.makedirs(user_data_dir, exist_ok=True)
    
    print("===================================================================")
    print("🚀 [프리미엄 쿠키 연동 모드 시작]")
    print(f"저장소 위치: {user_data_dir}")
    print("잠시 후 크롬 창이 열립니다.")
    print("1. 열린 창에서 WSJ, NYT 등에 접속하세요.")
    print("2. 구독하신 아이디로 로그인(체크박스 '로그인 유지' 필수) 하세요.")
    print("3. 로그인이 완벽히 끝나면 이 콘솔 창에서 Enter 키를 누르세요.")
    print("===================================================================")

    async with async_playwright() as p:
        # headless=False로 설정하여 사용자 눈에 띄게 합니다.
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            viewport={'width': 1280, 'height': 800}
        )
        
        page = await context.new_page()
        # 사용자가 수동으로 탭을 열고 구글, WSJ, NYT에 로그인할 시간을 줍니다.
        await page.goto("https://www.wsj.com/")
        
        # 터미널에서 엔터를 칠 때까지 무한 대기
        input("\n✅ 로그인을 모두 마치셨나요? (완료 시 Enter를 누르세요): ")
        
        # 로그인이 끝나면 안전하게 브라우저 정보(쿠키 등)를 디스크에 쓰고 종료합니다.
        print("\n⏳ 세션을 모두 저장했습니다. 이제 띄워진 창을 안전하게 닫습니다.")
        await context.close()
        print("🎉 [설정 완료!] 이제 봇이 백그라운드에서 동일한 쿠키를 가지고 무료 프리패스로 기사를 스크랩합니다.")

if __name__ == "__main__":
    asyncio.run(setup_login())
