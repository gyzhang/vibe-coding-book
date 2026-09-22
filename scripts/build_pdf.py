#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 A4 PDF —— 三形态的第三种。

用法：
    python3 scripts/build_pdf.py                 # 用默认 HTML 与产物名
    python3 scripts/build_pdf.py --keep-temp     # 保留 Chrome 的临时 profile（排障用）

为什么要有这个脚本（而不是在终端里敲命令）
------------------------------------------
上游项目（《智能体工程》）这一步是**手工敲 Chrome 命令**的，参数只活在项目记忆里。
本技能铁律 16：**交付链里任何一步只要还在"手工补"，就把它变成脚本**——
否则每轮都要重新回忆一遍参数，而 PDF 的分页对参数极度敏感，错一个参数就是整册重来。

参数为什么是这几个
------------------
· `--print-to-pdf`  —— Chrome 从 HTML 排版导出 PDF；页面尺寸/边距由 HTML 里的
   `@page{size:A4;margin:18mm 16mm}` 决定，**不在这里传**（两处都写必然漂）。
· `--no-pdf-header-footer` —— 关掉 Chrome 自动加的页眉页脚（文件路径 / 日期 / 它自己的页码）。
  页码由 `stamp_page_numbers.py` 事后盖：位置、字体、颜色都可控，且能跳过封面。
· `--no-sandbox` —— 本机无头 Chrome 不加这个会 `sandbox initialization failed`、退出码 133、产出空文件。
· `--virtual-time-budget` —— 等字体与 JS 布局稳定。**给足够长**：太短会漏字或半张图。
· `--run-all-compositor-stages-before-draw` —— 避免把"还在绘制中的图层"印进 PDF。

产物命名
--------
**不加"打印版"三字**（作者 2026-09-19 裁定）：PDF 同时承担在线阅读，
绝大多数读者是在阅读器里读它，叫"打印版"会误导。

⚠️ 判定"写完了"的两个坑（都实测过，第二个是第一个的变种）
------------------------------------------------------
**坑一（2026-09-20）**：判定只看"磁盘上已有文件体积连续两次不变"，**不校验这份
文件是不是本次 Chrome 写的**。Chrome 对 135 页的书要跑约 30 秒，远超 3 秒判定窗，
于是只要路径上还留着上一轮的 PDF，脚本就会把它当成"本次产物"返回 —— **静默交付
一份陈旧 PDF**。修法：启动前 `unlink` 旧产物。

**坑二（2026-09-21）**：坑一修好之后，判定窗本身仍然是 3 秒。Chrome 分块写盘时
只要有 3 秒停顿，脚本就认定"写完"，随即 `killpg` **把还在渲染的 Chrome 杀掉** ——
实测产出一份 **116 页（应为 136）** 的截断 PDF，末页停在 C.3，C.4/C.5 整段消失，
而脚本照样打印"✅ PDF 已生成"。靠 `add_pdf_bookmarks` 那条
"HTML 130 / PDF 16"的条数不一致才拦下来（**闸门立功，否则一本缺 20 页的书就交付了**）。

**判据：判定"产物写好了"必须证明它诞生于本次运行 —— 还不够，还必须证明它是完整的。**
所以现在三道：① 启动前删旧产物；② 主判据改为 **Chrome 进程自己退出**（它写完才退），
体积稳定只作兜底且延长到 15 秒；③ **完整性闸门**：PDF 末 3 页必须含 HTML 的最后一个
标题，不含就报错退出。截断的 PDF 不许以"✅"的姿态流出去。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # 本脚本的上一级 = 书目录


def guess_book_name(root: Path) -> str:
    r"""从书目录里推断书名：**取"有同名 .html 的那些 .md"**。

    为什么不用"排除法"：书目录里除正文外还可能有审稿意见、版本对比报告、勘误表……
    想穷举所有非正文文件名是追不上的（第一版用 `\d{2}-` 排除，就漏掉了
    `V2-审阅报告.md`，于是把它当成书名候选报错）。改用**正向特征**：
    这本书的产物是成对的（`<书名>.md` + `<书名>.html`），而协作文档不成对。
    换一本书时不用改脚本——把 build_pdf.py 放进新书的 scripts/ 即可。
    """
    cands = [p.stem for p in sorted(root.glob("*.md"))
             if (root / f"{p.stem}.html").is_file()]
    if len(cands) == 1:
        return cands[0]
    raise SystemExit(
        f"ERROR 无法从 {root} 推断书名（找到 {len(cands)} 个「md + html 成对」的候选：{cands}）。"
        f"请显式传 --html 与 --out。"
    )

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if os.access(c, os.X_OK):
            return c
    found = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if found:
        return found
    raise SystemExit(
        "ERROR 找不到 Chrome / Chromium。安装其一，或把可执行文件路径加进 CHROME_CANDIDATES。"
    )


def last_headings(html_path: Path, n: int = 3) -> list[str]:
    r"""取 HTML 里**最后几个标题**的纯文本（去标签、去空白）。

    用途：校验 PDF 有没有被截断。取 3 条而不是 1 条，是因为末页可能只剩一个标题的
    一部分；只要最后一条命中就算完整，多取几条是为了报错时能把"停在哪个标题"说清楚。
    """
    txt = html_path.read_text(encoding="utf-8")
    out = []
    for _lvl, body in re.findall(r"<h([1-4])[^>]*>(.*?)</h\1>", txt, re.S)[::-1]:
        t = re.sub(r"<[^>]+>", "", body)
        t = t.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        t = re.sub(r"\s+", "", t)
        if t and t not in out:
            out.append(t)
        if len(out) >= n:
            break
    return out


def verify_pdf_complete(pdf_path: Path, headings: list[str], tail_pages: int = 3) -> tuple[bool, str]:
    """完整性闸门：PDF 末 `tail_pages` 页必须含 HTML 的最后一个标题。

    返回 (是否完整, 说明)。**不完整一律报错退出** —— 截断的 PDF 不许以"✅"的姿态流出去
    （2026-09-21 实测：116 页 / 应为 136 页，脚本照样打印"已生成"）。
    """
    if not headings:
        return True, "(HTML 里没抓到标题，跳过完整性闸门)"
    try:
        import pymupdf
        d = pymupdf.open(pdf_path)
        n = d.page_count
        tail = "".join(d[i].get_text() for i in range(max(0, n - tail_pages), n))
        d.close()
    except Exception as e:                                  # 打不开也算不完整
        return False, f"无法打开产物 PDF：{e}"
    tail = re.sub(r"\s+", "", tail)
    if headings[0] in tail:
        return True, f"末页含最后一个标题「{headings[0]}」"
    stopped = next((h for h in headings if h in tail), None)
    if stopped:
        return False, (f"PDF 疑似被截断：末 {tail_pages} 页只到「{stopped}」，"
                       f"而 HTML 的最后一个标题是「{headings[0]}」")
    return False, (f"PDF 疑似被截断：末 {tail_pages} 页里连倒数 3 个标题「{' / '.join(headings)}」"
                   f"都没出现")


def verify_pdf_scale(pdf_path: Path, min_pt: float = 10.0) -> tuple[bool, str]:
    r"""缩放闸门：正文实测字号不得小于 `min_pt`。

    为什么光有"完整性闸门"还不够（2026-09-21 同一天踩的第二个洞）：
    内容宽度撑破 A4 可打印区时，Chrome 会**把整页缩小来适应**（实测缩到 0.871）。
    这时 PDF **内容照样完整**——末页也有最后一个标题，完整性闸门照样放行——
    但正文从 10.5pt 变成 9.15pt、章标题从 18.06pt 变成 15.73pt，
    页数从 131 掉到 116，书签闸门随即误报"HTML 130 / PDF 16"。
    **只看"内容在不在"抓不到缩放，必须看字号。**
    """
    try:
        import pymupdf
        from collections import Counter
        d = pymupdf.open(pdf_path)
        c: Counter = Counter()
        for p in d:
            for b in p.get_text("dict")["blocks"]:
                for ln in b.get("lines", []):
                    for sp in ln.get("spans", []):
                        c[round(sp["size"], 2)] += 1
        d.close()
    except Exception as e:
        return False, f"无法读取字号：{e}"
    if not c:
        return False, "PDF 里没有可读文字"
    body = c.most_common(1)[0][0]
    if body + 1e-6 < min_pt:
        return False, (f"正文实测 {body}pt < {min_pt}pt —— 页面被 Chrome 整体缩放。"
                       f"（多半是版心被某个不可断的长串撑破 A4 可打印宽度；"
                       f"查法：在 HTML 的 @media print 里给版心补 overflow-wrap:anywhere）")
    return True, f"正文 {body}pt（未缩放）"


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 A4 PDF（Chrome headless 排版）")
    ap.add_argument("--html", type=Path, default=None,
                    help="输入 HTML（缺省：从书目录推断书名后取同名 .html）")
    ap.add_argument("--out", type=Path, default=None, help="输出 PDF（缺省：同名 .pdf）")
    ap.add_argument("--budget", type=int, default=20000, help="Chrome virtual-time-budget（毫秒）")
    ap.add_argument("--timeout", type=int, default=300,
                    help="等产物稳定的上限秒数（判据是产物稳定，不是进程退出）")
    ap.add_argument("--keep-temp", action="store_true", help="保留临时 profile")
    a = ap.parse_args()

    if a.html is None:
        name = guess_book_name(ROOT)
        a.html = ROOT / f"{name}.html"
        if a.out is None:
            a.out = ROOT / f"{name}.pdf"
    elif a.out is None:
        a.out = a.html.with_suffix(".pdf")

    if not a.html.is_file():
        print(f"ERROR 找不到输入 HTML：{a.html}\n  先跑 build_html.py", file=sys.stderr)
        return 2

    chrome = find_chrome()
    profile = tempfile.mkdtemp(prefix="chrome-pdf-")

    # ⚠️ 必须先删掉旧产物（2026-09-20 实测的静默陈旧输出事故）：
    #   下面的"产物稳定"判定只看「磁盘上的文件体积连续两次不变」，**不校验这份文件
    #   是不是本次 Chrome 写的**。而 Chrome 对 135 页的书要跑约 30 秒，远超 3 秒的
    #   判定窗 —— 于是只要输出路径上还留着上一轮的 PDF，脚本就会在 3 秒时把它当成
    #   "本次产物"返回，**静默交付一份陈旧 PDF**。本轮实测：连续两次 build_pdf 都
    #   返回了旧的已盖章 PDF（7.4MB），靠 stamp_page_numbers 的防呆才暴露。
    #   删掉之后再跑，判定只能命中本次新写的文件，这类静默失败就不存在了。
    if a.out.exists():
        a.out.unlink()

    # 关掉会污染 PDF 或拖慢渲染的东西
    cmd = [
        chrome,
        "--headless=new",
        "--no-sandbox",                              # 本机必需（沙箱环境）
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--hide-scrollbars",
        "--no-pdf-header-footer",                    # 页码我们自己盖
        "--run-all-compositor-stages-before-draw",
        f"--virtual-time-budget={a.budget}",
        f"--user-data-dir={profile}",
        f"--print-to-pdf={a.out.resolve()}",
        a.html.resolve().as_uri(),                   # 中文路径必须走 URI 编码
    ]

    print(f"Chrome：{chrome}")
    print(f"输入：{a.html.name}")

    # 为什么不用 subprocess.run(capture_output=True)：
    # Chrome 会派生一堆子进程，它们**继承 stdout/stderr**。主进程退出后子进程还活着，
    # 管道读端就永远等不到 EOF —— 表现为"命令卡住不返回"，而 PDF 其实早就写好了。
    #
    # 更不能等 p.wait()：**本机实测 Chrome 写完 PDF 后主进程可能一直不退出**
    # （PDF 在 30 秒时就写好了，进程挂了 10 分钟以上，最后要靠手动 kill）。
    # 所以判据是**产物本身稳定**，不是"进程退出"：
    #   PDF 存在 + 体积连续 2 次不变 + 能被 pymupdf 打开 → 认定写完，立刻整组收尾。
    errlog = Path(tempfile.mkstemp(prefix="chrome-pdf-", suffix=".log")[1])
    with open(errlog, "w") as errf:
        p = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=errf, stdin=subprocess.DEVNULL,
            start_new_session=True,          # 自成进程组，便于整组收尾
        )
        pgid = os.getpgid(p.pid)
        deadline = time.time() + a.timeout
        last_size, stable, wrote = -1, 0, False
        # 为什么从 2 次（3 秒）改成 10 次（15 秒）：Chrome 是分块写盘的，过程中会有
        # 秒级停顿。3 秒判定窗会在它还没写完时判"写完"，紧接着的 killpg 就把渲染中的
        # Chrome 拦腰截断 —— 2026-09-21 实测产出 116 页（应为 136）的 PDF，C.4/C.5 整段消失。
        STABLE_NEEDED = 10
        while time.time() < deadline:
            if p.poll() is not None and a.out.is_file() and a.out.stat().st_size > 10_000:
                wrote = True                     # Chrome 自己退了 → 它必定已写完
                break
            if a.out.is_file():
                sz = a.out.stat().st_size
                if sz == last_size and sz > 10_000:
                    stable += 1
                    if stable >= STABLE_NEEDED:   # 1.5s × 10 = 15 秒不变才兜底认定
                        wrote = True
                        break
                else:
                    stable = 0
                last_size = sz
            time.sleep(1.5)
        if not wrote:
            print(f"ERROR Chrome 在 {a.timeout} 秒内未产出稳定 PDF", file=sys.stderr)
        # ⚠️ 阶段二：产物"看起来稳定"之后，**不许马上 kill**。
        # Chrome 在"布局 → 写盘"之间有一段很长的不写盘窗口（本项目实测 2 分钟以上）：
        # 只看体积稳定，会在它还没写完时判"完成"，紧接着 killpg 就产出截断或缩放的 PDF
        # ——而残留进程若侥幸跑完，会在脚本返回之后才把完整版写出来（实测：脚本读到
        # 116 页，两分钟后磁盘上的文件变成 131 页）。**它自己退出，才是"写完"唯一可靠的信号**，
        # 所以这里继续等它退，等到 deadline 为止。
        while time.time() < deadline and p.poll() is None:
            time.sleep(1.0)
        try:
            p.wait(timeout=10)
        except Exception:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
    err_text = errlog.read_text(errors="replace") if errlog.is_file() else ""
    errlog.unlink(missing_ok=True)

    if not a.keep_temp:
        shutil.rmtree(profile, ignore_errors=True)

    if not wrote or not a.out.is_file() or a.out.stat().st_size < 10_000:
        print("ERROR Chrome 未产出有效 PDF", file=sys.stderr)
        print(err_text[-800:] or "(无 stderr)", file=sys.stderr)
        return 1

    size_mb = a.out.stat().st_size / 1024 / 1024
    pages = "?"
    try:
        import pymupdf
        d = pymupdf.open(a.out)
        pages = d.page_count
        w, h = d[0].rect.width, d[0].rect.height
        d.close()
        rect = f"{w:.0f}×{h:.0f}"
    except Exception:
        rect = "?"

    # 完整性闸门（2026-09-21 加，截断事故的直接产物）：
    # 判定"产物写好了"曾经只证明"它诞生于本次运行"，没证明"它是完整的"。
    # 现在补上：PDF 末几页必须含 HTML 的最后一个标题，不含就报错退出——
    # 截断的 PDF 不许以"✅"的姿态流出去。
    ok_verify, why = verify_pdf_complete(a.out, last_headings(a.html))
    ok_scale, why_scale = verify_pdf_scale(a.out)      # 缩放闸门：内容完整 ≠ 排版正确
    if not ok_verify or not ok_scale:
        for w in (why if not ok_verify else None, why_scale if not ok_scale else None):
            if w:
                print(f"ERROR 闸门未通过：{w}", file=sys.stderr)
        print(f"  产物：{a.out.name} · {pages} 页 · {size_mb:.1f} MB —— 未过闸门，请勿使用", file=sys.stderr)
        print("  多半是 Chrome 渲染被提前终止或版心被撑破：可加大 --timeout / --budget 后重跑。", file=sys.stderr)
        return 1

    print(f"✅ PDF 已生成：{a.out.name}")
    print(f"   {pages} 页 · {rect} pt · {size_mb:.1f} MB")
    print(f"   闸门：{why} · {why_scale}")
    if rect != "?" and abs(float(rect.split('×')[0]) - 595) > 2:
        print(f"   ⚠️ 页面宽度不是 A4（595pt）——检查 HTML 里的 @page 规则")
    print("   下一步：add_pdf_bookmarks.py（书签）→ stamp_page_numbers.py（页码）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
