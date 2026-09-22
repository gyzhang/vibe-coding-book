#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三形态正反探针：验证一次文本改动真的在「四面」落地。

为什么需要它（教训，2026-09-19 / 09-21 两次踩到）
------------------------------------------------------------------
改完稿判断"落地了没有"，不能靠一次 grep，也不能靠一次朴素的 `in` 判断：

1. **同一句话在四种物理形态里长得不同**——markdown 的 `**` 变成 `<strong>`、
   实体被转义（`&quot;`）、表格的 `|` 消失、PDF 抽取把长行按页宽切开。
   朴素判断会把这些全报成"未落地"。
2. **归一化本身会删掉证据**（代价最大的一次）：PDF 正文里**合法**出现了 `<`
   （表格格「2 天人工 + `<50 元 token`」），与后面的 `>` 配成一对"假标签"，
   一句 `re.sub(r'<[^>]+>','',pdf_text)` **一口气吞掉 61,707 个字符**，
   四条正向探针全部报 0——看起来像"没落地"，其实三处全在。
   ⇒ 所以**归一化必须按形态分开**：HTML 才剥标签；md 与 PDF 只去 `**`、反引号、空白。
   ⇒ 并且**必须把归一化前后的长度差打出来**：差了几万字符，就是证据被删掉了。
3. **反向探针（旧文本应消失）的串必须够独特**（≥ 12 字、取旧文本的完整片段）。
   用 6 字通用短语复核过一次，报出"残留"，回查是 `07-ch7.md` 里另一处合法文本。

用法
------------------------------------------------------------------
    cd <书目录>
    # 先跑完交付链（PDF 已生成），一次覆盖四面
    python3 scripts/probe_text.py . \
        --new "补回来的方式只有一种" \
        --new "我知道它在哪儿不管用" \
        --old "你会有一段“感觉落后”的时期" \
        --old "的时期，这是正常的"

    --new      正向：该片段必须出现在**每一个**面里，缺一面即报错
    --old      反向：该片段必须在**每一个**面里都消失，出现即报错
    --skip-pdf 跳过 PDF 面（PDF 还没重新生成时用）
    --quiet-diff  不打印归一化长度差

退出码：全部通过 0；任一正向缺失或反向命中 1。
"""

import argparse
import glob
import os
import re
import sys

ZERO_WIDTH = "\u200b\u00ad\ufeff\u2060"


def norm(text, kind):
    """归一化。kind: 'html' | 'plain'（见模块 docstring 教训 2）"""
    if kind == "html":
        text = re.sub(r"<[^>]+>", "", text)
        for a, b in (
            ("&quot;", '"'), ("&#39;", "'"), ("&lt;", "<"),
            ("&gt;", ">"), ("&nbsp;", " "), ("&amp;", "&"),
        ):
            text = text.replace(a, b)
    else:
        text = text.replace("**", "").replace("`", "")
    text = text.translate({ord(c): None for c in ZERO_WIDTH})
    return re.sub(r"\s+", "", text)


def load_pdf(path):
    import pymupdf  # 懒导入：只有用到 PDF 面才需要它

    doc = pymupdf.open(path)
    pages = []
    for page in doc:
        lines = page.get_text().split("\n")
        # 去掉页脚那行的页码（纯数字）。理由（2026-09-22 实测）：
        # 页码夹在两页之间，会把**跨页的探针串**从中间切开——
        # 实测"据 Stack Overflow 2025 年的开发者调查"落在页 15→16 的分页处，
        # 拼接后变成 `据Stack14Overflow2025年…`，探针报"未落地"，实际内容完好。
        # 只去掉**每页最后一行且是纯数字**的那一行，避免误删正文里的数字行。
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and re.fullmatch(r"\d{1,4}", lines[-1].strip()):
            lines.pop()
        pages.append("\n".join(lines))
    return "\n".join(pages)


def discover(root):
    """发现三形态产物与合稿 md。

    ⚠️ 判据必须是「**与 HTML 同名**」，不能是"按前缀排除"。
    实测踩过（2026-09-22 本脚本首次运行）：靠排除 `00-`/`99-` 前缀去 glob 根目录的
    `*.md`，**第一个命中的是 `README.md`** —— 于是四个正向探针全部报"未落地"，
    看上去像"改稿没落地"，其实是**探针读错了文件**。
    报"未落地"之前，先证明不是自己取错了对象。
    """
    htmls = sorted(glob.glob(os.path.join(root, "*.html")))
    if htmls:
        stem = os.path.splitext(os.path.basename(htmls[0]))[0]
        md = os.path.join(root, stem + ".md")
        pdf = os.path.join(root, stem + ".pdf")
        return (
            md if os.path.exists(md) else None,
            htmls[0],
            pdf if os.path.exists(pdf) else None,
        )

    # 退化路径：没有 HTML 时按排除法找合稿 md（并明确告知，避免静默取错）
    known = ("00-", "99-", "README.md", "overview.md")
    md = next(
        (p for p in sorted(glob.glob(os.path.join(root, "*.md")))
         if not os.path.basename(p).startswith(known)),
        None,
    )
    print("⚠️  根目录没有 HTML，合稿 md 退回『排除法』识别 → %s（请核对是不是它）"
          % (os.path.basename(md) if md else "未找到"))
    pdfs = sorted(glob.glob(os.path.join(root, "*.pdf")))
    return md, None, (pdfs[0] if pdfs else None)


def main():
    ap = argparse.ArgumentParser(description="三形态正反探针")
    ap.add_argument("root", help="书目录")
    ap.add_argument("--new", action="append", default=[], help="正向探针（可重复）")
    ap.add_argument("--old", action="append", default=[], help="反向探针（可重复）")
    ap.add_argument("--skip-pdf", action="store_true", help="跳过 PDF 面")
    ap.add_argument("--quiet-diff", action="store_true", help="不打印归一化长度差")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not args.new and not args.old:
        ap.error("至少给一个 --new 或 --old")

    # ---- 组装四个面 ----
    faces = []  # (显示名, 归一化文本, 原始长度, kind)

    parts = sorted(glob.glob(os.path.join(root, "parts", "*.md")))
    if parts:
        raw = "\n".join(open(p, encoding="utf-8").read() for p in parts)
        faces.append(("parts/（源分片 %d 个）" % len(parts), norm(raw, "plain"), len(raw)))

    book_md, html, pdf = discover(root)

    if book_md:
        raw = open(book_md, encoding="utf-8").read()
        faces.append(("合稿 md（%s）" % os.path.basename(book_md), norm(raw, "plain"), len(raw)))

    if html:
        raw = open(html, encoding="utf-8").read()
        faces.append(("HTML（%s）" % os.path.basename(html), norm(raw, "html"), len(raw)))

    if pdf and not args.skip_pdf:
        raw = load_pdf(pdf)
        faces.append(("PDF（%s）" % os.path.basename(pdf), norm(raw, "plain"), len(raw)))

    if not faces:
        sys.exit("找不到任何面（parts/ 与三形态产物都不存在？）")

    print("探针报告 · %s" % root)
    print("─" * 72)
    for name, normalized, raw_len in faces:
        diff = "" if args.quiet_diff else "（归一化删去 %s 字符）" % format(raw_len - len(normalized), ",")
        print("  %-38s 原始 %9s %s" % (name, format(raw_len, ","), diff))
    print("─" * 72)

    failures = []

    if args.new:
        print("正向探针（必须在全部 %d 个面里找到）" % len(faces))
        for probe in args.new:
            pn = norm(probe, "plain")
            marks = " ".join("✅" if pn in t else "❌" for _, t, _ in faces)
            print("  %s  %s" % (marks, probe[:46]))
            if "❌" in marks:
                missing = [n for (n, t, _), m in zip(faces, marks.split()) if m == "❌"]
                failures.append("正向缺失：%r 不在 %s" % (probe, "、".join(missing)))

    if args.old:
        print("反向探针（必须在全部 %d 个面里消失）" % len(faces))
        for probe in args.old:
            pn = norm(probe, "plain")
            marks = " ".join("✅" if pn not in t else "❌" for _, t, _ in faces)
            print("  %s  %s" % (marks, probe[:46]))
            if "❌" in marks:
                present = [n for (n, t, _), m in zip(faces, marks.split()) if m == "❌"]
                failures.append("反向残留：%r 仍在 %s" % (probe, "、".join(present)))

    print("─" * 72)
    if failures:
        print("结论：❌ %d 项未通过" % len(failures))
        for f in failures:
            print("  · %s" % f)
        print("\n提示：报『未落地』之前先看上方长度差——差了几万字符，就是归一化把证据删掉了。")
        return 1

    print("结论：✅ 全部通过（正向 %d 条 · 反向 %d 条 × %d 个面）"
          % (len(args.new), len(args.old), len(faces)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
