# Makefile for GlobalID V2

.PHONY: help install up down restart logs ps test test-health clean format lint check

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
	poetry install
	@echo "依赖安装完成"

test:
	@echo "运行测试..."
	poetry run pytest -v

test-health:
	@echo "运行健康检查..."
	poetry run python tests/test_health.py

format:
	@echo "格式化代码..."
	poetry run black src tests
	@echo "代码格式化完成"

lint:
	@echo "代码检查..."
	poetry run ruff check src tests
	@echo "检查完成"

check:
	@echo "完整检查..."
	@make format
	@make lint
	@echo "类型检查..."
	poetry run mypy src
	@make test
	@echo "所有检查完成"

# ========== 清理命令 ==========

clean:
	@echo "清理临时文件..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov .coverage
	@echo "清理完成"
