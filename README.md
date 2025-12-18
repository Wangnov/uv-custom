# uv-custom: uv 国内加速镜像

[![GitHub release](https://img.shields.io/github/v/release/Wangnov/uv-custom?display_name=tag&sort=semver&logo=github)](https://github.com/Wangnov/uv-custom/releases/latest)
[![Sync Status](https://github.com/Wangnov/uv-custom/actions/workflows/sync_release.yml/badge.svg)](https://github.com/Wangnov/uv-custom/actions/workflows/sync_release.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

这是一个与 [astral-sh/uv](https://github.com/astral-sh/uv) 官方版本同步的镜像项目，旨在为国内用户提供更快速、更稳定的 `uv` 安装和使用体验。

## ✨ 项目特点

- **下载加速**：所有 GitHub 下载链接均通过镜像代理，大幅提升下载速度。
- **预设镜像**：自动为您配置清华大学 PyPI 镜像和 Python 构建镜像。
- **自动同步**：通过 GitHub Actions，每 5 分钟检查一次官方版本，并自动发布同步的镜像版本到 GitHub 和 Gitee。
- **一键安装**：提供平台原生的一键安装命令，无需手动下载或授权。
- **灵活配置**：您可以在运行一键安装命令时，通过设置 `UV_DOWNLOAD_PROXY` 和 `UV_PYPI_MIRROR` 环境变量来临时覆盖默认的下载代理和 PyPI 镜像源，以适应不同的网络环境。通过设置 `UV_VERSION` 来选择下载不同的uv版本。

---

## 🚀 快速安装

### Gitee (主推)

我们强烈推荐您通过 Gitee 的发布页面进行安装，以确保所有下载资源均来自国内服务器，实现纯净、高速的国内网络安装体验。

```sh
# macOS / Linux
curl -LsSf https://gitee.com/wangnov/uv-custom/releases/download/latest/uv-installer-custom.sh | sh
```

```sh
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -c "irm https://gitee.com/wangnov/uv-custom/releases/download/latest/uv-installer-custom.ps1 | iex"
```

### GitHub (备用)

如果您无法访问 Gitee，也可以通过 GitHub 的发布页面获取安装命令。

```sh
# macOS / Linux
curl -LsSf https://github.com/Wangnov/uv-custom/releases/download/latest/uv-installer-custom.sh | sh
```

```sh
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -c "irm https://github.com/Wangnov/uv-custom/releases/download/latest/uv-installer-custom.ps1 | iex"
```

高级用法请参考发布页面。

---

## 🔧 (可选) 配置 Conda/Mamba 环境联动

如果您希望 `uv` 能够自动识别并管理当前激活的 Conda/Mamba 环境，可以运行以下对应的脚本来配置 Shell 钩子。

### Gitee (推荐)

```sh
# macOS / Linux
curl -LsSf https://gitee.com/wangnov/uv-custom/releases/download/latest/setup_hooks.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -c "irm https://gitee.com/wangnov/uv-custom/releases/download/latest/setup_hooks.ps1 | iex"
```

---

## 🙏 致谢

- 本项目的所有核心功能均基于 [astral-sh/uv](https://github.com/astral-sh/uv) 的出色工作。
- 感谢所有提供高速、稳定镜像服务的贡献者。

## 📄 许可证

本项目采用 [MIT](LICENSE) 许可证。
