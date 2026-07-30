# scripts — 문서 빌드

`docs/waple-worklog-mcp-상세설명.md` 를 PDF로 변환하는 스크립트입니다.

## 실행

레포 루트에서 실행합니다.

```bash
python scripts/build_pdf.py
```

| 구분 | 경로 |
| --- | --- |
| 입력 | `docs/waple-worklog-mcp-상세설명.md` |
| 출력 | `docs/waple-worklog-mcp-상세설명.pdf` (덮어씀) |
| 서식 | `scripts/pdf_style.css` |

서식만 바꾸고 싶을 때는 `pdf_style.css` 만 수정하면 됩니다.

## 실행 환경

**데비안·우분투 계열 리눅스에서만 동일한 결과가 나옵니다.**
아래 폰트가 시스템에 설치되어 있어야 하며, Windows에는 없는 폰트가
포함되어 있어 Windows 로컬 실행 시 결과물이 달라집니다.

### 파이썬 패키지

```bash
pip install weasyprint markdown
```

### 폰트

```bash
sudo apt-get install -y fonts-noto-cjk fonts-dejavu-core fonts-noto-color-emoji
```

| 용도 | 폰트 |
| --- | --- |
| 한글 본문 | Noto Sans CJK KR |
| 코드 (영문) | DejaVu Sans Mono |
| 코드 (한글) | Noto Sans Mono CJK KR |
| 이모지 | Noto Color Emoji |

### 검증 도구 (선택)

```bash
sudo apt-get install -y poppler-utils
```

`pdftotext` 가 있으면 스크립트가 생성 직후 내용 검증까지 수행합니다.
없으면 검증을 건너뛰고 경고만 출력합니다.

## 주의

**폰트가 없어도 스크립트는 오류 없이 정상 종료됩니다.**
이 경우 PDF는 만들어지지만 한글이 통째로 빠지거나 네모(□)로 표시됩니다.
그래서 `build_pdf.py` 는 생성 후 아래 두 가지를 자동으로 확인합니다.

1. 본문에서 한글 문자열이 실제로 추출되는지 (폰트 누락 감지)
2. 서버 IP·포트 등 공개하면 안 되는 값이 섞이지 않았는지

둘 중 하나라도 걸리면 오류 메시지를 내고 중단합니다.

다만 **표가 페이지 폭을 넘어 잘리는 것은 자동으로 잡히지 않습니다.**
문서를 크게 고친 뒤에는 해당 페이지를 눈으로 확인하시기 바랍니다.

```bash
pdftoppm -png -r 110 -f 8 -l 8 docs/waple-worklog-mcp-상세설명.pdf page8
```

`-f` 는 시작 페이지, `-l` 은 끝 페이지입니다.

## 참고 — 이 스크립트를 만든 이유

이전까지 이 PDF는 일회성 작업 환경에서 생성되어 변환 명령과 CSS가
레포에 남지 않았습니다. 그 결과 문서를 고칠 때마다 변환 방법을 다시
찾아야 했고, 실제로 2026-07-30 작업 중 원본 CSS를 복원하지 못해
서식을 다시 정의해야 했습니다.

산출물만 있고 만드는 방법이 없으면 재현 가능한 결과물이 아니라고 보아,
변환 절차 자체를 레포에 포함시켰습니다.
