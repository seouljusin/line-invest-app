"""
github_backup.py — 추적로그 깃허브 영구저장
======================================================================
라인투자자산운용 | 김팀장 | 2026-06-01 (repo_path 인자 추가)

목적: Render 휘발성 문제 해결.
  news_track_log.jsonl 을 GitHub 레포에 자동 커밋 → 재시작해도 안 날아감.

방식: GitHub Contents API (git 설치 불필요, requests 만 사용)
  - 발송 시마다 또는 주기적으로 jsonl 전체를 레포에 PUT
  - 재시작 시 restore_from_github() 로 복원

★ 2026-06-01 추가: repo_path 인자
  - 추적로그(news_track_log.jsonl)는 GITHUB_PATH 고정 그대로 (하위호환).
  - reaction_scores.json 같은 다른 파일은 repo_path='reaction_scores.json' 로
    깃허브 별도 경로에 백업/복원 → 추적로그를 절대 덮어쓰지 않음.

환경변수 (Render):
  GITHUB_TOKEN  : Personal Access Token (repo 권한)
  GITHUB_REPO   : 'seouljusin/line-invest-app'  (기본값)
  GITHUB_PATH   : 'news_track_log.jsonl'         (레포 내 경로, 추적로그 기본)
  GITHUB_BRANCH : 'main'                          (기본값)

사용:
  import github_backup
  # 추적로그 (기존과 동일)
  github_backup.restore_from_github(LOG_PATH)
  github_backup.backup_to_github(LOG_PATH)
  # 다른 파일 (별도 경로)
  github_backup.backup_to_github('reaction_scores.json', repo_path='reaction_scores.json')
  github_backup.restore_from_github('reaction_scores.json', repo_path='reaction_scores.json')
"""
import os, json, base64
import requests

REPO   = os.environ.get('GITHUB_REPO', 'seouljusin/line-invest-app')
PATH   = os.environ.get('GITHUB_PATH', 'news_track_log.jsonl')
BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
TOKEN  = os.environ.get('GITHUB_TOKEN', '')

# 기존 호환용 (추적로그 기본 경로). 새 코드는 _api_for(repo_path) 사용.
API = f"https://api.github.com/repos/{REPO}/contents/{PATH}"


def _api_for(repo_path):
    """레포 내 임의 경로에 대한 Contents API URL."""
    return f"https://api.github.com/repos/{REPO}/contents/{repo_path}"


def _headers():
    return {
        'Authorization': f'token {TOKEN}',
        'Accept': 'application/vnd.github+json',
    }


def _get_sha(repo_path=None):
    """레포에 이미 파일이 있으면 그 sha 반환 (업데이트에 필요), 없으면 None."""
    repo_path = repo_path or PATH
    try:
        r = requests.get(_api_for(repo_path), headers=_headers(),
                         params={'ref': BRANCH}, timeout=15)
        if r.status_code == 200:
            return r.json().get('sha')
    except Exception:
        pass
    return None


def backup_to_github(local_path, msg=None, repo_path=None):
    """로컬 파일 전체를 깃허브 repo_path 에 커밋(PUT). 성공 시 True.
       repo_path 미지정 시 추적로그 기본 경로(PATH)."""
    repo_path = repo_path or PATH
    if not TOKEN:
        print("  [github_backup] GITHUB_TOKEN 없음 — 백업 건너뜀", flush=True)
        return False
    if not os.path.exists(local_path):
        return False
    try:
        with open(local_path, 'rb') as f:
            content = f.read()
        b64 = base64.b64encode(content).decode('ascii')
        import datetime
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        payload = {
            'message': msg or f'백업 {ts}',
            'content': b64,
            'branch': BRANCH,
        }
        sha = _get_sha(repo_path)
        if sha:
            payload['sha'] = sha   # 기존 파일 업데이트
        r = requests.put(_api_for(repo_path), headers=_headers(),
                         json=payload, timeout=20)
        if r.status_code in (200, 201):
            print(f"  [github_backup] 깃허브 커밋 성공 [{repo_path}] ({len(content)} bytes)", flush=True)
            return True
        else:
            print(f"  [github_backup] 실패 {r.status_code}: {r.text[:100]}", flush=True)
            return False
    except Exception as e:
        print(f"  [github_backup] 오류: {str(e)[:80]}", flush=True)
        return False


def restore_from_github(local_path, repo_path=None):
    """깃허브 repo_path 에서 다운로드 → 로컬에 복원. 시작 시 1번 호출.
       repo_path 미지정 시 추적로그 기본 경로(PATH).
       로컬에 이미 더 큰 파일이 있으면 덮어쓰지 않음(안전)."""
    repo_path = repo_path or PATH
    if not TOKEN:
        print("  [github_backup] GITHUB_TOKEN 없음 — 복원 건너뜀", flush=True)
        return False
    try:
        r = requests.get(_api_for(repo_path), headers=_headers(),
                         params={'ref': BRANCH}, timeout=15)
        if r.status_code != 200:
            print(f"  [github_backup] 복원할 파일 없음 [{repo_path}] (status {r.status_code})", flush=True)
            return False
        data = r.json()
        content = base64.b64decode(data['content'])
        # 안전장치: 로컬이 더 크면(=최신) 덮어쓰지 않음
        if os.path.exists(local_path):
            local_size = os.path.getsize(local_path)
            if local_size >= len(content):
                print(f"  [github_backup] 로컬({local_size})이 최신 — 복원 생략 [{repo_path}]", flush=True)
                return False
        with open(local_path, 'wb') as f:
            f.write(content)
        n_lines = content.decode('utf-8', 'ignore').count('\n')
        print(f"  [github_backup] 깃허브에서 복원 [{repo_path}] ({len(content)} bytes, {n_lines}줄)", flush=True)
        return True
    except Exception as e:
        print(f"  [github_backup] 복원 오류: {str(e)[:80]}", flush=True)
        return False


if __name__ == '__main__':
    # 테스트: 환경변수 GITHUB_TOKEN 설정 후 실행
    print(f"REPO={REPO} PATH={PATH} BRANCH={BRANCH} TOKEN={'있음' if TOKEN else '없음'}")
    test_file = 'news_track_log.jsonl'
    if os.path.exists(test_file):
        backup_to_github(test_file, '테스트 백업')
    else:
        print(f"테스트할 {test_file} 없음")
