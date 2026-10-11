# -*- coding: utf-8 -*-
"""批量导出原型/流程图 PNG 截图（调用本地导出服务 prototype_server.py）。

用法：
    python scripts/export_shots.py "需求名/原型"
    python scripts/export_shots.py "需求名/原型" --viewport 1440x900
    python scripts/export_shots.py "需求名/原型/01-某页.html"      # 单文件

前置：先启动导出服务（另开一个终端）
    PYTHONIOENCODING=utf-8 python scripts/prototype_server.py
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER = "http://localhost:8765"
ROOT = Path(__file__).resolve().parent.parent


def post(path, payload):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        SERVER + path,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(SERVER + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ensure_server():
    try:
        get("/api/status")
    except (urllib.error.URLError, OSError):
        sys.exit(
            "导出服务未启动。请另开终端执行：\n"
            "    PYTHONIOENCODING=utf-8 python scripts/prototype_server.py"
        )


def wait_done(timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = get("/api/status?ts=%d" % int(time.time() * 1000))
        if st.get("error"):
            return st
        if st.get("done") or not st.get("running"):
            return st
        time.sleep(1.2)
    return {"error": "等待超时（%ds）" % timeout}


def collect_targets(arg):
    p = (ROOT / arg).resolve()
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(f for f in p.glob("*.html") if not f.name.startswith("_"))
    sys.exit("路径不存在：%s" % p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="原型目录或单个 HTML（相对项目根目录）")
    ap.add_argument("--viewport", default="1440x900", help="视口尺寸，如 1440x900 或 375x812")
    args = ap.parse_args()

    try:
        vw, vh = (int(x) for x in args.viewport.lower().split("x"))
    except ValueError:
        sys.exit("--viewport 格式应为 宽x高，例如 1440x900")

    ensure_server()
    targets = collect_targets(args.target)
    if not targets:
        sys.exit("目录下没有 HTML 文件")

    print("共 %d 个页面，视口 %dx%d" % (len(targets), vw, vh))
    ok = fail = 0
    for html in targets:
        rel = html.relative_to(ROOT).as_posix()
        started = post("/api/screenshot", {"path": rel, "viewport": {"width": vw, "height": vh}})
        if not started.get("started"):
            print("  [跳过] %s -> %s" % (html.name, started.get("error") or started.get("message")))
            fail += 1
            continue
        st = wait_done()
        if st.get("error"):
            print("  [失败] %s -> %s" % (html.name, st["error"].splitlines()[0]))
            fail += 1
        else:
            files = st.get("files") or []
            print("  [完成] %s -> %d 张" % (html.name, len(files)))
            for f in files:
                print("         %s" % f)
            ok += 1

    print("\n成功 %d，失败 %d" % (ok, fail))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
