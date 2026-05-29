# -*- coding: utf-8 -*-
"""
news_smart_v2.py
라인투자자산운용 뉴스 스마트 시스템 v2
=========================================
뉴스수집 → 키워드추출 → 시간가중치 → 점수화 → 재정렬
→ 테마감지 → 키움 관련종목 자동조회 → 텔레그램 발송
"""
import os, sys, re, time, datetime
import requests
from urllib.request import urlopen, Request
from html import unescape
from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

try: sys.stdout.reconfigure(encoding='utf-8')
except: pass

# ════════════════════════════════════════════
# 테마 → 키움 업종코드 매핑
# ════════════════════════════════════════════
THEME_SECTOR = {
    '반도체':  {'code': 'Q20', 'keys': ['반도체', 'HBM', 'AI반도체', '파운드리', 'D램', '낸드']},
    'AI':      {'code': 'Q32', 'keys': ['AI', '인공지능', '데이터센터', 'GPU', '클라우드']},
    '2차전지': {'code': 'Q26', 'keys': ['배터리', '2차전지', '전기차', '양극재', '전고체']},
    '바이오':  {'code': 'Q15', 'keys': ['바이오', '신약', '임상', 'FDA', '치료제', '백신']},
    '방산':    {'code': 'Q28', 'keys': ['방산', '무기', 'K방산', '미사일', '수출계약']},
    '원전':    {'code': 'Q31', 'keys': ['원전', 'SMR', '핵연료', '원전수출']},
    '조선':    {'code': 'Q12', 'keys': ['조선', 'LNG선', '수주잔고', '선박']},
    '전력':    {'code': 'Q29', 'keys': ['전력망', '변압기', '송전', '전력인프라']},
    '로봇':    {'code': 'Q33', 'keys': ['로봇', '휴머노이드', '자율주행', '협동로봇']},
    '제약':    {'code': 'Q14', 'keys': ['제약', '의약품', '헬스케어', '의료기기']},
}

# 재료성 가산점
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
]

API_SEARCH = [
    '수주 계약', '임상 승인', '반도체 AI',
    '2차전지 배터리', '바이오 신약',
    '외국인 매수', '실적 서프라이즈', '대규모 투자',
]

def load_config():
    config = {}
    with open(r'D:\news_bot\config.env', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                config[k.strip()] = v.strip()
    return config

def clean_text(text):
    text = unescape(text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&[a-zA-Z#0-9]+;', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ════════════════════════════════════════════
# 시간 가중치 계산
# ════════════════════════════════════════════
def get_time_weight(pub_date_str):
    """발행시간 → 시간 가중치 (최신일수록 높음)"""
    if not pub_date_str:
        return 0.5  # 시간 모르면 중간값

    try:
        # RFC 2822 형식 파싱 (RSS 표준)
        pub_dt = parsedate_to_datetime(pub_date_str)
        now = datetime.datetime.now(pub_dt.tzinfo)
        diff_min = (now - pub_dt).total_seconds() / 60

        if diff_min <= 5:    return 1.0   # 5분 이내
        elif diff_min <= 30: return 0.8   # 30분 이내
        elif diff_min <= 60: return 0.6   # 1시간 이내
        elif diff_min <= 180:return 0.3   # 3시간 이내
        elif diff_min <= 360:return 0.1   # 6시간 이내
        else:                return 0.05  # 6시간 이상
    except:
        return 0.5

# ════════════════════════════════════════════
# 1. 뉴스 수집
# ════════════════════════════════════════════
def fetch_all_news(naver_id, naver_sec):
    news = []
    headers = {'User-Agent': 'Mozilla/5.0'}

    # RSS (발행시간 포함)
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
        except: pass

    # 네이버 API (발행시간 포함)
    if naver_id and naver_sec:
        api_headers = {
            'X-Naver-Client-Id': naver_id,
            'X-Naver-Client-Secret': naver_sec
        }
        for keyword in API_SEARCH[:6]:
            try:
                r = requests.get(
                    'https://openapi.naver.com/v1/search/news.json',
                    headers=api_headers,
                    params={'query': keyword, 'display': 8, 'sort': 'date'},
                    timeout=8
                )
                if r.status_code == 200:
                    for item in r.json().get('items', []):
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
            except: pass

    # 중복 제거
    seen = set(); unique = []
    for n in news:
        if n['title'] not in seen:
            seen.add(n['title']); unique.append(n)

    return unique

# ════════════════════════════════════════════
# 2. 키워드 추출
# ════════════════════════════════════════════
def extract_keywords(news_list):
    all_text = ' '.join([n['title'] for n in news_list])
    words = re.findall(r'[가-힣A-Z]{2,}', all_text)
    words = [w for w in words if w not in STOPWORDS]
    word_count = Counter(words)
    word_sources = defaultdict(set)
    for n in news_list:
        title_words = set(re.findall(r'[가-힣A-Z]{2,}', n['title']))
        for w in title_words:
            if w not in STOPWORDS:
                word_sources[w].add(n['source'])
    return word_count, word_sources

# ════════════════════════════════════════════
# 3. 테마 감지
# ════════════════════════════════════════════
def detect_theme(title):
    """뉴스 제목에서 테마 감지"""
    detected = []
    for theme, info in THEME_SECTOR.items():
        for key in info['keys']:
            if key in title:
                detected.append(theme)
                break
    return list(set(detected))

# ════════════════════════════════════════════
# 4. 키움 관련종목 조회
# ════════════════════════════════════════════
def get_theme_stocks_kiwoom(themes):
    """키움 Open API로 테마 관련 거래대금 상위 종목 조회"""
    if not themes:
        return []

    try:
        import win32com.client
        kiwoom = win32com.client.Dispatch('KHOPENAPI.KHOpenAPICtrl.1')

        stocks = []
        for theme in themes[:2]:  # 최대 2개 테마
            sector_code = THEME_SECTOR.get(theme, {}).get('code', '')
            if not sector_code:
                continue

            # 업종별 주요 종목 조회 (OPT20006)
            kiwoom.SetInputValue('업종코드', sector_code)
            kiwoom.CommRqData('업종별주가', 'OPT20006', 0, '0101')
            time.sleep(0.5)

            # 상위 5개 종목
            for i in range(5):
                name = kiwoom.GetCommData('업종별주가', '업종별주가', i, '종목명').strip()
                code = kiwoom.GetCommData('업종별주가', '업종별주가', i, '종목코드').strip()
                rate = kiwoom.GetCommData('업종별주가', '업종별주가', i, '등락률').strip()
                if name:
                    stocks.append(f"{name}({rate}%)")

        return stocks[:5]

    except Exception as e:
        # 키움 연결 안되면 테마별 대표종목 반환
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

# ════════════════════════════════════════════
# 5. 점수화 + 재정렬
# ════════════════════════════════════════════
def score_and_rank(news_list, word_count, word_sources):
    scored = []
    for n in news_list:
        title = n['title']
        score = 0
        bonus_matched = []

        words = re.findall(r'[가-힣A-Z]{2,}', title)
        words = [w for w in words if w not in STOPWORDS]

        for word in words:
            freq = word_count.get(word, 0)
            if freq >= 3:
                src_cnt = len(word_sources.get(word, set()))
                score += freq + (src_cnt * 2)

        # 재료성 가산점
        for bonus_kw, bonus_pt in BONUS_KEYWORDS.items():
            if bonus_kw in title:
                score += bonus_pt
                bonus_matched.append(f"{bonus_kw}(+{bonus_pt})")

        # ★ 시간 가중치 적용
        time_weight = n.get('time_weight', 0.5)
        final_score = int(score * time_weight)

        # 테마 감지
        themes = detect_theme(title)

        scored.append({
            'title':       title,
            'url':         n.get('url', ''),
            'source':      n['source'],
            'pub_date':    n.get('pub_date', ''),
            'time_weight': time_weight,
            'raw_score':   score,
            'score':       final_score,
            'bonus':       list(set(bonus_matched))[:3],
            'themes':      themes,
        })

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored

# ════════════════════════════════════════════
# 6. 텔레그램 발송
# ════════════════════════════════════════════
def send_telegram(bot_token, chat_id, message):
    try:
        requests.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'},
            timeout=10
        )
    except: pass

def build_report(top_news, keyword_ranking, now_str, cycle):
    msg  = f"<b>[라인 뉴스 브리핑] {now_str}</b>\n\n"

    # 핵심 키워드 TOP 5
    if keyword_ranking:
        kw_str = ' | '.join([f"{w}({c})" for w,c in keyword_ranking[:5]])
        msg += f"<b>핵심 키워드</b>\n{kw_str}\n\n"

    # 주목 뉴스 TOP 5
    msg += "<b>주목 뉴스 (점수순)</b>"
    for i, n in enumerate(top_news[:5], 1):
        # 시간 표시
        tw = n['time_weight']
        if tw >= 1.0:   time_tag = "🟢 5분내"
        elif tw >= 0.8: time_tag = "🔵 30분내"
        elif tw >= 0.6: time_tag = "🟡 1시간내"
        else:           time_tag = "⚪ 오래됨"

        bonus_str = ' '.join(n['bonus'][:2]) if n['bonus'] else ''
        theme_str = '/'.join(n['themes'][:2]) if n['themes'] else ''

        msg += f"\n\n{i}. {time_tag} <b>{n['title'][:38]}</b>"
        msg += f"\n   점수:{n['score']} | {theme_str} | {bonus_str}"

        # 관련종목
        if n['themes']:
            stocks = get_theme_stocks_kiwoom(n['themes'])
            if stocks:
                msg += f"\n   관련종목: {' '.join(stocks[:4])}"

        if n.get('url'):
            msg += f"\n   <a href='{n['url']}'>기사 보기</a>"

    msg += "\n\n<i>라인투자자산운용 | 투자판단은 본인책임</i>"
    return msg

# ════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════
def main():
    print("=" * 60)
    print("  라인 뉴스 스마트 시스템 v2")
    print("  시간가중치 + 키워드점수 + 관련종목 자동조회")
    print("  [5분마다 체크 | Ctrl+C 종료]")
    print("=" * 60)

    cfg = load_config()
    naver_id  = cfg.get('NAVER_CLIENT_ID', '')
    naver_sec = cfg.get('NAVER_CLIENT_SECRET', '')
    bot_token = cfg.get('TELEGRAM_BOT_TOKEN', '')
    chat_id   = cfg.get('TELEGRAM_CHAT_ID', '')

    sent_titles = set()
    cycle = 0
    print("\n  시작!\n")

    while True:
        cycle += 1
        now     = datetime.datetime.now()
        now_str = now.strftime('%Y-%m-%d %H:%M')
        print(f"  [{now.strftime('%H:%M:%S')}] 체크 #{cycle}")

        # 1. 수집
        news_list = fetch_all_news(naver_id, naver_sec)
        print(f"  수집: {len(news_list)}건")

        if not news_list:
            time.sleep(300); continue

        # 2. 키워드 추출
        word_count, word_sources = extract_keywords(news_list)
        keyword_ranking = [
            (w, c) for w, c in word_count.most_common(10)
            if c >= 3 and w not in STOPWORDS
        ]
        print(f"  키워드: {', '.join([f'{w}({c})' for w,c in keyword_ranking[:5]])}")

        # 3. 점수화 (시간가중치 포함)
        scored = score_and_rank(news_list, word_count, word_sources)
        new_scored = [n for n in scored if n['title'] not in sent_titles]

        # 상위 출력
        for n in new_scored[:3]:
            tw_str = f"시간가중:{n['time_weight']}"
            print(f"    [{n['score']}점/{tw_str}] {n['title'][:30]}... 테마:{'/'.join(n['themes'])}")

        # 4. 발송 판단
        should_send = False

        # 고점수 즉시 발송 (점수 40+)
        urgent = [n for n in new_scored if n['score'] >= 40]
        if urgent:
            print(f"  ★ 고점수 {len(urgent)}건 즉시 발송!")
            should_send = True

        # 30분마다 정기 발송
        if cycle % 6 == 1:
            should_send = True
            print(f"  정기 브리핑")

        if should_send and bot_token and chat_id:
            msg = build_report(new_scored, keyword_ranking, now_str, cycle)
            send_telegram(bot_token, chat_id, msg)
            for n in new_scored[:5]:
                sent_titles.add(n['title'])

        print(f"  다음: 5분 후\n")
        time.sleep(300)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n  종료!")
