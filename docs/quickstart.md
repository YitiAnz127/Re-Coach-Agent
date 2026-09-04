# 快速开始指南

[简体中文](quickstart.md) | [English](quickstart.en.md)

本指南将帮助你在5分钟内运行Re:Coach项目。

## 前置要求

- Docker >= 20.10
- Docker Compose >= 2.0
- (可选) Git

## 快速启动

### 1. 克隆项目（如果还没有）

```bash
git clone https://github.com/YitiAnz127/Re_Coach.git
cd Re_Coach
```

### 2. 一键启动

```bash
docker-compose up -d
```

第一次运行会构建镜像，大约需要3-5分钟。

### 3. 验证运行

访问以下URL验证服务是否正常：

- **前端**: http://localhost:4173
- **后端API文档**: http://localhost:8000/docs
- **后端健康检查**: http://localhost:8000/health

### 4. 开始使用

打开浏览器访问 http://localhost:4173，你应该能看到Re:Coach的界面。

默认配置使用**模板模式**（无需API key），可以体验基本功能。

## 配置真实LLM

如果想使用真实的AI模型，需要配置API key：

### 方式1: 使用环境变量（推荐）

创建 `.env` 文件：

```bash
# DeepSeek配置
export DEEPSEEK_API_KEY="your-api-key-here"

# 或者 Anthropic配置
export ANTHROPIC_API_KEY="your-api-key-here"
```

然后修改 `docker-compose.yml`，取消相应LLM配置的注释。

### 方式2: 直接修改docker-compose.yml

编辑 `docker-compose.yml`，找到backend服务的environment部分：

```yaml
environment:
  # 取消注释并配置DeepSeek
  - RECOACH_LLM_PROVIDER=deepseek
  - RECOACH_DEEPSEEK_API_KEY=your-api-key-here
  - RECOACH_DEEPSEEK_MODEL=deepseek-v4-flash
```

重启服务：

```bash
docker-compose restart backend
```

## 常用命令

### 查看日志

```bash
# 查看所有日志
docker-compose logs -f

# 只查看后端日志
docker-compose logs -f backend

# 只查看前端日志
docker-compose logs -f frontend
```

### 停止服务

```bash
docker-compose down
```

### 重启服务

```bash
docker-compose restart
```

### 清理并重新构建

```bash
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d
```

## 本地开发模式

如果你想进行开发而不是只运行项目：

### 后端开发

```bash
cd recoach-server

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 运行开发服务器
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 前端开发

```bash
cd recoach-frontend

# 安装依赖
npm install

# 运行开发服务器
npm run dev
```

开发服务器会在 http://localhost:5173 运行（注意不是4173）。

## 故障排除

### 端口已被占用

如果看到端口冲突错误，修改 `docker-compose.yml` 中的端口映射：

```yaml
ports:
  - "8001:8000"  # 将8000改为8001
```

### 前端无法连接后端

检查前端的环境变量配置，确保 `VITE_API_BASE_URL` 正确：

```yaml
environment:
  - VITE_API_BASE_URL=http://localhost:8000/api/v1
```

如果修改了后端端口，这里也要相应修改。

### 数据库文件权限问题

确保 `./data` 目录存在且有写权限：

```bash
mkdir -p data
chmod 755 data
```

### 查看容器状态

```bash
docker-compose ps
```

应该看到两个容器都是 `healthy` 状态。

## 下一步

- 查看 [API文档](http://localhost:8000/docs) 了解后端接口
- 阅读 [部署指南](deployment.md) 了解生产环境部署

## 需要帮助？

- 提交 [GitHub Issue](https://github.com/YitiAnz127/Re_Coach/issues)
- 查看项目 [README](../README.md)
