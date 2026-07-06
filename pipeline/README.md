# 학습 JSON 제작 파이프라인 (Claude Code 런타임판)

전공서 PDF → 소단원 분할 → (사람이 외부 AI로 소단원 JSON 생성) → 검증·병합 → `data/`에 반영하는
파이프라인을, **Claude Code 세션이 Colab을 대신해 실행**하도록 두 프로그램으로 묶은 것입니다.

> API를 호출하지 않습니다. **toc_data.json 생성**(목차 분류)과 **소단원 JSON 생성**은
> 여전히 사용자가 외부 AI로 수동 진행합니다. 이 파이프라인은 그 앞뒤의 기계적 작업만 자동화합니다.

## 콘텐츠 스키마 — `step3_checkpoint`의 `type`

각 소단원 JSON의 `step3_checkpoint` 배열 항목은 `type` 필드로 세 종류를 구분한다 (자세한 작성 기준은
`prompts/system_prompt.md` 참고):

| `type` | 필드 | 의미 |
|---|---|---|
| `"checkpoint"` (기본값, 생략 가능) | `problem`, `hint`, `solution`, `visualization?` | 즉시 적용 문제 |
| `"ox"` | `question`, `answer`("O"\|"X"), `explanation` | 이 소단원의 O/X 퀴즈 |
| `"conceptual"` | `question`, `hint`, `answer` | 이 소단원의 개념 질문 |

`type`이 없는 예전 항목은 `"checkpoint"`로 취급되어 계속 유효하다(하위 호환). 챕터 최상위의
`step4_final_review`는 더 이상 쓰지 않으며, 병합된 챕터 JSON은 `chapter_info` + `learning_flow`만 갖는다.
이미 예전 방식(챕터 단위 `step4_final_review`)으로 완성된 챕터가 있다면 `index.html`이 그 챕터에 한해서만
레거시 "최종 복습" 화면을 계속 보여준다 — 새로 만드는 챕터에는 그 화면 자체가 나타나지 않는다.

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
                    │  (그 소단원의 복습(ox/conceptual)도 system_prompt.md 규칙에 따라
                    │   이미 그 JSON의 step3_checkpoint 안에 함께 들어있다 — 별도 복습 단계 없음)
                    ▼
[프로그램 2] 챕터의 모든 소단원 완료 감지 → 병합 → data/<slug>.json + manifest.json 갱신
                    │
                    ▼
[Claude] dev 브랜치에 커밋·푸시 + 병합본을 사용자에게 전송
```

> **최종복습은 챕터 단위로 따로 만들지 않는다.** 예전에는 소단원들을 다 만든 뒤 챕터 전체를 훑는
> 복습 JSON을 한 번 더 만들어 병합했는데, 챕터가 길어질수록 그 복습 문제가 한 곳에 계속 누적되며
> 품질이 떨어지는 문제가 있었다. 지금은 `system_prompt.md`가 각 소단원 JSON을 만들 때 그 소단원 몫의
> `ox`/`conceptual` 문제를 `step3_checkpoint` 배열 안에 `type` 태그로 함께 만들도록 바뀌었다. 그래서
> 소단원 JSON들을 순서대로 다 모으면 그게 곧 챕터의 완성본이고, 프로그램 2가 별도로 더 만들 것이 없다.

## 여러 책 지원

상태는 **책마다 분리된 폴더** `work/<책 슬러그>/`에 저장된다(슬러그는 `toc_data.json`의 `book` 필드에서
`lib/merger.slug_id`로 만든다). 그래서 여러 전공서를 동시에 진행해도 진행 체크·조각·결과 JSON이 절대 섞이지 않는다.

- 프로그램 1은 `toc_data.json`의 `book`을 읽어 자동으로 `work/<슬러그>/`를 만든다(`--work-dir`로 강제 지정 가능).
- 프로그램 2는 `--book <책 이름 또는 슬러그>`로 대상 책을 고른다. 생략하면: `work/` 안에 책 폴더가 **하나뿐일 때만**
  자동으로 그 책을 쓰고, 여러 개면 목록을 보여주고 `--book`을 요구한다(다른 책 상태를 잘못 건드리는 사고 방지).
- `data/manifest.json`의 각 항목에는 항상 `book` 필드(`"저자 성 — 책 제목"`)가 채워진다 — `index.html`이 이
  필드로 상단 드롭다운을 책별로 그룹화하므로, 여러 책을 함께 쓸 계획이면 반드시 있어야 한다.
  (수동 병합용 `section_merger.html`도 동일하게 `book` 필드를 넣도록 맞춰 두었다 — 예전 버전은 이 필드를 빼먹었었다.)

## 순서 보장 (중요)

같은 챕터 안에서는 **소단원을 반드시 순서대로(seq 오름차순) 완성**해야 한다. `next_section.py`가 만드는 헤더의
"이전 소단원 핵심 결과" 블록은 `chapter_raw/`에 실제로 저장된 **직전 최대 3개 소단원**의 결과를 동적으로 읽어와
채우기 때문에 — 순서를 건너뛰면 그 참조가 비거나 틀어진다. `submit`은 같은 챕터에서 더 앞선 seq가 아직
미완료인데 뒤의 seq를 제출하면 **⚠ 순서 경고**를 출력한다(저장은 막지 않되 반드시 알린다). 이 경고가 뜨면
먼저 앞선 소단원을 완료하고, 뒤에 잘못 제출된 소단원은 헤더를 다시 받아 재생성하는 것이 원칙이다.

## 상태 보존 (중요)

이 컨테이너는 비활성 시 초기화됩니다. 그래서 **텍스트 상태만 `dev` 브랜치에 커밋**해 다음 세션이 이어받습니다.

- 커밋됨: `work/<책>/progress.md`, `work/<책>/sections_out/*.txt`, `work/<책>/chapter_raw/*.json`,
  `work/<책>/state.json`, `work/<책>/toc_data.json`
- 커밋 안 됨(`.gitignore`): 분할 `*.pdf`, 전달용 `_delivery/*.zip`, `_review_input_*.txt`
  → 분할 PDF는 프로그램 1이 만든 **zip으로 사용자에게 직접 전송**하며, 사용자가 로컬 보관합니다.

## 프로그램 1 — 분할기

```
python pipeline/program1_split.py --pdf <책.pdf> --toc <toc_data.json> --offset <정수>
    [--offset-override '{"App.A": 13}']   # 소단원별 오프셋 예외
    [--work-dir <경로>]                    # 기본은 work/<책 슬러그>(book 필드로 자동 결정)
```

- **오프셋**: `(그 페이지의 1-based PDF 쪽번호) − (그 페이지에 인쇄된 책 쪽수)`. 실행 시 사용자가 알려줍니다.
- 산출물: `work/<책>/sections_out/`(txt+pdf), `work/<책>/progress.md`, `work/<책>/state.json`,
  `work/<책>/toc_data.json` 사본, `work/<책>/_delivery/<책>_sections.zip`
  - `sections_out/`에는 `.txt`(헤더 파싱·진행 트래커용 내부 상태)와 `.pdf`가 함께 생기지만,
    사용자에게 전달되는 `_delivery/*.zip`에는 **`.pdf`만** 담긴다 — 헤더는 이제 `next_section.py`가
    이전 소단원의 `chapter_raw/*.json`을 읽어 동적으로 만들어 주므로 `.txt`를 사용자가 볼 필요가 없다.
- 로그 끝의 `⚠️⚠️⚠️` 경고(페이지 범위 누락 등)는 그대로 사용자에게 공지합니다.

## 프로그램 2 — 추적/수집/병합기

```
python pipeline/program2_track.py [--book <책>] next                       # 다음 만들 소단원 안내
python pipeline/program2_track.py [--book <책>] submit --json <완성.json>   # 검증·저장·진행갱신 (재업로드도 이걸로)
python pipeline/program2_track.py [--book <책>] submit --json <파일> --seq <NNN>          # 매칭 모호할 때 행 지정
python pipeline/program2_track.py [--book <책>] undo --seq <NNN>           # 미완료로 되돌리고 결과 JSON 삭제
python pipeline/program2_track.py [--book <책>] status                     # 챕터별 진행 현황
python pipeline/program2_track.py [--book <책>] merge --chapter <N>        # 강제 병합(보통은 자동)
```

동작 규칙:
- **검증**은 `section_merger.html` 규칙을 이식한 `lib/merger.py`로 수행. `오류(✗)`가 있으면 저장하지 않고 공지만 합니다.
- **매칭**: 업로드 파일명이 변형돼도 되도록, JSON 안의 `section_title` 내용으로 progress 행을 찾습니다(파일명·`--seq`는 보조).
- **순서 경고**: 위 「순서 보장」 참고.
- **챕터 완료 = 소단원 전부**. 별도의 최종복습 JSON은 없다 — 완료되면 소단원들을 이어붙여 그대로
  `data/<slug>.json`으로 쓰고 `data/manifest.json`(book 필드 포함)을 갱신합니다.
- 출력의 `[[MERGED ...]]` 마커는 Claude가 다음 조치(커밋·전송)를 판단하는 신호입니다.

## 구성

| 파일 | 역할 |
|---|---|
| `program1_split.py` | 분할 진입점 (pdf_splitter + make_tracker 래핑, 책별 폴더 자동 분리, zip 전송) |
| `program2_track.py` | 추적/수집/검증/병합 진입점 (책별 폴더 선택, 순서 경고) |
| `lib/pdf_splitter.py` | PDF → 소단원 조각 (원본 그대로) |
| `lib/make_tracker.py` | 조각 → `progress.md` (원본 그대로) |
| `lib/next_section.py` | 다음 작업·헤더 안내, 진행 체크 (원본 그대로) |
| `lib/merger.py` | `section_merger.html`의 검증·병합을 Python으로 이식 |
| `section_merger.html` | 수동 병합용 브라우저 도구(원본 보존 + manifest에 `book` 필드 추가) |
| `prompts/toc_classify_prompt.md` | 목차 이미지 → `toc_data.json` 분류용 (외부 AI에 붙여넣는 사용자용 자료) |
| `prompts/system_prompt.md` | 소단원 PDF → 학습용 JSON 변환용 (외부 AI에 붙여넣는 사용자용 자료) |

의존성: `pymupdf` (`pip install pymupdf`).
