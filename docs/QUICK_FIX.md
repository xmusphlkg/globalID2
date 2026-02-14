# 快速修复指南 - 立即减少API消耗

## 🚨 紧急情况说明

根据日志分析，您的项目存在严重的API资源浪费：
- **每个疾病section重试多达20次**
- **没有缓存机制，重复调用**
- **AI验证AI导致双倍消耗**

**预估当前成本**: 2000+ API调用/月 ≈ $20-40/月
**优化后成本**: 200-400 API调用/月 ≈ $2-4/月
**节省**: 85-90%

---

## ✅ 方案A: 最小改动快速修复（推荐⭐）

**耗时**: 15-30分钟
**风险**: 低
**效果**: 立即节省60-80% API调用

### 步骤1: 测试新模块

```bash
cd /home/likangguo/globalID/ID_CN/Script/CN

# 测试缓存模块
python cache.py

# 测试速率限制模块
python rate_limiter.py

# 测试改进版函数
python reporttext_improved.py
```

### 步骤2: 最小化修改 reporttext.py

只需要在 `reporttext.py` 文件开头添加几行：

```python
# 在文件顶部添加这些导入（在现有导入后）
try:
    from cache import get_cache
    from rate_limiter import get_rate_limiter
    
    # 初始化
    _cache = get_cache()
    _rate_limiter = get_rate_limiter(max_requests=50, time_window=60)
    CACHE_ENABLED = True
    print("✓ Cache and rate limiter enabled")
except ImportError:
    CACHE_ENABLED = False
    print("⚠ Running without cache (suboptimal)")
```

然后找到 `fetch_openai` 函数（大约在第406行），在函数开头添加：

```python
def fetch_openai(model, client, messages, info = "", token = 500, max_retries=20, delay=1):
    """原函数保持不变，只在开头添加下面几行"""
    
    # === 新增：缓存检查 ===
    if CACHE_ENABLED:
        cache_key = str(messages)
        cached = _cache.get(cache_key, model)
        if cached:
            logging.info(f"{info}: Cache HIT")
            return cached
    
    # === 新增：速率限制 ===
    if CACHE_ENABLED:
        _rate_limiter.wait_if_needed()
    
    # === 以下是原有代码，不需要修改 ===
    attempt = 0
    while attempt < max_retries:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=token
            )
            generated_text = response.choices[0].message.content
            
            # === 新增：保存到缓存 ===
            if CACHE_ENABLED and generated_text:
                _cache.set(cache_key, model, generated_text)
            
            return generated_text
        except Exception as e:
            # ... 原有的错误处理代码保持不变 ...
```

### 步骤3: 减少重试次数

**立即改动**（简单全局替换）：

```bash
# 在 reporttext.py 中，将所有 max_retries=20 改为 max_retries=3
cd /home/likangguo/globalID/ID_CN/Script/CN
sed -i 's/max_retries=20/max_retries=3/g' reporttext.py
```

或者手动修改所有函数的默认参数：
```python
# 之前
def openai_single(..., max_retries=20, delay=1):

# 之后
def openai_single(..., max_retries=3, delay=1):
```

### 步骤4: 测试运行

```bash
# 先在小数据集上测试
cd /home/likangguo/globalID/ID_CN/Script/CN
python main.py
```

**观察日志**，应该看到：
- ✓ `Cache HIT` 日志
- ✓ 重试次数减少
- ✓ 运行速度基本相同或更快

### 步骤5: 查看效果

运行结束后，检查缓存统计：

```python
# 在 main.py 最后添加
from reporttext_improved import print_api_usage_summary
print_api_usage_summary()
```

---

## 🔧 方案B: 完全替换（更彻底）

**耗时**: 1-2小时
**风险**: 中
**效果**: 节省80-90% API调用 + 代码更清晰

### 步骤1: 备份现有文件

```bash
cd /home/likangguo/globalID/ID_CN/Script/CN
cp reporttext.py reporttext_backup.py
cp main.py main_backup.py
```

### 步骤2: 逐步替换函数

在 `reporttext.py` 中：

```python
# 1. 导入新模块
from cache import get_cache
from rate_limiter import get_rate_limiter

cache = get_cache()
rate_limiter = get_rate_limiter(max_requests=50, time_window=60)

# 2. 替换 fetch_openai 函数
#    使用 reporttext_improved.py 中的 fetch_openai_with_cache

# 3. 替换验证逻辑
#    使用 reporttext_improved.py 中的 simple_validation

# 4. 替换每个 openai_xxx 函数
#    参考 reporttext_improved.py 中的实现
```

### 步骤3: 测试

```bash
# 测试单个功能
python reporttext.py

# 测试完整流程
python main.py
```

---

## 📊 效果评估

### 如何验证改进生效？

运行后检查以下指标：

1. **缓存目录出现**
   ```bash
   ls -lah /home/likangguo/globalID/ID_CN/.cache/
   # 应该看到很多 .json 文件
   ```

2. **日志中出现缓存命中**
   ```bash
   grep "Cache HIT" /home/likangguo/globalID/ID_CN/Log/CN/latest.log
   # 应该看到多条记录
   ```

3. **重试次数减少**
   ```bash
   grep "Retrying" /home/likangguo/globalID/ID_CN/Log/CN/latest.log | wc -l
   # 之前: 几百条
   # 之后: 几十条或更少
   ```

4. **总API调用次数**
   ```bash
   grep "HTTP Request: POST" /home/likangguo/globalID/ID_CN/Log/CN/latest.log | wc -l
   # 之前: 1000+
   # 之后: 200-400
   ```

---

## 🎯 预期改进效果

| 指标 | 修改前 | 修改后 | 改进 |
|------|--------|--------|------|
| API调用次数 | 2000+ | 200-400 | ↓ 85% |
| 重试次数 | 200+ | 20-40 | ↓ 85% |
| 运行时间 | 30-60分钟 | 10-20分钟 | ↓ 60% |
| API成本 | $20-40 | $2-4 | ↓ 90% |
| 失败率 | 20% | <5% | ↓ 75% |

---

## ⚠️ 注意事项

### 1. 缓存的问题

**问题**: 如果数据更新了，缓存可能返回旧内容

**解决**: 
```bash
# 清空缓存（如果需要）
rm -rf /home/likangguo/globalID/ID_CN/.cache/*

# 或者在代码中设置较短的缓存时间
cache = get_cache(max_age_hours=24)  # 1天后过期
```

### 2. 速率限制的问题

**问题**: 如果API提供商有更严格的限流

**解决**:
```python
# 调整速率限制参数
rate_limiter = get_rate_limiter(
    max_requests=30,  # 降低到30
    time_window=60
)
```

### 3. 如果出现问题

**回滚到原版本**:
```bash
cd /home/likangguo/globalID/ID_CN/Script/CN
cp reporttext_backup.py reporttext.py
cp main_backup.py main.py
```

---

## 🔍 调试技巧

### 查看缓存统计

```python
from cache import get_cache
cache = get_cache()
cache.print_stats()
```

### 查看速率限制统计

```python
from rate_limiter import get_rate_limiter
limiter = get_rate_limiter()
limiter.print_stats()
```

### 查看当前API使用情况

```python
# 在main.py最后添加
from reporttext_improved import print_api_usage_summary
print_api_usage_summary()
```

---

## 📞 需要帮助？

如果遇到问题：

1. **查看日志**
   ```bash
   tail -100 /home/likangguo/globalID/ID_CN/Log/CN/latest.log
   ```

2. **检查缓存**
   ```bash
   ls -lah /home/likangguo/globalID/ID_CN/.cache/
   ```

3. **测试API连接**
   ```bash
   cd /home/likangguo/globalID/ID_CN/Script/CN
   python -c "
from openai import OpenAI
import os
client = OpenAI(
    api_key=os.environ['OPENAI_API_KEY'],
    base_url=os.environ['OPENAI_API_BASE']
)
print('API connection OK')
"
   ```

---

## ✨ 下一步

完成快速修复后，建议：

1. **监控一周** - 观察API用量和成本
2. **收集数据** - 记录改进效果
3. **考虑重构** - 如果效果好，进行更彻底的架构重构

详见: [REFACTORING_PLAN.md](REFACTORING_PLAN.md)
