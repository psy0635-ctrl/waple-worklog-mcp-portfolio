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
          정상 종료되므로, 아래 검증 단계를 건너뛰지 말 것.
"""
import subprocess
import sys
from pathlib import Path

import markdown
from weasyprint import CSS, HTML

# ── 경로 (레포 루트 기준) ──
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "waple-worklog-mcp-상세설명.md"
OUT = ROOT / "docs" / "waple-worklog-mcp-상세설명.pdf"
CSS_PATH = Path(__file__).resolve().parent / "pdf_style.css"

# 변환 결과에 반드시 들어 있어야 하는 문자열.
# 폰트 누락이나 렌더링 실패를 조용히 넘기지 않기 위한 최소 검증 기준이다.
REQUIRED_STRINGS = ["프로젝트 개요", "자동화 테스트"]

# 공개 레포용 문서이므로 아래 값이 섞여 들어가면 즉시 중단한다.
FORBIDDEN_PATTERNS = ["61.32.164.99", "211.41.122.62", "60022"]


def build() -> None:
    """마크다운을 HTML로 변환한 뒤 CSS를 적용해 PDF로 렌더링한다."""
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
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[경고] pdftotext 를 실행할 수 없어 내용 검증을 건너뜁니다.")
        return

    missing = [s for s in REQUIRED_STRINGS if s not in text]
    if missing:
        sys.exit(f"[중단] 본문에서 확인되지 않은 문자열: {missing}\n"
                 "       한글 폰트가 적용되지 않았을 가능성이 큽니다.")

    leaked = [p for p in FORBIDDEN_PATTERNS if p in text]
    if leaked:
        sys.exit(f"[중단] 공개하면 안 되는 값이 포함되어 있습니다: {leaked}")

    pages = text.count("\f")
    print(f"[검증] 한글 정상 · 노출 없음 · 약 {pages}페이지")


if __name__ == "__main__":
    build()
    verify()
