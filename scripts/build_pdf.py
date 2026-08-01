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
import subprocess
import sys
from pathlib import Path

# 정보 노출 검사는 check_secrets.py 한 곳에 둔다.
# 같은 판정이 PDF 검증·문서 검사·푸시 전 점검 세 곳에서 따로 돌면
# 한 곳만 갱신됐을 때 나머지가 조용히 어긋난다.
# 허용 목록도 함께 가져와 이 파일에서 다시 정의하지 않는다.
from check_secrets import (  # noqa: F401  (테스트에서 참조)
    ALLOWED_DOMAINS,
    ALLOWED_EMAILS,
    ALLOWED_HOSTS,
    ALLOWED_PORTS,
    scan_text,
)

# markdown·weasyprint 는 build() 안에서 import 한다.
# 모듈 최상단에 두면 검사 기능만 쓰고 싶을 때도 렌더링 라이브러리가
# 없으면 import 자체가 실패한다. 실제로 이 결합 때문에 회귀 테스트가
# 수집 단계에서 중단됐다. 근거: docs/evidence/scan_pattern_evidence.txt

# ── 경로 (레포 루트 기준) ──
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "waple-worklog-mcp-상세설명.md"
OUT = ROOT / "docs" / "waple-worklog-mcp-상세설명.pdf"
CSS_PATH = Path(__file__).resolve().parent / "pdf_style.css"

# 변환 결과에 반드시 들어 있어야 하는 문자열.
# 폰트 누락이나 렌더링 실패를 조용히 넘기지 않기 위한 최소 검증 기준이다.
REQUIRED_STRINGS = ["프로젝트 개요", "자동화 테스트"]


def build() -> None:
    """마크다운을 HTML로 변환한 뒤 CSS를 적용해 PDF로 렌더링한다."""
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
    # 이전에는 여기서 경고만 출력하고 return 했다. 그러면 "검사를 안 함"과
    # "검사를 통과함"이 똑같이 종료 코드 0 이 되어, PDF가 생성됐다는 사실만
    # 남고 내용이 맞는지는 아무도 확인하지 않은 상태가 된다.
    # 검증이 불가능하면 성공으로 끝내지 않는다.
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

    leaked = scan_text(text)
    if leaked:
        sys.exit("[중단] 공개하면 안 되는 정보로 보이는 값이 있습니다: "
                 f"{leaked}\n"
                 "       오탐이면 scripts/check_secrets.py 의\n"
                 "       ALLOWED_HOSTS / ALLOWED_PORTS /\n"
                 "       ALLOWED_DOMAINS / ALLOWED_EMAILS 에 추가하세요.\n"
                 "       추가하기 전에 '이 값은 공개해도 되는가'를 확인할 것.")

    pages = text.count("\f")
    print(f"[검증] 한글 정상 · 노출 없음 · 약 {pages}페이지")
    print("[안내] 표 잘림은 자동으로 감지되지 않습니다.\n"
          "       pdftoppm -png 로 렌더링해 육안 확인하세요.")


if __name__ == "__main__":
    build()
    verify()