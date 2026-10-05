from flask import (
    Flask, render_template_string, request, redirect,
    url_for, session, flash, send_from_directory
)
import json
import os
import uuid
import random
import hashlib
import secrets
import csv
import io
import re
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("MONEYPLUS_SECRET_KEY", secrets.token_hex(32))

BANK_NAME = "Money Plus Banking Ltd"
APP_VERSION = "ULTIMATE MAX 2026"
DEMO_MODE = True
APP_TAGLINE = "Smart money management, built for your demo."
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(APP_DIR, "moneyplus_users.json")
PROFILE_DIR = os.path.join(APP_DIR, "profile_pics")
os.makedirs(PROFILE_DIR, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


# =========================================================
# DATA / SECURITY HELPERS
# =========================================================

def hash_value(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def verify_value(value, stored):
    if not stored:
        return False
    # Supports both the upgraded SHA-256 format and legacy plain values.
    return secrets.compare_digest(hash_value(value), stored) or \
           secrets.compare_digest(str(value), str(stored))


def prepare_user(user):
    user.setdefault("balance", 0)
    user.setdefault("wealth_balance", 0)
    user.setdefault("transactions", [])
    user.setdefault("profile_pic", "")
    user.setdefault("welcome_claimed", False)
    user.setdefault("money_added_once", False)
    user.setdefault("referrals", 0)
    user.setdefault("referral_earnings", 0)
    user.setdefault("referral_code", "")
    user.setdefault("referred_by", "")
    user.setdefault("savings", [])
    user.setdefault("dark_mode", False)
    user.setdefault("daily_reward", {})
    user.setdefault("notifications", [])
    user.setdefault("preferences", {
        "notifications": True,
        "compact_mode": False
    })
    user.setdefault("profile", {
        "full_name": user.get("username", ""),
        "bio": ""
    })
    user.setdefault("card", {
        "purchased": False,
        "purchase_date": "",
        "arrival_date": "",
        "phone": user.get("phone", ""),
        "address": ""
    })

    if not user["referral_code"]:
        user["referral_code"] = (
            "MP"
            + "".join(c for c in user.get("username", "").upper() if c.isalnum())[:6]
            + str(uuid.uuid4().int % 1000).zfill(3)
        )


def load_users():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as file:
            users = json.load(file)
        for user in users.values():
            ensure_user_upgrades(user)
        return users
    except (OSError, json.JSONDecodeError):
        return {}


def save_users(users):
    temp = DB_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(users, file, indent=4)
    os.replace(temp, DB_FILE)


def get_current_user():
    username = session.get("username")
    if not username:
        return None
    users = load_users()
    user = users.get(username)
    if user:
        prepare_user(user)
    return user


def current_users_and_user():
    users = load_users()
    username = session.get("username")
    user = users.get(username) if username else None
    if user:
        prepare_user(user)
    return users, user


def login_required():
    return get_current_user()


def add_transaction(user, title, amount, kind, note="", reference=None):
    user.setdefault("transactions", [])
    ref = reference or generate_ref()
    user["transactions"].insert(0, {
        "id": uuid.uuid4().hex[:10].upper(),
        "ref": ref,
        "title": title,
        "amount": round(float(amount), 2),
        "type": kind,
        "note": note,
        "date": datetime.now().strftime("%d %b %Y, %I:%M %p")
    })
    user["transactions"] = user["transactions"][:100]


def split_money(amount):
    transactions = round(float(amount) / 2, 2)
    wealth = round(float(amount) - transactions, 2)
    return transactions, wealth


def generate_account_number(phone, users):
    digits = "".join(c for c in phone if c.isdigit())
    base = digits[-6:].zfill(6)
    while True:
        number = base + str(uuid.uuid4().int % 10000).zfill(4)
        if not any(u.get("account_number") == number for u in users.values()):
            return number


def money(value):
    return f"₦{float(value):,.2f}"


def migrate_legacy_credentials(users):
    changed = False
    for user in users.values():
        prepare_user(user)

        password = str(user.get("password", ""))
        if password and len(password) != 64:
            user["password"] = hash_value(password)
            changed = True

        pin = str(user.get("pin", ""))
        if pin and len(pin) != 64:
            user["pin"] = hash_value(pin)
            changed = True

    if changed:
        save_users(users)


def valid_pin(user, pin):
    return verify_value(pin, user.get("pin", ""))


def validate_amount(raw, minimum=0):
    try:
        amount = round(float(raw), 2)
    except (TypeError, ValueError):
        return None
    if amount <= minimum:
        return None
    return amount


def add_notification(user, title, message, kind="info"):
    user.setdefault("notifications", [])
    user["notifications"].insert(0, {
        "id": uuid.uuid4().hex[:10].upper(),
        "title": title,
        "message": message,
        "kind": kind,
        "read": False,
        "date": datetime.now().strftime("%d %b %Y, %I:%M %p")
    })
    user["notifications"] = user["notifications"][:50]


def mask_phone(phone):
    phone = str(phone or "")
    if len(phone) <= 4:
        return phone
    return "*" * max(0, len(phone) - 4) + phone[-4:]



def ultimate_account_stats(user):
    """Dashboard-ready account metrics."""
    txs = user.get("transactions", []) or []
    incoming_types = {"credit", "deposit", "received", "income"}
    outgoing_types = {"debit", "transfer", "purchase", "withdrawal"}
    incoming = sum(float(t.get("amount", 0) or 0) for t in txs
                   if t.get("type") in incoming_types)
    outgoing = sum(float(t.get("amount", 0) or 0) for t in txs
                   if t.get("type") in outgoing_types)
    return {
        "balance": round(float(user.get("balance", 0) or 0), 2),
        "wealth": round(float(user.get("wealth_balance", 0) or 0), 2),
        "savings": round(float(user.get("savings", 0) or 0), 2),
        "incoming": round(incoming, 2),
        "outgoing": round(outgoing, 2),
        "transactions": len(txs),
        "referrals": len(user.get("referrals", []) or []),
    }

def transaction_stats(user):
    transactions = user.get("transactions", [])
    income = sum(float(x.get("amount", 0)) for x in transactions if x.get("type") == "credit")
    outgoing = sum(float(x.get("amount", 0)) for x in transactions if x.get("type") == "debit")
    recent = transactions[:10]
    recent_income = sum(float(x.get("amount", 0)) for x in recent if x.get("type") == "credit")
    recent_outgoing = sum(float(x.get("amount", 0)) for x in recent if x.get("type") == "debit")
    return {
        "income": income,
        "outgoing": outgoing,
        "recent_income": recent_income,
        "recent_outgoing": recent_outgoing,
        "count": len(transactions)
    }


def generate_ref():
    return "MP-" + datetime.now().strftime("%y%m%d") + "-" + secrets.token_hex(4).upper()


def ensure_user_upgrades(user):
    prepare_user(user)
    user.setdefault("notifications", [])
    user.setdefault("preferences", {
        "notifications": True,
        "compact_mode": False
    })
    user.setdefault("profile", {
        "full_name": user.get("username", ""),
        "bio": ""
    })



# =========================================================
# COMMON HTML
# =========================================================

BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<meta name="theme-color" content="#00a86b">
<title>{{ title }} · Money Plus</title>
<style>
*{box-sizing:border-box}
:root{
 --g:#00a86b;--g2:#007e55;--g3:#dff8ed;
 --bg:#eef4f1;--app:#f7faf8;--card:#fff;
 --txt:#17221d;--muted:#6b7771;--line:#dfe9e4;
 --danger:#dc3d3d;--danger-soft:#fff0f0;
 --gold:#e1a900;--gold-soft:#fff7db;
 --shadow:0 12px 32px rgba(16,24,40,.08);
}
body.dark{
 --bg:#09100d;--app:#0e1814;--card:#16221c;
 --txt:#f1f7f4;--muted:#a8b5ae;--line:#293a32;
 --g3:#183b2e;--danger-soft:#3a1b1b;--gold-soft:#3a3218;
 --shadow:0 12px 32px rgba(0,0,0,.28);
}
html,body{min-height:100%;margin:0}
body{
 background:var(--bg);font-family:Inter,Arial,sans-serif;color:var(--txt);
 -webkit-font-smoothing:antialiased;
}
a{color:inherit}
button,input,select,textarea{font:inherit}
.app{
 width:100%;max-width:520px;min-height:100vh;margin:auto;
 background:var(--app);padding-bottom:88px;box-shadow:0 0 40px rgba(0,0,0,.06);
}
.topbar{
 color:#fff;padding:18px 17px 21px;border-radius:0 0 28px 28px;
 background:linear-gradient(135deg,#0b3d2e 0%,#00a86b 52%,#063426 100%);
 box-shadow:0 10px 28px rgba(11,87,208,.18);
}
.topline{display:flex;align-items:center;justify-content:space-between;gap:12px}
.top-actions{display:flex;align-items:center;gap:9px}
.logo{font-size:21px;font-weight:900;letter-spacing:-.4px}
.user-name{font-size:12px;opacity:.86;margin-top:4px}
.menu-btn{
 width:42px;height:42px;border:0;border-radius:13px;background:rgba(255,255,255,.15);
 color:#fff;cursor:pointer;font-size:22px
}
.mini-profile{
 width:42px;height:42px;border-radius:50%;object-fit:cover;background:#fff;
 border:2px solid rgba(255,255,255,.55)
}
.assets-label{font-size:12px;opacity:.85;margin-top:23px}
.total-assets{font-size:32px;font-weight:900;letter-spacing:-.8px;margin-top:3px}
.asset-row{display:flex;gap:10px;margin-top:15px}
.asset{
 flex:1;background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.1);
 border-radius:15px;padding:12px
}
.asset small{display:block;opacity:.8;margin-bottom:5px;font-size:11px}
.asset b{font-size:14px}
.content{padding:16px}.compact .card{padding:13px;margin-bottom:10px}.compact .transaction{padding:10px 0}
.card{
 background:var(--card);border:1px solid var(--line);border-radius:20px;
 padding:17px;margin-bottom:15px;box-shadow:var(--shadow)
}
.card h2,.card h3{margin:0 0 8px}
.card p{line-height:1.5}
.hero-card{
 padding:20px;color:#fff;border:0;
 background:linear-gradient(135deg,#0b3d2e,#00a86b 58%,#007e55)
}
.hero-card .eyebrow{opacity:.8;font-size:12px}
.hero-card .hero-number{font-size:29px;font-weight:900;margin-top:7px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.action{
 display:flex;align-items:center;gap:10px;text-decoration:none;
 background:var(--g3);border:1px solid var(--line);border-radius:15px;
 padding:13px;font-weight:800;font-size:13px
}
.action-icon{
 width:34px;height:34px;border-radius:11px;background:var(--card);
 display:flex;align-items:center;justify-content:center;font-size:17px
}
.btn{
 display:block;width:100%;border:0;border-radius:13px;
 background:linear-gradient(135deg,#00a86b,#007e55);color:#fff;
 padding:14px;font-size:15px;font-weight:800;text-decoration:none;
 text-align:center;cursor:pointer;margin-top:10px;box-shadow:0 6px 15px rgba(11,87,208,.16)
}
.btn.secondary{background:var(--g3);color:var(--g2);box-shadow:none}
.btn.danger{background:var(--danger);box-shadow:none}
.btn.gold{background:#C98A00;color:#fff}
.btn.small{padding:10px;font-size:13px}
.btn:disabled{opacity:.55;cursor:not-allowed}
input,select,textarea{
 width:100%;padding:13px;border:1px solid var(--line);background:var(--card);
 color:var(--txt);border-radius:12px;margin-top:7px;margin-bottom:13px;
 font-size:15px;outline:none
}
input:focus,select:focus,textarea:focus{border-color:var(--g);box-shadow:0 0 0 3px rgba(11,87,208,.10)}
textarea{min-height:95px;resize:vertical}
label{font-size:12px;font-weight:800}
.msg{
 position:fixed;top:14px;left:50%;transform:translateX(-50%);
 width:calc(100% - 30px);max-width:490px;padding:13px 15px;border-radius:13px;
 color:#fff;background:#17221d;z-index:9999;box-shadow:0 7px 25px rgba(0,0,0,.2);
 font-size:13px;font-weight:700
}
.msg.success{background:#00a86b}.msg.error{background:#dc3d3d}
.transaction{
 display:flex;justify-content:space-between;align-items:center;gap:12px;
 padding:14px 0;border-bottom:1px solid var(--line)
}
.transaction:last-child{border-bottom:0}
.tx-title{font-weight:800;font-size:14px}.tx-date{font-size:11px;color:var(--muted);margin-top:3px}
.credit{color:#00a86b;font-weight:900}.debit{color:#d93636;font-weight:900}
.bottom-nav{
 position:fixed;bottom:0;left:50%;transform:translateX(-50%);
 width:100%;max-width:520px;height:72px;background:var(--card);
 border-top:1px solid var(--line);display:flex;z-index:500;
 padding-bottom:env(safe-area-inset-bottom)
}
.nav-item{
 flex:1;text-align:center;text-decoration:none;color:var(--muted);
 padding-top:9px;font-size:10px;font-weight:700
}
.nav-item span{display:block;font-size:20px;margin-bottom:3px}
.nav-item.active{color:var(--g)}
.reward{font-size:30px;font-weight:900;color:var(--g)}
.center{text-align:center}
.warning{
 background:var(--gold-soft);color:#765b00;padding:12px;border-radius:13px;
 font-size:13px;margin-bottom:12px
}
body.dark .warning{color:#f4dc78}
.avatar{
 width:105px;height:105px;border-radius:50%;object-fit:cover;display:block;
 margin:0 auto 15px;border:4px solid var(--g3)
}
.menu{
 display:flex;align-items:center;justify-content:space-between;
 text-decoration:none;color:var(--txt);padding:16px 4px;
 border-bottom:1px solid var(--line);font-weight:700
}
.menu:last-child{border-bottom:0}
.save-box{background:var(--g3);border-radius:15px;padding:15px}
.sidebar-overlay{
 display:none;position:fixed;inset:0;background:rgba(0,0,0,.48);z-index:700
}
.sidebar{
 position:fixed;top:0;left:-320px;width:300px;height:100vh;
 background:var(--card);z-index:701;padding:20px 16px;
 box-shadow:8px 0 35px rgba(0,0,0,.18);transition:left .25s
}
.sidebar.open{left:0}.sidebar-overlay.open{display:block}
.side-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:22px}
.side-title{font-size:22px;font-weight:900;color:var(--g)}
.close-side{
 border:0;background:var(--g3);font-size:22px;color:var(--txt);
 width:38px;height:38px;border-radius:12px;cursor:pointer
}
.side-link{
 display:flex;gap:13px;align-items:center;padding:14px 13px;border-radius:13px;
 color:var(--txt);text-decoration:none;margin-bottom:6px;font-weight:700
}
.side-link:hover{background:var(--g3)}
.side-divider{height:1px;background:var(--line);margin:15px 0}
.update{padding:13px 0;border-bottom:1px solid var(--line)}
.update:last-child{border-bottom:0}
.daily-task{
 background:linear-gradient(135deg,var(--g3),rgba(11,87,208,.08));
 border:1px solid var(--line);border-radius:16px;padding:16px
}
.card-art{display:block;text-decoration:none;color:#fff}
.money-card{
 height:210px;border-radius:23px;padding:22px;
 background:linear-gradient(135deg,#0b3d2e,#00a86b 55%,#063426);
 box-shadow:0 15px 30px rgba(0,0,0,.20);position:relative;overflow:hidden
}
.money-card:after{
 content:"";position:absolute;width:180px;height:180px;border:35px solid rgba(255,255,255,.08);
 border-radius:50%;right:-65px;top:-70px
}
.card-brand{font-size:21px;font-weight:900}
.chip{width:45px;height:32px;border-radius:7px;background:linear-gradient(135deg,#eee,#999);margin-top:28px}
.card-number{font-size:17px;letter-spacing:2px;margin-top:18px}
.card-bottom{display:flex;justify-content:space-between;margin-top:15px;font-size:12px}
.stat-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.stat{padding:12px;border-radius:14px;background:var(--g3)}
.stat small{display:block;color:var(--muted);font-size:10px}
.stat b{display:block;margin-top:4px;font-size:14px}
.badge{display:inline-block;padding:5px 9px;border-radius:999px;background:var(--g3);color:var(--g2);font-size:11px;font-weight:800}
.progress{height:9px;background:var(--line);border-radius:99px;overflow:hidden}
.progress > div{height:100%;background:var(--g);border-radius:99px}
.empty{padding:18px;text-align:center;color:var(--muted);font-size:13px}
.section-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
.section-head a{font-size:12px;color:var(--g2);font-weight:800;text-decoration:none}
.inline{display:flex;gap:8px;align-items:center}
.muted{color:var(--muted)}
.small{font-size:12px}
.account-number{font-size:20px;font-weight:900;letter-spacing:1px;margin-top:5px}
.shopping-grid{display:grid;gap:11px;margin-top:14px}
.shopping-card{
 display:flex;align-items:center;gap:12px;padding:14px;
 border:1px solid var(--line);border-radius:17px;background:var(--card);
 text-decoration:none;color:var(--txt);box-shadow:0 7px 20px rgba(16,24,40,.05);
 transition:transform .15s ease,box-shadow .15s ease,border-color .15s ease
}
.shopping-card:hover{transform:translateY(-1px);box-shadow:0 10px 25px rgba(16,24,40,.09);border-color:rgba(11,87,208,.35)}
.shopping-icon{
 width:48px;height:48px;flex:0 0 48px;border-radius:14px;background:var(--g3);
 display:flex;align-items:center;justify-content:center;font-size:25px
}
.shopping-info{min-width:0;flex:1}
.shopping-name{font-size:16px;font-weight:900;margin-bottom:5px}
.shopping-info .badge{font-size:9px;padding:4px 7px}
.shopping-info p{font-size:12px;line-height:1.4;color:var(--muted);margin:7px 0 0}
.shopping-arrow{font-size:22px;color:var(--g);font-weight:900}

@media(max-width:390px){
 .content{padding:12px}.card{padding:14px}.grid2{gap:8px}
 .action{padding:11px}.total-assets{font-size:28px}
}
</style>
</head>
<body class="{{ 'dark' if session.get('dark_mode') else '' }}">

{% with messages=get_flashed_messages(with_categories=true) %}
{% for category,message in messages %}
<div class="msg {{category}}">{{message}}</div>
{% endfor %}
{% endwith %}

{% if session.get("username") %}
<div class="sidebar-overlay" id="sideOverlay" onclick="closeSidebar()"></div>
<aside class="sidebar" id="sidebar">
  <div class="side-head">
    <div class="side-title">Money Plus</div>
    <button class="close-side" onclick="closeSidebar()">×</button>
  </div>
  <a class="side-link" href="{{url_for('dashboard')}}">🏠 <span>Home</span></a>
  <a class="side-link" href="{{url_for('transactions')}}">↔️ <span>Transactions</span></a>
  <a class="side-link" href="{{url_for('wealth')}}">◈ <span>Wealth</span></a>
  <a class="side-link" href="{{url_for('purchase_items')}}">🛍️ <span>Purchase Items</span></a>
  <a class="side-link" href="{{url_for('rewards')}}">🎁 <span>Rewards</span></a>
  <a class="side-link" href="{{url_for('notifications_page')}}">🔔 <span>Notifications{% if unread_count %} ({{unread_count}}){% endif %}</span></a>
  <a class="side-link" href="{{url_for('profile')}}">👤 <span>Profile & Card</span></a>
  <div class="side-divider"></div>
  <a class="side-link" href="{{url_for('settings')}}">⚙️ <span>Settings</span></a>
  <a class="side-link" href="{{url_for('account_management')}}">🧩 <span>Account Management</span></a>
  <a class="side-link" href="{{url_for('switch_account')}}">🔄 <span>Switch Account</span></a>
</aside>
{% endif %}

<div class="app {{ "compact" if session.get("compact_mode") else "" }}">
{{body|safe}}

{% if session.get("username") %}
<div class="bottom-nav">
  <a class="nav-item" href="{{url_for('dashboard')}}"><span>⌂</span>Home</a>
  <a class="nav-item" href="{{url_for('transactions')}}"><span>↔</span>Money</a>
  <a class="nav-item" href="{{url_for('wealth')}}"><span>◈</span>Wealth</a>
  <a class="nav-item" href="{{url_for('rewards')}}"><span>🎁</span>Rewards</a>
  <a class="nav-item" href="{{url_for('profile')}}"><span>👤</span>Me</a>
</div>
{% endif %}
</div>

<script>
function openSidebar(){
  const s=document.getElementById('sidebar'),o=document.getElementById('sideOverlay');
  if(s)s.classList.add('open'); if(o)o.classList.add('open');
}
function closeSidebar(){
  const s=document.getElementById('sidebar'),o=document.getElementById('sideOverlay');
  if(s)s.classList.remove('open'); if(o)o.classList.remove('open');
}
setTimeout(function(){
  document.querySelectorAll(".msg").forEach(function(m){
    m.style.opacity="0";
    m.style.transition="opacity .3s";
    setTimeout(function(){m.remove()},350);
  });
},3200);
</script>
<div style="text-align:center;padding:18px 0 90px;opacity:.55;font-size:12px;">Money Plus Banking Ltd · ULTIMATE MAX 2026 · DEMO</div></body>
</html>
"""


@app.context_processor
def inject_ui_data():
    user = get_current_user()
    unread = 0
    if user:
        unread = sum(1 for n in user.get("notifications", []) if not n.get("read"))
    return {"unread_count": unread}


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        pin = request.form.get("pin", "")
        referral = request.form.get("referral", "").strip().upper()

        if not all([username, email, phone, password, pin]):
            flash("Please fill all fields.", "error")
            return redirect(url_for("register"))

        if len(pin) != 4 or not pin.isdigit():
            flash("Payment PIN must be exactly 4 digits.", "error")
            return redirect(url_for("register"))

        users = load_users()
        migrate_legacy_credentials(users)

        if username in users:
            flash("Username already exists.", "error")
            return redirect(url_for("register"))

        if any(existing.get("email", "").lower() == email for existing in users.values()):
            flash("Email already exists.", "error")
            return redirect(url_for("register"))

        if any(existing.get("phone", "").strip() == phone for existing in users.values()):
            flash("Phone number already exists.", "error")
            return redirect(url_for("register"))

        account_number = generate_account_number(phone, users)
        referral_code = (
            "MP"
            + "".join(c for c in username.upper() if c.isalnum())[:6]
            + str(uuid.uuid4().int % 1000).zfill(3)
        )

        user = {
            "username": username,
            "email": email,
            "phone": phone,
            "password": hash_value(password),
            "pin": hash_value(pin),
            "account_number": account_number,
            "balance": 0,
            "wealth_balance": 0,
            "transactions": [],
            "profile_pic": "",
            "welcome_claimed": False,
            "money_added_once": False,
            "referrals": 0,
            "referral_earnings": 0,
            "referral_code": referral_code,
            "referred_by": referral,
            "savings": [],
            "dark_mode": False,
            "daily_reward": {},
            "card": {
                "purchased": False,
                "purchase_date": "",
                "arrival_date": "",
                "phone": phone,
                "address": ""
            }
        }

        users[username] = user

        if referral:
            for other in users.values():
                if other is user:
                    continue
                if other.get("referral_code") == referral:
                    other["referrals"] = other.get("referrals", 0) + 1
                    other["referral_earnings"] = other.get("referral_earnings", 0) + 25000
                    other["wealth_balance"] = other.get("wealth_balance", 0) + 500
                    add_transaction(other, "Referral Reward", 25000, "credit")
                    break

        add_notification(user, "Welcome to Money Plus", "Your account has been created successfully.", "welcome")
        save_users(users)

        # Log the newly created user in immediately. This prevents the Create Account
        # button from appearing to send the user back to the Login page.
        session.clear()
        session["username"] = username
        session["dark_mode"] = False
        session["compact_mode"] = False
        flash("Account created successfully. Welcome to Money Plus!", "success")
        return redirect(url_for("dashboard"))

    body = """
    <div class="content">
      <div class="card center">
        <span class="badge">CREATE ACCOUNT</span>
        <h2 style="margin-top:10px">Welcome to Money Plus</h2>
        <p class="muted">Create your secure banking account.</p>
      </div>
      <div class="card">
        <form method="POST">
          <label>Username</label>
          <input name="username" autocomplete="username" required>

          <label>Email</label>
          <input type="email" name="email" autocomplete="email" required>

          <label>Phone Number</label>
          <input name="phone" inputmode="tel" required>

          <label>Password</label>
          <input type="password" name="password" autocomplete="new-password" minlength="4" required>

          <label>4-Digit Payment PIN</label>
          <input type="password" name="pin" inputmode="numeric" maxlength="4" minlength="4" required>

          <label>Referral Code <span class="muted">(optional)</span></label>
          <input name="referral" placeholder="e.g. MPDEAN123">

          <button class="btn">Create Account</button>
        </form>
        <a class="btn secondary" href="{{url_for('login')}}">Already have an account? Login</a>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Register", body=body)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    # A dedicated endpoint prevents signup links from ever resolving to the login endpoint.
    # POST is forwarded to the same registration handler so validation and account creation
    # stay identical for /signup and /register.
    return register()


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        users = load_users()
        migrate_legacy_credentials(users)
        user = users.get(username)

        if not user or not verify_value(password, user.get("password", "")):
            flash("Invalid username or password.", "error")
            return redirect(url_for("login"))

        prepare_user(user)
        users[username] = user
        save_users(users)

        session.clear()
        session["username"] = username
        session["dark_mode"] = bool(user.get("dark_mode", False))
        session["compact_mode"] = bool(user.get("preferences", {}).get("compact_mode", False))
        flash("Login successful.", "success")
        return redirect(url_for("dashboard"))

    body = """
    <div class="content">
      <div class="card center" style="margin-top:25px">
        <span class="badge">MONEY PLUS</span>
        <h2 style="margin-top:12px">Welcome Back</h2>
        <p class="muted">Sign in to manage your money.</p>
      </div>
      <div class="card">
        <form method="POST">
          <label>Username</label>
          <input name="username" autocomplete="username" required>
          <label>Password</label>
          <input type="password" name="password" autocomplete="current-password" required>
          <button class="btn">Login Securely</button>
        </form>
        <a class="btn secondary" href="/signup">Sign Up / Create Account</a>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Login", body=body)


# =========================================================
# HOME / DASHBOARD
# =========================================================

@app.route("/")
@app.route("/dashboard")
def dashboard():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    users = load_users()
    user = users[session["username"]]
    prepare_user(user)
    save_users(users)

    total_assets = float(user.get("balance", 0)) + float(user.get("wealth_balance", 0))
    tx_count = len(user.get("transactions", []))
    savings_total = sum(float(s.get("amount", 0)) for s in user.get("savings", []))
    picture = url_for("profile_picture", filename=user["profile_pic"]) if user.get("profile_pic") else ""

    stats = transaction_stats(user)

    body = render_template_string("""
    <div class="topbar">
      <div class="topline">
        <div>
          <div class="logo">Money Plus</div>
          <div class="user-name">Welcome back, {{user.username}}</div>
        </div>
        <div class="top-actions">
          <a class="menu-btn" href="{{url_for('notifications_page')}}" style="display:flex;align-items:center;justify-content:center;text-decoration:none">🔔</a>
          <button class="menu-btn" onclick="openSidebar()">☰</button>
          {% if picture %}
          <img class="mini-profile" src="{{picture}}">
          {% else %}
          <div class="mini-profile" style="display:flex;align-items:center;justify-content:center;color:#00a86b">👤</div>
          {% endif %}
        </div>
      </div>
      <div class="assets-label">TOTAL ASSETS</div>
      <div class="total-assets">₦{{"{:,.2f}".format(total_assets)}}</div>
      <div class="asset-row">
        <div class="asset"><small>Transactions</small><b>₦{{"{:,.2f}".format(user.balance|float)}}</b></div>
        <div class="asset"><small>Wealth</small><b>₦{{"{:,.2f}".format(user.wealth_balance|float)}}</b></div>
      </div>
    </div>

    <div class="content">
      <div class="card hero-card">
        <div class="eyebrow">MONEY PLUS ACCOUNT</div>
        <div class="hero-number">₦{{"{:,.2f}".format(total_assets)}}</div>
        <div style="opacity:.82;font-size:12px;margin-top:5px">
          Account • {{user.account_number}}
        </div>
      </div>

      <div class="card">
        <div class="section-head">
          <h3>Quick Actions</h3>
          <a href="{{url_for('transactions')}}">View all</a>
        </div>
        <div class="grid2">
          <a class="action" href="{{url_for('transfer')}}"><span class="action-icon">↗</span>Transfer</a>
          <a class="action" href="{{url_for('purchase_items')}}"><span class="action-icon">🛍️</span>Purchase Items</a>
          <a class="action" href="{{url_for('wealth')}}"><span class="action-icon">◈</span>Wealth</a>
          <a class="action" href="{{url_for('rewards')}}"><span class="action-icon">🎁</span>Rewards</a>
        </div>
      </div>

      <div class="card">
        <div class="section-head"><h3>Account Snapshot</h3><span class="badge">ACTIVE</span></div>
        <div class="stat-grid">
          <div class="stat"><small>Transactions</small><b>₦{{"{:,.0f}".format(user.balance|float)}}</b></div>
          <div class="stat"><small>Wealth</small><b>₦{{"{:,.0f}".format(user.wealth_balance|float)}}</b></div>
          <div class="stat"><small>Saved</small><b>₦{{"{:,.0f}".format(savings_total)}}</b></div>
        </div>
      </div>

      <div class="card">
        <div class="section-head">
          <h3>Money Analytics</h3>
          <a href="{{url_for('transaction_history')}}">History</a>
        </div>
        <div class="stat-grid">
          <div class="stat"><small>Total In</small><b class="credit">₦{{"{:,.0f}".format(stats.income)}}</b></div>
          <div class="stat"><small>Total Out</small><b class="debit">₦{{"{:,.0f}".format(stats.outgoing)}}</b></div>
          <div class="stat"><small>Activity</small><b>{{stats.count}}</b></div>
        </div>
        <div style="margin-top:14px">
          <div class="small muted">Recent inflow vs outflow</div>
          {% set total = stats.recent_income + stats.recent_outgoing %}
          {% if total > 0 %}
          <div class="progress" style="margin-top:8px">
            <div style="width:{{(stats.recent_income / total * 100)|round(0)}}%"></div>
          </div>
          {% else %}
          <div class="progress" style="margin-top:8px"><div style="width:0%"></div></div>
          {% endif %}
        </div>
      </div>

      <div class="card">
        <div class="section-head">
          <h3>Recent Activity</h3>
          <a href="{{url_for('transaction_history')}}">History</a>
        </div>
        {% for item in user.transactions[:5] %}
        <div class="transaction">
          <div>
            <div class="tx-title">{{item.title}}</div>
            <div class="tx-date">{{item.date}}</div>
          </div>
          <div class="{{'credit' if item.type=='credit' else 'debit'}}">
            {{'+' if item.type=='credit' else '-'}}₦{{"{:,.2f}".format(item.amount|float)}}
          </div>
        </div>
        {% else %}
        <div class="empty">No transactions yet.</div>
        {% endfor %}
      </div>

      <div class="card info">
        <h3>Money Plus Updates</h3>
        <div class="update"><b>🎁 Daily Rewards</b><br><small>Complete daily tasks to earn Wealth rewards.</small></div>
        <div class="update"><b>💳 Money Plus Card</b><br><small>Manage your card from the Me section.</small></div>
        <div class="update"><b>🔐 Payment Security</b><br><small>Your payment PIN protects transfers and purchases.</small></div>
      </div>
    </div>
    """, user=user, total_assets=total_assets, tx_count=tx_count,
       savings_total=savings_total, picture=picture, stats=stats)

    return render_template_string(BASE_HTML, title="Home", body=body)


# =========================================================
# TRANSACTIONS
# =========================================================

@app.route("/transactions")
def transactions():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    body = render_template_string("""
    <div class="content">
      <div class="card hero-card">
        <div class="eyebrow">TRANSACTIONS BALANCE</div>
        <div class="hero-number">₦{{"{:,.2f}".format(user.balance|float)}}</div>
        <p style="opacity:.8">Use this balance for transfers and supported purchases.</p>
      </div>

      <div class="card">
        <h3>Money Services</h3>
        <a class="btn" href="{{url_for('transfer')}}">↗ Transfer Money</a>
        <a class="btn secondary" href="{{url_for('purchase_items')}}">🛍️ Purchase Items</a>
      </div>

      <div class="card">
        <div class="section-head">
          <h3>Transaction History</h3>
          <a href="{{url_for('transaction_history')}}">Open</a>
        </div>
        {% for item in user.transactions[:8] %}
        <div class="transaction">
          <div><div class="tx-title">{{item.title}}</div><div class="tx-date">{{item.date}}</div></div>
          <div class="{{'credit' if item.type=='credit' else 'debit'}}">
            {{'+' if item.type=='credit' else '-'}}₦{{"{:,.2f}".format(item.amount|float)}}
          </div>
        </div>
        {% else %}
        <div class="empty">No transactions yet.</div>
        {% endfor %}
      </div>
    </div>
    """, user=user)
    return render_template_string(BASE_HTML, title="Transactions", body=body)


@app.route("/transaction-history")
def transaction_history():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    query = request.args.get("q", "").strip().lower()
    tx_type = request.args.get("type", "all")

    filtered = []
    for item in user.get("transactions", []):
        haystack = " ".join([
            str(item.get("title", "")),
            str(item.get("note", "")),
            str(item.get("ref", "")),
            str(item.get("id", ""))
        ]).lower()

        if query and query not in haystack:
            continue
        if tx_type in ("credit", "debit") and item.get("type") != tx_type:
            continue
        filtered.append(item)

    body = render_template_string("""
    <div class="content">
      <div class="card">
        <div class="section-head">
          <div><h2>Transaction History</h2><small class="muted">{{filtered|length}} result(s)</small></div>
          <span class="badge">SEARCH</span>
        </div>
        <form method="GET">
          <label>Search</label>
          <input name="q" value="{{query}}" placeholder="Search title, note or reference">
          <label>Type</label>
          <select name="type">
            <option value="all" {%if tx_type=="all"%}selected{%endif%}>All transactions</option>
            <option value="credit" {%if tx_type=="credit"%}selected{%endif%}>Money in</option>
            <option value="debit" {%if tx_type=="debit"%}selected{%endif%}>Money out</option>
          </select>
          <button class="btn">Apply Filter</button>
        </form>
        <a class="btn secondary" href="{{url_for('export_transactions')}}">⬇ Export CSV</a>
      </div>

      <div class="card">
        {% for item in filtered %}
        <a href="{{url_for('transaction_detail', ref=item.ref or item.id)}}" style="text-decoration:none">
          <div class="transaction">
            <div>
              <div class="tx-title">{{item.title}}</div>
              <div class="tx-date">{{item.date}}</div>
              <small class="muted">{{item.ref or item.id}}{% if item.note %} · {{item.note}}{% endif %}</small>
            </div>
            <div class="{{'credit' if item.type=='credit' else 'debit'}}">
              {{'+' if item.type=='credit' else '-'}}₦{{"{:,.2f}".format(item.amount|float)}}
            </div>
          </div>
        </a>
        {% else %}
        <div class="empty">No matching transactions.</div>
        {% endfor %}
      </div>
    </div>
    """, user=user, filtered=filtered, query=query, tx_type=tx_type)

    return render_template_string(BASE_HTML, title="History", body=body)


@app.route("/transaction/<ref>")
def transaction_detail(ref):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    item = next(
        (x for x in user.get("transactions", [])
         if x.get("ref") == ref or x.get("id") == ref),
        None
    )
    if not item:
        flash("Transaction not found.", "error")
        return redirect(url_for("transaction_history"))

    body = render_template_string("""
    <div class="content">
      <div class="card center">
        <span class="badge">{{"MONEY IN" if item.type=="credit" else "MONEY OUT"}}</span>
        <div class="{{'credit' if item.type=='credit' else 'debit'}}" style="font-size:35px;font-weight:900;margin-top:12px">
          {{'+' if item.type=='credit' else '-'}}₦{{"{:,.2f}".format(item.amount|float)}}
        </div>
        <h2>{{item.title}}</h2>
        <p class="muted">{{item.date}}</p>
      </div>
      <div class="card">
        <div class="menu"><span>Reference</span><b>{{item.ref or item.id}}</b></div>
        <div class="menu"><span>Transaction ID</span><b>{{item.id}}</b></div>
        <div class="menu"><span>Status</span><b class="credit">Successful</b></div>
        {% if item.note %}<div class="menu"><span>Note</span><b>{{item.note}}</b></div>{% endif %}
      </div>
      <a class="btn secondary" href="{{url_for('transaction_history')}}">← Back to History</a>
    </div>
    """, item=item)
    return render_template_string(BASE_HTML, title="Receipt", body=body)


@app.route("/export-transactions")
def export_transactions():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Reference", "Date", "Title", "Type", "Amount", "Note"])

    for item in user.get("transactions", []):
        writer.writerow([
            item.get("ref", item.get("id", "")),
            item.get("date", ""),
            item.get("title", ""),
            item.get("type", ""),
            item.get("amount", 0),
            item.get("note", "")
        ])

    response = app.response_class(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=moneyplus_transactions.csv"
        }
    )
    return response


# =========================================================
# TRANSFER
# =========================================================

@app.route("/transfer", methods=["GET", "POST"])
def transfer():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":
        account_number = request.form.get("account_number", "").strip()
        pin = request.form.get("payment_pin", "")
        amount = validate_amount(request.form.get("amount", 0), minimum=0)

        if not valid_pin(user, pin):
            flash("Incorrect payment PIN.", "error")
            return redirect(url_for("transfer"))
        if amount is None:
            flash("Enter a valid transfer amount.", "error")
            return redirect(url_for("transfer"))

        users = load_users()
        sender_username = session["username"]
        sender = users[sender_username]

        if amount > float(sender.get("balance", 0)):
            flash("Insufficient Transactions balance.", "error")
            return redirect(url_for("transfer"))

        recipient_username = next(
            (u for u, o in users.items() if o.get("account_number") == account_number),
            None
        )

        if recipient_username is None:
            flash("Recipient is not a Money Plus user.", "error")
            return redirect(url_for("transfer"))
        if recipient_username == sender_username:
            flash("You cannot send money to yourself.", "error")
            return redirect(url_for("transfer"))

        recipient = users[recipient_username]
        sender["balance"] = round(float(sender.get("balance", 0)) - amount, 2)
        recipient["balance"] = round(float(recipient.get("balance", 0)) + amount, 2)

        add_transaction(sender, "Transfer to " + recipient_username, amount, "debit")
        add_transaction(recipient, "Transfer from " + sender_username, amount, "credit")
        add_notification(sender, "Transfer Successful", f"₦{amount:,.2f} was sent to {recipient_username}.", "money")
        add_notification(recipient, "Money Received", f"₦{amount:,.2f} was received from {sender_username}.", "money")
        save_users(users)

        flash("Transfer successful.", "success")
        return redirect(url_for("transactions"))

    body = render_template_string("""
    <div class="content">
      <div class="card">
        <h2>Transfer Money</h2>
        <p class="muted">Send money directly to another Money Plus account.</p>
        <div class="save-box"><b>Available</b><div class="reward">₦{{"{:,.2f}".format(user.balance|float)}}</div></div>
      </div>
      <div class="card">
        <form method="POST">
          <label>Recipient Account Number</label>
          <input name="account_number" inputmode="numeric" maxlength="10" required>
          <label>Amount (₦)</label>
          <input type="number" name="amount" min="0.01" step="0.01" required>
          <label>Payment PIN</label>
          <input type="password" name="payment_pin" inputmode="numeric" maxlength="4" required>
          <button class="btn">Confirm Transfer</button>
        </form>
      </div>
    </div>
    """, user=user)
    return render_template_string(BASE_HTML, title="Transfer", body=body)


# =========================================================
# PURCHASE ITEMS
# =========================================================

SHOPPING_SITES = [
    {
        "name": "AliExpress",
        "icon": "🛒",
        "description": "Shop electronics, fashion, accessories, home items and more.",
        "url": "https://www.aliexpress.com/",
        "tag": "GLOBAL SHOPPING",
    },
    {
        "name": "Jumia",
        "icon": "🟠",
        "description": "Shop phones, electronics, fashion, home products and everyday items.",
        "url": "https://www.jumia.com.ng/",
        "tag": "NIGERIA",
    },
    {
        "name": "Konga",
        "icon": "🛍️",
        "description": "Browse electronics, phones, appliances, fashion and other products.",
        "url": "https://www.konga.com/",
        "tag": "NIGERIA",
    },
    {
        "name": "Amazon",
        "icon": "📦",
        "description": "Explore a huge range of products from the Amazon marketplace.",
        "url": "https://www.amazon.com/",
        "tag": "GLOBAL SHOPPING",
    },
    {
        "name": "Temu",
        "icon": "🛒",
        "description": "Browse a wide selection of everyday products and accessories.",
        "url": "https://www.temu.com/",
        "tag": "GLOBAL SHOPPING",
    },
    {
        "name": "SHEIN",
        "icon": "👕",
        "description": "Browse clothing, accessories, beauty and lifestyle products.",
        "url": "https://www.shein.com/",
        "tag": "FASHION",
    },
]


@app.route("/purchase-items")
def purchase_items():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    body = render_template_string("""
    <div class="content">
      <div class="card hero-card">
        <div class="eyebrow">SHOPPING HUB</div>
        <div class="hero-number">Purchase Items</div>
        <p style="opacity:.86">
          Choose a shopping platform below. Tapping a platform opens its official website
          in a new browser tab so you can browse and purchase items there.
        </p>
      </div>

      <div class="card">
        <div class="section-head">
          <h3>Choose a Shopping App</h3>
          <span class="badge">{{sites|length}} STORES</span>
        </div>
        <p class="muted small">Money Plus does not collect your shopping-site password, PIN or card details.</p>

        <div class="shopping-grid">
          {% for site in sites %}
          <a class="shopping-card" href="{{site.url}}" target="_blank" rel="noopener noreferrer">
            <div class="shopping-icon">{{site.icon}}</div>
            <div class="shopping-info">
              <div class="shopping-name">{{site.name}}</div>
              <span class="badge">{{site.tag}}</span>
              <p>{{site.description}}</p>
            </div>
            <div class="shopping-arrow">↗</div>
          </a>
          {% endfor %}
        </div>
      </div>

      <div class="card">
        <h3>Safe Shopping</h3>
        <p class="muted">
          Check the website address before signing in or paying. Complete payments directly
          on the shopping platform you selected.
        </p>
      </div>
    </div>
    """, user=user, sites=SHOPPING_SITES)

    return render_template_string(BASE_HTML, title="Purchase Items", body=body)


# =========================================================
# WEALTH
# =========================================================

@app.route("/wealth")
def wealth():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    total_saved = sum(float(item.get("amount", 0)) for item in user.get("savings", []))
    estimated_earnings = sum(
        float(item.get("monthly_earning", 0))
        for item in user.get("savings", [])
    )

    body = render_template_string("""
    <div class="content">
      <div class="card hero-card">
        <div class="eyebrow">MONEY PLUS WEALTH</div>
        <div class="hero-number">₦{{"{:,.2f}".format(user.wealth_balance|float)}}</div>
        <p style="opacity:.82">Your Wealth balance receives rewards and savings earnings.</p>
      </div>

      <div class="card">
        <div class="stat-grid">
          <div class="stat"><small>Total Saved</small><b>₦{{"{:,.0f}".format(total_saved)}}</b></div>
          <div class="stat"><small>Projected</small><b>₦{{"{:,.0f}".format(estimated_earnings)}}</b></div>
          <div class="stat"><small>Rate</small><b>50%</b></div>
        </div>
      </div>

      <div class="card">
        <h3>Wealth Services</h3>
        <a class="btn" href="{{url_for('wealth_transfer')}}">↗ Transfer From Wealth</a>
        <a class="btn secondary" href="{{url_for('save_earn')}}">◈ Save & Earn</a>
      </div>

      <div class="card">
        <div class="section-head"><h3>Your Savings</h3><span class="badge">{{user.savings|length}} SAVINGS</span></div>
        {% for s in user.savings %}
        <div class="transaction">
          <div><b>₦{{"{:,.2f}".format(s.amount|float)}}</b><div class="tx-date">{{s.date}}</div></div>
          <div class="credit">+₦{{"{:,.2f}".format(s.monthly_earning|float)}}</div>
        </div>
        {% else %}
        <div class="empty">No savings yet.</div>
        {% endfor %}
      </div>
    </div>
    """, user=user, total_saved=total_saved, estimated_earnings=estimated_earnings)
    return render_template_string(BASE_HTML, title="Wealth", body=body)


# =========================================================
# WEALTH TRANSFER
# =========================================================

@app.route("/wealth-transfer", methods=["GET", "POST"])
def wealth_transfer():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":
        account_number = request.form.get("account_number", "").strip()
        amount = validate_amount(request.form.get("amount", 0), minimum=0)
        pin = request.form.get("payment_pin", "")

        if not valid_pin(user, pin):
            flash("Incorrect payment PIN.", "error")
            return redirect(url_for("wealth_transfer"))
        if amount is None:
            flash("Enter a valid amount.", "error")
            return redirect(url_for("wealth_transfer"))

        users = load_users()
        sender_username = session["username"]
        sender = users[sender_username]

        if amount > float(sender.get("wealth_balance", 0)):
            flash("Insufficient Wealth balance.", "error")
            return redirect(url_for("wealth_transfer"))

        recipient_username = next(
            (u for u, o in users.items() if o.get("account_number") == account_number),
            None
        )
        if recipient_username is None:
            flash("The recipient must use Money Plus.", "error")
            return redirect(url_for("wealth_transfer"))
        if recipient_username == sender_username:
            flash("You cannot transfer to yourself.", "error")
            return redirect(url_for("wealth_transfer"))

        recipient = users[recipient_username]
        sender["wealth_balance"] = round(float(sender.get("wealth_balance", 0)) - amount, 2)
        recipient["wealth_balance"] = round(float(recipient.get("wealth_balance", 0)) + amount, 2)

        add_transaction(sender, "Wealth Transfer", amount, "debit", recipient_username)
        add_transaction(recipient, "Wealth Transfer Received", amount, "credit", sender_username)
        add_notification(sender, "Wealth Transfer", f"₦{amount:,.2f} was sent from Wealth to {recipient_username}.", "money")
        add_notification(recipient, "Wealth Received", f"₦{amount:,.2f} was received from {sender_username}.", "money")
        save_users(users)

        flash("Wealth transfer successful.", "success")
        return redirect(url_for("wealth"))

    body = render_template_string("""
    <div class="content">
      <div class="card">
        <h2>Wealth Transfer</h2>
        <p class="muted">Send Wealth funds to another Money Plus account.</p>
        <div class="save-box"><b>Available Wealth</b><div class="reward">₦{{"{:,.2f}".format(user.wealth_balance|float)}}</div></div>
      </div>
      <div class="card">
        <form method="POST">
          <label>Recipient Account Number</label>
          <input name="account_number" inputmode="numeric" maxlength="10" required>
          <label>Amount (₦)</label>
          <input type="number" name="amount" min="0.01" step="0.01" required>
          <label>Payment PIN</label>
          <input type="password" name="payment_pin" inputmode="numeric" maxlength="4" required>
          <button class="btn">Send From Wealth</button>
        </form>
      </div>
    </div>
    """, user=user)
    return render_template_string(BASE_HTML, title="Wealth Transfer", body=body)


# =========================================================
# SAVE & EARN
# =========================================================

@app.route("/save-earn", methods=["GET", "POST"])
def save_earn():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":
        amount = validate_amount(request.form.get("amount", 0), minimum=49.99)
        pin = request.form.get("payment_pin", "")

        if not valid_pin(user, pin):
            flash("Incorrect payment PIN.", "error")
            return redirect(url_for("save_earn"))
        if amount is None:
            flash("Save at least ₦50.", "error")
            return redirect(url_for("save_earn"))

        users = load_users()
        user = users[session["username"]]

        if amount > float(user.get("wealth_balance", 0)):
            flash("Insufficient Wealth balance.", "error")
            return redirect(url_for("save_earn"))

        user["wealth_balance"] = round(float(user.get("wealth_balance", 0)) - amount, 2)
        earning = round(amount * 0.50, 2)
        user.setdefault("savings", []).append({
            "amount": amount,
            "monthly_rate": 50,
            "monthly_earning": earning,
            "date": datetime.now().strftime("%d %b %Y, %I:%M %p"),
            "daily_task_date": datetime.now().strftime("%Y-%m-%d")
        })
        add_transaction(user, "Save & Earn", amount, "debit")
        save_users(users)

        flash("Money saved successfully.", "success")
        return redirect(url_for("wealth"))

    body = render_template_string("""
    <div class="content">
      <div class="card hero-card">
        <div class="eyebrow">SAVE & EARN</div>
        <div class="hero-number">50%</div>
        <p style="opacity:.82">Monthly rate shown by this app for saved funds.</p>
      </div>
      <div class="card">
        <p>Available Wealth: <b>₦{{"{:,.2f}".format(user.wealth_balance|float)}}</b></p>
        <form method="POST">
          <label>Amount (minimum ₦50)</label>
          <input type="number" name="amount" min="50" step="0.01" required>
          <label>Payment PIN</label>
          <input type="password" name="payment_pin" inputmode="numeric" maxlength="4" required>
          <button class="btn">Save Money</button>
        </form>
      </div>
      <div class="card">
        <h3>Your Savings</h3>
        {% for s in user.savings %}
        <div class="transaction">
          <div><b>₦{{"{:,.2f}".format(s.amount|float)}}</b><div class="tx-date">{{s.date}}</div></div>
          <div class="credit">+₦{{"{:,.2f}".format(s.monthly_earning|float)}}</div>
        </div>
        {% else %}<div class="empty">No savings yet.</div>{% endfor %}
      </div>
    </div>
    """, user=user)
    return render_template_string(BASE_HTML, title="Save & Earn", body=body)


# =========================================================
# REWARDS
# =========================================================

@app.route("/rewards", methods=["GET", "POST"])
def rewards():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    users = load_users()
    username = session["username"]
    user = users[username]
    prepare_user(user)

    today = datetime.now().strftime("%Y-%m-%d")
    task = user.get("daily_reward", {})
    if task.get("date") != today:
        task = {}

    if request.method == "POST":
        action = request.form.get("action")

        if action == "claim_welcome":
            if user.get("welcome_claimed"):
                flash("You already claimed the bonus.", "error")
            else:
                user["welcome_claimed"] = True
                user["wealth_balance"] = round(float(user.get("wealth_balance", 0)) + 100000, 2)
                add_transaction(user, "₦100,000 Welcome Reward", 100000, "credit")
                save_users(users)
                flash("₦100,000 added to Wealth.", "success")
            return redirect(url_for("rewards"))

        if action == "new_daily":
            kind, target, reward = random.choice([
                ("save", 500, 100), ("save", 1000, 200),
                ("save", 2000, 300), ("refer", 1, 150),
                ("refer", 2, 300), ("refer", 3, 500)
            ])
            user["daily_reward"] = {
                "date": today, "type": kind, "target": target,
                "reward": reward, "claimed": False
            }
            save_users(users)
            flash("A new Daily Reward task has been generated.", "success")
            return redirect(url_for("rewards"))

        if action == "claim_daily":
            task = user.get("daily_reward", {})
            done = False

            if task.get("date") == today and task.get("type") == "save":
                done = any(
                    float(s.get("amount", 0)) >= float(task.get("target", 0))
                    and s.get("daily_task_date") == today
                    for s in user.get("savings", [])
                )

            if task.get("date") == today and task.get("type") == "refer":
                done = user.get("referrals", 0) >= int(task.get("target", 0))

            if task and task.get("date") == today and not task.get("claimed") and done:
                reward = float(task["reward"])
                user["wealth_balance"] = round(float(user.get("wealth_balance", 0)) + reward, 2)
                task["claimed"] = True
                user["daily_reward"] = task
                add_transaction(user, "Daily Reward", reward, "credit")
                save_users(users)
                flash("Daily Reward claimed successfully.", "success")
            elif task.get("claimed"):
                flash("Today's Daily Reward has already been claimed.", "error")
            else:
                flash("Complete today's task before claiming the reward.", "error")

            return redirect(url_for("rewards"))

    share_text = "Join Money Plus Banking Ltd. Use my referral code: " + user["referral_code"]

    body = render_template_string("""
    <div class="content">
      <div class="card center">
        <span class="badge">REWARDS</span>
        {% if not user.welcome_claimed %}
        <div class="reward" style="margin-top:10px">₦100,000</div>
        <p>Welcome Bonus</p>
        <form method="POST">
          <input type="hidden" name="action" value="claim_welcome">
          <button class="btn">Claim ₦100,000 Bonus</button>
        </form>
        {% else %}
        <p class="muted">Welcome bonus already claimed.</p>
        {% endif %}
      </div>

      <div class="card">
        <div class="section-head"><h3>Daily Rewards</h3><span class="badge">DAILY</span></div>
        {% if task and task.date==today %}
        <div class="daily-task">
          <b>Today's task</b>
          {% if task.type=="save" %}
          <p>Save at least <b>₦{{"{:,.0f}".format(task.target|float)}}</b> today.</p>
          {% else %}
          <p>Refer at least <b>{{task.target}}</b> friend{% if task.target!=1 %}s{% endif %} today.</p>
          {% endif %}
          <p>Reward: <b>₦{{"{:,.0f}".format(task.reward|float)}}</b> to Wealth.</p>
          {% if task.claimed %}
          <button class="btn secondary" disabled>Reward Claimed</button>
          {% else %}
          <form method="POST">
            <input type="hidden" name="action" value="claim_daily">
            <button class="btn">Claim Daily Reward</button>
          </form>
          {% endif %}
        </div>
        {% else %}
        <div class="empty">No task generated for today yet.</div>
        {% endif %}
        <form method="POST">
          <input type="hidden" name="action" value="new_daily">
          <button class="btn secondary">🎁 Generate Daily Task</button>
        </form>
      </div>

      <div class="card">
        <h3>Refer & Earn</h3>
        <p class="muted">Invite people to join Money Plus using your referral code.</p>
        <div class="save-box center">
          <div class="small muted">YOUR CODE</div>
          <div style="font-size:22px;font-weight:900;margin-top:4px">{{user.referral_code}}</div>
        </div>
        <p>Reward: <b>₦25,000 per successful referral</b></p>
        <a class="btn" target="_blank"
           href="https://wa.me/?text={{share_text|urlencode}}">Share on WhatsApp</a>
        <div class="stat-grid" style="margin-top:12px">
          <div class="stat"><small>Referrals</small><b>{{user.referrals}}</b></div>
          <div class="stat"><small>Earnings</small><b>₦{{"{:,.0f}".format(user.referral_earnings|float)}}</b></div>
          <div class="stat"><small>Wealth</small><b>₦{{"{:,.0f}".format(user.wealth_balance|float)}}</b></div>
        </div>
      </div>
    </div>
    """, user=user, task=task, today=today, share_text=share_text)

    return render_template_string(BASE_HTML, title="Rewards", body=body)


# =========================================================
# PROFILE / CARD
# =========================================================

@app.route("/profile", methods=["GET", "POST"])
def profile():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    ensure_user_upgrades(user)

    if request.method == "POST":
        image = request.files.get("profile_pic")

        if not image or not image.filename:
            flash("Choose a picture first.", "error")
            return redirect(url_for("profile"))

        filename = secure_filename(image.filename)
        if "." not in filename or filename.rsplit(".", 1)[1].lower() not in ALLOWED_EXTENSIONS:
            flash("Only PNG, JPG, JPEG, GIF and WEBP images are allowed.", "error")
            return redirect(url_for("profile"))

        ext = filename.rsplit(".", 1)[1].lower()
        new_name = uuid.uuid4().hex + "." + ext
        image.save(os.path.join(PROFILE_DIR, new_name))

        users = load_users()
        username = session["username"]
        old = users[username].get("profile_pic", "")

        if old:
            old_path = os.path.join(PROFILE_DIR, old)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass

        users[username]["profile_pic"] = new_name
        save_users(users)
        flash("Profile picture updated.", "success")
        return redirect(url_for("profile"))

    picture = url_for("profile_picture", filename=user["profile_pic"]) if user.get("profile_pic") else ""
    card = user.get("card", {})

    body = render_template_string("""
    <div class="content">
      <div class="card center">
        <span class="badge">MY PROFILE</span>
        {% if picture %}
        <img class="avatar" src="{{picture}}">
        {% else %}
        <div class="avatar" style="display:flex;align-items:center;justify-content:center;background:var(--g3);font-size:45px">👤</div>
        {% endif %}
        <h2>{{user.profile.full_name if user.profile else user.username}}</h2>
        <p class="muted">{{user.email}}</p>
        {% if user.profile and user.profile.bio %}<p class="small muted">{{user.profile.bio}}</p>{% endif %}
        <form method="POST" enctype="multipart/form-data">
          <label>Profile Picture</label>
          <input type="file" name="profile_pic" accept="image/*" required>
          <button class="btn">Upload Picture</button>
        </form>
      </div>

      <div class="card">
        <h3>Money Plus Card</h3>
        <a class="card-art" href="{{url_for('card_page')}}">
          <div class="money-card">
            <div class="card-brand">MONEY PLUS</div>
            <div style="font-size:11px">At your Utmost Interest</div>
            <div class="chip"></div>
            <div class="card-number">•••• •••• •••• 2048</div>
            <div class="card-bottom">
              <span>{{user.username|upper}}</span><span>DEBIT</span>
            </div>
          </div>
        </a>
        {% if card.get("purchased") %}
        <p class="credit"><b>Card purchased.</b></p>
        <p>Expected arrival: <b>{{card.arrival_date}}</b></p>
        {% else %}
        <p class="muted">Tap the card to view details and purchase it.</p>
        {% endif %}
      </div>

      <div class="card">
        <h3>Account</h3>
        <a class="menu" href="{{url_for('profile_edit')}}">✏️ <span>Edit Profile</span><span>›</span></a>
<a class="menu" href="{{url_for('account_management')}}">⚙️ <span>Account Management</span><span>›</span></a>
        <a class="menu" href="{{url_for('settings')}}">🎨 <span>Appearance & Settings</span><span>›</span></a>
<a class="menu" href="{{url_for('security')}}">🔐 <span>Security</span><span>›</span></a>
        <a class="menu" href="{{url_for('switch_account')}}">🔄 <span>Switch Account</span><span>›</span></a>
        <a class="menu" href="{{url_for('logout_now')}}">🚪 <span>Log Out</span><span>›</span></a>
<a class="menu" href="{{url_for('logout_page')}}">🗑️ <span>Delete Account</span><span>›</span></a>
      </div>
    </div>
    """, user=user, picture=picture, card=card)

    return render_template_string(BASE_HTML, title="Me", body=body)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    users = load_users()
    username = session["username"]
    user = users[username]
    ensure_user_upgrades(user)

    if request.method == "POST":
        user["dark_mode"] = request.form.get("dark_mode") == "on"
        user["preferences"]["notifications"] = request.form.get("notifications") == "on"
        user["preferences"]["compact_mode"] = request.form.get("compact_mode") == "on"
        session["dark_mode"] = user["dark_mode"]
        save_users(users)
        flash("Settings updated.", "success")
        return redirect(url_for("settings"))

    body = render_template_string("""
    <div class="content">
      <div class="card">
        <span class="badge">SETTINGS</span>
        <h2 style="margin-top:10px">Customize Money Plus</h2>
        <p class="muted">Control appearance and notifications.</p>
        <form method="POST">
          <label style="display:flex;align-items:center;gap:12px;font-size:15px;padding:14px 0">
            <input type="checkbox" name="dark_mode" style="width:21px;height:21px;margin:0" {%if user.dark_mode%}checked{%endif%}>
            Dark Mode
          </label>
          <label style="display:flex;align-items:center;gap:12px;font-size:15px;padding:14px 0">
            <input type="checkbox" name="notifications" style="width:21px;height:21px;margin:0" {%if user.preferences.notifications%}checked{%endif%}>
            Activity Notifications
          </label>
          <label style="display:flex;align-items:center;gap:12px;font-size:15px;padding:14px 0">
            <input type="checkbox" name="compact_mode" style="width:21px;height:21px;margin:0" {%if user.preferences.compact_mode%}checked{%endif%}>
            Compact Interface
          </label>
          <button class="btn">Save Settings</button>
        </form>
      </div>

      <div class="card">
        <h3>Security Center</h3>
        <p class="muted">Change your password and payment PIN.</p>
        <a class="btn secondary" href="{{url_for('security')}}">🔐 Open Security Center</a>
      </div>

      <div class="card">
        <h3>About</h3>
        <div class="menu"><span>Application</span><b>Money Plus</b></div>
        <div class="menu"><span>Version</span><b>MAX 2026</b></div>
        <div class="warning">Local/demo application — do not use real banking credentials.</div>
      </div>
    </div>
    """, user=user)

    return render_template_string(BASE_HTML, title="Settings", body=body)


@app.route("/card", methods=["GET", "POST"])
def card_page():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    users = load_users()
    username = session["username"]
    user = users[username]
    prepare_user(user)
    card = user.get("card", {})
    price = 17050

    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        pin = request.form.get("payment_pin", "")

        if card.get("purchased"):
            flash("Your Money Plus card has already been purchased.", "error")
            return redirect(url_for("card_page"))
        if not phone or not address:
            flash("Phone number and address are required.", "error")
            return redirect(url_for("card_page"))
        if not valid_pin(user, pin):
            flash("Incorrect payment PIN.", "error")
            return redirect(url_for("card_page"))
        if float(user.get("balance", 0)) < price:
            flash("Insufficient Transactions balance for the Money Plus card.", "error")
            return redirect(url_for("card_page"))

        purchase = datetime.now()
        arrival = purchase + timedelta(days=7)

        user["balance"] = round(float(user.get("balance", 0)) - price, 2)
        user["card"] = {
            "purchased": True,
            "purchase_date": purchase.strftime("%d %B %Y"),
            "arrival_date": arrival.strftime("%d %B %Y"),
            "phone": phone,
            "address": address
        }
        add_transaction(user, "Money Plus Card Purchase", price, "debit")
        save_users(users)

        flash("Card purchase recorded. Expected arrival: " + arrival.strftime("%d %B %Y"), "success")
        return redirect(url_for("profile"))

    body = render_template_string("""
    <div class="content">
      <div class="card">
        <h2>Money Plus Card</h2>
        <div class="money-card" style="margin-top:14px">
          <div class="card-brand">MONEY PLUS</div>
          <div style="font-size:11px">At your Utmost Interest</div>
          <div class="chip"></div>
          <div class="card-number">•••• •••• •••• 2048</div>
          <div class="card-bottom"><span>{{user.username|upper}}</span><span>DEBIT</span></div>
        </div>
        <p><b>Price: ₦17,050</b></p>

        {% if card.get("purchased") %}
        <div class="daily-task">
          <b>Card purchased successfully.</b>
          <p>Purchased: {{card.purchase_date}}</p>
          <p>Expected arrival: <b>{{card.arrival_date}}</b></p>
        </div>
        {% else %}
        <form method="POST">
          <label>Phone Number</label>
          <input name="phone" value="{{user.phone}}" required>
          <label>Delivery Address</label>
          <textarea name="address" placeholder="Enter delivery address" required></textarea>
          <label>Payment PIN</label>
          <input type="password" name="payment_pin" inputmode="numeric" maxlength="4" required>
          <button class="btn">Buy Card — ₦17,050</button>
        </form>
        {% endif %}
      </div>
    </div>
    """, user=user, card=card)

    return render_template_string(BASE_HTML, title="Money Plus Card", body=body)


@app.route("/profile_pics/<filename>")
def profile_picture(filename):
    return send_from_directory(PROFILE_DIR, filename)


# =========================================================
# NOTIFICATIONS
# =========================================================

@app.route("/notifications", methods=["GET", "POST"])
def notifications_page():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    users = load_users()
    user = users[session["username"]]
    ensure_user_upgrades(user)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "read_all":
            for notification in user.get("notifications", []):
                notification["read"] = True
            save_users(users)
            flash("All notifications marked as read.", "success")

        elif action == "clear":
            user["notifications"] = []
            save_users(users)
            flash("Notifications cleared.", "success")

        return redirect(url_for("notifications_page"))

    body = render_template_string("""
    <div class="content">
      <div class="card">
        <div class="section-head">
          <div><h2>Notifications</h2><small class="muted">Stay updated on your Money Plus activity.</small></div>
          <span class="badge">{{notifications|selectattr('read','equalto',false)|list|length}} NEW</span>
        </div>
        <div class="grid2">
          <form method="POST">
            <input type="hidden" name="action" value="read_all">
            <button class="btn secondary">Mark All Read</button>
          </form>
          <form method="POST">
            <input type="hidden" name="action" value="clear">
            <button class="btn danger">Clear All</button>
          </form>
        </div>
      </div>
      <div class="card">
        {% for n in notifications %}
        <div class="update" style="{%if not n.read%}background:var(--g3);padding:13px;border-radius:13px;margin-bottom:8px{%endif%}">
          <div class="section-head"><b>{{n.title}}</b><small class="muted">{{n.date}}</small></div>
          <div class="small" style="margin-top:6px">{{n.message}}</div>
        </div>
        {% else %}
        <div class="empty">No notifications.</div>
        {% endfor %}
      </div>
    </div>
    """, notifications=user.get("notifications", []))

    return render_template_string(BASE_HTML, title="Notifications", body=body)


# =========================================================
# SECURITY SETTINGS
# =========================================================

@app.route("/security", methods=["GET", "POST"])
def security():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    users = load_users()
    user = users[session["username"]]

    if request.method == "POST":
        action = request.form.get("action")

        if action == "password":
            current = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")

            if not verify_value(current, user.get("password", "")):
                flash("Current password is incorrect.", "error")
                return redirect(url_for("security"))
            if len(new_password) < 6:
                flash("New password must contain at least 6 characters.", "error")
                return redirect(url_for("security"))
            if new_password != confirm:
                flash("New passwords do not match.", "error")
                return redirect(url_for("security"))

            user["password"] = hash_value(new_password)
            add_notification(user, "Password changed", "Your account password was changed.", "security")
            save_users(users)
            flash("Password changed successfully.", "success")
            return redirect(url_for("security"))

        if action == "pin":
            current = request.form.get("current_pin", "")
            new_pin = request.form.get("new_pin", "")
            confirm = request.form.get("confirm_pin", "")

            if not valid_pin(user, current):
                flash("Current payment PIN is incorrect.", "error")
                return redirect(url_for("security"))
            if len(new_pin) != 4 or not new_pin.isdigit():
                flash("New payment PIN must be exactly 4 digits.", "error")
                return redirect(url_for("security"))
            if new_pin != confirm:
                flash("New PINs do not match.", "error")
                return redirect(url_for("security"))

            user["pin"] = hash_value(new_pin)
            add_notification(user, "Payment PIN changed", "Your payment PIN was changed.", "security")
            save_users(users)
            flash("Payment PIN changed successfully.", "success")
            return redirect(url_for("security"))

    body = """
    <div class="content">
      <div class="card">
        <span class="badge">SECURITY</span>
        <h2 style="margin-top:10px">Protect Your Account</h2>
        <p class="muted">Change your login password or payment PIN whenever needed.</p>
      </div>

      <div class="card">
        <h3>Change Password</h3>
        <form method="POST">
          <input type="hidden" name="action" value="password">
          <label>Current Password</label>
          <input type="password" name="current_password" required>
          <label>New Password</label>
          <input type="password" name="new_password" minlength="6" required>
          <label>Confirm New Password</label>
          <input type="password" name="confirm_password" minlength="6" required>
          <button class="btn">Update Password</button>
        </form>
      </div>

      <div class="card">
        <h3>Change Payment PIN</h3>
        <form method="POST">
          <input type="hidden" name="action" value="pin">
          <label>Current PIN</label>
          <input type="password" name="current_pin" inputmode="numeric" maxlength="4" required>
          <label>New 4-Digit PIN</label>
          <input type="password" name="new_pin" inputmode="numeric" maxlength="4" required>
          <label>Confirm New PIN</label>
          <input type="password" name="confirm_pin" inputmode="numeric" maxlength="4" required>
          <button class="btn">Update PIN</button>
        </form>
      </div>

      <div class="card">
        <div class="warning">
          Never share your password or payment PIN with another person.
        </div>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Security", body=body)


# =========================================================
# PROFILE EDIT
# =========================================================

@app.route("/profile-edit", methods=["GET", "POST"])
def profile_edit():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    users = load_users()
    user = users[session["username"]]
    ensure_user_upgrades(user)

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        bio = request.form.get("bio", "").strip()
        phone = request.form.get("phone", "").strip()

        if not full_name:
            flash("Full name cannot be empty.", "error")
            return redirect(url_for("profile_edit"))

        user["profile"]["full_name"] = full_name
        user["profile"]["bio"] = bio
        if phone:
            user["phone"] = phone

        save_users(users)
        flash("Profile details updated.", "success")
        return redirect(url_for("profile"))

    profile_data = user.get("profile", {})
    body = render_template_string("""
    <div class="content">
      <div class="card">
        <span class="badge">EDIT PROFILE</span>
        <h2 style="margin-top:10px">Profile Details</h2>
        <form method="POST">
          <label>Full Name</label>
          <input name="full_name" value="{{profile.full_name}}" required>
          <label>Phone</label>
          <input name="phone" value="{{user.phone}}" inputmode="tel">
          <label>Bio</label>
          <textarea name="bio" maxlength="160" placeholder="Tell us about yourself">{{profile.bio}}</textarea>
          <button class="btn">Save Profile</button>
        </form>
      </div>
    </div>
    """, user=user, profile=profile_data)
    return render_template_string(BASE_HTML, title="Edit Profile", body=body)


# =========================================================
# ACCOUNT MANAGEMENT
# =========================================================

@app.route("/account-management", methods=["GET", "POST"])
def account_management():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            password = request.form.get("password", "")
            pin = request.form.get("pin", "")

            if not all([username, email, phone, password, pin]):
                flash("Fill all fields.", "error")
                return redirect(url_for("account_management"))
            if len(pin) != 4 or not pin.isdigit():
                flash("Payment PIN must be exactly 4 digits.", "error")
                return redirect(url_for("account_management"))

            users = load_users()
            if username in users:
                flash("Username already exists.", "error")
                return redirect(url_for("account_management"))
            if any(o.get("email", "").lower() == email for o in users.values()):
                flash("Email already exists.", "error")
                return redirect(url_for("account_management"))
            if any(o.get("phone", "").strip() == phone for o in users.values()):
                flash("Phone number already exists.", "error")
                return redirect(url_for("account_management"))

            account_number = generate_account_number(phone, users)
            referral_code = (
                "MP"
                + "".join(c for c in username.upper() if c.isalnum())[:6]
                + str(uuid.uuid4().int % 1000).zfill(3)
            )

            users[username] = {
                "username": username,
                "email": email,
                "phone": phone,
                "password": hash_value(password),
                "pin": hash_value(pin),
                "account_number": account_number,
                "balance": 0,
                "wealth_balance": 0,
                "transactions": [],
                "profile_pic": "",
                "welcome_claimed": False,
                "money_added_once": False,
                "referrals": 0,
                "referral_earnings": 0,
                "referral_code": referral_code,
                "referred_by": "",
                "savings": [],
                "dark_mode": False,
                "daily_reward": {},
                "card": {
                    "purchased": False,
                    "purchase_date": "",
                    "arrival_date": "",
                    "phone": phone,
                    "address": ""
                }
            }

            save_users(users)
            flash("Account added successfully.", "success")
            return redirect(url_for("account_management"))

        if action == "delete":
            # A signed-in person may delete ONLY their own account.
            delete_username = request.form.get("delete_username", "").strip()
            current_username = session.get("username", "")

            if delete_username != current_username:
                flash("You can only delete your own account.", "error")
                return redirect(url_for("account_management"))

            users = load_users()
            if current_username not in users:
                session.clear()
                return redirect(url_for("login"))

            picture = users[current_username].get("profile_pic", "")
            if picture:
                path = os.path.join(PROFILE_DIR, picture)
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

            del users[current_username]
            save_users(users)
            session.clear()
            flash("Your account was deleted successfully.", "success")
            return redirect(url_for("login"))

    users = load_users()
    current = users.get(session["username"])
    prepare_user(current)

    body = render_template_string("""
    <div class="content">
      <div class="card">
        <span class="badge">ACCOUNT MANAGEMENT</span>
        <h2 style="margin-top:10px">Manage Accounts</h2>
        <p class="muted">Manage your own Money Plus account. You cannot delete another person's account.</p>
      </div>

      <div class="card">
        <h3>Current Account</h3>
        <div class="save-box">
          <b>{{current.username}}</b>
          <div class="small muted">{{current.email}}</div>
          <div class="small muted">Account: {{current.account_number}}</div>
        </div>
        <a class="btn secondary" href="{{url_for('switch_account')}}">🔄 Switch Account</a>
        <form method="POST">
          <input type="hidden" name="action" value="delete">
          <input type="hidden" name="delete_username" value="{{current.username}}">
          <button class="btn danger" onclick="return confirm('Delete this account permanently?')">
            Delete My Account
          </button>
        </form>
      </div>

      <div class="card">
        <h3>Add Account</h3>
        <form method="POST">
          <input type="hidden" name="action" value="add">
          <label>Username</label><input name="username" required>
          <label>Email</label><input type="email" name="email" required>
          <label>Phone</label><input name="phone" required>
          <label>Password</label><input type="password" name="password" required>
          <label>4-Digit Payment PIN</label><input type="password" name="pin" maxlength="4" inputmode="numeric" required>
          <button class="btn">Add Account</button>
        </form>
      </div>
    </div>
    """, current=current)

    return render_template_string(BASE_HTML, title="Account Management", body=body)


# =========================================================
# DELETE ACCOUNT / LOGOUT PAGE
# =========================================================

@app.route("/logout", methods=["GET", "POST"])
def logout_page():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":
        password = request.form.get("password", "")
        pin = request.form.get("payment_pin", "")

        if not verify_value(password, user.get("password", "")):
            flash("Incorrect password.", "error")
            return redirect(url_for("logout_page"))

        if not valid_pin(user, pin):
            flash("Incorrect payment PIN.", "error")
            return redirect(url_for("logout_page"))

        users = load_users()
        username = session["username"]
        picture = users[username].get("profile_pic", "")

        if picture:
            path = os.path.join(PROFILE_DIR, picture)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        del users[username]
        save_users(users)
        session.clear()
        flash("Account deleted successfully.", "success")
        return redirect(url_for("login"))

    body = """
    <div class="content">
      <div class="card">
        <span class="badge">DANGER ZONE</span>
        <h2 style="margin-top:10px">Delete Account</h2>
        <p>Enter your password and payment PIN to permanently delete this local account.</p>
        <div class="warning">This action cannot be undone. Your saved local account data will be deleted.</div>
        <form method="POST">
          <label>Password</label>
          <input type="password" name="password" required>
          <label>Payment PIN</label>
          <input type="password" name="payment_pin" inputmode="numeric" maxlength="4" required>
          <button class="btn danger" onclick="return confirm('Delete this account permanently?')">
            Delete Account
          </button>
        </form>
        <a class="btn secondary" href="{{url_for('profile')}}">Cancel</a>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Delete Account", body=body)


# =========================================================
# SWITCH ACCOUNT / LOGOUT
# =========================================================

@app.route("/switch")
def switch_account():
    session.clear()
    return redirect(url_for("login"))


@app.route("/logout-now")
def logout_now():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(413)
def file_too_large(error):
    flash("Image is too large. Maximum size is 5 MB.", "error")
    return redirect(url_for("profile"))


@app.errorhandler(404)
def not_found(error):
    return redirect(url_for("dashboard") if session.get("username") else url_for("login"))


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    users = load_users()
    for _user in users.values():
        ensure_user_upgrades(_user)
    migrate_legacy_credentials(users)

    print("")
    print("===================================")
    print("       MONEY PLUS BANKING LTD")
    print("===================================")
    print("")
    print("Open this in your browser:")
    print("http://127.0.0.1:5000")
    print("")

    app.run(host="127.0.0.1", port=5000, debug=False)
