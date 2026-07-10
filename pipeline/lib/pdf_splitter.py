# -*- coding: utf-8 -*-
"""
전공서 PDF 분할기 (단순 버전) — system_prompt.md 연동

역할 분담
  · AI (toc_classify_prompt.md로 별도 실행): 목차 이미지만 보고 번호/제목/챕터/역할/인쇄쪽을 분류 → toc_data.json
  · 사람: 인쇄쪽 → 실제 PDF쪽 오프셋을 손으로 확인해서 숫자로 입력
  · 이 코드: toc_data.json + 오프셋 + 원본 PDF만으로 나머지를 전부 기계적으로 처리
      - 실제 PDF 페이지 범위 계산 (printed_page + offset)
      - 해당 범위 본문 텍스트 추출
      - [문서 맥락] 헤더 자동 생성(책/저자/챕터/현재 소단원/직전 소단원)
      - role="drop"(참고문헌·색인)은 파일로 안 만들되, 경계 계산에는 그대로 사용
      - 너무 큰 소단원은 결과(정리/정의) 헤딩 경계에서 자동 소분할

설치: pip install pymupdf
"""

import os, re, json, shutil
import fitz  # PyMuPDF

# ============================ 설정 ============================
TARGET_PDF = "Lectures on Polytopes .pdf"
TOC_JSON   = "toc_data.json"     # AI가 만든 분류 결과
OUTPUT_DIR = "sections_out"

# ---- 오프셋 (직접 확인해서 입력) ----
# PDF 뷰어에서 아무 페이지나 열어 "그 페이지에 인쇄된 책 쪽수"를 확인하면:
#   MANUAL_OFFSET = (그 PDF 페이지 번호, 1-based) - (그 페이지에 인쇄된 책 쪽수)
MANUAL_OFFSET = 0

# 특정 소단원만 오프셋이 다르면(예: 책 후반부에 페이지 리셋이 있는 경우) 여기 번호별로 덮어쓴다.
# 비워두면 전부 MANUAL_OFFSET 하나로 계산한다.
OFFSET_OVERRIDES = {
    # "App.A": 13,
}

EMIT_TXT = True     # 헤더+본문 텍스트(.txt) — 시스템 프롬프트에 붙여넣기용
EMIT_PDF = True     # 분할 PDF도 함께 저장 — 수식 많은 책은 멀티모달 모델에 PDF째 투입 권장

MAX_TOKENS_PER_CHUNK = 6000   # 초과 시 결과(정리/정의) 헤딩 경계에서 소분할

# 챕터 시작 ~ 첫 소단원 시작 사이의 '챕터 도입부' 처리 기준.
# 이 페이지 수 이하면 첫 소단원에 자동 편입, 초과하면 별도 "개관" 조각으로 분리.
INTRO_MERGE_THRESHOLD_PAGES = 4

RESULT_HEAD = re.compile(
    r'^\s{0,6}(Theorem|Definition|Lemma|Proposition|Corollary|Example|Remark|Notation)\b'
    r'|^\s{0,6}(정리|정의|보조정리|명제|따름정리|예제|참고)\b', re.I)

BODY_TAG = {"section": "[본문]", "front": "[본문]", "appendix": "[본문]", "intro": "[본문]", "exercises": "[연습문제]"}


def est_tokens(s):
    return int(len(s) / 3.3)

def norm(s):
    return " ".join(str(s).split())

def slug(s, n=26):
    s = re.sub(r'[^0-9A-Za-z가-힣 .\-]', '', norm(s)).replace(' ', '')
    return (s[:n] or 'x')

def clean_num(s):
    # 소단원 번호(예: "0.intro", "6.1.a")의 마침표를 언더스코어로 바꾼다.
    # 이유: 시스템 프롬프트를 실행하는 파일 생성 샌드박스가 파일명 중간의 "."을 확장자로 착각해
    # 자동으로 "_"로 정규화해 버린다(우리가 통제할 수 없는 동작). 그래서 애초에 우리 쪽 파일명도
    # "."을 안 쓰고 "_"로 통일해야, next_section.py가 알려주는 파일명과 실제 생성되는 파일명이 어긋나지 않는다.
    s = str(s).replace(".", "_")
    return "".join(c for c in s if c.isalnum() or c in ("_", "-")) or "sec"


# ===================== 1) toc_data.json 로드 + 오프셋 적용 =====================
def _apply_offset(number, printed_page):
    off = OFFSET_OVERRIDES.get(number, MANUAL_OFFSET)
    return max(1, int(printed_page) + int(off))

def load_plan(toc_path):
    with open(toc_path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("sections", [])
    for e in entries:
        if "number" not in e or "printed_page" not in e:
            raise ValueError(f"toc_data.json 항목에 number/printed_page가 필요합니다: {e}")
        e["start"] = _apply_offset(e["number"], e["printed_page"])
        e.setdefault("role", "section")
        e.setdefault("chapter_no", "")
        e.setdefault("chapter_title", "")
        e.setdefault("title", "")

    chapters = data.get("chapters", [])
    for c in chapters:
        c["start"] = _apply_offset(f"ch:{c.get('chapter_no','')}", c["printed_page"])

    # groups: 대단원(chapters)과 최종 생성 단위(sections) 사이에 낀 임의 깊이의 중간 단원
    # (예: 소단원 밑에 세부단원까지 쪼갤 때, 그 소단원 자신의 시작 쪽) — 3중 이상 구조에서만 쓰인다.
    groups = data.get("groups", [])
    for g in groups:
        g["start"] = _apply_offset(f"grp:{g.get('number','')}", g["printed_page"])
        g.setdefault("chapter_no", "")
        g.setdefault("chapter_title", "")
        g.setdefault("title", "")

    entries = apply_group_intros(entries, chapters, groups)
    entries.sort(key=lambda x: x["start"])
    return data.get("book", ""), data.get("author", ""), entries


def apply_group_intros(entries, chapters, groups):
    """'상위 단원 시작 ~ 그 안의 가장 이른 생성 단위 시작' 사이의 '도입부'를 처리한다.
    - 간격이 짧으면(INTRO_MERGE_THRESHOLD_PAGES 이하) 그 생성 단위의 시작을 상위 단원 시작까지
      당겨서 자동 편입.
    - 간격이 길면 별도의 role="intro" 조각을 만들어 끼워 넣는다.

    chapters(대단원)뿐 아니라 groups(그 사이 임의 깊이의 중간 단원)까지 같은 로직으로 처리해,
    (대단원 > 소단원 > 세부단원) 같은 3중 이상 구조에서도 각 층의 도입부를 전부 잡아낸다.
    번호에 점이 많을수록(더 깊을수록) 먼저 처리한다 — 그래야 세부단원 하나가 먼저 자기 소단원
    시작까지 당겨진 뒤, 그 소단원이 다시 챕터 시작까지 당겨지는 식으로 안쪽→바깥쪽 순서로
    누적 병합된다(반대 순서면 바깥쪽에서 이미 당겨진 시작점을 안쪽이 다시 잘못 계산하게 된다)."""
    GENERATION_ROLES = ("section", "exercises", "appendix")

    containers = []
    for c in chapters:
        no = c.get("chapter_no", "")
        if not no:
            continue
        containers.append({
            "label": no, "depth": 0, "start": c["start"], "printed_page": c["printed_page"],
            "chapter_no": no, "chapter_title": c.get("chapter_title", ""),
            "match": (lambda e, no=no: e["chapter_no"] == no),
        })
    for g in groups:
        num = g.get("number", "")
        if not num:
            continue
        containers.append({
            "label": num, "depth": num.count("."), "start": g["start"], "printed_page": g["printed_page"],
            "chapter_no": g.get("chapter_no", ""), "chapter_title": g.get("chapter_title", ""),
            "match": (lambda e, num=num: e["number"].startswith(num + ".")),
        })
    containers.sort(key=lambda x: -x["depth"])  # 가장 깊은 단원부터

    extra = []
    for cont in containers:
        members = [e for e in entries if e["role"] in GENERATION_ROLES and cont["match"](e)]
        if not members:
            continue
        first = min(members, key=lambda x: x["start"])
        gap_pages = first["start"] - cont["start"]
        if gap_pages <= 0:
            continue  # 상위 단원 시작이 첫 생성 단위와 같거나 늦음 → 도입부 없음
        if gap_pages <= INTRO_MERGE_THRESHOLD_PAGES:
            print(f"  · {cont['label']} 도입부 {gap_pages}쪽 → 첫 하위 단위({first['number']})에 자동 편입")
            first["start"] = cont["start"]
        else:
            print(f"  · {cont['label']} 도입부 {gap_pages}쪽 → 별도 '개관' 조각으로 분리")
            extra.append({
                "number": f"{cont['label']}.intro", "title": "개관 (도입부)",
                "chapter_no": cont["chapter_no"], "chapter_title": cont["chapter_title"],
                "printed_page": cont["printed_page"], "start": cont["start"], "role": "intro",
            })
    return entries + extra



# ===================== 2) 헤더/소분할 =====================
def context_block(book, author, ch_no, ch_title, number, title, prev):
    L = ["[문서 맥락]", f"책: {book}" + (f" / 저자: {author}" if author else "")]
    ch = (f"{ch_no}. {ch_title}".strip() if ch_title else (ch_no or "")).strip()
    if ch:
        L.append(f"챕터: {ch}")
    L.append(f"현재 소단원: {number} {title}".strip())
    if prev:
        L.append(f"직전 소단원: 「{prev}」")
    return "\n".join(L)

def split_pages_oversized(pages):
    """pages: [(page_index, page_text), ...] (해당 소단원의 전체 페이지, 0-based 문서 인덱스).
    반환: [{"text":合친 텍스트, "start":시작페이지, "end":끝페이지(포함)}], 페이지 경계에서만 자른다
    (텍스트 청크와 PDF 청크가 항상 정확히 같은 페이지 범위를 갖도록 보장하기 위함).
    가능하면 결과(정리/정의) 헤딩으로 시작하는 페이지에서 자르고, 없으면 그냥 예산 초과 시점의
    페이지 경계에서 자른다(문단 등 텍스트 내부 경계는 보지 않는다 — PDF는 페이지 단위로만 자를 수 있어서)."""
    total_tok = sum(est_tokens(t) for _, t in pages)
    if total_tok <= MAX_TOKENS_PER_CHUNK or len(pages) <= 1:
        return [{"text": "\n".join(t for _, t in pages), "start": pages[0][0], "end": pages[-1][0]}] if pages else []

    chunks, cur, tok = [], [], 0
    for idx, (p, text) in enumerate(pages):
        starts_with_heading = bool(RESULT_HEAD.match(text.lstrip().splitlines()[0])) if text.strip() else False
        if cur and tok > MAX_TOKENS_PER_CHUNK * 0.85 and (starts_with_heading or tok > MAX_TOKENS_PER_CHUNK):
            chunks.append({"text": "\n".join(t for _, t in cur), "start": cur[0][0], "end": cur[-1][0]})
            cur, tok = [], 0
        cur.append((p, text)); tok += est_tokens(text)
    if cur:
        chunks.append({"text": "\n".join(t for _, t in cur), "start": cur[0][0], "end": cur[-1][0]})
    return chunks

def stem_for(seq, book, number, title, part=None, is_ex=False):
    # seq: 실제 페이지 순서(0부터). 파일명 맨 앞에 3자리로 고정해 어떤 라벨("Front", "0.ex" 등)이 와도
    # 알파벳 정렬이 실제 순서와 항상 일치하게 만든다. book/number/title은 식별용으로 뒤에 붙인다.
    st = f"{seq:03d}__{slug(book, 14)}__{clean_num(number)}_{slug(title)}"
    if part:
        st += f"__p{part}"
    if is_ex:
        st += "__EX"
    return st


# ===================== 3) 실행 =====================
def run(pdf_path=None, toc_path=None, out_dir=None):
    pdf_path = pdf_path or TARGET_PDF
    toc_path = toc_path or TOC_JSON
    out_dir = out_dir or OUTPUT_DIR

    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    book, author, entries = load_plan(toc_path)
    doc = fitz.open(pdf_path)
    if not book:
        book = os.path.splitext(os.path.basename(pdf_path))[0].strip()

    kept = dropped = warned = 0
    prev_label = None
    file_seq = 0   # 실제로 파일을 쓸 때만 증가 — 분할된 파트도 서로 다른 번호를 받는다(공유 금지).
    for i, e in enumerate(entries):
        a = max(0, e["start"] - 1)                                          # 0-based 시작
        # 다음 항목의 시작 페이지까지 1쪽 겹치게 포함한다(다음 소단원이 페이지 중간에서 시작해
        # 이번 소단원의 꼬리 내용이 다음 페이지로 넘어가 있는 경우 손실을 막기 위함).
        b = entries[i + 1]["start"] if i + 1 < len(entries) else doc.page_count
        a, b = min(a, doc.page_count), min(max(b, a + 1), doc.page_count)

        if e["role"] == "drop":
            dropped += 1
            print(f"  – 제외: {e['number']} {e['title']}  (PDF {a+1}~{b}쪽, 참고문헌/색인)")
            continue

        pages = [(p, doc.load_page(p).get_text()) for p in range(a, b)]
        if not pages:
            # 시작 페이지가 실제 PDF 전체 쪽수를 넘어섰다는 뜻(오프셋 실수, toc_data.json 오류,
            # 혹은 원본 PDF가 예상보다 짧은 경우). 조용히 "저장 완료"로 넘기면 나중에야 파일이
            # 통째로 없다는 걸 알아채게 되므로, 여기서 확실하게 경고하고 건너뛴다.
            warned += 1
            print(f"  ⚠️ 건너뜀(페이지 범위 없음): {e['number']} {e['title']}  "
                 f"(계산된 시작쪽 {a+1} > 문서 전체 {doc.page_count}쪽 — 오프셋/toc_data.json 확인 필요)")
            continue

        chunks = split_pages_oversized(pages)
        if not chunks:
            warned += 1
            print(f"  ⚠️ 건너뜀(분할 결과 없음, 내부 로직 이상): {e['number']} {e['title']} — 코드 점검 필요")
            continue
        is_ex = (e["role"] == "exercises")

        for pi, ch in enumerate(chunks, 1):
            part = pi if len(chunks) > 1 else None
            stem = stem_for(file_seq, book, e["number"], e["title"], part, is_ex)
            file_seq += 1
            ctx = context_block(book, author, e["chapter_no"], e["chapter_title"],
                                e["number"], e["title"], prev_label)
            if part:
                ctx = ctx.replace(f"현재 소단원: {e['number']} {e['title']}".strip(),
                                  f"현재 소단원: {e['number']} {e['title']} (분할 part {part})".strip())
            if EMIT_TXT:
                tag = BODY_TAG.get(e["role"], "[본문]")
                with open(os.path.join(out_dir, stem + ".txt"), "w", encoding="utf-8") as f:
                    f.write(ctx + "\n" + tag + "\n" + ch["text"].strip() + "\n")
            if EMIT_PDF:
                nd = fitz.open()
                nd.insert_pdf(doc, from_page=ch["start"], to_page=ch["end"])
                nd.save(os.path.join(out_dir, stem + ".pdf"))
                nd.close()

        flag = f" [{len(chunks)}분할]" if len(chunks) > 1 else ""
        note = " (연습문제)" if is_ex else (f" ({e['role']})" if e["role"] not in ("section",) else "")
        print(f"  + {e['number']} {e['title']}  (PDF {a+1}~{b}쪽){flag}{note}")
        prev_label = f"{e['number']} {e['title']}".strip()
        kept += 1

    doc.close()
    print(f"\n완료: {kept}개 저장, {dropped}개 제외(참고문헌/색인) → '{out_dir}/'")
    if warned:
        print(f"⚠️⚠️⚠️ 경고: {warned}개 항목이 페이지 범위 문제로 완전히 누락됐습니다! 위 로그에서 '⚠️ 건너뜀'을 찾아 확인하세요.")
    return out_dir


if __name__ == "__main__":
    out = run()
    try:
        from google.colab import files
        zip_path = shutil.make_archive("sections_split", "zip", out)
        files.download(zip_path)
    except Exception:
        pass
