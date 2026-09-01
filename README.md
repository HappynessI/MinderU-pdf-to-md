# MinerU PDF to Markdown + Markdown Translation for Codex

一个可复用的 Codex Skill：通过 [MinerU 官方云 API](https://mineru.net/apiManage/docs) 将本地 PDF 转换为结构化 Markdown，并可通过 OpenAI-compatible 文本模型把 Markdown 翻译成中文或其他语言。

它适合论文、扫描件、双栏文档、公式、表格和复杂版式。翻译阶段直接处理 `full.md`：正文、标题、图注和表题会翻译，HTML 与 Markdown 表格正文保持原样，同时保留公式、链接和图片引用。输出为独立的翻译 Markdown 文件夹，不重新渲染 PDF。

> 记得关闭代理登录 MinerU 获得 API。

## 功能

- 支持免 Token 的 MinerU Agent 轻量 API。
- 支持带 Token 的 MinerU 精准 API，可选择 `vlm` 或 `pipeline`。
- 支持本地 PDF 的签名 URL 上传、任务轮询、结果下载和安全 ZIP 解压。
- 默认精简输出，只保留 `full.md`、`*_content_list.json` 和全部提取图片。
- 可选调试模式可保留 MinerU 返回的原始 PDF、副本内容列表、模型结果和布局文件。
- Token 可从环境变量、Token 文件或 macOS Keychain 读取，不写入日志。
- 主流程仅使用 Python 标准库，不需要安装 `requests` 或完整 MinerU；若 Python TLS 与签名存储端不兼容，会自动使用系统 `curl` 兜底下载。
- 支持 DeepSeek 等 OpenAI-compatible 翻译 API，默认模型为 `deepseek-v4-flash`。
- DeepSeek 翻译默认关闭 thinking，避免把 token 消耗在不必要的推理上。
- Markdown 按结构分块翻译，支持断点续传、失败重试、术语表和参考文献跳过。
- 结果表格正文默认不翻译，方法名、指标名和数值均保持原样；表题及表格外的说明文字正常翻译。
- 翻译前保护公式、代码、图片路径、链接地址、URL 和 HTML 标签，翻译后校验结构。
- 将 Markdown 实际引用的本地图片复制到翻译目录，生成可独立移动的中文版 Markdown 文件夹。
- 带离线 mock 单元测试和 GitHub Actions。

## API 模式

| 模式 | Token | 限制 | 输出 | 适用场景 |
|---|---:|---:|---|---|
| Agent 轻量 | 不需要 | 10 MB、20 页 | Markdown | 小型普通 PDF、快速转换 |
| 精准 `vlm` | 需要 | 200 MB、200 页 | Markdown、内容列表、图片 | 论文、扫描件、复杂表格和公式 |
| 精准 `pipeline` | 需要 | 200 MB、200 页 | Markdown、内容列表、图片 | 更偏传统 OCR/版面流水线 |

限制可能由 MinerU 调整，请以[官方 API 文档](https://mineru.net/apiManage/docs)为准。

## 安装为 Codex Skill

### 方法一：直接克隆到个人 Skills 目录

```bash
git clone https://github.com/HappynessI/MinderU-pdf-to-md.git \
  ~/.codex/skills/mineru-pdf-to-md
```

重新打开 Codex 任务后，可以显式调用：

```text
$mineru-pdf-to-md 把 /path/to/paper.pdf 转成 Markdown
```

也可以直接说“用 MinerU 把这个 PDF 转成 Markdown”，Codex 会根据 Skill 描述自动选择它。

翻译已有 Markdown：

```text
$mineru-pdf-to-md 把 /path/to/full.md 翻译成中文，保留公式、结果表格正文和图片
```

### 方法二：让 Codex 安装

```text
使用 $skill-installer 安装 https://github.com/HappynessI/MinderU-pdf-to-md
```

## Token 配置

Agent 轻量模式不需要 Token。精准模式需要在 [MinerU API 管理](https://mineru.net/apiManage/docs)中创建 Token。

在 Codex 中，推荐把 Token 保存为全局凭据文件 `~/.codex/api/MinderU-API.md`，调用时传入：

```bash
--token-file ~/.codex/api/MinderU-API.md
```

### macOS Keychain（推荐）

```bash
python3 scripts/configure_token.py
```

脚本使用隐藏输入，并把 Token 保存到 Keychain 服务 `mineru-pdf-to-md`。

如果 Token 已经保存在文本文件中：

```bash
python3 scripts/configure_token.py --source-file /path/to/token.txt
```

### 环境变量

```bash
export MINERU_API_TOKEN='你的 Token'
```

不要把 Token 写入仓库、README、命令输出或公开日志。

## 翻译 API Key 配置

Markdown 翻译默认使用 DeepSeek OpenAI-compatible API 和 `deepseek-v4-flash`。也可以通过 `--base-url` 与 `--model` 接入其他兼容服务。

### 全局凭据文件（Codex 推荐）

把 API Key 保存为 `~/.codex/api/DeepSeek-API.md`，调用时传入：

```bash
--api-key-file ~/.codex/api/DeepSeek-API.md
```

### macOS Keychain（推荐）

```bash
python3 scripts/configure_translation_token.py
```

从已有文本或 Markdown 文件导入：

```bash
python3 scripts/configure_translation_token.py \
  --source-file /path/to/DeepSeek-API.md
```

### 环境变量

```bash
export MARKDOWN_TRANSLATION_API_KEY='你的 API Key'
```

也兼容 `DEEPSEEK_API_KEY`、`MARKDOWN_TRANSLATION_API_KEY_FILE` 以及命令行参数 `--api-key-file`。密钥只用于请求头，不会写入翻译结果或状态文件。

翻译 API Key 的解析顺序为：

1. `MARKDOWN_TRANSLATION_API_KEY`
2. `DEEPSEEK_API_KEY`
3. `--api-key-file` 或 `MARKDOWN_TRANSLATION_API_KEY_FILE`
4. macOS Keychain 服务 `mineru-markdown-translate`
5. `~/.config/mineru-pdf-to-md/translation-token`

DeepSeek 默认关闭 thinking。接入其他 OpenAI-compatible 服务时，如果服务不接受 `thinking: disabled` 请求字段，请添加 `--thinking auto`；仅在确实需要额外推理开销时再显式启用 thinking。

## 独立命令行使用

自动选择模式：

```bash
python3 scripts/mineru_pdf_to_md.py input.pdf -o output --mode auto --yes
```

强制使用精准 VLM 和 OCR：

```bash
python3 scripts/mineru_pdf_to_md.py input.pdf -o output \
  --mode precise --model vlm --ocr --yes
```

使用免 Token 轻量模式：

```bash
python3 scripts/mineru_pdf_to_md.py input.pdf -o output \
  --mode agent --yes
```

精准模式默认生成以下精简结构；图片无论是否被 Markdown 引用都会保留：

```text
output/
├── full.md
├── *_content_list.json
└── images/
```

如需排查版面、阅读顺序或模型识别问题，启用调试模式保留完整结果：

```bash
python3 scripts/mineru_pdf_to_md.py input.pdf -o output \
  --mode precise --keep-debug-artifacts --yes
```

不传 `--yes` 时，交互终端会在上传前询问；非交互调用会拒绝上传。

成功后，标准输出为便于 Agent 读取的 JSON：

```json
{
  "mode": "precise",
  "batch_id": "...",
  "model": "vlm",
  "output_dir": "/path/to/output",
  "markdown_path": "/path/to/output/full.md"
}
```

## Markdown 翻译

把 MinerU 生成的 `full.md` 翻译为简体中文：

```bash
python3 scripts/translate_markdown.py /path/to/paper-mineru/full.md \
  --api-key-file ~/.codex/api/DeepSeek-API.md \
  --model deepseek-v4-flash \
  --target-language zh-CN \
  --yes
```

默认产生：

```text
paper-mineru/
├── full.md
├── images/
└── translation-zh-CN/
    ├── full-CN.md
    ├── .translation-state.json
    └── images/
```

`.translation-state.json` 保存分块缓存和 token 用量，不包含 API Key。命令中断后再次执行会复用已完成的翻译块。只有明确需要重新翻译时才使用 `--force`。

翻译时，HTML `<table>` 与 Markdown 管道表格的正文会逐字保留，不发送给翻译模型；表题、正文、章节标题、图注以及表格外的解释性文字仍会翻译。

常用选项：

```bash
# 指定独立输出目录
python3 scripts/translate_markdown.py full.md \
  -o /path/to/translation-zh-CN --yes

# 使用 source→target JSON 术语表
python3 scripts/translate_markdown.py full.md \
  --glossary-file glossary.json --yes

# 默认不翻译参考文献条目；需要时显式开启
python3 scripts/translate_markdown.py full.md \
  --translate-references --yes

# 接入其他 OpenAI-compatible 服务
python3 scripts/translate_markdown.py full.md \
  --base-url https://provider.example/v1 \
  --model provider-model-name --thinking auto --yes
```

翻译脚本只使用 `full.md`。MinerU 的 `content_list.json`、`content_list_v2.json`、`model.json` 和 `layout.json` 不会发送给翻译模型，因为正文信息已经体现在 Markdown 中。

## 隐私说明

这个 Skill 使用云 API：PDF 转换会把 PDF 上传到 MinerU 官方服务及其签名对象存储；Markdown 翻译只会把 Markdown 文本发送给配置的翻译服务，不上传原始 PDF 和本地图片。处理未公开论文、合同、个人资料或其他敏感文件前，请确认你有权上传，并阅读对应服务的协议和隐私政策。

如果文件不能离开本机，应使用 MinerU 本地部署方案，而不是这个云 API Skill。

## 测试

离线测试使用本地 mock HTTP 服务，不上传任何文件：

```bash
python3 -m unittest discover -s tests -v
```

验证 Skill 结构：

```bash
python3 /path/to/skill-creator/scripts/quick_validate.py .
```

## 项目关系

本项目不是 MinerU 官方项目，也没有复制或打包 MinerU 模型。它是针对官方云 API 的独立轻量客户端和 Codex Skill。

- MinerU 开源项目：https://github.com/opendatalab/MinerU
- MinerU 云 API：https://mineru.net/apiManage/docs
- Codex Skills：https://learn.chatgpt.com/docs/build-skills

## 许可证

[MIT License](LICENSE)
