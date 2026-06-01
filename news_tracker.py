# -*- coding: utf-8 -*-
"""
news_tracker.py — 뉴스 추적 로그 (페이퍼웍의 핵심)
======================================================================
라인투자자산운용 | 김팀장 | 2026-06-01

목적: "뉴스 점수가 실제 주가를 예측하나?"를 검증할 데이터를 축적.
  → 나중에 뉴스봇을 나비봇 등 매매와 결합할 때의 '근거 자료'.

흐름:
  1. log_sent()  : 뉴스봇이 발송할 때마다 (날짜/종목/점수/키워드/신규성) 기록
  2. (나중에) track_returns.py 가 N일 후 주가를 채워넣어 검증
     → "점수 80↑ 뉴스 종목은 평균 +X% 올랐다" 같은 통계

저장: news_track_log.jsonl (한 줄=한 발송기록, append-only)
  클라우드(Render)면 환경변수 NEWS_TRACK_LOG 로 경로 지정 가능.
  ※ Render 재시작 시 로컬파일 날아갈 수 있음 → 운영 시 DB나 외부저장 권장(주석 참고).
"""
import os, json, datetime, re

LOG_PATH = os.environ.get('NEWS_TRACK_LOG', 'news_track_log.jsonl')
KST = datetime.timezone(datetime.timedelta(hours=9))

# ── 종목 사전 로드 (make_stock_master.py 가 만든 stock_master.json) ──
_STOCK_MASTER = None
def load_stock_master(path='stock_master.json'):
    global _STOCK_MASTER
    if _STOCK_MASTER is not None:
        return _STOCK_MASTER
    try:
        with open(path, encoding='utf-8') as f:
            _STOCK_MASTER = json.load(f)
    except Exception:
        _STOCK_MASTER = {'names': {}, 'aliases': {}}
    return _STOCK_MASTER

def match_stocks_in_title(title, master=None):
    """제목에서 사전에 있는 종목명/약칭을 정확히 매칭. 반환: [{'name','code'}]
       긴 이름 우선(삼성전자 > 삼성)으로 중복 매칭 방지."""
    if master is None:
        master = load_stock_master()
    names = master.get('names', {})
    aliases = master.get('aliases', {})
    found = {}   # code -> name
    # 정식 종목명: 긴 것부터 검사 (부분 겹침 방지)
    for nm in sorted(names.keys(), key=len, reverse=True):
        if len(nm) >= 2 and nm in title:
            code = names[nm]
            if code not in found:
                found[code] = nm
    # 약칭
    for al, code in aliases.items():
        if len(al) >= 2 and al in title and code not in found:
            found[code] = al
    return [{'name': v, 'code': k} for k, v in found.items()][:3]

# 종목명 → 종목코드 매핑 (사전 우선, 폴백은 stock_db)
def extract_stock(title, stocks_in_title, STOCK_DB=None, stock_db=None):
    """발송 뉴스에서 관련 종목 추출. 사전 매칭 우선."""
    # 1순위: stock_master 사전 정확 매칭
    hits = match_stocks_in_title(title)
    if hits:
        return hits
    # 2순위: stock_db 폴백
    found = []
    if STOCK_DB and stock_db:
        try:
            for nm in stocks_in_title:
                h = stock_db.match_keyword(STOCK_DB, nm, limit=1)
                if h:
                    found.append({'name': h[0].get('name', nm), 'code': h[0].get('code', '')})
        except Exception:
            pass
    return found[:3]


def log_sent(send_list, now=None, STOCK_DB=None, stock_db=None):
    """발송한 뉴스 리스트를 추적 로그에 append.
       send_list: score_news 결과 dict 리스트 (title/score/themes/novelty 등 포함)
    """
    if now is None:
        now = datetime.datetime.now(KST)
    ts = now.strftime('%Y-%m-%d %H:%M:%S')
    date = now.strftime('%Y-%m-%d')
    lines = []
    for n in send_list:
        title = n.get('title', '')
        themes = n.get('themes', [])
        # 관련종목: stock_master 사전으로 제목에서 정확히 매칭
        try:
            related = extract_stock(title, set(), STOCK_DB, stock_db)
        except Exception:
            related = []
        rec = {
            'ts': ts,
            'date': date,
            'title': title,
            'score': n.get('score', 0),
            'themes': themes,
            'novelty': n.get('novelty', ''),
            'novelty_pt': n.get('novelty_pt', 0),
            'bonus': n.get('bonus', []),
            'source': n.get('source', ''),
            'url': n.get('url', ''),
            'related_stocks': related,   # [{'name','code'}] best-effort
            # 아래는 나중에 track_returns.py 가 채움
            'ret_1d': None, 'ret_3d': None, 'ret_5d': None,
            'price_at_send': None,
        }
        lines.append(json.dumps(rec, ensure_ascii=False))
    if lines:
        try:
            with open(LOG_PATH, 'a', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
        except Exception as e:
            print(f"  [news_tracker] 저장 실패: {str(e)[:50]}", flush=True)
    return len(lines)


def load_log(path=None):
    """추적 로그 전체 로드 (분석용). 반환: list of dict"""
    path = path or LOG_PATH
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


if __name__ == '__main__':
    # 간단 테스트
    sample = [{
        'title': '한화에어로스페이스, 대규모 수주 계약 체결',
        'score': 85, 'themes': ['방산'], 'novelty': 'novel', 'novelty_pt': 6,
        'bonus': ['수주(+3)'], 'source': '연합뉴스', 'url': 'http://x',
    }]
    n = log_sent(sample)
    print(f"기록 {n}건 → {LOG_PATH}")
    for r in load_log():
        print(r['date'], r['score'], r['title'][:20], r['novelty'])
