# 验收记录 · 2026-11-17

## 环境与约束

- Mac mini Docker `comfytv`，ComfyUI 0.41.2 / 前端 1.55.17，Python 3.11，CPU。
- 21 个 EasyAPI V3 节点；92 个操作目录（91 HTTP、1 WebSocket）。
- 生成、上传、实时上游均使用本地模拟网关与模拟存储；没有付费模型调用。
- EasyAPI 生产侧仅匿名读取模型定价、schema 和接口文档，未读取生产凭据或连接生产数据库。

## Python 与协议

`python -m pytest tests -q --disable-warnings`：**53 项通过、118 个子测试通过**。
覆盖 JSON、重复文件 multipart、SSE 跨 UTF-8 分包、二进制媒体、重定向凭据隔离、
连接轮换、`/api` 前缀 cookie、模型 schema 匹配、参数约束、恢复任务与在途中断。
真实 Pillow/PyTorch/PyAV 解码 PNG、WAV、MP4；实时桥的双向事件、TTL、断线快照和资源释放使用本地模拟上游。

## 真实 ComfyUI 执行

以下13条工作流已在实际队列执行成功，记录在 `output/verification/comfy.json`：

1. 对话
2. SSE 对话
3. 图片生成（保留 seed=0、watermark=false）
4. 生成图片直接连接图片编辑
5. 异步图片
6. 视频生成与原生 SaveVideo
7. 首帧/尾帧素材组 → 视频 → SaveVideo
8. TTS → AUDIO → 转录及 SaveAudio
9. Suno → SaveAudio
10. Midjourney → SaveImage
11. 原生 Messages 请求
12. 提交视频任务 → 独立等待节点 → SaveVideo
13. JSON 构造与输出

通过浏览器冻结的真实快照还已连接图片生成与预览执行成功，文字、PNG 和音频文件正确返回。
日志与工作流只包含 `mock` 连接别名；工作流不携带上游 Key。

## 浏览器实时与界面

使用受信任 HTTPS 和真实 Chrome 的测试音频设备 `Fake Default Audio Input`，没有录制环境中的私人语音。
实际调用浏览器 getUserMedia / AudioWorklet / WebSocket / WebAudio：

- 音频上下文 48 kHz，重采样为 16 kHz 上行，24 kHz 回放。
- 双向会话、文字转录、音频响应、静音和结束应用通过。
- 静音时 track.enabled=false；结束后 stream、socket 释放，播放 sources=0。
- 冻结快照持久化后可由有限的 Comfy 工作流读取。
- 实际连接设置界面完成创建并删除测试连接，验证 `/api/easyapi/*` 下的同源 cookie 与 nonce。
- 混合 IMAGE+AUDIO 预览使用明确媒体类型，避免 Nodes 2.0 误判为视频。
- 在 Canvas 与 Nodes 2.0 两种模式检查控件及混合预览；图片完成解码，音频 readyState=4、无播放错误。
- 容器重建后原快照仍可执行成功；凭据、任务与快照目录均保持在既有 user 挂载中。

物理麦克风的实际音色、回声环境和真实厂商会话，仍需后续真实设备/模型验收。

## EasyAPI 后端

新增15个顶层 TestMedia 测试通过，真实 TokenAuth 路由覆盖无 Key 401、跨用户 404、owner 上传/查询/删除。
使用临时 SQLite 与模拟对象存储；测试还包含跨渠道 URL → multipart → URL 重试、素材清理、
运行任务保护、终态保留、上传中断和删除失败恢复。

扩展回归为78 pass、2 skip、0 fail；之后增加的上传故障测试也已通过。
新增 `media` 文档契约后，`docs_version` 升级为 `model-docs/2`，相关文档/schema/素材测试再次通过。
Go 构建通过。未在真实 MySQL/PostgreSQL 上执行迁移，未部署生产。

## 明确未验证

真实上游生成效果、计费、余额和厂商限额；每个生产模型的素材组合；真实对象存储上传；
物理麦克风与真实实时模型；旧版 ComfyUI、所有第三方视频类型和其他浏览器。
接口目录及模拟协议通过，不等同于这些项目已真实验证。
