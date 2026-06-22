import asyncio
import time
import os
import sys
import statistics
import json
import httpx

# Ensure project root is in python path
project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_dir)

from src import database
from src import utils
from src import llm
from src import config

DB_PATH = os.path.join(project_dir, "tests_tmp", "benchmark_bot.db")

# Sample inputs for HTML/Markdown conversion
sample_texts = {
    "plain": "Hello world, this is a plain text message with no formatting.",
    "formatting": "This is **bold** text, *italic* text, __underline__ text, and ~~strikethrough~~ text. Also a ||spoiler||.",
    "list_code": "* Item 1\n- Item 2\n+ Item 3\nHere is a code block:\n```python\ndef hello():\n    print('Hello World')\n```\nAnd inline code `import os`.",
    "math": "Solve the equation: $x^2 + y^2 = z^2$. Here is a block formula:\n$$e = mc^2$$\nAnd another one \\[\\sum_{i=1}^n i = \\frac{n(n+1)}{2}\\]",
    "table": "| Product | Calories | Proteins | Fats | Carbs |\n|:---|---|---|---|---|\n| Apple | 52 | 0.3 | 0.2 | 13.8 |\n| Banana | 89 | 1.1 | 0.3 | 22.8 |\n| Chicken Breast | 165 | 31.0 | 3.6 | 0.0 |\n| Salmon | 208 | 20.0 | 13.0 | 0.0 |",
    "complex": """# Detailed Nutrition Guide
## Introduction
This is a **detailed guide** to help you understand nutrition.
> "Let food be thy medicine and medicine thy food." — Hippocrates

* First list item
* Second list item with a nested link: [Google](https://google.com)

Here is a table of metrics:
| Metric | Value | Reference |
|---|---|---|
| BMI | 22.5 | Normal |
| Body Fat | 15% | Athlete |
| Water | 60% | Good |

Some formulas to calculate:
$$BMR = 10 \\times W + 6.25 \\times H - 5 \\times A + 5$$
Or inline formula like $BMR$.

Run this code to calculate BMI:
```python
def calculate_bmi(weight_kg, height_m):
    return weight_kg / (height_m ** 2)
```
Let's see if ||spoiler works||."""
}

def compute_stats(latencies):
    latencies_ms = [l * 1000 for l in latencies]
    latencies_ms.sort()
    n = len(latencies_ms)
    mean_val = statistics.mean(latencies_ms) if latencies_ms else 0
    median_val = statistics.median(latencies_ms) if latencies_ms else 0
    min_val = min(latencies_ms) if latencies_ms else 0
    max_val = max(latencies_ms) if latencies_ms else 0
    p90 = latencies_ms[int(n * 0.90)] if n > 0 else 0
    p95 = latencies_ms[int(n * 0.95)] if n > 0 else 0
    p99 = latencies_ms[int(n * 0.99)] if n > 0 else 0
    
    avg_sec = mean_val / 1000.0
    throughput = 1.0 / avg_sec if avg_sec > 0 else 0.0
    
    return {
        "mean_ms": mean_val,
        "median_ms": median_val,
        "min_ms": min_val,
        "max_ms": max_val,
        "p90_ms": p90,
        "p95_ms": p95,
        "p99_ms": p99,
        "throughput_ops_sec": throughput
    }

async def benchmark_db_sequential(db_path):
    print("Running database sequential benchmark...")
    if os.path.exists(db_path):
        try: os.remove(db_path)
        except: pass
    for suffix in ["-wal", "-shm"]:
        if os.path.exists(db_path + suffix):
            try: os.remove(db_path + suffix)
            except: pass
            
    await database.init_db(db_path)
    
    ops_to_test = [
        ("upsert_user", lambda uid: database.upsert_user(uid, f"user_{uid}", "First", "Last", db_path=db_path)),
        ("set_user_mode", lambda uid: database.set_user_mode(uid, "nutrition", db_path=db_path)),
        ("set_user_setting", lambda uid: database.set_user_setting(uid, "language", "en", db_path=db_path)),
        ("log_message_user", lambda uid: database.log_message(uid, "user", "Hello bot!", db_path=db_path)),
        ("log_message_bot", lambda uid: database.log_message(uid, "assistant", "Hello! How can I help you?", db_path=db_path)),
        ("log_usage_stats", lambda uid: database.log_usage_stats(uid, "gemini-2.5-flash", 100, 200, 0.45, db_path=db_path)),
        ("get_user_mode", lambda uid: database.get_user_mode(uid, db_path=db_path)),
        ("get_user_settings", lambda uid: database.get_user_settings(uid, db_path=db_path)),
        ("get_chat_history", lambda uid: database.get_chat_history(uid, limit=10, db_path=db_path)),
        ("get_usage_stats", lambda uid: database.get_usage_stats(uid, db_path=db_path)),
        ("clear_chat_history", lambda uid: database.clear_chat_history(uid, db_path=db_path))
    ]
    
    num_iterations = 100
    results = {}
    
    for name, op_func in ops_to_test:
        latencies = []
        for i in range(num_iterations):
            uid = 10000 + i
            # Setup prerequisite data to pass foreign key validation
            if name in ["log_message_user", "log_message_bot", "log_usage_stats", "clear_chat_history"]:
                await database.upsert_user(uid, f"user_{uid}", "First", "Last", db_path=db_path)
            
            start = time.perf_counter()
            await op_func(uid)
            latencies.append(time.perf_counter() - start)
            
        results[name] = compute_stats(latencies)
        
    return results

async def run_db_transaction_loop(task_id, db_path):
    uid = 20000 + task_id
    operations = 0
    start_time = time.perf_counter()
    try:
        # 1. Upsert User
        await database.upsert_user(uid, f"user_{uid}", "First", "Last", db_path=db_path)
        operations += 1
        # 2. Get settings
        await database.get_user_settings(uid, db_path=db_path)
        operations += 1
        # 3. Set mode
        await database.set_user_mode(uid, "nutrition", db_path=db_path)
        operations += 1
        # 4. Log message user
        await database.log_message(uid, "user", "Show me the food info.", db_path=db_path)
        operations += 1
        # 5. Log message bot
        await database.log_message(uid, "assistant", "Here is your table:\n| A | B |\n|---|---|", db_path=db_path)
        operations += 1
        # 6. Get chat history
        await database.get_chat_history(uid, limit=10, db_path=db_path)
        operations += 1
        # 7. Log usage stats
        await database.log_usage_stats(uid, "gemini-2.5-flash", 120, 180, 0.52, db_path=db_path)
        operations += 1
        # 8. Get usage stats
        await database.get_usage_stats(uid, db_path=db_path)
        operations += 1
        
        duration = time.perf_counter() - start_time
        return True, duration, operations
    except Exception as e:
        duration = time.perf_counter() - start_time
        print(f"Task {task_id} failed: {e}")
        return False, duration, operations

async def benchmark_db_concurrent(db_path, concurrency_levels=[5, 10, 25, 50, 100]):
    print("Running database concurrent benchmark...")
    if os.path.exists(db_path):
        try: os.remove(db_path)
        except: pass
    await database.init_db(db_path)
    
    results = {}
    for c in concurrency_levels:
        print(f"Testing concurrency level: {c}...")
        start_bench = time.perf_counter()
        tasks = [run_db_transaction_loop(i, db_path) for i in range(c)]
        outputs = await asyncio.gather(*tasks)
        total_bench_time = time.perf_counter() - start_bench
        
        successes = sum(1 for ok, _, _ in outputs if ok)
        total_ops = sum(ops for _, _, ops in outputs)
        durations = [dur for ok, dur, _ in outputs if ok]
        
        results[c] = {
            "concurrency": c,
            "success_rate": successes / c if c > 0 else 0.0,
            "total_ops": total_ops,
            "total_time_sec": total_bench_time,
            "aggregate_throughput_ops_sec": total_ops / total_bench_time if total_bench_time > 0 else 0.0,
            "avg_transaction_time_ms": statistics.mean(durations) * 1000 if durations else 0.0,
            "median_transaction_time_ms": statistics.median(durations) * 1000 if durations else 0.0,
            "max_transaction_time_ms": max(durations) * 1000 if durations else 0.0,
        }
    return results

def benchmark_conversions():
    print("Running HTML and Markdown conversion benchmark...")
    results = {}
    num_iterations = 1000
    
    for text_name, text_val in sample_texts.items():
        # Benchmark normalize_markdown_tables
        normalize_times = []
        for _ in range(num_iterations):
            start = time.perf_counter()
            _ = utils.normalize_markdown_tables(text_val)
            normalize_times.append(time.perf_counter() - start)
            
        # Benchmark to_telegram_html
        to_html_times = []
        for _ in range(num_iterations):
            start = time.perf_counter()
            _ = utils.to_telegram_html(text_val)
            to_html_times.append(time.perf_counter() - start)
            
        results[text_name] = {
            "normalize_markdown_tables": compute_stats(normalize_times),
            "to_telegram_html": compute_stats(to_html_times)
        }
    return results

async def measure_endpoint_latency(client, name, url, count=10):
    latencies = []
    success_count = 0
    for _ in range(count):
        start = time.perf_counter()
        try:
            # Simple GET request
            response = await client.get(url, timeout=10.0)
            latencies.append(time.perf_counter() - start)
            success_count += 1
        except Exception as e:
            pass
    
    if latencies:
        return {
            "name": name,
            "success_rate": success_count / count,
            "stats": compute_stats(latencies)
        }
    else:
        return {
            "name": name,
            "success_rate": 0.0,
            "stats": None
        }

async def benchmark_api_network():
    print("Running API network roundtrip benchmark...")
    token = config.TELEGRAM_BOT_TOKEN
    tg_url = f"https://api.telegram.org/bot{token}/getMe" if token else "https://api.telegram.org"
    
    endpoints = [
        ("Telegram Bot API", tg_url),
        ("Google Gemini API", "https://generativelanguage.googleapis.com/v1beta/models"),
        ("OpenRouter API", "https://openrouter.ai/api/v1/models")
    ]
    
    results = {}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for name, url in endpoints:
            print(f"Pinging {name}...")
            results[name] = await measure_endpoint_latency(client, name, url, count=10)
            
        print("Testing concurrent API requests...")
        concurrent_results = {}
        for name, url in endpoints:
            start_time = time.perf_counter()
            tasks = [client.get(url, timeout=15.0) for _ in range(10)]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            duration = time.perf_counter() - start_time
            successes = sum(1 for r in responses if isinstance(r, httpx.Response) and r.status_code < 500)
            
            concurrent_results[name] = {
                "concurrency": 10,
                "success_rate": successes / 10.0,
                "total_duration_sec": duration,
                "avg_per_request_ms": (duration / 10.0) * 1000
            }
        results["concurrent_requests"] = concurrent_results
        
    return results

async def main():
    print("=== STARTING PERFORMANCE BENCHMARK SUITE ===")
    
    # 1. DB Sequential
    db_seq = await benchmark_db_sequential(DB_PATH)
    
    # 2. DB Concurrent
    db_con = await benchmark_db_concurrent(DB_PATH)
    
    # 3. Conversion Routines
    conv = benchmark_conversions()
    
    # 4. API Network Latency
    net = await benchmark_api_network()
    
    output_data = {
        "db_sequential": db_seq,
        "db_concurrent": db_con,
        "conversions": conv,
        "network": net
    }
    
    out_file = os.path.join(project_dir, "tests_tmp", "benchmark_raw_results.json")
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(output_data, f, indent=2)
        
    print(f"\nBenchmarks completed! Raw results saved to {out_file}")
    
    print("\n=== SUMMARY RESULTS ===")
    print("\n[Database Operations (Sequential - Average Latency)]")
    for op, stats in db_seq.items():
        print(f" - {op:20s}: {stats['mean_ms']:6.2f} ms ({stats['throughput_ops_sec']:6.1f} ops/sec)")
        
    print("\n[Database Concurrency (WAL Mode)]")
    for c, stats in db_con.items():
        print(f" - Concurrency {c:3d}: Success {stats['success_rate']*100:5.1f}%, Aggregate Throughput: {stats['aggregate_throughput_ops_sec']:6.1f} ops/sec, Avg Trans Time: {stats['avg_transaction_time_ms']:6.2f} ms")
        
    print("\n[Conversions Latency (to_telegram_html vs normalize_markdown_tables)]")
    for text_name, stats in conv.items():
        t_html = stats["to_telegram_html"]["mean_ms"]
        norm_t = stats["normalize_markdown_tables"]["mean_ms"]
        ratio = t_html / norm_t if norm_t > 0 else 0
        print(f" - {text_name:10s}: to_telegram_html: {t_html:6.3f} ms | normalize_markdown_tables: {norm_t:6.3f} ms | Ratio: {ratio:.1f}x")
        
    print("\n[API Network Roundtrips (Sequential - Average Latency)]")
    for name, data in net.items():
        if name == "concurrent_requests": continue
        stats = data["stats"]
        mean_lat = stats["mean_ms"] if stats else float('nan')
        print(f" - {name:20s}: {mean_lat:6.1f} ms (Success: {data['success_rate']*100:.1f}%)")

if __name__ == "__main__":
    asyncio.run(main())
