"""
reaction_ranking.py — 재료별 종목 반응 순위 (반응학습 부품 2)
======================================================================
라인투자자산운용 | 김팀장 | 2026-06-01

목적: track_returns 가 ret 채운 추적로그를 집계 →
  "어떤 재료(테마) 뉴스가 뜨면, 어느 종목이 먼저·세게 반응했나" 순위.
  → reaction_scores.json 출력 → 매매엔진(나비/돌파/마왕)이 읽어서 참고.

★설계 원칙 (대표님 확정):
  - 1개만 X → 전부 나열 + 순위 + 점수 + 근거
  - 랭킹은 '추천'일 뿐, 최종 선택은 매매엔진이
  - 표본 부족(n<MIN_SAMPLE)은 '참고용'으로 표시 (정직)
  - 예측·매수권유 X → "과거에 이렇게 반응했다"는 사실만

점수 (1단계 — 단순·견고):
  종목별, 재료별로:
    n        = 표본 수 (그 재료에서 이 종목이 등장한 횟수)
    avg_ret  = 평균 수익률 (기본 ret_3d, 없으면 1d)
    hit_rate = ret>0 비율 (적중률)
    best_lag = 1d/3d/5d 중 평균이 가장 큰 시점 (반응 속도)
    score    = avg_ret * (0.5 + 0.5*hit_rate)   # 적중률로 가중
             ※ 표본 적으면 신뢰 낮으므로 reliable=False 플래그

출력: reaction_scores.json
  { "SMR": [ {code,name,score,avg_ret,hit_rate,n,best_lag,reliable}, ... 내림차순 ],
    "방산": [...], ... }

실행: python reaction_ranking.py
  (track_returns 후에 호출하면 최신 ret 반영)
"""
import os, json, datetime
from collections import defaultdict

LOG_PATH = os.environ.get('NEWS_TRACK_LOG', 'news_track_log.jsonl')
OUT_PATH = os.environ.get('REACTION_SCORES', 'reaction_scores.json')
MIN_SAMPLE = 3   # 이보다 표본 적으면 '참고용'(reliable=False)

_GH = None
try:
    import github_backup as _GH
except Exception:
    _GH = None


def _load_rows(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def _pick_ret(rec):
    """대표 수익률: 3d 우선, 없으면 1d, 없으면 5d. (값, lag) 반환 or (None,None)."""
    if rec.get('ret_3d') is not None:
        return rec['ret_3d'], '3d'
    if rec.get('ret_1d') is not None:
        return rec['ret_1d'], '1d'
    if rec.get('ret_5d') is not None:
        return rec['ret_5d'], '5d'
    return None, None


def build_ranking(path=None, out=None):
    """추적로그 → 재료별 종목 반응 순위 → reaction_scores.json"""
    path = path or LOG_PATH
    out = out or OUT_PATH

    if _GH is not None:
        try:
            _GH.restore_from_github(path)
        except Exception:
            pass

    rows = _load_rows(path)
    if not rows:
        print("  [reaction_ranking] 로그 없음 — 빈 결과", flush=True)
        json.dump({}, open(out, 'w', encoding='utf-8'))
        return {}

    # (theme, code) -> 반응 리스트 수집
    # rec 의 themes(여러 재료) × related_stocks(여러 종목) 조합
    agg = defaultdict(lambda: defaultdict(lambda: {
        'name': '', 'rets': [], 'lags': [], 'r1': [], 'r3': [], 'r5': []}))

    for rec in rows:
        themes = rec.get('themes') or []
        stocks = rec.get('related_stocks') or []
        if not themes or not stocks:
            continue
        ret, lag = _pick_ret(rec)
        if ret is None:
            continue   # 아직 ret 안 채워짐
        for th in themes:
            for s in stocks:
                code = s.get('code', '')
                if not code:
                    continue
                cell = agg[th][code]
                cell['name'] = s.get('name', '')
                cell['rets'].append(ret)
                cell['lags'].append(lag)
                if rec.get('ret_1d') is not None: cell['r1'].append(rec['ret_1d'])
                if rec.get('ret_3d') is not None: cell['r3'].append(rec['ret_3d'])
                if rec.get('ret_5d') is not None: cell['r5'].append(rec['ret_5d'])

    def _avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    result = {}
    for th, codes in agg.items():
        items = []
        for code, cell in codes.items():
            rets = cell['rets']
            n = len(rets)
            if n == 0:
                continue
            avg_ret = _avg(rets)
            hit_rate = round(sum(1 for r in rets if r > 0) / n, 2)
            # 반응 속도: 1d/3d/5d 평균 중 최대인 시점
            lag_avgs = {'1d': _avg(cell['r1']), '3d': _avg(cell['r3']), '5d': _avg(cell['r5'])}
            lag_valid = {k: v for k, v in lag_avgs.items() if v is not None}
            best_lag = max(lag_valid, key=lag_valid.get) if lag_valid else None
            # 점수: 평균수익 × (0.5 + 0.5×적중률)
            score = round(avg_ret * (0.5 + 0.5 * hit_rate), 2)
            items.append({
                'code': code,
                'name': cell['name'],
                'score': score,
                'avg_ret': avg_ret,
                'hit_rate': hit_rate,
                'n': n,
                'best_lag': best_lag,
                'reliable': n >= MIN_SAMPLE,   # 표본 충분?
            })
        # 점수 내림차순 정렬 (전부 나열, 1순위부터)
        items.sort(key=lambda x: x['score'], reverse=True)
        result[th] = items

    # 저장
    json.dump(result, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    n_themes = len(result)
    n_pairs = sum(len(v) for v in result.values())
    print(f"  [reaction_ranking] {n_themes}개 재료 / {n_pairs}개 (재료,종목) 순위 → {out}", flush=True)

    if _GH is not None:
        try:
            # reaction_scores.json 을 깃허브 별도 경로로 백업 (추적로그 안 건드림)
            # → 로컬 매매엔진(나비/돌파)이 깃허브에서 받아 읽을 수 있게 함
            _GH.backup_to_github(out, msg='반응순위 갱신',
                                 repo_path='reaction_scores.json')
        except Exception as e:
            print(f"  [reaction_ranking] 백업 스킵: {str(e)[:50]}", flush=True)

    return result


def pretty_print(result, theme=None, top=5):
    """사람이 보기 좋게 출력 (테스트/디버깅용)."""
    themes = [theme] if theme else list(result.keys())
    for th in themes:
        items = result.get(th, [])
        if not items:
            continue
        print(f"\n★ 재료: {th}  (과거 반응 순위, 상위 {top})")
        for i, it in enumerate(items[:top], 1):
            tag = '' if it['reliable'] else '  ※표본부족(참고용)'
            print(f"  {i}. {it['name']}({it['code']})  "
                  f"점수{it['score']} | 평균{it['avg_ret']:+}% | "
                  f"적중{int(it['hit_rate']*100)}% | n={it['n']} | {it['best_lag']}{tag}")
    print("\n※ 과거 반응 통계이며, 미래 수익을 보장하지 않습니다.")


if __name__ == '__main__':
    res = build_ranking()
    pretty_print(res)
