# Fixtures 数据维护指南

本目录是 `init_official_templates` 的**唯一数据源**，包含官方模板 JSON 与案例账单文件。**由社区维护**，欢迎通过 PR 贡献或讨论，详见下方规范。

## 目录结构

```
project/fixtures/
├── official_templates/   # 官方模板
│   ├── account.json
│   ├── mapping_expense.json
│   ├── mapping_income.json
│   └── mapping_assets.json
└── sample_files/         # 案例文件（微信/支付宝示例 CSV）
```

## 社区维护规范

- **贡献方式**：通过 Pull Request 修改 JSON 或替换案例文件，PR 描述请简要说明改动目的。
- **自测**：修改后本地执行 `init_official_templates`（或更新模板时加 `--force`）验证无误后再提交。
- **隐私**：映射模板中 `payee`/`payer` 等需脱敏或留空；案例文件不得包含真实隐私信息。
- **约定**：保持与下方数据结构一致；更新模板时建议同步更新 `version` 与 `update_notes`。

## 官方模板 (official_templates/)

| 文件 | 用途 |
|------|------|
| `account.json` | 官方账户模板 |
| `mapping_expense.json` | 官方支出映射 |
| `mapping_income.json` | 官方收入映射 |
| `mapping_assets.json` | 官方资产映射 |

**通用结构**：顶层含 `name`, `description`, `version`, `update_notes`；`items` 为数组。

- **account.json**：每项 `account_path`（必填）、`enable`、`reconciliation_rule`（受控自然语言循环规则，可空）、`description`（可选）。
- **mapping_*.json**：每项含 `key`、`account` 等，支出/收入另有 `payee`/`payer`（可选，需脱敏）。资产项用结构化身份描述账户：`institution_code`、`external_kind`、`tail`、`slot`、`display_name`（可选）。

缺失或校验失败时，`init_official_templates` 会报错并中止。

## 案例文件 (sample_files/)

- 当前：`完整测试_微信.csv`、`完整测试_支付宝.csv`（UTF-8 CSV）。
- 每次执行 `init_official_templates` 都会按本目录**强制覆盖** admin 的案例文件（删旧建新，无引用时同步删除 OSS 对象）；新用户注册时自动获得这些文件的引用。
- 替换文件后执行 `python manage.py init_official_templates` 即可，无需 `--force`。内容需具代表性、体积适中、无隐私信息。

### 微信账单：解析兼容性约定（重要）

微信支付导出的 CSV/xlsx **格式会随官方更新而变化**，本仓库**不承诺**兼容历史上所有旧版导出。

**必须长期兼容的两条基线**（修改 `WeChatPayInitStrategy`、`WeChat.py`、或微信相关 Excel 入口时不得破坏）：

1. **新用户案例账单**：[`sample_files/完整测试_微信.csv`](sample_files/完整测试_微信.csv)  
   - 代表「旧版 CSV 样式」的官方示例；所有新注册用户都会拿到该案例文件，用于上手解析。  
   - 解析逻辑须仍能正确识别并解析该文件（含 `¥`/`￥` 金额、表头与跳过行逻辑等）。
2. **当前线上微信导出**：以维护者在 issue/PR 中确认的**最新一版**为准（例如带 `金额(元)` 纯数字列的 xlsx 等）。  
   - 新增格式时优先**扩展识别与字段解析**，避免删掉对 `完整测试_微信.csv` 的支持。

若未来将案例文件整体换成「与微信最新导出一致」的新 CSV，须在 PR 中说明，并同步更新 `project/apps/translate/tests/` 中与微信相关的回归用例。

### 支付宝账单：解析兼容性约定（重要）

支付宝导出的 CSV/xlsx **格式会随官方更新而变化**，本仓库**不承诺**兼容历史上所有旧版导出。

**必须长期兼容的两条基线**（修改 `AlipayInitStrategy`、`AliPay.py`、或支付宝相关解析/忽略规则时不得破坏）：

1. **新用户案例账单**：[`sample_files/完整测试_支付宝.csv`](sample_files/完整测试_支付宝.csv)  
   - 代表「官方案例样式」的支付宝示例；所有新注册用户都会拿到该案例文件，用于上手解析。  
   - 解析逻辑须仍能正确识别并解析该文件。
2. **当前线上支付宝导出**：以维护者在 issue/PR 中确认的**最新一版**为准。  
   - 新增格式时优先**扩展识别与字段解析**，避免删掉对 `完整测试_支付宝.csv` 的支持。

若未来将案例文件整体换成与支付宝最新导出一致的新 CSV，须在 PR 中说明，并同步更新 `project/apps/translate/tests/` 中与支付宝相关的回归用例。

更完整的 fixtures 与初始化数据关系见：[`docs/模板与初始化数据梳理.md`](../docs/模板与初始化数据梳理.md)。

## 初始化命令

```bash
# 首次部署或仅更新案例文件
python manage.py init_official_templates

# 已修改官方模板 JSON，需强制重建模板
python manage.py init_official_templates --force
```

执行后将：创建/确认 admin 用户 → 加载并应用账户/映射模板（无 `--force` 时已存在则跳过）→ 创建格式化配置 → 按 fixtures 重建 admin 案例文件。
