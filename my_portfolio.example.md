# 내 포트폴리오 예시

아래 2가지 방식 중 하나를 사용하세요.

## 1) 라인 기반 (빠른 수기 입력)
- NVDA | qty: 3 | avg: 780
- 005930.KS, 12, 71200
- TSLA 2 @ 250

## 2) JSON 블록 기반 (정확한 기계 파싱)
```portfolio-json
[
  {"ticker":"NVDA","qty":3,"avg_price":780,"currency":"USD","note":"장기"},
  {"ticker":"005930.KS","qty":12,"avg_price":71200,"currency":"KRW","note":"코어"}
]
```

운영 파일 경로 기본값은 `data/my_portfolio.md` 입니다.
