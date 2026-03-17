# uv-custom: uv 公益镜像同步器

[![uv Sync](https://github.com/Wangnov/uv-custom/actions/workflows/sync_uv.yml/badge.svg)](https://github.com/Wangnov/uv-custom/actions/workflows/sync_uv.yml)
[![Python Sync](https://github.com/Wangnov/uv-custom/actions/workflows/sync_python.yml/badge.svg)](https://github.com/Wangnov/uv-custom/actions/workflows/sync_python.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

<p align="center">
  <img src="assets/readme-hero.jpg" alt="uv.agentsmirror.com 中国大陆可用的 uv 公益镜像入口" width="100%">
</p>

一个面向中国大陆的 `uv` 公益镜像入口。

目标：让 `uv` 本身，以及 `uv python install` 依赖的运行时下载，在大陆网络环境下更稳、更直接、更接近官方使用方式。

[立即安装](#立即安装) · [当前状态](#当前状态) · [镜像范围](#镜像范围) · [同步策略](#同步策略) · [本地验证](#本地验证)

## 为什么有这个项目

`uv` 的官方体验很好，但在中国大陆网络环境下，安装脚本、GitHub release 资产和 Python 运行时下载并不总是稳定。

这个项目做的事情：

- 同步官方 `uv` release 与 installer
- 同步 `uv python` 需要的最新运行时资产
- 提供一个面向大陆用户的统一安装入口
- 尽量保持与官方安装方式一致，不另起一套分发逻辑

本项目只提供对象分发，不提供 HTML 站点。

## 立即安装

### macOS / Linux

```sh
curl -LsSf https://uv.agentsmirror.com/install-cn.sh | sh
```

### Windows PowerShell

```powershell
irm https://uv.agentsmirror.com/install-cn.ps1 | iex
```

## 安装脚本会做什么

`install-cn` 不会暴力改写官方 installer，而是尽量复用官方路径：

- 直接分发官方 `uv-installer.sh` / `uv-installer.ps1`
- 通过 `UV_INSTALLER_GITHUB_BASE_URL` 让官方 installer 改从镜像取 `uv` 二进制
- 安装完成后再写入国内镜像相关的 `uv.toml` 与受管环境变量

当前会自动写入这些配置：

- `python-downloads-json-url`
- `pypy-install-mirror`
- `UV_INSTALLER_GITHUB_BASE_URL`
- `UV_PYTHON_DOWNLOADS_JSON_URL`
- `UV_PYPY_INSTALL_MIRROR`
- `UV_DEFAULT_INDEX`

如果本机已经存在 `uv.toml`，脚本会先备份，再只更新受管键。

为了让后续 `uv self update` 继续走镜像，profile 中还会写入一段受管块；如果你不想保留镜像环境变量，可以在安装后手动删除。

## 当前状态

截至 `2026-03-18`，当前已完成以下真实验证：

| 项目 | 当前结果 |
| --- | --- |
| 公网入口 | `https://uv.agentsmirror.com` 可访问 |
| 安装脚本 | `install-cn.sh` / `install-cn.ps1` 可下载 |
| `uv` 安装 | 已实测安装 `uv 0.10.11` |
| Python 安装 | 已实测 `uv python install 3.12.13` 成功 |
| 大陆服务器实测 | 上海 Ubuntu 服务器验证通过 |
| 自动同步 | `Sync uv Assets` 已连续定时成功 |

## 镜像范围

当前镜像内容包括：

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

默认写入的是清华源：

```text
https://pypi.tuna.tsinghua.edu.cn/simple
```

如果你更想使用阿里源，可以把 `UV_DEFAULT_INDEX` 改成：

```text
https://mirrors.aliyun.com/pypi/simple
```

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

## 致谢

- 感谢中国科学院高能物理研究所提供公益性质的 S3 存储桶支持
- `uv` 与其官方 installer、Python 元数据能力来自 [astral-sh/uv](https://github.com/astral-sh/uv)
- `CPython` managed runtime 资产来自 `python-build-standalone`
- `PyPy` 资产来自 `downloads.python.org`
- `GraalPy` 资产来自 `oracle/graalpython`

## 许可证

本项目采用 [MIT](LICENSE) 许可证。
