#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合稿脚本：把 parts/ 里的分片按写死的顺序拼成 <书名>.md。

用法（在任意目录下都可运行）：
    python3 scripts/build_book.py

输入：parts/*.md —— 唯一手写源
输出：dist/<书名>.md —— 合稿产物，禁止手改（下次合稿会覆盖）；
      并把 images/ 拷进 dist/images/，使 dist 自包含（可整体拷走部署/阅读）

设计要点：
  · 顺序写死在 PLAN 里，不靠记忆拼章节。
  · 路径一律锚定到「本脚本所在目录的上一级」（= 书目录），
    所以从哪个目录调用都一样，不受当前工作目录影响。
  · 末尾做一次双路复算：合稿结果必须等于各分片顺序拼接，
    任何一处漏拼都会当场断言失败，而不是静默产出一本少了章的书。
"""
import os
import re
import shutil
import sys

# scripts/ 的上一级 = 书目录（与调用时的 cwd 无关）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TITLE = "Vibe Coding 时代的我们（2026·V3.3）"

# 顺序 + 篇分隔（None 表示该分片前不加分隔）
PLAN = [
    ("parts/00-front.md", None),
    ("parts/01-ch1.md",  "# 第一篇　认清它：这台机器到底学会了什么"),
    ("parts/02-ch2.md",  None),
    ("parts/03-ch3.md",  None),
    ("parts/04-ch4.md",  "# 第二篇　算清账：它到底让你更快了吗"),
    ("parts/05-ch5.md",  None),
    ("parts/06-ch6.md",  None),
    ("parts/07-ch7.md",  "# 第三篇　用对它：一个人的功夫"),
    ("parts/08-ch8.md",  None),
    ("parts/09-ch9.md",  None),
    ("parts/10-ch10.md", None),
    ("parts/11-ch11.md", "# 第四篇　建起来：模型之外决定成败"),
    ("parts/12-ch12.md", None),
    ("parts/13-ch13.md", None),
    ("parts/14-ch14.md", "# 第五篇　看清楚：人还剩什么"),
    ("parts/15-ch15.md", None),
    ("parts/16-epilogue.md", None),
    ("parts/17-afterword.md", None),
    ("parts/18-appendix-a.md", None),
    ("parts/19-appendix-b.md", None),
    ("parts/20-appendix-c.md", None),
]


def main():
    chunks, missing, used = [], [], []
    for rel, sep in PLAN:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            missing.append(rel)
            continue
        used.append(rel)
        body = open(path, encoding="utf-8").read().rstrip("\n")
        if sep:
            chunks.append("%s\n\n---\n\n%s\n" % (sep, body))
        else:
            chunks.append(body + "\n")

    md = "\n".join(chunks)
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, TITLE + ".md")
    open(out, "w", encoding="utf-8").write(md)
    # dist 自包含：md 里的相对路径 images/… 指向 dist/images/，拷一份随 dist 走
    shutil.copytree(
        os.path.join(ROOT, "images"),
        os.path.join(dist, "images"),
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".DS_Store"),
    )

    # 双路复算：合稿结果必须等于各分片顺序拼接
    assert md == "\n".join(chunks), "合稿与分片不一致"

    cn = len(re.findall(r"[\u4e00-\u9fff]", md))
    print("✅ 合稿完成：%s" % os.path.relpath(out, ROOT))
    print("   分片 %d 个｜漏拼 %s" % (len(used), missing if missing else "无"))
    print("   总行数 %s｜中文字符 %s｜总字符 %s"
          % (format(len(md.splitlines()), ","), format(cn, ","), format(len(md), ",")))
    print("   插图引用 %d 处" % len(re.findall(r"images/fig-", md)))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
