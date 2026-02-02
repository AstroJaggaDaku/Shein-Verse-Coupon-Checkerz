import os, time, re, threading, requests, urllib3
from flask import Flask, render_template_string, request, jsonify, session, redirect
from functools import wraps

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# ================== SECURITY ==================
app.secret_key = "x8Q@92LkP#E!4zM7$A^fW3R&dT5J"  # strong secret

APP_USERNAME = "Admin"
APP_PASSWORD = "Aezakmi"

AUTO_PROTECT_INTERVAL = 300  # 5 minutes

# ================== STATE ==================
class AppState:
    def __init__(self):
        self.is_running = False
        self.auto_protect = False
        self.cookies = ""
        self.vouchers = []
        self.checked = 0
        self.valid = 0
        self.invalid = 0
        self.valid_results = []
        self.invalid_results = []
        self.status = "Idle"
        self.stop_requested = False
        self.log = []

state = AppState()
lock = threading.RLock()

# ================== HELPERS ==================
def add_log(msg):
    with lock:
        state.log.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(state.log) > 30:
            state.log.pop(0)

def extract_vouchers(text):
    return list(dict.fromkeys(re.findall(r"[A-Z0-9]{4,30}", text.upper())))

def extract_cookies(text):
    if "cookie:" in text.lower():
        return text.split(":",1)[1].strip()
    return text.strip()

def make_headers(cookie):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://www.sheinindia.in",
        "Referer": "https://www.sheinindia.in/cart",
        "Cookie": cookie
    }

def check_single_voucher(session_http, code, headers):
    url = "https://www.sheinindia.in/api/cart/apply-voucher"
    reset_url = "https://www.sheinindia.in/api/cart/reset-voucher"
    payload = {"voucherId": code, "device": {"client_type": "web"}}

    try:
        r = session_http.post(url, json=payload, headers=headers, timeout=15, verify=False)

        if r.status_code in (403, 429):
            return False, "Blocked / Cloudflare", 0

        if r.status_code != 200:
            return False, f"HTTP {r.status_code}", 0

        data = r.json()

        for v in data.get("appliedVouchers", []):
            if v.get("code") == code:
                val = v.get("appliedValue", {}).get("value", 0)
                session_http.post(reset_url, json={"voucherId": code}, headers=headers, timeout=10, verify=False)
                return True, "VALID", val

        return False, "INVALID", 0

    except Exception as e:
        return False, str(e), 0

# ================== WORKER ==================
def worker_loop():
    while not state.stop_requested:
        session_http = requests.Session()
        headers = make_headers(state.cookies)

        for code in state.vouchers:
            if state.stop_requested:
                break

            with lock:
                state.status = f"Checking {code}"

            ok, msg, discount = check_single_voucher(session_http, code, headers)

            with lock:
                state.checked += 1
                if ok:
                    state.valid += 1
                    state.valid_results.insert(0, {"code": code, "discount": discount})
                else:
                    state.invalid += 1
                    state.invalid_results.insert(0, {"code": code, "msg": msg})

                add_log(f"{code} => {msg}")

            time.sleep(1)

        if not state.auto_protect:
            break

        with lock:
            state.status = "Sleeping (Auto Protect Mode)"
            add_log("Auto Protect: Waiting next cycle")

        for _ in range(AUTO_PROTECT_INTERVAL):
            if state.stop_requested:
                break
            time.sleep(1)

    with lock:
        state.is_running = False
        state.status = "Stopped"

# ================== AUTH ==================
def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if not session.get("logged_in"):
            return redirect("/login")
        return fn(*a, **k)
    return wrapper

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == APP_USERNAME and request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect("/")
        return "<h3>Invalid Login</h3>"
    return """
    <h2>Secure Login</h2>
    <form method="post">
    <input name="username" placeholder="Username"><br><br>
    <input type="password" name="password" placeholder="Password"><br><br>
    <button>Login</button>
    </form>
    """

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ================== ROUTES ==================
@app.route("/")
@login_required
def index():
    return render_template_string(HTML)

@app.route("/start", methods=["POST"])
@login_required
def start():
    data = request.json or {}

    with lock:
        state.cookies = extract_cookies(data.get("cookies",""))
        state.vouchers = extract_vouchers(data.get("vouchers",""))
        state.auto_protect = data.get("auto_protect", False)

        if not state.cookies or not state.vouchers:
            return jsonify({"error":"Missing cookies or vouchers"}),400

        state.checked = state.valid = state.invalid = 0
        state.valid_results.clear()
        state.invalid_results.clear()
        state.stop_requested = False
        state.is_running = True
        state.status = "Starting..."

    threading.Thread(target=worker_loop, daemon=True).start()
    return jsonify({"success":True})

@app.route("/stop", methods=["POST"])
@login_required
def stop():
    with lock:
        state.stop_requested = True
        state.status = "Stopping"
    return jsonify({"success":True})

@app.route("/status")
@login_required
def status():
    with lock:
        return jsonify({
            "running": state.is_running,
            "status": state.status,
            "checked": state.checked,
            "valid": state.valid,
            "invalid": state.invalid,
            "valid_results": state.valid_results[:20],
            "invalid_results": state.invalid_results[:20],
            "log": state.log[-10:]
        })

# ================== PREMIUM UI ==================
HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Premium Checker</title>
<style>
body{background:#0f172a;color:white;font-family:sans-serif}
.card{background:#1e293b;padding:20px;border-radius:12px;width:600px;margin:auto}
textarea{width:100%;height:80px}
button{padding:10px 15px;margin:5px}
pre{background:black;padding:10px;height:200px;overflow:auto}
</style>
</head>
<body>
<div class=card>
<h2>Premium Coupon Checker</h2>
<button onclick="logout()">Logout</button><br><br>
<textarea id=cookies placeholder="Cookies"></textarea>
<textarea id=vouchers placeholder="Vouchers"></textarea><br>
<label><input type=checkbox id=auto> Auto Protect Mode</label><br><br>
<button onclick=start()>Start</button>
<button onclick=stop()>Stop</button>
<pre id=out></pre>
</div>
<script>
function logout(){location='/logout'}
function start(){
fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({cookies:cookies.value,vouchers:vouchers.value,auto_protect:auto.checked})})
}
function stop(){fetch('/stop',{method:'POST'})}
setInterval(()=>{
fetch('/status').then(r=>r.json()).then(d=>{
out.textContent=JSON.stringify(d,null,2)
})
},2000)
</script>
</body>
</html>
"""

# ================== MAIN ==================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
