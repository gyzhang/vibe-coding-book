#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 A4 PDF 居中盖页码。

用法：
    python3 scripts/stamp_page_numbers.py <in.pdf> [out.pdf]
不传 out.pdf 时**原地安全替换**（先写临时文件再原子替换）——链路里三个脚本都作用于
同一个产物，不额外产生 .numbered.pdf。
注意：pymupdf 不允许 save 回原件（要求增量保存），所以必须走「临时文件 + 原子替换」。

⚠️ **本脚本不幂等，连跑两次会把页码叠上去**（2026-09-20 实测：第 2 次运行后，
119/129 页的页脚在同一坐标出现两份文字，选中复制会得到 `63 63`；因为完全重叠，
**肉眼看不出来**，只有抽取文本或改偏移才现形）。
处理办法有两条，已经都做了：
  1. **防呆**：盖之前先扫一遍页脚带，若发现已盖过 → **拒绝执行并报错**（见 already_stamped）。
  2. **正确姿势**：页码是链路的**最后一步**，每份 PDF 只盖一次；要重盖就先从
     `build_pdf.py` 重跑整条链（Chrome 重排 → 书签 → 页码）。

为什么需要这一步
----------------
Chrome 的 `--print-to-pdf` 配合 `--no-pdf-header-footer` 不生成页码。
页码是**事后盖**的：从封面之后的第 1 页起编号，封面不编号。

参数（与同系列成品保持一致，改动会破坏跨轮一致性）
改动前请先量：基线 y 必须落在**下边距之内**（@page margin 18mm = 51.0pt，
A4 高 841.92 → 版心下沿在 y≈790.9），否则页码会压到正文最后一行上。
----------------------------------------------
字体 Helvetica 9pt · 颜色 #738599（0.451, 0.522, 0.600）· 水平居中 ·
基线 y = 812.4（A4 高 841.92，底边距 18mm，落在版心之下的页脚带）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pymupdf

FONT = "helv"
SIZE = 9.0
BASELINE_Y = 812.4
COLOR = (0.451, 0.522, 0.600)  # #738599
COVER_PAGES = 1  # 封面页数，不编号
FOOTER_BAND = (795.0, 828.0)   # 页脚带的 y 区间（版心下沿 ≈790.9，之下才是页脚）
SIZE_TOL = 0.6                 # 字号容差，用于识别"这是不是我们自己盖的页码"


def already_stamped(doc: pymupdf.Document) -> int:
    """数一数有多少页的页脚已经存在"我们自己盖的页码"。

    判定三条件同时成立：纯数字 + 字号≈9pt + 落在页脚带。
    正文到不了这个高度（版心下沿 y≈790.9），所以命中即说明盖过。
    """
    hit = 0
    for i in range(COVER_PAGES, doc.page_count):
        for block in doc[i].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                if any(
                    s["text"].strip().isdigit()
                    and FOOTER_BAND[0] < s["bbox"][1] < FOOTER_BAND[1]
                    and abs(s["size"] - SIZE) < SIZE_TOL
                    for s in line.get("spans", [])
                ):
                    hit += 1
                    break
    return hit


def stamp(in_pdf: str, out_pdf: str, force: bool = False) -> int:
    doc = pymupdf.open(in_pdf)

    # 防呆：页码是链路的最后一步，重复盖章会在同一坐标叠字，而肉眼看不出来。
    hit = already_stamped(doc)
    if hit and not force:
        print(f"ERROR 这份 PDF 的 {hit} 页已经有页码了，拒绝重复盖章。", file=sys.stderr)
        print("  页码必须在链路里只盖一次（build_pdf → add_pdf_bookmarks → stamp_page_numbers）。", file=sys.stderr)
        print("  要重盖，请从 build_pdf.py 重跑整条链，而不是在成品上再盖一遍。", file=sys.stderr)
        print("  （确实要强制覆盖请加 --force，但叠加的文字会留在内容流里。）", file=sys.stderr)
        doc.close()
        return 1

    n = 0
    for i in range(COVER_PAGES, doc.page_count):
        page = doc[i]
        num = i + 1 - COVER_PAGES  # 封面之后重新起号
        text = str(num)
        w = pymupdf.get_text_length(text, fontname=FONT, fontsize=SIZE)
        x = (page.rect.width - w) / 2
        page.insert_text(
            (x, BASELINE_Y), text, fontname=FONT, fontsize=SIZE, color=COLOR
        )
        n += 1
    tmp = Path(tempfile.mkstemp(prefix="pn-", suffix=".pdf",
                                dir=str(Path(out_pdf).parent))[1])
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, out_pdf)
    print(f"✅ 页码已盖：{n} 页（跳过封面 {COVER_PAGES} 页）-> {out_pdf}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    src = Path(args[0])
    dst = Path(args[1]) if len(args) > 1 else src   # 缺省 = 原地安全替换
    return stamp(str(src), str(dst), force)


if __name__ == "__main__":
    sys.exit(main())
