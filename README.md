# uv-custom: uv 公益镜像同步器

[![uv Sync](https://github.com/Wangnov/uv-custom/actions/workflows/sync_uv.yml/badge.svg)](https://github.com/Wangnov/uv-custom/actions/workflows/sync_uv.yml)
[![Python Sync](https://github.com/Wangnov/uv-custom/actions/workflows/sync_python.yml/badge.svg)](https://github.com/Wangnov/uv-custom/actions/workflows/sync_python.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

这个仓库用于把 `uv` 相关资产同步到位于中国大陆的 S3 兼容对象存储，并通过 `uv.agentsmirror.com` 作为安装入口，服务公益性质的国内下载场景。

## 镜像范围

- 官方 `uv` release 资产
- 官方 `uv-installer.sh` / `uv-installer.ps1`
- `uv python` 需要的最新运行时资产
  当前覆盖 `CPython`、`PyPy`、`GraalPy`
- 国内预设安装入口
  当前生成：
  - `/install-cn.sh`
  - `/install-cn.ps1`
  - `/metadata/uv-latest.json`
  - `/metadata/python-downloads.json`

## 公开访问模型

本项目只提供对象分发，不提供 HTML 站点。

推荐把仓库变量 `PUBLIC_BASE_URL` 设为：

```text
https://uv.agentsmirror.com
```

镜像路径约定如下：

```text
/github/astral-sh/uv/releases/download/<tag>/...
/github/astral-sh/uv/releases/download/latest/uv-installer.sh
/github/astral-sh/uv/releases/download/latest/uv-installer.ps1
/python-build-standalone/releases/download/<build>/...
/pypy/...
/graalpython/releases/download/<build>/...
/metadata/uv-latest.json
/metadata/python-downloads.json
/install-cn.sh
/install-cn.ps1
```

## 安装入口

以下命令假定公开地址为 `https://uv.agentsmirror.com`。

### macOS / Linux

```sh
curl -LsSf https://uv.agentsmirror.com/install-cn.sh | sh
```

### Windows PowerShell

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://uv.agentsmirror.com/install-cn.ps1 | iex"
```

`install-cn` 不会暴力改写官方 installer。本仓库的做法是：

- 直接分发官方 `uv-installer.sh` / `uv-installer.ps1`
- 通过 `UV_INSTALLER_GITHUB_BASE_URL` 让官方 installer 改从镜像取 `uv` 二进制
- 在安装完成后再写入国内镜像相关的 `uv.toml` 与环境变量

安装脚本会自动写入这些配置：

- `python-install-mirror`
- `python-downloads-json-url`
- `pypy-install-mirror`
- `UV_INSTALLER_GITHUB_BASE_URL`
- `UV_PYTHON_INSTALL_MIRROR`
- `UV_PYTHON_DOWNLOADS_JSON_URL`
- `UV_PYPY_INSTALL_MIRROR`
- `UV_DEFAULT_INDEX`

如果已有 `uv.toml`，脚本会先备份，再只更新受管键。

### 关于 `uv self update`

为了让后续 `uv self update` 继续走镜像，profile 中会写入：

```text
# >>> uv mirror managed block >>>
...
# <<< uv mirror managed block <<<
```

如果你不想保留镜像环境变量，可以删除这段受管块。

## 默认 PyPI

默认写入的是清华源：

```text
https://pypi.tuna.tsinghua.edu.cn/simple
```

如果你更想使用阿里源，可以把 `UV_DEFAULT_INDEX` 改成：

```text
https://mirrors.aliyun.com/pypi/simple
```

## 为什么不用 `aws s3 sync` / `rclone`

对 IHEP 这类 S3 兼容网关，本项目已经实测踩过三类坑：

- `aws s3api` / `aws s3 sync` 会发出 `Expect: 100-continue`、`Transfer-Encoding: chunked`、`Content-Encoding: aws-chunked` 和 trailer checksum，这类请求会被网关直接拒绝
- `CreateMultipartUpload` 会直接返回 `AccessDenied`
- `rclone` 即使把并发和速率压得很低，仍会频繁触发目标端 metadata/hash/mtime/HEAD 探测，最终也会掉进长时间 `403`

当前仓库已经切换为项目内 Python 低层上传器，只做最小必要操作：

- 单文件流式 `put_object`
- Python 资产通过状态清单显式删除 stale keys
- GitHub Actions 默认关闭 multipart
- 请求节流和指数退避都在项目内可控

## 仓库变量与 Secrets

### GitHub Actions vars

- `PUBLIC_BASE_URL`
- `MIRROR_KEY_PREFIX`
  可选。用于把实际对象写入桶内某个前缀，例如 `mirror`。公网 URL 不变，适合不同 AK/SK 对同一桶对象不可互读时做隔离。

### GitHub Actions secrets

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_ENDPOINT_URL`
- `AWS_REGION`
- `S3_BUCKET`

### GitHub Actions 可选 secret

- `AWS_SESSION_TOKEN`

## Cloudflare Worker

`cloudflare/uv-origin-proxy` 里的 Worker 不再代理大文件内容，而是为私有 IHEP S3 生成短时效 AWS SigV4 预签名 URL，并返回 `307` 跳转。

这有两个直接好处：

- 大文件流量直接从中国大陆 S3 出口下发，不穿过 Cloudflare Worker
- 公开访问不依赖桶匿名读，也避开了 Worker 直连回源时的兼容性问题

### 中国访问建议

如果 `uv.agentsmirror.com` 走的是 Cloudflare 代理，国内访问仍然会受 Cloudflare 网络质量影响。

当前这套实现的最佳实践是：

- 用 `uv.agentsmirror.com` 作为安装入口和轻量跳转入口
- 真实二进制与 Python 运行时包通过预签名 URL 直接从大陆 S3 下载

如果你后续能拿到大陆可直连的自有域名或大陆 CDN，再把 `PUBLIC_BASE_URL` 切过去会更稳；仓库里的对象路径布局不需要改。

### Worker vars

- `S3_ORIGIN_ENDPOINT`
  例如 `https://fgws3-ocloud.ihep.ac.cn`
- `S3_BUCKET`
- `S3_REGION`
- `S3_PRESIGN_TTL_SECONDS`
  可选，默认 `600`
- `S3_KEY_PREFIX`
  可选。若设置为 `mirror`，则 `https://uv.agentsmirror.com/install-cn.sh` 会被重定向到桶内 `mirror/install-cn.sh`

### Worker secrets

- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`

### Worker 可选 secret

- `S3_SESSION_TOKEN`

## 工作流说明

### `sync_uv.yml`

- 每小时轮询 `astral-sh/uv`
- 下载最新 release 全部资产
- 上传到：
  - `/github/astral-sh/uv/releases/download/<tag>/`
  - `/github/astral-sh/uv/releases/download/latest/`
- 生成并上传：
  - `/metadata/uv-latest.json`
  - `/install-cn.sh`
  - `/install-cn.ps1`

### `sync_python.yml`

- 每 6 小时拉取一次上游 `download-metadata.json`
- 对 `CPython`、`PyPy`、`GraalPy` 各自只保留最新 build
- 重写 URL 到你的公开域名
- 下载这些最新资产到 runner
- 用状态清单上传并清理：
  - `/python-build-standalone/...`
  - `/pypy/...`
  - `/graalpython/...`
  - `/metadata/python-downloads.json`

## 本地验证

### 运行单元测试

```sh
python3 -m unittest tests/test_uvmirror.py -v
```

### 本地生成安装脚本

```sh
python3 -m scripts.mirrorctl render-installers \
  --public-base-url https://uv.agentsmirror.com \
  --default-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --output-dir ./dist
```

### 本地生成 Python 元数据

```sh
python3 -m scripts.mirrorctl build-python-downloads \
  --input ./download-metadata.json \
  --output ./dist/metadata/python-downloads.json \
  --manifest-output ./dist/python-assets.json \
  --public-base-url https://uv.agentsmirror.com
```

### 部署 Worker

```sh
cd cloudflare/uv-origin-proxy
npx wrangler secret put S3_ACCESS_KEY_ID
npx wrangler secret put S3_SECRET_ACCESS_KEY
npx wrangler deploy
```

## 致谢

- `uv` 与其官方 installer、Python 元数据能力来自 [astral-sh/uv](https://github.com/astral-sh/uv)
- `CPython` managed runtime 资产来自 `python-build-standalone`
- `PyPy` 资产来自 `downloads.python.org`
- `GraalPy` 资产来自 `oracle/graalpython`

## 许可证

本项目采用 [MIT](LICENSE) 许可证。
