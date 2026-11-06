"""
產生離線版單一 HTML：Chart.js 與當日資料（data/latest.json、data/history.json）內嵌在檔案裡，
不需要網路，下載後雙擊即可開啟。輸出到 offline/。
用法：python scripts/build_offline.py [前綴，例如 TXO 或 PTA]
"""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
CDN = '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>'
FONTS = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "TXO"
    chart = read("vendor/chart.umd.min.js").replace("</script", "<\\/script")
    latest = json.loads(read("data/latest.json"))
    hist = json.loads(read("data/history.json"))
    date = latest.get("tradeDate", "").replace("-", "")
    names = {"desktop": f"{prefix}_GammaFlip_電腦版_離線.html", "mobile": f"{prefix}_GammaFlip_手機版_離線.html"}

    def js(o):
        return json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    shim = ("<script>(function(){var D={\"./data/latest.json\":" + js(latest) + ",\"./data/history.json\":" + js(hist)
            + "};var of=window.fetch;window.fetch=function(u,o){var k=typeof u===\"string\"?u:(u&&u.url)||\"\";"
            "for(var p in D){if(k===p||k.slice(-p.length+1)===p.slice(1)){return Promise.resolve(new Response(JSON.stringify(D[p]),"
            "{status:200,headers:{\"Content-Type\":\"application/json\"}}));}}"
            "return of?of.apply(this,arguments):Promise.reject(new Error(\"offline\"));};})();</script>")
    note = (f"<!-- 離線版：資料日 {latest.get('tradeDate','')}，Chart.js 4.4.1（MIT）與資料皆內嵌，不需網路。"
            "要看最新資料請重新下載或開啟線上版。 -->")
    os.makedirs(os.path.join(ROOT, "offline"), exist_ok=True)
    for key, src in (("desktop", "index.html"), ("mobile", "mobile.html")):
        s = read(src)
        assert CDN in s, f"{src} 找不到 Chart.js CDN 標籤"
        s = s.replace(CDN, f"<script>{chart}</script>{shim}", 1)
        # 字型改用系統字型（離線時 Google Fonts 載不到，避免等待逾時）
        i = s.find(FONTS)
        if i >= 0:
            j = s.index(">", i) + 1
            s = s[:i] + s[j:]
        s = s.replace('<link rel="preconnect" href="https://fonts.googleapis.com">', "")
        # 兩個離線檔之間的互相連結
        s = s.replace('href="./mobile.html"', f'href="./{names["mobile"]}"')
        s = s.replace('$("deskLink").href="./index.html";', f'$("deskLink").href="./{names["desktop"]}";')
        s = s.replace("<body>", "<body>" + note, 1)
        with open(os.path.join(ROOT, "offline", names[key]), "w", encoding="utf-8") as f:
            f.write(s)
        print("ok", names[key], len(s), date)


if __name__ == "__main__":
    main()
