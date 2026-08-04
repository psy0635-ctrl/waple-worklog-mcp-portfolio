"""공개 레포에 남으면 안 되는 정보를 '형태' 기준으로 검사한다.

이 파일이 따로 존재하는 이유:
    같은 판정이 세 곳에서 따로 돌고 있었다.
      - build_pdf.py 의 PDF 검증
      - 푸시 전 git diff | grep 점검 (손으로 쓴 다른 패턴)
      - evidence 파일 마스킹 sed (또 다른 패턴)
    한 곳만 갱신되면 나머지가 조용히 어긋났고, 실제로 도메인 검사는
    어느 쪽에도 제대로 들어가 있지 않았다.
    판정 기준을 한 곳에 두어 흩어짐 자체를 없앤다.

설계 원칙:
    금지할 값(서버 IP·도메인 등)을 코드에 그대로 적지 않는다.
    적는 순간 "노출을 막는 코드"가 그 값을 공개하게 된다.
    대신 형태(패턴)로 검사하고, 공개해도 되는 값만 허용 목록에 둔다.

    단, 형태로 검사하더라도 형태를 좁게 잡으면 뚫리고,
    넓게 잡으면 오탐이 쏟아져 사람이 검사기를 꺼버린다.
    둘 다 검사기가 무력해지는 경로이므로 어느 쪽도 방치하지 않는다.
    근거: docs/evidence/scan_pattern_evidence.txt

모듈로 쓰기:
    import sys; sys.path.insert(0, "scripts")
    from check_secrets import scan_text
    leaked = scan_text(어떤_문자열)      # 빈 목록이면 이상 없음

명령으로 쓰기 (레포 루트에서):
    python scripts/check_secrets.py README.md docs/integration-manual.md
    python scripts/check_secrets.py --all
    git diff --cached --name-only | xargs python scripts/check_secrets.py

    이상이 없으면 종료 코드 0, 발견되면 1 을 돌려준다.
    따라서 다음처럼 이어 붙이면 검사를 통과할 때만 커밋된다.
        python scripts/check_secrets.py --all && git commit -m "..."
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 허용 목록 ────────────────────────────────────────────────
# 여기에는 "공개해도 되는 값"만 적는다.
# 항목을 추가할 때마다 그 값이 정말 공개 가능한지 확인할 것.
# 오탐이 번거롭다고 넓히면 검사기가 다시 눈이 먼다.

# 문서에 정상적으로 등장하는 주소 (transport_security 설명 등)
ALLOWED_HOSTS = {"127.0.0.1", "0.0.0.0", "255.255.255.255", "::1"}

# 접두어 단위로 허용하는 대역.
# 160.79.106.x 는 Anthropic 발신 IP 로, 웹 커넥터 요청이 서버에 도달했음을
# 보여주는 근거 그 자체다. 가리면 evidence 가 성립하지 않으므로 유지한다.
# (판단 근거: 2026-07-31 캡처 마스킹 기준 _masking_record.txt)
ALLOWED_IP_PREFIXES = ("160.79.106.",)

# 호스트:포트 형태에서 허용되는 포트
ALLOWED_PORTS = {"80", "443", "3000", "5000", "8000", "8010", "8080"}

# 공개 문서에 나와도 되는 외부 도메인
ALLOWED_DOMAINS = {
    "example.com", "example.net", "example.org", "localhost",
    "github.com", "raw.githubusercontent.com",
    "claude.ai", "anthropic.com", "modelcontextprotocol.io",
    "python.org", "pypi.org", "docs.pytest.org",
}

# 문서에 정상적으로 등장하는 메일 주소 (필요해지면 추가)
ALLOWED_EMAILS: set[str] = set()

# ── 검사 패턴 ────────────────────────────────────────────────

# 1.2.3.4 형태
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# 도메인 — TLD 목록으로 범위를 좁힌다.
#   - 서브도메인 자리에 * 와 _ 를 허용해 와일드카드 표기(*.example.co.kr)도 잡는다
#   - (?:...\.)* 이므로 서브도메인이 없는 맨 도메인(example.kr)도 잡힌다
#     이전 검사가 "서브도메인이 반드시 있다"고 가정해 뚫렸던 지점
_TLD = r"(?:co\.kr|or\.kr|go\.kr|kr|com|net|org|io|ai|dev|app|cloud)"
DOMAIN_RE = re.compile(rf"\b(?:[A-Za-z0-9*_-]+\.)*[A-Za-z0-9*_-]+\.{_TLD}\b")

# 메일 주소 — 정규식이 이미 @ 를 요구하므로 뒤에서 다시 거르지 않는다.
# (이전 코드는 `if "@" in m` 을 덧붙였는데 항상 참이라 아무 일도 하지 않았다.)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")

# 호스트:포트 후보. 콜론 앞부분이 실제 IP·도메인일 때만 노출로 본다.
#
# 이전에는 `:(\d{2,5})` 로 포트만 봤다. 그러면 로그의 파일:줄번호
# (streamable_http.py:788), 파이썬 슬라이스([:70]), 포맷 지정자({k:12})가
# 전부 걸려 오탐이 200건 가까이 나왔다. 검사 결과를 사람이 읽지 않게 되면
# 검사기가 없는 것과 같으므로, 접속 정보로서 의미가 있는 형태만 남긴다.
#
# 또한 서비스 포트 자체는 nginx 구성을 설명하는 데 필요해 공개 문서에
# 그대로 두기로 이미 판단한 값이다. 포트 단독은 검사 대상이 아니다.
HOSTPORT_RE = re.compile(r"([A-Za-z0-9*_.-]+):(\d{2,5})\b")

# ssh -p 접속 명령. 따옴표를 넘어가지 않도록 경계를 둔다.
SSH_CMD_RE = re.compile(r"""\bssh\b[^\n"']*-p\s*\d+""")

# JSON 로그에는 줄바꿈이 문자 그대로("\n") 들어 있어, 뒤 단어와 붙어
# nclaude.ai 처럼 읽힌다. 검사 전에 공백으로 바꾼다.
ESCAPED_NEWLINE_RE = re.compile(r"\\[nrt]")

# ── 일괄 검사(--all) 범위 ────────────────────────────────────
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache",
             "node_modules", "assets"}

TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".yml", ".yaml",
                 ".cfg", ".ini", ".toml", ".sh", ".css", ".html"}

# 예시값이 본문인 파일. 검사기 자신과 그 근거·테스트가 여기 해당한다.
# 이 파일들은 "걸려야 하는 형태"를 일부러 담고 있으므로 검사 대상에서 뺀다.
# (대신 내용이 실제 값인지 여부는 코드 리뷰와 회귀 테스트로 확인한다.)
SELF_EXEMPT = {
    "scripts/check_secrets.py",
    "test_build_pdf.py",
    "docs/evidence/scan_pattern_evidence.txt",
}


def _allowed_ip(value: str) -> bool:
    return value in ALLOWED_HOSTS or value.startswith(ALLOWED_IP_PREFIXES)


def _allowed_domain(value: str) -> bool:
    """허용 도메인 자신과 그 하위 도메인을 통과시킨다.

    example.com 을 허용하기로 한 이상 evil.example.com 도 같은 성격이다.
    (7/28 DNS Rebinding 시험에서 위조 Host 로 사용한 값이며, 이 값이
     evidence 에 남아 있어야 421 차단 근거가 성립한다.)

    우리 서버 도메인은 허용 목록에 없으므로 이 규칙으로 새어 나가지 않는다.
    허용 목록에 항목을 추가하는 순간 그 하위 도메인까지 함께 허용된다는
    점을 인지하고 추가할 것.
    """
    v = value.lower()
    return any(v == d or v.endswith("." + d) for d in ALLOWED_DOMAINS)


def _is_real_host(token: str) -> bool:
    """콜론 앞 문자열이 실제 IP·도메인인지 판정한다."""
    return bool(IPV4_RE.fullmatch(token) or DOMAIN_RE.fullmatch(token))


def _leaked_hostport(host: str, port: str) -> bool:
    """host:port 쌍이 노출로 볼 형태인지 판정한다.

    IPv4 단독 검사(_allowed_ip)는 이미 127.0.0.1 등을 허용하는데,
    이 함수는 그 결과를 참조하지 않고 포트만 봤다. 그 결과 허용된
    루프백이어도 임시 포트(예: 클라이언트 접속 포트)가 붙으면 걸렸다.
    """
    return (_is_real_host(host)
            and not _allowed_ip(host)
            and port not in ALLOWED_PORTS)


def scan_text(text: str) -> list[str]:
    """공개하면 안 되는 '형태'의 값을 찾아 정렬된 목록으로 돌려준다.

    빈 목록이면 이상이 없다는 뜻이다.
    무엇이 걸렸는지 그대로 돌려주므로, 호출한 쪽에서 오탐 여부를
    사람이 판단할 수 있다.
    """
    text = ESCAPED_NEWLINE_RE.sub(" ", text)

    leaked: list[str] = []
    leaked += [v for v in IPV4_RE.findall(text) if not _allowed_ip(v)]
    leaked += [v for v in DOMAIN_RE.findall(text)
               if not _allowed_domain(v)]
    leaked += [v for v in EMAIL_RE.findall(text) if v not in ALLOWED_EMAILS]
    leaked += [f"{host}:{port}" for host, port in HOSTPORT_RE.findall(text)
               if _leaked_hostport(host, port)]
    leaked += SSH_CMD_RE.findall(text)
    return sorted(set(leaked))


def scan_file(path: Path) -> list[tuple[int, str, list[str]]]:
    """파일을 줄 단위로 검사한다.

    돌려주는 값: [(줄번호, 줄내용, 걸린값들), ...]
    줄 단위로 보는 이유는 어느 줄을 고쳐야 하는지 바로 알기 위해서다.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []          # 이진 파일 등은 검사 대상이 아니다

    hits = []
    for no, line in enumerate(text.splitlines(), start=1):
        leaked = scan_text(line)
        if leaked:
            hits.append((no, line.strip(), leaked))
    return hits


def collect_all() -> list[Path]:
    """--all 일 때 검사할 텍스트 파일 목록을 모은다."""
    files = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if p.relative_to(ROOT).as_posix() in SELF_EXEMPT:
            continue
        files.append(p)
    return sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="공개하면 안 되는 정보가 있는지 형태 기준으로 검사합니다.")
    parser.add_argument("paths", nargs="*", type=Path,
                        help="검사할 파일 경로 (여러 개 지정 가능)")
    parser.add_argument("--all", action="store_true",
                        help="레포 안의 텍스트 파일을 모두 검사")
    parser.add_argument("--quiet", action="store_true",
                        help="문제가 있는 파일만 출력")
    args = parser.parse_args()

    targets = collect_all() if args.all else [p for p in args.paths if p.is_file()]
    if not targets:
        print("[안내] 검사할 파일이 없습니다. 경로를 확인하거나 --all 을 쓰세요.")
        return 0

    total = 0
    for path in targets:
        hits = scan_file(path)
        if not hits:
            if not args.quiet:
                print(f"  OK   {path}")
            continue
        total += len(hits)
        print(f"\n  발견  {path}")
        for no, line, leaked in hits:
            preview = line if len(line) <= 70 else line[:70] + "..."
            print(f"    {no:>5}행  {leaked}")
            print(f"           {preview}")

    print(f"\n검사 파일 {len(targets)}개 · 의심 줄 {total}개")
    if total:
        print("오탐이면 check_secrets.py 의 ALLOWED_HOSTS / ALLOWED_IP_PREFIXES /\n"
              "ALLOWED_PORTS / ALLOWED_DOMAINS / ALLOWED_EMAILS 에 추가하세요.\n"
              "추가하기 전에 '이 값은 공개해도 되는가'를 반드시 확인할 것.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())