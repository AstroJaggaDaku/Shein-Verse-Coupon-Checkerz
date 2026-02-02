import os, time, re, threading, requests, urllib3
from flask import Flask, render_template_string, request, jsonify, session, redirect
from functools import wraps

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= APP =================
app = Flask(__name__)
app.secret_key = "x8Q@92LkP#E!4zM7$A^fW3R&dT5J"

APP_USERNAME = "Admin"
APP_PASSWORD = "Aezakmi"

AUTO_PROTECT_INTERVAL = 300  # 5 min
RETRY_LIMIT = 3

# ================= STATE =================
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

# ================= HELPERS =================
def add_log(msg):
    with lock:
        state.log.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(state.log) > 40:
            state.log.pop(0)

def extract_vouchers(text):
    found = re.findall(r"[A-Z0-9]{4,30}", text.upper())
    return list(dict.fromkeys(found))

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

# ================= CHECK LOGIC =================
def check_single_voucher(session_http, code, headers):
    url = "https://www.sheinindia.in/api/cart/apply-voucher"
    reset_url = "https://www.sheinindia.in/api/cart/reset-voucher"
    payload = {"voucherId": code, "device": {"client_type": "web"}}

    for attempt in range(RETRY_LIMIT):
        try:
            r = session_http.post(url, json=payload, headers=headers, timeout=15, verify=False)

            if r.status_code in (403, 429):
                return False, "BLOCKED / RATE LIMIT", 0

            if r.status_code == 400:
                try:
                    data = r.json()
                    msg = data.get("error", {}).get("message") or data.get("message") or "INVALID"
                    return False, msg, 0
                except:
                    return False, "INVALID (400)", 0

            if r.status_code != 200:
                if attempt < RETRY_LIMIT-1:
                    time.sleep(2)
                    continue
                return False, f"HTTP {r.status_code}", 0

            data = r.json()
            discount = 0
            applied = False

            # appliedVouchers
            for v in data.get("appliedVouchers", []):
                if v.get("code") == code:
                    discount = v.get("appliedValue", {}).get("value", 0)
                    applied = True
                    break

            # entries fallback
            if not applied:
                for entry in data.get("entries", []):
                    v_amt = entry.get("totalVoucherAmount", {}).get("value", 0)
                    promo = entry.get("voucherPromoAmt", 0)
                    if v_amt > 0 or promo > 0:
                        discount = max(v_amt, promo)
                        applied = True
                        break

            if applied:
                try:
                    session_http.post(reset_url, json={"voucherId": code}, headers=headers, timeout=10, verify=False)
                except:
                    pass
                return True, "VALID", discount

            return False, data.get("message","NOT APPLIED"), 0

        except Exception as e:
            if attempt < RETRY_LIMIT-1:
                time.sleep(2)
                continue
            return False, str(e), 0

    return False, "FAILED", 0

# ================= WORKER =================
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
            add_log("Auto Protect: Next cycle waiting")

        for _ in range(AUTO_PROTECT_INTERVAL):
            if state.stop_requested:
                break
            time.sleep(1)

    with lock:
        state.is_running = False
        state.status = "Stopped"

# ================= AUTH =================
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

# ================= ROUTES =================
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
            "valid_results": state.valid_results[:50],
            "invalid_results": state.invalid_results[:50],
            "log": state.log[-15:]
        })

# ================= UI =================
HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Ultimate Coupon Checker</title>
<style>
body{background:#0f172a;color:white;font-family:sans-serif}
.card{background:#1e293b;padding:20px;border-radius:12px;width:800px;margin:auto}
textarea{width:100%;height:80px}
button{padding:10px 15px;margin:5px}
pre{background:black;padding:10px;height:150px;overflow:auto}
.flex{display:flex;gap:10px}
.box{width:50%;background:#111827;padding:10px;border-radius:8px}
</style>
</head>
<body>
<div class=card>
<h2>Ultimate Coupon Checker</h2>
<button onclick="logout()">Logout</button><br><br>
<textarea id=cookies placeholder="Paste Cookies"></textarea>
<textarea id=vouchers placeholder="Paste Coupon List"></textarea><br>
<label><input type=checkbox id=auto> Auto Protect Mode</label><br><br>
<button onclick=startCheck()>Start</button>
<button onclick=stopCheck()>Stop</button>

<h3>Status</h3>
<pre id=statusBox></pre>

<div class=flex>
<div class=box>
<h3>✅ Valid Coupons</h3>
<pre id=validBox></pre>
</div>
<div class=box>
<h3>❌ Invalid / Expired</h3>
<pre id=invalidBox></pre>
</div>
</div>
<script>
function logout(){location='/logout'}
function startCheck(){
fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({cookies:cookies.value,vouchers:vouchers.value,auto_protect:auto.checked})})
}
function stopCheck(){fetch('/stop',{method:'POST'})}
setInterval(()=>{
fetch('/status').then(r=>r.json()).then(d=>{
statusBox.textContent = JSON.stringify(d,null,2)
validBox.textContent = d.valid_results.map(x=>x.code+" => ₹"+x.discount).join("\\n")
invalidBox.textContent = d.invalid_results.map(x=>x.code+" => "+x.msg).join("\\n")
})
},2000)
</script>
</body>
</html>
"""

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
