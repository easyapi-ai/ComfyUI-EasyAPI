# Mac mini 测试环境

入口：`https://192.168.2.48:3443`；原 HTTP 入口仍为 `http://192.168.2.48:3106`。
两个入口均可运行常规节点、手动保存 API Key 和删除连接。局域网中的官网授权和浏览器麦克风使用 HTTPS 入口。

## 持久安装

- 插件挂载：`/Users/easyapi/AIWorkFlow/deploy/comfytv-cloud-nodes/ComfyUI-EasyAPI` → `/app/custom_nodes/ComfyUI-EasyAPI`，只读。
- 状态：原有 user 挂载下的 `easyapi/`。连接密钥、任务记录、实时快照在此持久化，不在插件目录。
- 当前版本、镜像 ID、源码校验值和原容器名称见 `/Users/easyapi/AIWorkFlow/deploy/comfytv-easyapi/installation.json`。
- 原有 models、input、output、user 和其他插件的 bind mount 保留。
- `NEW-DOCKER-RUN.sh` 已更新挂载、镜像和 EasyAPI 环境项。`comfytv-easyapi/start.sh` 可启动当前三个服务。

ComfyUI-Manager 曾在容器可写层升级核心，因此先把运行中核心保存为本地 baseline 镜像，
再构建插件依赖层。基线使用 PyAV 18.1，依赖要求 `av>=18.1`，避免旧版缺少 V3 视频接口。
镜像保留在本机，不会推送到镜像仓库。

离线模拟服务 `comfytv-easyapi-mock` 只加入专用 Docker 网络，不发布宿主机端口。
需要运行离线验收时，在插件连接管理中手动创建 ID 为 `mock` 的连接，网关填写
`http://comfytv-easyapi-mock:3199`，Key 填写公开测试值 `mock-only-key`。

## HTTPS 与证书

Caddy 服务 `comfytv-easyapi-https` 代理到同网络的 `comfytv:8188`，支持 WebSocket。
配置、CA 和持久化状态在 `comfytv-easyapi/https/`。为 IP 客户端配置 `default_sni`。
本次仅把 CA 公钥证书加入测试 Windows 的**当前用户**受信根证书库。
其他设备需自行信任该专用 CA 才能正常开麦；不要复制 CA 私钥。

移除 Windows 证书时，打开当前用户证书管理器，删除
`Caddy Local Authority - 2026 ECC Root` 对应的本次证书。
具体证书指纹应与 `https/data/caddy/pki/authorities/local/root.crt` 核对。

## 更新与回滚

交付工具：`tools/package_release.py` 生成带 SHA256 的 ZIP；`deploy/install_mac.py` 在空闲队列下安装。
SSH 密码由 `tools/remote_mac.py` 交互输入，不保存在文件或命令参数中。
重复安装使用最初的 baseline，安装前保留上一容器与配置备份。

回滚前确认 `/queue` 没有运行或等待任务，然后读取 `installation.json` 中的 `rollback_container`：

1. 停止当前 `comfytv`，将其改名留作排查。
2. 将记录中的上一容器改名为 `comfytv`，接回 `comfytv-easyapi` 网络并启动。
3. 按需恢复同目录 backup 中的原启动脚本。

不要删除或回退 user/input/output/models。只回退插件代码、镜像和服务配置。
部署副本中的完整 inspect/env 备份包含已有应用配置，权限为 0600，不应公开分享。

## 平台素材接口发布

配套后端改动在工作区的 `easyapi-ai-api`，包括两张 GORM 素材表、TokenAuth 素材接口、
视频素材归一与生命周期任务。接口文档为该仓库的 `docs/media-api.md`。
本次通过隔离数据库和模拟存储验证，尚未向生产数据库迁移或部署。
URL-only 模型处理本地素材，需先部署这部分能力；直接文件/内联及已有 URL 仍可使用。
