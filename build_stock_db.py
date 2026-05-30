# -*- coding: utf-8 -*-
"""
build_stock_db.py — 관련주 DB 빌더
라인투자자산운용 / 뉴스봇·나비봇 공용 관련주 DB 생성

출력: stock_db.json
구성:
  1겹) KRX 전체 종목 (이름↔코드↔업종)   ← FinanceDataReader (항상 동작)
  2겹) 핵심 테마 큐레이션 CORE_THEMES     ← 대표님(베테랑)이 직접 보강 = 정확도의 핵심
       + 뉴스 키워드 별칭 ALIAS

설치(PC): pip install finance-datareader
실행(PC): python build_stock_db.py

※ 이 스크립트는 인터넷이 필요해서 PC에서 실행하세요(클라우드 아님).
※ CORE_THEMES는 '이름'만 적습니다. 코드는 KRX 명단에서 자동으로 찾아 붙입니다.
   (그래서 종목코드를 외울 필요 없음 — 이름만 정확히 적으면 됨)
"""
import json, sys

# ── 2겹) 핵심 테마 큐레이션 (이름만! 코드는 자동 매칭) ─────────────
#    ★ 대표님이 직접 추가/수정하는 부분 = 우리 DB의 진짜 정확도 ★
#    한 종목이 여러 테마에 들어가도 됩니다.
CORE_THEMES = {
    '반도체':   ['삼성전자','SK하이닉스','한미반도체','DB하이텍','리노공업','이오테크닉스','주성엔지니어링'],
    'HBM':      ['SK하이닉스','한미반도체','삼성전자','와이씨','피에스케이홀딩스','오픈엣지테크놀로지'],
    'AI':       ['삼성전자','SK하이닉스','NAVER','카카오','솔트룩스','코난테크놀로지'],
    '2차전지':  ['LG에너지솔루션','삼성SDI','에코프로비엠','포스코퓨처엠','엘앤에프','SK이노베이션'],
    '전고체':   ['삼성SDI','이수스페셜티케미컬','한농화성','씨아이에스'],
    '바이오':   ['삼성바이오로직스','셀트리온','유한양행','알테오젠','HLB','리가켐바이오'],
    '방산':     ['한화에어로스페이스','LIG디펜스앤에어로스페이스','현대로템','한국항공우주','한화시스템'],
    '원전':     ['두산에너빌리티','한전기술','비에이치아이','우진','보성파워텍','한전KPS'],
    'SMR':      ['두산에너빌리티','한전기술','비에이치아이','우진'],
    '조선':     ['HD한국조선해양','삼성중공업','HD현대중공업','한화오션'],
    '전력설비': ['LS ELECTRIC','효성중공업','제룡전기','HD현대일렉트릭','대한전선'],
    '로봇':     ['레인보우로보틱스','두산로보틱스','HD현대','에스피지'],
    '자율주행': ['현대차','기아','HL만도','넥스트칩'],
    '제약':     ['유한양행','종근당','한미약품','대웅제약','녹십자'],
    '게임':     ['크래프톤','NC','넷마블','펄어비스','위메이드'],
    '엔터':     ['하이브','에스엠','와이지엔터테인먼트'],
    '우주항공': ['한국항공우주','쎄트렉아이','인텔리안테크','한화에어로스페이스'],
    '풍력':     ['씨에스윈드','유니슨','동국S&C','SK오션플랜트'],
    '태양광':   ['한화솔루션','OCI홀딩스','HD현대에너지솔루션'],
}

# ── 뉴스 키워드 → 테마 별칭 (뉴스에 이 단어가 뜨면 이 테마로 매핑) ──
ALIAS = {
    'HBM':'HBM','고대역폭':'HBM','파운드리':'반도체','D램':'반도체','낸드':'반도체',
    '인공지능':'AI','데이터센터':'AI','GPU':'AI','엔비디아':'AI',
    '배터리':'2차전지','양극재':'2차전지','음극재':'2차전지','전기차':'2차전지',
    '소형모듈원자로':'SMR','원전수출':'원전','체코원전':'원전','핵연료':'원전',
    '신약':'바이오','임상':'바이오','FDA':'바이오','치료제':'바이오','항암':'바이오',
    'K방산':'방산','미사일':'방산','수출계약':'방산','자주포':'방산',
    'LNG선':'조선','수주잔고':'조선','선박':'조선',
    '변압기':'전력설비','송전':'전력설비','전력망':'전력설비',
    '휴머노이드':'로봇','협동로봇':'로봇',
    '풍력발전':'풍력','해상풍력':'풍력',
    # 옛 이름·약칭 → 테마 (뉴스 기사는 흔히 옛 이름을 씀)
    '엔씨소프트':'게임','LIG넥스원':'방산','LIG디펜스':'방산','현대미포':'조선','HD현대미포':'조선',
}

def get_listing():
    """KRX 전체 상장종목: 이름→코드, 코드→{이름,시장,업종}"""
    import FinanceDataReader as fdr
    df = fdr.StockListing('KRX')
    cols = {c.lower(): c for c in df.columns}
    code_col = cols.get('code') or cols.get('symbol')
    name_col = cols.get('name')
    mkt_col  = cols.get('market')
    sec_col  = cols.get('sector') or cols.get('industry')
    name2code, stocks = {}, {}
    for _, r in df.iterrows():
        code = str(r[code_col]).strip().zfill(6) if code_col else ''
        name = str(r[name_col]).strip() if name_col else ''
        if not code.isdigit() or len(code) != 6 or not name or name.lower() == 'nan':
            continue
        name2code[name] = code
        stocks[code] = {
            'name':   name,
            'market': str(r[mkt_col]).strip() if mkt_col else '',
            'sector': str(r[sec_col]).strip() if sec_col else '',
            'themes': [],
        }
    return stocks, name2code

def main():
    print("=" * 50)
    print("  관련주 DB 빌더 시작")
    print("=" * 50)

    print("\n[1] KRX 전체 종목 불러오는 중...")
    stocks, name2code = get_listing()
    print(f"    → 상장종목 {len(stocks)}개 로드 완료")

    print("\n[2] 핵심 테마 큐레이션 매칭 중...")
    themes = {}
    missing = []
    for theme, names in CORE_THEMES.items():
        codes = []
        for nm in names:
            code = name2code.get(nm)
            if code:
                codes.append(code)
                if theme not in stocks[code]['themes']:
                    stocks[code]['themes'].append(theme)   # 코드→테마 역색인
            else:
                missing.append((theme, nm))
        themes[theme] = codes
    print(f"    → 테마 {len(themes)}개 구축")
    if missing:
        print(f"    ⚠ 이름을 KRX 명단에서 못 찾음 {len(missing)}건 (오타/상장폐지/이름변경 확인):")
        for theme, nm in missing:
            print(f"       - [{theme}] {nm}")

    db = {
        'stocks':  stocks,        # 코드 → {name, market, sector, themes[]}
        'themes':  themes,        # 테마 → [코드]
        'alias':   ALIAS,         # 키워드 → 테마
        'name2code': name2code,   # 이름 → 코드 (직접 매칭용)
    }
    with open('stock_db.json', 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False)
    print(f"\n[완료] stock_db.json 저장 (종목 {len(stocks)} / 테마 {len(themes)})")
    print("       이제 뉴스봇·나비봇이 stock_db.py로 이 파일을 읽어 씁니다.")

if __name__ == '__main__':
    main()
