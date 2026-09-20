# 贡献指南

感谢考虑为 Re:Coach 做贡献！

## 开发流程

1. Fork这个仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交改动 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 提交Pull Request

## 代码规范

### Python代码
- 遵循PEP 8
- 使用类型注解
- 添加docstrings

### TypeScript代码
- 遵循项目ESLint配置
- 使用明确的类型定义
- 避免使用`any`

## 提交消息

使用清晰的提交消息：
- `feat: 添加新功能`
- `fix: 修复bug`
- `docs: 更新文档`
- `test: 添加测试`
- `refactor: 重构代码`

## 测试

三个部分各有自己的测试命令：

```bash
# 后端测试（18 个测试文件，217 passed）
cd recoach-server
.\.venv\Scripts\python.exe -m pytest -q          # macOS / Linux: ./.venv/bin/python -m pytest -q

# 前端测试（4 个测试文件，22 passed，Node 内置 test runner）
cd recoach-frontend
npm test
npm run check                                    # TypeScript 类型检查

# TUI 测试（6 个测试文件，92 passed，vitest）
cd re-coach-tui
npm test
npm run typecheck
```

所有测试必须通过才能合并。CI 目前跑后端与前端两个 job，
TUI 测试请在本地手动执行（见 `.github/workflows/test.yml`）。

## 改动配置或接口字段后

这类问题单看一个文件发现不了：配置项加了却忘了同步 `.env.example`、
后端字段加了却忘了同步前端类型、残留调试输出等。改完跑一遍审计脚本：

```bash
python tools/consistency_audit.py
```

退出码 0 表示全部通过。脚本会自行定位仓库位置，在任意目录下运行都可以。

另外两条约定：

- 新增 `RECOACH_*` 配置项时，必须同时写进 `recoach-server/.env.example`；若有意推荐非默认值，
  需在 `tools/consistency_audit.py` 的 `INTENTIONAL_DIVERGENCE` 中登记理由。
- 应用配置（模型、密钥、限流、记忆开关）只放 `recoach-server/.env`，
  不要写进 `docker-compose.yml` 的 `environment`——那里优先级更高，会静默覆盖用户的 `.env`。

## 问题报告

提交Issue时请包含：
- 问题描述
- 复现步骤
- 期望行为
- 实际行为
- 环境信息（OS、Python版本、Node版本）
