import yfinance as yf
import asyncio

class MacroFetcher:
    def __init__(self):
        # FRED API 등을 쓸 수도 있지만 yfinance로도 핵심 거시지표 수집이 가능합니다.
        self.macro_tickers = {
            "^TNX": "미국 10년물 국채 금리",
            "^VIX": "VIX 공포 지수",
            "KRW=X": "원/달러 환율"
        }

    async def get_macro_environment(self) -> str:
        """현재 시장의 큰 물줄기를 제공합니다."""
        
        def fetch_sync():
            try:
                # yf.download 시 쓰레딩/DB Lock 이슈를 피하기 위해 auto_adjust=False 추가
                histories = yf.download(
                    list(self.macro_tickers.keys()), 
                    period="5d", 
                    progress=False,
                    auto_adjust=False
                )
                
                # Close (종가) 정보만 추출
                if "Close" in histories.columns:
                    closes = histories["Close"]
                else:
                    closes = histories
                
                # 최신 종가 가져오기 (마지막 행)
                last_vals = closes.iloc[-1]
                
                result_str = "**[🌍 거시 경제 동향 (Macro)]**\n"
                
                for t_sym, t_name in self.macro_tickers.items():
                    val = last_vals.get(t_sym)
                    if val is not None and not pd.isna(val):
                        # % 포맷 등 처리
                        if t_sym == "^TNX":
                            result_str += f"- **{t_name}**: {val:.3f}%\n"
                        elif t_sym == "KRW=X":
                            result_str += f"- **{t_name}**: {val:.1f}원\n"
                        else:
                            result_str += f"- **{t_name}**: {val:.2f}\n"
                            
                return result_str
            except Exception as e:
                return f"[Macro] 거시 데이터 수집 실패: {e}\n"

        import pandas as pd
        result = await asyncio.to_thread(fetch_sync)
        return result
