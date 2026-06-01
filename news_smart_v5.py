# -*- coding: utf-8 -*-
"""
news_smart_v4.py
라인투자자산운용 뉴스 스마트 시스템 v4 (수정판)
- flush=True (클라우드 실시간 로그)
- 환경변수 우선 읽기
- RSS + 네이버API + DART 3중 수집
[수정 내역]
  (1) 구글뉴스 한글 URL 인코딩 → ascii 에러 해결
  (2) 머니투데이 RSS 폐지 → 이데일리 주식뉴스로 교체
  (3) 메인 루프 try/except → 한 사이클 실패해도 봇 안 죽음
  (4) KST 시간 고정 → 서버가 UTC여도 시각/DART 날짜 정확
  (5) 텔레그램 HTML escape → 특수문자(&,<,>) 발송 거부 방지
  (6) [1단계] 키워드 급증률 계산 + 장전(08:00) 브리핑 → 오늘 주목 키워드 & 관련주
"""
import os, sys, re, time, datetime, html, json
import requests
from urllib.request import urlopen, Request
from urllib.parse import quote                      # (1)
from html import unescape
from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

# (7) 관련주 DB — 있으면 쓰고, 없으면 기존 테마매핑으로 자동 폴백
try:
    import stock_db
except Exception:
    stock_db = None
STOCK_DB = None   # main()에서 로드

KST = datetime.timezone(datetime.timedelta(hours=9))  # (4) 한국시간 고정

# ── (6) 장전 브리핑 설정 ───────────────────────────────
BRIEF_HOUR        = 7      # 브리핑 발송 시각(시). NXT 프리마켓(08:00) 전.
BRIEF_MIN         = 40     # 브리핑 발송 시각(분) → 07:40
BRIEF_MIN_ARTICLES= 3      # 키워드가 오늘 최소 몇 개 기사에 떠야 후보로 인정
BASELINE_DAYS     = 5      # 급증률 기준이 되는 '최근 며칠' 평균
TOP_N_BRIEF       = 7      # 브리핑에 담을 키워드 수
STATE_FILE        = os.environ.get('STATE_FILE', 'kw_state.json')  # 일별 키워드 누적 저장

# ── 환경변수 우선, config.env fallback
def load_config():
    config = {}
    for key in ['DART_API_KEY','NAVER_CLIENT_ID','NAVER_CLIENT_SECRET',
                'TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID']:
        val = os.environ.get(key, '')
        if val:
            config[key] = val
    env_path = r'D:\news_bot\config.env'
    if os.path.exists(env_path):
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    k = k.strip(); v = v.strip()
                    if k not in config and v:
                        config[k] = v
    return config

THEME_SECTOR = {
    '반도체':  ['반도체', 'HBM', 'AI반도체', '파운드리', 'D램', '삼성전자', 'SK하이닉스'],
    'AI':      ['AI', '인공지능', '데이터센터', 'GPU', '클라우드', '엔비디아'],
    '2차전지': ['배터리', '2차전지', '전기차', '양극재', '전고체'],
    '바이오':  ['바이오', '신약', '임상', 'FDA', '치료제', '백신'],
    '방산':    ['방산', '무기', 'K방산', '미사일', '수출계약'],
    '원전':    ['원전', 'SMR', '핵연료', '원전수출'],
    '조선':    ['조선', 'LNG선', '수주잔고', '선박'],
    '전력':    ['전력망', '변압기', '송전', '전력인프라'],
    '로봇':    ['로봇', '휴머노이드', '자율주행'],
    '제약':    ['제약', '의약품', '헬스케어'],
}

BONUS_KEYWORDS = {
    # ★ 3점 — 거의 무조건 주가 반응
    '수주':3, '계약':3, '공급계약':3, '수출계약':3, '대규모 계약':3, '계약 체결':3,
    '승인':3, 'FDA':3, '임상성공':3, '임상 성공':3, '임상통과':3, '품목허가':3,
    '자사주 매입':3, '자사주 소각':3, '무상증자':3, '공개매수':3,
    '깜짝 실적':3, '사상 최대':3, '어닝 서프라이즈':3, '흑자 전환':3,
    '특허 등록':3, '세계 최초':3, '독점 기술':3,
    # ★ 2점 — 높은 확률로 반응
    '대통령 지시':2, '정부 지원':2, '국책 사업':2, '정책 수혜':2, '예산 반영':2,
    '인수':2, '합병':2, '피인수':2, '지분 취득':2, '전략적 투자':2,
    '목표주가 상향':2, '투자의견 상향':2, '실적 개선':2,
    '방산 수출':2, '원전 수주':2, '조선 수주':2,
    '외국인 순매수':2, '기관 순매수':2, '블록딜':2,
    '서프라이즈':2, '최대실적':2, '흑자':2, '상향':2,
    '증설':2, '대규모':2,
    # ★ 1점 — 참고 수준
    '배당 확대':1, '주주 환원':1, '밸류업':1,
    '코스피 편입':1, '지수 편입':1,
    '신제품':1, '신규 사업':1, '파트너십':1,
    '외국인':1, '기관':1, '매수':1, '투자':1, '지분':1,
}

# ★ 급등 키워드 — 두 그룹으로 분리
# (A) 강한 신호 = 점수 무관 즉시 발송 (진짜 그날 오른 종목 뉴스)
SURGE_STRONG = [
    '신고가', '52주 최고', '서킷브레이커', '급반등',
    '자사주 매입', '자사주 소각', '무상증자', '공개매수', '상장폐지 철회',
    '임상성공', '임상 성공', '임상통과', 'FDA 승인', '품목허가',
    '대규모 수주', '조 단위', '억달러 수주', '억달러 계약', '조원 수주', '조원 계약',
    '대규모 계약', '대통령 지시', '국책 사업', '세계 최초', '독점 기술',
]
# (B) 맥락 키워드 = 점수 높을 때만 (시황·회고 기사에도 흔히 쓰임)
SURGE_CONTEXT = [
    '특징주', '상한가', '급등주', '급등', '급상승', '폭등', '깜짝 실적', '사상 최대', '어닝 서프라이즈',
    '흑자 전환', '최대 실적', '계약 체결', '수출 계약', '정책 수혜', '특허 등록',
]
# 하위호환 — 전체 합친 리스트
SURGE_KEYWORDS = SURGE_STRONG + SURGE_CONTEXT

# ★ 블랙리스트 — 제목에 이게 있으면 SURGE라도 무조건 차단 (낚시·부정·잡주)
BLACKLIST_KEYWORDS = [
    # 낚시·회의성 제목
    '매출 0원', '매출 0', '직행', '진짜?', '왜?', '진짜', '논란', '의혹',
    '주의보', '주의', '경고', '경고음', '거품', '버블', '과열',
    # 부정·하락 (급등주와 반대)
    '급락', '폭락', '하한가', '추락', '곤두박질', '미끄', '하락 전환',
    '상장폐지', '거래정지', '관리종목', '횡령', '배임', '분식',
    # 회고·잡설
    '왜 올랐나', '왜 떨어', '뒤늦게', '알고보니', '알고 보니',
    # 사건사고 (투자정보 무관)
    '폭발 사고', '폭발 추정', '폭발사고', '폭발 추정 사고',
    '사망', '부상', '화재', '붕괴', '참사', '사고로', '숨져', '숨진',
    '실종', '추락사', '감전', '누출', '중독', '체포', '구속', '압수수색',
]

STOPWORDS = {
    '기자','뉴스','특파원','기사','관련','이후','현재',
    '대한','통해','위해','따라','대해','이번','지난',
    '오늘','내일','올해','시장','국내','해외','전망',
    '분석','보고','증권','주식','경제','한국','미국',
    '중국','일본','글로벌','세계','기업','회사','산업',
}

RSS_FEEDS = [
    ('연합뉴스', 'https://www.yonhapnewstv.co.kr/category/news/economy/feed/'),
    ('한국경제', 'https://www.hankyung.com/feed/all-news'),
    ('매일경제', 'https://www.mk.co.kr/rss/30000001/'),
    # (2) 머니투데이 RSS 폐지(404) → 이데일리 주식뉴스로 교체.
    #     혹시 이 주소도 죽으면 봇이 알아서 [RSS] 오류 한 줄 찍고 넘어감(무해).
    ('이데일리_주식', 'http://rss.edaily.co.kr/stock_news.xml'),
    ('구글뉴스_반도체', 'https://news.google.com/rss/search?q=반도체+수주&hl=ko&gl=KR&ceid=KR:ko'),
    ('구글뉴스_AI', 'https://news.google.com/rss/search?q=AI+투자&hl=ko&gl=KR&ceid=KR:ko'),
    ('구글뉴스_바이오', 'https://news.google.com/rss/search?q=바이오+임상&hl=ko&gl=KR&ceid=KR:ko'),
    ('구글뉴스_방산', 'https://news.google.com/rss/search?q=방산+수출&hl=ko&gl=KR&ceid=KR:ko'),
]

API_SEARCH = [
    '수주 계약', '임상 승인', '반도체 AI',
    '2차전지 배터리', '바이오 신약',
    '외국인 매수', '실적 서프라이즈', '대규모 투자',
]

def log(msg):
    print(msg, flush=True)

def clean_text(text):
    text = unescape(text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&[a-zA-Z#0-9]+;', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def get_time_weight(pub_date_str):
    if not pub_date_str: return 0.5
    try:
        pub_dt = parsedate_to_datetime(pub_date_str)
        now = datetime.datetime.now(pub_dt.tzinfo)
        diff_min = (now - pub_dt).total_seconds() / 60
        if diff_min <= 5:    return 1.0
        elif diff_min <= 30: return 0.8
        elif diff_min <= 60: return 0.6
        elif diff_min <= 180:return 0.3
        elif diff_min <= 360:return 0.1
        else:                return 0.05
    except:
        return 0.5

def fetch_rss():
    news = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    for source, url in RSS_FEEDS:
        try:
            # (1) 한글 등 비ASCII 문자를 퍼센트 인코딩 (URL 구조 문자는 보존)
            safe_url = quote(url, safe="%/:=&?~#+!$,;'@()*[]")
            req = Request(safe_url, headers=headers)
            content = urlopen(req, timeout=8).read()
            try:
                root = ET.fromstring(content)
            except:
                text = content.decode('utf-8', errors='ignore')
                text = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;)', '&amp;', text)
                root = ET.fromstring(text.encode())
            count = 0
            for item in root.findall('.//item')[:20]:
                title    = clean_text(item.findtext('title', ''))
                link     = item.findtext('link', '')
                pub_date = item.findtext('pubDate', '')
                if title and len(title) > 5:
                    news.append({
                        'source': source, 'title': title,
                        'url': link, 'pub_date': pub_date,
                        'time_weight': get_time_weight(pub_date)
                    })
                    count += 1
            log(f"  [RSS] {source}: {count}건")
        except Exception as e:
            log(f"  [RSS] {source} 오류: {str(e)[:50]}")
    return news

def fetch_naver_api(naver_id, naver_sec):
    news = []
    if not naver_id or not naver_sec:
        log("  [네이버API] 키 없음 스킵")
        return news
    headers = {
        'X-Naver-Client-Id': naver_id,
        'X-Naver-Client-Secret': naver_sec
    }
    for keyword in API_SEARCH[:6]:
        try:
            r = requests.get(
                'https://openapi.naver.com/v1/search/news.json',
                headers=headers,
                params={'query': keyword, 'display': 8, 'sort': 'date'},
                timeout=8
            )
            if r.status_code == 200:
                items = r.json().get('items', [])
                for item in items:
                    title    = clean_text(item.get('title', ''))
                    pub_date = item.get('pubDate', '')
                    if title:
                        news.append({
                            'source': f'네이버({keyword})',
                            'title': title,
                            'url': item.get('link', ''),
                            'pub_date': pub_date,
                            'time_weight': get_time_weight(pub_date)
                        })
            time.sleep(0.1)
        except Exception as e:
            log(f"  [네이버API] {keyword} 오류: {str(e)[:30]}")
    log(f"  [네이버API] 총 {len(news)}건")
    return news

def fetch_dart(dart_key):
    if not dart_key:
        log("  [DART] 키 없음 스킵")
        return []
    d = datetime.datetime.now(KST).date()         # (4) KST 기준 오늘
    if d.weekday() == 5: d -= datetime.timedelta(days=1)
    if d.weekday() == 6: d -= datetime.timedelta(days=2)
    bgn = d.strftime('%Y%m%d')
    try:
        r = requests.get('https://opendart.fss.or.kr/api/list.json', params={
            'crtfc_key': dart_key, 'bgn_de': bgn, 'end_de': bgn,
            'page_count': 40, 'sort': 'date', 'sort_mth': 'desc'
        }, timeout=10)
        data = r.json()
        if data.get('status') != '000':
            log(f"  [DART] {data.get('message')}")
            return []
        items = data.get('list', [])
        watch = ['신규시설투자','타법인주식취득','유상증자','무상증자',
                 '전환사채','자기주식','합병','분할','영업양수도',
                 '최대주주변경','주요사항','자본금변경']
        filtered = [
            {'source':'DART','type':'dart',
             'corp':i.get('corp_name',''),'title':i.get('report_nm',''),
             'pub_date':'', 'time_weight':0.8,
             'url': f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={i.get('rcept_no','')}"}
            for i in items if any(k in i.get('report_nm','') for k in watch)
        ]
        log(f"  [DART] {len(filtered)}건 ({bgn})")
        return filtered[:15]
    except Exception as e:
        log(f"  [DART] 오류: {str(e)[:50]}")
        return []

def title_keywords(title):
    """제목에서 키워드 집합 추출 (2글자 이상 한글/영대문자, 불용어 제외)"""
    return {w for w in re.findall(r'[가-힣A-Z]{2,}', title) if w not in STOPWORDS}

# ── [v5] 신규성 점수 (기관급 NLP의 '신규성' 요소, 무료·가벼움) ──
#  최근 본 뉴스들의 키워드 집합과 자카드 유사도를 재서,
#  많이 겹치면(재탕) 점수 깎고, 거의 안 겹치면(새 정보) 보너스.
#  임베딩 라이브러리 불필요 — 이미 있는 title_keywords()만 사용.
NOVELTY_RECENT_MAX = 200    # 최근 몇 개 뉴스 키워드집합을 기억할지
NOVELTY_SIM_HIGH   = 0.6    # 이 이상 겹치면 거의 같은 뉴스(재탕)
NOVELTY_SIM_MID    = 0.3    # 이 이상이면 비슷한 주제

def jaccard(a, b):
    """두 키워드 집합의 자카드 유사도 (0~1)"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0

def novelty_score(kw_set, recent_kw_sets):
    """신규성 점수 반환 (양수=새정보 보너스 / 음수=재탕 페널티).
       recent_kw_sets: 최근 본 뉴스들의 키워드집합 리스트(deque)."""
    if not kw_set:
        return 0, 'empty'
    if not recent_kw_sets:
        return 5, 'first'          # 기준 없을 때(초기) 첫 뉴스는 약보너스
    # 최근 뉴스들 중 가장 비슷한 것과의 유사도
    max_sim = max(jaccard(kw_set, prev) for prev in recent_kw_sets)
    if max_sim >= NOVELTY_SIM_HIGH:
        return -8, 'dup'           # 재탕 — 강한 페널티
    elif max_sim >= NOVELTY_SIM_MID:
        return -3, 'similar'       # 비슷한 주제 — 약한 페널티
    else:
        return 6, 'novel'          # 새 정보 — 보너스

def extract_keywords(news_list):
    all_text = ' '.join([n['title'] for n in news_list])
    words = re.findall(r'[가-힣A-Z]{2,}', all_text)
    words = [w for w in words if w not in STOPWORDS]
    word_count = Counter(words)
    word_sources = defaultdict(set)
    for n in news_list:
        for w in title_keywords(n['title']):
            word_sources[w].add(n['source'])
    return word_count, word_sources

# ── (6) 급증률 상태 저장/로드 ───────────────────────────
def load_state():
    """일별 키워드 누적 상태를 파일에서 로드"""
    state = {'daily': {}, 'last_briefing': ''}
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, encoding='utf-8') as f:
                state = json.load(f)
    except Exception as e:
        log(f"  [상태 로드 오류] {str(e)[:50]}")
    state.setdefault('daily', {})        # {날짜: {'total': N, 'kw': {키워드:기사수}}}
    state.setdefault('last_briefing', '')
    return state

def save_state(state):
    try:
        # 오래된 날짜는 정리 (최근 BASELINE_DAYS+3일만 보관)
        days = sorted(state['daily'].keys())
        for d in days[:-(BASELINE_DAYS + 3)]:
            del state['daily'][d]
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        log(f"  [상태 저장 오류] {str(e)[:50]}")

def accumulate_daily(state, today, new_articles):
    """오늘 새로 들어온 기사들의 키워드를 일별 누적에 더함 (기사 단위로 1회씩)"""
    day = state['daily'].setdefault(today, {'total': 0, 'kw': {}})
    for n in new_articles:
        day['total'] += 1
        for kw in title_keywords(n['title']):
            day['kw'][kw] = day['kw'].get(kw, 0) + 1

def compute_surges(state, today):
    """오늘 키워드 비중 ÷ 최근 평균 비중 = 급증률. (키워드, 오늘기사수, 급증률, 기준유무) 리스트 반환"""
    today_day = state['daily'].get(today)
    if not today_day or today_day['total'] == 0:
        return [], False
    today_total = today_day['total']

    # 기준일(과거) 데이터 모으기
    past_dates = [d for d in sorted(state['daily'].keys()) if d < today][-BASELINE_DAYS:]
    has_baseline = len(past_dates) >= 1

    results = []
    for kw, cnt in today_day['kw'].items():
        if cnt < BRIEF_MIN_ARTICLES:
            continue
        today_share = cnt / today_total
        if has_baseline:
            shares = []
            for d in past_dates:
                dd = state['daily'][d]
                if dd['total'] > 0:
                    shares.append(dd['kw'].get(kw, 0) / dd['total'])
            base_share = (sum(shares) / len(shares)) if shares else 0.0
            # 기준 비중이 0이면(과거 거의 안 나옴) 신규 급등으로 크게 가중
            ratio = (today_share / base_share) if base_share > 0 else (today_share * 100)
        else:
            ratio = today_share  # 기준 없으면 오늘 비중 자체로 정렬
        results.append((kw, cnt, ratio))
    results.sort(key=lambda x: x[2], reverse=True)
    return results, has_baseline

def kw_related_stocks(kw):
    """키워드와 연관된 종목. (7) 관련주 DB 있으면 우선, 없으면 테마매핑 폴백"""
    if STOCK_DB and stock_db:
        hits = stock_db.match_keyword(STOCK_DB, kw, limit=5)
        if hits:
            return [h['name'] for h in hits]
    themes = detect_theme(kw)
    return get_theme_stocks(themes) if themes else []

def build_briefing(surges, has_baseline, today):
    msg = f"<b>📈 장전 브리핑 — 오늘 주목 키워드 &amp; 관련주</b>\n<i>{html.escape(today)} (장 시작 전)</i>\n\n"
    if not surges:
        msg += "오늘은 기준치를 넘는 급증 키워드가 없어요. (조용한 장)\n"
        msg += "\n<i>라인투자자산운용 | 사실·재료 정리일 뿐, 투자판단은 본인책임</i>"
        return msg
    if not has_baseline:
        msg += "<i>※ 기준 데이터 누적 중 — 며칠 더 쌓이면 '급증률'이 정확해져요.</i>\n\n"
    for i, (kw, cnt, ratio) in enumerate(surges[:TOP_N_BRIEF], 1):
        kw_s = html.escape(kw)
        if has_baseline:
            msg += f"{i}. <b>{kw_s}</b>  (기사 {cnt}건, 평소 대비 {ratio:.1f}배)"
        else:
            msg += f"{i}. <b>{kw_s}</b>  (오늘 기사 {cnt}건)"
        stocks = kw_related_stocks(kw)
        if stocks:
            msg += f"\n   관련종목: {html.escape(' '.join(stocks[:4]))}"
        msg += "\n"
    msg += "\n<i>라인투자자산운용 | 사실·재료 정리일 뿐, 투자판단은 본인책임</i>"
    return msg

def detect_theme(title):
    detected = []
    for theme, keys in THEME_SECTOR.items():
        for key in keys:
            if key in title:
                detected.append(theme)
                break
    return list(set(detected))

def get_theme_stocks(themes):
    fallback = {
        '반도체':  ['삼성전자', 'SK하이닉스', '한미반도체'],
        'AI':      ['삼성전자', 'SK하이닉스', 'NAVER'],
        '2차전지': ['LG에너지솔루션', '삼성SDI', '에코프로비엠'],
        '바이오':  ['삼성바이오로직스', '셀트리온', '유한양행'],
        '방산':    ['한화에어로스페이스', 'LIG넥스원', '현대로템'],
        '원전':    ['두산에너빌리티', '한전기술', '비에이치아이'],
        '조선':    ['HD한국조선해양', '삼성중공업', 'HD현대중공업'],
        '전력':    ['LS ELECTRIC', '효성중공업', '제룡전기'],
        '로봇':    ['현대로보틱스', '레인보우로보틱스', 'HD현대'],
        '제약':    ['유한양행', '종근당', '한미약품'],
    }
    stocks = []
    for theme in themes[:2]:
        stocks.extend(fallback.get(theme, [])[:3])
    return list(set(stocks))[:5]

def score_news(news_list, word_count, word_sources, recent_kw_sets=None):
    scored = []
    for n in news_list:
        title = n['title']
        score = 0
        bonus_matched = []
        words = [w for w in re.findall(r'[가-힣A-Z]{2,}', title) if w not in STOPWORDS]
        for word in words:
            freq = word_count.get(word, 0)
            if freq >= 3:
                src_cnt = len(word_sources.get(word, set()))
                score += freq + (src_cnt * 2)
        for bonus_kw, bonus_pt in BONUS_KEYWORDS.items():
            if bonus_kw in title:
                score += bonus_pt
                bonus_matched.append(f"{bonus_kw}(+{bonus_pt})")
        # [v5] 신규성 점수 — 재탕이면 깎고, 새 정보면 보너스
        kw_set = title_keywords(title)
        nov_pt, nov_tag = novelty_score(kw_set, recent_kw_sets if recent_kw_sets else [])
        score += nov_pt
        time_weight = n.get('time_weight', 0.5)
        final_score = int(score * time_weight)
        themes = detect_theme(title)
        scored.append({
            'title': title, 'url': n.get('url', ''),
            'source': n['source'], 'time_weight': time_weight,
            'score': final_score, 'bonus': list(set(bonus_matched))[:3],
            'themes': themes, 'novelty': nov_tag, 'novelty_pt': nov_pt,
            'kw_set': kw_set,
        })
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored

def send_telegram(bot_token, chat_id, message):
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML',
                  'disable_web_page_preview': False},   # 이미지(링크 미리보기) 켜기
            timeout=10
        )
        if r.status_code == 200:
            log("  [텔레그램] 발송 완료")
        else:
            log(f"  [텔레그램] 오류: {r.status_code} {r.text[:80]}")
    except Exception as e:
        log(f"  [텔레그램] 오류: {str(e)[:50]}")

def is_us_stock(title):
    """미국 종목 뉴스인지 판별 — 영문 티커(괄호) 또는 미국 기업/거래소 표시"""
    # (1) 영문 티커 패턴: 엑셀릭시스(EXEL), ST마이크로(STM) 등 — 괄호 안 영대문자 2~5자
    if re.search(r'\([A-Z]{2,5}\)', title):
        return True
    # (2) 미국 대표 기업·거래소 명시 (한국 영향 뉴스가 아닌 미국 단독 종목 뉴스)
    us_markers = [
        '나스닥', '뉴욕증시', 'NYSE', 'S&P', '다우', '월가',
        '엑셀릭시스', 'ST마이크로', '인피니언',
    ]
    return any(m in title for m in us_markers)


def build_report(scored_news, keyword_ranking, now_str, header):
    # header: "* 돈이 반응한 뉴스 - 투자자필독 *" 또는 "📊 상위 검색순위 뉴스"
    msg = ""
    # ★ 맨 위 1등 뉴스 이미지 1장 띄우기 — 대표 링크를 보이지 않는 앵커로 상단에 삽입
    #   (텔레그램은 메시지 첫 링크의 미리보기를 위에 보여줌. 구글뉴스 링크는 이미지 안 뜰 수 있음)
    if scored_news and scored_news[0].get('url'):
        top_url = html.escape(scored_news[0]['url'], quote=True)
        msg += f"<a href='{top_url}'>\u200b</a>"   # \u200b = 보이지 않는 공백(앵커용)
    msg += f"<b>{html.escape(header)}</b>\n<i>{html.escape(now_str)}</i>\n\n"
    if keyword_ranking:
        kw_str = ' | '.join([f"{html.escape(w)}({c})" for w,c in keyword_ranking[:5]])
        msg += f"<b>핵심 키워드</b>\n{kw_str}\n\n"
    msg += "<b>주목 뉴스 (점수순)</b>"
    for i, n in enumerate(scored_news[:5], 1):
        tw = n['time_weight']
        if tw >= 1.0:   time_tag = "🟢"
        elif tw >= 0.8: time_tag = "🔵"
        elif tw >= 0.6: time_tag = "🟡"
        else:           time_tag = "⚪"
        bonus_str = html.escape(' '.join(n['bonus'][:2]) if n['bonus'] else '')
        theme_str = html.escape('/'.join(n['themes'][:2]) if n['themes'] else '')
        title_safe = html.escape(n['title'][:38])           # (5) 제목 escape
        us_tag = " 🇺🇸<b>[미국]</b>" if is_us_stock(n['title']) else ""
        msg += f"\n\n{i}. {time_tag} <b>{title_safe}</b>{us_tag}"
        msg += f"\n   점수:{n['score']} | {theme_str} | {bonus_str}"
        if n['themes']:
            stocks = get_theme_stocks(n['themes'])
            if stocks:
                msg += f"\n   관련종목: {html.escape(' '.join(stocks[:4]))}"
        if n.get('url'):
            url_safe = html.escape(n['url'], quote=True)     # (5) URL escape
            msg += f"\n   <a href='{url_safe}'>기사 보기</a>"
    msg += "\n\n<i>라인투자자산운용 | 투자판단은 본인책임</i>"
    return msg


def build_night_briefing(state, today, top_news):
    """자정 결산 브리핑 메시지 생성"""
    today_day = state['daily'].get(today, {})
    kw_dict   = today_day.get('kw', {})
    total     = today_day.get('total', 0)
    escaped_today = html.escape(today)
    msg = "<b>\U0001f319 \uc624\ub298 \ud558\ub8e8 \ud0a4\uc6cc\ub4dc \uacb0\uc0b0 | " + escaped_today + "</b>\n"
    msg += "<i>\uc218\uc9d1 \uae30\uc0ac " + str(total) + "\uac74 \uae30\uc900</i>\n\n"
    if kw_dict:
        msg += "<b>\U0001f511 TOP5 \ud0a4\uc6cc\ub4dc</b>\n"
        sorted_kw = sorted(kw_dict.items(), key=lambda x: x[1], reverse=True)
        medals = ['1\uc704', '2\uc704', '3\uc704', '4\uc704', '5\uc704']
        for i, (kw, cnt) in enumerate(sorted_kw[:5]):
            msg += medals[i] + "  <b>" + html.escape(kw) + "</b>  (" + str(cnt) + "\uac74)\n"
    if top_news:
        msg += "\n<b>\U0001f525 \uc624\ub298\uc758 \uc8fc\ubaa9 \ub274\uc2a4 TOP3</b>\n"
        for i, n in enumerate(top_news[:3], 1):
            title = html.escape(n.get('title', '')[:35])
            score = n.get('score', 0)
            msg += str(i) + ". " + title + "\u2026 (" + str(score) + "\uc810)\n"
    if kw_dict:
        top1 = sorted(kw_dict.items(), key=lambda x: x[1], reverse=True)[0][0]
        msg += "\n<b>\U0001f4a1 \ub0b4\uc77c \uc8fc\ubaa9\ud560 \ud0a4\uc6cc\ub4dc</b>\n\u2192 <b>" + html.escape(top1) + "</b> \ud750\ub984 \uc9c0\uc18d \uc5ec\ubd80 \uccb4\ud06c\n"
    msg += "\n<i>\ub77c\uc778\ud22c\uc790\uc790\uc0b0\uc6b4\uc6a9 | \uc0ac\uc2e4\u00b7\uc7ac\ub8cc \uc815\ub9ac\uc77c \ubfd0, \ud22c\uc790\ud310\ub2e8\uc740 \ubcf8\uc778\ucc45\uc784</i>"
    return msg


def check_night_briefing(cfg, state, now, today, top_news):
    """\ub9e4\uc77c \uc790\uc815(00:00~00:10) \ud558\ub8e8 \uacb0\uc0b0 \ube0c\ub9ac\ud551 \ubc1c\uc1a1"""
    bot_token = cfg.get('TELEGRAM_BOT_TOKEN', '')
    chat_id   = cfg.get('TELEGRAM_CHAT_ID', '')
    if not (bot_token and chat_id):
        return
    now_min = now.hour * 60 + now.minute
    if not (0 <= now_min < 10):
        return
    yesterday = (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    if state.get('last_night_briefing') == today:
        return
    msg = build_night_briefing(state, yesterday, top_news)
    send_telegram(bot_token, chat_id, msg)
    state['last_night_briefing'] = today
    log("  -> \ubc1c\uc1a1: \uc790\uc815 \uacb0\uc0b0 \ube0c\ub9ac\ud551 (" + yesterday + " TOP5)")


def maybe_send_briefing(cfg, state, now, today):
    """평일 아침 BRIEF_HOUR시대에 하루 한 번 장전 브리핑 발송"""
    bot_token = cfg.get('TELEGRAM_BOT_TOKEN', '')
    chat_id   = cfg.get('TELEGRAM_CHAT_ID', '')
    if not (bot_token and chat_id):
        return
    if state.get('last_briefing') == today:      # 오늘 이미 발송
        return
    if now.weekday() >= 5:                        # 토(5)/일(6) = 장 없음
        return
    # 07:40부터 정규장 시작(09:00) 전까지: 이 구간 첫 사이클에 한 번 발송
    now_min   = now.hour * 60 + now.minute
    brief_min = BRIEF_HOUR * 60 + BRIEF_MIN       # 07:40 = 460
    market_open_min = 9 * 60                       # 09:00 = 540
    if not (brief_min <= now_min < market_open_min):
        return
    surges, has_baseline = compute_surges(state, today)
    msg = build_briefing(surges, has_baseline, today)
    send_telegram(bot_token, chat_id, msg)
    state['last_briefing'] = today
    log(f"  → 발송: 📈 장전 브리핑 (키워드 {len(surges[:TOP_N_BRIEF])}개, 기준{'있음' if has_baseline else '누적중'})")

def run_cycle(cfg, sent_titles, cycle, state, mem):
    """한 번의 수집·점수·발송 사이클 (예외는 main에서 잡음)"""
    dart_key  = cfg.get('DART_API_KEY', '')
    naver_id  = cfg.get('NAVER_CLIENT_ID', '')
    naver_sec = cfg.get('NAVER_CLIENT_SECRET', '')
    bot_token = cfg.get('TELEGRAM_BOT_TOKEN', '')
    chat_id   = cfg.get('TELEGRAM_CHAT_ID', '')

    now     = datetime.datetime.now(KST)              # (4) KST
    today   = now.strftime('%Y-%m-%d')
    now_str = now.strftime('%Y-%m-%d %H:%M')
    log(f"[{now.strftime('%H:%M:%S')}] 체크 #{cycle}")

    # (6) 날짜 바뀌면 '오늘 본 기사' 집합 리셋
    if mem.get('seen_date') != today:
        mem['seen_date'] = today
        mem['seen_today'] = set()

    # 수집
    all_news = []
    all_news.extend(fetch_rss())
    all_news.extend(fetch_naver_api(naver_id, naver_sec))
    all_news.extend(fetch_dart(dart_key))

    # 중복 제거
    seen = set(); unique = []
    for n in all_news:
        if n['title'] not in seen:
            seen.add(n['title']); unique.append(n)
    all_news = unique
    log(f"  전체 수집: {len(all_news)}건")

    if all_news:
        # 키워드 추출
        word_count, word_sources = extract_keywords(all_news)
        keyword_ranking = [
            (w, c) for w, c in word_count.most_common(10)
            if c >= 3 and w not in STOPWORDS
        ]
        log(f"  키워드: {', '.join([f'{w}({c})' for w,c in keyword_ranking[:5]])}")

        # 점수화 ([v5] 신규성 점수용 최근 키워드집합 전달)
        recent_kw_sets = mem.get('recent_kw_sets', [])
        scored = score_news(all_news, word_count, word_sources, recent_kw_sets)

        # ★ 블랙리스트 사전 차단 — 낚시·부정·잡주 제목은 발송 후보에서 제외
        def is_blacklisted(news):
            title = news.get('title', '')
            return any(bad in title for bad in BLACKLIST_KEYWORDS)

        new_scored = [n for n in scored
                      if n['title'] not in sent_titles and not is_blacklisted(n)]
        for n in new_scored[:3]:
            log(f"  [{n['score']}점] {n['title'][:35]}")

        # 발송 판단
        def has_strong(news):   # 강한 신호 = 점수 무관 (상한가·특징주 등)
            title = news.get('title', '')
            return any(kw in title for kw in SURGE_STRONG)

        def has_context(news):  # 맥락 키워드 = 점수 높을 때만 (폭등·사상최대 등)
            title = news.get('title', '')
            return any(kw in title for kw in SURGE_CONTEXT)

        def has_bonus(news):
            title = news.get('title', '')
            return any(kw in title for kw in BONUS_KEYWORDS)

        # ★ 즉시발송: 80점↑ OR 강한신호(점수무관) OR 맥락키워드+30점↑
        urgent = [n for n in new_scored
                  if n['score'] >= 80
                  or has_strong(n)
                  or (has_context(n) and n['score'] >= 30)]
        should_send = False
        header = None
        send_list = []
        if urgent:
            should_send = True
            header = "* 돈이 반응한 뉴스 - 투자자필독 *"
            send_list = urgent
        elif cycle % 24 == 0 and cycle > 0:          # ★정기: 2시간마다 (첫사이클 제외)
            # ★30점↑ OR 강한신호 OR (BONUS+15점↑)
            filtered = [n for n in new_scored
                        if n['score'] >= 30
                        or has_strong(n)
                        or (has_bonus(n) and n['score'] >= 15)]
            if filtered:
                should_send = True
                header = "📊 상위 검색순위 뉴스"
                send_list = filtered

        if should_send and bot_token and chat_id and send_list:
            msg = build_report(send_list, keyword_ranking, now_str, header)
            send_telegram(bot_token, chat_id, msg)
            log(f"  → 발송: {header} ({len(send_list[:5])}건)")
            for n in send_list[:5]:
                sent_titles.add(n['title'])
            # [v5] 추적 로그 — 발송한 뉴스를 기록 (나중에 주가 추적 → 점수 검증용)
            try:
                import news_tracker
                news_tracker.log_sent(send_list[:5], now, STOCK_DB, stock_db)
            except Exception as e:
                log(f"  [추적로그] 스킵: {str(e)[:40]}")

        # [v5] 신규성 기억 누적 — 이번 사이클 뉴스 키워드집합을 최근목록에 추가
        from collections import deque as _deque
        if not isinstance(mem.get('recent_kw_sets'), _deque):
            mem['recent_kw_sets'] = _deque(mem.get('recent_kw_sets', []), maxlen=NOVELTY_RECENT_MAX)
        for n in scored:
            ks = n.get('kw_set')
            if ks:
                mem['recent_kw_sets'].append(ks)

        # (6) 일별 키워드 누적 — 오늘 처음 보는 기사만 1회씩
        new_articles = [n for n in all_news if n['title'] not in mem['seen_today']]
        for n in new_articles:
            mem['seen_today'].add(n['title'])
        accumulate_daily(state, today, new_articles)
    else:
        log("  뉴스 없음")

    # (6) 장전 브리핑 발송 체크 + 상태 저장
    maybe_send_briefing(cfg, state, now, today)

    # \uc790\uc815 \uacb0\uc0b0 \ube0c\ub9ac\ud551
    top_scored = scored[:10] if all_news else []
    check_night_briefing(cfg, state, now, today, top_scored)

    save_state(state)

    # 메모리 관리: 발송 기록이 너무 커지면 비움
    if len(sent_titles) > 3000:
        sent_titles.clear()

def main():
    log("=" * 60)
    log("  라인 뉴스 스마트 시스템 v4")
    log("  RSS + 네이버API + DART 3중 수집")
    log("  [5분마다 체크]")
    log("=" * 60)

    cfg = load_config()
    log(f"  DART: {'OK' if cfg.get('DART_API_KEY') else 'MISSING'}")
    log(f"  네이버: {'OK' if cfg.get('NAVER_CLIENT_ID') else 'MISSING'}")
    log(f"  텔레그램: {'OK' if cfg.get('TELEGRAM_BOT_TOKEN') else 'MISSING'}")

    state = load_state()                       # (6) 일별 키워드 누적 로드
    days = len(state.get('daily', {}))
    log(f"  급증률 기준 데이터: {days}일치 보유")

    global STOCK_DB                            # (7) 관련주 DB 로드
    if stock_db:
        STOCK_DB = stock_db.load_db()
    if STOCK_DB:
        log(f"  관련주 DB: 종목 {len(STOCK_DB['stocks'])} / 테마 {len(STOCK_DB['themes'])}")
    else:
        log("  관련주 DB: 없음 (테마매핑 폴백 사용)")

    from collections import deque
    mem = {'seen_date': '', 'seen_today': set(), 'recent_kw_sets': deque(maxlen=NOVELTY_RECENT_MAX)}

    # ★ sent_titles 파일 캐시 — 재시작 시 중복 방지 (/tmp는 재시작 사이엔 유지)
    SENT_CACHE = '/tmp/sent_titles_cache.json'
    def load_sent():
        try:
            import json as _j, time as _t
            data = _j.load(open(SENT_CACHE, encoding='utf-8'))
            now = _t.time()
            # 24시간 지난 항목 제거
            return set(t for t, ts in data.items() if now - ts < 86400)
        except Exception:
            return set()
    def save_sent(st):
        try:
            import json as _j, time as _t
            now = _t.time()
            existing = {}
            try:
                existing = _j.load(open(SENT_CACHE, encoding='utf-8'))
            except Exception:
                pass
            for t in st:
                if t not in existing:
                    existing[t] = now
            # 24시간 지난 것 정리
            existing = {t: ts for t, ts in existing.items() if now - ts < 86400}
            _j.dump(existing, open(SENT_CACHE, 'w', encoding='utf-8'), ensure_ascii=False)
        except Exception:
            pass

    sent_titles = load_sent()
    log(f"  캐시 로드: 기발송 {len(sent_titles)}건")
    cycle = 0
    log("\n  시작!\n")

    while True:
        cycle += 1
        try:                                          # (3) 한 사이클 실패해도 봇은 계속
            run_cycle(cfg, sent_titles, cycle, state, mem)
            save_sent(sent_titles)                   # ★ 매 사이클 후 캐시 저장
        except Exception as e:
            log(f"  [사이클 오류] {type(e).__name__}: {str(e)[:120]}")
        log("  다음: 5분 후\n")
        time.sleep(300)

if __name__ == '__main__':
    main()
