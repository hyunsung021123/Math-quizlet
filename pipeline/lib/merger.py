# -*- coding: utf-8 -*-
"""
merger.py — section_merger.html 의 검증(validateItem)·병합(doMerge) 로직을 Python으로 이식한 것.

브라우저 도구는 사람이 JSON을 하나씩 붙여넣어 눈으로 확인·병합하는 용도였다.
프로그램 2가 세션 안에서 그 역할을 대신하려면 같은 검증·병합 규칙이 코드로 있어야 한다.
원본 HTML의 규칙(스키마 검증, KaTeX $ 짝 검사, viz 화이트리스트, 소단원 번호 정렬)을 그대로 옮겼다.

step4_final_review(챕터 단위 최종복습)는 더 이상 없다 — ox/conceptual 문제는 각 소단원 자신의
step3_checkpoint 안에 {"type": "checkpoint"|"ox"|"conceptual", ...} 항목으로 함께 들어있다.
그래서 챕터 병합은 소단원 JSON들의 learning_flow를 이어붙이기만 하면 끝난다(별도 review 단계 없음).

주요 함수:
  validate_item(parsed) -> {"kind":..., "msgs":[{"level","text"}]}
  worst_level(msgs) -> "err"|"warn"|"ok"
  merge(items) -> 병합된 챕터 JSON dict  (items: [{"kind","parsed"}...])
  slug_id(title) -> manifest용 파일 슬러그
"""

import re

VIZ_WHITELIST = ['zonotope_2d', 'hyperplane_arrangement_2d', 'convex_hull_2d',
                 'vectors_2d', 'custom_2d', 'identification_polygon_2d', 'complex_2d',
                 'torus_2d', 'cylinder_2d']
RESULT_TYPES = ['Definition', 'Theorem', 'Lemma', 'Proposition', 'Corollary', 'Remark', 'Example']
CHECKPOINT_TYPES = ('checkpoint', 'ox', 'conceptual')


def _msg(level, text):
    return {"level": level, "text": text}


def check_viz(viz, tag, msgs):
    if not viz:
        return
    if not isinstance(viz, dict) or not viz.get("type"):
        msgs.append(_msg("err", tag + ".visualization: type이 없습니다."))
        return
    if viz["type"] not in VIZ_WHITELIST:
        msgs.append(_msg("err", tag + '.visualization: 알 수 없는 type "' + str(viz["type"]) +
                         '" (지원: ' + ", ".join(VIZ_WHITELIST) + ")"))


def check_katex_balance(obj, tag, msgs):
    """문자열들을 순회하며 '$' 개수가 짝수인지(수식 델리미터 미종료 여부) 대략 점검."""
    bad = []

    def walk(o, path):
        if isinstance(o, str):
            n = o.count("$")
            if n % 2 != 0:
                bad.append(path)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + "[" + str(i) + "]")
        elif isinstance(o, dict):
            for k in o:
                walk(o[k], path + "." + k)

    walk(obj, tag)
    if bad:
        extra = (" 외 " + str(len(bad) - 3) + "개") if len(bad) > 3 else ""
        msgs.append(_msg("warn", tag + ': KaTeX "$" 개수가 홀수인 필드가 있습니다 → ' +
                         ", ".join(bad[:3]) + extra + " (수식이 깨질 수 있음, 원문 확인 권장)"))


def validate_item(parsed):
    """원본 HTML validateItem 이식. 반환 {"kind","msgs"}."""
    msgs = []

    has_flow = isinstance(parsed, dict) and parsed.get("learning_flow") is not None
    if not has_flow:
        msgs.append(_msg("err", "인식할 수 없는 형식: learning_flow가 없습니다."))
        return {"kind": "unknown", "msgs": msgs}

    # chapter_or_section
    flow = parsed.get("learning_flow")
    if not isinstance(flow, list) or not flow:
        msgs.append(_msg("err", "learning_flow가 비어 있거나 배열이 아닙니다."))
        return {"kind": "section", "msgs": msgs}
    if not parsed.get("chapter_info") or not parsed["chapter_info"].get("title"):
        msgs.append(_msg("warn", "chapter_info.title이 없습니다 (병합 시 다른 항목의 값을 사용합니다)."))

    for si, sec in enumerate(flow):
        tag = f"learning_flow[{si}] ({sec.get('section_title') or '제목없음'})"
        bp = sec.get("step1_big_picture")
        if not bp:
            msgs.append(_msg("err", tag + ": step1_big_picture가 없습니다."))
        else:
            if not bp.get("context"):
                msgs.append(_msg("warn", tag + ": step1 context가 비어 있습니다."))
            if not bp.get("hidden_intuition"):
                msgs.append(_msg("warn", tag + ": step1 hidden_intuition이 비어 있습니다(핵심 가치 손실)."))
            check_viz(bp.get("visualization"), tag + ".step1", msgs)
        blocks = sec.get("step2_rigorous_logic") or []
        if not blocks:
            msgs.append(_msg("warn", tag + ": step2_rigorous_logic이 비어 있습니다."))
        for bi, b in enumerate(blocks):
            btag = tag + f".step2[{bi}]"
            if b.get("type") not in RESULT_TYPES:
                msgs.append(_msg("warn", btag + f': type "{b.get("type")}"이 표준 목록에 없습니다.'))
            if not b.get("formal_statement"):
                msgs.append(_msg("err", btag + ": formal_statement가 없습니다."))
            has_idea = bool(b.get("idea_behind_proof"))
            has_steps = isinstance(b.get("proof_steps"), list) and len(b.get("proof_steps"))
            if b.get("type") in ("Theorem", "Lemma", "Proposition") and not (has_idea and has_steps):
                msgs.append(_msg("warn", btag + ": " + str(b.get("type")) +
                                 "인데 idea_behind_proof/proof_steps가 부실합니다(핵심 가치 손실 가능)."))
            check_viz(b.get("visualization"), btag, msgs)
        cps = sec.get("step3_checkpoint") or []
        if not cps:
            msgs.append(_msg("warn", tag + ": step3_checkpoint가 비어 있습니다."))
        n_ox = n_cq = 0
        for ci, c in enumerate(cps):
            ctag = tag + f".step3[{ci}]"
            raw_type = c.get("type")
            ctype = raw_type or "checkpoint"
            if ctype not in CHECKPOINT_TYPES:
                msgs.append(_msg("warn", ctag + f': type "{raw_type}"이 표준 목록(checkpoint/ox/conceptual)에 없습니다 — checkpoint로 취급합니다.'))
                ctype = "checkpoint"
            if ctype == "ox":
                n_ox += 1
                if not c.get("question"):
                    msgs.append(_msg("warn", ctag + ": question이 없습니다."))
                ans = str(c.get("answer") or "").strip().upper()
                if ans not in ("O", "X"):
                    msgs.append(_msg("err", ctag + f': answer가 "O"/"X"가 아닙니다: {c.get("answer")!r}'))
                if not c.get("explanation"):
                    msgs.append(_msg("warn", ctag + ": explanation이 없습니다."))
            elif ctype == "conceptual":
                n_cq += 1
                if not c.get("question"):
                    msgs.append(_msg("warn", ctag + ": question이 없습니다."))
                if not c.get("answer"):
                    msgs.append(_msg("warn", ctag + ": answer가 없습니다."))
            else:
                if not c.get("problem"):
                    msgs.append(_msg("err", ctag + ": problem이 없습니다."))
                if not c.get("solution"):
                    msgs.append(_msg("warn", ctag + ": solution이 없습니다."))
                check_viz(c.get("visualization"), ctag, msgs)
        if cps and n_ox == 0:
            msgs.append(_msg("warn", tag + ": ox 타입 항목이 하나도 없습니다(이 소단원의 복습이 빠졌을 수 있음)."))
        check_katex_balance(sec, tag, msgs)

    if parsed.get("step4_final_review"):
        msgs.append(_msg("warn", "step4_final_review 키는 더 이상 쓰지 않습니다 — ox/conceptual 항목은 각 섹션의 "
                         "step3_checkpoint 안에 type으로 넣어야 합니다. 이 필드는 무시되고 병합되지 않습니다."))

    if not any(m["level"] == "err" for m in msgs):
        titles = ", ".join(str(s.get("section_title")) for s in flow)
        msgs.append(_msg("ok", f"섹션 {len(flow)}개 인식됨: {titles}"))
    return {"kind": "section", "msgs": msgs}


def worst_level(msgs):
    if any(m["level"] == "err" for m in msgs):
        return "err"
    if any(m["level"] == "warn" for m in msgs):
        return "warn"
    return "ok"


# ---------- 병합 ----------
def _num_key(title):
    m = re.search(r'(\d+(?:\.\w+)*)', title or "")
    if not m:
        return [9999]
    out = []
    for p in m.group(1).split("."):
        if p == "intro":
            # 챕터/소단원 도입부("2.intro")는 형제 항목(2.1, 2.2, ...)보다 앞에 와야 하므로
            # 문자열로 두면 안 된다(문자열 vs 정수 비교에서 오히려 맨 뒤로 밀림).
            out.append(-1)
            continue
        try:
            out.append(int(p))
        except ValueError:
            out.append(p)
    return out


def _cmp_key(a, b):
    # 타입이 섞인(int vs str) 비교에서 파이썬 3가 TypeError를 내지 않도록,
    # 원본 JS의 <, > 비교 의미를 유지하되 타입이 다르면 문자열로 승격해 비교한다.
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x is None:
            return -1
        if y is None:
            return 1
        if type(x) is not type(y):
            x, y = str(x), str(y)
        if x < y:
            return -1
        if x > y:
            return 1
    return 0


def merge(items):
    """items: [{"kind": "section", "parsed": {...}}, ...] (worst_level=='err' 은 호출부가 미리 제외).
    반환: 병합된 챕터 JSON dict — chapter_info + learning_flow 뿐이다. 각 섹션의 복습(ox/conceptual)은
    이미 그 섹션의 step3_checkpoint 안에 들어있으므로 별도로 모을 것이 없다(step4_final_review 없음)."""
    import functools
    chapter_info = None
    flow = []
    for it in items:
        p = it["parsed"]
        if it["kind"] == "section":
            if not chapter_info and p.get("chapter_info"):
                chapter_info = p["chapter_info"]
            for s in (p.get("learning_flow") or []):
                flow.append(s)

    flow.sort(key=functools.cmp_to_key(
        lambda a, b: _cmp_key(_num_key(a.get("section_title")), _num_key(b.get("section_title")))))

    return {
        "chapter_info": chapter_info or {"title": "제목 미상 (직접 수정 필요)"},
        "learning_flow": flow,
    }


def slug_id(s):
    s = re.sub(r'[^a-z0-9가-힣]+', '_', str(s or "chapter").lower())
    s = re.sub(r'^_+|_+$', '', s)[:40]
    return s or "chapter"
