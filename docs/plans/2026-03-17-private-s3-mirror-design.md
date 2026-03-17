# Private S3 Mirror Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** 让 `uv` / `uv python` 镜像既能稳定同步到 IHEP 私有 S3，又能通过 `uv.agentsmirror.com` 对外分发并完成安装。

**Architecture:** 同步侧放弃 `aws s3 sync` 与当前 `rclone sync` 的目标端探测模式，改成项目内自带的低层 S3 上传器，只执行最小必要的 `PutObject`、串行 multipart、读取状态清单和显式删除。分发侧不再依赖匿名读桶，而是由 Cloudflare Worker 生成短时效 SigV4 预签名 URL，并用 `307` 把客户端下载重定向到 IHEP S3。

**Tech Stack:** Python 3.12、botocore/boto3 low-level S3 API、GitHub Actions、Cloudflare Workers、AWS SigV4

---

## 现状与根因

- `aws s3 sync/cp` 会在 IHEP 网关上触发大量 `PutObject`、`CreateMultipartUpload`、`UploadPart` 的 `403 AccessDenied`。
- 改成 `rclone` 后，上传动作本身有所改善，但仍会持续触发目标端 `HEAD`、metadata 读取、mtime 回写、目标 hash 比对，最后同样掉进长时间 `403`。
- `uv.agentsmirror.com` 当前只是简单代理 `https://20830-uv-custom.s3.jwanfs.com`，而源站匿名读仍然返回 `403`，所以公开下载链路没有打通。

## 方案选择

### 方案 A：继续调 `rclone`

- 优点：改动小。
- 缺点：仍受 `rclone` 内部探测策略影响，排障成本高，且无法保证完全不碰目标端。

### 方案 B：改回 `aws s3api` + 自定义重试

- 优点：行为可控，和 IHEP 官方文档一致。
- 缺点：shell 里维护 multipart、manifest 和删除逻辑太脆弱。

### 方案 C：项目内 Python 低层上传器 + Worker 预签名跳转

- 优点：可以完全控制请求类型、重试节奏、multipart 粒度、状态文件和删除策略；同时能解决私有桶分发问题，而且大文件不经过 Cloudflare。
- 缺点：实现量更大。

**推荐：方案 C。**

## 同步侧设计

- 新增 Python S3 上传器模块，统一封装：
  - 小文件 `PutObject`
  - 大文件串行 multipart
  - 显式 `DeleteObject`
  - 带退避的重试
- 不再对目标对象做 `HEAD` / hash / mtime / metadata 读取。
- `uv`：
  - 版本目录只做“盲写覆盖”
  - `latest/` 和根部安装脚本、metadata 用固定 key 覆盖
- `python`：
  - 仍只选择最新 build 资产
  - 用状态清单对象记录上次同步过的 keys
  - 先上传新资产，再根据旧清单显式删除 stale keys

## 分发侧设计

- Worker 接受公网 `GET`/`HEAD`
- 构造 path-style 的 IHEP S3 URL：`https://fgws3-ocloud.ihep.ac.cn/<bucket>/<key>`
- 使用环境变量中的 AK/SK/region 生成短时效 SigV4 预签名 URL
- 返回 `307` 到预签名 URL，让大文件直接由大陆 S3 下发
- 如遇不同身份写入的对象彼此不可读，可通过 `S3_KEY_PREFIX` / `MIRROR_KEY_PREFIX` 把实际对象隔离到统一前缀，公网 URL 保持不变

## 验证标准

- GitHub Actions 的 `Sync uv Assets` 成功完成
- GitHub Actions 的 `Sync uv Python Assets` 成功完成
- `curl -I https://uv.agentsmirror.com/.../uv-installer.sh` 返回 `307`，并带有效 `Location`
- 本机以隔离 `HOME` 运行 `install-cn.sh` 成功安装 `uv`
- 安装后执行 `uv python install` 能通过镜像成功安装 Python
