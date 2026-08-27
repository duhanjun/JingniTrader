# 开发约定

面向 jingni-trader 的贡献者与维护者。用户向文档见 [README.md](./README.md)。

---

## 1. 本地测试：必须用目录分批，禁止依赖全量 `pytest`

### 1.1 为什么

裸跑 `pytest tests`（全量单进程）**稳定复现** `Windows fatal exception: access violation`，
崩溃点约在 69% 处。这不是代码逻辑缺陷，而是**原生扩展（cvxpy / lightgbm / gm 等）
同进程加载引发的线程竞态**（见台账 OPEN-2026-0814-13）。

该崩溃具备两个特征，决定了它不能靠"再跑一次"解决：

- **非确定性**：多次二分（master+data_engine、data_engine+execution_monitor）均未能复现，
  而全量跑每次都崩 → 与执行顺序/加载时序相关，非特定用例触发
- **逐目录不崩**：8 个测试目录单独跑全部通过

### 1.2 实测对照

| 方式 | 结果 |
|------|------|
| `pytest tests`（全量） | ❌ 稳定崩溃（access violation，约 69% 处）|
| 目录分批 + 单进程 + 单线程 BLAS | ✅ **909 passed / 0 failed / 0 crash** |

### 1.3 本地准入门禁命令（推荐）

与 CI 的 `heavy-regression` job 同构（见 `.github/workflows/ci.yml`）：

```bash
# Linux / macOS / Git Bash
for d in tests/backtest_engine tests/data_engine tests/execution_monitor_engine \
         tests/factor_engine tests/master tests/portfolio_risk_engine \
         tests/reports_engine tests/strategy_model_engine; do
  echo "===== ${d} ====="
  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    python -m pytest "$d" -q -p no:cacheprovider -p no:randomly || exit 1
done
```

```powershell
# Windows PowerShell
$dirs = @('tests/backtest_engine','tests/data_engine','tests/execution_monitor_engine',
          'tests/factor_engine','tests/master','tests/portfolio_risk_engine',
          'tests/reports_engine','tests/strategy_model_engine')
foreach ($d in $dirs) {
  Write-Host "===== $d ====="
  $env:OMP_NUM_THREADS=1; $env:OPENBLAS_NUM_THREADS=1; $env:MKL_NUM_THREADS=1
  python -m pytest $d -q -p no:cacheprovider -p no:randomly
  if ($LASTEXITCODE -ne 0) { exit 1 }
}
```

三个环境变量的作用是抑制原生栈多线程竞态：

| 变量 | 作用 |
|------|------|
| `OMP_NUM_THREADS=1` | OpenMP 单线程 |
| `OPENBLAS_NUM_THREADS=1` | OpenBLAS 单线程 |
| `MKL_NUM_THREADS=1` | Intel MKL 单线程 |

`-p no:randomly` 固定用例顺序，保证原生库加载时序确定（若安装了 pytest-randomly）。

### 1.4 默认安全子集

`pytest.ini` 的 `addopts` 已排除 `heavy` 与全部 `requires_*` 标记，裸跑 `pytest` 不会
加载原生扩展。当前状态：

```
899/993 tests collected (94 deselected)
```

其中 94 项被 deselect 的是 heavy / 网络 / 真实凭证类用例，由 CI 的
`heavy-regression` job 按目录分批执行。

---

## 2. 提交前检查

### 2.1 pre-commit

```bash
pip install pre-commit && pre-commit install
pre-commit run --all-files     # 手动全跑
```

已配置的门禁（`.pre-commit-config.yaml`）：

| hook | 作用 |
|------|------|
| `python-syntax-check` | `compileall` 语法检查 |
| `sync-consistency` | **三方一致性校验**（见 §3） |
| `key-test-subset` | 关键契约测试子集 |

### 2.2 三方一致性校验

```bash
python scripts/check_sync_consistency.py            # 轻量（pre-commit 用）
python scripts/check_sync_consistency.py --deep     # 追加内容漂移比对
python scripts/check_sync_consistency.py --list-protected
python scripts/check_sync_consistency.py --json     # 机器可读
```

| 检查项 | 级别 | 内容 |
|--------|------|------|
| `tracked-artifact` | **ERROR** | 版本库混入运行时产物（已跟踪文件不受 .gitignore 保护）|
| `unprotected` | **ERROR** | 仓库副本 16 项受保护资产缺失（防反向覆盖）|
| `unpushed` | WARN | 领先/落后 origin/main |
| `git-dirty` | WARN | 未提交改动（自动排除产物）|
| `drift` | INFO（`--deep`）| 共有文件内容漂移 |

ERROR 级会阻断提交，WARN 不阻断。

---

## 3. 单一权威源与同步方向

**权威源是 `core/skills/jingni-tech/jingni-trader/`（A 树）。同步链单向：**

```
A（权威源）──①镜像──> B（core/ir/_shared/skills/jingni-trader/，裁剪引用版）
     │
     └──②发布──> M（d:/codebuddy/jingni-trader）──③push──> G（GitHub）

⛔ 禁止：G→A、G→M、M→A 的反向覆盖
```

规范详见 `knowledge/engineering/jingni-trader-source-of-truth.md`。

### 3.1 不要在 B 树直接编辑

B 树是 A→B 单向镜像产物，**不独立演进**。直接在 B 树改文件会被门禁
`shared.jt_sync` 判为漂移并阻断提交（该机制已实战生效）。

正确做法：改 **A 树**，然后

```bash
python core/tools/sync_shared_jingni_trader.py --apply
```

### 3.2 ⚠ 避免并发写同一目录

A 树 11-backend 资产回灌主目录期间，**不要多人/多会话并发写同一工作目录**。

已观察到的并发写入后果：

- `tests/data_engine` 用例数在 163 ↔ 289 之间跳变
- 文件一度 `No such file or directory`，随后又出现
- 未提交的中间态被其他会话的 `git checkout --` / `git stash` 撤走

**建议的串行归属**：回灌窗口期内，由单一负责人独占该工作目录，
其余会话只读，待回灌完成并提交后再接手。

---

## 4. 代码风格

- 格式化：`black` 风格（A 树侧行宽 120、双引号、类型注解用 `X | None` 而非 `Optional[X]`）
- 提交信息：`<type>: <说明>`，type 取 `feat` / `fix` / `docs` / `refactor` / `chore` / `test`
- 禁止：手写或手改 `core/generator/` 生成的产物；一律从 IR 经生成器产出

---

## 5. 安全纪律

- 密钥一律环境变量注入（`${ENV}`），**禁止硬编码**（门禁 `secret.scan` 会拦截）
- 一次性调试脚本（`_gm*.py`、`_diag*.py`、`_check_env.py`、`_e2e*` 等）已在
  `.gitignore`，不得入库
- 运行时产物（`workspace/`、`mlruns/`、`audit/`、`.pt_cleanup/`、`.codebuddy/`、
  `*.log`、`*.pkl`、`*.db`）不得入库

---

*仅模拟、绝不下单、不构成投资建议*
