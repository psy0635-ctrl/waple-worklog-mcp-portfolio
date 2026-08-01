"""정보 노출 검사(scan_text)의 회귀 테스트.

이 테스트가 존재하는 이유:
    이전 검사기는 도메인을 전혀 보지 않아 `CN=*.example.co.kr` 같은 값이
    그대로 통과했다. 그런데도 아무도 알아채지 못한 이유는, 검사 결과가
    항상 "0건"이었고 그것이 "깨끗함"인지 "검사기가 눈멂"인지 구분할
    수단이 없었기 때문이다.

    그래서 이 테스트는 두 방향을 모두 고정한다.
      - 걸려야 하는 값이 실제로 걸리는가 (미검출 방지)
      - 통과해야 하는 값이 통과하는가 (오탐 방지)

    오탐 쪽도 같은 무게로 다루는 이유는, 오탐이 쏟아지면 사람이 검사
    결과를 읽지 않게 되고 그 시점에 검사기는 없는 것과 같아지기 때문이다.

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
    ("도메인:포트",       "접속 host.sample.kr:9999", "host.sample.kr:9999"),
    ("IP:포트",           "접속 203.0.113.45:9999", "203.0.113.45:9999"),
]


@pytest.mark.parametrize("desc,text,expected", MUST_DETECT,
                         ids=[d for d, _, _ in MUST_DETECT])
def test_걸려야_하는_값이_검출된다(desc, text, expected):
    assert expected in scan_text(text), f"{desc}: 검출되지 않았습니다"


# ── 통과해야 하는 값 ────────────────────────────────────────
MUST_PASS = [
    ("허용 호스트",       "bind 127.0.0.1 / 0.0.0.0 / ::1"),
    ("허용 포트",         "https://example.com:8010 과 :443 사용"),
    ("허용 도메인",       "https://github.com/user/repo 참고"),
    ("문서용 도메인",     "https://example.com 예시"),
    ("근거로 유지할 IP",  "INFO: 160.79.106.36:0 - POST /mcp 200 OK"),
    ("시각 표기",         "2026-07-28 17:02:12 요청 도달"),
    ("시분 표기",         "16:55 부터 17:02 까지"),
    ("로그의 파일:줄번호", "INFO Terminating session streamable_http.py:788"),
    ("파이썬 슬라이스",   "preview = line[:70] + '...'"),
    ("포맷 지정자",       'print(f"{name:20} -> {value}")'),
    ("한글 본문",         "업무일지 초안을 생성하고 사용자 승인을 받는다"),
    ("플레이스홀더",      "ssh -p [SSH_PORT] user@[SERVER_IP] 로 접속"),
    ("마스킹된 로그",     "INFO: [MASKED_IP]:53024 - GET /mcp 406"),
    ("JSON 이스케이프",   r'{"text":"확인할 수 없습니다.\nclaude.ai 웹 커넥터는"}'),
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
    text = "ssh -p 12345 user@sample.net 로 203.0.113.45 접속 후 확인"
    result = scan_text(text)
    assert len(result) >= 4, f"검출이 부족합니다: {result}"


def test_검사_대상_상수가_비어_있지_않다():
    """허용 목록을 비우면 검사가 무의미해지므로 최소 구성을 고정한다."""
    from build_pdf import ALLOWED_DOMAINS, ALLOWED_HOSTS, ALLOWED_PORTS
    assert "127.0.0.1" in ALLOWED_HOSTS
    assert "443" in ALLOWED_PORTS
    assert "github.com" in ALLOWED_DOMAINS


def test_예시값_파일은_일괄검사에서_제외된다():
    """검사기 자신과 그 테스트는 걸려야 하는 형태를 본문에 담고 있다.

    제외 목록이 사라지면 --all 이 자기 자신을 계속 신고하게 되고,
    그러면 사람이 결과를 읽지 않게 된다.
    """
    from check_secrets import SELF_EXEMPT
    assert "test_build_pdf.py" in SELF_EXEMPT
    assert "scripts/check_secrets.py" in SELF_EXEMPT