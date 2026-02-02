import os, time, re, threading, requests, urllib3
from flask import Flask, render_template_string, request, jsonify, session, redirect

urllib3.disable_warnings()

app = Flask(__name__)
app.secret_key = "9fH7@Kq2!M#8Zp$E6vW0xR3L^D*SaB"

APP_USERNAME = "Admin"
APP_PASSWORD = "Aezakmi"

AUTO_PROTECT_INTERVAL = 300  # 5 minutes

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

# ================= Helpers =================

def add_log(msg):
    with lock:
        state.log.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(state.log) > 30:
            state.log.pop(0)

def extract_vouchers(text):
    return list(dict.fromkeys(re.findall(r"[A-Z0-9]{4,30}", text.upper())))

def extract_cookies(text):
    return text.split(":",1)[1].strip() if "cookie:" in text.lower() else text.strip()

def check_single_voucher(session_http, code, headers):
    url = "https://www.sheinindia.in/api/cart/apply-voucher"
    reset_url = "https://www.sheinindia.in/api/cart/reset-voucher"
    payload = {"voucherId": code, "device": {"client_type": "web"}}
    try:
        r = session_http.post(url, json=payload, headers=headers, timeout=15, verify=False)
        if r.status_code in (403,429):
            return False, "Rate Limited / Blocked", 0
        if r.status_code != 200:
            return False, "HTTP Error", 0
        data = r.json()
        for v in data.get("appliedVouchers", []):
            if v.get("code") == code:
                val = v.get("appliedValue", {}).get("value", 0)
                session_http.post(reset_url, json={"voucherId": code}, headers=headers, timeout=10, verify=False)
                return True, "Valid", val
        return False, "Invalid", 0
    except Exception as e:
        return False, str(e), 0

# ================= Worker =================

def worker_loop():
    while not state.stop_requested:
        session_http = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "Cookie": state.cookies
        }

        for code in state.vouchers:
            if state.stop_requested:
                break
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

        state.status = "Sleeping (Auto Protect Mode)"
        add_log("Auto Protect: Waiting next cycle")
        for _ in range(AUTO_PROTECT_INTERVAL):
            if state.stop_requested:
                break
            time.sleep(1)

    state.is_running = False
    state.status = "Stopped"

# ================= Auth =================

def login_required(fn):
    def wrap(*a,**k):
        if not session.get("logged_in"):
            return redirect("/login")
        return fn(*a,**k)
    wrap.__name__ = fn.__name__
    return wrap

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        if request.form["username"]==APP_USERNAME and request.form["password"]==APP_PASSWORD:
            session["logged_in"]=True
            return redirect("/")
        return "Invalid Login"
    return """<form method=post>
    <h2>Login</h2>
    <input name=username><br><br>
    <input type=password name=password><br><br>
    <button>Login</button></form>"""

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ================= Routes =================

@app.route("/")
@login_required
def index():
    return render_template_string(HTML)

@app.route("/start", methods=["POST"])
@login_required
def start():
    data=request.json
    state.cookies=extract_cookies(data.get("cookies",""))
    state.vouchers=extract_vouchers(data.get("vouchers",""))
    state.auto_protect=data.get("auto_protect",False)

    if not state.cookies or not state.vouchers:
        return jsonify({"error":"Missing cookies or vouchers"}),400

    state.checked=state.valid=state.invalid=0
    state.valid_results.clear()
    state.invalid_results.clear()
    state.stop_requested=False
    state.is_running=True
    threading.Thread(target=worker_loop,daemon=True).start()
    return jsonify({"success":True})

@app.route("/stop",methods=["POST"])
@login_required
def stop():
    state.stop_requested=True
    state.status="Stopping"
    return jsonify({"success":True})

@app.route("/status")
@login_required
def status():
    return jsonify({
        "running":state.is_running,
        "status":state.status,
        "checked":state.checked,
        "valid":state.valid,
        "invalid":state.invalid,
        "valid_results":state.valid_results[:20],
        "invalid_results":state.invalid_results[:20],
        "log":state.log[-10:]
    })

# ================= Premium UI =================

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

# ================= Main =================

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
