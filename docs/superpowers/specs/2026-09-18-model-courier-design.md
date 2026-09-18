# ModelCourier 设计规格

状态：待用户审阅
日期：2026-09-18

## 1. 项目定位

ModelCourier 是一个面向树莓派、ESP32 等低算力设备的出站式 AI 任务中继平台。

设备只需要访问公网服务，就可以把图片、音频或其他输入提交为异步任务。用户自己的本地 GPU Worker 主动连接公网服务，领取任务、调用本地模型并回传结果。GPU 机器不需要公网 IP、端口映射或内网穿透。

首版服务于单个 Owner 的多台设备和多台 Worker，同时在数据模型和任务协议中保留 `owner_id`，为将来的多租户扩展留出边界。

## 2. 目标与非目标

### 目标

- 让 ESP32、树莓派等设备通过 HTTPS 调用没有公网入口的本地模型。
- 支持设备离线、Worker 离线、网络中断和 Worker 重启后的任务恢复。
- 用稳定的版本化任务协议隔离设备端、服务器和具体模型后端。
- 通过可注册 Provider Adapter 接入 Faster-Whisper、FunASR、Ultralytics、Ollama 或自定义模型。
- 在 2C2G、40GB 磁盘的低配公网服务器上运行控制平面。
- 提供 Python Worker SDK 和可供嵌入式设备参考的 REST 协议。

### 非目标

- 首版不在公网服务器上运行模型推理。
- 首版不实现公开的多用户注册、计费、公共 GPU 共享或模型市场。
- 首版不要求 Redis、RabbitMQ、Celery 或 MQTT Broker。
- 首版不把任何一个模型后端写死在服务端核心中。
- 首版不承诺高并发或永久保存媒体文件。

## 3. 核心架构

```text
ESP32 / Raspberry Pi
        │ HTTPS REST
        ▼
公网控制平面（2C2G）
  API + SQLite WAL + 文件存储 + 清理器
        ▲
        │ HTTPS 长轮询 / 心跳 / 文件传输
        │
本地 GPU Worker
  Worker Runtime + Provider Factory + Provider Adapters
        │
  Faster-Whisper / FunASR / Ultralytics / 自定义后端
```

### 3.1 公网控制平面

控制平面负责设备认证、任务创建、输入文件接收、队列状态、Worker 租约、结果元数据和文件清理。

- API：Python FastAPI 或等价的 ASGI HTTP 服务。
- 元数据：SQLite，启用 WAL；数据库只保存任务和文件元数据，不保存二进制媒体。
- 文件：服务器本地文件目录，按任务隔离输入、处理中间文件和结果。
- 调度：数据库事务实现任务领取，不引入独立消息队列。
- 清理：后台清理器删除过期输入、结果、失败任务文件和中断上传。
- 入口：Caddy 或 Nginx 终止 TLS 并限制上传大小、请求速率和并发连接。

### 3.2 设备端

首版设备通过 HTTPS REST 工作。设备持有独立的设备密钥，可以：

1. 创建任务并声明任务类型、输入元数据、选项、幂等键和 TTL。
2. 上传图片或音频文件。
3. 查询任务状态。
4. 下载结构化结果和可选的结果文件。

设备默认主动轮询。以后可以增加 MQTT 或其他通知适配器，但通知不承担任务持久化职责。

### 3.3 Worker

Worker 运行在本地 GPU 机器上，只发起出站 HTTPS 请求。

Worker 启动后：

1. 使用 Worker 密钥注册并上报能力清单。
2. 通过长轮询或短间隔轮询领取匹配任务。
3. 下载输入文件并计算校验和。
4. 通过 Provider Factory 选择 Provider Adapter。
5. 执行本地模型推理，生成符合任务 Schema 的结果。
6. 上传结果、发送心跳并确认任务完成。

Worker 断电、进程崩溃或网络断开时，租约过期后任务重新进入队列。

## 4. 分层与扩展模型

ModelCourier 的稳定边界不是某一个模型，而是四层协议：

1. **设备通信协议**：REST 首发，MQTT 等作为可替换通知层。
2. **任务协议**：定义任务类型、版本、输入、选项、结果 Schema 和错误格式。
3. **Provider 接口**：定义本地推理 Provider 如何声明能力、初始化、执行和释放资源。
4. **文件与结果协议**：定义 artifact 引用、媒体格式、校验和以及结果清单。

### 4.1 任务类型

任务类型使用稳定、版本化的语义名称，而不是模型名称：

- `audio.transcribe.v1`
- `vision.detect.v1`

后续可以增加：

- `vision.vqa.v1`
- `audio.command.v1`
- `text.generate.v1`
- `embedding.create.v1`

设备只依赖任务类型和 Schema。任务可以指定偏好的 Provider，也可以只指定任务类型，由 Worker 根据能力和本地策略选择 Provider。

### 4.2 Provider Adapter 与 Factory

Provider 是任务类型的具体本地实现。核心只依赖抽象接口，不导入任何模型框架。

建议的概念接口：

```python
class Provider:
    def describe(self) -> CapabilityManifest: ...
    def validate(self, task: TaskEnvelope) -> None: ...
    def execute(self, task: TaskEnvelope, context: ExecutionContext) -> ProviderResult: ...
    def close(self) -> None: ...
```

Provider Factory 根据任务类型、Provider 名称、模型名称和 Worker 配置创建实现：

```text
audio.transcribe.v1 + faster-whisper -> FasterWhisperProvider
audio.transcribe.v1 + funasr         -> FunASRProvider
vision.detect.v1  + ultralytics     -> UltralyticsProvider
```

真实 Provider 以可选插件包存在，核心仓库至少包含接口、Schema、Mock Provider 和插件开发示例。插件可以通过 Python entry point 或明确的本地插件目录注册。

建议的包名：

- `model-courier-core`
- `model-courier-worker`
- `model-courier-provider-faster-whisper`
- `model-courier-provider-funasr`
- `model-courier-provider-ultralytics`

### 4.3 Worker 能力清单

Worker 注册时上报能力、Provider、模型、支持的输入格式和资源约束：

```json
{
  "capabilities": [
    {
      "task_type": "audio.transcribe.v1",
      "provider": "funasr",
      "models": ["paraformer"],
      "formats": ["wav", "mp3"]
    }
  ]
}
```

服务端只做能力匹配和任务生命周期管理，不理解 Provider 的内部参数。

## 5. 任务生命周期

任务状态：

```text
queued → leased → running → succeeded
                         └→ failed
queued / leased → expired
```

- `queued`：等待匹配的 Worker。
- `leased`：Worker 已领取但尚未确认开始执行。
- `running`：Worker 已开始执行并持续发送心跳。
- `succeeded`：结果已上传且校验通过。
- `failed`：不可重试的 Provider 或输入错误，保留规范化错误信息。
- `expired`：超过任务 TTL 或结果保留期限。

每次领取任务都会写入 `lease_owner`、`lease_until` 和 `attempt`。租约过期后，调度器可以安全回收任务。网络重试使用幂等键和结果校验，避免重复任务产生多个有效结果。

默认策略：

- 任务默认 TTL：24 小时。
- 领取租约：10 分钟，可由 Worker 心跳续租。
- 网络错误和 Worker 崩溃：自动重试。
- 输入格式错误、Provider 配置错误：直接失败并返回可读错误。
- 结果和输入文件的保留时间可配置。

## 6. 数据与文件模型

SQLite 只保存元数据，至少包括：

- `owners`
- `devices`
- `workers`
- `tasks`
- `artifacts`
- `task_events`

任务至少包含：

- `id`
- `owner_id`
- `device_id`
- `task_type`
- `schema_version`
- `requested_provider`
- `status`
- `idempotency_key`
- `expires_at`
- `lease_owner`
- `lease_until`
- `attempt`
- `created_at`、`updated_at`

文件只通过 artifact 引用关联，数据库不保存二进制内容。每个 artifact 记录 MIME 类型、字节数、SHA-256、路径、来源和过期时间。

## 7. API 边界

设备 API 的逻辑流程：

```text
POST   /v1/tasks
PUT    /v1/tasks/{task_id}/input
POST   /v1/tasks/{task_id}/submit
GET    /v1/tasks/{task_id}
GET    /v1/tasks/{task_id}/result
```

Worker API 的逻辑流程：

```text
POST   /v1/workers/register
POST   /v1/workers/poll
POST   /v1/workers/tasks/{task_id}/heartbeat
GET    /v1/workers/tasks/{task_id}/input
PUT    /v1/workers/tasks/{task_id}/result
POST   /v1/workers/tasks/{task_id}/complete
```

具体 HTTP 状态码、认证头、错误 Schema 和分页策略在实现计划中固定，接口必须带 `/v1` 版本前缀。

## 8. 安全与资源保护

- 所有公网通信要求 HTTPS。
- 设备和 Worker 使用独立随机密钥；服务器只保存密钥哈希。
- 设备密钥可以撤销和轮换，Worker 密钥同样适用。
- 任务 ID 使用不可预测标识符；设备只能读取自己创建的任务。
- 服务器不记录输入文件内容和模型 Prompt 到普通日志。
- 上传采用流式写盘，不把整段媒体加载进内存。
- 限制单文件大小、任务 TTL、每设备并发任务和总磁盘用量。
- 处理完成后按策略删除原始输入；过期结果和失败文件自动清理。
- Provider 运行在 Worker 本地，默认不允许通过任务输入执行任意系统命令。

2C2G 服务器只承担控制平面和小规模文件中转，不承诺高并发推理服务。未来需要多租户、对象存储或高并发时，再替换存储和调度实现。

## 9. 首版范围

首版交付：

1. FastAPI 控制平面、SQLite WAL、文件存储和清理器。
2. 设备 REST API 及 Python 参考 SDK。
3. Python Worker Runtime、注册、能力上报、轮询、租约和结果回传。
4. 任务 Schema：`audio.transcribe.v1`、`vision.detect.v1`。
5. Mock Provider，供端到端测试使用。
6. Provider 插件接口、Factory 和能力清单示例。
7. 本地部署文档、协议文档和最小安全配置。

Faster-Whisper、FunASR 和 Ultralytics 作为可选 Provider 接入，不进入服务端核心耦合层。首个真实 Provider 可以在核心协议验证完成后独立加入。

首版不包含：

- 公开注册和多租户管理界面。
- MQTT Broker、WebSocket 推送和计费。
- 公共 Worker 池或跨用户任务路由。
- 大规模对象存储和永久媒体归档。

## 10. 验收标准

- 一个模拟设备可以创建并上传图片任务，查询到排队状态。
- Worker 离线时任务不会丢失；Worker 上线后可以领取任务。
- Mock Provider 可以完成任务并回传结构化结果。
- Worker 在执行中断后，租约过期，任务可以再次领取。
- 相同幂等键不会创建多个有效任务结果。
- 输入文件、结果文件和 SQLite 元数据关联正确，清理器能删除过期文件。
- 将 Provider 从 Mock 替换为另一个实现时，服务器 API 和设备端协议无需修改。
- 文档能够让第三方根据接口实现新的 Provider。
- 在 2C2G 服务器上运行控制平面时，上传、排队、轮询和结果下载可用。

## 11. 后续扩展路径

- 增加 MQTT 通知适配器，但保留 REST 作为可靠控制通道。
- 增加 WebSocket 或 SSE 的浏览器任务进度。
- 增加多租户、设备组、Worker 标签和策略路由。
- 增加 S3 兼容对象存储适配器。
- 增加 Provider 健康检查、模型预热和显存调度。
- 增加加密 artifact、端到端传输策略和审计日志。
- 增加更多语言 SDK 和 ESP32 原生客户端示例。
