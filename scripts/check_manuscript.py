#!/usr/bin/env python3
"""book-studio: 合稿校验器

把每轮改稿后手工做的几类检查固化下来，一条命令跑完：
  1. 脱敏：sensitive.txt 逐词扫描（sensitive.txt 每行一词，# 开头为注释）
  2. 图片引用正向：md 引用的每个 images/... 都存在
  3. 图片引用反向：images/ 里每个文件都被引用（孤立图 = 漏插的信号）
  4. svg/png 配对：svg/ 与 images/ 同名成对
  5. 中英间距：中文↔拉丁字母相邻处缺空格（跳过代码块、行内代码、URL）
  6. 体例完整：每章都有 本章承诺 / 读完后你能回答 / 本章小结 / 行动清单 / 延伸阅读
  7. 图号连续：fig-<章>-<序>-* 的序号无跳号
  8. 合稿完整：parts/ 每个分片都出现在合稿产物里（检测漏拼）
  9+. 直译黑名单：已知英文义项误用（如 call→"叫"）——只拦已知，见 check_calque

用法：
    python3 check_manuscript.py <书名目录> [--verbose] [--fix-spacing]

退出码：0 = 无 ERROR；1 = 存在 ERROR
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------- 常量 ----------

CH_HEADING = re.compile(r"^#\s*第\s*\d+\s*章", re.M)
CHAP_BLOCKS = [
    ("本章承诺", r"\*\*本章承诺\*\*"),
    ("读完后你能回答", r"\*\*读完后你能回答\*\*"),
    ("本章小结", r"\*\*本章小结\*\*"),
    ("行动清单", r"\*\*行动清单\*\*"),
    ("延伸阅读", r"\*\*延伸阅读\*\*"),
]
IMG_REF = re.compile(r"images/([A-Za-z0-9._\-\u4e00-\u9fff]+)")
IMG_EXT = re.compile(r"\.(png|jpe?g|svg|webp|gif)$", re.I)
FIG_NUM = re.compile(r"fig-(\d+)-(\d+)-")
CN_LATIN = re.compile(r"[\u4e00-\u9fff][A-Za-z]|[A-Za-z][\u4e00-\u9fff]")
CN_DIGIT = re.compile(r"[\u4e00-\u9fff][0-9]|[0-9][\u4e00-\u9fff]")

# markdown 链接与文件名 token：其中的「中文↔字母/数字」相邻是**合法**的——
# 文件名不能插空格（如 `95-V3.3配图修改说明.md`）。2026-09-25 作者授权豁免。
MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
FILE_TOKEN = re.compile(
    r"[0-9A-Za-z._\-·]*[\u4e00-\u9fff][0-9A-Za-z._\-·（）()]*"
    r"\.(?:md|markdown|txt|png|svg|pdf|html?|jpg|jpeg|csv|py|ipynb)\b"
)

NON_CHAPTER_HINT = re.compile(r"front|appendix|附录|epilogue|尾声|preface|术语", re.I)


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warns: list[str] = []
        self.oks: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warns.append(msg)

    def ok(self, msg: str) -> None:
        self.oks.append(msg)

    def render(self) -> str:
        out = []
        for m in self.oks:
            out.append(f"  ✅ {m}")
        for m in self.warns:
            out.append(f"  ⚠️  {m}")
        for m in self.errors:
            out.append(f"  ❌ {m}")
        return "\n".join(out)


# ---------- 工具 ----------

def strip_code(text: str) -> str:
    """剥掉围栏代码块与行内代码，避免在代码里误判中英间距。"""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    return text


def mask_code(text: str) -> str:
    """把代码块/行内代码内容抹成空白但**保留行结构**——与 strip_code 不同，
    本函数不删字符只替换，splitlines() 后的行号与原文一致，可放心报"文件:行号"。"""
    def blank(m: re.Match) -> str:
        return "".join("\n" if c == "\n" else " " for c in m.group(0))
    text = re.sub(r"```.*?```", blank, text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", blank, text)
    return text


def md_files(root: Path) -> list[Path]:
    """书稿扫描面 = parts/*.md（唯一手写源）+ **dist/** 下的合稿产物。

    2026-09-25 目录重构（作者裁定，仿前端 dist 模式）：全部生成产物收进 dist/
    （自包含：md / 两版 HTML / pdf / images 拷贝），.gitignore 只写一行 dist/。
    dist 里只放产物，无需再做名字排除；README/release-notes 等非书稿文件不在
    dist，天然不进扫描面——它们的合规由仓库卫生门（check_public_repo.py，
    按 git 纳管全量）负责。
    """
    parts = sorted((root / "parts").glob("*.md")) if (root / "parts").is_dir() else []
    merged = sorted((root / "dist").glob("*.md")) if (root / "dist").is_dir() else []
    return parts + merged


def is_chapter(path: Path, text: str) -> bool:
    if NON_CHAPTER_HINT.search(path.name):
        return False
    return bool(CH_HEADING.search(text))


# ---------- 各项检查 ----------

def check_sensitive(root: Path, files: list[Path], rep: Report) -> None:
    word_file = root / "sensitive.txt"
    if not word_file.exists():
        # 防呆：词表若被"整理"进子目录（scripts/ 之类），脱敏检查会**静默降级**成
        # 一句提醒，而全局结论仍显示"通过"—— 这是最危险的失败模式：校验器还在跑，
        # 但一整类检查已经下线。因此这种情况必须报错，且要指出文件在哪、怎么移回。
        stray = sorted(p for p in root.rglob("sensitive.txt") if p != word_file)
        if stray:
            rel = stray[0].relative_to(root)
            where = f"{rel.parent}/" if str(rel.parent) != "." else ""
            more = f"（另有 {len(stray) - 1} 份）" if len(stray) > 1 else ""
            rep.error(
                f"sensitive.txt 不在书目录根部，而在 {where} 下{more} —— 脱敏检查已被跳过。"
                f"本校验器的约定是 `{root.name}/sensitive.txt`，即与命令行传入的书目录同级；"
                f"词表跟着书稿走，不跟脚本走。请移回： mv {rel} sensitive.txt"
            )
        else:
            # 词表完全缺失：同样判**错误**。理由见铁律 20 —— 静默跳过比崩掉危险得多。
            # 典型触发场景：仓库把 sensitive.txt 写进 .gitignore（作者要求不入库），
            # 于是任何人克隆之后跑校验，脱敏这一类检查整体下线，而结论仍显示"0 项错误"。
            rep.error(
                "sensitive.txt 不存在 —— 脱敏检查被跳过，本轮「通过」的结论不成立。"
                "（词表按作者要求不纳入版本控制；从别处克隆本仓库后，必须先从安全位置恢复它，再跑校验。）"
                "机器只能发现你列进词表的词，词表缺席 = 这一类检查整体下线。"
            )
        return
    words = [
        ln.strip()
        for ln in word_file.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not words:
        rep.warn("sensitive.txt 里没有任何词条 —— 脱敏检查形同虚设")
        return

    hits: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        for w in words:
            for m in re.finditer(re.escape(w), text, re.I):
                line_no = text[: m.start()].count("\n") + 1
                hits.append(f"{f.name}:{line_no} 命中「{w}」")
    if hits:
        rep.error(f"脱敏违规 {len(hits)} 处：")
        for h in hits[:20]:
            rep.error(f"    {h}")
        if len(hits) > 20:
            rep.error(f"    …另有 {len(hits) - 20} 处")
    else:
        # 同时核对词表覆盖度：书里出现过但词表未收的常见公司后缀，只提示不报错
        rep.ok(f"脱敏通过（{len(words)} 个词条，零命中）")


def check_images(root: Path, files: list[Path], rep: Report, verbose: bool) -> None:
    referenced: set[str] = set()
    for f in files:
        text = f.read_text(encoding="utf-8")
        # 只认带图片扩展名的引用 —— 骨架模板里的 images/fig-N-1-<语义>.png 占位符不算
        referenced.update(r for r in IMG_REF.findall(text) if IMG_EXT.search(r))

    img_dir, svg_dir = root / "images", root / "svg"
    on_disk = {p.name for p in img_dir.iterdir() if p.is_file() and not p.name.startswith(".")} if img_dir.is_dir() else set()

    missing = sorted(r for r in referenced if not (img_dir / r).is_file())
    if missing:
        rep.error(f"图片引用缺失 {len(missing)} 个：{', '.join(missing)}")
    else:
        rep.ok(f"图片引用正向通过（{len(referenced)} 张全部存在）")

    orphan = sorted(on_disk - referenced)
    if orphan:
        rep.warn(f"孤立图 {len(orphan)} 个（在 images/ 里但正文未引用 —— 通常是漏插）：{', '.join(orphan)}")
    else:
        rep.ok(f"图片引用反向通过（images/ 无孤立文件，共 {len(on_disk)} 个）")

    # svg/png 配对
    if svg_dir.is_dir():
        svg_names = {p.stem for p in svg_dir.glob("*.svg")}
        png_names = {p.stem for p in img_dir.glob("*.png")} if img_dir.is_dir() else set()
        only_svg = sorted(svg_names - png_names)
        only_png = sorted(png_names - svg_names)
        if only_svg or only_png:
            if only_svg:
                rep.warn(f"只有 SVG 没有 PNG（漏渲染）：{', '.join(only_svg)}")
            if only_png:
                rep.warn(f"只有 PNG 没有 SVG（丢失矢量源，HTML 无法内联）：{', '.join(only_png)}")
        else:
            rep.ok(f"SVG/PNG 配对通过（{len(svg_names)} 对）")
    else:
        rep.warn("svg/ 目录不存在 —— HTML 阅读版需要矢量源内联")


def check_spacing(files: list[Path], rep: Report, verbose: bool, fix: bool) -> None:
    letter_hits: list[tuple[Path, int, str]] = []
    digit_count = 0
    for f in files:
        raw = f.read_text(encoding="utf-8")
        cleaned = strip_code(raw)
        for i, line in enumerate(cleaned.splitlines(), 1):
            if "http" in line or "://" in line:
                continue
            # 先剔 markdown 链接与文件名 token（合法相邻，见 MD_LINK/FILE_TOKEN 处注释），
            # 再做相邻判定；报告仍引原行，便于人核。
            probe = MD_LINK.sub("", line)
            probe = FILE_TOKEN.sub("", probe)
            if CN_LATIN.search(probe):
                letter_hits.append((f, i, line.strip()[:90]))
            probe_wo_gov = re.sub(r"\u3014\d+\u3015\d+\u53f7", "", probe)  # 公文号（国发〔2025〕11号）是法定格式，不算缺空格
            digit_count += len(CN_DIGIT.findall(probe_wo_gov))

    if letter_hits:
        rep.error(f"中英之间缺空格 {len(letter_hits)} 处：")
        for f, i, line in (letter_hits if verbose else letter_hits[:12]):
            rep.error(f"    {f.name}:{i}  {line}")
        if not verbose and len(letter_hits) > 12:
            rep.error(f"    …另有 {len(letter_hits) - 12} 处（--verbose 看全部）")
        if fix:
            fixed_n = 0
            for f in {x[0] for x in letter_hits}:
                raw = f.read_text(encoding="utf-8")
                blocks = re.split(r"(```.*?```)", raw, flags=re.S)
                for bi, blk in enumerate(blocks):
                    if blk.startswith("```"):
                        continue
                    inline = re.split(r"(`[^`\n]*`)", blk)
                    for ii, seg in enumerate(inline):
                        if seg.startswith("`"):
                            continue
                        new = re.sub(r"([\u4e00-\u9fff])([A-Za-z])", r"\1 \2", seg)
                        new = re.sub(r"([A-Za-z])([\u4e00-\u9fff])", r"\1 \2", new)
                        fixed_n += len(re.findall(r"[\u4e00-\u9fff] [A-Za-z]|[A-Za-z] [\u4e00-\u9fff]", new)) - \
                                   len(re.findall(r"[\u4e00-\u9fff] [A-Za-z]|[A-Za-z] [\u4e00-\u9fff]", seg))
                        inline[ii] = new
                    blocks[bi] = "".join(inline)
                f.write_text("".join(blocks), encoding="utf-8")
            rep.warn(f"--fix-spacing 已执行：修复 {fixed_n} 处（请重读文件确认，并重新合稿）")
    else:
        rep.ok("中英间距通过（零处缺空格）")

    if digit_count:
        rep.warn(f"中文↔数字相邻 {digit_count} 处（多为「第1章」这类章节号；如按规范要加空格，逐处人工处理）")


def check_blocks(root: Path, rep: Report) -> None:
    parts_dir = root / "parts"
    if not parts_dir.is_dir():
        rep.warn("parts/ 不存在 —— 跳过体例完整性检查")
        return
    chapters = []
    for f in sorted(parts_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        if is_chapter(f, text):
            chapters.append((f, text))
    if not chapters:
        rep.warn("parts/ 里没有识别到章节文件 —— 跳过体例检查")
        return

    bad: list[str] = []
    for f, text in chapters:
        lack = [name for name, pat in CHAP_BLOCKS if not re.search(pat, text)]
        if lack:
            bad.append(f"{f.name} 缺：{' / '.join(lack)}")
    if bad:
        rep.error(f"体例积木不完整（{len(bad)}/{len(chapters)} 章）：")
        for b in bad:
            rep.error(f"    {b}")
    else:
        rep.ok(f"体例积木完整（{len(chapters)} 章 × 5 块）")


def check_fig_numbers(files: list[Path], rep: Report) -> None:
    seen: dict[int, list[int]] = {}
    for f in files:
        for ch, seq in FIG_NUM.findall(f.read_text(encoding="utf-8")):
            seen.setdefault(int(ch), [])
            if int(seq) not in seen[int(ch)]:
                seen[int(ch)].append(int(seq))
    if not seen:
        rep.warn("未发现 fig-<章>-<序>-* 形式的图片引用 —— 跳过图号连续性检查")
        return
    problems = []
    for ch in sorted(seen):
        seqs = sorted(seen[ch])
        expect = list(range(1, len(seqs) + 1))
        if seqs != expect:
            problems.append(f"第 {ch} 章：实际 {seqs}，期望 {expect}")
    if problems:
        rep.error("图号不连续（可能有跳号或重号）：")
        for p in problems:
            rep.error(f"    {p}")
    else:
        rep.ok(f"图号连续通过（{len(seen)} 章，共 {sum(len(v) for v in seen.values())} 张）")


def check_merge(root: Path, rep: Report) -> None:
    parts_dir = root / "parts"
    if not parts_dir.is_dir():
        return
    merged_candidates = sorted((root / "dist").glob("*.md"))
    if not merged_candidates:
        rep.warn("dist/ 里没有合稿产物 —— 无法做漏拼检查")
        return
    probes = []
    for f in sorted(parts_dir.glob("*.md")):
        body = f.read_text(encoding="utf-8").strip()
        if body:
            probes.append((f.name, re.sub(r"\s+", "", body)[:120]))
    # dist 里可能多版本共存：逐一试，取「零漏拼」的那一版当合稿
    best, best_miss = None, None
    for cand in merged_candidates:
        mtext = re.sub(r"\s+", "", cand.read_text(encoding="utf-8"))
        miss = [name for name, probe in probes if probe and mtext.find(probe) < 0]
        if best is None or len(miss) < len(best_miss):
            best, best_miss = cand, miss
        if not miss:
            break
    if best_miss:
        rep.error(f"合稿漏拼 {len(best_miss)} 个分片：{', '.join(best_miss)}"
                  f"（核对的是 {best.name}；重跑合稿）")
    else:
        rep.ok(f"合稿完整（{len(probes)} 个分片全部出现在 {best.name} 里）")


def _mask_fences(text: str) -> str:
    """把围栏代码块（``` … ```）的内部替换成空行，**保留行号**。

    为什么必须剥：块内的 `# ` 是**内容**（bash 注释、模板里的标题行），不是书的大块标题。
    实测（2026-09-22）：`16-epilogue-appendix-ab.md` 被 check_skeleton 认出 9 个 H1，
    其中 5 个是假阳性 —— 附录 A 的三行 bash 注释（`# 提交数…` / `# 有提交的日历天数` /
    `# 代码行数…`）与附录 B 模板代码块里的 `# 规格：<功能名>` / `# <项目名> — 协作说明`。
    本书因合稿是**原样拼接**而"碰巧通过"，但它同时是一个**误报源**：
    只要某段代码块在合稿环节被处理过（缩进 / 空格 / 换行归一），就会报"标题不在合稿里"。
    **检查面不该包含代码块。**
    """
    out, infence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            infence = not infence
            out.append("")
            continue
        out.append("" if infence else line)
    return "\n".join(out)


def check_skeleton(root: Path, rep: Report) -> None:
    """每个分片里的 H1（行首 `# `）必须原样出现在合稿里，且相对顺序与分片拼接一致。

    来历（2026-09-22 实测事故）：插入一篇〈后记〉时，Edit 的 new_string 末尾漏写了
    old_string 里本应保留的 `---` + `# 附录 A　数据与来源清单` —— **附录 A 的标题连同分隔线
    被整体吃掉**。当时全套校验都没报：
      · 本脚本的 check_merge 只比"分片开头 120 字"，而标题在分片中部；
      · add_pdf_bookmarks 的 HTML↔PDF 逐条比对也一致（两边都没有这个标题）；
      · 脱敏 / 间距 / 体例 / 图号 / 引用，没有一项查"标题还在不在"。
    是**渲染目检**发现的。已固化为机械项 —— 机械能判定的，不该再靠眼睛。
    """
    parts_dir = root / "parts"
    if not parts_dir.is_dir():
        return
    candidates = sorted((root / "dist").glob("*.md"))
    if not candidates:
        return
    heads = []
    for f in sorted(parts_dir.glob("*.md")):
        # 剥掉围栏代码块再查 H1 —— 块内的 `# 注释` 不是书的大块标题（见 _mask_fences）
        for m in re.finditer(r"^# (.+)$", _mask_fences(f.read_text(encoding="utf-8")), re.M):
            heads.append((f.name, m.group(0).strip()))

    # dist 里可能多版本共存：逐一试，取「全部标题都在且顺序一致」的那一版当合稿
    best = None
    for merged in candidates:
        mtext = merged.read_text(encoding="utf-8")
        problems, cursor, checked = [], 0, 0
        for name, h in heads:
            # 用行锚匹配，不用 mtext.find("\n" + h + "\n")：后者匹配不到**合稿第一行**的标题
            #（自测当场抓到 —— 换一本书若首行就是 H1，会误报"不在合稿里"）
            found = re.search(r"(?m)^" + re.escape(h) + r"$", mtext)
            idx = found.start() if found else -1
            checked += 1
            if idx < 0:
                problems.append(f"{name} 的「{h}」不在合稿里")
            elif idx < cursor:
                problems.append(f"「{h}」在合稿里的位置早于前一个标题（顺序错乱）")
            else:
                cursor = idx
        if best is None or len(problems) < len(best[1]):
            best = (merged, problems, checked)
        if not problems:
            break
    merged, problems, checked = best
    if problems:
        rep.error("H1 骨架异常 %d 处：%s（核对的是 %s；典型成因：某次替换漏写了本应保留的标题）"
                  % (len(problems), "；".join(problems[:4]), merged.name))
    else:
        rep.ok(f"H1 骨架完整（{checked} 个标题全部在位、顺序与分片一致）")


# ---------- 主流程 ----------


# ---------- 9. 体例内部一致性（机械校验扫不到"读不懂"，但能扫到"数目对不上"）----------
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10}


def check_declared_counts(root: Path, rep: Report) -> None:
    """三类"自己声明了数字、要和自己对得上"的检查。

    这三类是从一次真实的回读里总结出来的：它们都能通过原本的七项校验，
    因为它们语法正确、与图片无关、中英间距也正常——错在"说的和做的不一致"。

      9a 读完后你能回答：3–4 条（多于 4 条说明承诺没收敛，少于 3 条说明拆得不够）
      9b 「N 道自检题」声明的 N 与实际条目数一致
      9c 中文全角破折号前后不带空格（加粗块后紧跟 —— 时最常见）
    """
    parts = root / "parts"
    a_bad, b_bad, a_files = [], [], 0

    for f in sorted(parts.glob("*.md")):
        t = f.read_text(encoding="utf-8")

        # 9a 只对章文件（# 第 N 章）检查，附录与前置章不适用
        if not re.search(r"^#\s*第\s*\d+\s*章", t, re.M):
            continue
        m = re.search(r"\*\*读完后你能回答\*\*(.*?)(?=\n---|\n##|\n\*\*核心判断|\Z)", t, re.S)
        if m:
            n = len(re.findall(r"^\s*-\s+\S", m.group(1), re.M))
            if not (3 <= n <= 4):
                a_bad.append(f"{f.name}（{n} 条）")

        # 9b N 道自检题
        for mm in re.finditer(r"\*\*([一二三四五六七八九十])\s*道自检题\*\*[：:]?\s*\n(.*?)(?=\n\*\*|\Z)", t, re.S):
            said = CN_NUM.get(mm.group(1), -1)
            got = len(re.findall(r"^\s*\d+\.\s+\S", mm.group(2), re.M))
            if said != got:
                b_bad.append(f"{f.name}（声称 {said} 道 / 实际 {got} 条）")

        # 9c 破折号空格
        if re.search(r"\*\*\s+——", t):
            a_files += 1

    if a_bad:
        rep.error("读完后你能回答 条目数不在 3–4 条：" + "、".join(a_bad))
    else:
        rep.ok("读完后你能回答 条目数合规（各章 3–4 条）")

    if b_bad:
        rep.error("自检题声明数与实际条目数不符：" + "、".join(b_bad))
    else:
        rep.ok("自检题声明数与实际条目数一致")

    if a_files:
        rep.warn(f"{a_files} 个文件里全角破折号前多了一个空格（中文排版不该有）")
    else:
        rep.ok("全角破折号前后无多余空格")



# ---------- 10. 交叉引用完整性 ----------
def check_crossrefs(root: Path, rep: Report) -> None:
    """核查两类跨章引用：① 「第 N.M 节」必须真实存在；② 「第 N 章」的章号必须有对应章文件。

    这两类同样是通过第 1–7 项校验的：它们是纯文本，不含数字、不涉图片、间距正常。
    错在「引用了一个不存在的落点」——读者按图索骥会扑空。
    """
    parts = root / "parts"
    files = sorted(parts.glob("*.md"))

    # 建索引：每章的节号集合、已有章号集合
    secs: dict[int, set[str]] = {}
    chapters: set[int] = set()
    for f in files:
        m = re.match(r"^\d+-ch(\d+)\.md$", f.name)
        if not m:
            continue
        ch = int(m.group(1))
        chapters.add(ch)
        secs[ch] = set(re.findall(r"^#{2,3} (\d+\.\d+(?:\.\d+)?)", f.read_text(encoding="utf-8"), re.M))

    if not chapters:
        rep.warn("未发现形如 NN-chN.md 的章文件，跳过交叉引用检查")
        return

    bad_sec, bad_ch = [], []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"第 (\d+)\.(\d+(?:\.\d+)?) 节", line):
                ch, sec = int(m.group(1)), f"{int(m.group(1))}.{m.group(2)}"
                if ch not in chapters:
                    bad_sec.append(f"{f.name}:{i} → 第 {ch} 章不存在")
                elif sec not in secs.get(ch, set()):
                    bad_sec.append(f"{f.name}:{i} → 第 {ch} 章无「{sec}」节")
            for m in re.finditer(r"第 (\d+) 章", line):
                n = int(m.group(1))
                if n not in chapters and n != 0:
                    bad_ch.append(f"{f.name}:{i} → 第 {n} 章")

    if bad_sec:
        rep.error("「第 N.M 节」引用指向不存在的落点：" + "；".join(bad_sec[:6]))
    else:
        rep.ok(f"跨章节引用全部有效（已索引 {sum(len(v) for v in secs.values())} 个节标题）")

    if bad_ch:
        rep.error("「第 N 章」引用了不存在的章号：" + "；".join(sorted(set(bad_ch))[:6]))
    else:
        rep.ok("「第 N 章」引用的章号均有对应章文件")


# ---------- 11. 体例表声明的数量区间必须被守住 ----------
def check_declared_ranges(root: Path, rep: Report) -> None:
    """体例表（前言）里凡写了数量区间的栏，实际各章必须落在区间内。

    为什么要有这一项：一份稿子在体例表里写了"核心判断 1–2 处""行动清单 3–5 条"，
    实际跑成 1–3 处与 5–8 条，漂了 21 章都没有任何东西报警——因为旧校验只查
    "块在不在"，不查"数量对不对"。

    可复用的判据：声明里出现的任何数字，都必须有一条对应的校验；
    没有校验的数字，就不要写进声明。
    """
    front = root / "parts" / "00-front.md"
    if not front.exists():
        rep.warn("未找到 parts/00-front.md，跳过体例数量区间检查")
        return
    ft = front.read_text(encoding="utf-8")

    specs = []
    m = re.search(r"\|\s*\*\*核心判断\*\*\s*\|[^|]*?(\d+)\s*[–-]\s*(\d+)\s*处", ft)
    if m:
        specs.append(("核心判断", int(m.group(1)), int(m.group(2)),
                      r"^\s*>\s*\*\*核心判断\s*\d", "处"))
    m = re.search(r"\|\s*\*\*行动清单\*\*\s*\|[^|]*\|\s*(\d+)\s*[–-]\s*(\d+)\s*条", ft)
    if m:
        specs.append(("行动清单", int(m.group(1)), int(m.group(2)),
                      r"^\s*\d+\.\s+\S", "条"))

    if not specs:
        rep.warn("体例表里未发现可校验的数量区间声明（核心判断 / 行动清单）")
        return

    bad = []
    for f in sorted((root / "parts").glob("*.md")):
        if not re.match(r"^\d+-ch\d+\.md$", f.name):
            continue
        t = f.read_text(encoding="utf-8")
        for name, lo, hi, pat, unit in specs:
            if name == "行动清单":
                mm = re.search(r"\*\*行动清单\*\*(.*?)(?=\n\*\*|\n#|\Z)", t, re.S)
                if not mm:
                    continue
                n = len(re.findall(pat, mm.group(1), re.M))
            else:
                n = len(re.findall(pat, t, re.M))
            if not (lo <= n <= hi):
                bad.append(f"{f.name} 的{name} {n}{unit}（声明 {lo}–{hi}）")

    if bad:
        rep.error("体例表声明的数量区间未被守住：" + "；".join(bad[:8]))
    else:
        rng = "、".join(f"{n} {lo}–{hi}{u}" for n, lo, hi, _, u in specs)
        rep.ok(f"体例表声明的数量区间全部被守住（{rng}）")

# ---------- 12. 附录 A 的条目编号与引用必须自洽 ----------
APPENDIX_SEC = re.compile(r"^## (A\.\d)", re.M)
APPENDIX_ANY_HEAD = re.compile(r"^#{1,2} ")
APPENDIX_ROW = re.compile(r"^\| (\d+) \|")
APPENDIX_REF = re.compile(r"附录\s*(A\.\d|A)?\s*第\s*(\d+)\s*条")


def check_appendix_refs(root: Path, rep: Report) -> None:
    """附录 A 的条目编号必须连续、无重号；正文里的「附录 A[.N] 第 M 条」必须指得着。

    为什么要加这一项（2026-09-20 实测）：一本稿子的 A.2 中段**后插入了一张 5 条的
    附表**（#15–#19），插入时只补了自己的号，**没有把后面各小节的序列顺推**——
    于是 A.3 的首条与 A.2 的末条**同为 #19**，全书 35 个条目最大编号只到 34。
    这类错误**纯文本、不含数字、不涉图片、间距正常**，能通过其余全部机械校验，
    只会在读者按「见附录 A.3 第 23 条」翻过去、发现对不上时暴露。

    ⚠️ 解析要点：**只在 `## A.N` 小节内计号**。附录 B/C 的表会从 1 重新起号，
    若不在遇到 `# 附录 B` 这类标题时把 `sec` 清空，会把它们的行算进 A 册，
    凭空制造一堆「重号」假警报。
    """
    parts_dir = root / "parts"
    files = sorted(parts_dir.glob("*.md")) if parts_dir.is_dir() else []
    if not files:
        return

    rows: list[tuple[str, int]] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        if not APPENDIX_SEC.search(text):
            continue
        sec: str | None = None
        for line in text.splitlines():
            if APPENDIX_ANY_HEAD.match(line):
                h = APPENDIX_SEC.match(line)
                sec = h.group(1) if h else None
                continue
            if sec is not None:
                m = APPENDIX_ROW.match(line)
                if m:
                    rows.append((sec, int(m.group(1))))

    if not rows:
        rep.warn("未发现「## A.N」小节下的编号条目，跳过附录条目校验")
        return

    nums = [n for _, n in rows]
    where: dict[int, set[str]] = {}
    for s, n in rows:
        where.setdefault(n, set()).add(s)

    dups = sorted(n for n, c in ((k, len(v)) for k, v in where.items()) if c > 1)
    gaps = sorted(set(range(1, max(nums) + 1)) - set(nums))
    if dups:
        detail = "；".join(f"#{n} 见于 {', '.join(sorted(where[n]))}" for n in dups[:5])
        rep.error(f"附录 A 条目编号有重号：{detail} —— 多半是**中途插入了一组条目却没把"
                  f"后面的序列顺推**。修法：对插入点之后的全部条目 +n，并同步正文里的引用")
    elif gaps:
        rep.error(f"附录 A 条目编号跳号：缺 {gaps[:10]}")
    else:
        rep.ok(f"附录 A 条目编号连续（1–{max(nums)}，共 {len(rows)} 条，无重号无跳号）")

    bad_ref = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in APPENDIX_REF.finditer(line):
                secno, num = m.group(1), int(m.group(2))
                if num not in where:
                    bad_ref.append(f"{f.name}:{i} → 第 {num} 条（无此条）")
                elif secno not in (None, "A") and secno not in where[num]:
                    real = "/".join(sorted(where[num]))
                    bad_ref.append(f"{f.name}:{i} → 写的 {secno} 第 {num} 条，实际在 {real}")
    if bad_ref:
        rep.error("「附录 A 第 M 条」引用指向不存在的落点：" + "；".join(bad_ref[:6]))
    else:
        rep.ok("附录 A 条目引用全部指得着（写了小节号的，小节也一致）")


# ---------- 直译黑名单（2026-09-21 新增）----------

# 教训：翻译腔分两层。A 类句式层（"让我们 / 换句话说"）可关键词扫描；
# B 类词义层是英文多义词的**义项选错**（call → "叫"），错误产物长着正常中文的脸，
# 任何关键词清单都扫不全。黑名单只拦**已发生过的**模式，是防复发补丁；
# 真防线是逐句反问"中文母语者会这么说吗"（回读检查，机器查不了）。
CALQUE_ERROR: list[tuple[str, str]] = [
    (r"被叫起来", "英文 call 误译为「叫」→ 应为「调用」"),
]
CALQUE_WARN: list[tuple[str, str]] = [
    (r"边缘情况", "edge case 直译 → 中文惯例为「边界情况」"),
]


def check_calque(files: list[Path], rep: Report) -> None:
    """直译黑名单：扫描已知的英文义项误用。命中报 ERROR / WARN。"""
    n = 0
    for f in files:
        for i, line in enumerate(mask_code(f.read_text(encoding="utf-8")).splitlines(), 1):
            for pat, why in CALQUE_ERROR:
                for m in re.finditer(pat, line):
                    n += 1
                    rep.error(f"直译黑名单 {f.name}:{i}：…{line[max(0, m.start() - 14):m.end() + 14]}…（{why}）")
            for pat, why in CALQUE_WARN:
                for m in re.finditer(pat, line):
                    n += 1
                    rep.warn(f"直译黑名单 {f.name}:{i}：…{line[max(0, m.start() - 14):m.end() + 14]}…（{why}）")
    if n == 0:
        rep.ok(f"直译黑名单：0 命中（{len(CALQUE_ERROR) + len(CALQUE_WARN)} 条模式；只拦已知，新直译靠逐句母语感反问）")


# ---------- 11. 加粗配对错位（只在渲染态现形，见铁律 25 / 31）----------

BOLD_ONLY_PUNCT = re.compile(
    r"^[\s，。、：；！？…—\-~·（）()\[\]「」【】“”\"'’‘①②③④⑤⑥⑦⑧⑨⑩0-9.．]+$"
)
# 纯连接词：`**要点 A**和**要点 B**` 这种合法写法里的裸段，**不算**嵌套嫌疑
BOLD_ONLY_CONJ = {"和", "与", "及", "或", "但", "而", "以及", "并且", "而且", "同时", "即", "、"}


def check_bold_nesting(root: Path, rep: Report, verbose: bool) -> None:
    """两类只靠读源码看不出来的加粗缺陷（渲染态才现形，见铁律 25 / 31）：

    1. **不成对**：一行里 `**` 个数是奇数 —— 句尾留了一个落单的 `**`。
    2. **嵌套意图 → 配对反转**：一行以 `**` 起、以 `**` 止、`**` ≥ 4 个，
       且**非空裸段全部是短词**（≤5 字、非纯标点、非纯连接词）。
       markdown 的 `**` 不能嵌套，最终渲染成"连接词被加粗、该强调的词变裸字"——
       强调完全反转（本书第 1 / 12 / 15 章各一例，全是这样被抓出来的）。

    判据的边界（免得下一轮把噪音重报一遍）：
    - `**要点 A**和**要点 B**` —— 裸段是纯连接词 → **合法，不报**。
    - `**问句？**因为……**结论。**` —— 存在**长裸段**（整句解释） → **合法，不报**
      （两段各自独立加粗，不是嵌套意图）。
    - 本检查是**启发式**，报出来的一律要人工确认；它只覆盖 `parts/`（合稿产物不重复报）。
    """
    unpaired: list[tuple[str, int, str]] = []
    nested: list[tuple[str, int, str, list[str]]] = []
    files = sorted((root / "parts").glob("*.md")) if (root / "parts").is_dir() else []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            body = line.strip().lstrip(">").strip()
            if body.count("**") == 0:
                continue
            if body.count("**") % 2 == 1:
                unpaired.append((f.name, i, body))
                continue
            if body.count("**") < 4 or not (body.startswith("**") and body.endswith("**")):
                continue
            seg = body.split("**")[1:]
            plain = [seg[k] for k in range(1, len(seg) - 1, 2) if seg[k].strip()]
            if not plain:
                continue
            if all(len(p.strip()) <= 5 and not BOLD_ONLY_PUNCT.match(p) and p.strip() not in BOLD_ONLY_CONJ
                   for p in plain):
                nested.append((f.name, i, body, [p.strip() for p in plain]))

    if unpaired:
        rep.warn(f"加粗标记不成对 {len(unpaired)} 处（行内 `**` 为奇数，句尾有落单）：")
        for name, i, body in (unpaired if verbose else unpaired[:6]):
            rep.warn(f"    {name}:{i}  {body[:60]}")
    if nested:
        rep.warn(f"加粗嵌套嫌疑 {len(nested)} 处（`**` 不能嵌套 → 渲染会把连接词加粗、该强调的词变裸字，逐处人工确认）：")
        for name, i, body, short in (nested if verbose else nested[:6]):
            rep.warn(f"    {name}:{i}  裸段={short}  {body[:60]}")
        if not verbose and len(nested) > 6:
            rep.warn(f"    …另有 {len(nested) - 6} 处（--verbose 看全部）")
    if not unpaired and not nested:
        rep.ok("加粗配对：无落单标记、无嵌套嫌疑（渲染态反转类缺陷）")


# ---------- 12. 汉字之间的多余空格（术语替换残留，见铁律 17 / 23）----------

CJK = "\u4e00-\u9fff"
CJK_SPACE = re.compile(f"([{CJK}]) ([{CJK}])")
# 合法例外：篇/章/节的号与名之间的分隔（「第三篇 用对它」「第 11 章 让它…」）、
# 以及国家标准名称里原文自带的分隔（《网络安全技术 人工智能生成合成内容标识方法》）
CJK_SPACE_OK = re.compile(r"第\s*[\d一二三四五六七八九十]+\s*[篇章节]|网络安全技术")


def check_cjk_space(root: Path, rep: Report, verbose: bool) -> None:
    """汉字之间不该有空格——但 `中英间距` 检查只查 CN↔Latin，查不到这一类。

    典型来历：把英文词统一替换成中文词时**只换了词、没删两侧的空格**
    （`受监督的 agent` → `受监督的 智能体`，正确应为 `受监督的智能体`）。
    本项目实测：**正文 0 处；`svg/` 图内文字 80 处、分布在 14 张图**——
    而图内文字不在 `parts/` 里，只有扫 `svg/` 才看得见（铁律 17 / 23）。
    """
    hits: list[tuple[str, int, str]] = []
    md_hits: list[tuple[str, int, str]] = []
    for f in sorted((root / "parts").glob("*.md")) if (root / "parts").is_dir() else []:
        text = re.sub(r"```.*?```", "", f.read_text(encoding="utf-8"), flags=re.S)
        for i, line in enumerate(text.split("\n"), 1):
            if CJK_SPACE_OK.search(line):
                continue
            if CJK_SPACE.search(line):
                md_hits.append((f.name, i, line.strip()))
    for f in sorted((root / "svg").glob("*.svg")) if (root / "svg").is_dir() else []:
        for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            if "<text" not in line:
                continue
            inner = re.sub(r"^.*?>", "", line).replace("</text>", "").strip()
            n = len(CJK_SPACE.findall(inner))
            if n:
                hits.append((f.name, i, n, inner[:80]))

    if md_hits:
        rep.warn(f"正文里汉字之间有多余空格 {len(md_hits)} 处（多半是术语替换残留）：")
        for name, i, line in (md_hits if verbose else md_hits[:6]):
            rep.warn(f"    {name}:{i}  {line[:70]}")
    if hits:
        figs = sorted({h[0] for h in hits})
        total = sum(h[2] for h in hits)
        rep.warn(f"图内文字里汉字之间有多余空格 {total} 处（{len(hits)} 行、{len(figs)} 张图）——"
                 f"改 svg 后须重渲 PNG + 跑 L5 闸门 + 目检：{', '.join(figs[:6])}{' …' if len(figs) > 6 else ''}")
        for name, i, n, inner in (hits if verbose else hits[:4]):
            rep.warn(f"    {name}:{i}（{n} 处）  {inner}")
        if not verbose and len(hits) > 4:
            rep.warn(f"    …另有 {len(hits) - 4} 行（--verbose 看全部）")
    if not md_hits and not hits:
        rep.ok("汉字间空格：正文与图内文字均无残留")


# ============ V3.3 新增闸门（本书仓库内副本专用）============
# 出处：`96-V3.3技术审稿与修订清单.md` §5 第二轮交叉复核（C03 / C13 / C14）。
# 本文件是 book-studio 技能校验器的**书仓内副本**（技能版权威 sha256 前缀 66bd9e29ab7b206c）。
# 两边不一致时：本书交付以本文件为准，通用改进回灌技能。
# 三道闸门各对应一个真实发生过的失效：
#   check_quote_forms     —— 批量修订混入弯引号；全书体例用 ASCII 直引号（C13）
#   check_figure_numbers  —— 图内数字在正文与附录 A 都查不到出处（C03，见 95 F19/F20）
#   check_probes          —— 上一轮探针短语没落文件，下一轮无法复跑（C14）

CURLY_QUOTES = "“”‘’"
PROBES_FILE = "scripts/probes.txt"
FIG_NUMBERS_OK_FILE = "scripts/figure-numbers-ok.txt"

# 带量纲/单位的图内数值：年份与坐标轴刻度故意不在其列
FIG_NUM_TOKEN = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:%|个百分点|多个|倍|万|亿|行/小时|LOC|小时|天|次|人|名|位|家|个|模型|条|行代码)\+?"
    r"|\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\d{1,3}(?:,\d{3})+"
)


def _svg_texts(root: Path) -> list[tuple[str, int, str]]:
    """取 svg/ 里所有 <text> 文本，返回 (文件名, 行号, 内容)。"""
    out: list[tuple[str, int, str]] = []
    for f in sorted((root / "svg").glob("*.svg")) if (root / "svg").is_dir() else []:
        for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            if "<text" not in line:
                continue
            inner = re.sub(r"^.*?>", "", line).replace("</text>", "").strip()
            inner = re.sub(r"<[^>]+>", "", inner)
            if inner:
                out.append((f.name, i, inner))
    return out


def check_quote_forms(root: Path, rep: Report, verbose: bool) -> None:
    """正文与图内文字不得混用弯引号——本书体例统一用 ASCII 直引号 `"`。

    来历：V3.3 第一轮批量修订在 2 个文件里留了 8 处弯引号（C13）。它是**编辑过程
    的残留**，不是排版选择，所以按错误处理；确有正当理由（如逐字引用外部公文原文）
    时逐处人工改正文，不在校验器里开口子。
    """
    hits: list[tuple[str, int, str]] = []
    targets = sorted((root / "parts").glob("*.md")) if (root / "parts").is_dir() else []
    targets += sorted((root / "svg").glob("*.svg")) if (root / "svg").is_dir() else []
    for f in targets:
        for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            if any(c in line for c in CURLY_QUOTES):
                hits.append((f.name, i, line.strip()))
    if hits:
        rep.error(f"弯引号 {len(hits)} 处（全书正文用 ASCII 直引号，弯引号是修订残留）：")
        for name, i, line in (hits if verbose else hits[:8]):
            rep.error(f"    {name}:{i}  {line[:70]}")
        if not verbose and len(hits) > 8:
            rep.error(f"    …另有 {len(hits) - 8} 处（--verbose 看全部）")
    else:
        rep.ok("引号形态：正文与图内文字统一为 ASCII 直引号")


def _load_fig_allow(root: Path) -> dict[tuple[str, str], str]:
    ok: dict[tuple[str, str], str] = {}
    p = root / FIG_NUMBERS_OK_FILE
    if not p.is_file():
        return ok
    for line in p.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cols = [c.strip() for c in re.split(r"[\t]|\s{2,}", line) if c.strip()]
        if len(cols) >= 2:
            ok[(cols[0], cols[1])] = cols[2] if len(cols) > 2 else "（登记项未写理由）"
    return ok


def _numtrace_present(corpus_norm: str, digits: str, unit: str) -> bool:
    """正文里是否真的出现过这个数字（并带着同一类单位）。

    只比数字会把 `731 次`（图）和 `**731**`（附录）判成对不上；只比"数字在不在正文"
    又会让被改口的 `39 个百分点` 蒙混过关。所以：定位数字主干，再看它左右 ±8 字内
    有没有同类单位。单位本身也在正文里出现过（说明不是凭空数字）时同样放行。
    """
    if not corpus_norm:
        return False
    if not unit or len(digits.replace(".", "")) >= 4:
        # 四位以上主干近乎唯一，出现即视为同一量；短数字必须配同类单位才算对上
        return digits in corpus_norm
    for m in re.finditer(re.escape(digits), corpus_norm):
        window = corpus_norm[max(0, m.start() - 8):m.end() + 8]
        if unit in window:
            return True
    return False


def check_figure_numbers(root: Path, rep: Report, verbose: bool) -> None:
    """图里的量化说法必须在正文/附录里查得到——否则读者只能信一张没有出处的图。

    判据：从 `svg/*.svg` 的 `<text>` 中取**带单位/量纲**的数值，按「数字主干 + 同类单位
    邻近共现」在 `parts/*.md` 全文（去代码块）里核对。找不到 → 提醒，并指向
    `95-V3.3配图修改说明.md`。年份、坐标轴刻度、纯结构数字故意不查，避免噪声盖住真问题。
    已复核的例外写进 `scripts/figure-numbers-ok.txt`（一行一条，必须带理由）。
    """
    corpus_parts = sorted((root / "parts").glob("*.md")) if (root / "parts").is_dir() else []
    if not corpus_parts:
        rep.warn("parts/ 不存在 —— 跳过图内数字溯源检查")
        return
    corpus = " ".join(
        re.sub(r"```.*?```", "", f.read_text(encoding="utf-8"), flags=re.S) for f in corpus_parts
    )
    corpus_norm = re.sub(r"[ \t\u3000,，]|<br>|[*_`]", "", corpus)
    allow = _load_fig_allow(root)

    orphans: list[tuple[str, int, str, str]] = []
    for name, i, text in _svg_texts(root):
        stem = name[:-4] if name.endswith(".svg") else name
        for m in FIG_NUM_TOKEN.finditer(text):
            tok = m.group(0).strip()
            if (stem, tok) in allow:
                continue
            digits = re.sub(r"[^\d.]", "", tok)
            unit = re.sub(r"[\d.,\s$]", "", tok)
            digits_norm = digits.replace(",", "")
            if _numtrace_present(corpus_norm, digits_norm, unit):
                continue
            orphans.append((name, i, tok, text[:56]))

    if orphans:
        figs = sorted({o[0] for o in orphans})
        rep.warn(f"图内数值在正文/附录查不到 {len(orphans)} 处（{len(figs)} 张图："
                 f"{', '.join(figs)}）——按 `95-V3.3配图修改说明.md` 撤下数字或先补附录 A 来源再回填")
        for name, i, tok, text in (orphans if verbose else orphans[:10]):
            rep.warn(f"    {name}:{i}  「{tok}」  …{text}…")
        if not verbose and len(orphans) > 10:
            rep.warn(f"    …另有 {len(orphans) - 10} 处（--verbose 看全部）")
        if len(orphans) > 12:
            rep.warn("    提示：单图超过 3 处无溯源数字，通常说明这张图该改成不编码数值的概念图")
    else:
        rep.ok("图内数字溯源：全部带单位数值都能在正文/附录中查到（例外见 figure-numbers-ok.txt）")


def _parse_probes(path: Path) -> list[tuple[str, str, int, str]]:
    """读探针清单。语法：`+ 短语 预期次数` / `- 短语` / `! 短语`（图内）。# 为注释。"""
    items: list[tuple[str, str, int, str]] = []
    for raw in path.read_text(encoding="utf-8").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        kind, rest = line[0], line[1:].strip()
        if kind not in "+-!" or not rest:
            continue
        cols = rest.rsplit(None, 1)
        expect = 1
        if kind == "+" and len(cols) == 2 and cols[1].isdigit():
            rest, expect = cols[0], int(cols[1])
        items.append((kind, rest, expect, line))
    return items


def check_probes(root: Path, rep: Report, verbose: bool) -> None:
    """正反探针入库：把「这轮改掉的句子确实改掉了」变成可复跑的检查。

    来历：V3.3 第一轮记了「14 条正向、8 条反向全部通过」，但**短语没落到任何文件**，
    第二轮无法复跑（C14）。清单在 `scripts/probes.txt`，每轮复核后追加，不删旧条目
    ——旧条目是最便宜的「别把改口改回去」护栏。
    """
    pf = root / PROBES_FILE
    if not pf.is_file():
        rep.warn(f"{PROBES_FILE} 不存在 —— 跳过探针检查（建议把本轮短语清单落进仓库）")
        return
    items = _parse_probes(pf)
    if not items:
        rep.warn(f"{PROBES_FILE} 里没有任何有效探针")
        return

    body_files = sorted((root / "parts").glob("*.md")) if (root / "parts").is_dir() else []
    body = {f.name: re.sub(r"```.*?```", "", f.read_text(encoding="utf-8"), flags=re.S)
            for f in body_files}
    figs = {n: t for n, _, t in _svg_texts(root)}
    fig_blob = " ".join(figs.values())

    miss: list[str] = []
    residue: list[str] = []
    for kind, phrase, expect, src in items:
        if kind == "+":
            n = sum(t.count(phrase) for t in body.values())
            if n < expect:
                miss.append(f"「{phrase}」应≥{expect} 次，实测 {n} 次（{src}）")
        else:
            blob = fig_blob if kind == "!" else " ".join(body.values())
            if phrase in blob:
                n = blob.count(phrase)
                where = "图内文字" if kind == "!" else "正文"
                if kind == "!":
                    hit = [k for k, t in figs.items() if phrase in t]
                    where = f"图内文字（{', '.join(sorted(set(hit))[:3])}）"
                residue.append(f"{where} 残留「{phrase}」{n} 次")

    if miss:
        rep.error(f"正向探针未落位 {len(miss)} 条：")
        for m in (miss if verbose else miss[:8]):
            rep.error(f"    {m}")
    if residue:
        rep.error(f"反向探针有残留 {len(residue)} 条：")
        for r in (residue if verbose else residue[:8]):
            rep.error(f"    {r}")
    if not miss and not residue:
        rep.ok(f"探针：正向 {sum(1 for k, *_ in items if k == '+')} 条全部落位、"
               f"反向 {sum(1 for k, *_ in items if k == '-')} 条正文 / "
               f"{sum(1 for k, *_ in items if k == '!')} 条图内全部 0 命中")


def run_selftest() -> int:
    """三道新闸门的正反双向自测：不写进书仓，只验证闸门本身会亮/会灭。"""
    import tempfile

    cases = [
        ("弯引号", check_quote_forms, 'parts/01-ch1.md', "他说“这是弯引号”。", "引号应报错"),
        ("图内数字", check_figure_numbers, "svg/fig-1-1-x.svg",
         '<text x="1" y="1">效率提升 91.4%</text>', "无溯源数字应报警"),
        ("探针残留", check_probes, "parts/02-ch2.md", "旧的绝对断言：它只会做一件事", "反向探针应报错"),
    ]
    passes = fails = 0
    for label, run, rel, payload, desc in cases:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "parts").mkdir()
            (root / "svg").mkdir()
            (root / "scripts").mkdir()
            (root / rel).write_text(payload, encoding="utf-8")
            (root / "parts" / "00-front.md").write_text("正文里没有这个数字。\n", encoding="utf-8")
            (root / PROBES_FILE).write_text("- 它只会做一件事\n+ 不存在的关键句\n", encoding="utf-8")
            rep = Report()
            run(root, rep, True)
            fired = bool(rep.errors or rep.warns)
            print(f"  [{'PASS' if fired else 'FAIL'}] 负向自测：{desc}（{label}）")
            passes += int(fired)
            fails += int(not fired)
    # 正向：干净样本必须全绿
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "parts").mkdir()
        (root / "svg").mkdir()
        (root / "scripts").mkdir()
        (root / "parts" / "01-ch1.md").write_text('他说"这是直引号"，实测 91.4%。\n', encoding="utf-8")
        (root / "svg" / "fig-1-1-x.svg").write_text('<text x="1" y="1">效率提升 91.4%</text>',
                                                    encoding="utf-8")
        (root / PROBES_FILE).write_text('- 它只会做一件事\n+ 直引号\n', encoding="utf-8")
        clean = 0
        for run in (check_quote_forms, check_figure_numbers, check_probes):
            rep = Report()
            run(root, rep, True)
            ok = not rep.errors and not rep.warns
            clean += int(ok)
            print(f"  [{'PASS' if ok else 'FAIL'}] 正向自测：{run.__name__} 干净样本应全绿")
        passes += clean
        fails += 3 - clean
    print(f"自测结果：{passes} 通过 / {fails} 失败")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="book-studio 合稿校验器（本书仓库内副本 + V3.3 新增闸门）")
    ap.add_argument("book_dir", nargs="?", help="书名目录（--selftest 时可省略）")
    ap.add_argument("--verbose", action="store_true", help="列出全部命中，不只前 12 条")
    ap.add_argument("--fix-spacing", action="store_true", help="自动修复中英之间缺空格（仅拉丁字母，不动数字）")
    ap.add_argument("--selftest", action="store_true",
                    help="只跑 V3.3 新增三道闸门的正反双向自测，不检查书稿")
    args = ap.parse_args()

    if args.selftest:
        return run_selftest()

    if not args.book_dir:
        print("ERROR: 需要给出书名目录（或改用 --selftest）", file=sys.stderr)
        return 2
    root = Path(args.book_dir).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: 目录不存在：{root}", file=sys.stderr)
        return 2

    files = md_files(root)
    if not files:
        print(f"ERROR: {root} 下没找到任何 md 文件", file=sys.stderr)
        return 2

    rep = Report()
    check_sensitive(root, files, rep)
    check_images(root, files, rep, args.verbose)
    check_spacing(files, rep, args.verbose, args.fix_spacing)
    check_blocks(root, rep)
    check_fig_numbers(files, rep)
    check_merge(root, rep)
    check_skeleton(root, rep)
    check_declared_counts(root, rep)
    check_crossrefs(root, rep)
    check_appendix_refs(root, rep)
    check_declared_ranges(root, rep)
    check_calque(files, rep)
    check_bold_nesting(root, rep, args.verbose)
    check_cjk_space(root, rep, args.verbose)
    check_quote_forms(root, rep, args.verbose)
    check_probes(root, rep, args.verbose)
    check_figure_numbers(root, rep, args.verbose)

    print(f"book-studio 合稿校验 · {root.name}")
    print(f"扫描 {len(files)} 个 md 文件")
    print("─" * 60)
    print(rep.render())
    print("─" * 60)
    print(f"结论：❌ {len(rep.errors)} 项错误 · ⚠️ {len(rep.warns)} 项提醒 · ✅ {len(rep.oks)} 项通过")
    if rep.errors:
        print("校验未通过 —— 按上述清单修完再跑一次，不要带错交付。")
        return 1
    print("校验通过。注意：机器查不了的三件事仍需人工确认——")
    print("  · 机构类型是否与源材料一致（国有大行 / 城商行 / 农商行）")
    print("  · 每个数字是否过了「数据三问」（主语 / 目标还是达成 / 口径）")
    print("  · 配图是否已全量目检（禁止抽查）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
