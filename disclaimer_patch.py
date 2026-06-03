"""
The Line Capital - 면책 문구 자동 부착 패치
═══════════════════════════════════════════════════
적용 대상: 뉴스봇 + 마녀봇 + 마왕봇 (텔레그램 발송 봇 전체)
작성: 김팀장 (Claude) | 2026-06-04
목적: 자본시장법 제101조 회색지대 위험 감소

★ 6대 원칙 ①번 「코드 동결」 유지:
   시그널 로직(buy/sell 결정) 무관, 출력 메시지에만 한 줄 추가.
   알고리즘 동결과 다름. 안전.
"""

# ═══════════════════════════════════════════════════
# 면책 문구 (모든 봇 공통)
# ═══════════════════════════════════════════════════
DISCLAIMER_TEXT = (
    "\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "※ 본 자료는 정보 제공 목적이며,\n"
    "   특정 종목의 매수·매도 추천이 아닙니다.\n"
    "   투자 판단·결과는 투자자 본인 책임입니다.\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
)


def append_disclaimer(message: str) -> str:
    """
    텔레그램 메시지 끝에 면책 문구를 자동 부착합니다.
    
    Args:
        message: 원본 메시지 텍스트
    
    Returns:
        면책 문구가 부착된 메시지
    
    Example:
        original_msg = "오늘 시황: 코스피 +1.2%"
        safe_msg = append_disclaimer(original_msg)
        send_telegram(chat_id, safe_msg)
    """
    if message is None:
        return DISCLAIMER_TEXT
    # 이미 면책 문구가 있으면 중복 부착 방지
    if "본 자료는 정보 제공 목적이며" in message:
        return message
    return str(message) + DISCLAIMER_TEXT


def get_disclaimer() -> str:
    """면책 문구 단독 반환 (필요시 사용)"""
    return DISCLAIMER_TEXT


# ═══════════════════════════════════════════════════
# 테스트
# ═══════════════════════════════════════════════════
if __name__ == "__main__":
    sample = "오늘 거래대금 집중 뉴스\n1. 삼성전자 신제품 발표"
    print("【테스트】 면책 문구 부착 결과:")
    print("=" * 50)
    print(append_disclaimer(sample))
    print("=" * 50)
    print("\n[중복 부착 방지 테스트]")
    safe = append_disclaimer(sample)
    double_safe = append_disclaimer(safe)
    assert safe == double_safe, "중복 부착 발생!"
    print("✓ 중복 부착 방지 OK")
