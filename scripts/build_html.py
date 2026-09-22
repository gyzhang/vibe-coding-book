#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把合并后的 markdown 书稿构建为单文件、离线可读、SVG 内联的 HTML 阅读版。

用法:  python3 scripts/build_html.py   （在任意目录下都可运行）
输入:  Vibe Coding 时代的我们（2026·V3.2）.md  +  svg/*.svg
输出:  Vibe Coding 时代的我们（2026·V3.2）.html   （自包含，无外部依赖）
"""
import base64
import glob
import io
import os
import re
import string
import sys
import html as H

import markdown

# ---------------------------------------------------------------- 0. 命令行开关（2026-09-22）
# 默认 = **在线阅读版**：字体外链到书站的 /site/fonts/，HTML 保持轻量。
# --embed-fonts = **下载版**：字体 base64 内嵌，单文件自包含、离线可读（约 1.3 MB）。
# 两个产物只差字体的携带方式，正文完全同源（同一份 md + svg）。
EMBED_FONTS = "--embed-fonts" in sys.argv
FONT_URL_BASE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--font-url=")),
                     "/site/fonts")

# scripts/ 的上一级 = 书目录（与调用时的 cwd 无关）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_NAME = "Vibe Coding 时代的我们（2026·V3.2）"
MD_NAME = BASE_NAME + ".md"
OUT_NAME = BASE_NAME + ".html"                    # 在线阅读版（字体外链）
OUT_NAME_OFFLINE = BASE_NAME + "（离线版）.html"   # 下载版（字体内嵌）
MD = os.path.join(ROOT, MD_NAME)
SVGDIR = os.path.join(ROOT, "svg")
OUT = os.path.join(ROOT, OUT_NAME_OFFLINE if EMBED_FONTS else OUT_NAME)

# ---------------------------------------------------------------- 0.1 字体策略
# **只用免费商用字体**。中文 = 思源黑体（本机 1.x 版为 Apache-2.0，2.0 起为 SIL OFL 1.1）
# 禁用：微软雅黑（著作权归方正，商业发布需单独授权）、苹方 / Menlo（Apple）等系统字体的对外发布用法。
# 详见 webooks/deploy/typography.md。
WEBFONT_FAMILY = "Book Sans"          # @font-face 注册名：与具体字体文件解耦，换字体只改这里
WEBFONT_MONO = "Book Mono"            # 代码用等宽字体（JetBrains Mono，OFL）
FONT_STACK = ('"%s","Source Han Sans CN","思源黑体 CN",-apple-system,BlinkMacSystemFont,'
              '"PingFang SC","Hiragino Sans GB",sans-serif') % WEBFONT_FAMILY
# 代码：Book Mono 优先；**中文由思源黑体承接**（JetBrains Mono 不含汉字），再落到系统等宽
FONT_MONO_STACK = ('"%s","Source Han Sans CN","思源黑体 CN",ui-monospace,SFMono-Regular,'
                   'Consolas,"Liberation Mono",monospace') % WEBFONT_MONO
# (family, 外链文件名键, 字体文件, 字重) —— key 必须与 webooks/scripts/make_webfonts.py 产出的文件名一致
SUBSET_SOURCES = [(WEBFONT_FAMILY, "book-sans-regular", "SourceHanSansCN-Regular.otf", 400),
                  (WEBFONT_FAMILY, "book-sans-bold", "SourceHanSansCN-Bold.otf", 700),
                  (WEBFONT_MONO, "book-mono-regular", "JetBrainsMono-Regular.ttf", 400),
                  (WEBFONT_MONO, "book-mono-bold", "JetBrainsMono-Bold.ttf", 700)]

BOOK_TITLE = "Vibe Coding 时代的我们"
BOOK_SUB = "AI 编码的能与不能"
DRAFT_LINE = "V3.2 秋分 · 2026 年 9 月 23 日"   # 兜底值；正常由封面块里的版本行覆盖
DRAFT_CHIP = "V3.2 秋分"                          # 顶栏徽章的短版（= 版本号部分）
AUTHOR_LINE = "Kevin + AI"

# 版本行的识别判据 —— **必须用正向特征（版本号模式），不能靠字样**（2026-09-22 实测）：
# 把「V3 草稿」改成「V3.2 秋分」后，旧的 `if "草稿" in txt` 判据立即失效 → 版本行被误判成副标题、
# DRAFT_LINE 静默回退到硬编码旧值 → HTML 与 md/PDF 不同源。**认行和取文件一样，都不许用排除法。**
VER_RE = re.compile(r"^V\d+(?:\.\d+)?(?:[\s·]|$)")

# ---------------------------------------------------------------- 1. 读入并剥离封面块
src = open(MD, encoding="utf-8").read()

m = re.match(r'\s*<div align="center">(.*?)</div>\s*', src, re.S)
cover_inner = m.group(1) if m else ""
src = src[m.end():] if m else src

if cover_inner:
    t = re.search(r'^#\s*(.+)$', cover_inner, re.M)
    if t:
        BOOK_TITLE = t.group(1).strip()
    _sub_taken = False
    for line in cover_inner.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("**") and line.endswith("**"):
            txt = line.strip("*").strip()
            if VER_RE.match(txt):
                DRAFT_LINE = txt
                DRAFT_CHIP = txt.split("·")[0].strip()
            elif not _sub_taken:      # 第一个加粗行 → 副标题（不再硬编码书名）
                BOOK_SUB = txt
                _sub_taken = True
            continue
        if not line.startswith("<") and not line.startswith("#"):
            AUTHOR_LINE = line

if cover_inner and not VER_RE.match(DRAFT_LINE):
    raise SystemExit(
        "❌ 封面块里没识别出版本行（形如「V3.2 秋分 · 2026 年 9 月 23 日」的加粗行）。\n"
        "   判据是「行首为版本号 V<数字>」——不要靠字样（如『草稿』）猜，否则会静默回退到\n"
        "   脚本里的兜底值，造成 HTML 与 md/PDF 不同源。请检查 parts/00-front.md 的封面块。")

# ---------------------------------------------------------------- 2. markdown -> html
md = markdown.Markdown(
    extensions=["tables", "fenced_code", "sane_lists", "toc"],
    extension_configs={"toc": {"toc_depth": "1-3", "anchorlink": False}},
)
body = md.convert(src)

# ---------------------------------------------------------------- 3. 图片 -> 内联 SVG
def inline_svg(name):
    s = open(os.path.join(SVGDIR, name + ".svg"), encoding="utf-8").read()
    s = re.sub(r'\s*data-pptx-[a-z-]+="[^"]*"', '', s)
    s = s.replace(' width="1280" height="720"', '')
    s = s.replace(' width="1200" height="1800"', '')
    slug = name.replace('_', '-')
    # 图内分组 id 在 39 张图之间大量重名（fig-header / fig-conclusion / fig-table …）。
    # 内联前一律加图名前缀；已确认全部 SVG 内不存在 url(#...) 引用，重命名安全。
    s = re.sub(r'id="([^"]+)"',
               lambda mm: 'id="%s__%s"' % (slug, mm.group(1)), s)
    s = s.replace("<svg ", '<svg role="img" aria-label="%s" ' % name, 1)
    return s


def caption_for(name, alt):
    # 正文中的 alt 通常已含"图 N-M"前缀，先剥掉，避免图注出现"图 1-1　图 1-1 …"
    alt = re.sub(r'^\s*图\s*\d+-\d+\s*', '', alt).strip()
    mm = re.match(r'fig-(\d+)-(\d+)-', name)
    if mm:
        return "图 %s-%s　%s" % (mm.group(1), mm.group(2), alt)
    mm = re.match(r'fig-e-(\d+)-', name)
    if mm:
        return "图 E-%s　%s" % (mm.group(1), alt)
    return alt


FIG_ORDER = []


def repl_img(mm):
    name, alt = mm.group(1), mm.group(2)
    if name.startswith("cover"):
        return ""  # 封面已单独处理
    cap = caption_for(name, alt)
    FIG_ORDER.append((name, cap))
    return (
        '\n<figure class="fig" id="%s">'
        '<div class="fig-inner" title="点击放大">%s</div>'
        '<figcaption><span class="fignum">%s</span></figcaption>'
        '</figure>\n'
    ) % (name, inline_svg(name), H.escape(cap))


body = re.sub(r'<img src="images/([\w\-.]+)\.png"[^>]*?alt="([^"]*)"[^>]*?/?>', repl_img, body)
body = re.sub(r'<p>\s*</p>', '', body)

# ---------------------------------------------------------------- 4. 表格包裹（便于横向滚动）
body = body.replace('<table>', '<div class="tbl"><table>').replace('</table>', '</table></div>')

# ---------------------------------------------------------------- 5. 标题加类名
def add_cls(mm):
    lvl, hid, text = mm.group(1), mm.group(2), mm.group(3)
    plain = re.sub(r'<[^>]+>', '', text)
    cls = "h-sub" if lvl == "3" else ("h-sec" if lvl == "2" else "h-part")
    if lvl == "1":
        if re.match(r'^第\s*\d+\s*章', plain):      # 本书章号是阿拉伯数字：第 1 章
            cls = "h-chap"
        elif re.match(r'^第[一二三四五六七八九十]+篇', plain):
            cls = "h-part"
    return '<h%s class="%s" id="%s">%s</h%s>' % (lvl, cls, hid, text, lvl)


body = re.sub(r'<h([123]) id="([^"]+)">(.*?)</h\1>', add_cls, body, flags=re.S)

# 5.1 重写为可读锚点（默认 slug 会把中文全部丢掉，产生 #13 这类无意义 id）
def slug_of(text):
    t = re.sub(r'<[^>]+>', '', text).replace('&amp;', '&')
    t = re.sub(r'[^\w\u4e00-\u9fff.]', '', t, flags=re.UNICODE)
    return t.lower() or 'h'


_seen = {}


def reid(mm):
    lvl, cls, text = mm.group(1), mm.group(2), mm.group(4)
    base = slug_of(text)
    n = _seen.get(base, 0)
    _seen[base] = n + 1
    new = base if n == 0 else '%s-%d' % (base, n)
    return '<h%s class="%s" id="%s">%s</h%s>' % (lvl, cls, new, text, lvl)


body = re.sub(r'<h([123]) class="([^"]+)" id="([^"]+)">(.*?)</h\1>', reid, body, flags=re.S)

# ---------------------------------------------------------------- 6. 目录
heads = re.findall(r'<h([123]) class="([^"]+)" id="([^"]+)">(.*?)</h\1>', body, re.S)
toc_items = []
for lvl, cls, hid, text in heads:
    plain = re.sub(r'<[^>]+>', '', text).strip()
    plain = plain.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    toc_items.append((int(lvl), cls, hid, plain))

toc_html = "\n".join(
    '<a class="toc-l%d %s" href="#%s" data-t="%s">%s</a>'
    % (lvl, cls, hid, H.escape(plain.lower(), quote=True), H.escape(plain))
    for lvl, cls, hid, plain in toc_items
)

fig_index = "\n".join(
    '<a class="toc-fig" href="#%s">%s</a>' % (n, H.escape(c)) for n, c in FIG_ORDER
)

# ---------------------------------------------------------------- 7. 统计
plain_all = re.sub(r'<[^>]+>', '', re.sub(r'<figure.*?</figure>', '', body, flags=re.S))
cj = len(re.findall(r'[\u4e00-\u9fff]', plain_all))
total_chars = len(re.sub(r'\s', '', plain_all))
# 不提供"预计阅读时间"：那是一个用虚构速度除出来的数字。
# 一本有大量代码、表格与配图的技术书，阅读速度因人因章差异极大，任何单一数字都是假的。
# 改为给读者一个可数的结构事实：正文里有多少段代码、多少张表。
n_code = body.count('<pre>')
n_table = body.count('<table>')

CSS = """
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth;--sw:312px;--cw:880px}
html[data-theme="paper"]{
  --bg:#F4F7FB; --paper:#FFFFFF; --panel:#F7FAFD; --side:#F7FAFD;
  --ink:#1F2937; --ink-soft:#475569; --head:#0C2864; --head2:#2064AE;
  --muted:#7B8CA0; --line:#E3EBF4; --accent:#08A6F6;
  --code-bg:#F4F8FC; --code-line:#E0E9F3; --th-bg:#0C2864; --th-ink:#fff;
  --quote-bg:#F1F6FC; --fig-bg:#FFFFFF; --shadow:0 1px 2px rgba(12,40,100,.05),0 8px 24px rgba(12,40,100,.06);
}
html[data-theme="night"]{
  --bg:#0C1119; --paper:#131B26; --panel:#18222F; --side:#141C27;
  --ink:#D8E1EC; --ink-soft:#AAB8C8; --head:#9CC2EE; --head2:#7FB2E8;
  --muted:#8494A8; --line:#26323F; --accent:#3FB6F5;
  --code-bg:#19222E; --code-line:#26323F; --th-bg:#1E2C3F; --th-ink:#DCE6F2;
  --quote-bg:#17222F; --fig-bg:#FFFFFF; --shadow:0 1px 2px rgba(0,0,0,.3),0 8px 24px rgba(0,0,0,.35);
}
body{
  margin:0;background:var(--bg);color:var(--ink);
  /* 字体策略见文件头 §0.1：webfont（思源黑体，免费商用）优先，系统字体兜底；
     不使用微软雅黑 —— 其著作权归方正，商业发布需单独授权。 */
  font-family:__FONT_STACK__;
  font-size:var(--fs,17px);line-height:1.95;letter-spacing:.012em;
  text-rendering:optimizeLegibility;
}
/* ---------- 顶栏 ---------- */
#topbar{
  position:fixed;left:0;right:0;top:0;height:52px;z-index:60;
  display:flex;align-items:center;gap:12px;padding:0 16px;
  background:color-mix(in srgb,var(--paper) 88%,transparent);
  backdrop-filter:saturate(160%) blur(10px);
  border-bottom:1px solid var(--line);
}
#topbar .tb-title{font-weight:700;color:var(--head);font-size:.95em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* 书名 = 本书的 home（惯例：左上角站名点击回首页）。**只包书名，不包版本徽章**——
   徽章是元信息，点它跳到封面属于"点了没预期的地方"。
   下划线用 text-decoration 的透明色，而不是 border-bottom：后者会给行盒加 1px，悬停时标题上下抖一下。 */
#topbar .tb-home{color:inherit;text-decoration:underline transparent;text-underline-offset:3px;
  transition:color .15s,text-decoration-color .15s;cursor:pointer}
#topbar .tb-home:hover,#topbar .tb-home:focus-visible{color:var(--head2);text-decoration-color:currentColor}
#topbar .tb-home:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:.72em;font-weight:700;
  background:#DF203C;color:#fff;letter-spacing:.06em;vertical-align:2px}
.tb-tools{margin-left:auto;display:flex;gap:6px;align-items:center}
.tb-tools button{
  font:inherit;font-size:.8em;padding:5px 10px;border-radius:8px;cursor:pointer;
  border:1px solid var(--line);background:var(--panel);color:var(--ink-soft);
  transition:.15s;
}
.tb-tools button:hover{border-color:var(--head2);color:var(--head2)}
#menuBtn{display:none}
/* ---------- 进度条 ---------- */
#progress{position:fixed;top:0;left:0;height:3px;width:0;z-index:80;
  background:linear-gradient(90deg,#2064AE,#08A6F6);transition:width .1s linear}
/* ---------- 侧栏 ---------- */
#side{
  position:fixed;left:0;top:52px;bottom:0;width:var(--sw);z-index:50;
  background:var(--side);border-right:1px solid var(--line);
  overflow-y:auto;overscroll-behavior:contain;padding:14px 10px 60px;
}
#side::-webkit-scrollbar{width:8px}
#side::-webkit-scrollbar-thumb{background:var(--line);border-radius:8px}
#q{
  width:100%;font:inherit;font-size:.82em;padding:7px 10px;margin-bottom:10px;
  border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--ink);
}
#q:focus{outline:2px solid var(--accent);outline-offset:-1px}
#nav{display:flex;flex-direction:column;gap:1px}
#nav a{
  display:flex;align-items:center;gap:2px;text-decoration:none;color:var(--ink-soft);border-radius:7px;
  padding:5px 9px;font-size:.83em;line-height:1.55;border-left:3px solid transparent;
}
#nav a .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#nav a:hover{background:var(--panel);color:var(--head2)}
#nav a.h-part{font-weight:700;color:var(--head);margin-top:9px;letter-spacing:.04em}
#nav a.h-chap{font-weight:700;color:var(--head);margin-top:9px}
#nav a.toc-l2{padding-left:20px}
#nav a.toc-l3{padding-left:32px;font-size:.79em;color:var(--muted)}
#nav a.active{background:var(--panel);border-left-color:var(--accent);color:var(--head2);font-weight:600}
#side details{margin-top:16px;border-top:1px solid var(--line);padding-top:10px}
#side summary{cursor:pointer;font-size:.8em;font-weight:700;color:var(--head2);padding:4px 2px}
#side .toc-fig{display:block;font-size:.77em;color:var(--muted);text-decoration:none;padding:3px 9px;border-radius:6px}
#side .toc-fig:hover{background:var(--panel);color:var(--head2)}
/* ---------- 正文 ---------- */
#wrap{margin-left:var(--sw);padding:52px 0 0}
main{
  max-width:var(--cw);margin:0 auto;background:var(--paper);
  padding:44px 56px 120px;min-height:100vh;box-shadow:var(--shadow);
}
h1,h2,h3{line-height:1.4;letter-spacing:.01em}
h1.h-part{
  font-size:1.55em;margin:2.6em 0 1.1em;padding:14px 20px;border-radius:10px;
  background:linear-gradient(100deg,#0C2864,#2064AE);color:#fff;
}
h1.h-chap{
  font-size:1.72em;margin:2.2em 0 1em;padding-bottom:.45em;color:var(--head);
  border-bottom:2px solid var(--head);
}
h1.h-part:first-of-type,h1.h-chap:first-of-type{margin-top:.6em}
h2.h-sec{
  font-size:1.22em;margin:2.2em 0 .7em;padding-left:12px;color:var(--head2);
  border-left:4px solid var(--accent);
}
h3.h-sub{font-size:1.05em;margin:1.8em 0 .5em;color:var(--head)}
h1,h2,h3{scroll-margin-top:70px}
p{margin:0 0 1.05em}
strong{font-weight:700;color:var(--head)}
em{font-style:normal;color:var(--ink-soft)}
a{color:var(--head2)}
hr{border:0;border-top:1px solid var(--line);margin:2.6em 0}
ul,ol{margin:0 0 1.1em;padding-left:1.5em}
li{margin:.3em 0}
blockquote{
  margin:1.4em 0;padding:14px 20px;background:var(--quote-bg);
  border-left:4px solid var(--head2);border-radius:0 8px 8px 0;color:var(--head);
}
blockquote p{margin:0}
blockquote p+p{margin-top:.5em}
code{
  /* 代码字体：Book Mono（JetBrains Mono，OFL 免费商用）；代码里的中文由思源黑体承接 */
  font-family:__FONT_MONO__;
  font-size:.87em;background:var(--code-bg);border:1px solid var(--code-line);
  border-radius:4px;padding:.1em .36em;color:var(--head2);
}
pre{
  background:var(--code-bg);border:1px solid var(--code-line);border-radius:10px;
  padding:14px 16px;overflow-x:auto;margin:1.3em 0;
}
pre code{background:none;border:0;padding:0;color:var(--ink-soft);font-size:.83em;line-height:1.75;white-space:pre}
.tbl{overflow-x:auto;margin:1.3em 0;border:1px solid var(--line);border-radius:10px}
.tbl table{border-collapse:collapse;width:100%;font-size:.87em;line-height:1.7}
.tbl th{background:var(--th-bg);color:var(--th-ink);text-align:left;padding:9px 12px;font-weight:600;white-space:nowrap}
.tbl td{padding:9px 12px;border-top:1px solid var(--line);vertical-align:top}
.tbl tr:nth-child(even) td{background:var(--code-bg)}
.tbl td:first-child{color:var(--head);font-weight:600}
/* ---------- 配图 ---------- */
figure.fig{margin:2em 0;padding:0}
.fig-inner{
  background:var(--fig-bg);border:1px solid var(--line);border-radius:12px;
  overflow:hidden;cursor:zoom-in;transition:.18s;
}
.fig-inner:hover{box-shadow:var(--shadow);transform:translateY(-1px);border-color:var(--head2)}
.fig-inner svg{display:block;width:100%;height:auto}
html[data-theme="night"] .fig-inner{filter:invert(1) hue-rotate(180deg)}
figcaption{margin-top:.6em;text-align:center;font-size:.8em;color:var(--muted);line-height:1.6}
figcaption .fignum{color:var(--head2);font-weight:600}
/* ---------- 封面与题头 ---------- */
/* 书名的 home 锚点落在封面上（#cover = .hero）。scroll-margin-top 要取一个**大于**封面距文档
   顶端距离的值：浏览器把算出的负滚动位置夹到 0，于是点书名回到的是**真正的第一页**
   （scrollTop=0，顶栏正常悬在上面），而不是把封面顶部塞进固定顶栏底下。
   封面距文档顶端：桌面 52+44=96px / 窄屏(≤1080px) 52+28=80px / 沉浸态 12+44=56px。取 200 即可全命中。 */
#cover{scroll-margin-top:200px}
.hero{
  border:1px solid var(--line);border-radius:14px;overflow:hidden;margin:0 0 26px;
  background:var(--panel);
}
/* 封面双轨（2026-09-20 作者裁定"换横版头图"）：屏幕用裁剪版头图（viewBox 0 0 1200 560，
   只取深蓝主视觉区），撑满版心、无两侧空白；竖版全图（1200×1800）保留给打印/PDF 第一页。
   两个容器同源（同一份 cover.svg），只是 viewBox 不同——改封面只改 svg/cover.svg 一处。 */
.hero .hero-cv-web{padding:0}
.hero .hero-cv-web svg{display:block;width:100%;height:auto}
.hero .hero-cv-print{display:none}
html[data-theme="night"] .hero .hero-cv-web,
html[data-theme="night"] .hero .hero-cv-print{filter:invert(1) hue-rotate(180deg)}
.hero-meta{padding:0 22px 20px;text-align:center}
/* 书名在屏幕态由头图承载（2026-09-20 作者裁定），hero-meta 隐藏 h1——但打印题名页仍需要，
   base 规则对打印同样生效，所以 @media print 里显式恢复 h1。
   副题"AI 编码的能与不能"曾上移进头图（同日），因"白大字→淡蓝小字→白次大字"的亮度
   三明治刺眼而撤回：留在浅底首行、深色字，成为头图之后的下一个视线落点。 */
.hero-meta h1{display:none}
.hero-meta .sub{font-size:1.16em;color:var(--head);font-weight:700;margin:.1em 0 .5em}
.hero-meta .ver{font-size:.85em;color:var(--muted);margin:0}
.draftbar{
  margin:0 0 22px;padding:12px 16px;border-radius:10px;font-size:.85em;
  background:#FDF3E3;border:1px solid #E8C79A;color:#8A5A0B;
}
html[data-theme="night"] .draftbar{background:#2A2113;border-color:#5A451F;color:#E2B96B}
.stats{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin:14px 0 0}
.stats span{font-size:.78em;color:var(--ink-soft);background:var(--paper);border:1px solid var(--line);
  padding:3px 10px;border-radius:999px}
.hint{font-size:.8em;color:var(--muted);margin:12px 0 0;line-height:1.7}
/* ---------- 放大层 ---------- */
#lb{
  position:fixed;inset:0;z-index:100;display:none;background:rgba(6,14,26,.93);
  overflow:auto;padding:56px 24px;
}
#lb.on{display:block}
#lb .lb-in{width:min(94vw,1500px);margin:0 auto}
#lb svg{display:block;width:100%;height:auto;background:#fff;border-radius:10px}
#lb .lb-cap{color:#CBD9E8;text-align:center;font-size:.85em;margin-top:12px}
#lb .lb-x{
  position:fixed;top:14px;right:18px;width:38px;height:38px;border-radius:50%;
  border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.1);color:#fff;
  font-size:19px;line-height:1;cursor:pointer;
}
#toTop{
  position:fixed;right:20px;bottom:24px;z-index:70;display:none;
  width:42px;height:42px;border-radius:50%;cursor:pointer;
  border:1px solid var(--line);background:var(--paper);color:var(--head2);
  box-shadow:var(--shadow);font-size:16px;
}
#toTop.on{display:block}
#mask{position:fixed;inset:0;background:rgba(6,14,26,.5);z-index:45;display:none}
#mask.on{display:block}
/* ---------- 侧栏收起 / 沉浸阅读 ---------- */
#side{transition:transform .22s ease}
#side.off{transform:translateX(-100%)}
#wrap{transition:margin-left .22s ease,padding-top .22s ease}
#wrap.full{margin-left:0}
#topbar{transition:transform .25s ease}
html.immersive #topbar{transform:translateY(-100%)}
html.immersive #side{transform:translateX(-100%)}
html.immersive #wrap{margin-left:0;padding-top:12px}
html.immersive h1,html.immersive h2,html.immersive h3{scroll-margin-top:20px}
#nav a.toc-l1{padding-right:8px}
#nav .caret{
  flex:none;display:inline-flex;align-items:center;justify-content:center;
  width:24px;height:24px;margin:0 -4px 0 2px;border-radius:7px;
  color:var(--muted);cursor:pointer;user-select:none;
  transition:transform .18s,background .15s,color .15s,box-shadow .15s;
}
#nav .caret svg{display:block}
#nav .caret:hover{background:color-mix(in srgb,var(--head2) 14%,transparent);color:var(--head2);box-shadow:0 0 0 1px color-mix(in srgb,var(--head2) 25%,transparent)}
#nav .toc-group.folded .caret{transform:rotate(-90deg);color:var(--ink-soft)}
#nav .toc-group.folded .toc-subs{display:none}
#nav .toc-subs .toc-subs{margin-left:10px;border-left:1px solid var(--line);padding-left:4px}
.nav-tools{display:flex;gap:6px;margin:0 0 10px}
.nav-tools button{
  flex:1;font:inherit;font-size:.76em;padding:5px 8px;border-radius:8px;cursor:pointer;
  border:1px solid var(--line);background:var(--paper);color:var(--ink-soft);
  display:inline-flex;align-items:center;justify-content:center;gap:5px;
  transition:.15s;
}
.nav-tools button:hover{border-color:var(--head2);color:var(--head2)}
.nav-tools button svg{flex:none}
/* ---------- 侧栏宽度拖动 ---------- */
#resize{
  position:fixed;top:52px;bottom:0;left:var(--sw);width:8px;margin-left:-4px;z-index:52;
  cursor:col-resize;touch-action:none;
}
#resize::after{
  content:"";position:absolute;left:50%;top:0;bottom:0;width:2px;margin-left:-1px;
  background:transparent;transition:background .15s;
}
#resize:hover::after,#resize.on::after{background:var(--accent)}
#imExit{
  position:fixed;top:12px;right:16px;z-index:65;display:none;
  font:inherit;font-size:.8em;padding:6px 12px;border-radius:999px;cursor:pointer;
  border:1px solid var(--line);background:var(--paper);color:var(--ink-soft);
  box-shadow:var(--shadow);opacity:.4;transition:.2s;
}
#imExit:hover{opacity:1;color:var(--head2);border-color:var(--head2)}
html.immersive #imExit{display:block}
/* ---------- 窄屏 ---------- */
@media (max-width:1080px){
  #menuBtn{display:inline-block}
  #side{transform:translateX(-100%);transition:transform .22s;top:0;padding-top:64px;z-index:55}
  #side.on{transform:none}
  #wrap{margin-left:0}
  main{padding:28px 20px 90px;box-shadow:none}
  .tb-title{font-size:.88em}
  #sideBtn{display:none}
  #resize{display:none}
}
/* ---------- 打印 / PDF ---------- */
@page{size:A4;margin:18mm 16mm}
@media print{
  #topbar,#side,#progress,#toTop,#lb,#mask,#q,#imExit,#resize{display:none!important}
  /* .hint 是**纯网页操作说明**（点击放大 / 右上角调字号 / 沉浸阅读 / Ctrl+P），
     PDF 里这些控件根本不存在，印出来对读者是噪音。2026-09-20 实测它在 PDF 第 1 页
     变成了一整段话，故在打印态隐藏。.draftbar 保留 —— 它是版本与口径说明，属于书的内容。 */
  .hint{display:none!important}
  html.immersive #wrap{margin:0;padding-top:0}
  #wrap{margin:0;padding:0}
  main{max-width:none;padding:0;box-shadow:none;background:#fff}
  body{background:#fff;font-size:10.5pt;line-height:1.7;
       -webkit-print-color-adjust:exact;print-color-adjust:exact}
  /* 长网址必须允许断行（2026-09-21 实测事故）：延伸阅读里一条 107 字符的裸 URL
     会把版心的 min-content 撑到 ~772px（大于 178mm 可打印宽度），Chrome 打印时
     整体缩放到 0.871 补偿——实测全书 136 页缩成 116 页、正文 10.5pt 缩成 9.15pt、
     章标题 18.06pt 缩成 15.73pt，书签闸门随之误报"HTML 130 / PDF 16"。
     anywhere 只影响"不可断的长 token"（URL / 标识符），中文本来就可断，无副作用；
     pre 除外——代码块有自己的滚动/换行策略，不许被这里改写。 */
  p,li,td,th,blockquote,figcaption{overflow-wrap:anywhere}
  pre{overflow-wrap:normal}
  h1.h-chap,h1.h-part,h2.h-sec{page-break-after:avoid}
  h1.h-part{page-break-before:always}
  h1.h-part:first-of-type{page-break-before:auto}
  figure.fig,.tbl,pre,blockquote{page-break-inside:avoid}
  .fig-inner:hover{box-shadow:none;transform:none}
  .draftbar{page-break-inside:avoid}
  /* 封面独立成页（2026-09-20 作者裁定"不要重复"）：打印用竖版全图 .hero-cv-print，
     屏幕用的横版头图 .hero-cv-web 在此隐藏。注意：父容器 .hero 必须放开
     page-break-inside:avoid，否则父规则会压住子元素的 page-break-after，分页静默失效。 */
  .hero{border:none;border-radius:0;overflow:visible}
  .hero .hero-cv-web{display:none}
  .hero .hero-cv-print{display:block;max-width:88%;margin:0 auto;padding:0;page-break-after:always}
  .hero-meta h1{display:block}   /* 恢复被屏幕态隐藏的书名——第 2 页题名页仍需要 */
  a{color:inherit;text-decoration:none}
}
"""

JS = """
(function(){
  var root=document.documentElement;
  var k='vibe-coding-v3';   // 独立键，避免与另一本书的设置互相覆盖
  var pendingFold=null;
  var pendingWide=false;
  try{
    var st=JSON.parse(localStorage.getItem(k)||'{}');
    if(st.fs) root.style.setProperty('--fs',st.fs+'px');
    if(st.theme) root.setAttribute('data-theme',st.theme);
    if(st.fold) pendingFold=st.fold;
    if(st.sw) root.style.setProperty('--sw',Math.round(st.sw)+'px');
    if(st.cw){
      root.style.setProperty('--cw',Math.round(st.cw)+'px');
      pendingWide=Math.round(st.cw)>=1000;
    }
  }catch(e){}
  function save(patch){
    var st={};
    try{st=JSON.parse(localStorage.getItem(k)||'{}');}catch(e){}
    for(var x in patch) st[x]=patch[x];
    try{localStorage.setItem(k,JSON.stringify(st));}catch(e){}
  }
  function curFs(){return parseFloat(getComputedStyle(root).getPropertyValue('--fs'))||17;}

  // ---------- 侧栏宽度拖动 ----------
  (function(){
    var rz=document.getElementById('resize');
    if(!rz)return;
    var drag=false;
    function setW(px){
      px=Math.min(560,Math.max(200,Math.round(px)));
      root.style.setProperty('--sw',px+'px');
      return px;
    }
    rz.addEventListener('pointerdown',function(e){
      drag=true;rz.classList.add('on');
      try{rz.setPointerCapture(e.pointerId);}catch(err){}
      e.preventDefault();
    });
    rz.addEventListener('pointermove',function(e){if(drag)setW(e.clientX);});
    rz.addEventListener('pointerup',function(e){
      if(!drag)return;
      drag=false;rz.classList.remove('on');
      save({sw:parseFloat(getComputedStyle(root).getPropertyValue('--sw'))||312});
    });
    rz.addEventListener('pointercancel',function(){drag=false;rz.classList.remove('on');});
    rz.addEventListener('dblclick',function(){setW(312);save({sw:312});});
  })();

  var bar=document.getElementById('progress');
  var toTop=document.getElementById('toTop');
  var side=document.getElementById('side');
  var mask=document.getElementById('mask');
  var navLinks=[].slice.call(document.querySelectorAll('#nav a'));
  var heads=[].slice.call(document.querySelectorAll('main h1[id],main h2[id],main h3[id]'));

  // ---------- 目录按篇/章/节多级折叠 ----------
  // 用栈构建嵌套分组：凡是有下级条目的层级（篇/章/节）都有折叠箭头。
  var groups=[];
  var CARET_SVG='<svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">'+
    '<path d="M3 5l4 4.2L11 5" fill="none" stroke="currentColor" stroke-width="2.2" '+
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';
  (function(){
    var stack=[]; // 保存当前各层级的打开分组
    navLinks.forEach(function(a){
      var lvl=a.classList.contains('toc-l1')?1:(a.classList.contains('toc-l2')?2:3);
      while(stack.length&&stack[stack.length-1].lvl>=lvl)stack.pop();
      var parentSubs=stack.length?stack[stack.length-1].subs:document.getElementById('nav');
      var g=document.createElement('div');g.className='toc-group';
      var subs=document.createElement('div');subs.className='toc-subs';
      parentSubs.appendChild(g);
      g.appendChild(a);g.appendChild(subs);
      if(lvl<3){
        stack.push({el:g,l1:a,subs:subs,lvl:lvl});
        groups.push({el:g,l1:a,subs:subs});
      }
    });
    // 只有真正拥有下级内容的组才配折叠箭头（如 2.2 无小节则不加）
    groups=groups.filter(function(g){
      if(!g.subs.firstChild)return false;
      var c=document.createElement('span');c.className='caret';c.title='展开 / 收起本组';
      c.innerHTML=CARET_SVG;
      g.l1.appendChild(c);
      return true;
    });
    // 单行省略号布局：把链接内文字包进 .t（flex:1 + ellipsis），箭头固定在右侧；
    // 完整标题放入 title，悬停可看全文。
    navLinks.forEach(function(a){
      var t=document.createElement('span');t.className='t';
      [].slice.call(a.childNodes).forEach(function(n){
        if(n.nodeType===1&&n.classList.contains('caret'))return;
        t.appendChild(n);
      });
      a.insertBefore(t,a.firstChild);
      var full=(t.textContent||'').replace(/\\s+/g,' ').trim();
      if(full)a.title=full;
    });
  })();
  function getFolds(){
    try{return (JSON.parse(localStorage.getItem(k)||'{}').fold)||{};}catch(e){return{};}
  }
  function setFold(g,fold,keep){
    var will=(fold===undefined)?!g.el.classList.contains('folded'):!!fold;
    g.el.classList.toggle('folded',will);
    if(!keep){
      var f=getFolds();
      f[g.l1.getAttribute('href')]=will;
      save({fold:f});
    }
  }
  // 事件委托：点击篇/章标题右侧的箭头折叠/展开该组。
  // 箭头内部是 SVG（path/svg 元素），e.target 不会是 .caret 本身，必须用 closest 向上找。
  document.getElementById('nav').addEventListener('click',function(e){
    var t=e.target;
    var c=t&&t.closest?t.closest('.caret'):null;
    if(c){
      e.preventDefault();e.stopPropagation();
      var grp=c.closest('.toc-group');
      if(grp)setFold({el:grp,l1:grp.querySelector('.toc-l1')});
    }
  });
  groups.forEach(function(g){
    if(pendingFold&&pendingFold[g.l1.getAttribute('href')])g.el.classList.add('folded');
  });
  // 全部展开 / 全部收起
  [].slice.call(document.querySelectorAll('.nav-tools button')).forEach(function(b){
    b.onclick=function(){
      var fold=b.getAttribute('data-navact')==='foldAll';
      var f=getFolds();
      groups.forEach(function(g){
        g.el.classList.toggle('folded',fold);
        f[g.l1.getAttribute('href')]=fold;
      });
      save({fold:f});
    };
  });

  function onScroll(){
    var st=window.scrollY||document.documentElement.scrollTop;
    var h=document.documentElement.scrollHeight-window.innerHeight;
    bar.style.width=(h>0?Math.min(100,st/h*100):0)+'%';
    toTop.className=st>700?'on':'';
    // 当前章节
    var idx=0;
    for(var i=0;i<heads.length;i++){
      if(heads[i].getBoundingClientRect().top<=90) idx=i; else break;
    }
    var id=heads[idx]?heads[idx].id:'';
    navLinks.forEach(function(a){
      if(a.getAttribute('href')==='#'+id){
        if(!a.classList.contains('active')){
          navLinks.forEach(function(b){b.classList.remove('active');});
          var pg=a.closest('.toc-group');
          if(pg&&pg.classList.contains('folded')&&!a.classList.contains('toc-l1'))setFold(pg,false,true);
          a.classList.add('active');
          var r=a.offsetTop-side.scrollTop;
          if(r<40||r>side.clientHeight-60) side.scrollTop=a.offsetTop-side.clientHeight*0.35;
        }
      }
    });
  }
  window.addEventListener('scroll',onScroll,{passive:true});
  window.addEventListener('resize',onScroll);
  onScroll();

  document.getElementById('toTop').onclick=function(){window.scrollTo({top:0,behavior:'smooth'});};
  document.getElementById('lb').onclick=function(e){
    if(e.target===this||e.target.className==='lb-x'){this.classList.remove('on');}
  };
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape'){
      document.getElementById('lb').classList.remove('on');
      side.classList.remove('on');mask.classList.remove('on');
      if(root.classList.contains('immersive'))exitIm();
    }
  });

  // 配图放大
  [].slice.call(document.querySelectorAll('figure.fig')).forEach(function(f){
    var inner=f.querySelector('.fig-inner');
    if(!inner) return;
    inner.onclick=function(){
      var lb=document.getElementById('lb');
      var cap=f.querySelector('figcaption');
      lb.innerHTML='<button class="lb-x">×</button><div class="lb-in">'+inner.innerHTML+
                   '</div><div class="lb-cap">'+(cap?cap.textContent:'')+'（按 Esc 关闭）</div>';
      lb.classList.add('on');
      lb.scrollTop=0;
    };
  });

  // 目录筛选
  var q=document.getElementById('q');
  q.addEventListener('input',function(){
    var v=q.value.trim().toLowerCase();
    navLinks.forEach(function(a){
      var hit=!v||(a.getAttribute('data-t')||'').indexOf(v)>=0;
      a.style.display=hit?'':'none';
    });
    groups.forEach(function(g){
      if(v){g.el.classList.remove('folded');}
      else if(getFolds()[g.l1.getAttribute('href')]){g.el.classList.add('folded');}
    });
  });

  // 工具按钮
  [].slice.call(document.querySelectorAll('.tb-tools button')).forEach(function(b){
    b.onclick=function(){
      var act=b.getAttribute('data-act');
      if(act==='font+'){var n=Math.min(22,curFs()+1);root.style.setProperty('--fs',n+'px');save({fs:n});}
      else if(act==='font-'){var n2=Math.max(14,curFs()-1);root.style.setProperty('--fs',n2+'px');save({fs:n2});}
      else if(act==='font0'){root.style.setProperty('--fs','17px');save({fs:17});}
      else if(act==='wide'){
        var cw=parseFloat(getComputedStyle(root).getPropertyValue('--cw'))||880;
        var wide=cw<1000;
        root.style.setProperty('--cw',(wide?1240:880)+'px');
        save({cw:wide?1240:880});
        b.textContent=wide?'标准':'加宽';
      }
      else if(act==='theme'){
        var t=root.getAttribute('data-theme')==='night'?'paper':'night';
        root.setAttribute('data-theme',t);save({theme:t});
        b.textContent=t==='night'?'日间':'夜读';
      }
      else if(act==='menu'){side.classList.toggle('on');mask.classList.toggle('on');}
      else if(act==='side'){
        if(window.matchMedia&&window.matchMedia('(max-width:1080px)').matches){
          side.classList.toggle('on');mask.classList.toggle('on');
        }else{
          var off=!side.classList.contains('off');
          side.classList.toggle('off',off);
          document.getElementById('wrap').classList.toggle('full',off);
          b.textContent=off?'展目录':'收目录';
          save({side:off?'off':'on'});
        }
      }
      else if(act==='im'){root.classList.contains('immersive')?exitIm():enterIm();}
    };
  });
  if(pendingWide){
    var wb=document.querySelector('.tb-tools [data-act="wide"]');
    if(wb)wb.textContent='标准';
  }

  // 点击目录后关闭移动端侧栏
  navLinks.forEach(function(a){
    a.onclick=function(){
      side.classList.remove('on');mask.classList.remove('on');
    };
  });
  mask.onclick=function(){side.classList.remove('on');mask.classList.remove('on');};

  // ---------- 沉浸阅读 ----------
  function enterIm(keep){
    root.classList.add('immersive');
    if(!keep)save({im:1});
  }
  function exitIm(keep){
    root.classList.remove('immersive');
    if(!keep)save({im:0});
  }
  document.getElementById('imExit').onclick=function(){exitIm();};
  if(st.im)enterIm(true);
  if(st.side==='off'&&!(window.matchMedia&&window.matchMedia('(max-width:1080px)').matches)){
    side.classList.add('off');
    document.getElementById('wrap').classList.add('full');
    var sb=document.getElementById('sideBtn');
    if(sb)sb.textContent='展目录';
  }

  // 打印时确保是日间主题
  window.addEventListener('beforeprint',function(){root.setAttribute('data-theme','paper');});
})();
"""

cover_full = inline_svg("cover")   # 竖版全图（1200×1800）：打印 / PDF 第一页
# 屏幕版头图：只取深蓝主视觉区（y=0..560，含顶部青条）。SVG 根元素默认 overflow:hidden，
# 改 viewBox 即完成裁剪。曾把中文副标题注入头图（2026-09-20），实测形成"白大字→淡蓝小字→
# 白次大字"的亮度三明治，刺眼，同日撤销：**副题回到浅底的 hero-meta 首行（深色字）**，
# 头图保持纯封面艺术（大标题 + 英文回声行 + 阶梯图），深底上的高亮文字只有一层主层。
# 防呆：viewBox 字符串对不上（封面被重画）就退回全图，绝不静默出一张空头图。
_web = cover_full.replace('viewBox="0 0 1200 1800"', 'viewBox="0 0 1200 560"', 1)
cover_web = _web if _web != cover_full else cover_full

SHELL = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="paper">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · __DRAFT__</title>
<meta name="description" content="__TITLE__ —— __SUB__。__AUTHOR__，__DRAFT__。">
<style>__FONTFACE__
__CSS__</style>
</head>
<body>
<div id="progress"></div>
<header id="topbar">
  <button id="menuBtn" data-act="menu">目录</button>
  <div class="tb-title"><a class="tb-home" href="#cover" title="回到封面（本书第一页）">__TITLE__</a> <span class="chip">__CHIP__</span></div>
  <div class="tb-tools">
    <button data-act="font-">A−</button>
    <button data-act="font0">A</button>
    <button data-act="font+">A＋</button>
    <button data-act="wide" title="切换正文版心宽度（标准 880px / 加宽 1240px）">加宽</button>
    <button id="sideBtn" data-act="side">收目录</button>
    <button data-act="theme">夜读</button>
    <button data-act="im">沉浸</button>
  </div>
</header>
<div id="mask"></div>
<aside id="side">
  <input id="q" type="search" placeholder="筛选章节…" autocomplete="off">
  <div class="nav-tools">
    <button type="button" data-navact="expandAll" title="展开所有篇/章的小节">
      <svg width="11" height="11" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 5.5L7 9.5l4-4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>全部展开</button>
    <button type="button" data-navact="foldAll" title="收起所有篇/章的小节">
      <svg width="11" height="11" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 8.5L7 4.5l4 4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>全部收起</button>
  </div>
  <nav id="nav">__TOC__</nav>
  <details>
    <summary>配图索引（__NFIG__）</summary>
    __FIGIDX__
  </details>
</aside>
<div id="resize" title="拖动调整目录宽度（双击恢复默认）"></div>
<div id="wrap">
<main>
  <div class="hero" id="cover">
    <div class="hero-cv-web">__COVER_WEB__</div>
    <div class="hero-cv-print">__COVER_PRINT__</div>
    <div class="hero-meta">
      <h1>__TITLE__</h1>
      <p class="sub">__SUB__</p>
      <p class="ver">__AUTHOR__　·　__DRAFT__</p>
      <div class="stats"><span>中文 __CJ__ 字</span><span>配图 __NFIG__ 张</span><span>代码 __NCODE__ 段</span><span>表格 __NTABLE__ 个</span></div>
    </div>
  </div>
  <div class="draftbar"><strong>__DRAFT__</strong> —— 全书重写 · 五篇十五章 · __NFIG__ 张配图。文中引用的原始来源<strong>多数可点开核实</strong>；标注为 C 级的数据（厂商自述或第三方分析）<strong>本书未独立核实</strong>。数据核查截止 2026 年 9 月 19 日，模型价格与榜单分数会快速过期，<strong>请以方法论为准</strong>。逐条口径、时点、局限与网址见<strong>附录 A</strong>。</div>
  <p class="hint">阅读提示：点击任意配图可放大（Esc 关闭）；右上角可调字号、<strong>收起目录</strong>、加宽版心、切换夜读，或进入<strong>沉浸阅读</strong>（再按一次按钮或 Esc 退出）；目录中篇 / 章 / 节标题右侧的 <strong>˅ 箭头</strong>可折叠下级内容，也可用筛选框下方的「<strong>全部展开 / 全部收起</strong>」一键操作；拖动目录与正文之间的细线可调宽度（双击恢复默认）；用 Ctrl/⌘ + P 可直接打印为 PDF（打印时自动隐藏侧栏、按章分页）。</p>
__BODY__
</main>
</div>
<button id="imExit" title="退出沉浸模式（Esc）">⤢ 退出沉浸</button>
<button id="toTop" title="回到封面">↑</button>
<div id="lb"></div>
<script>__JS__</script>
</body>
</html>
"""

# ---------------------------------------------------------------- 8. 字体 @font-face
def _find_font(basename):
    """定位思源黑体。注意：本机部分字体文件名**以空格开头**，故用通配匹配而非硬写路径。"""
    for d in (os.path.expanduser("~/Library/Fonts"), "/Library/Fonts", "/System/Library/Fonts"):
        hits = sorted(glob.glob(os.path.join(d, "*" + basename)))
        if hits:
            return hits[0]
    return None


def _subset_woff2(font_path, chars):
    """把字体裁剪到 chars 用到的字形，返回 woff2 字节（需 fontTools + brotli）。"""
    from fontTools.ttLib import TTFont
    from fontTools.subset import Options, Subsetter

    opts = Options()
    opts.layout_features = ["*"]
    opts.drop_tables = ["DSIG"]
    font = TTFont(font_path, fontNumber=0)
    sub = Subsetter(options=opts)
    sub.populate(text="".join(sorted(chars)))
    sub.subset(font)
    font.flavor = "woff2"
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def build_fontface(chars=None, mono_chars=None):
    """生成 @font-face 规则：EMBED_FONTS 时 base64 内嵌（按 chars 子集化），否则外链。

    chars      = 正文字符集（中文思源黑体）
    mono_chars = 代码字符集（JetBrains Mono 只含拉丁与符号；中文由思源黑体承接）
    """
    rules = []
    for family, key, fname, weight in SUBSET_SOURCES:
        src_chars = (mono_chars if "mono" in key else chars) if EMBED_FONTS else None
        if EMBED_FONTS:
            path = _find_font(fname)
            if not path:
                raise SystemExit(
                    "找不到字体 %s —— 内嵌模式需要本机安装该免费商用字体；\n"
                    "思源黑体：https://github.com/adobe-fonts/source-han-sans/releases\n"
                    "JetBrains Mono：https://github.com/JetBrains/JetBrainsMono/releases" % fname)
            data = _subset_woff2(path, src_chars)
            src = "url(data:font/woff2;base64,%s) format('woff2')" % base64.b64encode(data).decode()
        else:
            src = "url('%s/%s.woff2') format('woff2')" % (FONT_URL_BASE.rstrip("/"), key)
        rules.append("@font-face{font-family:\"%s\";src:%s;font-weight:%d;font-style:normal;font-display:swap}"
                     % (family, src, weight))
    return "\n".join(rules)


out = SHELL
CSS = CSS.replace("__FONT_STACK__", FONT_STACK).replace("__FONT_MONO__", FONT_MONO_STACK)
for k, v in [
    ("__TITLE__", H.escape(BOOK_TITLE)),
    ("__SUB__", H.escape(BOOK_SUB)),
    ("__DRAFT__", H.escape(DRAFT_LINE)),
    ("__CHIP__", H.escape(DRAFT_CHIP)),
    ("__AUTHOR__", H.escape(AUTHOR_LINE)),
    ("__CSS__", CSS),
    ("__TOC__", toc_html),
    ("__FIGIDX__", fig_index),
    ("__NFIG__", str(len(FIG_ORDER))),
    ("__COVER_WEB__", cover_web),
    ("__COVER_PRINT__", cover_full),
    ("__CJ__", "{:,}".format(cj)),
    ("__NCODE__", str(n_code)),
    ("__NTABLE__", str(n_table)),
    ("__BODY__", body),
    ("__JS__", JS),
]:
    out = out.replace(k, v)

# 字体：外链版直接写 URL；内嵌版按**成品内容**统计字符集再原子集化，
# 保证"书里出现过的字一个不缺、没出现的一个不带"（含标点、数字、拉丁字母）。
_chars = _mono_chars = None
if EMBED_FONTS:
    _vis = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", out, flags=re.S)
    _vis = H.unescape(re.sub(r"<[^>]+>", "", _vis))
    _chars = {c for c in _vis if c not in "\n\r\t "}
    # 代码字体只需拉丁与符号（汉字由思源黑体承接），故单独统计 <pre> 内容 + ASCII 全集
    _code = "".join(re.findall(r"<pre[^>]*>(.*?)</pre>", out, re.S))
    _code = H.unescape(re.sub(r"<[^>]+>", "", _code))
    _mono_chars = (set(_code) | set(string.printable)) - set("\n\r\t\x0b\x0c")
    print("  字体子集：正文 %d 字符 / 代码 %d 字符" % (len(_chars), len(_mono_chars)))
out = out.replace("__FONTFACE__", build_fontface(_chars, _mono_chars))

open(OUT, "w", encoding="utf-8").write(out)
print("OK ->", OUT)
print("  %s" % ("下载版 · 字体已内嵌，单文件离线可读" if EMBED_FONTS
                else "在线阅读版 · 字体外链 %s/（需连同字体文件一起部署）" % FONT_URL_BASE.rstrip("/")))
print("  中文字数 %s | 配图 %d 张 | 代码 %d 段 | 表格 %d 个 | 目录 %d 条"
      % ("{:,}".format(cj), len(FIG_ORDER), n_code, n_table, len(toc_items)))
print("  文件大小 %.1f KB" % (os.path.getsize(OUT) / 1024))
