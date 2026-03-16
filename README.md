# uv-custom: uv 公益镜像同步器

[![uv Sync](https://github.com/Wangnov/uv-custom/actions/workflows/sync_uv.yml/badge.svg)](https://github.com/Wangnov/uv-custom/actions/workflows/sync_uv.yml)
[![Python Sync](https://github.com/Wangnov/uv-custom/actions/workflows/sync_python.yml/badge.svg)](https://github.com/Wangnov/uv-custom/actions/workflows/sync_python.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

这个仓库不再做 GitHub / Gitee Release 二次发布，而是通过 GitHub Actions 将 `uv` 相关资产同步到一个位于中国大陆的 S3 兼容对象存储中，作为公益镜像源使用。

默认设计目标是：

- 镜像 `uv` standalone installer、各平台二进制与校验文件
- 镜像 `uv python` 所需的最新运行时资产
  目前覆盖 `CPython`、`PyPy`、`GraalPy`
- 提供一个国内预设安装入口
  安装后自动写入：
  - `python-install-mirror`
  - `python-downloads-json-url`
  - `UV_INSTALLER_GITHUB_BASE_URL`
  - `UV_DEFAULT_INDEX`

## 公开访问模型

本项目只提供静态对象访问，不提供 HTML 站点。

推荐公开基地址通过仓库变量 `PUBLIC_BASE_URL` 注入，例如：

```text
https://uv.agentsmirror.com
```

镜像目录约定如下：

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

## 用户安装

以下命令假定你的公开地址是 `https://uv.agentsmirror.com`。

### macOS / Linux

```sh
curl -LsSf https://uv.agentsmirror.com/install-cn.sh | sh
```

### Windows PowerShell

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://uv.agentsmirror.com/install-cn.ps1 | iex"
```

安装脚本会做四件事：

1. 通过镜像中的官方 installer 安装 `uv`
2. 在用户级 `uv.toml` 中写入：
   - `python-install-mirror`
   - `python-downloads-json-url`
3. 在 shell profile / PowerShell profile 中持久化：
   - `UV_INSTALLER_GITHUB_BASE_URL`
   - `UV_DEFAULT_INDEX`
4. 如果已有 `uv.toml`，会先备份，再只更新受管键

### 关于 `uv self update`

为了让后续 `uv self update` 继续走镜像，安装脚本会在 profile 中写入 `UV_INSTALLER_GITHUB_BASE_URL`。如果你不想保留这个行为，可以手动删除脚本写入的受管块：

```text
# >>> uv mirror managed block >>>
...
# <<< uv mirror managed block <<<
```

## 默认 PyPI 与切换方式

默认写入的是清华源：

```text
https://pypi.tuna.tsinghua.edu.cn/simple
```

如果你更想使用阿里源，可以在安装完成后把 profile 中的 `UV_DEFAULT_INDEX` 改成：

```text
https://mirrors.aliyun.com/pypi/simple
```

或直接删除受管块，自行管理 `UV_DEFAULT_INDEX`。

## 仓库变量与 Secrets

### 必填仓库变量

- `PUBLIC_BASE_URL`
  公开访问的 HTTPS 基地址，不要带结尾 `/`

### 必填 Secrets

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_ENDPOINT_URL`
- `AWS_REGION`
- `S3_BUCKET`

## 工作流说明

### `sync_uv.yml`

- 每小时轮询 `astral-sh/uv`
- 下载最新 release 的全部资产
- 上传到：
  - `/github/astral-sh/uv/releases/download/<tag>/`
  - `/github/astral-sh/uv/releases/download/latest/`
- 生成：
  - `/metadata/uv-latest.json`
  - `/install-cn.sh`
  - `/install-cn.ps1`
- 自动清理仅保留最近 `20` 个 `uv` 版本

### `sync_python.yml`

- 每 6 小时拉取一次 `uv` 上游的 `download-metadata.json`
- 针对 `CPython`、`PyPy`、`GraalPy` 各自只保留最新 build
- 重写 URL 到你的公开基地址
- 上传：
  - `/python-build-standalone/...`
  - `/pypy/...`
  - `/graalpython/...`
  - `/metadata/python-downloads.json`

## 本地验证

### 运行单元测试

```sh
python3 -m unittest tests/test_uvmirror.py -v
```

### 本地生成安装脚本与元数据

```sh
python3 -m scripts.mirrorctl render-installers \
  --public-base-url https://uv.agentsmirror.com \
  --default-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --output-dir ./dist
```

```sh
python3 -m scripts.mirrorctl build-python-downloads \
  --input ./download-metadata.json \
  --output ./dist/metadata/python-downloads.json \
  --manifest-output ./dist/python-assets.json \
  --public-base-url https://uv.agentsmirror.com
```

## 致谢

- `uv` 与其官方 installer、Python 元数据能力来自 [astral-sh/uv](https://github.com/astral-sh/uv)
- `CPython` managed runtime 资产来自 `python-build-standalone`
- `PyPy` 资产来自 `downloads.python.org`
- `GraalPy` 资产来自 `oracle/graalpython`

## 许可证

本项目采用 [MIT](LICENSE) 许可证。
