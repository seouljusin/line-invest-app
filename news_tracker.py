# -*- coding: utf-8 -*-
"""
news_tracker.py — 뉴스 추적 로그 (페이퍼웍의 핵심) [깃허브 영구저장판]
======================================================================
라인투자자산운용 | 김팀장 | 2026-06-01

목적: "뉴스 점수가 실제 주가를 예측하나?"를 검증할 데이터를 축적.
  → 나중에 뉴스봇을 나비봇 등 매매와 결합할 때의 '근거 자료'.

흐름:
  1. log_sent()  : 뉴스봇이 발송할 때마다 (날짜/종목/점수/키워드/신규성) 기록
  2. (나중에) track_returns.py 가 N일 후 주가를 채워넣어 검증
     → "점수 80↑ 뉴스 종목은 평균 +X% 올랐다" 같은 통계

저장: news_track_log.jsonl (한 줄=한 발송기록, append-only)
  ★영구저장: github_backup 으로 깃허브에 자동 커밋/복원 (Render 휘발성 대응)
  - import 시 깃허브에서 복원 (restore)
  - log_sent 후 깃허브에 커밋 (backup)
  - GITHUB_TOKEN 없으면 백업/복원 자동 생략 (로컬만, 에러 없음)
"""
import os, json, datetime, re

LOG_PATH = os.environ.get('NEWS_TRACK_LOG', 'news_track_log.jsonl')
KST = datetime.timezone(datetime.timedelta(hours=9))

# ── 깃허브 영구저장 (있으면 사용, 없으면 무시) ──
_GH = None
try:
    import github_backup as _GH
    # 시작 시 깃허브에서 복원 (Render 재시작 대응)
    try:
        _GH.restore_from_github(LOG_PATH)
    except Exception as _e:
        print(f"  [news_tracker] 복원 생략: {str(_e)[:50]}", flush=True)
except Exception:
    _GH = None   # github_backup.py 없거나 토큰 없으면 로컬만

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
    for nm in sorted(names.keys(), key=len, reverse=True):
        if len(nm) >= 2 and nm in title:
            code = names[nm]
            if code not in found:
                found[code] = nm
    for al, code in aliases.items():
        if len(al) >= 2 and al in title and code not in found:
            found[code] = al
    return [{'name': v, 'code': k} for k, v in found.items()][:3]

def extract_stock(title, stocks_in_title, STOCK_DB=None, stock_db=None):
    """발송 뉴스에서 관련 종목 추출. 사전 매칭 우선."""
    hits = match_stocks_in_title(title)
    if hits:
        return hits
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
    """발송한 뉴스 리스트를 추적 로그에 append. 그 후 깃허브에 백업."""
    if now is None:
        now = datetime.datetime.now(KST)
    ts = now.strftime('%Y-%m-%d %H:%M:%S')
    date = now.strftime('%Y-%m-%d')
    lines = []
    for n in send_list:
        title = n.get('title', '')
        themes = n.get('themes', [])
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
            'related_stocks': related,
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
        # ★깃허브 백업 (토큰 있을 때만, 실패해도 무시)
        if _GH is not None:
            try:
                _GH.backup_to_github(LOG_PATH, msg=f'추적로그 {date} ({len(lines)}건)')
            except Exception as e:
                print(f"  [news_tracker] 깃허브 백업 실패: {str(e)[:50]}", flush=True)
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
    sample = [{
        'title': '한화에어로스페이스, 대규모 수주 계약 체결',
        'score': 85, 'themes': ['방산'], 'novelty': 'novel', 'novelty_pt': 6,
        'bonus': ['수주(+3)'], 'source': '연합뉴스', 'url': 'http://x',
    }]
    n = log_sent(sample)
    print(f"기록 {n}건 → {LOG_PATH}")
    for r in load_log():
        print(r['date'], r['score'], r['title'][:20], r['novelty'])
