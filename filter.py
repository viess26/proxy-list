import datetime, ipaddress, json, os, re, socket, ssl, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

KEEP = 50
MAX_CANDS = 3000
HOST = "speed.cloudflare.com"
KEYWORDS = ["proxyip", "cf proxyip", "优选 proxyip"]
TOKEN = os.environ.get("GITHUB_TOKEN", "")

def get(url, api=False):
    h = {"User-Agent": "Mozilla/5.0"}
    if api and TOKEN:
        h["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=60).read().decode(errors="ignore")

nets = [ipaddress.ip_network(x) for x in get("https://www.cloudflare.com/ips-v4").split()]

# 1. 自动找来源
urls = set()
since = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
for kw in KEYWORDS:
    q = urllib.parse.quote(f"{kw} pushed:>{since}")
    try:
        data = json.loads(get(f"https://api.github.com/search/repositories?q={q}&sort=updated&per_page=15", api=True))
    except Exception as e:
        print("搜索失败:", kw, e)
        continue
    for repo in data.get("items", []):
        full, br = repo["full_name"], repo["default_branch"]
        try:
            tree = json.loads(get(f"https://api.github.com/repos/{full}/git/trees/{br}?recursive=1", api=True))
        except Exception:
            continue
        n = 0
        for it in tree.get("tree", []):
            p = it["path"]
            if it["type"] == "blob" and p.lower().endswith(".txt") and it.get("size", 0) < 500000 \
               and re.search(r"ip|proxy|best|cf", p.lower()) and n < 5:
                urls.add(f"https://raw.githubusercontent.com/{full}/{br}/{p}")
                n += 1
        time.sleep(1)

# 2. 手动来源
if os.path.exists("sources.txt"):
    for line in open("sources.txt").read().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.add(line)
print("来源文件数:", len(urls))

# 3. 提取候选
cands = set()
for u in urls:
    try:
        text = get(u)
    except Exception:
        continue
    for ip, port in re.findall(r"(\d{1,3}(?:\.\d{1,3}){3})(?::(\d{2,5}))?", text):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if a.is_private or any(a in n for n in nets):
            continue
        cands.add((ip, int(port) if port else 443))
cands = list(cands)[:MAX_CANDS]
print("候选:", len(cands))

# 4. 验证
def test(c):
    ip, port = c
    try:
        t = time.time()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((ip, port), timeout=5) as s:
            with ctx.wrap_socket(s, server_hostname=HOST) as c2:
                c2.sendall(f"GET /cdn-cgi/trace HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n".encode())
                data = b""
                while len(data) < 4096:
                    chunk = c2.recv(2048)
                    if not chunk:
                        break
                    data += chunk
        text = data.decode(errors="ignore")
        if "colo=" not in text:
            return None
        loc = re.search(r"loc=(\w+)", text)
        return ((time.time() - t) * 1000, ip, port, loc.group(1) if loc else "XX")
    except Exception:
        return None

with ThreadPoolExecutor(100) as p:
    ok = sorted(r for r in p.map(test, cands) if r)
print("可用:", len(ok))

if not ok:
    sys.exit("没有可用 ProxyIP，不更新文件")

with open("ProxyIPs", "w") as f:
    f.write("\n".join(f"{ip}:{port}#{loc}" for _, ip, port, loc in ok[:KEEP]) + "\n")
