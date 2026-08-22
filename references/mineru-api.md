# MinerU 云 API 协议摘要

以官方实时文档为准：https://mineru.net/apiManage/docs

## Agent 轻量 API

- 不需要 Token，按 IP 限频。
- 文件限制：不超过 10 MB、20 页。
- 固定使用轻量 pipeline，只返回 Markdown CDN 链接。
- 本地文件流程：
  1. `POST /api/v1/agent/parse/file` 获取 `task_id` 和签名 `file_url`。
  2. `PUT file_url` 上传文件，不发送 MinerU Token。
  3. `GET /api/v1/agent/parse/{task_id}` 轮询。
  4. `state=done` 后下载 `markdown_url`。

## 精准 API

- 需要 Bearer Token。
- 文件限制：不超过 200 MB、200 页。
- 支持 `pipeline` 和 `vlm`；默认优先使用 `vlm`。
- 本地文件流程：
  1. `POST /api/v4/file-urls/batch` 申请 `batch_id` 和签名上传 URL。
  2. `PUT file_url` 上传文件；上传时不要发送 Authorization 或 Content-Type。
  3. `GET /api/v4/extract-results/batch/{batch_id}` 轮询。
  4. `state=done` 后下载 `full_zip_url` 并安全解压。
- ZIP 通常包含 `full.md`、JSON 中间结果和提取图片。
- 客户端默认只落盘 `full.md`、扁平版 `*_content_list.json` 和全部图片；传入 `--keep-debug-artifacts` 时保留 ZIP 中其余调试中间文件。

## 状态与停止条件

- 中间状态：`waiting-file`、`pending`、`running`、`converting`。
- 成功：`done`。
- 失败：`failed`，显示 `err_msg` 后停止。
- 客户端达到总超时后停止，不无限重试。

## 安全约束

- 签名上传 URL 只用于对应文件，不写入日志。
- Token 只发送给 `mineru.net` 的精准 API 请求；绝不发送给 OSS/CDN 签名 URL。
- ZIP 解压必须拒绝绝对路径、目录穿越和符号链接。
