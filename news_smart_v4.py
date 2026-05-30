# -*- coding: utf-8 -*-
"""
news_smart_v4.py
라인투자자산운용 뉴스 스마트 시스템 v4
- flush=True 추가 (클라우드 실시간 로그)
- 환경변수 우선 읽기
- RSS + 네이버API + DART 3중 수집
"""
import os, sys, re, time, datetime
import requests
from urllib.request import urlopen, Request
from html import unescape
from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

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
    '수주':3, '계약':3, '공급계약':3, '수출계약':3,
    '승인':3, 'FDA':3, '허가':3, '임상성공':3,
    '투자':2, '증설':2, '대규모':2, '확대':2,
    '서프라이즈':2, '최대실적':2, '흑자':2, '상향':2,
    '인수':2, '합병':2, '지분':2,
    '외국인':1, '기관':1, '매수':1,
}

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
    ('머니투데이', 'https://www.mt.co.kr/rss/rss.xml'),
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
            req = Request(url, headers=headers)
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
    d = datetime.date.today()
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

def extract_keywords(news_list):
    all_text = ' '.join([n['title'] for n in news_list])
    words = re.findall(r'[가-힣A-Z]{2,}', all_text)
    words = [w for w in words if w not in STOPWORDS]
    word_count = Counter(words)
    word_sources = defaultdict(set)
    for n in news_list:
        for w in set(re.findall(r'[가-힣A-Z]{2,}', n['title'])):
            if w not in STOPWORDS:
                word_sources[w].add(n['source'])
    return word_count, word_sources

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

def score_news(news_list, word_count, word_sources):
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
        time_weight = n.get('time_weight', 0.5)
        final_score = int(score * time_weight)
        themes = detect_theme(title)
        scored.append({
            'title': title, 'url': n.get('url', ''),
            'source': n['source'], 'time_weight': time_weight,
            'score': final_score, 'bonus': list(set(bonus_matched))[:3],
            'themes': themes,
        })
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored

def send_telegram(bot_token, chat_id, message):
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'},
            timeout=10
        )
        if r.status_code == 200:
            log("  [텔레그램] 발송 완료")
        else:
            log(f"  [텔레그램] 오류: {r.status_code}")
    except Exception as e:
        log(f"  [텔레그램] 오류: {str(e)[:50]}")

def build_report(scored_news, keyword_ranking, now_str):
    msg  = f"<b>[라인 뉴스 브리핑] {now_str}</b>\n\n"
    if keyword_ranking:
        kw_str = ' | '.join([f"{w}({c})" for w,c in keyword_ranking[:5]])
        msg += f"<b>핵심 키워드</b>\n{kw_str}\n\n"
    msg += "<b>주목 뉴스 (점수순)</b>"
    for i, n in enumerate(scored_news[:5], 1):
        tw = n['time_weight']
        if tw >= 1.0:   time_tag = "🟢"
        elif tw >= 0.8: time_tag = "🔵"
        elif tw >= 0.6: time_tag = "🟡"
        else:           time_tag = "⚪"
        bonus_str = ' '.join(n['bonus'][:2]) if n['bonus'] else ''
        theme_str = '/'.join(n['themes'][:2]) if n['themes'] else ''
        msg += f"\n\n{i}. {time_tag} <b>{n['title'][:38]}</b>"
        msg += f"\n   점수:{n['score']} | {theme_str} | {bonus_str}"
        if n['themes']:
            stocks = get_theme_stocks(n['themes'])
            if stocks:
                msg += f"\n   관련종목: {' '.join(stocks[:4])}"
        if n.get('url'):
            msg += f"\n   <a href='{n['url']}'>기사 보기</a>"
    msg += "\n\n<i>라인투자자산운용 | 투자판단은 본인책임</i>"
    return msg

def main():
    log("=" * 60)
    log("  라인 뉴스 스마트 시스템 v4")
    log("  RSS + 네이버API + DART 3중 수집")
    log("  [5분마다 체크]")
    log("=" * 60)

    cfg = load_config()
    dart_key  = cfg.get('DART_API_KEY', '')
    naver_id  = cfg.get('NAVER_CLIENT_ID', '')
    naver_sec = cfg.get('NAVER_CLIENT_SECRET', '')
    bot_token = cfg.get('TELEGRAM_BOT_TOKEN', '')
    chat_id   = cfg.get('TELEGRAM_CHAT_ID', '')

    log(f"  DART: {'OK' if dart_key else 'MISSING'}")
    log(f"  네이버: {'OK' if naver_id else 'MISSING'}")
    log(f"  텔레그램: {'OK' if bot_token else 'MISSING'}")

    sent_titles = set()
    cycle = 0
    log("\n  시작!\n")

    while True:
        cycle += 1
        now     = datetime.datetime.now()
        now_str = now.strftime('%Y-%m-%d %H:%M')
        log(f"[{now.strftime('%H:%M:%S')}] 체크 #{cycle}")

        # 수집
        all_news = []
        rss_news = fetch_rss()
        all_news.extend(rss_news)
        api_news = fetch_naver_api(naver_id, naver_sec)
        all_news.extend(api_news)
        dart_news = fetch_dart(dart_key)
        all_news.extend(dart_news)

        # 중복 제거
        seen = set(); unique = []
        for n in all_news:
            if n['title'] not in seen:
                seen.add(n['title']); unique.append(n)
        all_news = unique

        log(f"  전체 수집: {len(all_news)}건")

        if not all_news:
            log("  뉴스 없음\n")
            time.sleep(300)
            continue

        # 키워드 추출
        word_count, word_sources = extract_keywords(all_news)
        keyword_ranking = [
            (w, c) for w, c in word_count.most_common(10)
            if c >= 3 and w not in STOPWORDS
        ]
        log(f"  키워드: {', '.join([f'{w}({c})' for w,c in keyword_ranking[:5]])}")

        # 점수화
        scored = score_news(all_news, word_count, word_sources)
        new_scored = [n for n in scored if n['title'] not in sent_titles]

        for n in new_scored[:3]:
            log(f"  [{n['score']}점] {n['title'][:35]}")

        # 발송
        should_send = False
        urgent = [n for n in new_scored if n['score'] >= 40]
        if urgent: should_send = True
        if cycle % 6 == 1: should_send = True

        if should_send and bot_token and chat_id:
            msg = build_report(new_scored, keyword_ranking, now_str)
            send_telegram(bot_token, chat_id, msg)
            for n in new_scored[:5]:
                sent_titles.add(n['title'])

        log(f"  다음: 5분 후\n")
        time.sleep(300)

if __name__ == '__main__':
    main()
