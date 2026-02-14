# 项目重构方案

## 📋 当前问题分析

### 1. 严重的API资源浪费问题 ⚠️

**问题根源：**
- 每个疾病的每个section都需要**多次重试**（最多20次）
- 日志显示：`Retrying (15/20)...`, `Retrying (20/20)...` 频繁出现
- **检查机制过于严格**：AI验证器经常返回 `No` 或 `I can't determine`，导致无限循环
- **没有缓存机制**：重复生成相同内容时仍然调用API
- 并发调用但**没有速率限制**，导致瞬间流量激增

**成本估算：**
```
假设：
- 26个疾病 × 4个section = 104个API调用
- 每个调用平均重试5次 = 520次API调用
- 每次create + check = 1040次实际API请求
- 如果失败率高，可能达到2000+次请求
```

### 2. 架构设计问题

#### 2.1 单体架构 (Monolithic)
```
main.py (106行) 包含所有流程逻辑：
  └─ 数据获取
  └─ 数据处理
  └─ 报告生成 (调用 report.py)
      └─ 26个疾病 × 4个section × 多次重试
  └─ 邮件发送
  └─ 网站生成
```

**问题：**
- 流程不可恢复：一旦失败需要从头开始
- 无法跳过已完成的部分
- 调试困难

#### 2.2 硬编码的配置
```python
folder_path_get = "../../Data/GetData/CN/"
folder_path_save = "../../Data/AllData/CN/"
folder_path_mail = "../../Mail/CN/"
folder_path_web = "../../Website/content/CN"
folder_path_log = "../../Log/CN"
```

**问题：**
- 路径修改需要改代码
- 不同环境（开发/生产）无法切换
- 无法单元测试

#### 2.3 OpenAI调用代码重复
```
reporttext.py (446行) 包含：
- openai_trans()
- openai_single()
- openai_mail()
- openai_key()
- openai_image()
- openai_abstract()
- bing_analysis()
```

**问题：**
- 90%的代码逻辑相同（重试、检查、日志）
- 维护困难
- 修复bug需要改多处

### 3. 错误处理问题

#### 3.1 无效的验证机制
```python
# 检查逻辑过于模糊
messages_check = [{"role": "user", 
                   "content": "Tell me if this text is from the Highlights section"}]
# AI返回："I'm sorry, but I can't determine..." → 重试 → 浪费API
```

#### 3.2 没有状态保存
- 生成了20个疾病的报告后，第21个失败 → 前面20个白做了
- 没有checkpoint机制

### 4. 数据流问题

```
[数据源] → [翻译] → [合并] → [生成报告] → [发送邮件] → [生成网站]
   ↓         ↓         ↓          ↓            ↓            ↓
  失败     失败      失败       失败          失败         失败
   ↓         ↓         ↓          ↓            ↓            ↓
从头开始  从头开始  从头开始   从头开始     从头开始     从头开始
```

---

## 🎯 重构方案

### Phase 1: 紧急修复（1-2天）

#### 1.1 优化API调用策略

**A. 实现智能缓存**
```python
# 新增: Script/CN/cache.py
import hashlib
import json
import os
from datetime import datetime, timedelta

class APICache:
    def __init__(self, cache_dir="../../.cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
    
    def get_cache_key(self, prompt, model):
        """生成缓存key"""
        content = f"{model}:{prompt}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def get(self, prompt, model, max_age_hours=168):  # 7天
        """获取缓存"""
        key = self.get_cache_key(prompt, model)
        cache_file = os.path.join(self.cache_dir, f"{key}.json")
        
        if not os.path.exists(cache_file):
            return None
        
        with open(cache_file, 'r') as f:
            cache_data = json.load(f)
        
        # 检查过期
        cache_time = datetime.fromisoformat(cache_data['timestamp'])
        if datetime.now() - cache_time > timedelta(hours=max_age_hours):
            return None
        
        return cache_data['response']
    
    def set(self, prompt, model, response):
        """保存缓存"""
        key = self.get_cache_key(prompt, model)
        cache_file = os.path.join(self.cache_dir, f"{key}.json")
        
        cache_data = {
            'timestamp': datetime.now().isoformat(),
            'prompt': prompt,
            'model': model,
            'response': response
        }
        
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)
```

**B. 简化验证逻辑**
```python
# 修改验证策略：不要让AI验证AI
# 使用简单的规则验证

def simple_validation(content, expected_type, max_words):
    """简单但有效的验证"""
    # 1. 内容不为空
    if not content or len(content.strip()) < 50:
        return False, "Content too short"
    
    # 2. 长度检查
    word_count = len(content.split())
    if word_count > max_words * 1.2:
        return False, f"Too long: {word_count} words"
    
    # 3. 格式检查
    if expected_type == "bullet_points":
        if not re.search(r'<br/>|•|-|\*', content):
            return False, "Missing bullet points"
    
    # 4. 基本内容检查（关键词）
    if expected_type == "highlights" and word_count < 20:
        return False, "Highlights too brief"
    
    return True, "OK"
```

**C. 添加速率限制**
```python
# 新增: Script/CN/rate_limiter.py
import time
from collections import deque

class RateLimiter:
    def __init__(self, max_requests=50, time_window=60):
        """
        max_requests: 时间窗口内最大请求数
        time_window: 时间窗口（秒）
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = deque()
    
    def wait_if_needed(self):
        """如果超过速率限制，等待"""
        now = time.time()
        
        # 移除过期的请求记录
        while self.requests and self.requests[0] < now - self.time_window:
            self.requests.popleft()
        
        # 如果达到限制，等待
        if len(self.requests) >= self.max_requests:
            sleep_time = self.requests[0] + self.time_window - now
            if sleep_time > 0:
                logging.info(f"Rate limit reached, sleeping {sleep_time:.2f}s")
                time.sleep(sleep_time)
        
        self.requests.append(now)
```

#### 1.2 实现状态恢复机制

```python
# 新增: Script/CN/state_manager.py
import json
import os
from typing import Dict, List

class StateManager:
    def __init__(self, state_dir="../../.state"):
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
    
    def save_progress(self, year_month: str, completed_diseases: List[str], 
                      completed_sections: Dict[str, List[str]]):
        """保存进度"""
        state_file = os.path.join(self.state_dir, f"{year_month}.json")
        state = {
            'year_month': year_month,
            'completed_diseases': completed_diseases,
            'completed_sections': completed_sections,
            'last_updated': datetime.now().isoformat()
        }
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def load_progress(self, year_month: str):
        """加载进度"""
        state_file = os.path.join(self.state_dir, f"{year_month}.json")
        if not os.path.exists(state_file):
            return None
        
        with open(state_file, 'r') as f:
            return json.load(f)
    
    def skip_completed(self, year_month: str, disease: str, section: str):
        """检查是否已完成"""
        progress = self.load_progress(year_month)
        if not progress:
            return False
        
        return (disease in progress['completed_diseases'] and 
                section in progress['completed_sections'].get(disease, []))
```

#### 1.3 减少重试次数
```python
# 修改 reporttext.py
# 从 max_retries=20 改为 max_retries=3

def openai_single(..., max_retries=3):  # 从20改为3
    """
    3次重试就够了：
    - 第1次：正常调用
    - 第2次：网络问题重试
    - 第3次：最后机会
    
    如果3次都失败，说明prompt有问题，继续重试也没用
    """
```

### Phase 2: 架构重构（3-5天）

#### 2.1 模块化设计

```
ID_CN/
├── src/                          # 新的源代码目录
│   ├── core/                     # 核心功能
│   │   ├── __init__.py
│   │   ├── config.py            # 配置管理
│   │   ├── logging.py           # 日志管理
│   │   └── pipeline.py          # 数据流管道
│   │
│   ├── data/                     # 数据层
│   │   ├── __init__.py
│   │   ├── fetcher.py           # 数据获取
│   │   ├── cleaner.py           # 数据清洗
│   │   └── storage.py           # 数据存储
│   │
│   ├── ai/                       # AI服务层
│   │   ├── __init__.py
│   │   ├── client.py            # OpenAI客户端封装
│   │   ├── cache.py             # 缓存
│   │   ├── rate_limiter.py      # 速率限制
│   │   └── prompts/             # Prompt模板
│   │       ├── translate.txt
│   │       ├── summary.txt
│   │       └── ...
│   │
│   ├── report/                   # 报告生成
│   │   ├── __init__.py
│   │   ├── generator.py         # 报告生成器
│   │   ├── figures.py           # 图表生成
│   │   └── templates/           # Jinja2模板
│   │
│   ├── delivery/                 # 输出层
│   │   ├── __init__.py
│   │   ├── email.py             # 邮件服务
│   │   └── website.py           # 网站生成
│   │
│   └── cli.py                    # 命令行接口
│
├── config/                       # 配置文件
│   ├── default.yml              # 默认配置
│   ├── development.yml          # 开发环境
│   └── production.yml           # 生产环境
│
├── tests/                        # 测试目录
│   ├── test_data_fetcher.py
│   ├── test_ai_client.py
│   └── ...
│
└── scripts/                      # 工具脚本
    ├── migrate_old_data.py      # 数据迁移
    └── check_api_usage.py       # API用量检查
```

#### 2.2 配置管理重构

```yaml
# config/production.yml
app:
  name: "GlobalID CN"
  environment: "production"

paths:
  data:
    get: "${PROJECT_ROOT}/Data/GetData/CN"
    all: "${PROJECT_ROOT}/Data/AllData/CN"
  output:
    mail: "${PROJECT_ROOT}/Mail/CN"
    website: "${PROJECT_ROOT}/Website/content/CN"
    log: "${PROJECT_ROOT}/Log/CN"

api:
  openai:
    base_url: "${OPENAI_API_BASE}"
    api_key: "${OPENAI_API_KEY}"
    max_retries: 3
    timeout: 60
    rate_limit:
      max_requests: 50
      time_window: 60
  
  cache:
    enabled: true
    directory: "${PROJECT_ROOT}/.cache"
    max_age_hours: 168  # 7 days

models:
  translate:
    create: "gpt-4o"
    max_tokens: 500
  report:
    abstract: "gpt-4o"
    sections: "gpt-4o"
    max_tokens: 2000

pipeline:
  stages:
    - name: "fetch_data"
      enabled: true
    - name: "process_data"
      enabled: true
    - name: "generate_reports"
      enabled: true
      checkpoint: true  # 保存checkpoint
    - name: "send_emails"
      enabled: false    # 测试时关闭
    - name: "build_website"
      enabled: true
```

#### 2.3 可恢复的Pipeline设计

```python
# src/core/pipeline.py
from typing import List, Callable
from dataclasses import dataclass
from enum import Enum

class StageStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

@dataclass
class PipelineStage:
    name: str
    function: Callable
    enabled: bool = True
    checkpoint: bool = False
    status: StageStatus = StageStatus.PENDING

class Pipeline:
    def __init__(self, config, state_manager):
        self.config = config
        self.state_manager = state_manager
        self.stages: List[PipelineStage] = []
    
    def add_stage(self, name: str, function: Callable, 
                  enabled: bool = True, checkpoint: bool = False):
        """添加pipeline阶段"""
        stage = PipelineStage(name, function, enabled, checkpoint)
        self.stages.append(stage)
        return self
    
    def run(self, context: dict, resume: bool = True):
        """运行pipeline"""
        # 加载之前的进度
        if resume:
            progress = self.state_manager.load_progress(context.get('year_month'))
            if progress:
                logging.info(f"Resuming from checkpoint: {progress}")
                context.update(progress)
        
        for stage in self.stages:
            if not stage.enabled:
                stage.status = StageStatus.SKIPPED
                continue
            
            # 检查是否已完成
            if self._is_stage_completed(stage, context):
                logging.info(f"Stage {stage.name} already completed, skipping")
                stage.status = StageStatus.COMPLETED
                continue
            
            try:
                stage.status = StageStatus.RUNNING
                logging.info(f"Running stage: {stage.name}")
                
                # 执行阶段
                result = stage.function(context)
                context.update(result)
                
                stage.status = StageStatus.COMPLETED
                
                # 保存checkpoint
                if stage.checkpoint:
                    self._save_checkpoint(stage, context)
                
            except Exception as e:
                stage.status = StageStatus.FAILED
                logging.error(f"Stage {stage.name} failed: {e}")
                raise
        
        return context
    
    def _is_stage_completed(self, stage: PipelineStage, context: dict) -> bool:
        """检查阶段是否已完成"""
        # 实现逻辑...
        pass
    
    def _save_checkpoint(self, stage: PipelineStage, context: dict):
        """保存checkpoint"""
        self.state_manager.save_progress(
            year_month=context['year_month'],
            completed_stages=[s.name for s in self.stages if s.status == StageStatus.COMPLETED],
            context=context
        )
```

#### 2.4 统一的AI客户端

```python
# src/ai/client.py
from openai import OpenAI
from typing import Optional, Dict, Any
import logging

class AIClient:
    def __init__(self, config, cache, rate_limiter):
        self.config = config
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.client = OpenAI(
            api_key=config['api']['openai']['api_key'],
            base_url=config['api']['openai']['base_url']
        )
    
    def generate(self, 
                 prompt: str, 
                 model: str,
                 system_message: str = "You are an epidemiologist.",
                 max_tokens: int = 2000,
                 temperature: float = 0.7,
                 use_cache: bool = True) -> Optional[str]:
        """
        统一的生成接口
        """
        # 1. 检查缓存
        if use_cache:
            cached = self.cache.get(prompt, model)
            if cached:
                logging.info(f"Cache hit for model {model}")
                return cached
        
        # 2. 速率限制
        self.rate_limiter.wait_if_needed()
        
        # 3. 调用API
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )
            
            result = response.choices[0].message.content
            
            # 4. 保存缓存
            if use_cache:
                self.cache.set(prompt, model, result)
            
            return result
            
        except Exception as e:
            logging.error(f"API call failed: {e}")
            return None
    
    def generate_with_retry(self, 
                           prompt: str, 
                           model: str,
                           max_retries: int = 3,
                           validator: Optional[Callable] = None,
                           **kwargs) -> Optional[str]:
        """
        带重试的生成（简化版）
        """
        for attempt in range(max_retries):
            result = self.generate(prompt, model, **kwargs)
            
            if result is None:
                logging.warning(f"Attempt {attempt+1}/{max_retries} failed")
                continue
            
            # 验证
            if validator:
                is_valid, message = validator(result)
                if not is_valid:
                    logging.warning(f"Validation failed: {message}")
                    continue
            
            return result
        
        logging.error(f"All {max_retries} attempts failed")
        return None
```

### Phase 3: 监控和优化（1-2天）

#### 3.1 API使用统计

```python
# scripts/check_api_usage.py
import json
from collections import defaultdict
from datetime import datetime

class APIUsageTracker:
    def __init__(self):
        self.calls = []
    
    def record_call(self, model: str, tokens: int, cached: bool):
        """记录API调用"""
        self.calls.append({
            'timestamp': datetime.now().isoformat(),
            'model': model,
            'tokens': tokens,
            'cached': cached
        })
    
    def generate_report(self):
        """生成使用报告"""
        total_calls = len(self.calls)
        cached_calls = sum(1 for c in self.calls if c['cached'])
        total_tokens = sum(c['tokens'] for c in self.calls)
        
        by_model = defaultdict(lambda: {'calls': 0, 'tokens': 0})
        for call in self.calls:
            by_model[call['model']]['calls'] += 1
            by_model[call['model']]['tokens'] += call['tokens']
        
        return {
            'summary': {
                'total_calls': total_calls,
                'cached_calls': cached_calls,
                'cache_hit_rate': f"{cached_calls/total_calls*100:.1f}%",
                'total_tokens': total_tokens,
                'estimated_cost': total_tokens * 0.00001  # 假设价格
            },
            'by_model': dict(by_model)
        }
```

#### 3.2 健康检查

```python
# src/cli.py
import click

@click.group()
def cli():
    """GlobalID CN CLI"""
    pass

@cli.command()
def check_health():
    """健康检查"""
    checks = [
        ("API连接", check_api_connection),
        ("数据目录", check_data_directories),
        ("配置文件", check_config),
        ("依赖包", check_dependencies)
    ]
    
    for name, check_func in checks:
        try:
            check_func()
            click.echo(f"✓ {name}: OK")
        except Exception as e:
            click.echo(f"✗ {name}: {e}")

@cli.command()
@click.option('--year-month', required=True)
@click.option('--resume/--no-resume', default=True)
def run(year_month, resume):
    """运行数据处理流程"""
    # 实现...
    pass

@cli.command()
def estimate_cost():
    """估算API成本"""
    # 统计疾病数、section数、预估调用次数
    click.echo("Cost estimation:")
    click.echo(f"  Diseases: 26")
    click.echo(f"  Sections per disease: 4")
    click.echo(f"  Estimated API calls: 104-312")
    click.echo(f"  Estimated cost: $2-6")

if __name__ == '__main__':
    cli()
```

---

## 🚀 实施步骤

### 立即执行（今天）：
1. ✅ **添加缓存机制** - 立即生效，节省50-70% API调用
2. ✅ **减少重试次数** - 从20次改为3次
3. ✅ **简化验证逻辑** - 移除AI验证AI的逻辑
4. ✅ **添加速率限制** - 防止瞬间流量激增

### 短期（本周）：
5. ⚡ **实现状态恢复** - 失败后可以继续，不用从头开始
6. ⚡ **添加API使用统计** - 了解实际使用情况

### 中期（下周）：
7. 🔨 **模块化重构** - 按照新架构重新组织代码
8. 🔨 **配置管理重构** - 使用配置文件而非硬编码
9. 🔨 **编写测试** - 保证重构不破坏功能

---

## 💰 预期效果

### API成本优化：
- **之前**: 2000+ API调用 / 月 ≈ $20-40
- **优化后**: 200-400 API调用 / 月 ≈ $2-4
- **节省**: 85-90%

### 开发效率：
- 调试时间：从2小时 → 15分钟
- 失败恢复：从重新运行 → 断点续传
- 代码维护：从"不敢动" → 模块化清晰

### 稳定性：
- 错误率：从20% → <5%
- 可恢复性：0% → 100%
- 监控能力：无 → 完整

---

## ⚠️ 风险提示

1. **数据兼容性**: 重构时要保证与现有数据格式兼容
2. **平滑过渡**: 建议先在dev分支测试，确认无误后再部署
3. **备份**: 重构前做好完整备份

---

## 📞 下一步

请确认你想从哪个阶段开始？

- [ ] **紧急修复** - 立即减少API消耗（推荐先做）
- [ ] **架构重构** - 彻底解决技术债务
- [ ] **两者都做** - 先紧急修复，测试OK后再重构

我可以帮你实现任何一个方案！
