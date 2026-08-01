"""
상세설명 마크다운 → PDF 변환 스크립트.

이 스크립트가 존재하는 이유:
    이전까지 이 PDF는 일회성 작업 환경에서 생성되어, 변환 명령과 CSS가
    어디에도 남지 않았다. 그래서 문서를 고칠 때마다 "어떻게 만들었더라"를
    다시 찾아야 했다. 산출물만 있고 만드는 방법이 없으면 재현 가능한 결과물이
    아니므로, 변환 절차 자체를 레포에 남긴다.

실행 (레포 루트에서):
    python scripts/build_pdf.py

    입력  docs/waple-worklog-mcp-상세설명.md
    출력  docs/waple-worklog-mcp-상세설명.pdf
    서식  scripts/pdf_style.css

필요 환경: scripts/README.md 참고. 폰트가 없으면 한글이 통째로 빠진 채
          생성되므로, 아래 검증 단계를 건너뛰지 말 것.
"""
import re
import subprocess
import sys
from pathlib import Path

# ★ markdown·weasyprint 는 build() 안에서 import 한다.
#   모듈 최상단에 두면, 정보 노출 검사(scan_text)만 쓰고 싶을 때도
#   렌더링 라이브러리가 없으면 import 자체가 실패한다.
#   검사 함수를 다른 곳에서 재사용하기 위한 조치다.

# ── 경로 (레포 루트 기준) ──
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "waple-worklog-mcp-상세설명.md"
OUT = ROOT / "docs" / "waple-worklog-mcp-상세설명.pdf"
CSS_PATH = Path(__file__).resolve().parent / "pdf_style.css"

# 변환 결과에 반드시 들어 있어야 하는 문자열.
# 폰트 누락이나 렌더링 실패를 조용히 넘기지 않기 위한 최소 검증 기준이다.
REQUIRED_STRINGS = ["프로젝트 개요", "자동화 테스트"]

# ─────────────────────────────────────────────────────────────
# 정보 노출 검사
#
# 공개 레포용 문서이므로 배포 서버 정보가 섞이면 중단한다.
#
# 주의: 금지할 값(서버 IP·도메인·포트 등)을 여기에 그대로 적으면
#       "노출을 막는 코드"가 그 값을 공개하는 모순이 생긴다.
#       그래서 구체적인 값 대신 형태(패턴)로 검사한다.
#       부수 효과로, 미리 예상하지 못한 주소까지 함께 걸린다.
#
#       단, 형태로 검사하더라도 형태를 좁게 잡으면 뚫린다.
#       실제로 이전 버전은 도메인을 아예 보지 않아
#       `CN=*.example.co.kr` 같은 값이 그대로 통과했다.
# ─────────────────────────────────────────────────────────────

# 문서에 정상적으로 등장하는 주소 (transport_security 설명 등)
ALLOWED_HOSTS = {"127.0.0.1", "0.0.0.0", "255.255.255.255", "::1"}

# 흔히 쓰는 개발 포트는 문서에 나와도 문제가 없다
ALLOWED_PORTS = {"80", "443", "3000", "5000", "8000", "8010", "8080"}

# ★ 공개 문서에 나와도 되는 외부 도메인.
#   여기에 추가할 때는 "이건 공개해도 되는 값인가"를 매번 확인할 것.
#   오탐이 귀찮다고 통째로 넓히면 검사기가 다시 눈이 먼다.
ALLOWED_DOMAINS = {
    "example.com", "localhost",
    "github.com", "raw.githubusercontent.com",
    "claude.ai", "anthropic.com", "modelcontextprotocol.io",
    "python.org", "pypi.org",
}

# ★ 문서에 정상적으로 등장하는 메일 주소 (필요해지면 추가)
ALLOWED_EMAILS: set[str] = set()

# 1.2.3.4 형태
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# ★ 도메인 — TLD 목록으로 범위를 좁힌다.
#   - 서브도메인 자리에 * 와 _ 를 허용해 와일드카드 표기(*.example.co.kr)도 잡는다
#   - (?:...\.)* 이므로 서브도메인이 없는 맨 도메인(example.kr)도 잡힌다
#     ← 이전 검사가 "서브도메인이 반드시 있다"고 가정해 뚫렸던 지점
_TLD = r"(?:co\.kr|or\.kr|go\.kr|kr|com|net|org|io|ai|dev|app|cloud)"
DOMAIN_RE = re.compile(rf"\b(?:[A-Za-z0-9*_-]+\.)*[A-Za-z0-9*_-]+\.{_TLD}\b")

# ★ 메일 주소 — 이전 코드는 정규식이 이미 @ 를 요구하는데
#   뒤에서 다시 `if "@" in m` 으로 걸렀다. 항상 참이라
#   필터가 있는 것처럼 보이지만 아무 일도 하지 않았다.
#   여기서는 TLD 를 강제해 파일명·데코레이터 오탐을 줄인다.
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")

# :포트번호 형태. 앞에 숫자나 콜론이 오면 포트가 아니다.
PORT_RE = re.compile(r"(?<![\d:]):(\d{2,5})\b")

# ★ 시각 표기(17:02, 17:02:12)는 포트가 아니므로 검사 전에 지운다.
#   PORT_RE 의 선행 부정만으로는 "17:02" 의 :02 를 걸러내지 못한다.
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")

# ssh -p 접속 명령
SSH_CMD_RE = re.compile(r"\bssh\b[^\n]*-p\s*\d+")


def scan_text(text: str) -> list[str]:
    """공개하면 안 되는 '형태'의 값을 찾아 정렬된 목록으로 돌려준다.

    반환값이 빈 목록이면 노출 의심 값이 없다는 뜻이다.
    무엇이 걸렸는지 그대로 돌려주므로, 호출한 쪽에서
    오탐 여부를 사람이 판단할 수 있다.
    """
    leaked: list[str] = []
    leaked += [v for v in IPV4_RE.findall(text) if v not in ALLOWED_HOSTS]
    leaked += [v for v in DOMAIN_RE.findall(text)
               if v.lower() not in ALLOWED_DOMAINS]
    leaked += [v for v in EMAIL_RE.findall(text) if v not in ALLOWED_EMAILS]
    leaked += [f":{p}" for p in PORT_RE.findall(TIME_RE.sub(" ", text))
               if p not in ALLOWED_PORTS]
    leaked += SSH_CMD_RE.findall(text)
    return sorted(set(leaked))


def build() -> None:
    """마크다운을 HTML로 변환한 뒤 CSS를 적용해 PDF로 렌더링한다."""
    # ★ 지연 import — 파일 상단 주석 참고
    try:
        import markdown
        from weasyprint import CSS, HTML
    except ImportError as e:
        sys.exit(f"[중단] 변환에 필요한 패키지가 없습니다: {e.name}\n"
                 "       가상환경을 확인하고 다음을 실행하세요.\n"
                 "         pip install weasyprint markdown\n"
                 "       자세한 환경 조건은 scripts/README.md 참고.")

    for path in (SRC, CSS_PATH):
        if not path.exists():
            sys.exit(f"[중단] 파일을 찾을 수 없습니다: {path}")

    md_text = SRC.read_text(encoding="utf-8")

    # tables       : 마크다운 표 문법
    # fenced_code  : ``` 코드블록
    # nl2br 은 쓰지 않는다. 켜면 문단 안의 줄바꿈이 전부 <br>로 바뀌어
    # 본문 줄 간격이 원본과 달라진다.
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])

    html_doc = (
        '<!DOCTYPE html><html lang="ko"><head>'
        '<meta charset="utf-8"></head><body>'
        f"{html_body}</body></html>"
    )

    HTML(string=html_doc, base_url=str(ROOT)).write_pdf(
        OUT, stylesheets=[CSS(filename=str(CSS_PATH))]
    )
    print(f"[생성] {OUT}")


def verify() -> None:
    """생성만 하고 끝내지 않는다. 폰트 누락·정보 노출을 실제로 확인한다."""
    try:
        text = subprocess.run(
            ["pdftotext", str(OUT), "-"],
            capture_output=True, text=True, check=True,
        ).stdout
    # ★ 이전에는 여기서 경고만 출력하고 return 했다.
    #   그러면 "검사를 안 함"과 "검사를 통과함"이 똑같이 종료 코드 0 이 되어,
    #   PDF가 생성됐다는 사실만 남고 내용이 맞는지는 아무도 확인하지 않은
    #   상태가 된다. 검증이 불가능하면 성공으로 끝내지 않는다.
    except FileNotFoundError:
        sys.exit("[중단] pdftotext 를 찾을 수 없어 검증할 수 없습니다.\n"
                 "       검증을 건너뛰면 PDF가 생성됐다는 사실만 남고\n"
                 "       내용이 맞는지는 확인되지 않은 상태가 됩니다.\n"
                 "       poppler-utils 를 설치한 뒤 다시 실행하세요.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"[중단] pdftotext 실행이 실패했습니다 (종료 코드 {e.returncode}).\n"
                 f"       {(e.stderr or '').strip()}")

    missing = [s for s in REQUIRED_STRINGS if s not in text]
    if missing:
        sys.exit(f"[중단] 본문에서 확인되지 않은 문자열: {missing}\n"
                 "       한글 폰트가 적용되지 않았을 가능성이 큽니다.")

    # ★ 검사 로직을 scan_text() 로 분리했다.
    #   같은 판정을 evidence 파일이나 푸시 전 점검에서도 쓰기 위한 것이며,
    #   패턴이 여러 곳에 흩어져 한 곳만 갱신되는 상황을 막는다.
    leaked = scan_text(text)
    if leaked:
        sys.exit("[중단] 공개하면 안 되는 정보로 보이는 값이 있습니다: "
                 f"{leaked}\n"
                 "       오탐이면 ALLOWED_HOSTS / ALLOWED_PORTS /\n"
                 "       ALLOWED_DOMAINS / ALLOWED_EMAILS 에 추가하세요.\n"
                 "       추가하기 전에 '이 값은 공개해도 되는가'를 확인할 것.")

    pages = text.count("\f")
    print(f"[검증] 한글 정상 · 노출 없음 · 약 {pages}페이지")
    print("[안내] 표 잘림은 자동으로 감지되지 않습니다.\n"
          "       pdftoppm -png 로 렌더링해 육안 확인하세요.")


if __name__ == "__main__":
    build()
    verify()