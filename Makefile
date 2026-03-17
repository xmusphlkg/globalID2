# Makefile for GlobalID V2

.PHONY: help install up down restart logs ps test test-health clean format lint check \
	site-data site-install site-dev site-build site-preview site-publish wpp-import

help:
	@echo "GlobalID V2 - 开发命令"
	@echo ""
	@echo "Docker 服务:"
	@echo "  make up           启动所有 Docker 服务"
	@echo "  make down         停止所有 Docker 服务"
	@echo "  make restart      重启所有服务"
	@echo "  make logs         查看服务日志"
	@echo "  make ps           查看服务状态"
	@echo ""
	@echo "开发环境:"
	@echo "  make install      安装 Python 依赖（需要 Poetry）"
	@echo "  make test         运行所有测试"
	@echo "  make test-health  运行健康检查"
	@echo "  make format       格式化代码"
	@echo "  make lint         代码检查"
	@echo "  make check        完整检查（格式+类型+测试）"
	@echo ""
	@echo "静态站点 (Cloudflare Pages):"
	@echo "  make site-install  安装 Astro/npm 依赖"
	@echo "  make site-data     导出数据库数据到 JSON 文件"
	@echo "  make site-dev      启动本地开发预览"
	@echo "  make site-build    生产构建"
	@echo "  make site-preview  预览生产构建"
	@echo "  make site-publish  一键导出数据 + 构建"
	@echo "  make wpp-import    导入 WPP 人口数据到数据库"
	@echo ""
	@echo "清理:"
	@echo "  make clean        清理临时文件"

# ========== Docker 命令 ==========

up:
	@echo "启动 Docker 服务..."
	sudo docker-compose up -d
	@echo "等待服务启动..."
	@sleep 5
	@make ps

down:
	@echo "停止 Docker 服务..."
	sudo docker-compose down

restart:
	@make down
	@make up

logs:
	sudo docker-compose logs -f

ps:
	sudo docker-compose ps

# ========== 开发命令 ==========

install:
	@echo "安装依赖..."
	python3 -m venv venv && venv/bin/pip install -r requirements.txt
	@echo "依赖安装完成"

test:
	@echo "运行测试..."
	venv/bin/pytest -v

test-health:
	@echo "运行健康检查..."
	venv/bin/python3 tests/test_health.py

format:
	@echo "格式化代码..."
	venv/bin/black src tests
	@echo "代码格式化完成"

lint:
	@echo "代码检查..."
	venv/bin/ruff check src tests
	@echo "检查完成"

check:
	@echo "完整检查..."
	@make format
	@make lint
	@echo "类型检查..."
	venv/bin/mypy src
	@make test
	@echo "所有检查完成"

# ========== 清理命令 ==========

# ========== 静态站点 (Cloudflare Pages) ==========

site-data:
	@echo "导出站点数据到 astro-site/src/data/ ..."
	venv/bin/python3 scripts/generate_site_data.py
	@echo "数据导出完成"

site-install:
	@echo "安装 Astro 依赖..."
	cd astro-site && npm install
	@echo "Astro 依赖安装完成"

site-dev:
	@echo "启动 Astro 开发服务器..."
	cd astro-site && npm run dev

site-build:
	@echo "构建静态站点..."
	cd astro-site && npm run build
	@echo "构建完成，输出目录: astro-site/dist/"

site-preview:
	@echo "预览生产构建..."
	cd astro-site && npm run preview

# 完整的端到端发布流程: 导出数据 → 构建 → 提示推送
site-publish: site-data site-build
	@echo ""
	@echo "✓ 站点已构建完毕"
	@echo "请执行以下命令推送到 Cloudflare Pages:"
	@echo "  git add astro-site/src/data astro-site/dist"
	@echo "  git commit -m 'chore: update site data and rebuild'"
	@echo "  git push"

wpp-import:
	@echo "导入 WPP 人口数据..."
	venv/bin/python3 scripts/import_wpp_population.py
	@echo "WPP 人口数据导入完成"

clean:
	@echo "清理临时文件..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov .coverage
	@echo "清理完成"
