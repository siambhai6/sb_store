from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(40), default="")
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    is_admin = db.Column(db.Boolean, default=False)
    is_blocked = db.Column(db.Boolean, default=False)
    avatar = db.Column(db.String(255), default="")
    ip_address = db.Column(db.String(64), default="")
    is_ip_banned = db.Column(db.Boolean, default=False)
    plain_password = db.Column(db.String(120), default="")
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    orders = db.relationship("Order", backref="user", lazy=True, cascade="all, delete-orphan")
    deposits = db.relationship("Deposit", backref="user", lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def total_spent(self):
        return sum(o.total for o in self.orders if o.status == "completed")

    def __repr__(self):
        return f"<User {self.email}>"


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(180), unique=True, nullable=False)
    description = db.Column(db.Text, default="")
    image = db.Column(db.String(255), default="")
    icon = db.Column(db.String(16), default="gamepad")
    sort_order = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    products = db.relationship("Product", backref="category", lazy=True, cascade="all, delete-orphan")


class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), unique=True, nullable=False)
    description = db.Column(db.Text, default="")
    # Per-product order rules / "how to order" text (one rule per line).
    order_rules = db.Column(db.Text, default="")
    image = db.Column(db.String(255), default="")
    badge = db.Column(db.String(60), default="")
    sort_order = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    packages = db.relationship("Package", backref="product", lazy=True, cascade="all, delete-orphan",
                               order_by="Package.sort_order")
    # Deleting a product also removes its orders so the admin delete action
    # never fails with a foreign-key / NOT NULL IntegrityError.
    orders = db.relationship("Order", backref="product", lazy=True,
                             cascade="all, delete-orphan")

    @property
    def min_price(self):
        prices = [p.discounted_price for p in self.packages if p.active and not p.stock_out]
        return min(prices) if prices else 0


class Package(db.Model):
    __tablename__ = "packages"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    amount = db.Column(db.String(80), default="")
    price = db.Column(db.Float, nullable=False, default=0)
    sort_order = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    # Discount: type is "none" / "percent" / "fixed"
    discount_type = db.Column(db.String(20), default="none")
    discount_value = db.Column(db.Float, default=0)
    stock_out = db.Column(db.Boolean, default=False)

    @property
    def has_discount(self):
        return (self.discount_type in ("percent", "fixed")
                and (self.discount_value or 0) > 0 and (self.price or 0) > 0)

    @property
    def discounted_price(self):
        price = self.price or 0
        if not self.has_discount:
            return price
        if self.discount_type == "percent":
            value = min(max(self.discount_value or 0, 0), 100)
            return round(max(price - price * value / 100.0, 0), 2)
        return round(max(price - (self.discount_value or 0), 0), 2)

    @property
    def discount_percent(self):
        """Whole-number percentage used for the small red label."""
        if not self.has_discount:
            return 0
        if self.discount_type == "percent":
            return int(round(min(max(self.discount_value or 0, 0), 100)))
        if not self.price:
            return 0
        return int(round((self.discount_value or 0) / self.price * 100))


class Coupon(db.Model):
    """A discount coupon the admin creates from the admin panel."""
    __tablename__ = "coupons"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False, index=True)
    type = db.Column(db.String(20), default="percent")  # percent / fixed
    value = db.Column(db.Float, default=0)
    # Comma separated product ids. Empty string means "all products".
    product_ids = db.Column(db.Text, default="")
    active = db.Column(db.Boolean, default=True)
    used_count = db.Column(db.Integer, default=0)
    # 0 means unlimited. usage_limit = total number of times the coupon can be
    # used; per_user_limit = how many times one user may use it.
    usage_limit = db.Column(db.Integer, default=0)
    per_user_limit = db.Column(db.Integer, default=0)
    # Minimum order amount required before the coupon can be applied.
    min_amount = db.Column(db.Float, default=0)
    # Maximum discount the coupon may give (0 = no cap).
    max_discount = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def scope_ids(self):
        raw = (self.product_ids or "").strip()
        if not raw:
            return []
        ids = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        return ids

    @property
    def applies_to_all(self):
        return not self.scope_ids

    def applies_to(self, product_id):
        ids = self.scope_ids
        return (not ids) or (product_id in ids)

    def discount_for(self, amount):
        amount = float(amount or 0)
        if self.type == "percent":
            value = min(max(self.value or 0, 0), 100)
            discount = round(amount * value / 100.0, 2)
        else:
            discount = round(min(max(self.value or 0, 0), amount), 2)
        cap = float(self.max_discount or 0)
        if cap > 0:
            discount = min(discount, cap)
        return round(max(discount, 0), 2)

    @property
    def label(self):
        if self.type == "percent":
            return f"{int(round(self.value or 0))}% OFF"
        return f"৳{int(round(self.value or 0))} OFF"

    @property
    def usage_left(self):
        if not self.usage_limit:
            return None
        return max(0, int(self.usage_limit) - int(self.used_count or 0))


class CouponRedemption(db.Model):
    """One row per successful coupon redemption, used for per-user limits."""
    __tablename__ = "coupon_redemptions"
    id = db.Column(db.Integer, primary_key=True)
    coupon_id = db.Column(db.Integer, db.ForeignKey("coupons.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"))
    amount = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    coupon = db.relationship("Coupon", backref=db.backref(
        "redemptions", lazy=True, cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref(
        "coupon_redemptions", lazy=True, cascade="all, delete-orphan"))


class ProductField(db.Model):
    """Extra input fields shown on a product's checkout form.

    Every product automatically gets one UID field (is_uid=True). The admin can
    rename it, make it optional, and add any number of additional fields.
    """
    __tablename__ = "product_fields"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    label = db.Column(db.String(160), nullable=False, default="Player ID / UID")
    placeholder = db.Column(db.String(200), default="")
    field_type = db.Column(db.String(20), default="text")  # text / number / tel / email
    required = db.Column(db.Boolean, default=True)
    is_uid = db.Column(db.Boolean, default=False)
    sort_order = db.Column(db.Integer, default=0)

    product = db.relationship("Product", backref=db.backref(
        "fields", lazy=True, cascade="all, delete-orphan",
        order_by="ProductField.sort_order"))


class PaymentMethod(db.Model):
    __tablename__ = "payment_methods"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    type = db.Column(db.String(20), default="instant")  # wallet / instant
    account_number = db.Column(db.String(80), default="")
    instructions = db.Column(db.Text, default="")
    logo = db.Column(db.String(255), default="")
    color = db.Column(db.String(20), default="#db2d20")
    sort_order = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    package_id = db.Column(db.Integer, db.ForeignKey("packages.id"))
    player_id = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Float, default=0)
    total = db.Column(db.Float, default=0)
    payment_method = db.Column(db.String(120), default="Wallet")
    payment_method_id = db.Column(db.Integer, db.ForeignKey("payment_methods.id"))
    txn_id = db.Column(db.String(160), default="")
    extra_data = db.Column(db.Text, default="")  # JSON of dynamic checkout fields
    status = db.Column(db.String(20), default="pending")  # pending / completed / cancelled
    note = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    package = db.relationship("Package")
    payment_method_ref = db.relationship("PaymentMethod")

    @property
    def extra_fields(self):
        import json
        try:
            return json.loads(self.extra_data or "{}")
        except (ValueError, TypeError):
            return {}

    @property
    def status_bn(self):
        return {"pending": "অপেক্ষমাণ", "completed": "Complete", "cancelled": "বাতিল"}.get(self.status, self.status)


class Deposit(db.Model):
    __tablename__ = "deposits"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    amount = db.Column(db.Float, default=0)
    method = db.Column(db.String(120), default="")
    txn_id = db.Column(db.String(160), default="")
    sender_number = db.Column(db.String(60), default="")
    status = db.Column(db.String(20), default="pending")  # pending / approved / rejected
    note = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def status_bn(self):
        return {"pending": "অপেক্ষমাণ", "approved": "অনুমোদিত", "rejected": "বাতিল"}.get(self.status, self.status)


class Banner(db.Model):
    __tablename__ = "banners"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), default="")
    subtitle = db.Column(db.String(255), default="")
    image = db.Column(db.String(255), default="")
    link = db.Column(db.String(255), default="/topup")
    sort_order = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)


class SiteSetting(db.Model):
    __tablename__ = "site_settings"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.Text, default="")

    @staticmethod
    def get(key, default=""):
        row = SiteSetting.query.filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = SiteSetting.query.filter_by(key=key).first()
        if row:
            row.value = value
        else:
            db.session.add(SiteSetting(key=key, value=value))
        db.session.commit()

    @staticmethod
    def all_dict():
        return {r.key: r.value for r in SiteSetting.query.all()}


class Notification(db.Model):
    """In-app notification shown in the header bell menu."""
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(200), default="")
    body = db.Column(db.Text, default="")
    kind = db.Column(db.String(30), default="system")  # order / deposit / announcement / system
    link = db.Column(db.String(255), default="")
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref(
        "notifications", lazy=True, cascade="all, delete-orphan",
        order_by="Notification.created_at.desc()"))


class ChatMessage(db.Model):
    """A message in the support chat between a user and the admin."""
    __tablename__ = "chat_messages"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    sender = db.Column(db.String(10), default="user")  # user / admin
    body = db.Column(db.Text, default="")
    image = db.Column(db.String(255), default="")
    reply_to_id = db.Column(db.Integer, db.ForeignKey("chat_messages.id"))
    reactions = db.Column(db.Text, default="")  # JSON {"user": "❤️", "admin": "👍"}
    edited = db.Column(db.Boolean, default=False)
    deleted = db.Column(db.Boolean, default=False)
    read_by_admin = db.Column(db.Boolean, default=False)
    read_by_user = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref(
        "chat_messages", lazy=True, cascade="all, delete-orphan"))
    reply_to = db.relationship("ChatMessage", remote_side=[id], foreign_keys=[reply_to_id])

    @property
    def reaction_map(self):
        import json
        try:
            return json.loads(self.reactions or "{}")
        except (ValueError, TypeError):
            return {}
