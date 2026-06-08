# 朱雀AI小说

朱雀AI小说是一个面向长篇小说创作的 AI 工作台，覆盖项目规划、世界观、角色与组织、章节创作、剧情分析、伏笔管理、提示词、拆书导入、封面生成和漫画生产等流程。

## 功能概览

- 项目管理：创建和维护小说项目、封面、主题、写作参数和系统配置。
- 世界观与规划：生成和编辑世界观、职业体系、大纲、章节展开规划、角色、组织与关系。
- 章节工作流：章节创建、编辑、阅读、重写、润色、批量生成、单章分析和导出。
- 剧情分析：提取钩子、伏笔、情节点、角色状态、节奏和评分结果。
- 连续性管理：根据章节分析同步记忆、角色状态、组织变化和伏笔状态。
- 视觉生产：角色/组织形象图、角色多版本、项目封面、漫画分镜和漫画页。
- 管理能力：本地登录、可选 OAuth / 邮箱登录、用户管理、AI 供应商配置、MCP 插件、系统设置。

## 技术栈

- 前端：React 18、TypeScript、Vite、Ant Design、Axios、Zustand、React Router
- 后端：FastAPI、Pydantic、SQLAlchemy async、Alembic、Uvicorn
- 数据：PostgreSQL 或 SQLite，另有向量检索能力用于长期记忆
- AI 集成：OpenAI 兼容接口、Anthropic、Gemini、图像生成服务、MCP 工具

## 目录结构

```text
.
├── backend/      # FastAPI 后端、模型、API、迁移
├── frontend/     # React 前端
├── docs/         # 架构与验证文档
├── images/       # 项目图片资料
├── storage/      # 本地运行存储目录
└── README.md
```

## 本地开发

复制示例配置并填写自己的数据库、AI、登录、邮箱和对象存储配置：

```bash
cp backend/.env.example .env
```

启动后端：

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

启动前端：

```bash
cd frontend
pnpm install
pnpm dev
```

开发模式下，Vite 会把 `/api` 和 `/generated-assets` 代理到后端。

## 配置

配置统一通过环境变量或根目录 `.env` 提供。请不要提交真实密钥、服务器地址、数据库连接串、对象存储地址或部署参数。

常用配置分类：

- 应用：`APP_NAME`、`APP_VERSION`、`APP_PORT`、`DEBUG`、`FRONTEND_URL`、`CORS_ORIGINS`
- 数据库：`DATABASE_URL`、`POSTGRES_*`、`DATABASE_POOL_*`
- 文本 AI：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`GEMINI_API_KEY`、`ANTHROPIC_API_KEY`、`DEFAULT_AI_PROVIDER`、`DEFAULT_MODEL`
- 图像 AI：`HERMES_IMAGE_BASE_URL`、`HERMES_IMAGE_API_KEY`、`HERMES_IMAGE_MODEL`
- 登录：`LOCAL_AUTH_*`、`LINUXDO_*`、`EMAIL_*`、`SMTP_*`、`SESSION_*`
- 对象存储：`TENCENT_COS_*`
- UI 资产：`VITE_ZHUQUE_ASSET_BASE_URL`

## 验证

提交前建议执行：

```bash
cd frontend && npm run build
cd frontend && npm run lint
cd backend && python -m compileall app
```

## 安全说明

本公开仓库不包含真实部署脚本、服务器信息、数据库连接信息、对象存储地址或历史 `.env` 文件。部署时请在私有环境中维护相关配置。

## License

本项目采用 GPL v3 许可，见 [LICENSE](LICENSE)。
