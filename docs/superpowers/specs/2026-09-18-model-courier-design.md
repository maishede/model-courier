# ModelCourier 设计规格

状态：待用户审阅
日期：2026-09-18
审查：已完成第一轮架构自审；以下默认值是待实现验证的设计目标，不是性能承诺。

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

设备只依赖任务类型和 Schema。任务可以通过 `requires.provider` 和 `requires.model` 指定硬约束，也可以省略；指定后不得静默降级到其他实现。服务端按 Owner、任务类型、格式和硬约束匹配能力，再将具体 `capability_id` 固定到本次租约。Worker 只执行该绑定；更换实现需重新领取。

任务协议版本、Provider 插件接口版本和模型版本分别管理。`task_type` 中的 `.v1` 是任务 Schema 版本的唯一来源；协议外壳另有 `protocol_version`。新增任务类型通过管理员安装 Schema 注册包完成，不能由设备提交任意 Schema；服务端校验标准输入和结果，Provider 校验自己的命名空间选项。

标准音频结果包括文本、语言及可选分段，时间戳单位为秒。标准检测结果包括原图尺寸、标签、置信度及基于原图的像素坐标 `xyxy`。插件负责转换模型原生输出；可选字段缺失用明确的可选语义表达，不伪造时间戳或置信度。

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

真实 Provider 以可选插件包存在，核心仓库至少包含接口、Schema、Mock Provider 和插件开发示例。首版通过 `model_courier.providers` Python entry point 注册工厂，并由本地配置白名单启用。设备请求不能安装包、指定 Python 导入路径、模型下载 URL 或本地文件路径。

工厂创建 Provider 实例，Adapter 负责统一语义；不为每个模型增加独立网络协议。后续可增加本地 HTTP/gRPC 模型服务 Adapter。Worker Runtime 负责下载、心跳、结果上传和重试，Provider 仅接收本地输入与执行上下文。上下文提供截止时间、取消信号和进度报告。

推理在独立子进程中执行，避免阻塞心跳；首版每台 Worker 默认并发为 1。模型懒加载并在切换时释放，失联、超时或取消后先请求停止，必要时终止子进程。插件依赖冲突可以通过不同虚拟环境运行多个 Worker 进程解决，各自声明能力；不得假设所有模型框架能共存。

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
uploading → queued → leased → running → succeeded
                    │          ├→ failed
                    └──────────┴→ queued（可重试，延迟领取）
所有非终态 → expired / canceled
```

- `uploading`：任务已创建，输入尚未完整验证，不可领取。
- `queued`：等待匹配的 Worker。
- `leased`：Worker 已领取但尚未确认开始执行。
- `running`：Worker 已开始执行并持续发送心跳。
- `succeeded`：结果已上传且校验通过。
- `failed`：不可重试错误或尝试次数耗尽，保留规范化错误信息。
- `expired`：超过执行截止时间，不能再领取或提交结果。
- `canceled`：设备取消任务，后续心跳与完成请求被拒绝。

成功、失败、过期和取消都是终态。结果过期不改变成功状态，而是标记 `result_available=false`，下载返回 410。`waiting_for_worker` 是排队原因，不是独立状态；Worker 心跳过期后视为离线。

每次领取通过短写事务完成，并生成递增 `lease_generation` 和随机 `lease_token`。心跳、开始、失败、上传和完成请求必须匹配当前 Owner、Worker、代次和有效租约。旧 Worker 的迟到请求返回 409，不能覆盖新结果。任务截止时间和租约均以服务器时间为准；过期、取消、完成通过条件更新竞争，只有一个终态能生效。

系统保证至少一次执行、最多一个有效结果发布，不保证模型只执行一次。创建幂等键按 `(owner_id, device_id, idempotency_key)` 唯一，并绑定规范化请求摘要；相同请求返回原任务，不同请求返回 409。完成响应丢失时，当前成功尝试的相同结果摘要可重放并返回成功；不同摘要或旧代次不得写入。Provider 首版不承担付款、设备动作等不可重复副作用。

默认策略：

- 任务默认 TTL：24 小时。
- 领取租约：10 分钟，可由 Worker 心跳续租。
- 心跳每 30 秒发送，续租不能超过任务截止时间；长轮询最长 25 秒，等待时不得持有数据库事务。
- 网络错误和 Worker 崩溃：在截止时间内最多领取 3 次，退避 5 秒、30 秒；次数耗尽后失败。每次执行最长默认 10 分钟，允许管理员调整，心跳不延长执行时限。
- 输入格式错误直接失败；Provider 配置错误先将该能力标记为不可用，任务可改派其他匹配 Worker，仍受重试次数和截止时间约束。
- TTL 从创建时起计算，默认 24 小时、最大 7 天；未完成上传 1 小时后过期。设备取消后停止接收结果，Worker 在下次心跳时停止执行。
- 成功提交后删除输入；失败、过期、取消的输入最多保留 24 小时。结果保留 7 天，元数据和幂等记录保留 30 天，日志轮转。清理失败可重试。

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
- `protocol_version`
- `requested_provider`
- `status`
- `idempotency_key`
- `expires_at`
- `lease_owner`
- `lease_until`
- `attempt`
- `request_digest`、`requires`、`bound_capability_id`
- `lease_generation`、`lease_token_hash`、`next_attempt_at`
- `execution_deadline`、`result_available`、`result_expires_at`
- `created_at`、`updated_at`

文件只通过 artifact 引用关联，数据库不保存二进制内容。每个 artifact 记录 MIME 类型、字节数、SHA-256、路径、来源和过期时间。

创建时声明输入大小并预留磁盘配额，输入上传采用原始二进制流而非 base64。写入唯一临时文件，服务端计算摘要、核对大小后原子重命名，再记录完整 artifact；只有 `submit` 校验通过才入队。首版中断后整文件重传，不承诺分片续传。同一任务禁止并行写入；入队后输入不可修改。

结果先按任务和租约代次上传暂存，再以完成请求发布清单；服务端确认文件完整、结果 Schema 正确且租约有效后，事务提交成功状态。文件系统和 SQLite 不能做跨系统原子事务，因此启动和定期清理均需对账：回收孤立暂存文件，禁止发布缺失文件的结果。清理通过删除标记与引用检查执行，保护有效租约的输入和正在传输的文件。

## 7. API 边界

设备 API 的逻辑流程：

```text
POST   /v1/tasks
PUT    /v1/tasks/{task_id}/input
POST   /v1/tasks/{task_id}/submit
GET    /v1/tasks/{task_id}
GET    /v1/tasks/{task_id}/result
POST   /v1/tasks/{task_id}/cancel
```

Worker API 的逻辑流程：

```text
POST   /v1/workers/register
POST   /v1/workers/poll
POST   /v1/workers/tasks/{task_id}/heartbeat
POST   /v1/workers/tasks/{task_id}/start
POST   /v1/workers/tasks/{task_id}/fail
GET    /v1/workers/tasks/{task_id}/input
PUT    /v1/workers/tasks/{task_id}/result
POST   /v1/workers/tasks/{task_id}/complete
```

接口带 `/v1` 前缀，认证使用 `Authorization: Bearer`。密钥通过服务器本地管理 CLI 创建、轮换和撤销；Worker register 是已授权 Worker 的会话建立，不允许匿名自助注册。Owner、设备与 Worker 身份从密钥解析，不能由请求体指定权限范围。所有任务和 artifact 端点均校验权限。

创建返回 201，幂等重放返回 200，提交返回 202，无可领取任务返回 204。非法状态或租约返回 409，超限文件返回 413，Schema 错误返回 422，配额或请求频率限制返回 429，临时容量不足返回 503；重试响应携带 `Retry-After`。错误统一为 `code/message/retryable/request_id`，不得泄露文件路径、密钥或完整推理输入。结果未就绪返回 409，已清理返回 410。

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

首版资源默认值：图片最大 8 MiB，音频最大 32 MiB、时长最大 120 秒（解码后由 Worker 复核），单任务结果总量最大 16 MiB。每设备最多 10 个非终态任务，全实例最多 100 个非终态任务。文件总预算 10 GiB，包含输入、暂存、输出和预留量；磁盘可用空间不足 5 GiB 时拒绝新任务。创建时同时预留输入声明大小和结果预算，上传时按实际字节再次检查，避免并发绕过配额。

控制平面采用一个 API 进程、SQLite 本地磁盘及短事务；不在网络共享盘运行 SQLite。忙等待设定上限，超时返回可重试错误；启用外键、领取索引和 WAL checkpoint。备份使用 SQLite 在线备份或停服一致性快照，不能单独复制正在写入的主数据库文件。反向代理与应用的流式上传配置需实测，避免两层缓冲重复占盘。初始全局文件传输并发上限为 4；2C2G 的实际可用容量通过部署压测确定。

首版只支持异步图片和短音频任务，不适合实时语音对话、连续视频或机械运动闭环。Worker 不在线时不可承诺即时响应。媒体会经过公网服务器，HTTPS 不等于端到端加密；本地推理仍有电力和网络成本。

## 9. 首版范围

首版交付：

1. FastAPI 控制平面、SQLite WAL、文件存储和清理器。
2. 设备 REST API 及 Python 参考 SDK。
3. Python Worker Runtime、注册、能力上报、轮询、租约和结果回传。
4. 任务 Schema：`audio.transcribe.v1`、`vision.detect.v1`。
5. Mock Provider，供端到端测试使用。
6. Provider 插件接口、Factory 和能力清单示例。
7. 本地部署文档、协议文档和最小安全配置。

交付分两步：M0 验证 Mock 的协议与可靠性；M1 才是可用首版，需要一个真实视觉插件和两个可替换的语音插件。以 Ultralytics、Faster-Whisper、FunASR 为候选，在核实依赖和模型许可证、硬件兼容性后确定可发布组合。插件独立安装，不成为核心必需依赖。交付至少一个 ESP32 图片示例和一个树莓派音频示例；模拟验证与实机验证分别记录，硬件未验证时不得宣称完成。模型权重不随核心仓库分发。

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
- 输入未上传或摘要不符时，Worker 不能领取任务；并发领取同一任务只有一个当前有效租约。
- 旧 Worker 在重新分配、取消或超时后回传结果必须失败；完成响应丢失后可安全重放。
- 推理占满 GPU 或阻塞 Provider 时，Runtime 仍能续租、响应取消并执行超时终止。
- 重试次数耗尽、能力失效和无匹配 Worker 均有可观察状态，不产生无限重试。
- 两个设备和两个 Owner 的权限测试覆盖查询、输入、结果、租约及 artifact，禁止跨范围访问。
- 并发上传、磁盘不足、提交中崩溃和清理重启测试不发布半成品、不突破预留配额。
- M1 用真实图片和音频完成端到端处理，并在不修改设备请求和服务器代码的情况下切换两种语音 Provider。

## 11. 后续扩展路径

- 增加 MQTT 通知适配器，但保留 REST 作为可靠控制通道。
- 增加 WebSocket 或 SSE 的浏览器任务进度。
- 增加多租户、设备组、Worker 标签和策略路由。
- 增加 S3 兼容对象存储适配器。
- 增加 Provider 健康检查、模型预热和显存调度。
- 增加加密 artifact、端到端传输策略和审计日志。
- 增加更多语言 SDK 和嵌入式设备示例。
