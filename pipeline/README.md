# 학습 JSON 제작 파이프라인 (Claude Code 런타임판)

전공서 PDF → 소단원 분할 → (사람이 외부 AI로 소단원 JSON 생성) → 검증·병합 → `data/`에 반영하는
파이프라인을, **Claude Code 세션이 Colab을 대신해 실행**하도록 두 프로그램으로 묶은 것입니다.

> API를 호출하지 않습니다. **toc_data.json 생성**(목차 분류)과 **소단원/복습 JSON 생성**은
> 여전히 사용자가 외부 AI로 수동 진행합니다. 이 파이프라인은 그 앞뒤의 기계적 작업만 자동화합니다.

## 전체 흐름

```
[사람] 목차 이미지 + toc_classify_prompt.md → 외부 AI → toc_data.json
                    │
                    ▼
[프로그램 1] PDF + toc_data.json + 오프셋  →  소단원 분할(.txt/.pdf) + progress.md + zip 전송
                    │
                    ▼
[사람] 프로그램 2가 알려준 PDF+헤더를 system_prompt.md 붙인 외부 AI에 입력 → 소단원 JSON
                    │
                    ▼
[프로그램 2] 소단원 JSON 업로드 → 검증 → 저장 → 진행 갱신 → 다음 작업 안내
                    │  (한 챕터 소단원 전부 끝나면 최종복습 입력을 만들어 안내)
                    ▼
[사람] 최종복습 입력 → 외부 AI → 복습 JSON 업로드
                    │
                    ▼
[프로그램 2] 챕터 완료 감지 → 병합 → data/<slug>.json + manifest.json 갱신
                    │
                    ▼
[Claude] dev 브랜치에 커밋·푸시 + 병합본을 사용자에게 전송
```

## 상태 보존 (중요)

이 컨테이너는 비활성 시 초기화됩니다. 그래서 **텍스트 상태만 `dev` 브랜치에 커밋**해 다음 세션이 이어받습니다.

- 커밋됨: `work/progress.md`, `work/sections_out/*.txt`, `work/chapter_raw/*.json`, `work/state.json`
- 커밋 안 됨(`.gitignore`): 분할 `*.pdf`, 전달용 `_delivery/*.zip`, `_review_input_*.txt`
  → 분할 PDF는 프로그램 1이 만든 **zip으로 사용자에게 직접 전송**하며, 사용자가 로컬 보관합니다.

## 프로그램 1 — 분할기

```
python pipeline/program1_split.py --pdf <책.pdf> --toc <toc_data.json> --offset <정수>
    [--offset-override '{"App.A": 13}']   # 소단원별 오프셋 예외
```

- **오프셋**: `(그 페이지의 1-based PDF 쪽번호) − (그 페이지에 인쇄된 책 쪽수)`. 실행 시 사용자가 알려줍니다.
- 산출물: `work/sections_out/`(txt+pdf), `work/progress.md`, `work/state.json`, `work/_delivery/<책>_sections.zip`
- 로그 끝의 `⚠️⚠️⚠️` 경고(페이지 범위 누락 등)는 그대로 사용자에게 공지합니다.

## 프로그램 2 — 추적/수집/병합기

```
python pipeline/program2_track.py next                       # 다음 만들 소단원(번호·PDF·헤더·저장명) 안내
python pipeline/program2_track.py submit --json <완성.json>   # 소단원 JSON 검증·저장·진행갱신 (재업로드도 이걸로)
python pipeline/program2_track.py submit --json <복습.json> --chapter <N>   # 최종복습 JSON
python pipeline/program2_track.py submit --json <파일> --seq <NNN>          # 매칭 모호할 때 행 지정
python pipeline/program2_track.py undo --seq <NNN>           # 미완료로 되돌리고 결과 JSON 삭제(재작업용)
python pipeline/program2_track.py status                     # 챕터별 진행 현황
python pipeline/program2_track.py merge --chapter <N>        # 강제 병합(보통은 자동)
```

동작 규칙:
- **검증**은 `section_merger.html` 규칙을 이식한 `lib/merger.py`로 수행. `오류(✗)`가 있으면 저장하지 않고 공지만 합니다.
- **매칭**: 업로드 파일명이 변형돼도 되도록, JSON 안의 `section_title` 내용으로 progress 행을 찾습니다(파일명·`--seq`는 보조).
- **챕터 완료 = 소단원 전부 + 최종복습 JSON**. 완료되면 `data/<slug>.json`과 `data/manifest.json`을 갱신합니다.
- 출력의 `[[MERGED ...]]` / `[[NEED_REVIEW ...]]` 마커는 Claude가 다음 조치(커밋·전송/복습 안내)를 판단하는 신호입니다.

## 구성

| 파일 | 역할 |
|---|---|
| `program1_split.py` | 분할 진입점 (pdf_splitter + make_tracker 래핑, zip 전송) |
| `program2_track.py` | 추적/수집/검증/병합 진입점 |
| `lib/pdf_splitter.py` | PDF → 소단원 조각 (원본 그대로) |
| `lib/make_tracker.py` | 조각 → `progress.md` (원본 그대로) |
| `lib/next_section.py` | 다음 작업·헤더 안내, 진행 체크 (원본 그대로) |
| `lib/make_review_input.py` | 최종복습 입력 생성 (원본 그대로) |
| `lib/merger.py` | `section_merger.html`의 검증·병합을 Python으로 이식 (신규) |
| `prompts/toc_classify_prompt.md` | 목차 이미지 → `toc_data.json` 분류용 (외부 AI에 붙여넣는 사용자용 자료) |
| `prompts/system_prompt.md` | 소단원 PDF → 학습용 JSON 변환용 (외부 AI에 붙여넣는 사용자용 자료) |

의존성: `pymupdf` (`pip install pymupdf`).
