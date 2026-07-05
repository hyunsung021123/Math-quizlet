# 목차 이미지 → 분류 데이터(JSON) 생성 프롬프트

> **사용법**
> 1. 아래 「프롬프트 시작~끝」을 복사해 대화창 AI(Opus 4.8 등)에 붙여넣는다.
> 2. **목차 이미지**만 첨부한다(전체 PDF는 필요 없음 — AI는 분류만 한다).
> 3. (선택) 그 책 목차만의 특이한 구조·규칙을 알고 있으면, 프롬프트 맨 끝에 「요구사항」 블록을 자유 형식으로 붙인다(아래 참고). 번호 없는 특수 항목의 성격만 지정하고 싶으면 더 간단한 「수동 지정」 블록을 대신 써도 된다.
> 4. 출력된 JSON을 `toc_data.json`으로 저장한다.
> 5. `pdf_splitter.py`에 이 파일 + 오프셋 숫자 + 원본 PDF를 넣고 실행한다.

---8<--- 프롬프트 시작 ---8<---

첨부된 전공서 목차 이미지를 읽고, 아래 JSON 형식으로만 응답해라. 다른 말은 절대 추가하지 마라.

**임무는 순수 분류·전사다. 페이지 번호 계산이나 추정은 하지 마라 — 목차에 인쇄된 숫자를 그대로 옮기기만 하면 된다.**

## 규칙
1. 번호가 붙은 모든 소단원(예: 1.1, 1.2, 7.3)을 목차에 나온 순서 그대로 뽑는다.
2. **모든 항목에 예외 없이 챕터 번호·제목을 붙인다.** `chapter_no`/`chapter_title`을 빈 문자열로 남기지 마라. 부록·참고문헌처럼 특정 소단원 번호가 없는 항목은 **가장 마지막 챕터**의 번호·제목을 붙인다.
3. 각 항목의 역할을 `role`로 분류한다:
   - `"section"` — 일반 소단원
   - `"exercises"` — 그 장의 연습문제/문제
   - `"appendix"` — 부록
   - `"drop"` — 그 장의 결과 목록·역사적 참고("Notes"), 색인(Index), 참고문헌(References/Bibliography). 실제 학습 내용이 아니라서 생성 대상은 아니지만, **다음 항목과의 경계를 정확히 자르기 위해 페이지 위치는 반드시 기록**해야 한다.
4. **서문류는 아예 목록에 넣지 마라.** Preface, Preface to the Second/Seventh Printing, Foreword, Acknowledgments, Dedication처럼 저자가 쓴 책 서두의 글은 수학 학습 내용이 아니고, 책의 첫 챕터보다 물리적으로 앞에 있어서 경계 계산에도 필요 없다. `sections`에도 `chapters`에도 아예 등장시키지 마라 — 참고문헌·색인과 달리 이건 "role: drop"으로 적는 것도 아니고 그냥 통째로 무시한다.
5. **번호가 없어도 페이지가 매겨진 목차 항목은 절대 빠뜨리지 마라.** 소단원 번호(1.1, 7.3 등)가 없는데도 목차에 독립된 줄로, 자기 페이지 번호를 갖고 실려 있는 항목이 있으면 — 그 항목의 정확한 이름은 책마다 다르다("Notes", "Preliminaries", "Remarks", "Historical Remarks", "Summary", "Further Reading", "Background" 등 — 이 목록은 예시일 뿐, 이름으로 판단하지 마라 — **반드시 뽑아라.** 이걸 놓치면 앞뒤 경계가 사라져서, 소단원이 없는 챕터(서론 성격의 챕터 등)의 본문 전체나 챕터 끝의 서술형 내용이 다음 항목까지 통째로 뭉쳐 들어간다.
   - `number`는 `"<챕터번호>.<제목에서 뽑은 짧은 슬러그>"`로 만든다(예: `0.notes`, `3.remarks`, `5.prelim`).
   - `role`은 **내용의 성격**으로 판단한다(이름 매칭 아님):
     - 이후 소단원에서 실제로 쓰일 정의·배경지식·결과를 담고 있을 것 같으면(예: "Preliminaries", "Background", 그 자체로 수학적 내용) → `"section"`으로 취급해 정식으로 생성 대상에 포함시킨다.
     - 역사적 논평·출처 안내·요약·감사의 글처럼 부가적 성격이면(예: "Notes", "Remarks", "Historical Remarks", "Summary", "Further Reading") → `"drop"`으로 취급한다(생성 대상은 아니지만 경계 계산을 위해 페이지는 기록).
     - **판단이 애매하면 `"drop"`이 아니라 `"section"`으로 남겨라.** 수학적으로 유의미한 내용을 실수로 빼는 것이, 굳이 안 필요한 항목을 하나 더 생성하는 것보다 훨씬 나쁘다.
   - **입력에 「수동 지정」 블록이 있으면 위 자동 판단보다 그것을 항상 우선한다.** (아래 「수동 지정 (선택)」 참고)
   - "Exercises"/"Problems"류는 이름이 뭐든(Problems, Exercises, Problems and Exercises, Exercises for Chapter 3 …) 항상 `role: "exercises"`로 분류한다.
6. 목차에 인쇄된 페이지 번호를 `printed_page`에 정수로 적는다.
7. **챕터(대단원) 자체의 시작 쪽번호도 따로 뽑는다.** 목차에는 보통 "Chapter 7 ... 195" 처럼 챕터 제목이 첫 소단원(7.1)과는 별도 줄, 별도 페이지 번호로 적혀 있다. 이걸 최상위 `chapters` 배열에 담는다. 챕터 제목 줄과 첫 소단원 줄의 페이지 번호가 다르면(=챕터 도입부가 존재하면) 반드시 챕터의 번호를 별도로 기록해야 한다. 목차에 챕터 자체의 페이지가 안 보이면(소단원과 한 줄로 합쳐져 있으면) 생략해도 된다.
8. 목차에 없는 내용은 지어내지 마라.
9. **입력에 「요구사항」 블록이 있으면, 위 규칙 1~8보다 그 지시를 우선한다** — 단 출력은 여전히 아래 「출력 형식」의 JSON 스키마(키 이름·구조)를 반드시 지켜야 한다. 세분화 단위를 늘리거나(예: 소단원보다 더 잘게), 특정 항목의 처리 방식을 바꾸는 지시라도, 그 결과는 `sections`/`chapters` 배열 안의 표준 필드(`number`, `title`, `chapter_no`, `chapter_title`, `printed_page`, `role`)로 표현한다.

## 출력 형식
```json
{
  "book": "책 제목",
  "author": "저자 (모르면 빈 문자열)",
  "chapters": [
    {"chapter_no": "0", "chapter_title": "Introduction and Examples", "printed_page": 1},
    {"chapter_no": "1", "chapter_title": "Polytopes, Polyhedra, and Cones", "printed_page": 27}
  ],
  "sections": [
    {"number": "0.notes", "title": "Notes", "chapter_no": "0", "chapter_title": "Introduction and Examples", "printed_page": 22, "role": "drop"},
    {"number": "0.ex", "title": "Problems and Exercises", "chapter_no": "0", "chapter_title": "Introduction and Examples", "printed_page": 23, "role": "exercises"},
    {"number": "1.prelim", "title": "Preliminaries", "chapter_no": "1", "chapter_title": "Polytopes, Polyhedra, and Cones", "printed_page": 27, "role": "section"},
    {"number": "1.1", "title": "The \"Main Theorem\"", "chapter_no": "1", "chapter_title": "Polytopes, Polyhedra, and Cones", "printed_page": 29, "role": "section"},
    {"number": "1.6", "title": "Carathéodory's Theorem", "chapter_no": "1", "chapter_title": "Polytopes, Polyhedra, and Cones", "printed_page": 45, "role": "section"},
    {"number": "1.remarks", "title": "Historical Remarks", "chapter_no": "1", "chapter_title": "Polytopes, Polyhedra, and Cones", "printed_page": 47, "role": "drop"},
    {"number": "1.ex", "title": "Problems and Exercises", "chapter_no": "1", "chapter_title": "Polytopes, Polyhedra, and Cones", "printed_page": 49, "role": "exercises"}
  ]
}
```
(위 예시에는 "Preface" 계열이 아예 등장하지 않는다 — 규칙 4에 따라 통째로 뺐다. `0.notes`·`1.remarks`는 역사적 논평 성격이라 `role: "drop"`이지만, `1.prelim`("Preliminaries")은 실제 수학 배경지식을 담고 있을 가능성이 커서 `role: "section"`으로 남겼다 — **이름이 아니라 내용의 성격으로 판단한 것이다.** 이 책이 "Notes"라는 이름을 쓴다고 다른 책도 "Notes"를 쓴다고 가정하지 마라 — 그 책의 목차에 실제로 적힌 이름을 그대로 쓰고, 위 기준으로 role만 판단하면 된다.)

## 요구사항 (선택) — 그 책만의 특이한 목차 구조 설명하기
「수동 지정」이 "번호 없는 특정 항목 하나의 역할"만 지정하는 좁은 도구라면, 이건 **그 책 목차 전체의 구조적 특징이나 분류 방식 자체**를 자유 문장으로 설명하는 창구다. 목차 형식은 책마다 천차만별이라, 미리 정해둔 규칙(1~8)이 못 잡는 패턴을 만나면 여기에 적으면 된다. 프롬프트 끝에 아래처럼 붙인다:
```
[요구사항]
목차의 각 소단원 밑에 그 안의 정리(Theorem)·개념(Definition) 이름과 각각의 페이지 번호가 따로 나열되어 있다.
이 정보를 이용해서 소단원보다 더 잘게, 정리/개념 단위로 쪼개서 `sections`에 넣어줘.
번호는 "<소단원번호>.<순번>"으로 만들고(예: 7.1.1, 7.1.2), role은 "section"으로.
```
- 이 블록의 지시는 규칙 1~8보다 우선한다(규칙 9 참고). 다만 **출력은 여전히 정해진 JSON 스키마를 따라야 한다** — 세분화 단위를 얼마나 잘게 잡든, 결과는 항상 `sections` 배열 안의 `number`/`title`/`chapter_no`/`chapter_title`/`printed_page`/`role` 필드로 표현한다.
- 이 블록이 없으면(대부분의 경우) 규칙 1~8만으로 처리한다 — 예외 대비용 옵션이다.
- 「요구사항」과 「수동 지정」을 동시에 써도 된다. 순서 상관없이 둘 다 프롬프트 끝에 붙이면 된다.

## 수동 지정 (선택)
자동 판단(규칙 5)을 못 믿겠거나, 그 책의 특수한 사정을 이미 알고 있으면(예: "이 책의 Remarks에는 실제로 못 보던 정리가 하나 들어있다") 프롬프트 끝에 아래 블록을 붙여서 직접 지정할 수 있다. 이 블록이 있으면 **규칙 5의 자동 판단보다 항상 우선한다.**
```
[수동 지정]
- "Remarks" → section   (이 책에서는 정리를 포함하고 있어 학습 내용으로 취급)
- "Summary" → drop
- "Historical Notes" → drop
```
- 왼쪽 따옴표 안의 문자열은 목차에 인쇄된 제목과 **부분 일치**로 매칭한다(예: `"Remarks"`는 "Chapter 3 Remarks", "Concluding Remarks" 등에 모두 매칭).
- 오른쪽 값은 `section`(생성 대상 포함) 또는 `drop`(경계용으로만, 생성 제외) 중 하나만 쓴다. `exercises`/`appendix`처럼 이미 이름으로 명백히 구분되는 역할은 수동 지정 없이도 규칙대로 분류되니 굳이 지정하지 않아도 된다.
- 블록이 없으면(대부분의 경우) 규칙 5의 자동 판단을 그대로 쓴다 — 이 옵션은 어디까지나 예외 대비용이다.

---8<--- 프롬프트 끝 ---8<---
