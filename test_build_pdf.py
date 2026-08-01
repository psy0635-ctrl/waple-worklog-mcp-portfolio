"""정보 노출 검사(scan_text)의 회귀 테스트.

이 테스트가 존재하는 이유:
    이전 검사기는 도메인을 전혀 보지 않아 `CN=*.example.co.kr` 같은 값이
    그대로 통과했다. 그런데도 아무도 알아채지 못한 이유는, 검사 결과가
    항상 "0건"이었고 그것이 "깨끗함"인지 "검사기가 눈멂"인지 구분할
    수단이 없었기 때문이다.

    그래서 이 테스트는 두 방향을 모두 고정한다.
      - 걸려야 하는 값이 실제로 걸리는가 (미검출 방지)
      - 통과해야 하는 값이 통과하는가 (오탐 방지)

주의: 여기에 실제 배포 서버의 값을 적지 않는다.
      노출을 막는 코드가 값을 공개하는 모순이 생기기 때문이며,
      검사가 '형태' 기준이므로 가짜 값으로도 동일하게 검증된다.
"""
import sys
from pathlib import Path

import pytest

# scripts/ 는 pytest 의 기본 경로에 포함되지 않으므로 직접 추가한다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from build_pdf import scan_text  # noqa: E402


# ── 걸려야 하는 값 ──────────────────────────────────────────
# (설명, 검사 대상 문자열, 결과에 반드시 포함돼야 하는 값)
MUST_DETECT = [
    ("와일드카드 도메인", "0 s:CN=*.example.co.kr", "example.co.kr"),
    ("서브도메인 포함",   "https://api.example.co.kr/mcp", "api.example.co.kr"),
    ("맨 도메인",         "서비스(sample.kr) 로그인", "sample.kr"),
    ("점 세 개 도메인",   "a.b.example.or.kr 접속", "a.b.example.or.kr"),
    ("공인 IP",           "발신 203.0.113.45", "203.0.113.45"),
    ("사설 IP",           "내부 192.168.0.10", "192.168.0.10"),
    ("메일 주소",         "문의 someone@sample.net", "someone@sample.net"),
    ("ssh 접속 명령",     "ssh -p 12345 user@host", "ssh -p 12345"),
    ("비허용 포트",       "서버가 :9999 에서 대기", ":9999"),
]


@pytest.mark.parametrize("desc,text,expected", MUST_DETECT,
                         ids=[d for d, _, _ in MUST_DETECT])
def test_걸려야_하는_값이_검출된다(desc, text, expected):
    assert expected in scan_text(text), f"{desc}: 검출되지 않았습니다"


# ── 통과해야 하는 값 ────────────────────────────────────────
MUST_PASS = [
    ("허용 호스트",     "bind 127.0.0.1 / 0.0.0.0 / ::1"),
    ("허용 포트",       "포트 :8010 과 :443 사용"),
    ("허용 도메인",     "https://github.com/user/repo 참고"),
    ("문서용 도메인",   "https://example.com 예시"),
    ("시각 표기",       "2026-07-28 17:02:12 요청 도달"),
    ("시분 표기",       "16:55 부터 17:02 까지"),
    ("한글 본문",       "업무일지 초안을 생성하고 사용자 승인을 받는다"),
    ("플레이스홀더",    "ssh -p [SSH_PORT] user@[SERVER_IP] 로 접속"),
    ("마스킹된 로그",   "INFO: [MASKED_IP]:0 - GET /mcp 200 OK"),
    ("코드 블록",       "def build() -> None:  # 주석"),
]


@pytest.mark.parametrize("desc,text", MUST_PASS,
                         ids=[d for d, _ in MUST_PASS])
def test_통과해야_하는_값은_검출되지_않는다(desc, text):
    assert scan_text(text) == [], f"{desc}: 오탐이 발생했습니다"


# ── 검사기 자체의 성질 ──────────────────────────────────────

def test_빈_문자열은_검출이_없다():
    assert scan_text("") == []


def test_결과는_중복_없이_정렬된다():
    text = "b.sample.kr 와 a.sample.kr 와 b.sample.kr"
    result = scan_text(text)
    assert result == sorted(set(result))


def test_여러_종류가_한번에_검출된다():
    """실제 사고는 한 줄에 여러 종류가 섞여 있는 경우가 많다."""
    text = "ssh -p 12345 user@sample.net 로 203.0.113.45 접속 후 :9999 확인"
    result = scan_text(text)
    assert len(result) >= 4, f"검출이 부족합니다: {result}"


def test_검사_대상_상수가_비어_있지_않다():
    """허용 목록을 비우면 검사가 무의미해지므로 최소 구성을 고정한다."""
    from build_pdf import ALLOWED_DOMAINS, ALLOWED_HOSTS, ALLOWED_PORTS
    assert "127.0.0.1" in ALLOWED_HOSTS
    assert "443" in ALLOWED_PORTS
    assert "github.com" in ALLOWED_DOMAINS