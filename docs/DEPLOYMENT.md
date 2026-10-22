# 单机部署：Web、后台 Worker 与 PostgreSQL

仓库提供一个 Docker Compose 部署包，包含 `web`、`worker` 和 PostgreSQL 17
三个服务。Web 与 Worker 使用同一个应用镜像、同一个持久数据库和同一份加密
配置。这个文件说明如何部署；仓库内的文件不表示服务已经上线，也没有预设
公共域名或可访问的公网地址。

## 准备条件

需要已安装并可使用的 Docker Engine 与 **Docker Compose v2**，以及能够访问
项目依赖和镜像仓库的网络。先检查：

```bash
docker version
docker compose version
```

本说明不执行或推荐任意下载的 Docker 安装脚本。若宿主机尚无 Docker，请按
所在操作系统及组织的安装方式准备。以下命令均在 PA Agent 仓库根目录执行。

## 创建本机配置并启动

```bash
python3 scripts/init_deploy_env.py
docker compose --env-file .env.deploy build
docker compose --env-file .env.deploy up -d
docker compose --env-file .env.deploy ps
```

初始化脚本生成被 Git 忽略的 `.env.deploy`，权限为 `0600`，创建随机数据库
密码、稳定的 Fernet 加密密钥和注册邀请码。它不打印这些值，也不会覆盖已有
文件。已有部署应继续使用原配置；不要为了升级重新生成密钥。

通过本机编辑器读取或调整配置。主要字段为：

| 字段 | 用途 |
|---|---|
| `POSTGRES_PASSWORD` | PostgreSQL 密码；Web 与 Worker 使用相同值连接数据库 |
| `PA_WEB_ENCRYPTION_KEY` | 加密用户保存的模型、数据源和通知凭据；必须长期保留 |
| `PA_WEB_INVITE_CODE` | 首次初始化时导入数据库的注册邀请码 |
| `PA_WEB_ADMIN_USERNAME` | 独立管理员用户名，新生成配置默认为 `pa_admin` |
| `PA_WEB_ADMIN_PASSWORD` | 随机生成的初始管理员密码，首次创建后不再重置密码 |
| `PA_WEB_IMAGE` | 应用镜像名称，默认 `pa-agent:local` |
| `PA_WEB_PORT` | 宿主机 Web 端口，默认 `8000` |
| `PA_WEB_TRUSTED_PROXIES` | 允许传递代理头的来源 IP，默认 `127.0.0.1` |

数据库名和数据库用户均为 `pa_agent`。Compose 项目名是 `pa-agent`，数据库
使用名为 `postgres_data` 的持久卷，默认实际名称为 `pa-agent_postgres_data`。
数据库没有映射到宿主机端口。

生成的 `POSTGRES_PASSWORD` 使用 URL 安全字符，因为 Compose 会把它嵌入
`DATABASE_URL`。手动替换时应保持 URL 安全字符；如果使用 URL 保留字符，需
另外正确编码连接 URL，不能直接把未编码密码拼入当前模板。

Web 默认仅绑定 `127.0.0.1:8000`；修改端口后仍只绑定回环地址。这适合由同一
宿主机上的 TLS 反向代理对外提供服务。初始本机检查地址为：

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/api/health
curl --fail --silent --show-error http://127.0.0.1:8000/api/status
```

若修改了 `PA_WEB_PORT`，将命令中的 `8000` 改成相同端口。`/api/health` 应返回
`ok: true`，随后确认 `/api/status` 的 `configured` 为 `true`。前一个检查只表明
Web 进程响应，后一个才会检查账户数据库配置。匿名访问时 `authenticated: false`
是正常结果。

在浏览器打开本机页面，使用邀请码注册账户。登录后进入后台挖掘页面，确认
Worker 在线；对应的 `/api/mining/status` 需要登录，不要把会话 Cookie 复制到
公开命令记录中。每位用户再在个人设置中填写自己的模型或数据源 API Key。

## 使用自己的域名和 TLS

公共访问需要你提供并配置实际域名、DNS 和有效 TLS 证书。先把该域名指向
宿主机，再使用宿主机已有的 Nginx、Caddy 或其他 TLS 代理，将请求转发到
`127.0.0.1` 上的 Web 端口。这里没有替你配置域名或申请证书。

代理必须保留原始 `Host`，并覆盖设置 `X-Forwarded-Proto`。例如，在已经完成
域名和证书配置的 Nginx HTTPS `server` 块中，可以采用以下转发部分；如端口
已调整，同步修改 `proxy_pass`：

```nginx
client_max_body_size 3m;

location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

关闭响应缓冲是为了及时传递分析流式结果。应用本身仍执行请求体大小限制。

Compose 中 `PA_WEB_TRUSTED_PROXIES` 默认为回环 IP。宿主机代理通过 Docker
发布端口连接后，容器实际看到的来源可能是 **Docker 网桥网关 IP**，因此不能
直接假设它仍为 `127.0.0.1`。可以只查看 Web 容器所在网络的网关：

```bash
docker inspect --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{println}}{{end}}' "$(docker compose --env-file .env.deploy ps -q web)"
```

结合实际网络路径核实哪个 IP 是代理连接在 Web 容器中的来源，将需要信任的
网关 IP 显式加入 `.env.deploy` 的 `PA_WEB_TRUSTED_PROXIES`，多个 IP 用逗号
分隔，然后重新创建 Web 服务：

```bash
docker compose --env-file .env.deploy up -d --no-deps web
```

不要将 `*` 设为默认信任来源，也不要信任所有公网客户端。这个配置决定应用
是否接受代理传来的 HTTPS 协议和客户端地址。若 HTTPS 页面发生“请求来源
无效”，先检查 `Host`、协议头和实际代理来源 IP；不要禁用来源检查。

最后通过你实际配置的 HTTPS 域名重复检查健康状态、登录和一次保存个人设置。
浏览器应使用 HTTPS，会话 Cookie 应带有 `Secure`。外部请求由 TLS 代理进入，
Web 的回环端口和 PostgreSQL 都无需直接暴露公网。

## 后台 Worker 与版本一致性

`worker` 运行独立的常驻进程 `python -m pa_agent.web.mining_worker`，不依赖浏览器
打开状态或 Web 请求结束后的线程。它与 Web 使用相同镜像和 PostgreSQL，
任务、检查点和产物存入数据库。挖掘任务租约独立于普通 PA 分析租约，不应
把 Worker 改成网页请求内启动的临时进程。

```bash
docker compose --env-file .env.deploy logs --tail=100 web worker
docker compose --env-file .env.deploy images
```

Web 和 Worker 的计算版本/代码摘要需要一致，网页只把兼容且心跳新鲜的
Worker 显示为可用。领取任务时不会按版本筛选，旧 Worker 可能先领取新版本
任务，再以 `runtime_changed` 拒绝并使任务失败；不能只依赖网页的在线状态。
升级时应协调更新并重新创建 Web 与 Worker，确保两者运行同一个新镜像。
Worker 停止时任务保留在数据库；恢复同版本进程后才能按检查点继续。CPU 搜索
支持每步检查取消和累计时间预算，具体规则见[公式挖掘说明](ALPHAGPT_MINING.md)。

## 受管网络中的构建证书

普通服务器使用上述 Compose 构建即可。如果当前受管环境的 HTTPS 代理需要
附加 CA，Dockerfile 支持 BuildKit 的 `combined_ca` 构建 secret。使用已存在、
经环境信任的合并证书链，例如：

```bash
docker build --secret id=combined_ca,src=/etc/ssl/certs/ca-certificates.crt -t pa-agent:local .
docker compose --env-file .env.deploy up -d --no-build
```

若 `.env.deploy` 指定了不同的 `PA_WEB_IMAGE`，构建标签必须与该值一致。这个
选项用于构建过程的 CA 信任，不需要关闭 TLS 校验；Docker 守护进程拉取基础
镜像所需的网络和代理信任仍由宿主机管理。不要把 API Key、邀请码或数据库
密码写入 Dockerfile、构建参数或镜像层。

## 备份、更新与停止

数据库与原有加密密钥需要一起保留。只有数据库而没有原 Fernet 密钥，将无法
解密用户已保存的凭据；重新生成数据库密码也不会自动更新已有数据库角色的
密码。需要轮换时，应把数据库角色修改与应用配置变更作为同一次维护操作。

更新前先停止提交新研究任务，等待正在运行的任务结束，或在界面取消不再需要
的任务。将备份写到仓库外的受限目录，例如：

```bash
umask 077
mkdir -p ../pa-agent-backups
docker compose --env-file .env.deploy exec -T postgres pg_dump -U pa_agent -d pa_agent --format=custom > ../pa-agent-backups/pre-upgrade.dump
cp .env.deploy ../pa-agent-backups/env.deploy
```

确认命令成功且备份文件有效，再更新代码并重新构建。上例文件名表示当前升级
备份；实际长期保留时使用带日期的文件名，避免覆盖唯一副本。数据库转储和
`.env.deploy` 都应保存在受控的备份位置，避免提交到 Git 或放在可下载的目录。
部署前应在独立数据库演练 `pg_restore`，不要把首次恢复尝试直接指向生产库。

```bash
docker compose --env-file .env.deploy build
docker compose --env-file .env.deploy up -d
docker compose --env-file .env.deploy ps
```

保留上一个镜像版本与升级前数据库备份。代码变更可能使旧任务的检查点版本
不兼容；不要强制跳过版本核验，应在新版本中明确重新提交任务。启动后重新
验证 `/api/status`、登录、Worker 在线以及一项小规模示例任务。

暂时停止服务可使用：

```bash
docker compose --env-file .env.deploy stop
```

`docker compose ... down` 会移除容器和网络但通常保留命名卷。**不要在保留生产
数据的操作中使用 `down -v`**，它会删除数据库持久卷。重建容器不等于备份；
备份、恢复演练与稳定的密钥配置应单独保留。
