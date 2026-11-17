# EasyAPI 创作工具 · ComfyUI

把 EasyAPI 的图像、视频、对话、音频、音乐与实时语音接进 ComfyUI 工作流。
常用任务使用专用节点，原厂协议与特殊参数使用高级工具节点。

## 安装

要求 Python 3.11+，ComfyUI V3 节点接口；验证基线为 ComfyUI 0.41.2 / 前端 1.55.17。

1. 将安装包中的 `ComfyUI-EasyAPI` 放到 `ComfyUI/custom_nodes/`。
2. 使用 **ComfyUI 实际运行的 Python** 执行 `python -m pip install -r custom_nodes/ComfyUI-EasyAPI/requirements.txt`。
3. 重启 ComfyUI，并刷新浏览器。节点分类为 `EasyAPI`。

不需要安装本地大模型。依赖复用 ComfyUI 的 PyTorch、Pillow、NumPy，不会重新安装 PyTorch。
前端扩展已经包含在安装包中，无需 Node.js 编译。

## 配置连接

在 EasyAPI 节点的连接管理界面配置连接别名、网关地址与 API Key。
官方网关为 `https://token.easyapi.com`；输入末尾的 `/v1` 会自动归一。
工作流只保存连接别名，密钥存在 ComfyUI 后端的持久用户目录中。

不要将共享 ComfyUI 直接当成多租户账户系统：可以访问实例的用户属于同一可信工作区，
可使用其中配置的连接。需对外开放时，应由部署层提供访问控制。

## 创作方式

- 对话节点支持 Chat、Responses、Claude Messages 与 Gemini；历史消息、工具定义和结构化输出参数放入参数 JSON。
- 图片、视频与音频节点可直接连接 ComfyUI 的原生媒体节点。编辑图片需要图片输入，蒙版使用 MASK 输入。
- 视频多参考素材保留首帧、尾帧、参考图、参考视频和参考音频的角色。
- 生成/提交节点每次明确排队会发起新请求。查询、等待和下载节点只处理已有任务。
- 模型选择器只显示当前连接可用的模型，并可随时刷新。缺少参数文档的模型仍可手填名称、使用 JSON 参数。
- JSON 中的 `0`、`false`、对象和数组会保留。已连接的模型与媒体输入优先于同名扩展字段。

原生请求节点用于完整的协议请求体；模型专属能力以平台实际开放情况为准。
接口路径与请求说明见 [接口覆盖清单](docs/INTERFACE-COVERAGE.md)。

## 本地素材

支持文件或内联数据的接口直接传递本地素材；需要 URL 的接口通过 EasyAPI 素材接口转存。
这要求网关部署本项目配套的 `/v1/media` 能力。旧网关没有该能力时，URL 模型需使用已有公网链接。

单素材默认上限 100 MiB。大文件使用文件流；多素材超过总请求限制时使用素材引用。
素材默认保留 72 小时，运行任务的素材受到保护，任务结束后至少保留 24 小时。

## 任务恢复

任务 ID 和轮询地址保存在 ComfyUI 用户目录下的 `easyapi/` 状态目录。
停止等待、网络中断或重启后，可在任务列表找到原任务继续查询。
平台没有取消接口时，停止本地等待不会取消上游任务或停止其计费。
提交超时且尚未收到任务 ID 时，需要先核对平台任务，避免重新提交。

## 实时麦克风

使用实时对话节点的连接、开始、静音、打断、结束按钮。
点击应用结果生成冻结快照，再运行工作流获取文字与 AUDIO 输出。
会话不占用普通生成队列，导出工作流不会恢复活跃麦克风连接。

麦克风需要受信任的 HTTPS 或 localhost；普通内网 HTTP 页面不能开麦。
API Key 只由后端加入上游连接。关闭页面、移除节点或结束会话会释放音频资源。

## 离线验证

```powershell
python -m pip install -r requirements.txt
python tools/mock_gateway.py --port 3199
```

测试连接地址为 `http://127.0.0.1:3199`，测试 Key 固定为 `mock-only-key`。
模拟网关不向外部网络发送请求。Docker 内应使用同一网络中的模拟服务名称。
测试音频、图片和视频是程序生成的协议素材，不代表任何上游模型的创作效果。

```powershell
python -m pip install -e '.[test]'
python -m pytest tests -q
```

测试环境安装与回滚见 [部署说明](docs/DEPLOYMENT.md)，本次验证结果见 [验收报告](docs/VERIFICATION.md)。
本轮不进行任何付费上游生成、上传或实时调用；真实模型效果、计费和厂商限制另行验收。

MIT License · EasyAPI
