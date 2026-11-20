# 示例工作流

示例默认使用 `mock` 离线连接。先运行 `tools/mock_gateway.py`，在 ComfyUI 中配置同名连接；切换到真实连接后会按平台实际规则调用和计费。

`api/` 下是 ComfyUI API 格式工作流，可导入 ComfyUI，或放入 `{"prompt": 工作流}` 发送到本机 `/prompt`。

| 示例 | 内容 |
|---|---|
| `01-chat.json` | 普通对话 |
| `02-stream.json` | SSE 对话，保留温度 `0` |
| `03-image.json` | 图片生成，保留 `seed=0` 和 `watermark=false` |
| `04-image-edit.json` | 生成图片直接连接图片编辑 |
| `05-async-image.json` | 异步图片任务 |
| `06-video.json` | 统一视频生成及原生 SaveVideo |
| `07-video-references.json` | 图片直接连接视频 `first_frame` 与 `last_frame` |
| `08-speech-transcribe.json` | TTS、原生 AUDIO、兼容转录模型及 SaveAudio |
| `10-claude-messages.json` | 普通对话节点使用 Claude Messages |
| `11-video-audio-reference.json` | TTS 的 AUDIO 直接连接视频参考音频端口 |
| `12-split-task.json` | 提交任务与等待节点分开 |
| `13-json-tools.json` | JSON 构造与预览 |

`14-realtime-and-image.json` 是可拖入的图形工作流。先选择连接和实时模型，通过语音或文字完成对话并冻结或应用，再运行工作流读取结果。快照 ID 初始为空，不引用其他机器的录音。麦克风需要受信任 HTTPS 或 localhost。

示例不依赖已移除的 Moderation、Search、Rerank、Embedding、NativeRequest、Midjourney、Suno 节点。旧音乐、Midjourney、原生请求示例不再作为本版本用法。

模拟音频、图片和视频只验证协议与媒体连接，不代表真实模型效果；参考角色、数量和组合以当前连接的能力声明为准。
