#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 A4 PDF 注入书签（大纲）与文档元数据。

用法：
    python3 scripts/add_pdf_bookmarks.py <book.html> <in.pdf> [out.pdf]
    不传 out.pdf 时安全地原地替换（先写临时文件再原子替换）。
    python3 scripts/add_pdf_bookmarks.py <book.html> <in.pdf> --probe
    只校准、不写书签：打印字号分布与各级标题的实测字号（改过 CSS 后用它重校 BANDS）。

为什么需要这一步
----------------
Chrome 的 `--print-to-pdf` **不会**从 HTML 标题生成 PDF 大纲。一份 129 页、128 个标题的
书，没有书签就只能在在线阅读器里一页页翻——**PDF 不"只是打印版"，它往往就是在线阅读版。**

定位原理（不靠猜）
----------------
1. 从 HTML 取标题序列（文档顺序、层级、显示文本）——那是权威清单。
2. 在 PDF 里按**字号带**抓标题候选（见 BANDS，数值是实测的，不是估的）。
3. **排除封面页**——封面是独立 SVG，它的字号会落进标题带，制造假标题。
4. 两份清单**逐条比对归一化文本**；只有全部对上才落书签。
   **对不上就报错退出，不猜页码。**

BANDS 的来历（2026-09-20 实测，不是抄来的）
-----------------------------------------
打印态 CSS 里 `body{font-size:10.5pt}` **会覆盖读者的字号设置**，所以 em 的绝对值是定的：

| 元素 | CSS | 实测 |
|---|---|---|
| h1.h-part（篇 / 前言 / 附录） | 1.55em | **16.275 pt** ×13 |
| h1.h-chap（章）             | 1.72em | **18.060 pt** ×15 |
| h2.h-sec（节）              | 1.22em | **12.810 pt** ×90 |
| h3.h-sub（子节）            | 1.05em | **11.025 pt** ×10 |
| 正文 | — | 10.500 pt |
| 表格 / 代码 | — | 9.135 pt |
| **封面页**（被 COVER_PAGES 排除） | — | 17.287 / 19.950 pt |

⚠️ **本项目的 L1 带不能沿用同系列另一本书的 (15.5, 17.5)**：那本书的章标题不在这一档，
而本书的章标题是 **18.06pt，会整个掉出带外**。这就是"照抄参数"会翻车的地方。
改过 `build_html.py` 的打印样式后，**必须跑 `--probe` 重校**。
"""
from __future__ import annotations

import argparse
import collections
import html as H
import os
import re
import sys
import tempfile
from pathlib import Path

import pymupdf

# 字号带：(层级, 下界, 上界)。上界 18.5 是为了包住章标题 18.06，同时挡住 hero 的 19.95。
BANDS = [(1, 15.5, 18.5), (2, 12.70, 12.95), (3, 10.90, 11.15)]
COVER_PAGES = 1  # 封面页数，从第 1 页起（实测：第 1 页是封面，第 2 页起是正文）

METADATA = {
    "title": "Vibe Coding 时代的我们：AI 编码的能与不能",
    "author": "Kevin + AI",
    "subject": "AI 编码的能与不能：155 天、四个仓库、一次可核对的实验（五篇十五章 · 22 张配图 · 引用均可溯源）",
    "keywords": "Vibe Coding, AI 编码, 智能体, 工程判断力, 软件工程, harness, 上下文工程, 人机协作",
    "creator": "book-studio · md-to-html-book（Chrome 排版 + pymupdf 书签）",
}


def norm(s: str) -> str:
    """归一化：去掉全部空白。只用于比对，不用于显示。"""
    return re.sub(r"\s+", "", s)


def headings_from_html(path: Path) -> list[tuple[int, str]]:
    """按文档顺序取 <h1|h2|h3 id="...">：返回 (层级, 显示文本)。

    必须带 id —— 这条约束顺手排除了封面 hero 里那个没有 id 的 <h1>（19.95pt）。
    """
    src = path.read_text(encoding="utf-8")
    out: list[tuple[int, str]] = []
    for m in re.finditer(r'<h([1-3])[^>]*\bid="[^"]+"[^>]*>(.*?)</h\1>', src, re.S):
        level = int(m.group(1))
        text = H.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            out.append((level, text))
    return out


def pdf_lines(doc: pymupdf.Document) -> list[tuple[int, float, str]]:
    """全部文本行：(页码 1 起, 最大字号, 归一化文本)。"""
    out: list[tuple[int, float, str]] = []
    for pno, page in enumerate(doc, 1):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                size = max(s["size"] for s in spans)
                text = norm("".join(s["text"] for s in spans))
                if text:
                    out.append((pno, size, text))
    return out


def headings_from_pdf(lines: list[tuple[int, float, str]]) -> list[tuple[int, int, str]]:
    """按文档顺序取 PDF 里的标题候选：(层级, 页码, 文本)。"""
    out: list[tuple[int, int, str]] = []
    for pno, size, text in lines:
        if pno <= COVER_PAGES:
            continue
        for level, lo, hi in BANDS:
            if lo <= size <= hi:
                out.append((level, pno, text))
                break
    return out


def probe(html_path: Path, doc: pymupdf.Document) -> int:
    """校准模式：报告各级标题的实测字号，以及当前 BANDS 会抓出多少候选。"""
    expected = headings_from_html(html_path)
    lines = pdf_lines(doc)
    by_level = collections.Counter(l for l, _ in expected)
    print(f"HTML 标题（带 id 的 h1/h2/h3）：{len(expected)} 条"
          f"（h1 {by_level[1]} · h2 {by_level[2]} · h3 {by_level[3]}）")
    print(f"PDF：{doc.page_count} 页 · 文本行 {len(lines):,}")

    print("\n--- 全文字号分布（9–22pt）---")
    hist = collections.Counter(round(s, 3) for _, s, _ in lines if 9 <= s <= 22)
    for s, n in sorted(hist.items()):
        marks = [f"L{lvl}" for lvl, lo, hi in BANDS if lo <= s <= hi]
        tag = ("  ← 命中 " + "/".join(marks)) if marks else ""
        print(f"  {s:7.3f} pt : {n:6,d} 行{tag}")

    print("\n--- 用标题文本反查：各级在 PDF 里的实际字号 ---")
    for lvl in (1, 2, 3):
        counter: collections.Counter = collections.Counter()
        for l, t in expected:
            if l != lvl:
                continue
            counter.update(s for _, s, x in lines if x == norm(t))
        if counter:
            got = "；".join(f"{s}pt ×{n}" for s, n in counter.most_common())
            print(f"  h{lvl}：{got}")
        else:
            print(f"  h{lvl}：未匹配")

    cands = headings_from_pdf(lines)
    print(f"\n--- 当前 BANDS 抓出候选：{len(cands)} 条（应为 {len(expected)} 条）---")
    if len(cands) != len(expected):
        print("  ⚠️ 数量不等 → 调 BANDS 的上/下界，或看是不是封面溢出了第 1 页")
    return 0 if len(cands) == len(expected) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="给 A4 PDF 注入三层书签与元数据")
    ap.add_argument("html", type=Path, help="构建用的 HTML（标题清单的来源）")
    ap.add_argument("pdf", type=Path, help="输入 PDF")
    ap.add_argument("out", type=Path, nargs="?", help="输出 PDF（缺省 = 安全地原地替换）")
    ap.add_argument("--probe", action="store_true", help="只校准字号带，不写书签")
    a = ap.parse_args()

    if not a.html.is_file():
        print(f"ERROR 找不到 HTML：{a.html}", file=sys.stderr)
        return 2
    if not a.pdf.is_file():
        print(f"ERROR 找不到 PDF：{a.pdf}", file=sys.stderr)
        return 2

    doc = pymupdf.open(a.pdf)
    if a.probe:
        rc = probe(a.html, doc)
        doc.close()
        return rc

    expected = headings_from_html(a.html)
    actual = headings_from_pdf(pdf_lines(doc))
    print(f"HTML 标题 {len(expected)} 条 | PDF 抓到候选 {len(actual)} 条")

    # 逐条比对：层级与归一化文本都必须一致
    if len(expected) != len(actual):
        print(f"ERROR 条数不一致（HTML {len(expected)} / PDF {len(actual)}），拒绝写书签。")
        print("  请检查：封面页数、字号带、打印模板的字号是否被改过。")
        print("  重校命令：python3 scripts/add_pdf_bookmarks.py <html> <pdf> --probe")
        doc.close()
        return 1

    toc: list[list] = []
    bad: list[tuple[int, tuple[int, str], tuple[int, int, str]]] = []
    for i, ((el, et), (al, ap_, at)) in enumerate(zip(expected, actual)):
        if el != al or norm(et) != at:
            bad.append((i, (el, et), (al, ap_, at)))
        toc.append([el, et, ap_])

    if bad:
        print(f"ERROR {len(bad)} 条对不上，拒绝写书签：")
        for i, e, ac in bad[:10]:
            print(f"  #{i}\n    题目 {e}\n    实际 {ac}")
        doc.close()
        return 1

    print("✅ 全部逐条对上（层级 + 文本）")

    doc.set_toc(toc)
    doc.set_metadata(METADATA)

    # 不能直接 save 回原路径：pymupdf 对"保存到原件"要求增量保存，否则报错。
    # 一律先写临时文件，成功后再原子替换 —— 中途失败不会留下半份坏 PDF。
    target = a.out or a.pdf
    in_place = target.resolve() == a.pdf.resolve()
    tmp = Path(tempfile.mkstemp(prefix="bm-", suffix=".pdf", dir=str(a.pdf.parent))[1])
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, target)

    chk = pymupdf.open(target)
    got = chk.get_toc()
    depth = collections.Counter(lvl for lvl, _t, _p in got)
    print(f"✅ 书签已写入：{len(got)} 条（1 级 {depth[1]} / 2 级 {depth[2]} / 3 级 {depth[3]}）")
    print(f"   首页书签: {got[0] if got else '—'}")
    print(f"   末条书签: {got[-1] if got else '—'}")
    meta = chk.metadata
    print(f"   元数据: title={meta.get('title')!r}")
    print(f"           author={meta.get('author')!r}")
    print(f"   页数 {chk.page_count} | 体积 {target.stat().st_size/1024/1024:.1f} MB"
          f" -> {target.name}{'（原地替换）' if in_place else ''}")
    chk.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
