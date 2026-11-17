import html
import json
import os
import re
import uuid
from datetime import datetime
from functools import wraps

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)
from markupsafe import Markup
from PIL import Image
from slugify import slugify
from werkzeug.utils import secure_filename

from config import Config
from models import (Banner, Category, ChatMessage, Coupon, CouponRedemption,
                    Deposit, Notification, Order, Package, PaymentMethod,
                    Product, ProductField, SiteSetting, User, db)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "অর্ডার করতে অনুগ্রহ করে লগইন করুন।"
login_manager.login_message_category = "warning"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in Config.ALLOWED_EXTENSIONS


def save_image(file, max_size=512):
    """Save an uploaded image, auto-resize keeping aspect ratio. Returns filename."""
    if not file or file.filename == "":
        return ""
    if not allowed_file(file.filename):
        return ""
    ext = file.filename.rsplit(".", 1)[1].lower()
    ext = "jpg" if ext == "jpeg" else ext
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(Config.UPLOAD_FOLDER, filename)
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    try:
        img = Image.open(file)
        if img.mode in ("RGBA", "P") and ext == "jpg":
            img = img.convert("RGB")
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        img.save(path, quality=88)
    except Exception:
        return ""
    return filename


def unique_slug(model, name, current_id=None):
    base = slugify(name) or f"item-{uuid.uuid4().hex[:6]}"
    slug = base
    i = 1
    while True:
        q = model.query.filter_by(slug=slug)
        if current_id:
            q = q.filter(model.id != current_id)
        if not q.first():
            return slug
        i += 1
        slug = f"{base}-{i}"


def humanize_time(value):
    """Return a short 'x minutes ago' label used by the live order ticker."""
    if not value:
        return ""
    seconds = max(0, int((datetime.utcnow() - value).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def ensure_schema():
    """Tiny SQLite migration helper so existing databases keep working."""
    from sqlalchemy import text
    try:
        cols = [r[1] for r in db.session.execute(text("PRAGMA table_info(orders)"))]
    except Exception:
        cols = []
    additions = {
        "payment_method_id": "INTEGER",
        "txn_id": "VARCHAR(160) DEFAULT ''",
        "extra_data": "TEXT DEFAULT ''",
    }
    for name, ddl in additions.items():
        if name not in cols:
            try:
                db.session.execute(text(f"ALTER TABLE orders ADD COLUMN {name} {ddl}"))
                db.session.commit()
            except Exception:
                db.session.rollback()
    # User table additions (avatar / ip / plain password for admin view).
    try:
        ucols = [r[1] for r in db.session.execute(text("PRAGMA table_info(users)"))]
    except Exception:
        ucols = []
    uadds = {
        "avatar": "VARCHAR(255) DEFAULT ''",
        "ip_address": "VARCHAR(64) DEFAULT ''",
        "is_ip_banned": "BOOLEAN DEFAULT 0",
        "plain_password": "VARCHAR(120) DEFAULT ''",
        "last_seen": "DATETIME",
    }
    for name, ddl in uadds.items():
        if name not in ucols:
            try:
                db.session.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
                db.session.commit()
            except Exception:
                db.session.rollback()
    # Package table additions (discounts / stock-out).
    try:
        pcols = [r[1] for r in db.session.execute(text("PRAGMA table_info(packages)"))]
    except Exception:
        pcols = []
    padds = {
        "discount_type": "VARCHAR(20) DEFAULT 'none'",
        "discount_value": "FLOAT DEFAULT 0",
        "stock_out": "BOOLEAN DEFAULT 0",
    }
    for name, ddl in padds.items():
        if name not in pcols:
            try:
                db.session.execute(text(f"ALTER TABLE packages ADD COLUMN {name} {ddl}"))
                db.session.commit()
            except Exception:
                db.session.rollback()
    # Product table additions (per-product order rules).
    try:
        prcols = [r[1] for r in db.session.execute(text("PRAGMA table_info(products)"))]
    except Exception:
        prcols = []
    if "order_rules" not in prcols:
        try:
            db.session.execute(text("ALTER TABLE products ADD COLUMN order_rules TEXT DEFAULT ''"))
            db.session.commit()
        except Exception:
            db.session.rollback()
    # Coupon table additions (usage / amount limits).
    try:
        ccols = [r[1] for r in db.session.execute(text("PRAGMA table_info(coupons)"))]
    except Exception:
        ccols = []
    cadds = {
        "usage_limit": "INTEGER DEFAULT 0",
        "per_user_limit": "INTEGER DEFAULT 0",
        "min_amount": "FLOAT DEFAULT 0",
        "max_discount": "FLOAT DEFAULT 0",
    }
    for name, ddl in cadds.items():
        if name not in ccols:
            try:
                db.session.execute(text(f"ALTER TABLE coupons ADD COLUMN {name} {ddl}"))
                db.session.commit()
            except Exception:
                db.session.rollback()
    # Create the new tables (notifications / chat_messages / coupons /
    # coupon_redemptions) if missing.
    try:
        db.create_all()
    except Exception:
        db.session.rollback()


def ensure_default_fields():
    """Every product must have exactly one default UID field."""
    for product in Product.query.all():
        uid = ProductField.query.filter_by(product_id=product.id, is_uid=True).first()
        if not uid:
            db.session.add(ProductField(
                product_id=product.id, label="Player ID / UID",
                placeholder="আপনার Player ID / UID লিখুন",
                field_type="text", required=True, is_uid=True, sort_order=0))
    db.session.commit()


def parse_product_fields(product):
    """Read dynamic field rows from an admin product form."""
    labels = request.form.getlist("field_label[]")
    placeholders = request.form.getlist("field_placeholder[]")
    types = request.form.getlist("field_type[]")
    required = request.form.getlist("field_required[]")
    flags = request.form.getlist("field_is_uid[]")
    rows = []
    for i, label in enumerate(labels):
        label = (label or "").strip()
        if not label:
            continue
        rows.append(dict(
            label=label,
            placeholder=(placeholders[i] if i < len(placeholders) else "").strip(),
            field_type=(types[i] if i < len(types) else "text") or "text",
            required=(required[i] if i < len(required) else "1") == "1",
            is_uid=(flags[i] if i < len(flags) else "0") == "1",
            sort_order=i,
        ))
    if not rows:
        rows = [dict(label="Player ID / UID", placeholder="আপনার Player ID / UID লিখুন",
                     field_type="text", required=True, is_uid=True, sort_order=0)]
    # Guarantee exactly one UID field, always first.
    uid_rows = [r for r in rows if r["is_uid"]]
    if not uid_rows:
        rows.insert(0, dict(label="Player ID / UID", placeholder="আপনার Player ID / UID লিখুন",
                            field_type="text", required=True, is_uid=True, sort_order=0))
    else:
        uid = uid_rows[0]
        rows = [r for r in rows if not r["is_uid"]]
        rows.insert(0, uid)
    for i, row in enumerate(rows):
        row["sort_order"] = i
        row["is_uid"] = (i == 0)
    return rows


def save_product_fields(product, rows):
    ProductField.query.filter_by(product_id=product.id).delete()
    for row in rows:
        db.session.add(ProductField(product_id=product.id, **row))
    db.session.commit()


def collect_checkout_fields(product):
    """Validate dynamic product fields and return (values, player_id, error)."""
    values = {}
    player_id = ""
    for field in product.fields:
        raw = request.form.get(f"field_{field.id}", "").strip()
        if field.required and not raw:
            return values, player_id, f"'{field.label}' পূরণ করুন।"
        values[field.label] = raw
        if field.is_uid:
            player_id = raw
    if not player_id:
        # Backwards compatibility if a product somehow has no UID field.
        player_id = request.form.get("player_id", "").strip()
        if not player_id:
            return values, player_id, "Player ID / UID দিন।"
        values.setdefault("Player ID / UID", player_id)
    return values, player_id, ""


def checkout_session_data():
    data = session.get("checkout") or {}
    product = db.session.get(Product, data.get("product_id") or 0)
    package = db.session.get(Package, data.get("package_id") or 0)
    if not product or not package or package.product_id != product.id:
        return None, None, None
    return data, product, package


def resolve_coupon(code, product_id, amount, user_id=None):
    """Return (coupon, discount, error). Coupon may be None when no code given.

    Enforces: product scope, minimum order amount, total usage limit and
    per-user usage limit. The discount itself is capped by ``max_discount``
    inside ``Coupon.discount_for``.
    """
    code = (code or "").strip()
    if not code:
        return None, 0.0, ""
    coupon = Coupon.query.filter(db.func.lower(Coupon.code) == code.lower()).first()
    if not coupon or not coupon.active:
        return None, 0.0, "কুপন কোডটি সঠিক নয় বা নিষ্ক্রিয়।"
    if not coupon.applies_to(product_id):
        return None, 0.0, "এই কুপনটি এই প্রোডাক্টে প্রযোজ্য নয়।"
    # Minimum order amount
    min_amount = float(coupon.min_amount or 0)
    if min_amount > 0 and float(amount or 0) < min_amount:
        return None, 0.0, f"এই কুপন ব্যবহার করতে কমপক্ষে {min_amount:g} টাকার অর্ডার প্রয়োজন।"
    # Total usage limit
    if coupon.usage_limit and (coupon.used_count or 0) >= coupon.usage_limit:
        return None, 0.0, "এই কুপনের ব্যবহারের সীমা শেষ হয়ে গেছে।"
    # Per-user usage limit
    if coupon.per_user_limit and user_id:
        used_by_user = CouponRedemption.query.filter_by(
            coupon_id=coupon.id, user_id=user_id).count()
        if used_by_user >= coupon.per_user_limit:
            return None, 0.0, "আপনি এই কুপনটি সর্বোচ্চ সংখ্যকবার ব্যবহার করেছেন।"
    discount = coupon.discount_for(amount)
    if discount <= 0:
        return None, 0.0, "এই কুপনে কোনো ডিসকাউন্ট নেই।"
    return coupon, discount, ""


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("অ্যাডমিন লগইন প্রয়োজন।", "warning")
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return decorated


# ----------------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------------
def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()
        ensure_schema()
        try:
            ensure_default_fields()
        except Exception:
            db.session.rollback()

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ---------------- Helpers ----------------
    def client_ip():
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.remote_addr or ""

    def notify(user_id, title, body="", kind="system", link=""):
        """Create an in-app notification for a single user."""
        db.session.add(Notification(user_id=user_id, title=title, body=body,
                                    kind=kind, link=link))
        db.session.commit()

    def notify_all(title, body="", kind="announcement", link=""):
        for u in User.query.filter_by(is_admin=False).all():
            db.session.add(Notification(user_id=u.id, title=title, body=body,
                                        kind=kind, link=link))
        db.session.commit()

    @app.before_request
    def track_and_guard():
        # Website ON/OFF switch. The admin panel and static assets always stay
        # reachable so the owner can turn the site back on.
        try:
            site_on = SiteSetting.get("site_enabled", "1") != "0"
        except Exception:
            site_on = True
        if not site_on:
            path = request.path or "/"
            allowed = (path.startswith("/admin") or path.startswith("/static")
                       or path in ("/favicon.ico",))
            if not allowed:
                return render_template("maintenance.html"), 503

        # Remember the visitor IP and enforce IP bans.
        if current_user.is_authenticated:
            ip = client_ip()
            if ip and current_user.ip_address != ip:
                current_user.ip_address = ip
            current_user.last_seen = datetime.utcnow()
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            if current_user.is_ip_banned:
                logout_user()
                return render_template("banned.html", reason="ip"), 403
            if current_user.is_blocked and not current_user.is_admin:
                logout_user()
                return render_template("banned.html", reason="account"), 403
        else:
            ip = client_ip()
            if ip:
                banned = User.query.filter_by(is_ip_banned=True).filter(
                    User.ip_address == ip).first()
                if banned:
                    return render_template("banned.html", reason="ip"), 403
        return None

    # ---------------- Context processors ----------------
    @app.context_processor
    def inject_globals():
        settings = SiteSetting.all_dict()
        cats = Category.query.filter_by(active=True).order_by(Category.sort_order).all()
        unread = 0
        notifs = []
        chat_unread = 0
        if current_user.is_authenticated:
            unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
            notifs = Notification.query.filter_by(user_id=current_user.id)\
                .order_by(Notification.created_at.desc()).limit(8).all()
            chat_unread = ChatMessage.query.filter_by(
                user_id=current_user.id, sender="admin", read_by_user=False).count()
            if current_user.is_admin:
                admin_chat_unread = ChatMessage.query.filter_by(
                    sender="user", read_by_admin=False).count()
                chat_unread = admin_chat_unread
        return dict(
            settings=settings,
            nav_categories=cats,
            current_year=datetime.utcnow().year,
            now=datetime.utcnow(),
            unread_notifications=unread,
            header_notifications=notifs,
            chat_unread=chat_unread,
            admin_chat_unread=chat_unread if current_user.is_authenticated and current_user.is_admin else 0,
        )

    @app.template_filter("taka")
    def taka_filter(value):
        symbol = SiteSetting.get("currency", "৳") or "৳"
        try:
            return f"{symbol}{value:,.0f}"
        except (TypeError, ValueError):
            return f"{symbol}0"

    @app.template_filter("dt")
    def dt_filter(value, fmt="%d %b %Y, %I:%M %p"):
        return value.strftime(fmt) if value else ""

    @app.template_filter("timeago")
    def timeago_filter(value):
        return humanize_time(value)

    @app.template_filter("paydesc")
    def paydesc_filter(text, price=0):
        """Render admin payment instructions.

        Supports {price} (replaced with the selected package price) and
        <c>text</c> which becomes a small inline chip with a copy button.
        """
        raw = str(text or "")
        safe = html.escape(raw)
        safe = safe.replace("{price}", html.escape(taka_filter(price)))
        chip = ('<span class="copy-chip"><span class="copy-chip-text">{}</span>'
                '<button type="button" class="copy-chip-btn" data-copy="{}">Copy</button></span>')

        def _chip(match):
            value = match.group(1)
            return chip.format(value, value)

        safe = re.sub(r"&lt;c&gt;(.*?)&lt;/c&gt;", _chip, safe, flags=re.S | re.I)
        safe = safe.replace("\r\n", "\n").replace("\n", "<br>")
        return Markup(safe)

    # Valid CSS icon keys (see static/css/icons.css). Anything unknown falls
    # back to the gamepad icon so legacy/emoji values never break the UI.
    ICON_KEYS = {
        "bolt", "user", "menu", "phone", "mail", "clock", "heart", "home",
        "gamepad", "chat", "search", "shield", "money", "gem", "lock", "cart",
        "clipboard", "gift", "card", "box", "key", "chart", "folder", "image",
        "receipt", "wallet", "users", "gear", "globe", "logout", "save",
        "arrow", "arrow-left", "check", "close", "crown", "fire", "star",
        "spark", "info", "plus",
    }

    @app.template_filter("cat_icon")
    def cat_icon_filter(value):
        key = (value or "").strip().lower()
        return key if key in ICON_KEYS else "gamepad"

    # ========================================================================
    # PUBLIC ROUTES
    # ========================================================================
    @app.route("/")
    def home():
        banners = Banner.query.filter_by(active=True).order_by(Banner.sort_order).all()
        categories = Category.query.filter_by(active=True).order_by(Category.sort_order).all()
        products = (Product.query.filter_by(active=True)
                    .order_by(Product.sort_order).limit(12).all())
        recent = (Order.query.order_by(Order.created_at.desc()).limit(10).all())
        payments = PaymentMethod.query.filter_by(active=True).order_by(PaymentMethod.sort_order).all()
        return render_template("index.html", banners=banners, categories=categories,
                               products=products, recent=recent, payments=payments)

    @app.route("/topup")
    def topup():
        cat_slug = request.args.get("category", "")
        q = request.args.get("q", "").strip()
        query = Product.query.filter_by(active=True)
        active_cat = None
        if cat_slug:
            active_cat = Category.query.filter_by(slug=cat_slug).first()
            if active_cat:
                query = query.filter_by(category_id=active_cat.id)
        if q:
            query = query.filter(Product.name.ilike(f"%{q}%"))
        products = query.order_by(Product.sort_order).all()
        categories = Category.query.filter_by(active=True).order_by(Category.sort_order).all()
        return render_template("topup.html", products=products, categories=categories,
                               active_cat=active_cat, q=q)

    @app.route("/topup/<int:pid>/<slug>")
    def product_detail(pid, slug):
        product = db.session.get(Product, pid)
        if not product or not product.active:
            abort(404)
        # Guests are sent to the login page before they can open a product.
        if not current_user.is_authenticated:
            flash("প্রোডাক্ট দেখতে ও অর্ডার করতে অনুগ্রহ করে লগইন করুন।", "warning")
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        packages = [p for p in product.packages if p.active]
        payments = PaymentMethod.query.filter_by(active=True).order_by(PaymentMethod.sort_order).all()
        related = (Product.query.filter(Product.category_id == product.category_id,
                                        Product.id != product.id, Product.active.is_(True))
                   .limit(4).all())
        return render_template("product.html", product=product, packages=packages,
                               payments=payments, related=related,
                               fields=product.fields)

    @app.route("/coupon/apply", methods=["POST"])
    @login_required
    def coupon_apply():
        """AJAX endpoint used by the coupon box on the product page."""
        pid = request.form.get("product_id", type=int)
        pkg_id = request.form.get("package_id", type=int)
        qty = max(1, request.form.get("quantity", 1, type=int))
        code = request.form.get("code", "").strip()
        product = db.session.get(Product, pid)
        package = db.session.get(Package, pkg_id)
        if not product or not package or package.product_id != product.id:
            return jsonify(ok=False, message="প্রোডাক্ট বা প্যাকেজ সঠিক নয়।")
        if package.stock_out or not package.active:
            return jsonify(ok=False, message="এই প্যাকেজটি স্টক আউট।")
        base_total = package.discounted_price * qty
        coupon, discount, error = resolve_coupon(code, product.id, base_total, current_user.id)
        if error:
            return jsonify(ok=False, message=error)
        total = round(max(base_total - discount, 0), 2)
        return jsonify(ok=True, code=coupon.code, label=coupon.label,
                       discount=discount, total=total, base_total=base_total,
                       message=f"কুপন প্রয়োগ হয়েছে — {coupon.label}")

    @app.route("/checkout", methods=["POST"])
    @login_required
    def checkout():
        pid = request.form.get("product_id", type=int)
        pkg_id = request.form.get("package_id", type=int)
        payment_type = request.form.get("payment_type", "wallet")
        qty = max(1, request.form.get("quantity", 1, type=int))

        product = db.session.get(Product, pid)
        package = db.session.get(Package, pkg_id)
        if not product or not package or package.product_id != product.id:
            flash("অবৈধ প্রোডাক্ট বা প্যাকেজ।", "danger")
            return redirect(url_for("topup"))

        if package.stock_out or not package.active:
            flash("দুঃখিত, এই প্যাকেজটি এখন স্টক আউট। অন্য প্যাকেজ বেছে নিন।", "danger")
            return redirect(url_for("product_detail", pid=product.id, slug=product.slug))

        values, player_id, error = collect_checkout_fields(product)
        if error:
            flash(error, "danger")
            return redirect(url_for("product_detail", pid=product.id, slug=product.slug))

        base_total = package.discounted_price * qty
        coupon_code = request.form.get("coupon_code", "").strip()
        coupon, coupon_discount, coupon_error = resolve_coupon(coupon_code, product.id, base_total, current_user.id)
        if coupon_error:
            flash(coupon_error, "danger")
            return redirect(url_for("product_detail", pid=product.id, slug=product.slug))
        total = round(max(base_total - coupon_discount, 0), 2)

        if payment_type == "wallet":
            wallet = PaymentMethod.query.filter_by(type="wallet", active=True).first()
            if current_user.balance < total:
                flash("আপনার ওয়ালেট ব্যালেন্স যথেষ্ট নয়। অনুগ্রহ করে ডিপোজিট করুন অথবা Instant Pay ব্যবহার করুন।", "danger")
                return redirect(url_for("product_detail", pid=product.id, slug=product.slug))
            current_user.balance -= total
            note = f"কুপন {coupon.code} (-৳{coupon_discount:g})" if coupon else ""
            order = Order(user_id=current_user.id, product_id=product.id, package_id=package.id,
                          player_id=player_id, quantity=qty, unit_price=package.discounted_price,
                          total=total, payment_method=(wallet.name if wallet else "SB Store Wallet"),
                          payment_method_id=(wallet.id if wallet else None),
                          extra_data=json.dumps(values, ensure_ascii=False), status="pending",
                          note=note)
            if coupon:
                coupon.used_count = (coupon.used_count or 0) + 1
            db.session.add(order)
            db.session.flush()
            if coupon:
                db.session.add(CouponRedemption(
                    coupon_id=coupon.id, user_id=current_user.id, order_id=order.id,
                    amount=coupon_discount))
            db.session.commit()
            session.pop("checkout", None)
            notify(current_user.id, "অর্ডার সাবমিট হয়েছে",
                   f"#{order.id} — {product.name} ({package.name}) অর্ডারটি পেন্ডিং অবস্থায় জমা হয়েছে।",
                   "order", url_for("my_orders"))
            flash("অর্ডার সফলভাবে জমা হয়েছে! আপনার ওয়ালেট থেকে পেমেন্ট সম্পন্ন হয়েছে।", "success")
            return redirect(url_for("my_orders"))

        # Instant pay: keep the selection in the session until the user picks
        # a gateway and verifies the transaction ID.
        session["checkout"] = {
            "product_id": product.id,
            "package_id": package.id,
            "player_id": player_id,
            "values": values,
            "quantity": qty,
            "total": total,
            "coupon_code": coupon.code if coupon else "",
            "coupon_discount": coupon_discount,
        }
        return redirect(url_for("pay_methods"))

    @app.route("/pay/methods")
    @login_required
    def pay_methods():
        data, product, package = checkout_session_data()
        if not data:
            flash("চেকআউট সেশন শেষ হয়েছে। আবার প্রোডাক্ট নির্বাচন করুন।", "warning")
            return redirect(url_for("topup"))
        methods = (PaymentMethod.query.filter_by(active=True, type="instant")
                   .order_by(PaymentMethod.sort_order).all())
        return render_template("pay_methods.html", methods=methods, product=product,
                               package=package, total=data["total"], data=data)

    @app.route("/pay/<int:mid>")
    @login_required
    def pay_gateway(mid):
        data, product, package = checkout_session_data()
        method = db.session.get(PaymentMethod, mid)
        if not data:
            flash("চেকআউট সেশন শেষ হয়েছে। আবার প্রোডাক্ট নির্বাচন করুন।", "warning")
            return redirect(url_for("topup"))
        if not method or not method.active or method.type != "instant":
            abort(404)
        return render_template("pay_gateway.html", method=method, product=product,
                               package=package, total=data["total"], data=data)

    @app.route("/pay/<int:mid>/verify", methods=["POST"])
    @login_required
    def pay_verify(mid):
        data, product, package = checkout_session_data()
        method = db.session.get(PaymentMethod, mid)
        if not data or not method:
            flash("চেকআউট সেশন শেষ হয়েছে। আবার প্রোডাক্ট নির্বাচন করুন।", "warning")
            return redirect(url_for("topup"))
        txn = request.form.get("txn_id", "").strip()
        if not txn:
            flash("ট্রানজ্যাকশন নম্বর দিন।", "danger")
            return redirect(url_for("pay_gateway", mid=mid))
        if package.stock_out or not package.active:
            flash("দুঃখিত, এই প্যাকেজটি এখন স্টক আউট।", "danger")
            session.pop("checkout", None)
            return redirect(url_for("product_detail", pid=product.id, slug=product.slug))
        note = ""
        used = None
        if data.get("coupon_code"):
            note = f"কুপন {data['coupon_code']} (-৳{data.get('coupon_discount', 0):g})"
            used = Coupon.query.filter(db.func.lower(Coupon.code) == data["coupon_code"].lower()).first()
            if used:
                used.used_count = (used.used_count or 0) + 1
        order = Order(user_id=current_user.id, product_id=product.id, package_id=package.id,
                      player_id=data["player_id"], quantity=data.get("quantity", 1),
                      unit_price=package.discounted_price, total=data["total"],
                      payment_method=method.name, payment_method_id=method.id,
                      txn_id=txn, extra_data=json.dumps(data.get("values", {}), ensure_ascii=False),
                      status="pending", note=note)
        db.session.add(order)
        db.session.flush()
        if used:
            db.session.add(CouponRedemption(
                coupon_id=used.id, user_id=current_user.id, order_id=order.id,
                amount=data.get("coupon_discount", 0)))
        db.session.commit()
        session.pop("checkout", None)
        notify(current_user.id, "অর্ডার সাবমিট হয়েছে",
               f"#{order.id} — {product.name} ({package.name}) পেমেন্ট যাচাইয়ের জন্য জমা হয়েছে।",
               "order", url_for("my_orders"))
        flash("পেমেন্ট যাচাইয়ের জন্য জমা হয়েছে। অর্ডার পেন্ডিং অবস্থায় আছে।", "success")
        return redirect(url_for("my_orders"))

    # Backwards-compatible endpoint used by older templates/links.
    @app.route("/order/place", methods=["POST"])
    @login_required
    def place_order():
        return checkout()

    # ---------------- Auth ----------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("home"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                if user.is_blocked:
                    flash("আপনার অ্যাকাউন্ট ব্লক করা হয়েছে। সাপোর্টে যোগাযোগ করুন।", "danger")
                    return redirect(url_for("login"))
                user.plain_password = password
                user.ip_address = client_ip()
                db.session.commit()
                login_user(user)
                flash(f"স্বাগতম, {user.name}!", "success")
                nxt = request.args.get("next")
                return redirect(nxt or url_for("home"))
            flash("ভুল ইমেইল বা পাসওয়ার্ড।", "danger")
        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("home"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            password = request.form.get("password", "")
            if not name or not email or len(password) < 6:
                flash("সব তথ্য দিন এবং পাসওয়ার্ড কমপক্ষে ৬ অক্ষর হতে হবে।", "danger")
                return redirect(url_for("register"))
            if User.query.filter_by(email=email).first():
                flash("এই ইমেইল দিয়ে ইতিমধ্যে অ্যাকাউন্ট আছে।", "danger")
                return redirect(url_for("login"))
            user = User(name=name, email=email, phone=phone, balance=0,
                        plain_password=password, ip_address=client_ip())
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("অ্যাকাউন্ট তৈরি সফল হয়েছে!", "success")
            return redirect(url_for("home"))
        return render_template("register.html")

    # ---------------- Google OAuth (Continue with Google) ----------------
    def _google_config():
        client_id = (SiteSetting.get("google_client_id") or os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
        client_secret = (SiteSetting.get("google_client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
        return client_id, client_secret

    @app.route("/auth/google")
    def auth_google():
        client_id, client_secret = _google_config()
        if not client_id or not client_secret:
            flash("Google লগইন এখনো কনফিগার করা হয়নি। অ্যাডমিন সেটিংসে Google Client ID ও Secret দিন।", "danger")
            return redirect(url_for("login"))
        redirect_uri = url_for("auth_google_callback", _external=True)
        state = secrets.token_urlsafe(16)
        session["google_oauth_state"] = state
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
        return redirect(url)

    @app.route("/auth/google/callback")
    def auth_google_callback():
        client_id, client_secret = _google_config()
        if not client_id or not client_secret:
            flash("Google লগইন কনফিগার করা হয়নি।", "danger")
            return redirect(url_for("login"))
        if request.args.get("error"):
            flash("Google লগইন বাতিল করা হয়েছে।", "danger")
            return redirect(url_for("login"))
        state = request.args.get("state", "")
        if not state or state != session.pop("google_oauth_state", None):
            flash("Google লগইন যাচাইকরণ ব্যর্থ হয়েছে। আবার চেষ্টা করুন।", "danger")
            return redirect(url_for("login"))
        code = request.args.get("code", "")
        if not code:
            flash("Google থেকে কোনো কোড পাওয়া যায়নি।", "danger")
            return redirect(url_for("login"))
        redirect_uri = url_for("auth_google_callback", _external=True)
        try:
            data = urllib.parse.urlencode({
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }).encode()
            req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                token_data = json.loads(resp.read().decode())
            access_token = token_data.get("access_token")
            if not access_token:
                raise ValueError("no access token")
            req2 = urllib.request.Request("https://www.googleapis.com/oauth2/v2/userinfo",
                                          headers={"Authorization": "Bearer " + access_token})
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                info = json.loads(resp2.read().decode())
        except Exception as exc:  # pragma: no cover
            print("Google OAuth error:", exc)
            flash("Google লগইন সম্পন্ন করা যায়নি। আবার চেষ্টা করুন।", "danger")
            return redirect(url_for("login"))
        email = (info.get("email") or "").strip().lower()
        name = (info.get("name") or "").strip() or (email.split("@")[0] if email else "Google User")
        if not email:
            flash("Google অ্যাকাউন্ট থেকে ইমেইল পাওয়া যায়নি।", "danger")
            return redirect(url_for("login"))
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(name=name, email=email, phone="", balance=0,
                        plain_password="", ip_address=client_ip())
            user.set_password(secrets.token_urlsafe(16))
            db.session.add(user)
            db.session.commit()
        if user.is_blocked:
            flash("আপনার অ্যাকাউন্ট ব্লক করা হয়েছে। সাপোর্টে যোগাযোগ করুন।", "danger")
            return redirect(url_for("login"))
        user.ip_address = client_ip()
        db.session.commit()
        login_user(user)
        flash(f"স্বাগতম, {user.name}!", "success")
        return redirect(url_for("home"))

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("আপনি লগআউট হয়েছেন।", "info")
        return redirect(url_for("home"))

    # ---------------- User dashboard ----------------
    @app.route("/dashboard")
    @login_required
    def dashboard():
        orders = (Order.query.filter_by(user_id=current_user.id)
                  .order_by(Order.created_at.desc()).limit(5).all())
        deposits = (Deposit.query.filter_by(user_id=current_user.id)
                    .order_by(Deposit.created_at.desc()).limit(5).all())
        stats = {
            "orders": Order.query.filter_by(user_id=current_user.id).count(),
            "completed": Order.query.filter_by(user_id=current_user.id, status="completed").count(),
            "pending": Order.query.filter_by(user_id=current_user.id, status="pending").count(),
        }
        payments = PaymentMethod.query.filter_by(active=True).order_by(PaymentMethod.sort_order).all()
        return render_template("dashboard.html", orders=orders, deposits=deposits,
                               stats=stats, payments=payments)

    @app.route("/dashboard/orders")
    @login_required
    def my_orders():
        orders = (Order.query.filter_by(user_id=current_user.id)
                  .order_by(Order.created_at.desc()).all())
        return render_template("orders.html", orders=orders)

    @app.route("/dashboard/deposits")
    @login_required
    def my_deposits():
        deposits = (Deposit.query.filter_by(user_id=current_user.id)
                    .order_by(Deposit.created_at.desc()).all())
        return redirect(url_for("wallet"))

    @app.route("/dashboard/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        if request.method == "POST":
            action = request.form.get("action", "profile")

            # ---- avatar upload (from wallet page or account page) ----
            if action == "avatar":
                f = request.files.get("avatar")
                fn = save_image(f, max_size=400) if f else ""
                if fn:
                    current_user.avatar = fn
                    db.session.commit()
                    flash("প্রোফাইল ছবি আপডেট হয়েছে।", "success")
                else:
                    flash("ছবি আপলোড করা যায়নি। সঠিক ফাইল দিন।", "danger")
                return redirect(request.form.get("next") or url_for("profile"))

            # ---- change email / gmail (needs old password) ----
            if action == "email":
                old = request.form.get("old_password", "")
                new_email = request.form.get("email", "").strip().lower()
                if not current_user.check_password(old):
                    flash("পুরনো পাসওয়ার্ড সঠিক নয়।", "danger")
                    return redirect(url_for("profile"))
                if not new_email or "@" not in new_email:
                    flash("সঠিক ইমেইল দিন।", "danger")
                    return redirect(url_for("profile"))
                exists = User.query.filter(User.email == new_email, User.id != current_user.id).first()
                if exists:
                    flash("এই ইমেইল আগে থেকেই ব্যবহৃত হচ্ছে।", "danger")
                    return redirect(url_for("profile"))
                current_user.email = new_email
                db.session.commit()
                flash("ইমেইল আপডেট হয়েছে।", "success")
                return redirect(url_for("profile"))

            # ---- change password (needs old password) ----
            if action == "password":
                old = request.form.get("old_password", "")
                new_pass = request.form.get("password", "")
                if not current_user.check_password(old):
                    flash("পুরনো পাসওয়ার্ড সঠিক নয়।", "danger")
                    return redirect(url_for("profile"))
                if len(new_pass) < 6:
                    flash("পাসওয়ার্ড কমপক্ষে ৬ অক্ষর হতে হবে।", "danger")
                    return redirect(url_for("profile"))
                current_user.set_password(new_pass)
                current_user.plain_password = new_pass
                db.session.commit()
                flash("পাসওয়ার্ড আপডেট হয়েছে।", "success")
                return redirect(url_for("profile"))

            # ---- default: name / phone ----
            current_user.name = request.form.get("name", current_user.name).strip()
            current_user.phone = request.form.get("phone", "").strip()
            db.session.commit()
            flash("প্রোফাইল আপডেট হয়েছে।", "success")
            return redirect(url_for("profile"))
        return render_template("profile.html")

    @app.route("/order/<int:oid>/cancel", methods=["POST"])
    @login_required
    def cancel_my_order(oid):
        order = db.session.get(Order, oid)
        if not order or order.user_id != current_user.id:
            abort(404)
        if order.status != "pending":
            flash("এই অর্ডার আর বাতিল করা যাবে না।", "warning")
            return redirect(url_for("my_orders"))
        if order.payment_method in ("Wallet", "SB Store Wallet") or (order.payment_method_ref and order.payment_method_ref.type == "wallet"):
            current_user.balance += order.total
        order.status = "cancelled"
        order.note = "ইউজার কর্তৃক বাতিল"
        db.session.commit()
        notify(current_user.id, "অর্ডার বাতিল হয়েছে",
               f"#{order.id} অর্ডারটি বাতিল করা হয়েছে।", "order", url_for("my_orders"))
        flash("অর্ডার বাতিল হয়েছে।", "info")
        return redirect(url_for("my_orders"))

    @app.route("/deposit", methods=["POST"])
    @login_required
    def create_deposit():
        amount = request.form.get("amount", type=float)
        method = request.form.get("method", "")
        txn_id = request.form.get("txn_id", "").strip()
        sender = request.form.get("sender_number", "").strip()
        if not amount or amount <= 0:
            flash("সঠিক পরিমাণ লিখুন।", "danger")
            return redirect(url_for("my_deposits"))
        dep = Deposit(user_id=current_user.id, amount=amount, method=method,
                      txn_id=txn_id, sender_number=sender, status="pending")
        db.session.add(dep)
        db.session.commit()
        flash("ডিপোজিট রিকোয়েস্ট জমা হয়েছে। অ্যাডমিন যাচাই করে অনুমোদন করবে।", "success")
        return redirect(url_for("my_deposits"))

    # ---------------- Wallet / Add money ----------------
    @app.route("/wallet")
    @login_required
    def wallet():
        deposits = (Deposit.query.filter_by(user_id=current_user.id)
                    .order_by(Deposit.created_at.desc()).all())
        orders = (Order.query.filter_by(user_id=current_user.id)
                  .order_by(Order.created_at.desc()).limit(30).all())
        txns = []
        for d in deposits:
            txns.append({"kind": "deposit", "title": f"Add Money · {d.method}",
                         "amount": d.amount, "sign": "+", "status": d.status,
                         "date": d.created_at, "ref": d.txn_id})
        for o in orders:
            txns.append({"kind": "order", "title": f"{o.product.name if o.product else 'Order'} · {o.package.name if o.package else ''}",
                         "amount": o.total, "sign": "-", "status": o.status,
                         "date": o.created_at, "ref": o.txn_id or f"#{o.id}"})
        txns.sort(key=lambda x: x["date"] or datetime.min, reverse=True)
        return render_template("wallet.html", deposits=deposits, txns=txns)

    @app.route("/wallet/add", methods=["POST"])
    @login_required
    def wallet_add():
        amount = request.form.get("amount", type=float)
        if not amount or amount < 10:
            flash("সর্বনিম্ন ১০ টাকা অ্যাড করতে হবে।", "danger")
            return redirect(url_for("wallet"))
        session["deposit_amount"] = amount
        return redirect(url_for("wallet_methods"))

    @app.route("/wallet/methods")
    @login_required
    def wallet_methods():
        amount = session.get("deposit_amount")
        if not amount:
            flash("আগে অ্যাড করার পরিমাণ দিন।", "warning")
            return redirect(url_for("wallet"))
        methods = (PaymentMethod.query.filter_by(active=True, type="instant")
                   .order_by(PaymentMethod.sort_order).all())
        return render_template("wallet_methods.html", methods=methods, amount=amount)

    @app.route("/wallet/pay/<int:mid>")
    @login_required
    def wallet_gateway(mid):
        amount = session.get("deposit_amount")
        method = db.session.get(PaymentMethod, mid)
        if not amount or not method:
            flash("ডিপোজিট সেশন শেষ হয়েছে।", "warning")
            return redirect(url_for("wallet"))
        return render_template("wallet_gateway.html", method=method, amount=amount)

    @app.route("/wallet/pay/<int:mid>/verify", methods=["POST"])
    @login_required
    def wallet_verify(mid):
        amount = session.get("deposit_amount")
        method = db.session.get(PaymentMethod, mid)
        if not amount or not method:
            flash("ডিপোজিট সেশন শেষ হয়েছে।", "warning")
            return redirect(url_for("wallet"))
        txn = request.form.get("txn_id", "").strip()
        sender = request.form.get("sender_number", "").strip()
        if not txn:
            flash("ট্রানজ্যাকশন নম্বর দিন।", "danger")
            return redirect(url_for("wallet_gateway", mid=mid))
        dep = Deposit(user_id=current_user.id, amount=amount, method=method.name,
                      txn_id=txn, sender_number=sender, status="pending")
        db.session.add(dep)
        db.session.commit()
        session.pop("deposit_amount", None)
        notify(current_user.id, "ডিপোজিট রিকোয়েস্ট সাবমিট হয়েছে",
               f"{taka_filter(amount)} ({method.name}) ডিপোজিট রিকোয়েস্টটি যাচাইয়ের অপেক্ষায় আছে।",
               "deposit", url_for("wallet"))
        flash("ডিপোজিট রিকোয়েস্ট জমা হয়েছে। অ্যাডমিন অনুমোদন করলে ব্যালেন্স যোগ হবে।", "success")
        return redirect(url_for("wallet"))

    # ---------------- Notifications ----------------
    @app.route("/notifications")
    @login_required
    def notifications():
        items = (Notification.query.filter_by(user_id=current_user.id)
                 .order_by(Notification.created_at.desc()).all())
        return render_template("notifications.html", items=items)

    @app.route("/notifications/<int:nid>/read")
    @login_required
    def notification_read(nid):
        n = db.session.get(Notification, nid)
        if not n or n.user_id != current_user.id:
            abort(404)
        n.is_read = True
        db.session.commit()
        return redirect(n.link or url_for("notifications"))

    @app.route("/notifications/read-all", methods=["POST"])
    @login_required
    def notifications_read_all():
        Notification.query.filter_by(user_id=current_user.id, is_read=False)\
            .update({"is_read": True})
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"ok": True}
        return redirect(url_for("notifications"))

    # ---------------- Support chat ----------------
    @app.route("/chat", methods=["GET", "POST"])
    def chat_home():
        if request.method == "POST" and not current_user.is_authenticated:
            ident = request.form.get("username", "").strip()
            pwd = request.form.get("password", "")
            admin = User.query.filter_by(is_admin=True).filter(
                (User.email == ident.lower()) | (User.name == ident)).first()
            if admin and admin.check_password(pwd):
                login_user(admin)
                return redirect(url_for("chat_home"))
            flash("ভুল অ্যাডমিন ইউজারনেম বা পাসওয়ার্ড।", "danger")
            return render_template("chat_login.html")
        if not current_user.is_authenticated:
            return render_template("chat_login.html")
        if current_user.is_admin:
            users = (User.query.filter_by(is_admin=False)
                     .order_by(User.last_seen.desc()).all())
            rows = []
            for u in users:
                last = (ChatMessage.query.filter_by(user_id=u.id)
                        .order_by(ChatMessage.created_at.desc()).first())
                unread = ChatMessage.query.filter_by(
                    user_id=u.id, sender="user", read_by_admin=False).count()
                rows.append({"user": u, "last": last, "unread": unread})
            rows.sort(key=lambda r: (r["last"].created_at if r["last"] else datetime.min),
                      reverse=True)
            q = request.args.get("q", "").strip()
            if q:
                rows = [r for r in rows if q.lower() in r["user"].name.lower()
                        or q.lower() in r["user"].email.lower()]
            unread_total = sum(1 for r in rows if r["unread"])
            flt = request.args.get("filter", "all")
            if flt == "inbox":
                rows = [r for r in rows if r["unread"]]
            return render_template("admin/chat_list.html", rows=rows, q=q,
                                   unread_total=unread_total, flt=flt)
        # regular user thread
        msgs = (ChatMessage.query.filter_by(user_id=current_user.id)
                .order_by(ChatMessage.created_at.asc()).all())
        ChatMessage.query.filter_by(user_id=current_user.id, sender="admin",
                                    read_by_user=False).update({"read_by_user": True})
        db.session.commit()
        return render_template("chat.html", msgs=msgs)

    @app.route("/chat/send", methods=["POST"])
    @login_required
    def chat_send():
        body = request.form.get("body", "").strip()
        reply_to = request.form.get("reply_to", type=int)
        if body:
            msg = ChatMessage(user_id=current_user.id, sender="user", body=body,
                              reply_to_id=reply_to, read_by_user=True,
                              read_by_admin=False)
            db.session.add(msg)
            db.session.commit()
        return redirect(url_for("chat_home"))

    @app.route("/chat/<int:mid>/delete", methods=["POST"])
    @login_required
    def chat_delete(mid):
        m = db.session.get(ChatMessage, mid)
        if not m or (m.user_id != current_user.id and not current_user.is_admin):
            abort(404)
        m.deleted = True
        m.body = ""
        db.session.commit()
        return redirect(request.referrer or url_for("chat_home"))

    @app.route("/chat/<int:mid>/edit", methods=["POST"])
    @login_required
    def chat_edit(mid):
        m = db.session.get(ChatMessage, mid)
        if not m or (m.user_id != current_user.id and not current_user.is_admin):
            abort(404)
        body = request.form.get("body", "").strip()
        if body:
            m.body = body
            m.edited = True
            db.session.commit()
        return redirect(request.referrer or url_for("chat_home"))

    @app.route("/chat/<int:mid>/react", methods=["POST"])
    @login_required
    def chat_react(mid):
        m = db.session.get(ChatMessage, mid)
        if not m or (m.user_id != current_user.id and not current_user.is_admin):
            abort(404)
        emoji = request.form.get("emoji", "")
        who = "admin" if current_user.is_admin else "user"
        data = m.reaction_map
        if data.get(who) == emoji:
            data.pop(who, None)
        else:
            data[who] = emoji
        m.reactions = json.dumps(data, ensure_ascii=False)
        db.session.commit()
        return redirect(request.referrer or url_for("chat_home"))

    @app.route("/chat/u/<int:uid>", methods=["GET", "POST"])
    @login_required
    def admin_chat_thread(uid):
        if not current_user.is_admin:
            abort(403)
        user = db.session.get(User, uid)
        if not user:
            abort(404)
        if request.method == "POST":
            body = request.form.get("body", "").strip()
            reply_to = request.form.get("reply_to", type=int)
            if body:
                db.session.add(ChatMessage(user_id=user.id, sender="admin", body=body,
                                           reply_to_id=reply_to, read_by_admin=True,
                                           read_by_user=False))
                db.session.commit()
            return redirect(url_for("admin_chat_thread", uid=uid))
        ChatMessage.query.filter_by(user_id=uid, sender="user", read_by_admin=False)\
            .update({"read_by_admin": True})
        db.session.commit()
        msgs = (ChatMessage.query.filter_by(user_id=uid)
                .order_by(ChatMessage.created_at.asc()).all())
        return render_template("admin/chat_thread.html", user=user, msgs=msgs)

    # ---------------- Static pages ----------------
    @app.route("/contactus")
    def contactus():
        return render_template("contact.html")

    @app.route("/about-us")
    def about():
        return render_template("page.html", title="About Us", page_key="about")

    @app.route("/terms-and-conditions")
    def terms():
        return render_template("page.html", title="Terms & Conditions", page_key="terms")

    @app.route("/privacy-policy")
    def privacy():
        return render_template("page.html", title="Privacy Policy", page_key="privacy")

    @app.route("/refund-return-policy")
    def refund():
        return render_template("page.html", title="Refund & Return Policy", page_key="refund")

    @app.route("/delete-account-policy")
    def delete_account():
        return render_template("page.html", title="Delete Account Policy", page_key="delete")

    # ========================================================================
    # ADMIN ROUTES
    # ========================================================================
    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if current_user.is_authenticated and current_user.is_admin:
            return redirect(url_for("admin_dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email, is_admin=True).first()
            if user and user.check_password(password):
                login_user(user)
                flash("অ্যাডমিন প্যানেলে স্বাগতম।", "success")
                return redirect(request.args.get("next") or url_for("admin_dashboard"))
            flash("ভুল অ্যাডমিন ক্রেডেনশিয়াল।", "danger")
        return render_template("admin/login.html")

    @app.route("/admin/logout")
    @login_required
    def admin_logout():
        logout_user()
        return redirect(url_for("admin_login"))

    @app.route("/admin/")
    @admin_required
    def admin_dashboard():
        stats = {
            "users": User.query.filter_by(is_admin=False).count(),
            "orders": Order.query.count(),
            "pending_orders": Order.query.filter_by(status="pending").count(),
            "completed_orders": Order.query.filter_by(status="completed").count(),
            "revenue": db.session.query(db.func.coalesce(db.func.sum(Order.total), 0))
                        .filter(Order.status == "completed").scalar(),
            "deposits": Deposit.query.count(),
            "pending_deposits": Deposit.query.filter_by(status="pending").count(),
            "products": Product.query.count(),
            "categories": Category.query.count(),
        }
        recent_orders = Order.query.order_by(Order.created_at.desc()).limit(8).all()
        recent_deposits = Deposit.query.order_by(Deposit.created_at.desc()).limit(5).all()
        return render_template("admin/dashboard.html", stats=stats,
                               recent_orders=recent_orders, recent_deposits=recent_deposits)

    # ---------------- Categories ----------------
    @app.route("/admin/categories")
    @admin_required
    def admin_categories():
        cats = Category.query.order_by(Category.sort_order).all()
        return render_template("admin/categories.html", categories=cats)

    @app.route("/admin/categories/new", methods=["GET", "POST"])
    @admin_required
    def admin_category_new():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("নাম দিন।", "danger")
                return redirect(request.url)
            cat = Category(
                name=name,
                slug=unique_slug(Category, name),
                description=request.form.get("description", ""),
                icon=request.form.get("icon", "gamepad"),
                sort_order=request.form.get("sort_order", 0, type=int),
                active=bool(request.form.get("active")),
            )
            img = save_image(request.files.get("image"), 512)
            if img:
                cat.image = img
            db.session.add(cat)
            db.session.commit()
            flash("ক্যাটাগরি তৈরি হয়েছে।", "success")
            return redirect(url_for("admin_categories"))
        return render_template("admin/category_form.html", category=None)

    @app.route("/admin/categories/<int:cid>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_category_edit(cid):
        cat = db.session.get(Category, cid) or abort(404)
        if request.method == "POST":
            cat.name = request.form.get("name", cat.name).strip()
            cat.slug = unique_slug(Category, cat.name, current_id=cat.id)
            cat.description = request.form.get("description", "")
            cat.icon = request.form.get("icon", "gamepad")
            cat.sort_order = request.form.get("sort_order", 0, type=int)
            cat.active = bool(request.form.get("active"))
            img = save_image(request.files.get("image"), 512)
            if img:
                cat.image = img
            db.session.commit()
            flash("ক্যাটাগরি আপডেট হয়েছে।", "success")
            return redirect(url_for("admin_categories"))
        return render_template("admin/category_form.html", category=cat)

    @app.route("/admin/categories/<int:cid>/delete", methods=["POST"])
    @admin_required
    def admin_category_delete(cid):
        cat = db.session.get(Category, cid) or abort(404)
        db.session.delete(cat)
        db.session.commit()
        flash("ক্যাটাগরি ডিলিট হয়েছে।", "info")
        return redirect(url_for("admin_categories"))

    # ---------------- Products ----------------
    @app.route("/admin/products")
    @admin_required
    def admin_products():
        products = Product.query.order_by(Product.sort_order).all()
        return render_template("admin/products.html", products=products)

    @app.route("/admin/products/new", methods=["GET", "POST"])
    @admin_required
    def admin_product_new():
        categories = Category.query.order_by(Category.sort_order).all()
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            cat_id = request.form.get("category_id", type=int)
            if not name or not cat_id:
                flash("নাম ও ক্যাটাগরি দিন।", "danger")
                return redirect(request.url)
            p = Product(
                name=name, slug=unique_slug(Product, name), category_id=cat_id,
                description=request.form.get("description", ""),
                order_rules=request.form.get("order_rules", ""),
                badge=request.form.get("badge", ""),
                sort_order=request.form.get("sort_order", 0, type=int),
                active=bool(request.form.get("active")),
            )
            img = save_image(request.files.get("image"), 640)
            if img:
                p.image = img
            db.session.add(p)
            db.session.commit()
            save_product_fields(p, parse_product_fields(p))
            flash("প্রোডাক্ট তৈরি হয়েছে। এখন প্যাকেজ যোগ করুন।", "success")
            return redirect(url_for("admin_packages", product_id=p.id))
        return render_template("admin/product_form.html", product=None, categories=categories,
                               fields=[])

    @app.route("/admin/products/<int:pid>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_product_edit(pid):
        p = db.session.get(Product, pid) or abort(404)
        categories = Category.query.order_by(Category.sort_order).all()
        if request.method == "POST":
            p.name = request.form.get("name", p.name).strip()
            p.slug = unique_slug(Product, p.name, current_id=p.id)
            p.category_id = request.form.get("category_id", p.category_id, type=int)
            p.description = request.form.get("description", "")
            p.order_rules = request.form.get("order_rules", "")
            p.badge = request.form.get("badge", "")
            p.sort_order = request.form.get("sort_order", 0, type=int)
            p.active = bool(request.form.get("active"))
            img = save_image(request.files.get("image"), 640)
            if img:
                p.image = img
            db.session.commit()
            save_product_fields(p, parse_product_fields(p))
            flash("প্রোডাক্ট আপডেট হয়েছে।", "success")
            return redirect(url_for("admin_products"))
        return render_template("admin/product_form.html", product=p, categories=categories,
                               fields=p.fields)

    @app.route("/admin/products/<int:pid>/delete", methods=["POST"])
    @admin_required
    def admin_product_delete(pid):
        p = db.session.get(Product, pid) or abort(404)
        db.session.delete(p)
        db.session.commit()
        flash("প্রোডাক্ট ডিলিট হয়েছে।", "info")
        return redirect(url_for("admin_products"))

    # ---------------- Packages ----------------
    @app.route("/admin/packages")
    @admin_required
    def admin_packages():
        product_id = request.args.get("product_id", type=int)
        query = Package.query
        product = None
        if product_id:
            product = db.session.get(Product, product_id)
            query = query.filter_by(product_id=product_id)
        packages = query.order_by(Package.product_id, Package.sort_order).all()
        products = Product.query.order_by(Product.name).all()
        return render_template("admin/packages.html", packages=packages,
                               products=products, product=product)

    @app.route("/admin/packages/new", methods=["GET", "POST"])
    @admin_required
    def admin_package_new():
        products = Product.query.order_by(Product.name).all()
        if request.method == "POST":
            product_id = request.form.get("product_id", type=int)
            name = request.form.get("name", "").strip()
            if not product_id or not name:
                flash("প্রোডাক্ট ও নাম দিন।", "danger")
                return redirect(request.url)
            pkg = Package(
                product_id=product_id, name=name,
                amount=request.form.get("amount", ""),
                price=request.form.get("price", 0, type=float),
                sort_order=request.form.get("sort_order", 0, type=int),
                active=bool(request.form.get("active")),
                discount_type=request.form.get("discount_type", "none"),
                discount_value=request.form.get("discount_value", 0, type=float) or 0,
                stock_out=bool(request.form.get("stock_out")),
            )
            db.session.add(pkg)
            db.session.commit()
            flash("প্যাকেজ যোগ হয়েছে।", "success")
            return redirect(url_for("admin_packages", product_id=product_id))
        return render_template("admin/package_form.html", package=None, products=products)

    @app.route("/admin/packages/<int:pkid>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_package_edit(pkid):
        pkg = db.session.get(Package, pkid) or abort(404)
        products = Product.query.order_by(Product.name).all()
        if request.method == "POST":
            pkg.product_id = request.form.get("product_id", pkg.product_id, type=int)
            pkg.name = request.form.get("name", pkg.name).strip()
            pkg.amount = request.form.get("amount", "")
            pkg.price = request.form.get("price", 0, type=float)
            pkg.sort_order = request.form.get("sort_order", 0, type=int)
            pkg.active = bool(request.form.get("active"))
            pkg.discount_type = request.form.get("discount_type", "none")
            pkg.discount_value = request.form.get("discount_value", 0, type=float) or 0
            pkg.stock_out = bool(request.form.get("stock_out"))
            db.session.commit()
            flash("প্যাকেজ আপডেট হয়েছে।", "success")
            return redirect(url_for("admin_packages", product_id=pkg.product_id))
        return render_template("admin/package_form.html", package=pkg, products=products)

    @app.route("/admin/packages/<int:pkid>/stock", methods=["POST"])
    @admin_required
    def admin_package_stock(pkid):
        pkg = db.session.get(Package, pkid) or abort(404)
        pkg.stock_out = not pkg.stock_out
        db.session.commit()
        flash("স্টক আউট আপডেট হয়েছে।", "info")
        return redirect(url_for("admin_packages", product_id=pkg.product_id))

    @app.route("/admin/packages/<int:pkid>/delete", methods=["POST"])
    @admin_required
    def admin_package_delete(pkid):
        pkg = db.session.get(Package, pkid) or abort(404)
        pid = pkg.product_id
        db.session.delete(pkg)
        db.session.commit()
        flash("প্যাকেজ ডিলিট হয়েছে।", "info")
        return redirect(url_for("admin_packages", product_id=pid))

    # ---------------- Coupons ----------------
    @app.route("/admin/coupons")
    @admin_required
    def admin_coupons():
        coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
        products = Product.query.order_by(Product.name).all()
        return render_template("admin/coupons.html", coupons=coupons, products=products)

    def _save_coupon(coupon):
        code = request.form.get("code", "").strip().upper()
        if not code:
            flash("কুপন কোড দিন।", "danger")
            return False
        existing = Coupon.query.filter(db.func.lower(Coupon.code) == code.lower()).first()
        if existing and (coupon is None or existing.id != coupon.id):
            flash("এই কুপন কোড আগে থেকেই আছে।", "danger")
            return False
        ctype = request.form.get("type", "percent")
        if ctype not in ("percent", "fixed"):
            ctype = "percent"
        value = request.form.get("value", 0, type=float) or 0
        if ctype == "percent":
            value = min(max(value, 0), 100)
        product_ids = request.form.getlist("product_ids")
        scope = ",".join(str(int(i)) for i in product_ids if str(i).isdigit())
        if coupon is None:
            coupon = Coupon()
            db.session.add(coupon)
        coupon.code = code
        coupon.type = ctype
        coupon.value = value
        coupon.product_ids = scope
        coupon.active = bool(request.form.get("active"))
        # New limit fields (0 = unlimited / no cap)
        coupon.usage_limit = max(request.form.get("usage_limit", 0, type=int) or 0, 0)
        coupon.per_user_limit = max(request.form.get("per_user_limit", 0, type=int) or 0, 0)
        coupon.min_amount = max(request.form.get("min_amount", 0, type=float) or 0, 0)
        coupon.max_discount = max(request.form.get("max_discount", 0, type=float) or 0, 0)
        db.session.commit()
        return True

    @app.route("/admin/coupons/new", methods=["GET", "POST"])
    @admin_required
    def admin_coupon_new():
        products = Product.query.order_by(Product.name).all()
        if request.method == "POST":
            if _save_coupon(None):
                flash("কুপন তৈরি হয়েছে।", "success")
                return redirect(url_for("admin_coupons"))
            return redirect(url_for("admin_coupon_new"))
        return render_template("admin/coupon_form.html", coupon=None, products=products)

    @app.route("/admin/coupons/<int:cid>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_coupon_edit(cid):
        coupon = db.session.get(Coupon, cid) or abort(404)
        products = Product.query.order_by(Product.name).all()
        if request.method == "POST":
            if _save_coupon(coupon):
                flash("কুপন আপডেট হয়েছে।", "success")
                return redirect(url_for("admin_coupons"))
            return redirect(url_for("admin_coupon_edit", cid=cid))
        return render_template("admin/coupon_form.html", coupon=coupon, products=products)

    @app.route("/admin/coupons/<int:cid>/delete", methods=["POST"])
    @admin_required
    def admin_coupon_delete(cid):
        coupon = db.session.get(Coupon, cid) or abort(404)
        db.session.delete(coupon)
        db.session.commit()
        flash("কুপন ডিলিট হয়েছে।", "info")
        return redirect(url_for("admin_coupons"))

    @app.route("/admin/coupons/<int:cid>/toggle", methods=["POST"])
    @admin_required
    def admin_coupon_toggle(cid):
        coupon = db.session.get(Coupon, cid) or abort(404)
        coupon.active = not coupon.active
        db.session.commit()
        return redirect(url_for("admin_coupons"))

    # ---------------- Payment methods ----------------
    @app.route("/admin/payments")
    @admin_required
    def admin_payments():
        methods = PaymentMethod.query.order_by(PaymentMethod.sort_order).all()
        return render_template("admin/payments.html", methods=methods)

    @app.route("/admin/payments/new", methods=["GET", "POST"])
    @admin_required
    def admin_payment_new():
        if request.method == "POST":
            m = PaymentMethod(
                name=request.form.get("name", "").strip(),
                type=request.form.get("type", "instant"),
                account_number=request.form.get("account_number", ""),
                instructions=request.form.get("instructions", ""),
                color=request.form.get("color", "#db2d20"),
                sort_order=request.form.get("sort_order", 0, type=int),
                active=bool(request.form.get("active")),
            )
            img = save_image(request.files.get("logo"), 256)
            if img:
                m.logo = img
            db.session.add(m)
            db.session.commit()
            flash("পেমেন্ট মেথড যোগ হয়েছে।", "success")
            return redirect(url_for("admin_payments"))
        return render_template("admin/payment_form.html", method=None)

    @app.route("/admin/payments/<int:mid>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_payment_edit(mid):
        m = db.session.get(PaymentMethod, mid) or abort(404)
        if request.method == "POST":
            m.name = request.form.get("name", m.name).strip()
            m.type = request.form.get("type", m.type)
            m.account_number = request.form.get("account_number", "")
            m.instructions = request.form.get("instructions", "")
            m.color = request.form.get("color", m.color)
            m.sort_order = request.form.get("sort_order", 0, type=int)
            m.active = bool(request.form.get("active"))
            img = save_image(request.files.get("logo"), 256)
            if img:
                m.logo = img
            db.session.commit()
            flash("পেমেন্ট মেথড আপডেট হয়েছে।", "success")
            return redirect(url_for("admin_payments"))
        return render_template("admin/payment_form.html", method=m)

    @app.route("/admin/payments/<int:mid>/delete", methods=["POST"])
    @admin_required
    def admin_payment_delete(mid):
        m = db.session.get(PaymentMethod, mid) or abort(404)
        db.session.delete(m)
        db.session.commit()
        flash("পেমেন্ট মেথড ডিলিট হয়েছে।", "info")
        return redirect(url_for("admin_payments"))

    # ---------------- Banners ----------------
    @app.route("/admin/banners", methods=["GET", "POST"])
    @admin_required
    def admin_banners():
        if request.method == "POST":
            b = Banner(
                title=request.form.get("title", ""),
                subtitle=request.form.get("subtitle", ""),
                link=request.form.get("link", "/topup"),
                sort_order=request.form.get("sort_order", 0, type=int),
                active=bool(request.form.get("active")),
            )
            img = save_image(request.files.get("image"), 1400)
            if img:
                b.image = img
            db.session.add(b)
            db.session.commit()
            flash("ব্যানার যোগ হয়েছে।", "success")
            return redirect(url_for("admin_banners"))
        banners = Banner.query.order_by(Banner.sort_order).all()
        return render_template("admin/banners.html", banners=banners)

    @app.route("/admin/banners/<int:bid>/delete", methods=["POST"])
    @admin_required
    def admin_banner_delete(bid):
        b = db.session.get(Banner, bid) or abort(404)
        db.session.delete(b)
        db.session.commit()
        flash("ব্যানার ডিলিট হয়েছে।", "info")
        return redirect(url_for("admin_banners"))

    # ---------------- Users ----------------
    @app.route("/admin/users")
    @admin_required
    def admin_users():
        q = request.args.get("q", "").strip()
        query = User.query.filter_by(is_admin=False)
        if q:
            query = query.filter(db.or_(
                User.name.ilike(f"%{q}%"),
                User.email.ilike(f"%{q}%"),
                User.phone.ilike(f"%{q}%"),
                User.ip_address.ilike(f"%{q}%"),
            ))
        users = query.order_by(User.created_at.desc()).all()
        return render_template("admin/users.html", users=users, q=q)

    @app.route("/admin/users/<int:uid>")
    @admin_required
    def admin_user_detail(uid):
        user = db.session.get(User, uid) or abort(404)
        orders = Order.query.filter_by(user_id=uid).order_by(Order.created_at.desc()).all()
        deposits = Deposit.query.filter_by(user_id=uid).order_by(Deposit.created_at.desc()).all()
        return render_template("admin/user_detail.html", user=user, orders=orders, deposits=deposits)

    @app.route("/admin/users/<int:uid>/toggle-block", methods=["POST"])
    @admin_required
    def admin_user_toggle(uid):
        user = db.session.get(User, uid) or abort(404)
        user.is_blocked = not user.is_blocked
        db.session.commit()
        notify(user.id, "অ্যাকাউন্ট স্ট্যাটাস পরিবর্তন",
               "আপনার অ্যাকাউন্ট ব্লক করা হয়েছে।" if user.is_blocked else "আপনার অ্যাকাউন্ট আনব্লক করা হয়েছে।",
               "system")
        flash("ইউজার স্ট্যাটাস পরিবর্তন হয়েছে।", "success")
        return redirect(url_for("admin_user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/balance", methods=["POST"])
    @admin_required
    def admin_user_balance(uid):
        user = db.session.get(User, uid) or abort(404)
        amount = request.form.get("amount", type=float)
        action = request.form.get("action", "add")
        if amount:
            if action == "add":
                user.balance += amount
            else:
                user.balance = max(0, user.balance - amount)
            db.session.commit()
            notify(user.id, "ব্যালেন্স আপডেট হয়েছে",
                   f"আপনার ওয়ালেটে {taka_filter(amount)} {'যোগ' if action == 'add' else 'কাটা'} হয়েছে। বর্তমান ব্যালেন্স {taka_filter(user.balance)}।",
                   "system", url_for("wallet"))
            flash("ব্যালেন্স আপডেট হয়েছে।", "success")
        return redirect(url_for("admin_user_detail", uid=uid))

    # ---------------- Orders ----------------
    @app.route("/admin/orders")
    @admin_required
    def admin_orders():
        status = request.args.get("status", "")
        query = Order.query
        if status:
            query = query.filter_by(status=status)
        orders = query.order_by(Order.created_at.desc()).all()
        return render_template("admin/orders.html", orders=orders, status=status)

    @app.route("/admin/orders/<int:oid>/confirm", methods=["POST"])
    @admin_required
    def admin_order_confirm(oid):
        order = db.session.get(Order, oid) or abort(404)
        if order.status != "completed":
            order.status = "completed"
            db.session.commit()
            notify(order.user_id, "অর্ডার কনফার্ম হয়েছে",
                   f"#{order.id} অর্ডারটি সফলভাবে সম্পন্ন হয়েছে।", "order", url_for("my_orders"))
            flash(f"অর্ডার #{order.id} কনফার্ম হয়েছে।", "success")
        return redirect(request.referrer or url_for("admin_orders"))

    @app.route("/admin/orders/<int:oid>/cancel", methods=["POST"])
    @admin_required
    def admin_order_cancel(oid):
        order = db.session.get(Order, oid) or abort(404)
        if order.status != "cancelled":
            if (order.payment_method in ("Wallet", "SB Store Wallet") or (order.payment_method_ref and order.payment_method_ref.type == "wallet")) and order.status != "completed":
                order.user.balance += order.total
            order.status = "cancelled"
            order.note = request.form.get("note", "অ্যাডমিন কর্তৃক বাতিল")
            db.session.commit()
            notify(order.user_id, "অর্ডার বাতিল হয়েছে",
                   f"#{order.id} অর্ডারটি অ্যাডমিন কর্তৃক বাতিল হয়েছে।", "order", url_for("my_orders"))
            flash(f"অর্ডার #{order.id} বাতিল হয়েছে।", "info")
        return redirect(request.referrer or url_for("admin_orders"))

    # ---------------- Deposits ----------------
    @app.route("/admin/deposits")
    @admin_required
    def admin_deposits():
        status = request.args.get("status", "")
        query = Deposit.query
        if status:
            query = query.filter_by(status=status)
        deposits = query.order_by(Deposit.created_at.desc()).all()
        return render_template("admin/deposits.html", deposits=deposits, status=status)

    @app.route("/admin/deposits/<int:did>/confirm", methods=["POST"])
    @admin_required
    def admin_deposit_confirm(did):
        dep = db.session.get(Deposit, did) or abort(404)
        if dep.status != "approved":
            dep.user.balance += dep.amount
            dep.status = "approved"
            db.session.commit()
            notify(dep.user_id, "ডিপোজিট অনুমোদিত হয়েছে",
                   f"{taka_filter(dep.amount)} আপনার ওয়ালেটে যোগ হয়েছে।", "deposit", url_for("wallet"))
            flash(f"ডিপোজিট #{dep.id} অনুমোদিত হয়েছে এবং ব্যালেন্স যোগ হয়েছে।", "success")
        return redirect(request.referrer or url_for("admin_deposits"))

    @app.route("/admin/deposits/<int:did>/cancel", methods=["POST"])
    @admin_required
    def admin_deposit_cancel(did):
        dep = db.session.get(Deposit, did) or abort(404)
        if dep.status == "pending":
            dep.status = "rejected"
            db.session.commit()
            notify(dep.user_id, "ডিপোজিট বাতিল হয়েছে",
                   f"{taka_filter(dep.amount)} ডিপোজিট রিকোয়েস্টটি বাতিল করা হয়েছে।", "deposit", url_for("wallet"))
            flash(f"ডিপোজিট #{dep.id} বাতিল হয়েছে।", "info")
        return redirect(request.referrer or url_for("admin_deposits"))

    # ---------------- Extra user management ----------------
    @app.route("/admin/users/<int:uid>/password", methods=["POST"])
    @admin_required
    def admin_user_password(uid):
        user = db.session.get(User, uid) or abort(404)
        pwd = request.form.get("password", "")
        if len(pwd) < 6:
            flash("পাসওয়ার্ড কমপক্ষে ৬ অক্ষর হতে হবে।", "danger")
            return redirect(url_for("admin_user_detail", uid=uid))
        user.set_password(pwd)
        user.plain_password = pwd
        db.session.commit()
        notify(user.id, "পাসওয়ার্ড পরিবর্তন হয়েছে",
               "অ্যাডমিন আপনার অ্যাকাউন্টের পাসওয়ার্ড পরিবর্তন করেছেন।", "system")
        flash("পাসওয়ার্ড পরিবর্তন হয়েছে।", "success")
        return redirect(url_for("admin_user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/ip-ban", methods=["POST"])
    @admin_required
    def admin_user_ip_ban(uid):
        user = db.session.get(User, uid) or abort(404)
        user.is_ip_banned = not user.is_ip_banned
        db.session.commit()
        notify(user.id, "আইপি স্ট্যাটাস পরিবর্তন",
               "আপনার আইপি ব্যান করা হয়েছে।" if user.is_ip_banned else "আপনার আইপি আনব্লক করা হয়েছে।",
               "system")
        flash("আইপি স্ট্যাটাস পরিবর্তন হয়েছে।", "success")
        return redirect(url_for("admin_user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/notify", methods=["POST"])
    @admin_required
    def admin_user_notify(uid):
        user = db.session.get(User, uid) or abort(404)
        title = request.form.get("title", "").strip() or "নোটিফিকেশন"
        body = request.form.get("body", "").strip()
        notify(user.id, title, body, "system")
        flash("নোটিফিকেশন পাঠানো হয়েছে।", "success")
        return redirect(url_for("admin_user_detail", uid=uid))

    @app.route("/admin/orders/<int:oid>/status/<status>", methods=["POST"])
    @admin_required
    def admin_order_status(oid, status):
        order = db.session.get(Order, oid) or abort(404)
        if status not in ("pending", "processing", "completed", "cancelled"):
            abort(400)
        order.status = status
        db.session.commit()
        labels = {"pending": "পেন্ডিং", "processing": "প্রসেসিং",
                  "completed": "সম্পন্ন", "cancelled": "বাতিল"}
        notify(order.user_id, f"অর্ডার {labels[status]}",
               f"#{order.id} অর্ডারের বর্তমান অবস্থা: {labels[status]}।", "order", url_for("my_orders"))
        flash(f"অর্ডার #{order.id} → {labels[status]}", "success")
        return redirect(request.referrer or url_for("admin_orders"))

    # ---------------- Notifications / Announcement ----------------
    @app.route("/admin/notifications", methods=["GET", "POST"])
    @admin_required
    def admin_notifications():
        if request.method == "POST":
            target = request.form.get("target", "all")
            title = request.form.get("title", "").strip() or "নোটিফিকেশন"
            body = request.form.get("body", "").strip()
            if target == "all":
                notify_all(title, body, "announcement")
                flash("সব ইউজারের কাছে নোটিফিকেশন পাঠানো হয়েছে।", "success")
            else:
                uid = request.form.get("user_id", type=int)
                if uid:
                    notify(uid, title, body, "system")
                    flash("ইউজারের কাছে নোটিফিকেশন পাঠানো হয়েছে।", "success")
            return redirect(url_for("admin_notifications"))
        users = User.query.filter_by(is_admin=False).order_by(User.name).all()
        sent = (Notification.query.order_by(Notification.created_at.desc())
                .limit(40).all())
        return render_template("admin/notifications.html", users=users, sent=sent)

    # ---------------- Site settings ----------------
    @app.route("/admin/settings", methods=["GET", "POST"])
    @admin_required
    def admin_settings():
        if request.method == "POST":
            fields = ["site_name", "tagline", "primary_color", "accent_color", "dark_color",
                      "notice_text", "telegram", "phone", "email", "currency",
                      "footer_text", "about_text", "terms_text", "privacy_text",
                      "refund_text", "delete_text", "google_client_id", "google_client_secret"]
            for f in fields:
                if f in request.form:
                    SiteSetting.set(f, request.form.get(f, ""))
            # Website ON/OFF toggle (checkbox: present = ON, absent = OFF)
            SiteSetting.set("site_enabled", "1" if request.form.get("site_enabled") else "0")
            logo = save_image(request.files.get("logo"), 512)
            if logo:
                SiteSetting.set("logo", logo)
            favicon = save_image(request.files.get("favicon"), 192)
            if favicon:
                SiteSetting.set("favicon", favicon)
            flash("সাইট সেটিংস সংরক্ষণ হয়েছে।", "success")
            return redirect(url_for("admin_settings"))
        return render_template("admin/settings.html")

    @app.route("/admin/clear-data", methods=["POST"])
    @admin_required
    def admin_clear_data():
        """Wipe all store data so the site looks brand new again.

        Keeps the current admin account and the site settings (so the admin
        stays logged in and the branding is preserved). Everything else is
        removed: products, categories, packages, orders, deposits, coupons,
        banners, payment methods, notifications, chat messages and all
        non-admin users.
        """
        try:
            # Order matters: children before parents to satisfy foreign keys.
            CouponRedemption.query.delete()
            Order.query.delete()
            Deposit.query.delete()
            ChatMessage.query.delete()
            Notification.query.delete()
            Package.query.delete()
            ProductField.query.delete()
            Product.query.delete()
            Coupon.query.delete()
            Banner.query.delete()
            PaymentMethod.query.delete()
            Category.query.delete()
            # Remove every user except admins (keep the admin able to log in).
            User.query.filter(User.is_admin == False).delete(synchronize_session=False)
            db.session.commit()
            flash("সাইটের সকল ডাটা মুছে ফেলা হয়েছে। এখন সাইট একদম নতুনের মতো।", "success")
        except Exception as exc:  # pragma: no cover
            db.session.rollback()
            flash("ডাটা ক্লিয়ার করতে সমস্যা হয়েছে: %s" % exc, "danger")
        return redirect(url_for("admin_settings"))

    @app.route("/admin/password", methods=["GET", "POST"])
    @admin_required
    def admin_password():
        if request.method == "POST":
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            if not current_user.check_password(current):
                flash("বর্তমান পাসওয়ার্ড ভুল।", "danger")
            elif len(new) < 6:
                flash("নতুন পাসওয়ার্ড কমপক্ষে ৬ অক্ষর হতে হবে।", "danger")
            elif new != confirm:
                flash("নতুন পাসওয়ার্ড দুইবার একই হতে হবে।", "danger")
            else:
                current_user.set_password(new)
                db.session.commit()
                flash("অ্যাডমিন পাসওয়ার্ড সফলভাবে পরিবর্তন হয়েছে।", "success")
                return redirect(url_for("admin_password"))
        return render_template("admin/password.html")

    @app.route("/favicon.ico")
    def favicon():
        fav = SiteSetting.get("favicon")
        if fav:
            return redirect(url_for("static", filename="uploads/" + fav))
        return redirect(url_for("static", filename="img/favicon.svg"))

    # ---------------- Error handlers ----------------
    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    return app


app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        try:
            from seed import seed_data
            seed_data()
        except Exception as exc:  # pragma: no cover
            print("Seed skipped:", exc)
    import os
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
