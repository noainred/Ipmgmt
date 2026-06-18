# Ipmgmt — 개발 가이드 (Claude Code)

## 워크플로 (중요)
- 변경 작업은 항상 별도 작업 브랜치에서 진행한다.
- 작업을 푸시한 뒤에는 **자동으로 Pull Request를 생성/갱신**한다. base 브랜치는 `main`.
  - 사용자가 매번 요청하지 않아도 PR을 연다. (사용자 지시: "앞으로 계속 자동으로 PR")
  - 같은 브랜치에 이미 열린 PR이 있으면 새로 만들지 말고 푸시로 해당 PR을 갱신한다.

## 프로젝트 구조
- `collector/` — 데이터센터별 수집기. nmap으로 서브넷 스캔 → 포탈로 push (`--demo` 모드 지원)
- `portal/`    — 중앙 Flask 포탈. ingest/조회 API + 웹 UI, SQLite(WAL) 저장
- `common/`    — collector·portal 공용 호스트 레코드 스키마/검증
- `scripts/demo.sh` — nmap/네트워크 없이 동작하는 로컬 end-to-end 데모

## 실행 / 검증
- 데모: `pip install -r requirements.txt && ./scripts/demo.sh` → http://127.0.0.1:8000
- 컴파일 체크: `python -m py_compile common/*.py collector/*.py portal/*.py`
- 실제 스캔(OS/uptime)은 root 권한 필요. 아니면 IP/호스트명/포트만 폴백 수집.
