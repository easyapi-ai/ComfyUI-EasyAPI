# 当前节点与接口覆盖

本页说明当前 ComfyUI 产品提供的节点。内部 `PROTOCOLS` 可保留历史适配及模拟 fixture，不能据其条目数推断当前可添加的节点、可选择的操作或已经通过真实厂商验证的能力。

## 注册节点

| 节点 ID | 用户用途 | 上游接口或行为 |
|---|---|---|
| `EasyAPIChat` | 对话与多模态 | 下方对话操作 |
| `EasyAPIImages` | 图片生成、编辑、异步任务 | 下方图片操作 |
| `EasyAPIVideo` | 视频与多种参考素材 | 只提交 `POST /v1/videos` |
| `EasyAPISpeech` | 语音合成 | `POST /v1/audio/speech` |
| `EasyAPITranscribe` | 音频转录与翻译 | `POST /v1/audio/transcriptions`、`/v1/audio/translations` |
| `EasyAPICountTokens` | 消息 Token 计数 | `POST /v1/messages/count_tokens` |
| `EasyAPIMedia` | 额外素材组与参考角色 | 本地整理；需要 URL 时由相应操作使用素材接口 |
| `EasyAPIJSON` | JSON 数据工具 | 本地解析与变换 |
| `EasyAPITaskQuery` | 查询已有任务 | 使用任务句柄保存的查询接口 |
| `EasyAPITaskWait` | 恢复等待已有任务 | 查询与轮询，不重新提交生成 |
| `EasyAPIResultDownload` | 下载和解码已有结果 | 结果 URL 或视频 content 接口 |
| `EasyAPIRealtime` | 浏览器实时会话与冻结快照 | `GET /v1/realtime` WebSocket |
| `EasyAPIPreview` | 混合媒体预览 | 本地预览，不调用生成接口 |

共 13 个注册节点。节点名称及端口翻译跟随 ComfyUI，模型 ID 和操作值保持不变。

## 对话与图片操作

| 操作值 | method | path | 传输 / 任务 |
|---|---|---|---|
| `chat` | POST | `/v1/chat/completions` | JSON / 可请求 SSE |
| `responses` | POST | `/v1/responses` | JSON / 可请求 SSE |
| `responses-compact` | POST | `/v1/responses/compact` | JSON |
| `messages` | POST | `/v1/messages` | JSON / 可请求 SSE |
| `completions` | POST | `/v1/completions` | JSON / 可请求 SSE |
| `gemini-generate` | POST | `/v1beta/models/{model}:generateContent` | JSON |
| `gemini-stream` | POST | `/v1beta/models/{model}:streamGenerateContent` | SSE |
| `image-generation` | POST | `/v1/images/generations` | JSON |
| `image-edit` | POST | `/v1/images/edits` | multipart |
| `image-task` | POST | `/v1/images/tasks` | JSON / 异步任务 |
| `image-task-edit` | POST | `/v1/images/tasks` | multipart / 异步任务 |

图片菜单不提供旧 `/v1/edits` 别名，也不把查询混入生成操作；已有任务使用独立任务节点。

## 视频

视频节点只有 `video-generation`，统一提交 `/v1/videos`。本地素材使用文件传输或内联，URL 素材使用引用。专用输入为 `first_frame`、`last_frame`、`reference_image_1/2`、`reference_video_1/2`、`reference_audio_1/2`，额外数量通过 `references` 素材组提供。

模式默认 `auto`，由模型能力及已连接素材选择；不暴露原厂路由或旧 `/v1/video/generations` 入口。模式、角色、数量和组合必须符合模型声明，没有明确声明时不能把未知能力描述成已支持。

## 连接、模型和表单

- 模型权限来自当前连接的 `GET /v1/models`，再按当前操作的接口能力筛选。分类标签不是协议兼容的充分证据，TTS、Realtime、转录不能仅按 `audio` 混选。
- `/pricing/docs`、`/pricing/schema`、`/pricing/detail` 为参数 UI、输入能力和匹配后的校验补充元数据；公开定价目录不替代 Key 权限。
- 元数据不会自动创建协议、安装厂商插件或授权任意路由。当前节点只执行明确支持的操作。
- 基础表单、模型动态字段与折叠高级 JSON 共享参数；高级 JSON 不能把节点变为任意 HTTP 请求工具。
- 静态定义提供 `en`、`zh`、`zh-TW` 翻译。模型 ID、JSON 键、操作值及参考角色不翻译。

插件本地 `/easyapi/*`（ComfyUI 可经 `/api/easyapi/*` 访问）负责连接、目录、参数、任务、文件及实时快照，不是上游创作协议。

## 任务恢复与配套媒体

任务操作选择限制为 `video-generation`、`image-task`、`image-task-edit`、`tool-motion-video`。优先传入任务句柄，以保留连接及查询路径。等待超时或停止后可恢复，不会自动重新提交生成。

配套 `/v1/media` 为需要公网 URL 的素材提供上传与生命周期能力。支持本地文件的接口不强制先上传；旧网关没有素材接口时，只接受 URL 的操作需要已有公网链接。

## 移除与排除项

旧工作流如果引用 `EasyAPITools`、`EasyAPIModeration`、`EasyAPISearch`、`EasyAPIRerank`、`EasyAPIEmbedding`、`EasyAPINativeRequest`、`EasyAPIMidjourney` 或 `EasyAPISuno`，需替换或移除相应节点。已有 `tool-motion-video` 任务可通过任务节点查询和恢复。Claude Messages 由普通对话节点提供。

账户、登录/OAuth、令牌管理、充值支付、团队/BYOK 和后台配置不属于创作节点。网页会话 `/pg/*` 及网页七牛凭证不作为插件 API Key 接口使用。未实现的 Files、Fine-tunes、图片 variations 和 Gemini `:countTokens` 也不列为可用能力。

历史原生厂商、Midjourney、Suno 等适配即使仍在代码或 fixture 中，也不属于当前节点菜单的可用承诺；旧 `custom-user-request` 的 `__path` 文法不再是产品入口。

## 验证边界

本地 mock fixture 验证请求构造、传输、任务恢复和真实媒体文件处理；模拟网关不向付费上游发请求。真实 ComfyUI 或浏览器验证也不等于真实厂商验证。真实模型效果、费用、限流、每个模型的素材组合及真实存储需单独验收。历史记录见 [验收记录](VERIFICATION.md)，历史通过数量不能作为新版本的验证结果。
