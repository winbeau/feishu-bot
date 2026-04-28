# Feishu Bot Gateway

飞书消息到 Dify 的 FastAPI 网关。当前实现支持飞书文本、图片、文件消息解析，Redis 会话与去重，Dify streaming/blocking 调用，以及本地文件解析后通过 Dify `inputs` 传递给应用。

## 功能状态

- 飞书 webhook：支持 URL challenge、token 校验、消息去重、文本回复。
- 统一消息模型：`UnifiedMessage` 支持 `text`、`image`、`file` 和附件 metadata。
- 会话管理：按 `platform + user_id` 在 Redis 中维护业务 session id。
- 附件链路：下载飞书 image/file 到本地临时目录；图片发布为公网 URL 后传给 Dify，文件解析后写入 `parsed_text` 和 `file_tags`。
- 文件解析：支持 `txt`、`md`、`csv`、`pptx`、`pdf`。
- Dify 后端：默认 `streaming`，构造 `inputs`，公网图片 URL 才作为 Dify `files.remote_url` 传递。
- 健康检查：`GET /health` 检查 Dify 后端可用性。

未完成或占位：

- 微信、QQ 适配器尚未接入主链路。
- 对话摘要当前是简单拼接截断，不调用 LLM 生成摘要。

## 技术栈

- Python 3.12
- FastAPI
- uv
- httpx
- Redis
- Dify Chat API
- pytest
- Docker Compose

## 目录结构

```text
app/
  backends/        # LLM 后端适配器，当前为 Dify
  core/            # 平台无关模型、路由、会话、去重
  platforms/       # 平台适配器，当前主链路为飞书
  services/        # 飞书文件下载、文件解析等服务
  main.py          # FastAPI 入口
tests/
  unit/
  integration/
docker-compose.yml # 本地 Redis
```

## 环境变量

先复制样例：

```bash
cp .env.example .env
```

然后编辑 `.env`：

```bash
FEISHU_APP_ID=你的飞书应用 App ID
FEISHU_APP_SECRET=你的飞书应用 App Secret
FEISHU_VERIFICATION_TOKEN=你的飞书事件订阅 Verification Token

DIFY_API_KEY=你的 Dify App API Key
DIFY_BASE_URL=http://你的-dify-服务器/v1
DIFY_RESPONSE_MODE=streaming
DIFY_FILE_UPLOAD_TIMEOUT_SECONDS=30
DIFY_FILE_UPLOAD_MAX_BYTES=15728640
DIFY_IMAGE_DEFAULT_QUERY=请分析这张图片
PUBLIC_FILE_BASE_URL=https://你的机器人域名
PUBLIC_FILE_DIR=/tmp/feishu-bot-public-files

REDIS_URL=redis://localhost:6380/0

FEISHU_FILE_DOWNLOAD_DIR=/tmp/feishu-bot-files
FEISHU_FILE_DOWNLOAD_TIMEOUT_SECONDS=30
FEISHU_FILE_MAX_BYTES=104857600

FILE_FULL_TEXT_MAX_BYTES=65536
DIFY_PARSED_TEXT_MAX_CHARS=12000

SUMMARY_TTL_SECONDS=604800
SUMMARY_MAX_CHARS=2000
```

安全要求：

- 不要提交 `.env`。
- 不要把真实 `DIFY_API_KEY`、`FEISHU_APP_SECRET` 写进 README、代码或公开 issue。
- 如果真实 key 曾经进入 GitHub 提交历史，应立即在 Dify/飞书后台轮换密钥。

Dify 图片要求：

- Dify 应用需要开启图片上传能力。
- 飞书图片会先下载到本地，再复制到 `PUBLIC_FILE_DIR`，通过 `PUBLIC_FILE_BASE_URL/public/files/<随机文件名>` 生成 Dify 可访问的公网 URL。
- 聊天请求会在顶层 `files` 字段使用 Dify 官方 `remote_url` 方式引用图片，同时 `inputs.image_urls` 也会包含该 URL。
- `PUBLIC_FILE_BASE_URL` 推荐配置为飞书 webhook 同一个 HTTPS 域名；临时只用 IP 时可以配置为 `http://120.46.94.148`，前提是 Dify 能访问该地址。
- 纯图片消息没有文本时，`query` 默认使用 `DIFY_IMAGE_DEFAULT_QUERY`。
- `DIFY_FILE_UPLOAD_MAX_BYTES` 只限制保留的 Dify 文件上传服务；当前飞书图片主链路不调用 `/files/upload`。飞书下载大小仍由 `FEISHU_FILE_MAX_BYTES` 控制。

## 本地开发

安装依赖并启动 Redis：

```bash
uv sync --group dev
docker compose up -d redis
```

运行测试：

```bash
uv run pytest tests/ -q
```

启动服务：

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

检查健康状态：

```bash
curl http://127.0.0.1:8000/health
```

如果 `.env` 中的 Dify 配置正确，返回类似：

```json
{
  "ok": true,
  "backends": {
    "dify": true
  }
}
```

## 本地模拟 Dify 链路

不依赖真实飞书回调，可以直接构造飞书 payload 测试解析和 Dify：

```bash
uv run python -c $'import asyncio, json\nfrom dotenv import load_dotenv\nfrom app.platforms.feishu import FeishuAdapter\nfrom app.backends.dify import DifyBackend\n\nasync def main():\n    load_dotenv()\n    raw={\"schema\":\"2.0\",\"header\":{\"event_type\":\"im.message.receive_v1\"},\"event\":{\"sender\":{\"sender_id\":{\"open_id\":\"ou_local_sim_user\"}},\"message\":{\"message_id\":\"om_local_sim_001\",\"chat_id\":\"oc_local_sim_chat\",\"message_type\":\"text\",\"content\":json.dumps({\"text\":\"这是一条本地模拟飞书消息。请用一句中文回复确认你收到了。\"}, ensure_ascii=False)}}}\n    message=await FeishuAdapter().parse_incoming(raw)\n    message.session_id=\"local-sim-session-001\"\n    answer=await DifyBackend().chat(message, message.session_id)\n    print(answer)\n\nasyncio.run(main())'
```

## 飞书事件配置

飞书开放平台中需要配置：

1. 创建或选择企业自建应用。
2. 配置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`。
3. 在事件订阅中配置 `Verification Token`，写入 `FEISHU_VERIFICATION_TOKEN`。
4. 订阅消息接收事件：`im.message.receive_v1`。
5. 将请求地址配置为：

```text
https://你的域名/feishu/webhook
```

6. 确认应用具备读取消息资源和发送消息所需权限，并发布应用版本。

本项目当前使用 token 校验，不包含飞书加密回调解密和 `X-Lark-Signature` 校验。

## Dify 应用配置

Dify 应用需要在用户输入表单中配置这些变量，当前代码会通过 `inputs` 传入：

- `feishu_user_id`
- `session_id`
- `message_type`
- `file_list`
- `image_urls`
- `parsed_text`
- `file_tags`
- `conversation_summary`

当前 payload 行为：

- `query` 使用飞书文本内容或文件消息附带的文本。
- `conversation_id` 固定传空字符串。
- `user` 使用飞书用户 open_id。
- `file_list`、`image_urls`、`file_tags` 是 JSON 字符串。
- 飞书图片会由服务发布为公网 `http(s)` 图片 URL，并加入 Dify `files.remote_url`。
- 本地下载路径不会传给 Dify `remote_url`。

## 服务器部署

以下以一台 Linux 服务器为例，假设域名已经解析到服务器，并准备使用 Nginx 做 HTTPS 反代。

### 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y git curl nginx
```

安装 uv：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

确认：

```bash
uv --version
python3 --version
```

### 2. 拉取代码

```bash
cd /opt
sudo git clone https://github.com/你的用户名/feishu-bot.git
sudo chown -R "$USER":"$USER" /opt/feishu-bot
cd /opt/feishu-bot
```

安装依赖：

```bash
uv sync --group dev
```

生产环境也可以不安装 dev 依赖：

```bash
uv sync
```

### 3. 配置环境变量

```bash
cp .env.example .env
nano .env
```

填入真实的飞书、Dify、Redis 配置。

服务器上 Redis 如果也用 Docker Compose，可以复用仓库里的配置：

```bash
docker compose up -d redis
```

如果使用系统 Redis 或云 Redis，修改：

```bash
REDIS_URL=redis://你的-redis-host:6379/0
```

### 4. 先做启动前检查

```bash
uv run pytest tests/ -q
uv run python -c "from app.main import validate_required_configuration; validate_required_configuration(); print('env ok')"
uv run python -c "import asyncio; from app.backends.dify import DifyBackend; print(asyncio.run(DifyBackend().health_check()))"
```

最后一条应输出：

```text
True
```

### 5. 使用 systemd 启动

创建服务文件：

```bash
sudo nano /etc/systemd/system/feishu-bot.service
```

写入：

```ini
[Unit]
Description=Feishu Bot Gateway
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/feishu-bot
EnvironmentFile=/opt/feishu-bot/.env
ExecStart=/opt/feishu-bot/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now feishu-bot
sudo systemctl status feishu-bot
```

查看日志：

```bash
journalctl -u feishu-bot -f
```

### 6. 配置 Nginx 反向代理

创建配置：

```bash
sudo nano /etc/nginx/sites-available/feishu-bot
```

写入：

```nginx
server {
    listen 80;
    server_name 你的域名;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

启用：

```bash
sudo ln -s /etc/nginx/sites-available/feishu-bot /etc/nginx/sites-enabled/feishu-bot
sudo nginx -t
sudo systemctl reload nginx
```

建议再用 Certbot 配置 HTTPS：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名
```

### 7. 验证线上服务

```bash
curl https://你的域名/health
```

飞书 webhook 地址：

```text
https://你的域名/feishu/webhook
```

在飞书开放平台保存事件订阅 URL 时，飞书会发送 challenge 请求。本项目会在 token 正确时返回 challenge。

## GitHub 发布前检查

发布前建议执行：

```bash
git status --short
uv run pytest tests/ -q
rg "app\\.platforms|app\\.backends" app/core
rg "app-[A-Za-z0-9]|FEISHU_APP_SECRET|DIFY_API_KEY=" .
```

最后一条用于检查是否误提交了真实密钥。它可能会匹配 README 或 `.env.example` 中的占位说明，需要人工确认没有真实值。

提交并推送：

```bash
git add -A
git commit -m "docs: add deployment readme"
git branch -M main
git remote add origin https://github.com/你的用户名/feishu-bot.git
git push -u origin main
```

如果 remote 已存在：

```bash
git remote -v
git push
```

## 常见问题

### `/health` 返回 503

通常是 Dify 配置不可用：

- 检查 `DIFY_BASE_URL` 是否以 `/v1` 结尾。
- 检查 `DIFY_API_KEY` 是否属于当前 Dify 应用。
- 如果图片消息失败，检查 Dify 应用是否开启图片上传能力，以及 `DIFY_FILE_UPLOAD_MAX_BYTES` 是否小于实际图片大小。
- 在服务器上直接执行：

```bash
curl -i -H "Authorization: Bearer $DIFY_API_KEY" "$DIFY_BASE_URL/parameters"
```

### 飞书事件订阅校验失败

- 检查飞书后台的 Verification Token 是否与 `.env` 中一致。
- 检查服务是否已经通过公网 HTTPS 暴露。
- 查看服务日志：

```bash
journalctl -u feishu-bot -f
```

### 重复消息被忽略

这是预期行为。飞书可能重试同一个 `message_id`，本项目用 Redis 做去重，重复消息直接返回 `{"ok": true}`，不会再次调用 Dify。

### 附件下载失败

用户会收到固定回复：

```text
文件下载失败，请稍后重试
```

需要检查：

- 飞书应用是否有读取消息资源权限。
- `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 是否正确。
- 文件是否还在消息资源有效期内。
- 服务器是否能访问 `https://open.feishu.cn`。

## 开发命令速查

```bash
uv sync --group dev
docker compose up -d redis
uv run pytest tests/ -q
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
curl http://127.0.0.1:8000/health
```
