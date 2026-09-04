# 贡献指南

感谢考虑为Re:Coach做贡献！

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

运行测试前请确保：

```bash
# 后端测试
cd recoach-server
pytest tests/ -v

# 前端测试
cd recoach-frontend
npm test
```

所有测试必须通过才能合并。

## 问题报告

提交Issue时请包含：
- 问题描述
- 复现步骤
- 期望行为
- 实际行为
- 环境信息（OS、Python版本、Node版本）
