# 接口覆盖清单

本清单从当前 `easyapi_comfy/protocols.py` 的 `PROTOCOLS` 生成，共 **92 个操作：91 个 HTTP、1 个 WebSocket**。不同操作可能共用同一路径，例如同步图片与异步图片、JSON 与 multipart、提交与查询。

目录覆盖通过本地模拟网关 fixture 检查；这证明插件的请求构造、传输、任务恢复与结果处理可以运行，**不等于真实厂商已经验证**。本轮没有进行付费上游生成、上传或实时会话。真实模型效果、费用、限流及厂商限制仍需单独验收。

## 目录与动态元数据

- `GET /v1/models` 使用当前连接的 API Key 获取可见模型；公开模型定价目录不能替代该权限集合。
- `/pricing/docs`、`/pricing/schema`、`/pricing/detail` 补充模型参数控件；只有已声明且与操作匹配的 schema 才用于参数校验。缺少声明不代表模型不可调用。
- 元数据不会自动创建新协议、安装上游插件，或自动改写运行时提交和轮询路径。运行时使用下表的明确协议，新增原生路由通过 `custom-user-request` 显式描述。
- 表中 async=是表示提供任务句柄和轮询配置；SSE 与长连接 WebSocket 不因此标为任务 async。`transport` 是目录默认值；`video-generation` 连接本地素材时可改用 multipart，URL 素材使用 JSON。
- SSE 由所选操作、请求的 `stream` 或 Gemini 流式路径决定。Realtime 通过专用实时节点及浏览器控制面板使用。

## 按 family 列出的操作

### text（12）

| id | method | path | transport | async |
|---|---|---|---|---|
| `chat` | POST | `/v1/chat/completions` | json | 否 |
| `responses` | POST | `/v1/responses` | json | 否 |
| `responses-compact` | POST | `/v1/responses/compact` | json | 否 |
| `messages` | POST | `/v1/messages` | json | 否 |
| `messages-count-tokens` | POST | `/v1/messages/count_tokens` | json | 否 |
| `completions` | POST | `/v1/completions` | json | 否 |
| `alpha-search` | POST | `/v1/alpha/search` | json | 否 |
| `moderations` | POST | `/v1/moderations` | json | 否 |
| `gemini-generate` | POST | `/v1beta/models/{model}:generateContent` | json | 否 |
| `gemini-stream` | POST | `/v1beta/models/{model}:streamGenerateContent` | json | 否 |
| `gemini-compatible-generate` | POST | `/v1/models/{model}:generateContent` | json | 否 |
| `gemini-compatible-stream` | POST | `/v1/models/{model}:streamGenerateContent` | json | 否 |

### image（11）

| id | method | path | transport | async |
|---|---|---|---|---|
| `edits` | POST | `/v1/edits` | multipart | 否 |
| `image-generation` | POST | `/v1/images/generations` | json | 否 |
| `image-edit` | POST | `/v1/images/edits` | multipart | 否 |
| `image-task` | POST | `/v1/images/tasks` | json | 是 |
| `image-task-edit` | POST | `/v1/images/tasks` | multipart | 是 |
| `image-task-fetch` | GET | `/v1/images/tasks/{task_id}` | json | 否 |
| `image-task-fetch-post` | POST | `/v1/images/tasks/{task_id}` | json | 否 |
| `tool-remove-watermark` | POST | `/v1/tools/remove-watermark` | multipart | 否 |
| `tool-remove-background` | POST | `/v1/tools/remove-background` | multipart | 否 |
| `tool-upscale` | POST | `/v1/tools/upscale` | multipart | 否 |
| `tool-image-text` | POST | `/v1/tools/image-text` | multipart | 否 |

### utility（6）

| id | method | path | transport | async |
|---|---|---|---|---|
| `custom-user-request` | POST | `/v1/chat/completions` | json | 否 |
| `gemini-models` | GET | `/v1beta/models` | json | 否 |
| `gemini-compatible-models` | GET | `/v1beta/openai/models` | json | 否 |
| `models` | GET | `/v1/models` | json | 否 |
| `model` | GET | `/v1/models/{model}` | json | 否 |
| `generation-bill` | GET | `/v1/generation` | json | 否 |

### embedding（2）

| id | method | path | transport | async |
|---|---|---|---|---|
| `embeddings` | POST | `/v1/embeddings` | json | 否 |
| `engine-embeddings` | POST | `/v1/engines/{model}/embeddings` | json | 否 |

### rerank（1）

| id | method | path | transport | async |
|---|---|---|---|---|
| `rerank` | POST | `/v1/rerank` | json | 否 |

### video（18）

| id | method | path | transport | async |
|---|---|---|---|---|
| `video-generation` | POST | `/v1/videos` | json | 是 |
| `video-generations` | POST | `/v1/video/generations` | json | 是 |
| `video-remix` | POST | `/v1/videos/{task_id}/remix` | json | 是 |
| `video-fetch` | GET | `/v1/videos/{task_id}` | json | 否 |
| `video-generations-fetch` | GET | `/v1/video/generations/{task_id}` | json | 否 |
| `video-content` | GET | `/v1/videos/{task_id}/content` | binary | 否 |
| `tool-motion-video` | POST | `/v1/tools/motion-video` | multipart | 是 |
| `native-kling-text` | POST | `/kling/v1/videos/text2video` | json | 是 |
| `native-kling-image` | POST | `/kling/v1/videos/image2video` | json | 是 |
| `native-seedance` | POST | `/seedance/api/v3/contents/generations/tasks` | json | 是 |
| `native-seedance-list` | GET | `/seedance/api/v3/contents/generations/tasks` | json | 否 |
| `native-seedance-fetch` | GET | `/seedance/api/v3/contents/generations/tasks/{task_id}` | json | 否 |
| `native-kling-text-fetch` | GET | `/kling/v1/videos/text2video/{task_id}` | json | 否 |
| `native-kling-image-fetch` | GET | `/kling/v1/videos/image2video/{task_id}` | json | 否 |
| `native-jimeng` | POST | `/jimeng/` | json | 是 |
| `native-jimeng-fetch` | POST | `/jimeng/` | json | 否 |
| `native-dashscope` | POST | `/dashscope/api/v1/services/aigc/video-generation/video-synthesis` | json | 是 |
| `native-dashscope-fetch` | GET | `/dashscope/api/v1/tasks/{task_id}` | json | 否 |

### audio（4）

| id | method | path | transport | async |
|---|---|---|---|---|
| `audio-speech` | POST | `/v1/audio/speech` | json | 否 |
| `audio-transcriptions` | POST | `/v1/audio/transcriptions` | multipart | 否 |
| `audio-translations` | POST | `/v1/audio/translations` | multipart | 否 |
| `realtime` | GET | `/v1/realtime` | websocket | 否 |

### media（3）

| id | method | path | transport | async |
|---|---|---|---|---|
| `media-upload` | POST | `/v1/media` | multipart | 否 |
| `media-fetch` | GET | `/v1/media/{media_id}` | json | 否 |
| `media-delete` | DELETE | `/v1/media/{media_id}` | json | 否 |

### midjourney（32）

| id | method | path | transport | async |
|---|---|---|---|---|
| `mj-imagine` | POST | `/mj/submit/imagine` | json | 是 |
| `mj-action` | POST | `/mj/submit/action` | json | 是 |
| `mj-shorten` | POST | `/mj/submit/shorten` | json | 是 |
| `mj-modal` | POST | `/mj/submit/modal` | json | 是 |
| `mj-change` | POST | `/mj/submit/change` | json | 是 |
| `mj-simple-change` | POST | `/mj/submit/simple-change` | json | 是 |
| `mj-describe` | POST | `/mj/submit/describe` | json | 是 |
| `mj-blend` | POST | `/mj/submit/blend` | json | 是 |
| `mj-edits` | POST | `/mj/submit/edits` | json | 是 |
| `mj-video` | POST | `/mj/submit/video` | json | 是 |
| `mj-upload-discord-images` | POST | `/mj/submit/upload-discord-images` | json | 是 |
| `mj-swap` | POST | `/mj/insight-face/swap` | json | 是 |
| `mj-fetch` | GET | `/mj/task/{task_id}/fetch` | json | 否 |
| `mj-image-seed` | GET | `/mj/task/{task_id}/image-seed` | json | 否 |
| `mj-list` | POST | `/mj/task/list-by-condition` | json | 否 |
| `mj-image` | GET | `/mj/image/{task_id}` | binary | 否 |
| `mj-mode-imagine` | POST | `/{mode}/mj/submit/imagine` | json | 是 |
| `mj-mode-action` | POST | `/{mode}/mj/submit/action` | json | 是 |
| `mj-mode-shorten` | POST | `/{mode}/mj/submit/shorten` | json | 是 |
| `mj-mode-modal` | POST | `/{mode}/mj/submit/modal` | json | 是 |
| `mj-mode-change` | POST | `/{mode}/mj/submit/change` | json | 是 |
| `mj-mode-simple-change` | POST | `/{mode}/mj/submit/simple-change` | json | 是 |
| `mj-mode-describe` | POST | `/{mode}/mj/submit/describe` | json | 是 |
| `mj-mode-blend` | POST | `/{mode}/mj/submit/blend` | json | 是 |
| `mj-mode-edits` | POST | `/{mode}/mj/submit/edits` | json | 是 |
| `mj-mode-video` | POST | `/{mode}/mj/submit/video` | json | 是 |
| `mj-mode-upload-discord-images` | POST | `/{mode}/mj/submit/upload-discord-images` | json | 是 |
| `mj-mode-swap` | POST | `/{mode}/mj/insight-face/swap` | json | 是 |
| `mj-mode-fetch` | GET | `/{mode}/mj/task/{task_id}/fetch` | json | 否 |
| `mj-mode-image-seed` | GET | `/{mode}/mj/task/{task_id}/image-seed` | json | 否 |
| `mj-mode-list` | POST | `/{mode}/mj/task/list-by-condition` | json | 否 |
| `mj-mode-image` | GET | `/{mode}/mj/image/{task_id}` | binary | 否 |

### suno（3）

| id | method | path | transport | async |
|---|---|---|---|---|
| `suno-action` | POST | `/suno/submit/{action}` | json | 是 |
| `suno-fetch` | POST | `/suno/fetch` | json | 否 |
| `suno-fetch-id` | GET | `/suno/fetch/{task_id}` | json | 否 |

## 参数化路径与配套接口

`{model}`、`{task_id}`、`{media_id}`、`{action}`、`{mode}` 是路径占位符。普通操作可以在 `params_json` 中给出对应值；显式模型输入优先。MJ mode 操作可填 `{"mode":"fast"}`，具体模式是否开放由网关决定。占位符会进行 URL 编码。

下列配套入口不计为额外创作操作：

| 用途 | 接口 |
|---|---|
| 模型权限及参数 UI | `/v1/models`、`/pricing/docs`、`/pricing/schema`、`/pricing/detail` |
| 插件本地连接、目录与任务 | `/easyapi/profiles`、`/easyapi/models`、`/easyapi/schema`、`/easyapi/operations`、`/easyapi/tasks` |
| 插件本地管理握手、文件与实时快照 | `/easyapi/bootstrap`、`/easyapi/files/{filename}`、`/easyapi/realtime`、`/easyapi/snapshots` |

ComfyUI 的 `/api/easyapi/*` 是上述本地路由的前缀形式；它不是上游 EasyAPI 创作协议。

## 明确排除的接口

| 范围 | 排除原因 |
|---|---|
| 已实现的账户、登录/OAuth、令牌管理、支付充值、团队/BYOK 管理、控制台日志及后台管理接口 | 超出创作插件范围；不提供通用后台代理 |
| 网页会话 `/pg/*`、网页登录用户的 `/qiniu/key` 与 `/qiniu/upload-token` | 网页身份与扣费方式不同；插件使用 API Key 创作接口及 `/v1/media` |
| `/v1/files*`、`/v1/fine-tunes*`、`/v1/images/variations`、`DELETE /v1/models/{model}` | 当前网关路由为未实现占位，不列为可用能力；媒体文件使用已实现的 `/v1/media` |
| Gemini `:countTokens` | 当前网关没有实现，不能替代为生成调用；Claude 的 `messages-count-tokens` 是单独操作 |
| 未安装插件、未注册的动态原生路径 | 高级请求只能描述已存在的创作路由；目录和参数 schema 不会使路径自动可用 |

## 动态原生请求文法

在“EasyAPI 原生创作请求”节点选择 `custom-user-request`，把完整 JSON 放入 `request_body`。只允许所选 profile 网关的相对创作路径，不能设置外部主机或转发后台 API。认证在后端从 profile 解析，工作流中不填写 Key 或 Authorization。

| 元字段 | 含义 |
|---|---|
| `__path` | 网关相对路径；必须是允许的创作路径，且网关实际注册了该路由 |
| `__method` | `GET`、`POST` 或 `DELETE`；默认 `POST` |
| `__transport` | `json`、`multipart` 或 `binary`；已知路径可继承目录值，其余默认 `json`；WS 使用实时节点 |
| `__polling` | 异步任务描述：`method`、`path`、`id_fields`、`status_field`，可加 `query`、`body`、`success_value` |
| `__query` | HTTP URL 查询参数对象；真实请求体里的 `query` 字段保持为业务字段 |
| `__route_params` | 路径占位符值对象；也用于填充轮询路径中除 `{task_id}` 外的占位符 |
| `__media_fields` | 连接素材类型到目标字段的映射，例如 `{"image":"input.img_url"}`；未知协议连接素材必须显式指定 |

这些元字段由插件消费，不原样发送给上游。其余原生 body 保留；显式模型输入覆盖 body 的 `model`，连接素材覆盖映射到的目标字段。原生 body 不会被便捷 `prompt` 输入重写，`params_json` 也不会作为普通字段自动并入完整原生 body。

下面是动态插件的结构示例。`example-video` 仅为示例插件名，必须换成部署中实际注册的前缀；不能据此断言平台存在此路径。此例连接已有图片 URL，将它写入原厂 `input.img_url`：

```json
{
  "__path": "/example-video/api/v1/services/aigc/{service}/video-synthesis",
  "__method": "POST",
  "__transport": "json",
  "__route_params": {"service": "video-generation"},
  "__query": {},
  "__media_fields": {"image": "input.img_url"},
  "__polling": {
    "method": "GET",
    "path": "/example-video/api/v1/tasks/{task_id}",
    "id_fields": ["output.task_id"],
    "status_field": "output.task_status",
    "success_value": "SUCCEEDED"
  },
  "model": "替换为当前令牌可用的模型名",
  "input": {"prompt": "一只猫在草地上行走"},
  "parameters": {"duration": 5}
}
```

`id_fields` 是提交响应中任务 ID 的候选点号路径；轮询时 `{task_id}` 替换为该 ID。等待、恢复和下载都不会重新提交生成。关闭“等待结果”会返回任务句柄，供查询或等待节点续接。

JSON 的 `__media_fields` 支持点号对象路径；同一字段的一份素材写为 URI 字符串，多份写为 URI 数组。它不会自动合成厂商的角色对象或内容块数组。multipart 的映射值是文件表单字段名，需要本地素材。原厂只接受公网 URL 时，可先执行 `media-upload`，或使用具有 URL 上传适配的便捷原厂节点；不要把本地 data URL 当作公网 URL。

例如未知 multipart 路由可使用 `"__media_fields":{"image":"source_image","mask":"mask_file"}`；复杂角色数组则直接按该原厂文档写入完整 body。已有素材 URL 可直接放入 body，不必通过连接素材重建它。
