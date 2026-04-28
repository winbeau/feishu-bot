# Bot Gateway — CLAUDE.md

## 项目一句话
多平台（飞书 / 微信 / QQ）统一消息网关，后端 LLM 通过 Dify 编排，支持论文库 RAG。

## 技术栈
- Python 3.12 + FastAPI
- uv（包管理 / 虚拟环境，替代 pip + venv）
- pytest（测试框架）
- httpx（异步 HTTP 客户端）
- Redis（消息去重 / 会话状态）
- Docker Compose（本地开发环境）

## 目录结构规范
```
bot-gateway/
├── CLAUDE.md              # 本文件，AI 开发宪法
├── claude-progress.txt    # 跨 session 进度日志（每次 session 结束必须更新）
├── feature_list.json      # 功能清单（passes 字段 true/false，禁止删除条目）
├── init.sh                # 环境初始化脚本
├── app/
│   ├── core/              # 平台无关核心逻辑（消息模型、路由、会话）
│   ├── platforms/         # 各平台适配器（feishu / wechat / qq）
│   ├── backends/          # LLM 后端适配器（dify / openai / ...）
│   └── main.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── conftest.py
└── docker-compose.yml
```

## 核心架构原则
1. **平台适配器模式**：每个平台实现 `PlatformAdapter` 抽象接口，网关核心不感知平台差异
2. **后端适配器模式**：每个 LLM 后端实现 `LLMBackend` 抽象接口，业务层不感知后端差异
3. **消息统一模型**：所有消息先转换为内部 `UnifiedMessage`，再转为平台格式发出
4. **禁止跨层直接调用**：platforms → core → backends，禁止反向依赖

## 开发工作流（必须遵守）

### 每次 session 开始时，必须按顺序执行：
```bash
pwd                              # 确认工作目录
cat claude-progress.txt          # 读取上次进度
cat feature_list.json            # 读取功能清单，找到最高优先级未完成项
git log --oneline -10            # 了解最近提交
bash init.sh                     # 启动开发环境并运行冒烟测试
```

### 每次实现功能时，必须按顺序执行：
1. **先写测试**：在 `tests/` 中写测试，运行确认为 RED（失败）
2. **再写实现**：最小实现使测试变 GREEN
3. **重构**：在测试保护下重构，保持 GREEN
4. **提交**：`git commit -m "feat: [功能名] - tests pass"`
5. **更新清单**：将 feature_list.json 中对应条目的 `passes` 改为 `true`
6. **更新进度**：追加到 claude-progress.txt

### 每次 session 结束时，必须：
```bash
uv run pytest tests/ -q           # 确认所有测试绿色
git add -A && git commit -m "chore: session end - update progress"
# 在 claude-progress.txt 末尾追加本次 session 摘要
```

## 禁止行为（违反视为严重错误）
- ❌ 禁止在测试未写之前先写实现代码
- ❌ 禁止在测试失败状态下 commit
- ❌ 禁止删除或修改 feature_list.json 中的测试步骤（只能修改 passes 字段）
- ❌ 禁止一次性实现多个 feature（每次只做一个）
- ❌ 禁止在 claude-progress.txt 为空时认为项目完成
- ❌ 禁止硬编码平台特定逻辑到 core 层

## 平台适配器接口规范
```python
class PlatformAdapter(ABC):
    async def parse_incoming(self, raw: dict) -> UnifiedMessage: ...
    async def verify_signature(self, request: Request) -> bool: ...
    async def send_message(self, msg: UnifiedMessage) -> bool: ...
    async def handle_challenge(self, request: Request) -> Response | None: ...
```

## LLM 后端接口规范
```python
class LLMBackend(ABC):
    async def chat(self, message: UnifiedMessage, session_id: str) -> str: ...
    async def health_check(self) -> bool: ...
```

## 测试规范
- 单元测试：mock 所有外部 IO，速度 < 100ms/个
- 集成测试：使用 `httpx.AsyncClient` + `TestClient`，mock 第三方平台 API
- 每个 platform adapter 必须有：签名验证测试、消息解析测试、发送测试
- 每个 backend 必须有：正常响应测试、超时测试、错误重试测试
