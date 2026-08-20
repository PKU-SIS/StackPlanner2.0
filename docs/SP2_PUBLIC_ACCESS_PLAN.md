# SP2.0 启动与公网访问方案

## 1. 当前验证结果

本机已经验证 SP2.0 可以运行：

| 组件 | 当前端口 | 状态 |
|---|---:|---|
| Gateway | `8101` | 已启动，`/health` 返回 200 |
| Next.js | `3100` | 已启动，首页返回 200 |
| nginx | `2027` | 已启动，代理首页和 `/health` 返回 200 |

默认端口仍然是 `8001/3000/2026`。因为服务器上已有其他 DeerFlow 实例占用默认端口，本次验证使用了 `8101/3100/2027`。

注意：当前实例是首次启动，Gateway 日志显示还没有 admin 账号。公网暴露前必须先完成 `/setup`，不要把未初始化的 `/setup` 直接暴露给公网。

## 2. 环境结论

SP2.0 的后端声明 `Python >=3.12`。现有 `df2` Conda 环境是 Python 3.10，不能直接作为 SP2.0 的 Python 解释器使用。

可复用 `df2` 环境中的 `uv`、Node.js 和 pnpm，同时由 uv 使用 Linux Python 3.12 管理项目环境：

```bash
cd /data/sp/jxk/xwx/SP2.0
conda activate df2

# 当前机器上已存在 uv 管理的 Python 3.12
uv venv backend/.venv --python 3.12
cd backend
UV_LINK_MODE=copy uv sync --all-packages
cd ../frontend
NPM_CONFIG_USERCONFIG=/dev/null pnpm install --frozen-lockfile
```

如果希望完全使用 Conda，应新建 Python 3.12 环境，而不是把 SP2.0 降级到 Python 3.10：

```bash
conda create -n sp20-py312 python=3.12 -y
conda activate sp20-py312
```

## 3. 本机启动

### 3.1 推荐的端口设置

当 `8001/3000/2026` 没有被其他实例占用时，直接执行：

```bash
make dev
```

当前服务器上已有端口占用时，使用环境变量切换端口。`scripts/serve.sh` 和 `scripts/nginx.sh` 已支持这三个变量：

```bash
cd /data/sp/jxk/xwx/SP2.0
conda activate df2
export GATEWAY_PORT=8101
export FRONTEND_PORT=3100
export NGINX_PORT=2027
export NPM_CONFIG_USERCONFIG=/dev/null  # 仅用于绕过失效的全局 npm 代理配置

./scripts/serve.sh --dev --daemon --skip-install
```

检查：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  curl -fsS http://127.0.0.1:2027/health
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  curl -I http://127.0.0.1:2027/
```

停止当前端口实例：

```bash
GATEWAY_PORT=8101 FRONTEND_PORT=3100 NGINX_PORT=2027 ./scripts/serve.sh --stop
```

## 4. 先完成本地初始化和登录

最安全的顺序是先通过 SSH 端口转发访问初始化页面：

```bash
# 在外部电脑执行；把 user、server 替换为实际值
ssh -N -L 2027:127.0.0.1:2027 user@server
```

然后在外部电脑浏览器打开：

```text
http://127.0.0.1:2027/setup
```

完成 admin 创建后，再从 `/login` 登录并验证：

```bash
curl -i http://127.0.0.1:2027/api/models
```

未登录时 `/api/models` 返回 `401` 是正常的；不要为了方便把认证关闭。

## 5. 外部访问方案选择

### 方案 A：SSH 隧道（最安全，适合少量固定用户）

不需要公网开放应用端口。每个用户使用自己的 SSH 账号和密钥：

```bash
ssh -N -L 2027:127.0.0.1:2027 user@server
```

访问：`http://127.0.0.1:2027`。

优点是没有公网扫描面；缺点是每个用户都要保持 SSH 连接，适合内部使用或管理员验证。

### 方案 B：Tailscale Serve（推荐的私有网络穿透）

让服务器和使用者加入同一个 Tailnet，然后在服务器执行：

```bash
tailscale serve --https=443 http://127.0.0.1:2027
```

使用 Tailscale 分配的 HTTPS 域名访问，并通过 Tailnet ACL 限制成员。Tailscale Serve 会把本地 HTTP 服务代理成 HTTPS；详见[官方 Serve 文档](https://tailscale.com/docs/reference/tailscale-cli/serve)。

### 方案 C：Cloudflare Tunnel（适合固定域名和长期运行）

生产环境创建命名 Tunnel，并把域名指向本地统一 nginx 端口 `2027`：

```bash
cloudflared tunnel login
cloudflared tunnel create sp20
cloudflared tunnel route dns sp20 sp20.example.com
```

创建配置文件，例如 `/etc/cloudflared/config.yml`：

```yaml
tunnel: <TUNNEL_UUID>
credentials-file: /etc/cloudflared/<TUNNEL_UUID>.json

ingress:
  - hostname: sp20.example.com
    service: http://127.0.0.1:2027
  - service: http_status:404
```

启动：

```bash
cloudflared tunnel run sp20
```

再在 Cloudflare Zero Trust 中配置 Access 登录策略，只允许指定邮箱、群组或身份提供商。Cloudflare 的 Quick Tunnel 命令如下，但只适合临时开发测试：

```bash
cloudflared tunnel --url http://127.0.0.1:2027
```

Quick Tunnel 有并发限制且不支持 SSE，不适合作为 SP2.0 的长期入口；详见[Cloudflare Tunnel 官方文档](https://developers.cloudflare.com/tunnel/setup/)。

### 方案 D：公网 IP/域名 + HTTPS 反向代理（适合正式部署）

不要直接把 `8101` 或 `3100` 暴露到公网，也不要长期使用 `http://公网IP:2027`。推荐：

1. DNS `A/AAAA` 记录指向服务器公网 IP。
2. 对外只开放 TCP `80/443`。
3. 在宿主机的 Caddy/Nginx/云负载均衡上终止 TLS。
4. 反向代理到 `http://127.0.0.1:2027`。
5. 保留 SSE 和长任务设置：`proxy_buffering off`、`proxy_read_timeout 600s`。

Nginx 示例：

```nginx
server {
    listen 443 ssl;
    server_name sp20.example.com;

    ssl_certificate     /etc/letsencrypt/live/sp20.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sp20.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:2027;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_read_timeout 600s;
    }
}
```

验证并 reload：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 6. 公网部署检查清单

- [ ] 已通过 SSH/Tailscale 完成 `/setup`，已创建 admin。
- [ ] 已使用 HTTPS；不把长期服务放在裸 HTTP 上。
- [ ] `8101`、`3100`、`2027` 未对公网开放；公网只开放 443，必要时开放 80 做证书签发。
- [ ] 统一从 `2027` 入口转发，不直接穿透 Gateway `8101`。
- [ ] `BETTER_AUTH_SECRET` 使用随机高强度值；生产 Docker 部署让 `make up` 持久化生成，手工部署则自行持久化保存。
- [ ] `GATEWAY_CORS_ORIGINS` 只填写真实的前端 Origin，例如 `https://sp20.example.com`，不要填写 `*`。
- [ ] 如使用统一 nginx 同源入口，前端无需单独配置 Gateway 公网地址。
- [ ] 生产环境可设置 `GATEWAY_ENABLE_DOCS=false`，减少 Swagger/OpenAPI 暴露面。
- [ ] 限制 Cloudflare Access、Tailscale ACL、VPN 或 SSH 用户范围，并定期检查 `logs/`。
- [ ] 服务器有 Docker 权限后，长期运行优先切换到 `make up`；当前环境 Docker socket 权限不足，已采用本地进程方式验证。

## 7. 推荐落地顺序

1. 当前先用 SSH 隧道完成 `/setup` 和登录验证。
2. 内部多人使用：采用 Tailscale Serve + Tailnet ACL。
3. 需要固定公网域名：采用 Cloudflare named Tunnel + Access，或宿主机 Nginx/Caddy + HTTPS。
4. 最后再配置 systemd/Docker 自启动、日志轮转、备份和监控。
