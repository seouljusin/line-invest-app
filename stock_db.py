# -*- coding: utf-8 -*-
"""
stock_db.py — 관련주 DB 매칭 모듈 (뉴스봇·나비봇 공용)

쓰는 법:
    import stock_db
    db = stock_db.load_db()                         # stock_db.json 로드
    stock_db.match_keyword(db, '원전')              # 키워드 → 관련 종목 [{code,name}]
    stock_db.themes_of_code(db, '034020')           # 종목코드 → 속한 테마들  ← 나비봇 교차검증용
    stock_db.is_in_themes(db, '034020', ['원전'])   # 이 종목이 해당 테마에 속하나? (True/False)

DB는 build_stock_db.py로 PC에서 먼저 만들어 두세요.
"""
import json, os

def load_db(path='stock_db.json'):
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def _stocks(db, codes):
    out = []
    seen = set()
    for c in codes:
        if c in db['stocks'] and c not in seen:
            seen.add(c)
            out.append({'code': c, 'name': db['stocks'][c]['name']})
    return out

def match_keyword(db, keyword, limit=8):
    """뉴스 키워드 → 관련 종목 리스트.
       우선순위: ① 종목명 직접 일치 ② 별칭→테마 ③ 테마명 일치 ④ 종목명 부분 포함"""
    if not db or not keyword:
        return []
    kw = keyword.strip()
    codes = []

    # ① 종목명 정확 일치 (뉴스에 회사 이름이 직접 뜬 경우)
    if kw in db['name2code']:
        codes.append(db['name2code'][kw])

    # ② 별칭 → 테마
    theme = db['alias'].get(kw)
    if theme and theme in db['themes']:
        codes += db['themes'][theme]

    # ③ 테마명 자체와 일치
    if kw in db['themes']:
        codes += db['themes'][kw]

    # ④ 종목명 부분 포함 (예: '삼성' → 삼성전자 등) — 너무 흔하면 생략
    if not codes and len(kw) >= 2:
        for name, code in db['name2code'].items():
            if kw in name:
                codes.append(code)

    return _stocks(db, codes)[:limit]

def themes_of_code(db, code):
    """종목코드 → 속한 테마 목록 (나비봇이 '이 종목이 오늘 급증 테마인가' 확인용)"""
    if not db:
        return []
    code = str(code).zfill(6)
    info = db['stocks'].get(code)
    return list(info['themes']) if info else []

def is_in_themes(db, code, themes):
    """이 종목(code)이 주어진 테마들 중 하나에 속하는지"""
    mine = set(themes_of_code(db, code))
    return bool(mine & set(themes))

def theme_members(db, theme, limit=20):
    """테마 → 소속 종목 [{code,name}]"""
    if not db or theme not in db.get('themes', {}):
        return []
    return _stocks(db, db['themes'][theme])[:limit]

# 간단 자가 테스트 (DB가 있을 때만)
if __name__ == '__main__':
    db = load_db()
    if not db:
        print("stock_db.json 없음 — 먼저 build_stock_db.py를 PC에서 실행하세요.")
    else:
        print("DB 로드 OK — 종목", len(db['stocks']), "/ 테마", len(db['themes']))
        for kw in ['원전', '한미반도체', 'HBM', '방산']:
            hits = match_keyword(db, kw)
            print(f"  '{kw}' →", ', '.join(h['name'] for h in hits))
