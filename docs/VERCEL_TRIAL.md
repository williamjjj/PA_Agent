# Vercel 网页试用：PA 分析与历史回测

本次选择 Vercel 托管网页/API，连接持久 PostgreSQL，使用各账户自己的模型
和行情凭据。支持登录、PA 分析、量化因子与历史回测；不部署常驻挖掘 Worker。
目前尚未确认本次版本的线上地址，以实际部署成功后的 URL 和检查结果为准。

## 1. 确认身份、项目与分支

- 在 Vercel 登录拥有现有项目的账户，在控制台核对团队 `tianwei` 下的
  `pa-agent-rebuild` 是否为自己的目标项目。
- 将自己的 GitHub 账户 `williamjjj` 正确关联到该 Vercel 账户。确认提交使用的
  本人 Gmail 已在 GitHub 添加并验证，并在 Vercel 中核对项目成员/提交者关联。
  若仍出现提交者不属于团队的提示，先修正真实账户关联，不伪造提交作者。
- 这次网页代码位于 **`codex/web-multiuser-vercel`**。`main` 仍是桌面版本；
  选择该网页分支创建 Preview，不自动切换生产分支、合并到 `main` 或把私有
  仓库改为公开。

项目根目录使用仓库根目录，框架选择 FastAPI，Python 为 `.python-version`
指定的 3.12。`pyproject.toml` 已指定 `app:app` 入口。移除旧 v0 项目遗留的
Build Command、Output Directory 和 Install Command 覆盖项，让 Vercel 使用
当前 Python 项目配置。

## 2. 准备试用数据库与环境变量

可从 Vercel Marketplace 或数据库供应商控制台创建 PostgreSQL，例如 Neon。
在创建页面选择当前可用的免费计划，并核对其额度、休眠和使用限制。数据库
账户需要创建应用表的权限，首次连接会自动建表。

从供应商连接页面复制 **pooled PostgreSQL 连接串**，保留供应商提供的 TLS
参数，例如 `sslmode=require`。在 Vercel 项目设置中配置以下变量，值只通过
控制台安全填写，不放进代码、聊天或构建日志：

| 变量 | 设置 |
|---|---|
| `DATABASE_URL` | 上述连接串；若 Marketplace 注入了其他变量名，明确配置为应用读取的这个名称 |
| `PA_WEB_ENCRYPTION_KEY` | 稳定的 Fernet 密钥；已有数据库必须复用其原密钥 |
| `PA_WEB_INVITE_CODE` | 自定的私密邀请码，启用邀请码注册 |
| `PA_WEB_REGISTRATION` | 保持 `0` 即可使用邀请码注册；仅在明确需要公开注册时设为 `1` |

新数据库可在可信本地环境使用 `scripts/init_deploy_env.py` 生成私密
`.env.deploy`，再通过本地编辑器取出其中的 Fernet 密钥与邀请码填入 Vercel。
该脚本为单机部署生成的 `POSTGRES_PASSWORD` **不是 Neon 的连接密码**；
`DATABASE_URL` 必须使用数据库供应商提供的真实连接串。脚本不会打印密钥或
覆盖已有文件。不要为升级已有数据库重新生成加密密钥，并安全备份原密钥。

这次分支试用需要配置 **Preview** 环境，并在修改变量后重新部署。
Production 环境独立配置；除非有意共用正式账户数据，Preview 应使用独立的
数据库与密钥。不要设置 SQLite 或依赖 `PA_WEB_DATA_DIR` 在 Vercel 保存账户。

## 3. 部署与检查

从目标网页分支的实际部署记录打开 Preview URL。当前配置请求最长 240 秒的
函数执行时间；在项目设置及构建日志中确认当前运行模式允许该配置。部署失败
时按实际日志处理，不把升级付费计划视为默认步骤。

试用预览可以保留 Vercel 登录保护，使用项目所有者的 Vercel 账户打开即可；
不需要为了此次试用关闭保护。Vercel 登录与应用内注册/登录是两层不同身份。

在实际 Preview 地址验证：

1. `/api/health` 返回 `ok: true`；`/api/status` 返回 `configured: true`。
2. 用邀请码注册并登录；在个人设置填写自己的模型 Base URL、模型名称和 Key，
   检测连接后保存。模型和行情 Key 属于个人账户，不设为全站共用凭据。
3. 载入示例行情并计算因子；在「历史研究」载入 600 根示例数据，运行并重新
   打开保存的回测结果。示例验证不需要行情 Key，也不代表真实收益。
4. 用自己的模型完成一次 PA 分析，确认流式结果与历史记录可读。

## 后台挖掘在此部署中的状态

Vercel 网页函数不会自动启动 Worker。「后台挖掘」现有页面会明确显示工作
进程离线；提交的任务只会持久化排队，不执行搜索，也不影响 PA 分析与历史
回测。每账户最多三个活动任务，可取消排队任务。以后增加兼容版本的常驻
Worker，即可启用后台自动挖掘。
