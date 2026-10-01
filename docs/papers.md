# Paper Library / 论文知识库

新增 `/papers/` 和 `/papers/<id>/`，沿用 Astro 静态路由、`BaseLayout`、原有导航
和全局配色。没有新增框架、数据库、后端服务或 MCP；既有页面和财务导入流程保持不变。
GitHub Pages 的项目子路径由现有 `astro.config.mjs` 处理。

## 关键概念与架构图（Prompt 1.1）

普通 ingest 和桌面批量脚本都自动使用新的选图流程，操作命令不变：

1. 默认优先本机 MinerU 4 CLI，解析正文、章节、图注和图块定位，支持矢量图。
2. 从 PDF 前半部分、具有可信图注的候选中准备最多 8 个图像附件；优先概念总览、
   模型架构和方法流程，仍保留全篇文本来分析论文。
3. 用 Codex `--image` 提交真实图像，按附件编号/候选 ID 检查像素、图注、正文。
   scientific-paper-reader 的视觉核对、证据区分和不猜测规则固化在 Prompt 中；
   不为加载技能而放开 Codex shell、全局记忆或其他论文上下文。
4. 每篇最终选择 1–3 张不重复的核心图；拒绝结果/指标对比/消融图和后半篇图。
   确无可靠概念图时允许空数组，不用结果图凑数。
5. 只把选中的压缩 WebP 发布到 `public/paper-assets/<id>/`。候选预览随本次任务
   自动删除，不永久保存所有图片。同页、同图注的多个图块合为一个逻辑图，
   保留各子图内容；拼接不是原始排版，Prompt 禁止据此猜测相对位置和跨图关系。

详情页新增 Key diagrams 区域：原文图注、PDF 页码、中文解读、图片放大、
键盘 Esc/按钮关闭。列表缩略图只选概念/架构/方法图。旧 JSON 继续兼容；
历史 result/benchmark 枚举保留用于读旧数据，但新导入不会发布它们。
没有修改网站其他模块或引入新前端框架。

已收录且无图的论文不会自动覆写。要补图请使用：

```bash
python tools/papers/ingest.py /path/to/paper.pdf --update --force
```

### 本地 MinerU 配置

本机已验证 MinerU 4.0.7 的标准本地服务。默认查找 PATH 或 `~/.local/bin/mineru`；
可用 `PAPER_MINERU_BIN` 指定 CLI，`PAPER_MINERU_TIER` 显式选择
`basic`、`standard`、`advanced`，留空则用正常本地默认质量。不会自动选 flash、
上传 remote、升级安装、下载模型或改动 MinerU 持久配置。
质量/服务错误会明确报错，不默默换解析器；请检查 `mineru server status --json`，
配置可用本地服务，或明确选择轻量解析器：

```bash
python tools/papers/ingest.py /path/to/paper.pdf --parser pymupdf
# 环境级选择（auto / mineru / pymupdf）：
export PAPER_PARSER=auto
```

`auto` 仅在 MinerU CLI 未安装时使用 PyMuPDF；后者仍只提供位图候选，可能漏掉
矢量架构图。每个 MinerU CLI 命令默认超时 900 秒，等待时有 15 秒心跳，可通过
`PAPER_MINERU_TIMEOUT` 调整。中断只停止本工具 CLI，不擅自停止独立共享服务。
MinerU 也保留自己的解析缓存（默认 `~/.mineru`），这不是 Codex 对话记录，
本工具不会自动执行 MinerU forget/cleanup。已从项目目录启动的 MinerU 服务可能
产生 `blobs/` 解析产物，该目录已单独忽略；网站仍不复制原始 PDF。

## 安装分析工具

网站本身仍只需要原来的 Node 环境。仅在本机 ingest 时需要 Python 3.11+、
已登录 ChatGPT 账户的 Codex CLI，以及以下三个 Python 依赖：

```bash
python3 -m venv .venv-papers
source .venv-papers/bin/activate
python -m pip install -r tools/papers/requirements.txt
codex --version
codex exec --help
codex login
```

开发时实际验证了 `codex-cli 0.159.3`，使用官方支持的 `--output-schema` 和
`--ephemeral`。旧 CLI 缺少必要隔离参数时会明确报错，请升级，不降级成共享 session。
当前实现使用 CLI 登录，不需要 OpenAI API key；不会从环境继承 API key 切换为付费 API。
账户的实际可用模型和使用额度取决于你的 Codex 账户。

默认分析配置固定为 `gpt-6.1-sol` + `high`，每次 `codex exec` 都显式传入
`--model gpt-6.1-sol -c 'model_reasoning_effort="high"'`，不依赖 CLI 的隐含默认值。
覆盖顺序为 CLI 参数 → `PAPER_CODEX_*` 环境变量 → 上述默认值；空环境变量视作未设置。
如确有需要，可使用 `--model` 和 `--reasoning-effort` 覆盖。修改推理强度也会改变缓存键，
不会复用以前 `none`/其他推理配置的分析结果。

### 自定义 Codex 配置目录

程序读取并继承环境中的 `CODEX_HOME`，不假设它位于项目 `./.codex`。
例如使用 personal 配置时，在运行 ingest 的同一个终端中确认：

```bash
export CODEX_HOME="$HOME/.codex-personal"
echo "$CODEX_HOME"
```

若实际目录是 `~/codex-personal`（不带点），请按真实目录设置，不要照抄上面的路径。
Python 直接执行 Codex 二进制，不会调用 `.zshrc` 中的 `codex`/`codexp` shell 函数。
未导出变量时会明确提示使用官方默认 `~/.codex`，不会偷偷猜测使用哪个账户配置。
共享配置/认证目录不等于共享论文上下文；论文任务仍各有独立工作目录和进程。

## 添加普通 PDF

激活上述环境后，在项目根目录执行：

```bash
python tools/papers/ingest.py /path/to/paper.pdf
# 完整子命令形式等价：
python tools/papers/ingest.py ingest /path/to/paper.pdf
```

运行后终端立即显示六个阶段：读取/hash → PDF 解析 → Codex 分析 → Schema/证据校验
→ 图片处理 → 正式发布。解析或分析缓存命中、失败重试也会明确提示。
启动时显示 CLI 版本、请求的模型与推理强度、实际 CODEX_HOME、凭据存储类型、超时上限
和任务日志目录；能读到 CLI 启动头时，还会显示 CLI 实际报告的模型和推理强度。
不显示凭据、不把整篇 prompt 或模型推理内容刷到终端。

Codex 运行期间默认每 **15 秒**输出一次等待心跳，包含已等待秒数、超时上限和 stderr
最后更新时间。这仅表示子进程尚未退出，不伪造百分比，也不能据此断言网络或模型正常。
需要详细状态时打开终端显示的任务目录里的 `stderr.log`。
Ctrl+C 会停止这一篇独立任务的子进程组（包括 CLI 的包装子进程），清理临时任务文件并返回 130；
超时也会停止该任务，不留下分析进程继续运行，不发布半成品。

默认不保留分析对话残留：已有 `--ephemeral` 禁止持久化会话 rollout，另外显式设置
`history.persistence="none"`。每次分析（包括失败、超时、中断）退出后自动删除本次
task 子目录中的 prompt、stdout/stderr、原始 output 及其他任务文件。
这只针对本工具新启动的任务，不扫描或删除 `CODEX_HOME` 中的其他对话、登录信息，
也不删除修改前产生的缓存或正在运行的旧任务。运行中的临时文件仍可用于查看状态；
强制 kill -9、断电或清理权限错误可能留下文件，无法承诺全机零残留。
这些本地设置不控制服务端数据保留，也不会清除终端自身的滚屏记录。

保留的解析缓存包含论文文本，`analysis-*.json` 是已验证的结构化结果缓存，用于避免
重复解析/分析，并不是可恢复的 Codex 对话。正式网站 JSON、展示图片、简短错误记录
及批次结果不受清理影响。需要排查某次新任务时可显式开启详细日志：

```bash
export PAPER_KEEP_ANALYSIS_LOGS=1
# 然后重新运行 ingest；恢复默认：
unset PAPER_KEEP_ANALYSIS_LOGS
```

可选：提供已经核实的基础元数据，减少 PDF 元数据抽取的歧义：

```bash
python tools/papers/ingest.py /path/to/paper.pdf --metadata /path/to/metadata.json --id navsim-2024
```

`--metadata` 接受 `title, short_title, authors, year, venue, doi, arxiv_id,
paper_url, pdf_url, project_url, code_url, zotero`；缺失字段可省略。
`authors` 是字符串数组，`year` 是整数或 null。为兼容旧输入，metadata 中的 `tags`
仍可提供，但会忽略，不作为网站标签。
`zotero` 若提供，需包含 `item_key, library_id, collections`。
还可通过 `related_papers` 提供你已经核实的外部元数据数组，每条至少包含 title，
可带 doi/arxiv_id/url/relation_type；未指定关系时只能用 `related`。
分析器不能根据记忆增加这些条目或猜测其链接。

例如：

```json
{
  "title": "Your verified paper title",
  "authors": ["First Author", "Second Author"],
  "year": 2026
}
```

原始 PDF 在原位置读取，不复制到网站、不自动下载公开 PDF。
解析后的正文会交给 Codex，因此不要 ingest 不适合发送给该服务的保密材料。

## 桌面批量导入（本机）

双击桌面的 `批量导入论文.command`，无需先手动激活虚拟环境。
对应的启动脚本保存在 `tools/papers/batch-ingest.command`。
本机脚本固定使用 `/Users/wudi/code/Portfolio/.venv-papers/bin/python`、
项目 `/Users/wudi/code/Portfolio` 和 `gpt-6.1-sol / high`；未设置 `CODEX_HOME`
时使用 `~/.codex-personal`，并补充 Finder 启动所需的 Homebrew PATH。
如果将项目移动到别处，需要修改启动脚本的 `PROJECT_DIR` 并重新复制到桌面。

将普通 PDF 放到 `~/paper_reading/new` 第一层，程序按文件名依次调用现有
`ingest.py`，每篇仍为全新独立 Codex 进程。只有返回成功且正式 JSON、图片引用、
索引及 PDF 的 SHA256 全部核验通过后，才把该原始 PDF 移到 `~/paper_reading/ready`。
JSON 始终生成到 `/Users/wudi/code/Portfolio/data/papers/`，不会生成在桌面或 PDF 目录。
ready 同名文件不覆盖，新文件追加 hash 后缀。符号链接、子目录、非 PDF 文件不处理。
原始 PDF 不会复制进网站仓库；ready 是用户指定的本地归档目录，不是 Zotero 同步附件。

单篇失败保留原文并继续下一篇；Ctrl+C 停止后续论文。重复运行时，已经完整入库但
尚未归档的相同 hash PDF 只归档、不重复分析。不会自动添加 `--update` 或 `--force`，
不会覆盖已有 JSON。防止重复双击的锁和逐篇批次结果保存在项目已忽略的
`.cache/papers/batches/`；详细任务日志默认退出即清理，只有显式开启保留才留在 `PAPER_CACHE_DIR`。
脚本不会自动提交、推送或发布网站。

也可以在已配置 Codex 的终端运行：

```bash
export CODEX_HOME="$HOME/.codex-personal"
.venv-papers/bin/python tools/papers/batch.py
```

`batch.py --new /path/to/new --ready /path/to/ready` 可覆盖输入/归档目录；
输出目录仍由项目中的 `ingest.py` 固定决定。

## 从 Zotero 添加

在 Zotero 的 Settings → Advanced 中启用
“Allow other applications on this computer to communicate with Zotero”，保持 Zotero 运行：

```bash
python tools/papers/ingest.py --zotero-item ABCD1234
```

支持文献条目 key 或有父文献条目的 PDF attachment key。默认从用户个人库读取；
组库可通过 `ZOTERO_LOCAL_API` 指定 `/api/groups/<group-id>`。
附件默认在 `~/Zotero/storage`，可通过 `ZOTERO_STORAGE_DIR` 修改，代码没有硬编码用户名。
有多个 PDF 附件时必须指定一个，不猜测哪个是正文：

```bash
python tools/papers/ingest.py /path/to/chosen.pdf --zotero-item ABCD1234
```

此适配器只执行 GET，完全不改 Zotero 条目、标签、collections 或附件。
Zotero 负责可靠的标题、作者、年份、DOI、URL、collections 和 item key；
非空可靠值覆盖 AI 的抽取结果。AI 负责研究问题、贡献、方法、实验、局限与证据。
网站不会连接 Zotero；Zotero 停止时，普通 PDF ingest 和已生成的网页仍然正常。

可选 Web API 只用于读取元数据，需要 `ZOTERO_USER_ID` 与 `ZOTERO_API_KEY`。
PDF 仍需位于本机已同步 storage 或显式指定的路径，不会下载并复制进仓库。
密钥仅由适配器读取，不传给 Codex，不写入 JSON、日志、前端或 Git。

## 更新与强制分析

已经存在的论文默认拒绝覆盖，包括命中缓存的情况：

```bash
python tools/papers/ingest.py /path/to/paper.pdf --update
python tools/papers/ingest.py /path/to/paper.pdf --update --force
python tools/papers/ingest.py --zotero-item ABCD1234 --update --force
```

身份优先使用 DOI、arXiv、规范化标题与年份；已存在的 source hash 和
Zotero item/library 标识也用于识别同一篇论文。已有记录保持其 ID，标题修正不改 URL。
首个 slug 使用 short_title 或标题加年份，不使用文件名；同名不同论文追加身份摘要防冲突。
`--id` 仅用于第一次选择 slug，不能借它覆盖不同论文。

更新只替换该篇论文和索引；只清理旧 JSON 引用而新版本不再引用的 generated WebP，
不删除原始 PDF，也不会递归清理别的论文目录。
命中缓存时沿用原分析时间；真正重新分析时刷新 `analyzed_at`。

## 重建索引

```bash
python tools/papers/ingest.py rebuild-index
```

逐篇校验，按年份降序、ID 升序排列。没有生成时刻等非确定性字段，重复运行结果一致。
`index.json` 只包含列表与关系匹配需要的轻量字段，不包含方法、实验和证据全文。
手工添加合规 paper JSON 后也不需要修改前端代码：Astro 构建扫描单篇 JSON，
重新生成静态路由和轻量 `/papers/index.json`。建议同时 rebuild-index 保持仓库索引同步。
开发服务器运行时新增数据后，刷新页面；新增详情路由若未发现，重启 dev。

## 本地启动与发布

```bash
npm install
npm run dev
npm run check
npm run build
npm run preview
```

打开本地 `/papers/`。部署继续使用原有 GitHub Actions，只提交正式 paper JSON、
索引以及少量被引用的图片；推送 main 后网站重新构建。静态网站不会后台自动分析论文。
浏览器列表只包含轻量摘要，无需请求所有完整 paper JSON；详情由 Astro 生成独立 HTML，
直接访问或刷新 URL 不依赖客户端路由 fallback。

## 文件分别保存在哪里

| 内容 | 位置 | 提交 Git / 发布 |
| --- | --- | --- |
| 原始论文和基础文献元数据 | Zotero / 显式指定的本地 PDF | 不因 ingest 复制或提交 |
| 结构化分析 | `data/papers/<id>.json` | 是 |
| 轻量列表索引 | `data/papers/index.json` | 是 |
| 正式 Schema | `schemas/paper.schema.json` | 是 |
| 选中的展示图 | `public/paper-assets/<id>/*.webp` | 是，仅 JSON 引用的少数图 |
| 解析/结构化结果缓存、临时图、本地 source path、简短错误 | `PAPER_CACHE_DIR` | 否 |
| prompt、stdout/stderr、raw output、任务文件 | `PAPER_CACHE_DIR/<hash>/tasks/<uuid>/` | 否，默认退出即删除 |

默认 cache 为 `.cache/papers/`。支持：

```bash
export PAPER_CACHE_DIR="$HOME/.paper-research-cache"
export ZOTERO_STORAGE_DIR="$HOME/Zotero/storage"
export PAPER_CODEX_TIMEOUT=1200
export PAPER_CODEX_HEARTBEAT_SECONDS=15
# 默认已经如此；仅需覆盖时设置：
export PAPER_CODEX_MODEL="gpt-6.1-sol"
export PAPER_CODEX_REASONING_EFFORT="high"
```

`.env.example` 列出配置；程序直接读取环境，不自动加载 `.env`。可复制 `.env.example`
到 ignored `.env` 并在 shell 中加载你自己的配置。没有真实密钥随项目分发。
仓库内的自定义 cache 必须位于 ignored `.cache/` 下；仓库外路径可以自由配置。
不要把私人路径填到正式 metadata 或 paper JSON：校验会拒绝常见本机路径和 file URL。
`PAPER_DATA_DIR` 是可选的 **build-only** 数据目录覆盖，用于下面的 fixture 验证；正常部署不要设置它。

缓存键包含文件 SHA256、prompt/schema 版本及内容摘要、parser 版本、CLI 版本、
分析器/模型/推理强度与可信输入元数据。`--force` 重新解析并分析。缓存没有自动过期清理；
可以在确认不再需要复用或排错后自行删除具体缓存子目录。

## 事实、证据和解释

正式 Schema 的每个对象禁止额外字段。除 `interpretation` 外，所有分析字段均为论文事实；
建议、推测和开放问题只能写在独立的 AI interpretation 对象，并在页面明确标注。
未说明内容为 null、空数组或 `not explicitly stated`，不强行填满。

贡献、主要结果和论文明确写出的局限必须引用存在的 evidence ID；论文引用来源的
相关文献也必须有依据。每条 evidence 保存 PDF 的 1-based page、section 和极短原文。
ingest 校验原文短句确实存在于指定页，且不超过 25 个空格分隔词。这个检查验证来源定位，
不能证明 AI 对句意和关系的解释一定正确，重要结论仍需人工核对原文。

网页不再显示 Evidence 区域、导航入口和证据编号跳转。`paper.json` 的
`evidence` / `evidence_ids`、分析器的原文核对与 ingest 的严格校验仍完整保留。
这是展示层调整，不是取消证据约束，也不保存模型的私有思维链。

## 本机手工标签

运行 `npm run dev`，打开 `http://localhost:4321/papers/`（以终端显示端口为准）。
论文卡片和详情页都有「设置标签」按钮；没有标签时显示「尚未设置标签，请开始设置」。
点击后可多选，或在窗口下方创建新标签，再点击「保存标签」。
「标签管理」进入 `/papers/tags/`，可新增、改名或删除标签。名称去除首尾空白，
不区分大小写去重；稳定标签 ID 保证改名保留关联。删除时确认并从所有论文中移除该标签。
清空一篇论文的全部选择后，它会重新提示尚未设置。年份、作者和标签组合筛选仍然保留。

保存直接写入 `data/paper-tags.json`，同时以可恢复事务更新 `data/papers/index.json`。
没有 localStorage 草稿、线上编辑或 GitHub API；刷新、重启及重新分析都不会丢失手工标签。
本机 API 仅在 Astro 开发服务开放，限制 loopback 地址，同源 JSON 请求，最大 1 MB，
并用配置版本校验阻止其他页面的旧配置覆盖新配置；冲突或保存失败会明确提示。
`npm run preview` / 静态构建只展示已保存标签，不开放编辑，请使用 `npm run dev`。
需要先按本文安装 `.venv-papers` Python 依赖；本地服务优先用该环境，不需要额外框架或数据库。

所有网站标签都来自这个独立文件，旧 `paper.json.tags` 中的 AI 分类不自动迁移，
避免将重复或不准确分类变成手工标签。新分析固定输出 `tags=[]`，Zotero 标签也不自动导入。
重建索引会使用手工配置；批量导入成功检查同样采用手工配置，不影响 PDF 归档。
Prompt 版本已升为 `1.2`，后续分析不会复用旧版分类分析缓存；不必重分析旧论文才能设置标签。
手工修改配置时应遵循 `schemas/paper-tags.schema.json`；正常使用直接在本机网页操作即可。

相关文献在分析结束后按 DOI、arXiv 或精确规范化标题匹配本地库。
即便先收录 A、后收录 B，构建时也会重新解析站内关系，不需要重分析 A。
无站内匹配时使用安全的 DOI/arXiv/已提供 URL；没有可核实地址的引用保留标题而不编造链接。
所有外部链接限制 http/https，并使用 `noopener noreferrer`。

## 隔离和失败处理

`CodexAnalyzer` 集中管理 shell 调用，每一次 analyze 或重试均启动全新 `codex exec`。
不调用 resume/fork，也不传入已有论文的正文或分析。进程结束后任务结束。
每次任务有独立 cache 子目录，使用 `--ephemeral`、`--ignore-user-config`、read-only sandbox；
禁用记忆、shell、浏览器、插件、MCP/apps 和子 agent 等工具。
从用户配置仅读取非秘密凭据存储选择（例如 keyring），并显式传入模型及推理配置；
不加载其他用户上下文或工具配置，认证仍由 CLI 自己处理；
不读取/复制 auth.json 或令牌。解析文本作为 stdin 提交。

解析、分析、证据或图片失败时，简短错误写入 `<cache>/<source-hash>/last-error.log`，
Codex 每次尝试的临时任务文件默认自动删除；设置 `PAPER_KEEP_ANALYSIS_LOGS=1` 才保留
详细日志与 traceback。重试也是新进程，
重新给出同一篇的源文，不拿上一轮输出做共享 session 修复。
`--retries 0` 禁止自动重试，默认最多重试一次。

正式文件使用临时文件、fsync、atomic replace；paper、index 与资产的多文件写入有
回滚 journal，并用文件锁串行发布。写入失败立即回滚；进程意外中断时，下次 CLI
读写会恢复到先前完整状态。若构建发现未完成事务会停止，请先运行 rebuild-index 恢复。
文件锁使用 macOS/Linux 的 fcntl；原生 Windows 需替换锁实现或使用 WSL。

## Parser 与 Analyzer 扩展

`PaperParser` 定义 parse/extract_figure；`MinerUParser` 用 CLI 读取完整页序正文及
稳定图块 locator，使用 MinerU 块图像输出提取矢量/位图，保留图注和章节。
`PyMuPDFParser` 是显式轻量替代，保留页面、章节提示、文本块、caption 和 references，
双栏使用阅读顺序启发式。OCR 或复杂双栏仍需核对原文。
没有默默截断长论文：超过 `PAPER_MAX_PARSED_BYTES`（默认 1,500,000）会报错。
最终最多三张关键图，而非保存所有页面；MinerU 图块 WebP 最大 2200px、quality 88，
多子图拼接高度可为 4400px。PyMuPDF 位图保留原有 1600px、quality 82。

新增分析器时继承 `tools/papers/analyzers/base.py` 的 `PaperAnalyzer`：

```python
from tools.papers.analyzers.base import PaperAnalyzer
from tools.papers.service import PaperService

class DeepSeekAnalyzer(PaperAnalyzer):
    name = "deepseek"

    def cache_identity(self):
        return {"name": self.name, "model": "configured-model", "version": "1"}

    def analyze(self, parsed, metadata, task_dir):
        # 在这里集中调用新服务；密钥仅从环境读取。
        # parsed['_figure_images'] 为候选 ID -> WebP bytes，应当作为实际图像输入；
        # 不要把 bytes、本机路径或 base64 作为论文事实写进 JSON。
        # 输出相同 Schema 的 dict，遵守同一证据和无记忆补全合同。
        raise NotImplementedError

service = PaperService(analyzer=DeepSeekAnalyzer())
```

无需修改前端或 storage。`PaperService` 提供 ingest/get_paper/list_papers/search_papers/
related_papers/rebuild_index，可在以后用 CLI、Web API 或 MCP 包装，当前没有构建这些服务。
修改结构/分析规则时同时更新 Schema/Prompt 的版本和 `storage.py` 中对应常量。

## 验证

离线测试不连接 Codex/Zotero，不向正式库写入假论文：

```bash
python -m unittest discover -s tools/papers/tests -v
python tools/papers/tests/smoke.py
```

真实独立 Codex 分析（会使用账户额度，输出仍在 ignored cache）：

```bash
python tools/papers/tests/smoke.py --live
```

生成测试网页时，使用 build-only fixture 目录；不要将这个构建用于部署：

```bash
python tools/papers/tests/smoke.py --browser-assets
PAPER_DATA_DIR=.cache/papers/smoke-site/data/papers GITHUB_ACTIONS=true GITHUB_REPOSITORY=wudi/Portfolio GITHUB_REPOSITORY_OWNER=wudi npm run build
# Node 22+，需要 Chrome；其他系统用 PAPER_TEST_CHROME 指定其可执行文件。
node tools/papers/tests/browser_check.mjs
# 验证结束恢复正式数据构建：
python tools/papers/tests/smoke.py --clean-browser-assets
npm run build
```

浏览器检查概念图加载/放大/Esc 关闭、搜索、组合筛选、重置、轻量索引、直接详情访问/刷新、站内外关系链接、证据展开
和 390px 移动端横向溢出，并把截图保存在 `.cache/papers/browser/`。

可选真实 PDF / MinerU 图块检查（不调用 Codex，不写正式 JSON，不移动原文）：

```bash
python tools/papers/tests/mineru_check.py /path/to/paper.pdf
```

输出位于 `.cache/papers/figure-audit/`；该步骤用于人工视觉核验。浏览器 fixture 的
`--browser-assets` 只暂时复制一个合成 WebP 到 public，检查结束应运行上述清理命令，
以免把演示资产带入正式构建；清理会核验内容一致，不覆盖或删除真实论文图片。

## 参考

- [OpenAI Docs：Codex 非交互模式](https://developers.openai.com/codex/noninteractive)
- [OpenAI Docs：图片附件输入](https://learn.chatgpt.com/docs/image-inputs)
- [Zotero 官方 Local API](https://www.zotero.org/support/dev/web_api/v3/local_api)
- [PyMuPDF 官方文本抽取说明](https://pymupdf.readthedocs.io/en/latest/recipes-text.html)
