# OpenClaus

> 一个会改写自身代码的AI系统。

OpenClaus 是一个实验性项目：程序在运行时通过 Gemini API 分析自己的源码，生成改进方案，验证通过后以新进程替换自身——完成一次"进化"。每一代的改动都以 git commit 记录，失败时自动回滚。

---

## 架构

```
openclaus/
├── bootstrap.py          # 不可变启动器（永不被AI修改）
├── config.yaml           # 本地配置，含API Key（gitignored）
├── config.example.yaml   # 配置模板，提交到git
├── requirements.txt
│
├── core/                 # 可进化区域——AI可以改写这里的一切
│   ├── brain.py          # Gemini API 调用
│   ├── evolve.py         # 进化主循环
│   ├── codemod.py        # 文件读写、备份、解析
│   ├── health.py         # 健康检查
│   ├── strategy.py       # 进化策略选择
│   └── prompts.py        # 提示词模板（最值得进化的文件）
│
├── backups/              # 每代进化前的自动快照（gitignored）
└── logs/                 # 进化历史与日志（gitignored）
```

**核心边界**：`bootstrap.py`、`config.yaml`、`requirements.txt` 被硬编码保护，AI 的写操作只能发生在 `core/` 目录内。

---

## 进化循环

每一代（generation）执行以下步骤：

```
1. READ      读取 core/ 下所有源文件
2. STRATEGY  选择本代改进目标（加权随机，偏向成功率高的策略）
3. GENERATE  Gemini 生成改进代码
             ├─ 模块化变异：只发送目标文件完整内容，其他文件发送签名摘要
             └─ 反思机制：附带最近10代历史（策略 + 结果），让AI避免重复失败
4. REVIEW    Gemini 自我审查（低温度，倾向保守）
             └─ 拒绝 → 跳过本代
4.5 VALIDATE 函数签名验证（AST对比，防止AI破坏现有接口）
             └─ 失败 → 跳过本代
5. BACKUP    备份当前 core/ 到 backups/
6. WRITE     将新代码写入磁盘
7. HEALTH    语法检查 + 模块导入 + API契约验证 + pytest测试（若存在）
             └─ 失败 → 回滚备份
8. COMMIT    git commit "gen-N: strategy"
9. DEPLOY    子进程热部署
             ├─ 成功 → 旧进程退出，新进程接管
             └─ 失败 → 回滚代码 + git reset HEAD~1
```

---

## 热部署机制

```
旧进程（PID: 1234）                 新进程（PID: 5678）
════════════════                    ════════════════
写入新代码到 core/
git commit
启动新进程 --replace 1234
轮询 .evolve_ready ...              导入新代码
                                    运行健康检查
                                    ✓ 通过 → 写入 .evolve_ready
检测到标记文件
删除标记
sys.exit(0) ───────────────────→   继续下一代进化
```

若新进程在30秒内未写入标记（或提前崩溃），旧进程回滚并继续当前代码的进化循环。

---

## 快速开始

**依赖**：Python 3.11+、Git、Gemini API Key

```bash
# 1. 克隆项目
git clone <repo-url>
cd openclaus

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 API Key
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入你的 Gemini API Key

# 4. 运行
python bootstrap.py
```

首次运行会自动初始化 git 仓库并创建初始 commit。

**仅验证环境，不修改代码**：
```bash
python bootstrap.py --dry-run
```

---

## 分支策略

本项目区分两类工作：人工开发种子代码 vs AI进化实验。

```
main
│  ← 你在这里开发和改进种子代码
│  seed-v0.1 (tag)
│  seed-v0.2 (tag)
│
├── evolve/seed-v0.1   ← 从 v0.1 启动的AI进化线
│   gen-4, gen-5, ...  ← AI自动提交
│
└── evolve/seed-v0.2   ← 从 v0.2 启动的AI进化线
    gen-1, gen-2, ...
```

**启动新一轮进化**：
```bash
# 在 main 上开发完毕，打 tag
git tag seed-v0.2

# 切出新的进化分支
git checkout -b evolve/seed-v0.2

# 启动进化
python bootstrap.py
```

**将 main 的改动同步到进化分支**：
```bash
git checkout evolve/seed-v0.1
git rebase main
```

---

## 配置说明

`config.yaml`（从 `config.example.yaml` 复制）：

```yaml
gemini:
  api_key: "YOUR_GEMINI_API_KEY"   # Gemini API Key
  model: "gemini-2.5-flash"         # 使用的模型

evolution:
  max_generations: 50               # 最多运行多少代
  delay_between_generations: 5      # 每代之间的间隔（秒）
  hot_deploy_timeout: 30            # 等待新进程健康的超时（秒）

safety:
  max_backups: 20                   # 最多保留多少代备份
  protected_files:                  # 禁止AI写入的文件
    - bootstrap.py
    - config.yaml
    - requirements.txt
```

`config.yaml` 含 API Key，已加入 `.gitignore`，不会被提交。

---

## 安全边界

| 保护机制 | 说明 |
|---|---|
| 文件写入限制 | `codemod.py` 拒绝写入 `core/` 外的任何路径 |
| 受保护文件列表 | `bootstrap.py`、`config.yaml`、`requirements.txt` 不可写 |
| 路径遍历防护 | 包含 `..` 的路径被拒绝 |
| 语法验证 | 写入前对每个文件执行 `ast.parse()` |
| 健康检查门控 | 新代码必须通过导入测试和 API 契约检查才能部署 |
| 自动回滚 | 健康检查失败或热部署失败时自动恢复备份并回退 git |
| eval/exec 检测 | 健康检查扫描禁止的危险构造 |

---

## 设计哲学

**种子要小，让 AI 自己长。**

本项目遵循以下几条原则，与通常的软件工程实践有所不同：

**1. Seed 是起点，不是终点**
`main` 分支的代码只需要"足够好地启动进化"，不必追求完美。过度手工打磨种子会让每条进化线都从同一个近乎完美的状态出发，减少了多样性和探索空间。

**2. 人工干预只修基础设施问题**
当进化卡住时（如解析器崩溃、API 契约被破坏、策略目标太模糊），才回到 `main` 修复根因。如果 AI 只是做了"无聊但安全"的改动，这是策略设计问题，不是代码问题。

**3. 将 AI 的进化成果"回收"到 seed**
当一条进化线产生了稳定有价值的改动（通过多代验证），可以挑选并合并回 `main`，打新 tag，开启下一轮进化——带着上一代的智慧重新出发。回收标准：让系统更稳定运行，而非仅仅增加功能。

**4. 观察优先于控制**
运行时不要干预。先让 AI 跑几十代，观察失败模式，再针对性地修改策略列表或提示词。每次改动应该针对一个具体的、可验证的根因。

**5. 策略要具体，不要笼统**
每条 `STRATEGIES` 应指定**具体文件**和**具体改动**，可通过 diff 验证是否真正执行。笼统的目标（如"improve robustness"）会让 AI 用安全但低价值的改动（加 try/except、加 logging）来敷衍。

---

## 进化机制

系统内置了三种改善进化质量的机制，均可被 AI 自身继续进化：

**反思机制（Reflection）**：每次生成时，AI 能看到最近 10 代的历史记录（策略文本 + 执行结果），从而避免重复已失败的路径，向已成功的方向靠拢。

**模块化变异（Modular Mutation）**：从策略文本中提取目标文件，只向 AI 发送该文件的完整内容；其他文件压缩为函数签名摘要。既降低 token 消耗，也让 AI 更聚焦于目标。

**测试驱动进化（TDD Scaffold）**：`health.py` 在 `core/tests/` 目录存在时自动运行 pytest。AI 可通过写测试文件这一进化策略逐步构建测试套件，被写入的测试将作为后续所有进化的约束门控。

---

## 已知限制

- **Gemini 输出格式不稳定**：部分模型/提示词组合下，Gemini 不按期望格式返回代码，导致该代跳过。解析器已针对此问题做鲁棒性处理。
- **无目标评估**：没有量化的"改进度"评估，AI 的改动可能是有益的，也可能是中性的。
- **API 费用**：每代至少消耗 2-3 次 Gemini API 调用（生成 + 审查），高频运行需注意费用。
- **语义 bug 无法检测**：健康检查只能发现语法错误和导入失败，语义上有问题但能运行的代码会通过检查。

---

## 依赖

```
google-genai>=1.0.0   # Gemini API SDK
pyyaml>=6.0           # YAML 配置解析
```

---

## 许可

MIT
