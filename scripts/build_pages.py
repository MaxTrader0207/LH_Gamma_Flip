"""
產生兩個版本的頁面：
- src/desktop.html          電腦版原始檔（唯一的計算引擎來源）
- src/mobile.src.html       手機版樣板，/*@@ENGINE@@*/ 會被替換成電腦版的計算引擎
輸出：
- build/desktop.html、build/mobile.html   給 Claude 頁面（artifact）發布用
- index.html、mobile.html                 給 GitHub Pages 用（加上完整 HTML 外框）
用法：python scripts/build_pages.py
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
SECTIONS = [
    ("/* ---------- math ---------- */", "/* ---------- JSON upload ---------- */"),
    ("/* ---------- engine ---------- */", "/* ---------- render ---------- */"),
    ("/* ---------- per-group advice ---------- */", "/* ---------- history ---------- */"),
]
WRAP_HEAD = ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
             '<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)}'
             'body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style></head><body>')


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


def write(p, s):
    full = os.path.join(ROOT, p)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(s)


def main():
    desk = read("src/desktop.html")
    parts = []
    for a, b in SECTIONS:
        i, j = desk.index(a), desk.index(b)
        parts.append(desk[i:j])
    engine = "\n".join(parts)
    if re.search(r"document\.|\$\(\"(?!rate\"|ivFallback\"|signMode\")", engine):
        raise SystemExit("計算引擎含有未預期的 DOM 存取，請檢查 src/desktop.html")
    mob = read("src/mobile.src.html")
    assert "/*@@ENGINE@@*/" in mob
    mob = mob.replace("/*@@ENGINE@@*/", "/* ===== 以下由 build_pages.py 自電腦版複製，請勿直接修改 ===== */\n"
                      + engine + "\n/* ===== 計算引擎結束 ===== */")
    urls = {}
    up = os.path.join(ROOT, "src", "artifact_urls.json")
    if os.path.exists(up):
        import json
        urls = json.load(open(up, encoding="utf-8"))
    desk_a = desk.replace("@@MOBILE_ARTIFACT@@", urls.get("mobile", ""))
    mob_a = mob.replace("@@DESKTOP_ARTIFACT@@", urls.get("desktop", ""))
    desk, mob = desk_a, mob_a
    write("build/desktop.html", desk)
    write("build/mobile.html", mob)
    write("index.html", WRAP_HEAD + desk + "</body></html>\n")
    write("mobile.html", WRAP_HEAD + mob + "</body></html>\n")
    print("ok", len(desk), len(mob))


if __name__ == "__main__":
    main()
