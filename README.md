# EasyAPI 创作工具 · ComfyUI

在 ComfyUI 中使用 EasyAPI 对话、图片、视频、音频及实时语音。模型列表按当前连接的权限和所选操作筛选，常用参数直接填写，模型专属参数按需显示。

## 安装

要求 Python 3.11+ 和 ComfyUI V3 节点接口；验证基线为 ComfyUI 0.41.2 / 前端 1.55.17。

1. 将 `ComfyUI-EasyAPI` 放到 `ComfyUI/custom_nodes/`。
2. 使用 **ComfyUI 实际运行的 Python** 安装依赖：

   ```shell
   python -m pip install -r custom_nodes/ComfyUI-EasyAPI/requirements.txt
   ```

3. 重启 ComfyUI，刷新浏览器，在 `EasyAPI` 分类中添加节点。

不需要本地大模型。依赖复用 ComfyUI 的 PyTorch、Pillow、NumPy；前端扩展已随包提供，无需 Node.js 编译。

## 连接与模型

从左侧 **EasyAPI** 打开连接管理，也可以从顶部扩展菜单或任意 EasyAPI 节点进入同一个面板。新连接默认使用官方网关 `https://token.easyapi.com`。

- **官网授权**。跳转到 EasyAPI 官网同意授权。ComfyUI 后端为当前实例领取 30 天的专用令牌。重新授权可继续使用，吊销在 EasyAPI 控制台完成。内网远程部署请通过 HTTPS 打开 ComfyUI。
- **手动 API Key**。适合已有密钥或自定义网关，可在 ComfyUI 的同源 HTTP 或 HTTPS 页面保存。已保存的密钥只显示尾号，编辑时留空会保留原值。
- **部署密钥**。在 ComfyUI 服务进程设置 `EASYAPI_API_KEY_FILE` 指向挂载的 Secret 文件，或设置 `EASYAPI_API_KEY`。可通过 `EASYAPI_BASE_URL` 指定网关。侧栏显示只读连接 `easyapi-env`。

「测试连接」只读取权限模型列表。工作流只保存连接别名。官网授权的专用令牌仅由 ComfyUI 后端保存和使用。

### 创作预设

可按连接、操作和模型保存命名预设。设为操作默认的预设会给新节点填入它的模型和参数；打开已有工作流时使用工作流保存的模型和数值。也可在节点中主动应用预设，或把刚调好的模型参数保存为预设。

先选连接，再选操作和兼容模型。实时对话、TTS、转录等能力分开筛选，不把同属音频类视为可以互换。模型列表为空时，检查连接状态、Key 权限及该操作是否有兼容模型；刷新不会自动切换到另一个付费连接。

工作流只保存连接别名，Key 存在 ComfyUI 后端的持久用户目录。可访问同一 ComfyUI 实例的用户属于同一可信工作区，能够使用已配置连接；对外开放应由部署层提供访问控制。

## 当前节点

| 节点 | 用途 |
|---|---|
| EasyAPI 对话 | Chat、Responses、Claude Messages、Gemini 和文本补全 |
| EasyAPI 图片 | 图片生成、编辑和异步图片任务 |
| EasyAPI 视频 | 使用统一 `/v1/videos` 接口生成视频 |
| EasyAPI 语音合成 | 文本转原生 AUDIO |
| EasyAPI 转录与翻译 | 使用兼容模型处理 AUDIO 或带音轨的 VIDEO |
| EasyAPI 创作工具 | 平台启用的去背景、去水印、超分、图片文字和动作视频工具；使用平台配置的模型 |
| EasyAPI Token 计数 | 对兼容消息请求计数 |
| EasyAPI 素材与参考组 | 组合额外素材、已有 URL 与参考角色 |
| EasyAPI JSON 工具 | 解析、读取、合并、设置、追加及格式化 JSON |
| EasyAPI 查询任务 / 等待任务 | 查询或恢复已有任务，不重新提交生成 |
| EasyAPI 下载结果 | 下载并解码已有结果 |
| EasyAPI 实时对话 | 浏览器麦克风或文字会话，冻结后供工作流读取 |
| EasyAPI 预览 | 预览文本、JSON、图片、音频、视频及素材组 |

共 14 个注册节点。当前不注册 Moderation、Search、Rerank、Embedding、NativeRequest、Midjourney、Suno 七个旧节点，旧工作流中的这些节点需要重建。高级参数是当前操作的参数 JSON，不是任意接口代理；仓库保留的内部协议适配不表示旧节点仍可使用。

## 表单与语言

基础表单提供提示词和常用设置，动态表单只显示当前模型已声明的参数。高级 JSON 默认折叠，保留 `0`、`false`、数组和对象；表单与 JSON 编辑同一份参数，不需要填写同一字段两次。切换连接、模型或操作后，以当前组合的能力和参数为准。

节点名称、输入输出及控件跟随 ComfyUI 的语言设置，提供 English（`en`）、简体中文（`zh`）和繁體中文（`zh-TW`）节点定义。模型 ID、协议值、参考角色和 JSON 键保持原值，不因翻译改变。

## 视频与素材

视频节点统一使用 `/v1/videos`。可直接连接：

- `first_frame`、`last_frame`：首帧与尾帧 IMAGE；
- `reference_image_1/2`：参考图片；
- `reference_video_1/2`：参考视频；
- `reference_audio_1/2`：参考音频；
- `references`：由素材节点组合的额外参考组。

输入模式默认 `auto`，根据已连接素材和模型声明选择。端口可用性、素材数量及组合限制以当前模型为准；尾帧、首尾帧和多模态参考并非每个模型都支持。

支持文件或内联的接口直接使用本地素材；需要公网 URL 的接口通过配套 `/v1/media` 转存。旧网关没有素材接口时，URL-only 能力需使用已有公网链接。单素材默认上限 100 MiB，大文件通过文件流传输。具体边界见 [接口覆盖清单](docs/INTERFACE-COVERAGE.md)。

## 任务与实时会话

生成节点每次明确排队都会发起新请求。异步操作可关闭“等待结果”，把任务输出连接到查询、等待或下载节点。任务 ID 和轮询信息保存在持久用户目录；停止等待、超时或重启后可以恢复。停止本地等待不等于取消上游任务，提交超时且未收到 ID 时应先核对平台任务，避免重复提交。

实时对话使用节点上的连接、语音/文字、静音、打断及结束控件。冻结或应用对话后，再执行工作流读取文本和 AUDIO 快照。活跃会话独立于生成队列，导入工作流不会自动开启麦克风。

麦克风需要受信任 HTTPS 或 localhost，普通内网 HTTP 页面不能采音。Key 仅由后端加入上游连接；结束会话、关闭页面或移除节点会释放资源。

## 示例与验证

[示例工作流](examples/README.md) 包含图片编辑、视频首尾帧、语音转录、Claude Messages、视频参考音频及分离任务流程。示例默认引用 `mock` 连接，运行前先启动离线服务并配置同名连接：

```shell
python tools/mock_gateway.py --port 3199
```

本机模拟地址为 `http://127.0.0.1:3199`，公开测试 Key 为 `mock-only-key`；Docker 内使用模拟服务的容器网络地址。模拟服务不向外部网络发请求，生成的 PNG、WAV、MP4 只验证协议和媒体链路，不代表模型创作效果。

```shell
python -m pip install -e '.[test]'
python -m pytest tests -q
```

环境安装与回滚见 [部署说明](docs/DEPLOYMENT.md)，既有验证及其边界见 [验收记录](docs/VERIFICATION.md)。本轮不进行付费上游生成、上传或实时调用。

MIT License · EasyAPI
