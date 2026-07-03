#!/usr/bin/env python3
"""Task Looper — 持续迭代，独立会话，状态外存。"""
import json, os, sys, time, subprocess, signal
from pathlib import Path

TASK_ID = sys.argv[1]
PROMPT = sys.argv[2]
INTERVAL = int(sys.argv[3])  # seconds
STATE_DIR = Path.home() / ".hermes" / "task_loops" / TASK_ID
STATE_FILE = STATE_DIR / "state.json"
STOP_FILE = STATE_DIR / "stop"

STATE_DIR.mkdir(parents=True, exist_ok=True)

# Init state
state = {"task": PROMPT, "interval_min": INTERVAL // 60, "round": 0, "last_summary": "", "status": "running"}
if STATE_FILE.exists():
    try:
        state = json.loads(STATE_FILE.read_text())
    except:
        pass

def save_state(**updates):
    state.update(updates)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

save_state(status="running")

print(f"[Looper] 任务: {PROMPT[:80]}")
print(f"[Looper] 间隔: {INTERVAL//60}分钟")
print(f"[Looper] 停止信号: {STOP_FILE}")
print(f"[Looper] 回复'停止'结束循环")

while True:
    # Check stop signal
    if STOP_FILE.exists():
        save_state(status="stopped")
        STOP_FILE.unlink()
        print(f"[Looper] 收到停止信号，循环结束。共 {state['round']} 轮。")
        break

    state["round"] += 1
    save_state(status=f"running_round_{state['round']}")

    # Build prompt with last summary
    full_prompt = PROMPT
    if state.get("last_summary"):
        full_prompt = f"[上轮结果] {state['last_summary']}\n\n[本轮任务] {PROMPT}"

    print(f"\n{'='*50}")
    print(f"[Looper] 第 {state['round']} 轮开始...")
    print(f"[Looper] Prompt: {full_prompt[:150]}...")

    try:
        result = subprocess.run(
            ["/home/andore/.local/bin/hermes", "chat", "-q", full_prompt],
            capture_output=True, text=True, timeout=max(300, INTERVAL * 2),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}
        )
        output = result.stdout.strip()
        if not output:
            output = result.stderr.strip()[:500]

        # Save summary (last 200 chars)
        summary = output[-200:] if len(output) > 200 else output
        save_state(last_summary=summary, last_output=output[:2000])
        
        print(f"[Looper] 第 {state['round']} 轮完成")
        print(f"[Looper] 摘要: {summary[:100]}...")

    except subprocess.TimeoutExpired:
        save_state(last_summary="[超时]", status="timeout_round")
        print("[Looper] ⚠️ 超时")
    except Exception as e:
        save_state(last_summary=f"[错误: {e}]", status="error_round")
        print(f"[Looper] ❌ 错误: {e}")

    # Wait for next interval
    print(f"[Looper] 等待 {INTERVAL//60} 分钟...")
    for _ in range(INTERVAL):
        if STOP_FILE.exists():
            break
        time.sleep(1)

save_state(status="completed")
print("[Looper] 结束")
