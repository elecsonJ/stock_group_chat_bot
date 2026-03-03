import yfinance as yf
import pandas_ta as ta
import pandas as pd
import asyncio
from datetime import datetime, timedelta

class AdvancedDataFetcher:
    def __init__(self):
        pass

    async def get_comprehensive_stock_data(self, ticker: str) -> str:
        """
        주어진 티커에 대해 펀더멘털, 재무제표, 기술적 지표(이동평균, RSI 등)를 
        모두 수집하여 하나의 마크다운 텍스트(Fact-Sheet 조각)로 반환합니다.
        비동기로 yfinance I/O가 블로킹되지 않도록 to_thread로 감쌉니다.
        """
        def fetch_sync(t_name):
            try:
                stock = yf.Ticker(t_name)
                info = stock.info
                
                # 1. 기본 가치평가 및 성장 지표
                current_price = info.get('currentPrice', info.get('regularMarketPrice', 'N/A'))
                market_cap = info.get('marketCap', 'N/A')
                trailing_pe = info.get('trailingPE', 'N/A')
                forward_pe = info.get('forwardPE', 'N/A')
                peg_ratio = info.get('pegRatio', 'N/A')
                pb_ratio = info.get('priceToBook', 'N/A')
                
                # 2. 수익성 및 배당 지표
                profit_margin = info.get('profitMargins', 0)
                if isinstance(profit_margin, float): profit_margin = f"{profit_margin*100:.2f}%"
                
                roe = info.get('returnOnEquity', 0)
                if isinstance(roe, float): roe = f"{roe*100:.2f}%"
                
                dividend_yield = info.get('dividendYield', 0)
                if isinstance(dividend_yield, float): dividend_yield = f"{dividend_yield*100:.2f}%"
                
                # 3. 기관 / 공매도 / 내부자 데이터
                short_ratio = info.get('shortRatio', 'N/A')
                short_percent = info.get('shortPercentOfFloat', 0)
                if isinstance(short_percent, float): short_percent = f"{short_percent*100:.2f}%"
                
                inst_own = info.get('heldPercentInstitutions', 0)
                if isinstance(inst_own, float): inst_own = f"{inst_own*100:.2f}%"
                
                insider_own = info.get('heldPercentInsiders', 0)
                if isinstance(insider_own, float): insider_own = f"{insider_own*100:.2f}%"
                
                # 3.5. 섹터 / 산업 정보 (Peer 비교용)
                sector = info.get('sector', 'N/A')
                industry = info.get('industry', 'N/A')
                
                # 재무 데이터 텍스트 구축
                fund_text = (
                    f"**[종목: {t_name.upper()} | 섹터: {sector} | 산업: {industry}]**\n"
                    f"- **현재가**: ${current_price} | **시가총액**: {self._format_market_cap(market_cap)}\n"
                    f"- **가치평가**: Trailing PER {trailing_pe}배 | Forward PER {forward_pe}배 | PEG {peg_ratio} | PBR {pb_ratio}\n"
                    f"- **수익성/배당**: 순이익률 {profit_margin} | ROE {roe} | 배당수익률 {dividend_yield}\n"
                    f"- **수급/내부자/공매도**: 기관보유율 {inst_own} | 내부자보유율 {insider_own} | 공매도 잔고율 {short_percent} (Short Ratio: {short_ratio})\n"
                )

                # 4. 차트 및 기술적 지표 (최근 6개월 일봉 데이터)
                hist = stock.history(period="6mo")
                tech_text = "- **기술적 차트 지표 (6개월 기준)**: 데이터를 불러올 수 없습니다.\n"
                if not hist.empty and len(hist) > 50:
                    # pandas_ta를 활용하여 지표 계산
                    hist.ta.sma(length=20, append=True)
                    hist.ta.sma(length=50, append=True)
                    hist.ta.sma(length=200, append=True)
                    hist.ta.rsi(length=14, append=True)
                    hist.ta.macd(fast=12, slow=26, signal=9, append=True)
                    hist.ta.bbands(length=20, std=2, append=True)
                    
                    last_row = hist.iloc[-1]
                    sma20 = last_row.get('SMA_20', 'N/A')
                    sma50 = last_row.get('SMA_50', 'N/A')
                    sma200 = last_row.get('SMA_200', 'N/A')
                    rsi14 = last_row.get('RSI_14', 'N/A')
                    macd = last_row.get('MACD_12_26_9', 'N/A')
                    macd_signal = last_row.get('MACDs_12_26_9', 'N/A')
                    bb_low = last_row.get('BBL_20_2.0', 'N/A')
                    bb_high = last_row.get('BBU_20_2.0', 'N/A')
                    current_close = last_row.get('Close', current_price)
                    
                    # 52주 데이터
                    high_52 = info.get('fiftyTwoWeekHigh', 'N/A')
                    low_52 = info.get('fiftyTwoWeekLow', 'N/A')
                    
                    # 텍스트화
                    rsi_status = "과매수(Overbought)" if isinstance(rsi14, float) and rsi14 > 70 else ("과매도(Oversold)" if isinstance(rsi14, float) and rsi14 < 30 else "중립구간")
                    if isinstance(rsi14, float): rsi14 = f"{rsi14:.2f}"
                    
                    macd_status = "골든크로스(상승 모멘텀)" if (isinstance(macd, float) and isinstance(macd_signal, float) and macd > macd_signal) else "데드크로스(하락 모멘텀)"
                    
                    bb_status = "밴드 하단 이탈(극단적 과매도/반등 가능성)" if isinstance(bb_low, float) and isinstance(current_close, float) and current_close < bb_low else ("밴드 상단 돌파(극단적 과매수/조정 가능성)" if isinstance(bb_high, float) and isinstance(current_close, float) and current_close > bb_high else "밴드 내 정상 등락")
                    
                    # 20일선 이격도
                    divergence_20 = "N/A"
                    if isinstance(sma20, float) and isinstance(current_close, float):
                        div_val = ((current_close - sma20) / sma20) * 100
                        divergence_20 = f"{div_val:+.2f}%"
                        sma20 = f"${sma20:.2f}"
                        
                    if isinstance(sma50, float): sma50 = f"${sma50:.2f}"
                    if isinstance(sma200, float): sma200 = f"${sma200:.2f}"

                    vol_text = "N/A"
                    if 'Volume' in hist.columns:
                        recent_vol_avg = hist['Volume'].tail(5).mean()
                        last_vol = last_row.get('Volume', 0)
                        if recent_vol_avg > 0:
                            vol_surge_ratio = last_vol / recent_vol_avg
                            if vol_surge_ratio > 1.5:
                                vol_text = f"최근 5일 평균 대비 {vol_surge_ratio:.1f}배 거래량 폭증 (변동성 극대화 징후)"
                            else:
                                vol_text = "평균 수준의 거래량 유지"

                    tech_text = (
                        f"- **차트 및 추세 (Technical)**:\n"
                        f"  - 52주 변동폭: {low_52} ~ {high_52} (현재 {current_close})\n"
                        f"  - 이평선: 20일선({sma20}) 대비 이격도 {divergence_20}, 장기추세 200일선({sma200})\n"
                        f"  - 모멘텀: RSI(14) {rsi14} ({rsi_status}) | MACD {macd_status}\n"
                        f"  - 변동성: 볼린저밴드 기준 '{bb_status}' | 거래량: {vol_text}\n"
                    )

                # 5. YTD(연초 대비) 수익률 비교 (시장 대비 상대 모멘텀)
                ytd_hist = stock.history(period="ytd")
                ytd_perf = "N/A"
                if not ytd_hist.empty and len(ytd_hist) > 0:
                    ytd_start_price = ytd_hist.iloc[0].get('Close', current_price)
                    if isinstance(ytd_start_price, float) and isinstance(current_close, float) and ytd_start_price > 0:
                        ytd_perf_val = ((current_close - ytd_start_price) / ytd_start_price) * 100
                        ytd_perf = f"{ytd_perf_val:+.2f}%"
                        
                # SPY(S&P 500 ETF) YTD 비교용
                try:
                    spy = yf.Ticker("SPY")
                    spy_ytd = spy.history(period="ytd")
                    spy_perf = "N/A"
                    if not spy_ytd.empty and len(spy_ytd) > 0:
                        spy_start = spy_ytd.iloc[0].get('Close', 1)
                        spy_end = spy_ytd.iloc[-1].get('Close', 1)
                        if isinstance(spy_start, float) and isinstance(spy_end, float) and spy_start > 0:
                            spy_perf_val = ((spy_end - spy_start) / spy_start) * 100
                            spy_perf = f"{spy_perf_val:+.2f}%"
                except:
                    spy_perf = "수집 실패"

                alpha_text = (
                    f"- **알파 지표 (Alpha & Momentum)**:\n"
                    f"  - 연초 대비(YTD) 수익률: {t_name.upper()} {ytd_perf} vs 시장(SPY) {spy_perf}\n"
                )

                return fund_text + tech_text + alpha_text

            except Exception as e:
                return f"[{t_name}] 기본 데이터 수집 실패: {e}\n"

        # yfinance 호출 시 블로킹 방지
        result = await asyncio.to_thread(fetch_sync, ticker) # type: ignore
        return result

    def _format_market_cap(self, val):
        if not isinstance(val, (int, float)):
            return str(val)
        if val >= 1_000_000_000_000:
            return f"${val/1_000_000_000_000:.2f}T (조 달러)"
        elif val >= 1_000_000_000:
            return f"${val/1_000_000_000:.2f}B (십억 달러)"
        elif val >= 1_000_000:
            return f"${val/1_000_000:.2f}M (백만 달러)"
        return f"${val}"
