# MinerU PDF to Markdown for Codex

一个可复用的 Codex Skill，通过 [MinerU 官方云 API](https://mineru.net/apiManage/docs) 将本地 PDF 转换为结构化 Markdown。

它适合论文、扫描件、双栏文档、公式、表格和复杂版式。Skill 负责判断调用模式、保护 Token、执行固定脚本，并要求在转换后核对 Markdown 与原 PDF，而不是每次临时重写 API 代码。

> 记得关闭代理登录 MinerU 获得 API。

## 功能

- 支持免 Token 的 MinerU Agent 轻量 API。
- 支持带 Token 的 MinerU 精准 API，可选择 `vlm` 或 `pipeline`。
- 支持本地 PDF 的签名 URL 上传、任务轮询、结果下载和安全 ZIP 解压。
- 默认精简输出，只保留 `full.md`、`*_content_list.json` 和全部提取图片。
- 可选调试模式可保留 MinerU 返回的原始 PDF、副本内容列表、模型结果和布局文件。
- Token 可从环境变量、Token 文件或 macOS Keychain 读取，不写入日志。
- 主流程仅使用 Python 标准库，不需要安装 `requests` 或完整 MinerU；若 Python TLS 与签名存储端不兼容，会自动使用系统 `curl` 兜底下载。
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

### 方法二：让 Codex 安装

```text
使用 $skill-installer 安装 https://github.com/HappynessI/MinderU-pdf-to-md
```

## Token 配置

Agent 轻量模式不需要 Token。精准模式需要在 [MinerU API 管理](https://mineru.net/apiManage/docs)中创建 Token。

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

## 隐私说明

这个 Skill 使用云 API，会把 PDF 上传到 MinerU 官方服务及其签名对象存储。处理未公开论文、合同、个人资料或其他敏感文件前，请确认你有权上传，并阅读 MinerU 的服务协议和隐私政策。

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
