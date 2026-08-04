# OpenClaus

> 一个能改写自身策略代码、但不能改写本轮裁判的 AI 实验系统。

OpenClaus 通过 Gemini 生成策略与提示词改进，使用固定的可信控制器验证候选代码，并在验证通过后提交、启动下一代进程。当前版本的重点不是“修改尽可能多的文件”，而是让每次改动都有明确边界、最终结果和可恢复的 Git 历史。

## 当前定位

OpenClaus 是研究原型，不是面向公网或多租户环境的安全沙箱。

它现在提供：

- 固定可信内核和可进化策略的明确分层；
- 候选写入白名单和路径解析防护；
- 独立于候选代码的语法、策略安全、契约和测试门禁；
- `evolve/*` 分支、干净工作区和单实例强制检查；
- 每代唯一最终结果、分支隔离和原子状态写入；
- 带随机 token、代数和 PID 的热部署握手；
- 部署失败使用 `git revert` 保留历史，不再执行 `git reset --hard`。

它暂时不提供操作系统级文件系统或网络隔离。有关边界见“安全说明”。

## 架构

```text
openclaus/
├── bootstrap.py              # 固定启动器与替换进程握手
├── engine/                   # 可信内核，进化引擎禁止写入
│   ├── controller.py         # 进化状态机与热部署协调
│   ├── model_client.py       # Gemini 客户端，持有 API 凭据
│   ├── candidate.py          # 输出解析、白名单写入、备份、签名验证
│   ├── verifier.py           # 固定验证器
│   ├── runtime.py            # Git、分支、锁、PID 和握手工具
│   └── state.py              # 原子、分支级状态
├── core/                     # 策略层
│   ├── prompts.py            # 可进化
│   ├── strategy.py           # 可进化
│   ├── tests/test_*.py       # 可新增的候选测试
│   └── brain/codemod/...     # 旧 API 的兼容包装，不可由进化引擎写入
├── tests/acceptance/         # 固定验收测试，候选代码不可修改
├── scripts/verify.py         # 完整本地验证入口
├── backups/                  # 运行时备份，gitignored
└── logs/                     # 状态和运行记录，gitignored
```

可信边界是硬编码白名单。模型输出只能写入：

```text
core/prompts.py
core/strategy.py
core/tests/test_*.py
```

即使模型输出包含 `engine/verifier.py`、`core/health.py`、`bootstrap.py` 或路径遍历，解析器和写入器也会拒绝。

## 一代的执行流程

```text
PRECHECK
  ├─ 必须位于 evolve/* 分支
  ├─ Git 工作区必须干净
  └─ 获取单实例锁

READ → STRATEGY → GENERATE → MODEL REVIEW → SIGNATURE VALIDATE
  → BACKUP → ALLOWLIST WRITE → TRUSTED VERIFY → GIT COMMIT
  → TOKEN HANDSHAKE → DEPLOYED
```

可信验证包括：

1. `engine/` 和 `core/` 全部 Python 文件的语法解析；
2. 策略文件静态规则：只允许 `random`、`collections` 导入，拒绝文件、进程、环境和网络相关入口；
3. `get_strategy`、`build_improvement_prompt`、`build_review_prompt` 调用契约；
4. 在清理敏感环境变量后的独立 Python 进程中导入策略模块；
5. 运行候选测试与候选不可写的 `tests/acceptance/`。

候选提交后才会启动替换进程。替换进程必须使用本轮随机 token、generation 和自身 PID 写入就绪标记。部署失败会新增一个 revert commit，保留候选及回滚证据。

## 状态模型

`logs/state.json` 使用 schema v2。每个 generation 只保留一个最终 outcome：

```text
deployed
generation_failed
no_changes
rejected
validation_failed
write_failed
verification_failed
commit_failed
deploy_failed
```

`deploying` 不再作为成功记录。历史旧值会在加载时规范化，例如 `deploying`、`success` → `deployed`。

状态通过临时文件、`fsync` 和 `os.replace` 原子写入。切换分支时，旧状态使用分支名和时间戳归档，避免覆盖。

## 安装

依赖 Python 3.11+ 和 Git。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item config.example.yaml config.yaml
```

编辑 `config.yaml`，设置 Gemini API Key。密钥文件已被 Git 忽略。

如果使用 pyenv-win，请先选择已安装版本，例如：

```powershell
pyenv local 3.12.10
```

## 本地验证

完整门禁：

```powershell
python scripts\verify.py
```

也可以只执行可信验证；该命令不读取 API Key，也不会修改源码：

```powershell
python bootstrap.py --dry-run
```

当前固定测试会覆盖写入边界、解析、签名保护、策略静态规则、状态迁移、分支保护、单实例锁、带身份的就绪标记以及 Git commit/revert 回滚。

## 开启一条进化线

人工开发在 `main` 完成。确认完整验证通过并提交 seed 后，再创建独立进化分支：

```powershell
git switch main
python scripts\verify.py
git tag seed-v0.8
git switch -c evolve/seed-v0.8
python bootstrap.py
```

正常进化在以下情况会直接安全停止：

- 当前是 `main`、detached HEAD 或非 `evolve/*` 分支；
- tracked 或 untracked 工作区不干净；
- 另一个 OpenClaus 进程持有 `.openclaus.lock`；
- Git HEAD 在候选生成过程中发生变化。

`--dry-run` 不受分支限制，因此可以在 `main` 上执行。

## 配置

```yaml
gemini:
  api_key: "YOUR_GEMINI_API_KEY"
  model: "gemini-3-flash-preview"

evolution:
  max_generations: 50
  delay_between_generations: 5
  hot_deploy_timeout: 30
  api_max_retries: 2

safety:
  max_backups: 20
  test_timeout: 60
```

429 和常见 5xx 模型错误会进行有限指数退避。无论最终成功或失败，generation 都会持久化，避免重启后重复使用同一代编号。

## 设计原则

### 候选不能裁判自己

候选可以改进提示词、策略和候选测试，但不能修改控制器、最终验证器或固定验收测试。候选测试只能增加信号，不能替代固定门禁。

### 部署成功不等于质量提升

`deployed` 只表示固定门禁和进程交接成功。状态同时记录耗时、变更文件、commit 和审查摘要，为后续建立冻结基准与客观 fitness 提供数据。

### Git 历史优先于破坏性回退

运行前必须干净，候选 commit 与失败 revert 都保留。系统不会再使用 `git reset --hard HEAD~1` 隐藏失败候选。

### Seed 保持小，但可信内核保持固定

进化结果如果证明某项可信基础设施值得采用，应由人工审查后合入 `main` 并形成新 seed。在线候选不能直接扩张自身写权限。

## 安全说明

当前静态策略规则和清理后的子进程环境属于纵深防护，不是 Python 安全沙箱。Python 对象模型允许绕过许多纯 AST 限制；候选测试进程目前也没有操作系统级网络、文件和资源配额。

因此：

- 只应在专用 `evolve/*` 分支和可恢复的开发机上运行；
- 不应给进程生产凭据、云管理员权限或敏感目录访问权；
- 不应把来自不受信任第三方的候选代码当作安全代码运行；
- 面向无人值守或公网场景前，仍需增加容器/虚拟机隔离、只读挂载、禁网和 CPU/内存/时间限制。

## 后续路线

1. 在无网络、只读挂载的容器中执行候选测试；
2. 候选先进入临时 Git worktree，通过后再提升分支引用；
3. 建立候选不可见的冻结行为基准和 holdout fitness；
4. 记录模型版本、提示词哈希、token、费用和完整验证报告；
5. 增加 `status`、`report`、`harvest` 等实验管理命令。

## 许可

MIT
