#!/usr/bin/env python3
import json, os, urllib.request
from pathlib import Path

ROOT = Path("/root/.hermes")
STATE = ROOT / "cron" / "jin10-seen.json"
LOCK = ROOT / "cron" / "jin10-dispatch.lock"
URL = "https://www.jin10.com/flash_newest.js?t=1"
API = "http://127.0.0.1:8642/v1/chat/completions"

def load_env():
    values = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value
    return values

def load_seen():
    try:
        return set(json.loads(STATE.read_text()))
    except Exception:
        return set()

def acquire_lock():
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(LOCK), flags, 0o600)
    except FileExistsError:
        return False
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True

def fetch_items():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    marker = "var newest = "
    start = raw.find(marker)
    if start < 0:
        raise RuntimeError("jin10 payload missing")
    payload = raw[start + len(marker):].strip().rstrip(";")
    return json.loads(payload)

def main():
    if not acquire_lock():
        return 0
    try:
        items = fetch_items()
        seen = load_seen()
        fresh = []
        ignored = []
        for item in items:
            news_id = str(item.get("id") or "")
            data = item.get("data") or {}
            content = str(data.get("content") or data.get("title") or "").strip()
            if not news_id or not content or news_id in seen:
                continue
            important = int(item.get("important") or 0)
            if important < 1:
                ignored.append(news_id)
                continue
            fresh.append({
                "id": news_id,
                "time": item.get("time"),
                "important": important,
                "content": content[:220],
            })
        fresh = fresh[:8]
        if ignored:
            seen.update(ignored)
        if not fresh:
            if ignored:
                STATE.parent.mkdir(parents=True, exist_ok=True)
                STATE.write_text(json.dumps(sorted(seen)[-800:], ensure_ascii=False))
            return 0
        seen.update(item["id"] for item in fresh)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(sorted(seen)[-800:], ensure_ascii=False))
        env = load_env()
        key = env.get("API_SERVER_KEY", "")
        prompt = (
            "使用 skill `market-event-48h-impact`。只评估下面这些未分析过的重要金十快讯，"
            "不要搜索或爬取任何网站。按 skill 合同 POST 到财经日历。"
            "每个品种都要给出是否有影响，以及影响程度 high/medium/low。"
            "无实质影响就提交空 events。成功后只返回新增条数和标题。\n"
            + json.dumps({"items": fresh}, ensure_ascii=False)
        )
        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            API,
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode())
        print(result["choices"][0]["message"]["content"][:500])
        return 0
    finally:
        try:
            os.remove(LOCK)
        except FileNotFoundError:
            pass

if __name__ == "__main__":
    raise SystemExit(main())
