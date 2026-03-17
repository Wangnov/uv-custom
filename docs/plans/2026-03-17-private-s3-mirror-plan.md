# Private S3 Mirror Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 用项目内低层 S3 上传器和 Worker 预签名跳转，完成私有 IHEP S3 上的 `uv` 镜像同步与公开安装分发。

**Architecture:** 先以 TDD 方式给同步状态与上传清单补测试，再实现 Python 上传器并替换 workflows。之后实现 Worker SigV4 预签名跳转，最后做 GitHub Actions 与本机安装验证。

**Tech Stack:** Python 3.12、unittest、GitHub Actions、Cloudflare Workers、AWS SigV4

---

### Task 1: 定义同步状态与上传清单行为

**Files:**
- Modify: `tests/test_uvmirror.py`
- Modify: `uvmirror/metadata.py`

**Step 1: Write the failing test**

- 为 Python 资产新增“生成状态清单”和“比较 stale keys”的测试。

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_uvmirror.py -v`

**Step 3: Write minimal implementation**

- 在 `uvmirror/metadata.py` 增加状态清单辅助函数。

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_uvmirror.py -v`

### Task 2: 实现低层 S3 上传器

**Files:**
- Create: `uvmirror/s3_upload.py`
- Modify: `scripts/mirrorctl.py`
- Modify: `tests/test_uvmirror.py`

**Step 1: Write the failing test**

- 为上传计划构建、状态差异和固定 key 覆盖逻辑补最小单元测试。

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_uvmirror.py -v`

**Step 3: Write minimal implementation**

- 实现串行 `put_object` / multipart / state manifest / delete stale keys。
- 在 `scripts/mirrorctl.py` 中暴露对应命令。

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_uvmirror.py -v`

### Task 3: 替换 GitHub Actions 同步链路

**Files:**
- Modify: `.github/workflows/sync_uv.yml`
- Modify: `.github/workflows/sync_python.yml`
- Modify: `README.md`

**Step 1: Update workflow commands**

- 移除 `rclone` 安装与调用
- 改为执行 Python 低层上传命令

**Step 2: Verify syntax**

Run: `bash -n scripts/*.sh && ruby -e 'require %q[yaml]; YAML.load_file(%q[.github/workflows/sync_uv.yml]); YAML.load_file(%q[.github/workflows/sync_python.yml])'`

### Task 4: 实现 Worker 预签名跳转

**Files:**
- Modify: `cloudflare/uv-origin-proxy/src/index.js`
- Modify: `cloudflare/uv-origin-proxy/wrangler.jsonc`

**Step 1: Write minimal request-signing implementation**

- 支持 `GET`/`HEAD`
- 从 env 读取 endpoint、bucket、region、AK、SK
- 返回 `307` 到预签名 URL，而不是在 Worker 内流式回源
- 支持可选对象前缀，把公网根路径映射到桶内统一前缀

**Step 2: Deploy and verify**

- 部署 Worker
- `curl -I https://uv.agentsmirror.com/...` 验证返回有效预签名跳转

### Task 5: 端到端验证

**Files:**
- Modify if needed: `README.md`

**Step 1: Trigger fresh GitHub Actions**

- `Sync uv Assets`
- `Sync uv Python Assets`

**Step 2: Verify mirror outputs**

- 检查对象可访问
- 检查安装脚本内容

**Step 3: Verify local install**

- 用隔离 `HOME` 运行 `install-cn.sh`
- 执行 `uv python install`
- 确认 Python 来自镜像
