"""
track_returns.py — 추적로그 주가 반응 채우기 (반응학습 부품 1)
======================================================================
라인투자자산운용 | 김팀장 | 2026-06-01

목적: news_track_log.jsonl 의 ret_1d/3d/5d (비어있음)를 실제 주가로 채움.
  → "이 재료 뉴스 발송 후, 그 종목이 1/3/5일 뒤 몇 % 움직였나"
  → reaction_ranking.py 가 이걸로 종목 반응점수 순위 계산.

흐름:
  1. (깃허브에서 jsonl 복원 — github_backup 있으면)
  2. jsonl 읽기 → ret 비어있고 + 발송 후 N일 지난 종목 찾기
  3. 그 종목의 발송일 종가 + N일 후 종가 → 수익률 계산
  4. ret 채워서 jsonl 다시 저장 → 깃허브 커밋

주가 소스: FinanceDataReader (일봉). Render에서 KRX 접근 안 되면
  USE_NAVER=True 로 네이버 금융 폴백 (간단 파서).

실행:
  python track_returns.py
  (Render: 뉴스봇 사이클 중 하루 1번 호출하거나, 별도 스케줄)
"""
import os, json, datetime

LOG_PATH = os.environ.get('NEWS_TRACK_LOG', 'news_track_log.jsonl')
KST = datetime.timezone(datetime.timedelta(hours=9))

# ── 깃허브 백업 (있으면 사용) ──
_GH = None
try:
    import github_backup as _GH
except Exception:
    _GH = None


def _get_prices_naver(code, pages=3):
    """네이버 금융 일별시세로 종가 시계열. {날짜(str): 종가} 반환, 실패 시 None.
       requests 만 사용 (Render 친화). pages*10 거래일치 수집."""
    import requests, re
    out = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        for p in range(1, pages + 1):
            url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page={p}"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                break
            html = r.text
            # 각 행: 날짜(YYYY.MM.DD) ... 종가 ... (테이블 파싱)
            # 날짜와 그 뒤 첫 숫자(종가)를 정규식으로 추출
            rows = re.findall(
                r'(\d{4}\.\d{2}\.\d{2})</span>.*?<span[^>]*>\s*([\d,]+)\s*</span>',
                html, re.S)
            if not rows:
                break
            for d, price in rows:
                d2 = d.replace('.', '-')
                try:
                    out[d2] = float(price.replace(',', ''))
                except Exception:
                    pass
        return out if out else None
    except Exception as e:
        print(f"  [track_returns] 네이버 실패 {code}: {str(e)[:40]}", flush=True)
        return None


def _get_prices_fdr(code, start, end):
    """FinanceDataReader 폴백 (설치돼 있을 때만). {날짜str: 종가} or None."""
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(code, start, end)
        if df is None or len(df) == 0:
            return None
        return {idx.strftime('%Y-%m-%d'): float(row['Close'])
                for idx, row in df.iterrows()}
    except Exception:
        return None


def _get_prices(code, sd):
    """주가 시계열 조회: 네이버 우선, 실패 시 FDR 폴백."""
    prices = _get_prices_naver(code, pages=3)
    if prices:
        return prices
    start = (sd - datetime.timedelta(days=3)).strftime('%Y-%m-%d')
    end = (sd + datetime.timedelta(days=12)).strftime('%Y-%m-%d')
    return _get_prices_fdr(code, start, end)


def _ret_pct(prices, send_date, ndays):
    """발송일 종가 대비 n거래일 후 종가 수익률(%). 못 구하면 None.
       prices: {날짜str: 종가}, send_date: 'YYYY-MM-DD'."""
    if not prices:
        return None, None
    dates = sorted(prices.keys())
    # 발송일 이후 첫 거래일 = 기준(매수 가정)
    base_dates = [d for d in dates if d >= send_date]
    if len(base_dates) < 1:
        return None, None
    base_d = base_dates[0]
    base_p = prices[base_d]
    # 기준일로부터 ndays 거래일 뒤
    idx = dates.index(base_d)
    tgt_idx = idx + ndays
    if tgt_idx >= len(dates):
        return None, base_p   # 아직 n일 안 지남
    tgt_p = prices[dates[tgt_idx]]
    if base_p <= 0:
        return None, base_p
    return round((tgt_p - base_p) / base_p * 100, 2), base_p


def fill_returns(path=None, today=None):
    """ret 비어있는 기록을 채움. 반환: 채운 건수."""
    path = path or LOG_PATH
    if today is None:
        today = datetime.datetime.now(KST).date()

    # 깃허브에서 최신 복원
    if _GH is not None:
        try:
            _GH.restore_from_github(path)
        except Exception:
            pass

    if not os.path.exists(path):
        print(f"  [track_returns] 로그 없음: {path}", flush=True)
        return 0

    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass

    filled = 0
    price_cache = {}   # code -> prices dict (재사용)

    for rec in rows:
        # 이미 다 채워졌으면 skip
        if rec.get('ret_5d') is not None:
            continue
        stocks = rec.get('related_stocks') or []
        if not stocks:
            continue
        send_date = rec.get('date')
        try:
            sd = datetime.datetime.strptime(send_date, '%Y-%m-%d').date()
        except Exception:
            continue
        days_passed = (today - sd).days
        if days_passed < 1:
            continue   # 아직 1일도 안 지남

        # related_stocks 각각에 대해 ret 계산 → 대표값은 1순위 종목 기준 저장
        # (종목별 상세는 reaction_ranking 이 stocks 돌며 재계산)
        code = stocks[0].get('code', '')
        if not code:
            continue
        if code not in price_cache:
            price_cache[code] = _get_prices(code, sd)
        prices = price_cache[code]

        r1, base = _ret_pct(prices, send_date, 1)
        r3, _ = _ret_pct(prices, send_date, 3)
        r5, _ = _ret_pct(prices, send_date, 5)
        changed = False
        if rec.get('ret_1d') is None and r1 is not None:
            rec['ret_1d'] = r1; changed = True
        if rec.get('ret_3d') is None and r3 is not None:
            rec['ret_3d'] = r3; changed = True
        if rec.get('ret_5d') is None and r5 is not None:
            rec['ret_5d'] = r5; changed = True
        if rec.get('price_at_send') is None and base is not None:
            rec['price_at_send'] = base; changed = True
        if changed:
            filled += 1

    # 저장
    if filled > 0:
        with open(path, 'w', encoding='utf-8') as f:
            for rec in rows:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        # 깃허브 커밋
        if _GH is not None:
            try:
                _GH.backup_to_github(path, msg=f'ret 채움 {filled}건')
            except Exception:
                pass

    print(f"  [track_returns] ret 채움: {filled}건 / 전체 {len(rows)}건", flush=True)
    return filled


if __name__ == '__main__':
    fill_returns()
