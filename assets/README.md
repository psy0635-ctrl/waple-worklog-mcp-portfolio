# assets

시연 영상과 스크린샷을 이 폴더에 넣습니다.

| 파일 | 내용 |
| --- | --- |
| `demo-local.mp4` | 로컬(stdio) 전체 시연 |
| `demo-waple.mp4` | Waple 등록 결과 화면 |
| `screenshots/` | 원격 연동 및 서버 로그 캡처 |

GitHub README 에 영상을 직접 임베드하려면 파일 크기를 10MB 이하로 맞춰야 합니다.
터미널 화면처럼 움직임이 적은 영상은 프레임레이트만 낮춰도 크게 줄어듭니다.

```bash
ffmpeg -i 입력.mp4 -vf "fps=30" -c:v libx264 -crf 22 -preset veryfast \
       -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart 출력.mp4
```
