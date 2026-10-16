# 《Vibe Coding 时代的我们》

> **AI 编码的能与不能** · Kevin + AI · V3.3 修订稿 · 2026-09-24

一本中文技术判断书的**手写源 + 构建工具链**。主证据是作者本人 155 天"一行代码没手写、全部由 AI 实现"的一手实践（含失败记录），辅以业界实证研究。

| 项 | 内容 |
|---|---|
| 书名 | 《Vibe Coding 时代的我们》—— AI 编码的能与不能 |
| 版本 | V3.3 修订稿 · 2026-09-24 |
| 结构 | 前言 + 五篇十五章 + 尾声 + 后记 + 附录 A/B/C |
| 体量 | 21 个分片 · 22 张正文配图（另封面 1 张） |
| 核心命题 | AI 编码不是"能不能"的问题，是"怎么分工"的问题 |
| 署名 | Kevin + AI |

**想直接读**：四种成品（A4 PDF · 单文件离线版 HTML · 在线阅读版 HTML · 合稿 md）见本仓库的 [Releases](../../releases)。**普通读者下 PDF 或「离线版 HTML」即可**——在线阅读版的字体是站点级外链资源（`/site/fonts/`），本地打开会回落系统字体，它面向的是自建在线阅读站的场景（详见 §4）。本仓库只放手写源与工具链、不放构建产物——想自己出一份，跑 §2 的链路即可。

---

## 1. 目录

```
vibe-coding-book/
├── parts/     ← 【唯一手写源】21 个分片（前言 + 15 章 + 尾声 + 后记 + 附录 A/B/C）
├── images/    ← PNG 配图（供 md 引用）· 23 张
├── svg/       ← SVG 矢量源（供 HTML/PDF 内联）· 与 images/ 同名成对
├── scripts/   ← 生成 / 校验 / 交付脚本（**跑法见 [scripts/README.md](scripts/README.md)**）
└── LICENSE · README.md
```

---

## 2. 构建

**唯一手写源是 `parts/`。** 改稿只改这里，然后重跑对应链路。产物名（下文 `<书名>`）：`Vibe Coding 时代的我们（2026·V3.3）`。

```bash
python3 scripts/fix_hard_breaks.py --check        # 0. 引用块闸门
python3 scripts/build_book.py                     # 1. parts/ → dist/<书名>.md
python3 scripts/build_html.py                     # 2. 在线阅读版（字体外链，体积小）
python3 scripts/build_html.py --embed-fonts       # 2'. 下载版（字体内嵌，单文件离线可读）
python3 scripts/build_pdf.py --html "dist/<书名>（离线版）.html" --out "dist/<书名>.pdf"   # 3. A4 PDF
python3 scripts/add_pdf_bookmarks.py "dist/<书名>（离线版）.html" "dist/<书名>.pdf"       # 4. 三层书签 + 元数据
python3 scripts/stamp_page_numbers.py "dist/<书名>.pdf"                            # 5. 页码（每份只跑一次）
python3 scripts/probe_text.py . --new "新文本" --old "旧文本"                   # 6. 改过用词时：三形态正反探针
```

> PDF 请从**离线版** HTML 导出——那一版字体内嵌，PDF 的字形来源才可控（见 §4）。

**改哪类内容要重跑哪几步**、以及九个"违反了不报错、只静默出错"的坑，全部在 [`scripts/README.md`](scripts/README.md)——**动手之前请先读完那份**。

---

## 3. 四条硬约定

这四条一旦违反，**不会崩溃、只会静默出错**：

| # | 规则 | 违反后的症状 |
|---|---|---|
| 1 | **`parts/` 是唯一手写源**，不要改根目录的 `<书名>.md` | 下次 `build_book.py` 直接覆盖，手改全丢 |
| 2 | **`images/` 与 `svg/` 必须同名成对**，只改 SVG 重渲染，不重画 PNG | 阅读版/PDF 内联的还是旧图，与 md 里的 PNG 不一致 |
| 3 | **重渲 PNG 的比例要从既有文件头反推**（内容图 2.2×、封面 1.4×） | 按统一比例硬渲会把封面压扁 |
| 4 | **页码脚本不幂等**，每份 PDF 只跑一次 | 连跑两次会在同坐标叠两份文字，肉眼看不出，选中复制得到 `63 63` |

---

## 4. 字体：只使用免费商用字体

| 用途 | 字体 | 许可 |
|---|---|---|
| 中文正文、标题与全部图表 | **思源黑体 Source Han Sans CN**（Adobe） | Apache-2.0（1.x）／ SIL OFL 1.1（2.0 起） |
| 代码、等宽片段 | **JetBrains Mono** | SIL OFL 1.1 |

- 本项目**不使用**微软雅黑（其著作权归方正，商业发布需单独授权）、苹方等系统字体作为发布用字；字体栈里保留的系统字体名**仅作本机兜底**，其字形不随本仓库或任何产物分发。
- 下载版 HTML 内嵌的是上述免费字体的**子集**（woff2，按正文实际用字裁剪）；PDF 的中文字形同样来自思源黑体。
- `--embed-fonts` 需要本机安装这两种字体，缺失时会明确报错并给出下载地址（见 [思源黑体](https://github.com/adobe-fonts/source-han-sans/releases) / [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono/releases) 的官方 release 页）。
- ⚠️ **在线阅读版的字体是"外挂"的，产物本身不含字体**：它的 `@font-face` 指向站点的 `/site/fonts/book-sans-*.woff2` 与 `book-mono-*.woff2`，而**本仓库不提供这些文件**（它们是按"书库累计字集"生成的站点级资源）。因此：
  - **只想自己读、或分发给朋友** → 请构建**下载版**（`python3 scripts/build_html.py --embed-fonts`），单文件自包含、离线可读、不依赖任何外部字体；
  - **要在自己的站点上托管在线阅读版** → 需按正文实际用字自行生成字体子集（`pyftsubset`，可参考 `build_html.py` 中 `build_fontface()` 的写法）并部署到该站点的 `/site/fonts/` 下，否则访客端会**静默回落系统字体**（能读，但字形各平台不一）。
- **字体版本建议固定**：本版成品由**思源黑体 1.x（Apache-2.0）** + **JetBrains Mono 2.304** 生成。更换字体大版本会让字形产生细微差异，同一本书的新旧产物将不一致，升级前请留意。

---

## 5. 许可与署名

- **脚本与工具链**：MIT，Copyright © 2026 Kevin Zhang —— 见 [`LICENSE`](LICENSE)
- **书稿正文与配图**：版权归作者所有，署名 **Kevin + AI**。转载、翻译、商用请先联系作者。
