# 示例工作流

示例默认使用 `mock` 离线连接。先运行 `tools/mock_gateway.py`，在 ComfyUI 中配置同名连接；切换到真实连接后会按平台实际规则调用和计费。

`api/` 下是 ComfyUI API 格式工作流，可导入 ComfyUI，或放入 `{"prompt": 工作流}` 发送到本机 `/prompt`。

| 示例 | 内容 |
|---|---|
| `01-chat.json` | 普通对话 |
| `02-stream.json` | SSE 对话，保留温度 `0` |
| `03-image.json` | 图片生成，保留 `seed=0` 和 `watermark=false` |
| `04-image-edit.json` | 生成图片直接连接参考图片输入，自动调用同步图生图 |
| `05-image-response.json` | 图片、URL 和完整响应三个输出 |
| `06-video.json` | 统一视频生成及原生 SaveVideo |
| `07-video-references.json` | 图片直接连接视频 `first_frame` 与 `last_frame` |
| `08-speech-transcribe.json` | TTS、原生 AUDIO、兼容转录模型及 SaveAudio |
| `10-claude-messages.json` | 普通对话节点使用 Claude Messages |
| `11-video-audio-reference.json` | TTS 的 AUDIO 直接连接视频参考音频端口 |
| `12-split-task.json` | 仅提交视频任务并查看响应 |
| `12-resume-task.json` | 在任务历史中选择或填写任务 ID，恢复等待并保存视频 |
| `13-json-tools.json` | JSON 构造与预览 |
| `15-video-prompt-tuner.json` | 独立视频提示词调优与结果预览，不提交视频生成 |

`14-realtime-and-image.json` 是可拖入的图形工作流。先选择连接和实时模型，通过语音或文字完成对话并冻结或应用，再运行工作流读取结果。快照 ID 初始为空，不引用其他机器的录音。麦克风需要受信任 HTTPS 或 localhost。

示例不依赖已移除的 Moderation、Search、Rerank、Embedding、NativeRequest、Midjourney、Suno 节点。旧音乐、Midjourney、原生请求示例不再作为本版本用法。

图片节点只使用同步接口：未传参考图片时文生图，传入图片或图片参考组时图生图。输出顺序为 `image`、`url`、`响应`；多张图片保留为 IMAGE 批次，`url` 为首个上游 URL，完整结果在响应中。仅返回 base64 时 URL 为空。图片异步任务示例已移除。

视频节点输出顺序为 `video`、`url`、`response`（索引 `0/1/2`）；视频可直连 SaveVideo。拆分任务示例分两次执行：先运行 `12-split-task.json`，再在 `12-resume-task.json` 的等待节点中选择刚提交的任务或填写其 ID，不会重新提交生成。

模拟音频、图片和视频只验证协议与媒体连接，不代表真实模型效果；参考角色、数量和组合以当前连接的能力声明为准。

`15-video-prompt-tuner.json` 显式提供节点缓存标识，适合直接发送 API 格式工作流。画布中的新调优节点会自动生成并保存该标识，复制节点会分配新的标识。调优节点本身不是输出节点，只有输出连接到预览或后续创作时才参与整图执行；画布上的“单独调优”会附加临时预览执行目标，仅执行本节点及所需上游。完整流程可将优化提示词输出 `0` 接到视频节点的提示词输入，将分析输出 `1` 或结构化结果输出 `2` 接到预览节点。
