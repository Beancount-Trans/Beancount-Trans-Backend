# Beancount-Trans-Backend

Beancount-Trans-Backend 是 Beancount-Trans 项目的后端子模块，用于处理与前端交互的数据和业务逻辑。

## 项目简介

Beancount-Trans-Backend 提供了与前端交互的 API 服务，处理用户请求及数据处理逻辑。本项目使用了 [Django](https://www.django-rest-framework.org/) 和 [Django Rest Framework](https://www.django-rest-framework.org/)（DRF）作为 Web 框架，并集成了多种扩展功能。

## 核心特性

- **开箱即用**: 新用户注册即可开始使用，无需手动配置
- **智能解析**: 支持支付宝、微信、多家银行账单自动识别和解析
- **模板系统**: 统一的账户、映射和配置模板管理
- **匿名试用**: 匿名用户可使用系统功能进行账单解析
- **AI 增强**: 支持 BERT、DeepSeek 等 AI 模型进行智能映射匹配

## 快速开始

### 1. 系统初始化

```bash
# 运行数据库迁移
python manage.py migrate

# 初始化官方模板和默认用户
python manage.py init_official_templates
```

此命令会自动创建：

- admin 用户（id=1，用于匿名访问）
- 77 个标准账户（Beancount 五大账户类型）
- 537 个常用映射（支出/收入/资产）
- 2 个案例账单文件（微信/支付宝）
- 默认格式化配置

### 2. 启动服务

```bash
python manage.py runserver
```

### 3. 访问 API

- API 文档: <http://localhost:8000/api/docs/>
- Admin 后台: <http://localhost:8000/admin/>
- 账单解析: <http://localhost:8000/api/translate/trans>

详细使用说明请查看 [快速开始指南](docs/QUICK_START.md)。

## 项目结构

```shell
Beancount-Trans-Backend/
├── bin/          # 启动、备份等脚本
├── conf/         # uwsgi 等配置
├── docs/         # 文档
├── project/      # Django 项目
│   ├── apps/     # account、maps 等应用
│   ├── fixtures/ # 官方模板与案例文件（社区维护，见 [project/fixtures/README.md](project/fixtures/README.md)）
│   └── utils/
├── translate/    # 账单解析（AliPay、WeChat、各银行等）
├── manage.py
└── pyproject.toml
```

## API 文档

本项目提供了详细的 [API 文档](https://trans.dhr2333.cn/api/docs)，涵盖所有请求和响应格式、示例等，由 Django REST framework 的 [API 文档方案](https://www.django-rest-framework.org/topics/documenting-your-api/#third-party-packages-for-openapi-support) 自动生成。

- **给人看**：[/api/docs/](https://trans.dhr2333.cn/api/docs) — ReDoc 网页
- **给机器/AI 用**：[/api/schema/](https://trans.dhr2333.cn/api/schema/) — OpenAPI 3 规范（JSON），可用于代码生成、契约测试或提供给 AI/工具读取

### 测试

在项目根目录运行：`pytest`（配置见 `pyproject.toml`）。

## 贡献指南

欢迎以 Issue（Bug/需求）、PR（代码/文档）、以及参与 [官方模板与案例数据](project/fixtures/README.md) 的社区维护等方式贡献。提交 PR 前请在本地测试，并保持代码风格与现有约定一致。

## 许可证

该项目的许可证信息，请查看 [LICENSE](https://github.com/Beancount-Trans/Beancount-Trans-Backend/blob/main/LICENSE.txt)。
