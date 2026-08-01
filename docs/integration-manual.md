# Waple 업무일지 MCP 연동 매뉴얼

> 최종 수정: 2026-07-21 · 기준 브랜치: `dev/mcp`
> 대상 독자: 멘토(직접 테스트), 팀원(설치·사용)
> 배포 URL: `https://[SERVER_URL]/llm/mcp` (2026-07-21 E2E 검증 완료)

---

## 1. 개요

Claude / Claude Code에서 하루 작업 내역을 수집해 Waple 업무일지 초안을 만들고,
**사용자가 승인한 경우에만** Waple API(`POST`)로 등록하는 MCP 서버입니다.

연결 방법은 세 가지입니다.

| 입구 | 대상 | 통신 방식 | 인증 방식 | 상태 |
| --- | --- | --- | --- | --- |
| A. 원격 — Claude Code | 멘토·팀원 테스트 | Streamable HTTP | 등록 시 지정한 `x-api-key` 헤더 (요청마다 전달) | ✅ 검증 완료 (2-2절) |
| B. 원격 — Claude 데스크톱 앱 | 비개발자 채팅 사용 | Streamable HTTP (`mcp-remote` 브리지 경유) | config에 지정한 `x-api-key` 헤더 | ✅ 검증 완료 (2-6절) |
| C. 로컬 Claude Code (stdio) | 개발자 | stdio | `llm팀/.env`의 `WAPLE_API_KEY` | ✅ 검증 완료 (3장) |

같은 `server.py` 하나가 stdio와 Streamable HTTP 두 방식을 모두 지원합니다(이중 transport).

**A·B는 레포 클론·Python 설치가 필요 없습니다.** Waple API 키만 있으면 연결됩니다.
단, 원격은 사용자 PC의 Git 기록에 접근할 수 없으므로 Git 기반 자동 수집(`tasklog`)은
HTTP 모드에서 차단됩니다(2-4절 참고). 원격에서는 `chat_tasklog`를 사용합니다.

### ⚠️ claude.ai 웹(브라우저)은 사용할 수 없습니다 — 데스크톱 앱으로 우회

claude.ai **웹 화면**의 커스텀 커넥터 추가 UI는 인증 방식으로 **OAuth만 지원**하며,
`x-api-key` 같은 임의 헤더를 입력하는 항목이 존재하지 않습니다.
(임의 헤더 지정 기능인 `static_headers`는 조직 관리자 전용 베타 기능입니다.)
웹 화면에서 URL만 등록하면 커넥터 목록에는 나타나지만 인증이 통과되지 않습니다.

**단, 채팅 환경 자체가 막힌 것은 아닙니다.** Claude 데스크톱 앱은 웹 UI가 아니라
설정 파일(`claude_desktop_config.json`) 기반이므로 커스텀 헤더를 전달할 수 있습니다.
일반 채팅에서 사용하려면 **2-6절(데스크톱 앱 원격 연동)** 을 따르십시오.

---

## 2. 원격 서버로 연결하기 (A·B)

서버가 배포되어 있으므로 **레포 설치 없이 URL만으로** 연결할 수 있습니다.

### 2-1. 사전 준비 (공통)

- Waple([WAPLE_BASE_URL]) 로그인 → 환경설정 → API설정 → API 키 발급
- Claude Code 설치 (`npm install -g @anthropic-ai/claude-code`)

### 2-2. A. Claude Code 등록

터미널에서 아래 한 줄을 실행합니다.

```bash
claude mcp add --transport http waple-remote https://[SERVER_URL]/llm/mcp --header "x-api-key: 발급받은키"
```

| 옵션 | 의미 |
| --- | --- |
| `--transport http` | stdio가 아닌 Streamable HTTP로 연결 |
| `waple-remote` | 서버 별칭 (자유롭게 변경 가능) |
| `--header` | 요청마다 전달할 헤더. 이 경로에서만 `x-api-key` 지정이 가능합니다 |

등록 확인:

```bash
claude mcp list
# waple-remote: https://[SERVER_URL]/llm/mcp (HTTP) - Connected
```

### 2-3. 사용 흐름

1. `claude` 실행 후 "오늘 업무일지 초안 만들어줘" 요청
2. `chat_tasklog`가 대화 내용 기반 초안을 생성해 **전문을 표시** (이 단계에서는 등록되지 않음)
3. 내용 수정 요청 시 → 변경 요약 + 전체 초안 재표시
4. **"등록해" / "승인" / "Waple에 저장해"** 라고 말해야만 `submit_worklog`가 실제 등록
   - "정리해줘 / 검토해줘 / 수정해줘"는 등록 트리거가 아닙니다.
5. 등록 성공/실패 결과가 HTTP 상태 코드와 함께 표시됩니다.

### 2-4. 원격 모드 특이사항

- API 키는 **서버에 저장되지 않습니다.** 매 요청 헤더의 키를 그대로 Waple에 전달합니다 (HTTP 모드에서는 `.env` 저장·폴백이 차단됨).
- 초안 캐시는 사용자(키)별로 분리되어 다른 사용자와 섞이지 않습니다.
- 같은 날짜에 이미 업무일지가 있으면 **덮어쓰기(upsert)** 됩니다. 등록 전 미리보기에서 신규/수정 여부를 확인하세요.
- 원격 서버는 사용자 PC의 Git 저장소와 Claude Code 세션 로그에 접근할 수 없습니다.
  따라서 원격 경로에서는 Git 기반 `tasklog`가 아니라 대화 기반 `chat_tasklog`를 사용합니다.
  Git 커밋·토큰 집계가 필요하면 3장의 로컬 stdio 경로를 사용하세요.
- **원격에서 `tasklog`를 호출하면 차단 메시지가 반환됩니다(7/29).** 차단하지 않으면
  서버가 접근할 수 있는 유일한 저장소, 즉 **배포 서버 자신의 저장소**가 수집되어
  사용자가 하지 않은 커밋이 업무일지에 실립니다. 수집이 비어 있는 것보다 알아채기
  어려운 형태라 안내가 아니라 차단으로 처리했습니다.

### 2-5. ⚠️ 키 취급 주의

Claude에게 "`.env`에서 키를 찾아 로그인해줘"처럼 요청하면, 키 값을 터미널에 출력한 뒤 도구에 전달하는 경우가 있습니다.
이 경우 키가 터미널 스크롤백과 세션 로그에 평문으로 남습니다.

요청 시 **"키 값은 화면에 출력하지 마"** 를 함께 지시하고,
키가 노출된 경우 Waple에서 즉시 **재발급 후 기존 키를 삭제**하십시오.
Waple은 다중 키 구조라 재발급만으로는 기존 키가 무효화되지 않습니다.

또한 2-6절의 `mcp-remote` 브리지는 **전달받은 헤더를 그대로 로그에 출력**합니다
(외부 패키지 동작이라 수정 불가). 데스크톱 앱의 "로그 보기" 화면을
캡처·공유할 때 키가 함께 노출되지 않도록 주의하십시오.

### 2-6. B. Claude 데스크톱 앱 등록 (일반 채팅)

개발 도구 없이 **일반 채팅 화면**에서 원격 서버를 사용하는 방법입니다.

**중요:** `claude_desktop_config.json`은 **stdio 서버 형식만 인식**합니다.
Claude Code의 `.mcp.json`처럼 `"type": "http"`, `"url"`, `"headers"`를 적으면
항목이 조용히 무시되거나 설정 전체가 로드되지 않습니다.
따라서 공개 npm 패키지 `mcp-remote`를 **stdio ↔ HTTP 브리지**로 사용합니다.

```
데스크톱 앱 ─(stdio)─ mcp-remote ─(HTTPS + x-api-key)─ 원격 MCP 서버
```

**사전 준비**: Node.js 18+ (`node -v`로 확인)

1. 데스크톱 앱 → 설정 → 개발자 → 구성 편집 → `claude_desktop_config.json`
   (위치: `%APPDATA%\Claude\`)
2. `mcpServers` 안에 아래 항목을 추가합니다. **기존 항목은 지우지 않습니다.**

```json
{
  "mcpServers": {
    "waple-remote": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://[SERVER_URL]/llm/mcp",
        "--transport", "http-only",
        "--header", "x-api-key:${WAPLE_API_KEY}"
      ],
      "env": {
        "WAPLE_API_KEY": "발급받은키"
      }
    }
  }
}
```

| 항목 | 이유 |
| --- | --- |
| `-y` | npx 설치 확인 프롬프트 자동 승인. 없으면 첫 실행이 멈춤 |
| `--transport http-only` | SSE 협상 생략. 없으면 실행마다 약 5초 지연 |
| `x-api-key:${...}` | **콜론 뒤에 공백 금지.** 공백이 있으면 인자 파싱이 깨져 헤더가 전달되지 않습니다 |
| `env` 참조 | 키를 `args`에 직접 쓰면 프로세스 목록(작업관리자)에 명령줄이 노출됩니다 |

3. 저장 후 앱을 **완전 종료** — 창 닫기가 아니라 트레이 아이콘 우클릭 → 종료 후 재실행
4. 설정 → 개발자 → 로컬 MCP 서버에서 `waple-remote`가 `running`인지 확인

> ⚠️ **`running` 표시만으로 인증 성공을 판단하지 마십시오.**
> 브리지 프로세스는 키가 틀려도 기동됩니다. 채팅에서 `와플 로그인해줘`를 실행해
> 로그인 성공 응답까지 확인해야 합니다. 이때 **키를 입력하지 않았는데도 성공**하면
> 헤더가 정상 전달된 것입니다(2026-07-21 실측).

이후 사용 흐름은 2-3절과 동일합니다.

---

## 3. C. 로컬 Claude Code로 연결하기 (개발자)

### 3-1. 설치

```bash
# 1) 레포 클론
git clone https://github.com/uxis-co-kr/2026-uxis-mirae.git
cd 2026-uxis-mirae

# 2) 가상환경 (레포 루트에 .venv)
python -m venv .venv
source .venv/Scripts/activate   # Git Bash 기준. PowerShell은 .venv\Scripts\Activate.ps1

# 3) 의존성 설치
pip install mcp requests python-dotenv
```

### 3-2. 실행 위치 — 가장 흔한 실수 ⚠️

Claude Code는 **반드시 `llm팀/` 폴더에서** 열어야 합니다.

```bash
cd llm팀
claude
```

- 레포 루트에서 열면 `.mcp.json`을 인식하지 못합니다.
  이 경우 Claude가 MCP 없이 Bash로 git 명령을 직접 흉내 내므로, 겉보기에 정상처럼 보여도 실제 MCP 경로가 아닙니다.
- 확인 방법: Claude Code에서 `/mcp` 입력 → `waple-tasklog: connected` 표시 확인. 이게 안 보이면 실행 위치부터 다시 확인하세요.

### 3-3. API 키 등록 (최초 1회)

Claude Code 채팅에서:

```
waple_login 도구로 API 키 등록해줘. 키는 ○○○
```

- `GET /api/api-key/validate`로 키를 검증한 뒤 `llm팀/.env`에 저장합니다.
- 타임아웃·연결 오류 시 키는 저장되지 않으므로 같은 키로 재실행하면 됩니다.

### 3-4. 사용 흐름

1. `tasklog` 호출 → 오늘의 **Git 커밋·수정 파일·토큰 사용량**(Claude Code 세션 로그 기반)을 수집해 초안 생성
2. 초안 전문 확인 → 수정 요청 가능
3. 승인 표현("등록해" 등)으로만 `submit_worklog` 실행

### 3-5. 로컬·원격 동시 등록 시 주의

로컬(`waple-tasklog`)과 원격(`waple-remote`)을 함께 등록하면 **도구 이름이 동일**하여
어느 서버가 호출됐는지 구분되지 않습니다.
원격 경로를 검증할 때는 로컬을 일시적으로 제거하십시오.

```bash
claude mcp remove waple-tasklog
# 검증 후 원복
git checkout .mcp.json
```

---

## 4. 서버 직접 실행 (배포·로컬 테스트용)

### 4-1. stdio 모드 (기본)

Claude Code가 `.mcp.json`을 통해 자동 실행하므로 수동 실행이 필요 없습니다.

### 4-2. Streamable HTTP 모드 (원격 커넥터용)

```bash
# 로컬 테스트 (기본: 127.0.0.1:8000)
python server.py --transport streamable-http

# 배포 서버 (EdgeXpert) — 8000번은 타 서비스가 선점하여 8010 사용
python server.py --transport streamable-http --port 8010
```

- 엔드포인트: `http://<host>:<port>/mcp`
- 커넥터 등록 URL: `https://[SERVER_URL]/llm/mcp`
- 내부 구현: FastMCP(`stateless_http=True`), transport=`streamable-http`
- 인증: 매 요청의 `x-api-key` 헤더 (per-user, 서버에 키 저장 안 함)

### 4-3. 🔴 nginx 리버스 프록시 설정 — 누락 시 421 오류

FastMCP는 DNS Rebinding 공격 방지를 위해 **허용 Host를 `127.0.0.1`로 자동 제한**합니다.
프록시가 원래 Host(`[SERVER_URL]`)를 그대로 전달하면 서버가 이를 거부하고
**421 Misdirected Request**를 반환합니다.

`Host` 헤더를 내부 주소로 재작성해야 합니다.

```nginx
location /llm/ {
    proxy_pass http://127.0.0.1:8010/;
    proxy_set_header Host 127.0.0.1:8010;   # ← 이 줄이 없으면 421 발생
    proxy_http_version 1.1;
    proxy_buffering off;                     # SSE 스트리밍 대응
}
```

설정 반영:

```bash
sudo nginx -t                  # 문법 검사
sudo systemctl restart nginx
```

> **후속 과제:** 정석 해법은 `server.py`에서 `transport_security`로 허용 Host를 명시하는 것입니다.
> 현재는 nginx 우회로 동작하며, 서버 코드 수정은 배포 PR에 포함 예정입니다.

### 4-4. 🔴 HTTPS 인증서는 fullchain으로 설치할 것

원격 커넥터는 서버 간 통신이므로 **중간 인증서(intermediate)를 자동 보완하지 않습니다.**
leaf 인증서만 설치하면 브라우저와 Windows curl에서는 정상으로 보이지만
(AIA 기능으로 자동 보완됨), 리눅스 curl과 Anthropic 서버에서는 연결이 실패합니다.

검증 방법:

```bash
openssl s_client -connect [SERVER_URL]:443 -servername [SERVER_URL] < /dev/null 2>&1 | head -20
```

`Certificate chain`에 **leaf와 중간 CA가 모두** 표시되어야 정상입니다.

```
 0 s:CN=*.[SERVER_URL]
 1 s:C=GB, O=Sectigo Limited, CN=Sectigo Public Server Authentication CA DV R36
```

### 4-5. 배포 환경 (EdgeXpert 미니PC)

| 항목 | 값 |
| --- | --- |
| 접속 | `ssh -p [SSH_PORT] mirae@[SERVER_IP]` |
| 서버 경로 | `~/2026-uxis-mirae/llm팀` (브랜치 `dev/mcp`) |
| 포트 | 8010 (8000·8081은 타 서비스 사용 중) |
| 로그 | `~/mcp.log` |

서비스 운영 (systemd 사용자 서비스, 7/27 전환 완료):

```bash
systemctl --user status waple-mcp     # 상태 확인
systemctl --user restart waple-mcp    # 재시작 (배포 후)
tail -f ~/mcp.log                     # 로그
```

- 유닛 파일: `~/.config/systemd/user/waple-mcp.service` (레포 사본은 `deploy/waple-mcp.service`)
- `Restart=always` + `loginctl enable-linger`로 크래시·재부팅 후 자동 복구합니다.
- `WorkingDirectory`는 `%h/2026-uxis-mirae/llm팀`입니다. 이 경로가 원격 `tasklog`가
  읽던 저장소였으며, 그래서 HTTP 모드에서는 `tasklog`를 차단합니다(7/29, 7절 참조).

> 🔴 **`nohup` 방식은 사용하지 마십시오.** systemd가 이미 8010 포트를 점유하고
> 있어 포트 충돌이 발생하며, 어느 프로세스가 응답 중인지 구분되지 않습니다.

> 생존 확인:
> ```bash
> curl -s -o /dev/null -w "%{http_code}\n" https://[SERVER_URL]/llm/mcp
> ```
> **406**이 정상입니다(MCP는 `Accept: text/event-stream`을 요구). 000·502면 서버가 내려간 상태입니다.

---

## 5. MCP 도구 목록

| 도구 | 역할 | 비고 |
| --- | --- | --- |
| `waple_login` | API 키 검증·저장 | stdio 전용 저장. HTTP 모드에서는 저장 차단(헤더 방식 사용) |
| `tasklog` | Git 커밋·파일·토큰 기반 초안 생성 | 로컬 Claude Code 전용. **HTTP(원격) 모드에서는 호출 차단** — 차단하지 않으면 배포 서버 자신의 저장소가 수집됨 (7/29 정정) |
| `chat_tasklog` | 대화 내용 기반 초안 생성 | 원격 커넥터용. 토큰은 "집계 대상 아님" 표기 |
| `submit_worklog` | 승인된 초안 등록 | 서버 캐시 원본 그대로 전송 → 미리보기 = 등록본 100% 일치 |

> 지침 14는 6개 도구 분리를 권장하나, 수집·분류·초안생성은 하나의 흐름이라 3개(+로그인)로 통합했습니다. 초안 생성과 등록은 분리 원칙을 유지합니다.

---

## 6. 안전장치 요약

- **승인 없는 등록 불가**: 명확한 승인 표현이 있을 때만 `submit_worklog` 호출
  (프롬프트 지시에 의한 통제이며, 코드로 강제되지는 않습니다)
- **1회용 캐시**: 등록 성공 시에만 초안 캐시 초기화, 실패 시 유지(재시도 안전). 서버 재시작 시 캐시가 사라지므로 초안부터 다시 생성 필요
- **확인 안 된 사실 미작성**: 추정 항목은 "사용자 확인 필요"로 분리, 자기보고는 `[사용자 메모]` 라벨
- **키 보호**: `.env`는 `.gitignore` 등록, 로그·화면 출력 시 마스킹

---

## 7. 자주 발생하는 오류

| 증상 | 원인 | 해결 |
| --- | --- | --- |
| `/mcp`에 waple-tasklog 없음 | 레포 루트에서 Claude Code 실행 | `llm팀/` 폴더에서 재실행 |
| 401 오류 | API 키 무효·오타 | Waple에서 키 재발급 후 재등록 |
| **421 Misdirected Request** | nginx가 원래 Host를 전달 → FastMCP가 거부 | 4-3절 `proxy_set_header Host 127.0.0.1:8010` 적용 |
| **claude.ai 웹 커넥터 인증 실패** | 웹 UI가 커스텀 헤더를 지원하지 않음 | 제품 제약. Claude Code(2-2절) 또는 데스크톱 앱(2-6절) 사용 |
| 데스크톱 config에 `url`/`type:"http"`를 넣었더니 항목이 사라짐 | 데스크톱 config는 stdio 스키마만 검증 | `mcp-remote` 브리지 방식으로 등록 (2-6절) |
| 데스크톱 `waple-remote`가 `running`인데 인증 실패 | `--header` 값의 콜론 뒤 공백으로 인자 파싱이 깨짐 | `x-api-key:키` 형태로 공백 없이 지정 |
| 데스크톱 원격 등록 직후 잠깐 연결 실패 | `npx`가 `mcp-remote`를 처음 내려받는 중 | 10~30초 후 재확인 |
| 원격에서 `tasklog` 호출 시 "원격 연결에서는 사용할 수 없습니다" 응답 | 의도된 차단 — 원격 서버는 사용자 PC가 아니라 자기 저장소를 읽음 | 정상 동작. `chat_tasklog` 사용 또는 로컬 경로(3장) |
| **리눅스 curl은 실패, 브라우저는 성공** | 인증서 중간 CA 누락 | 4-4절 fullchain 설치 |
| 등록했는데 결과 불명 | 네트워크 오류 등 | 캐시가 유지되므로 "등록해" 재시도 (중복 등록은 upsert라 안전) |
| 커넥터 연결 실패(000·502) | 서버 미기동 | 4-5절 기동 명령으로 재실행 |
| 첫 응답이 매우 느림 | EXAONE 콜드스타트(~80초) | 사용 전 워밍업 호출 1회 |

---

## 8. 테스트 확인 항목 (멘토용 체크리스트)

- [ ] `claude mcp add` 후 `claude mcp list`에 Connected 표시 확인
- [ ] 초안 생성(`chat_tasklog`) 동작 확인
- [ ] 승인 없이 "정리해줘"만으로는 등록되지 않는지 확인
- [ ] "등록해" 승인 → Waple 화면에서 등록 내용 = 미리보기 일치 확인
- [ ] 잘못된 API 키로 401 처리 확인
- [ ] 같은 날짜 재등록 시 덮어쓰기 동작 확인
- [ ] (데스크톱 앱) 2-6절 config 등록 후 `running` 표시 확인
- [ ] (데스크톱 앱) 키 미입력 상태로 `와플 로그인해줘` → 성공 응답 확인

---

## 9. 검증 이력

| 일자 | 항목 | 결과 |
| --- | --- | --- |
| 2026-07-20 | EdgeXpert 배포, nginx 라우팅, 외부 initialize 핸드셰이크 | ✅ 200 OK |
| 2026-07-20 | 421 오류 → Host 재작성으로 해결 | ✅ |
| 2026-07-21 | HTTPS 인증서 fullchain 적용 확인 | ✅ |
| 2026-07-21 | 원격 커넥터 `x-api-key` 전달 및 Waple 등록 E2E (Claude Code) | ✅ 실등록 확인 |
| 2026-07-21 | 데스크톱 앱 config에 `type:"http"` 직접 지정 | ❌ 미지원(stdio 스키마만 인식) |
| 2026-07-21 | 데스크톱 앱 + `mcp-remote` 브리지 원격 연동 | ✅ 키 미입력 상태로 `waple_login` 성공 |
| 2026-07-21 | 원격에서 `tasklog` 자동 수집 | ⚠️ 당시 기록 "Git·토큰 수집 불가" — 7/29 정정됨(아래) |
| 2026-07-29 | 원격 `tasklog`가 배포 서버 저장소를 수집하던 문제 확인 | ✅ 원인 `WorkingDirectory=%h/2026-uxis-mirae/llm팀` 실측 |
| 2026-07-29 | HTTP 모드 `tasklog` 차단 적용 후 배포 서버 실측 | ✅ 차단 응답 반환, 서버 로그 `CallToolRequest` 확인 |
| — | claude.ai 웹(브라우저) 커스텀 커넥터 | ❌ 제품 제약으로 불가 → 데스크톱 앱으로 우회 |