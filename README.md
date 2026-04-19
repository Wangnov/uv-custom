<p align="center">
  <img src="assets/readme-hero.jpg" alt="uv.agentsmirror.com 中国大陆可用的 uv 公益镜像入口" width="100%">
</p>

# uv-custom: uv 公益镜像同步器

[![uv Sync](https://github.com/Wangnov/uv-custom/actions/workflows/sync_uv.yml/badge.svg)](https://github.com/Wangnov/uv-custom/actions/workflows/sync_uv.yml)
[![Python Sync](https://github.com/Wangnov/uv-custom/actions/workflows/sync_python.yml/badge.svg)](https://github.com/Wangnov/uv-custom/actions/workflows/sync_python.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

一个面向中国大陆的 `uv` 公益镜像入口。

目标：让 `uv` 本身、`uv python install` 依赖的运行时下载，以及 `uv add` / `uv lock` / `uv sync` / `uv pip install` 所依赖的 PyPI 访问，在大陆网络环境下更稳、更直接、更接近官方使用方式。

[立即安装](#立即安装) · [当前状态](#当前状态) · [镜像范围](#镜像范围) · [同步策略](#同步策略) · [本地验证](#本地验证)

## 为什么有这个项目

`uv` 的官方体验很好，但在中国大陆网络环境下，安装脚本、GitHub release 资产和 Python 运行时下载并不总是稳定。

这个项目做的事情：

- 同步官方 `uv` release 与 installer
- 同步 `uv python` 需要的最新运行时资产
- 提供一个面向大陆用户的统一安装入口
- 提供一个面向 `uv` / `pip` 的 PyPI Simple 代理入口
- 尽量保持与官方安装方式一致，不另起一套分发逻辑

本项目不提供普通展示站点，但会提供 `uv` / `pip` 需要的 Simple JSON / HTML 接口。

## 立即安装

### macOS / Linux

```sh
curl -LsSf https://uv.agentsmirror.com/install-cn.sh | sh
```

### Windows PowerShell

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://uv.agentsmirror.com/install-cn.ps1 | iex"
```

## 安装脚本会做什么

`install-cn` 不会暴力改写官方 installer，而是尽量复用官方路径：

- 直接分发官方 `uv-installer.sh` / `uv-installer.ps1`
- 通过 `UV_INSTALLER_GITHUB_BASE_URL` 让官方 installer 改从镜像取 `uv` 二进制
- 安装完成后再写入国内镜像相关的 `uv.toml` 与受管环境变量

当前会自动写入这些配置：

- `python-downloads-json-url`
- `UV_INSTALLER_GITHUB_BASE_URL`
- `UV_PYTHON_DOWNLOADS_JSON_URL`
- `UV_DEFAULT_INDEX`

其中 `UV_DEFAULT_INDEX` 现在默认写入：

```text
https://uv.agentsmirror.com/pypi/simple
```

这个入口的行为是：

- `simple` 页面和 `*.metadata` 由 `uv.agentsmirror.com` 自己代理并缓存
- wheel / sdist 包体优先走清华源
- 清华源失败时由 Worker 回退到官方 `files.pythonhosted.org`

如果本机已经存在 `uv.toml`，脚本会先备份，再只更新受管键。

PyPy 下载地址由 `metadata/python-downloads.json` 统一提供。重跑 installer 会清理历史安装留下的 `pypy-install-mirror` 与 `UV_PYPY_INSTALL_MIRROR`。

为了让后续 `uv self update` 继续走镜像，profile 中还会写入一段受管块；如果你不想保留镜像环境变量，可以在安装后手动删除。

## 恢复官方设置 / 退出镜像

如果你之后不想继续使用本项目，不需要卸载 `uv`，只需要移除镜像相关配置即可；完成后，`uv` 会回到官方默认的下载与索引行为。

### 1. 删除 profile 中的受管块

安装脚本会在 shell profile 或 PowerShell profile 中写入一段受管块：

```text
# >>> uv mirror managed block >>>
...
# <<< uv mirror managed block <<<
```

删除这一整段即可。

在 macOS / Linux 上，通常位于：

- `~/.profile`
- `~/.bashrc`
- `~/.zshrc`

在 Windows PowerShell 上，通常位于：

- `$PROFILE`

### 2. 恢复或清理 `uv.toml`

安装脚本在修改 `uv.toml` 前会先备份旧文件。你可以直接恢复备份，也可以只删除镜像相关键。

当前需要移除的是：

- `python-downloads-json-url`

历史安装还可能带有 `pypy-install-mirror`，一起删除即可。

如果你希望最稳妥地恢复到安装前状态，优先建议直接用安装时生成的备份文件覆盖当前 `uv.toml`。

### 3. 清理额外环境变量

如果你后来又手动设置过这些环境变量，也一并删除：

- `UV_INSTALLER_GITHUB_BASE_URL`
- `UV_PYTHON_DOWNLOADS_JSON_URL`
- `UV_DEFAULT_INDEX`

历史安装还可能带有 `UV_PYPY_INSTALL_MIRROR`，一起删除即可。

完成以上步骤后，后续 `uv self update`、`uv python install`、`uv add`、`uv sync`、`uv pip install` 等行为，就会重新回到官方默认链路。

## 当前状态

截至 `2026-03-29`，当前已完成以下真实验证：

| 项目 | 当前结果 |
| --- | --- |
| 公网入口 | `https://uv.agentsmirror.com` 可访问 |
| 安装脚本 | `install-cn.sh` / `install-cn.ps1` 可下载 |
| `uv` 安装 | 已实测安装 `uv 0.11.2` |
| Python 安装 | 已实测 `uv python install 3.12.12` 成功 |
| `uv add` / `uv sync` 重依赖冒烟 | 已实测 `numpy 2.4.3`、`orjson 3.11.7`、`pillow 12.1.1`、`torch 2.11.0` |
| 大陆服务器实测 | 上海 Ubuntu 服务器验证通过 |
| 自动同步 | `Sync uv Assets` 已连续定时成功 |

## 镜像范围

当前镜像内容包括：

- 官方 `uv` release 资产
- 官方 `uv-installer.sh` / `uv-installer.ps1`
- `uv python` 需要的最新运行时资产
  当前覆盖 `CPython`、`PyPy`、`GraalPy`
- `uv` / `pip` 需要的 PyPI Simple 入口
  当前提供：
  - `/pypi/simple/<project>/`
  - `/pypi/files/files.pythonhosted.org/...`
- 国内预设安装入口
  当前生成：
  - `/install-cn.sh`
  - `/install-cn.ps1`
  - `/metadata/uv-latest.json`
  - `/metadata/python-downloads.json`

## 镜像路径约定

```text
/github/astral-sh/uv/releases/download/<tag>/...
/github/astral-sh/uv/releases/download/latest/uv-installer.sh
/github/astral-sh/uv/releases/download/latest/uv-installer.ps1
/python-build-standalone/releases/download/<build>/...
/pypy/...
/graalpython/releases/download/<build>/...
/metadata/uv-latest.json
/metadata/python-downloads.json
/pypi/simple/<project>/
/pypi/files/files.pythonhosted.org/...
/install-cn.sh
/install-cn.ps1
```

## 同步策略

### `sync_uv.yml`

- 每小时轮询 `astral-sh/uv`
- 下载最新 release 全部资产
- 上传最新版本与 `latest` 入口
- 刷新：
  - `/metadata/uv-latest.json`
  - `/install-cn.sh`
  - `/install-cn.ps1`

### `sync_python.yml`

- 每 6 小时拉取一次上游 `download-metadata.json`
- 对 `CPython`、`PyPy`、`GraalPy` 各自只保留最新 build
- 重写下载地址到公开镜像域名
- 上传并清理：
  - `/python-build-standalone/...`
  - `/pypy/...`
  - `/graalpython/...`
  - `/metadata/python-downloads.json`

## 默认 PyPI

默认写入的是自家 PyPI 代理入口：

```text
https://uv.agentsmirror.com/pypi/simple
```

这个入口会：

- 对 `uv` 优先返回 Simple JSON
- 对 `pip` / 浏览器返回 Simple HTML
- 把文件 URL 统一改写到 `uv.agentsmirror.com/pypi/files/...`
- 对 `*.metadata` 做缓存
- 对 wheel / sdist 优先走清华源，失败时回退官方

如果你更想手动指定别的索引，仍然可以覆盖 `UV_DEFAULT_INDEX`。

## 本地验证

### 运行单元测试

```sh
python3 -m unittest tests/test_uvmirror.py -v
```

### 本地生成安装脚本

```sh
python3 -m scripts.mirrorctl render-installers \
  --public-base-url https://uv.agentsmirror.com \
  --default-index-url https://uv.agentsmirror.com/pypi/simple \
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

### 运行重依赖 smoke

```sh
python3 -m scripts.uv_smoke
```

默认会：

- 使用 `https://uv.agentsmirror.com/pypi/simple`
- 使用 `https://uv.agentsmirror.com/metadata/python-downloads.json`
- 创建临时项目并安装 `Python 3.12`
- 分三步执行 `uv add pillow==12.1.1 orjson==3.11.7`、`uv add torch==2.11.0`、`uv add numpy==2.4.3`
- 再执行 `uv sync --reinstall` 与导入验证

如果你想保留临时项目目录方便排查，可以加：

```sh
python3 -m scripts.uv_smoke --keep-project
```

## 致谢

- 感谢中国科学院高能物理研究所提供公益性质的 S3 存储桶支持
- 感谢 [linux.do 社区](https://linux.do) 的支持与分享精神，使本项目获得了关键的资源信息
- `uv` 与其官方 installer、Python 元数据能力来自 [astral-sh/uv](https://github.com/astral-sh/uv)
- `CPython` managed runtime 资产来自 `python-build-standalone`
- `PyPy` 资产来自 `downloads.python.org`
- `GraalPy` 资产来自 `oracle/graalpython`

## 许可证

本项目采用 [MIT](LICENSE) 许可证。
