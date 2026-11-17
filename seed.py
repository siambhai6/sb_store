from datetime import datetime, timedelta

from models import (Banner, Category, Deposit, Order, Package, PaymentMethod,
                    Product, SiteSetting, User, db)


def seed_data():
    if User.query.first():
        return  # already seeded

    # ---------------- Settings ----------------
    defaults = {
        "site_name": "Topupjo",
        "tagline": "Games TopUp in Bangladesh — Fast, Safe & Reliable",
        "primary_color": "#db2d20",
        "accent_color": "#fbbf24",
        "dark_color": "#0f172a",
        "notice_text": "১৮ বছরের নিচে কেউ অর্ডার করবেন না! এই সাইট শুধুমাত্র প্রাপ্তবয়স্কদের জন্য। বাবা/মা বা ফ্যামিলির টাকা চুরি করে অর্ডার করলে তার বিরুদ্ধে ব্যবস্থা নেওয়া হবে!",
        "telegram": "https://t.me/topuplyapps",
        "phone": "01700-000000",
        "email": "support@topupjo.com",
        "currency": "৳",
        "footer_text": "আমাদের সাইট ১০০% ট্রাস্টেড, নিশ্চিন্তে নির্ভয়ে আমাদের এই সাইট থেকে আপনি ডায়মন্ড টপ-আপ করতে পারবেন।",
        "about_text": "Topupjo is a trusted game top-up platform in Bangladesh. We provide instant Free Fire diamonds, weekly/monthly memberships, UID top-up and exclusive offers at the best price with 24/7 support.",
        "terms_text": "By using Topupjo you agree to provide accurate Player ID / UID. We are not responsible for wrong IDs. Orders are processed within minutes. Abusive or fraudulent activity will result in account suspension.",
        "privacy_text": "We collect only the information needed to process your orders (name, email, phone, player ID). We never sell your data to third parties. Payments are handled through trusted local gateways.",
        "refund_text": "If an order cannot be delivered due to our fault, you are eligible for a full refund to your wallet. Wrong Player ID / UID provided by the customer is non-refundable. Contact support within 24 hours.",
        "delete_text": "You may request account deletion by contacting support. Your personal data will be removed within 7 working days, except records required for legal or accounting purposes.",
    }
    for k, v in defaults.items():
        db.session.add(SiteSetting(key=k, value=v))

    # ---------------- Users ----------------
    admin = User(name="Administrator", email="admin@topupjo.com", phone="01700000000",
                 balance=0, is_admin=True)
    admin.set_password("admin123")
    db.session.add(admin)

    # Fresh-install mode: only the admin account + site settings are created.
    # No demo users, categories, products, orders, deposits or banners — the
    # site owner adds everything themselves from the admin panel.
    db.session.commit()
    print("Fresh database ready. Admin login: admin@topupjo.com / admin123")
    return

    demo = User(name="Demo User", email="user@demo.com", phone="01800000000", balance=1000)
    demo.set_password("user123")
    db.session.add(demo)

    names = ["Ismail hossen", "Fahim Ahmed", "Sajib Khan", "Sayem Hossain", "Tuhin Uddin",
             "Jannat Islam", "Sakib MD", "Rakib Hasan", "Nusrat Jahan", "Arif Chowdhury"]
    demo_users = []
    for i, n in enumerate(names):
        u = User(name=n, email=f"user{i+1}@demo.com", phone=f"0180000000{i}",
                 balance=0)
        u.set_password("user123")
        db.session.add(u)
        demo_users.append(u)

    db.session.flush()

    # ---------------- Categories ----------------
    cats = {}
    cat_defs = [
        ("Free Fire Diamond", "free-fire-diamond", "gem", "Instant Free Fire diamond top-up with best price."),
        ("Membership", "membership", "crown", "Weekly & Monthly membership at unbeatable price."),
        ("Special Offers", "special-offers", "fire", "Hot offers, bonus top-up and big packages."),
        ("Lucky & Lottery", "lucky-lottery", "gift", "Lucky weekly, monthly and mystery box."),
        ("Free Diamond Apps", "free-diamond-apps", "phone", "Watch ads and earn free diamonds."),
    ]
    for i, (name, slug, icon, desc) in enumerate(cat_defs):
        c = Category(name=name, slug=slug, icon=icon, description=desc, sort_order=i)
        db.session.add(c)
        cats[slug] = c
    db.session.flush()

    # ---------------- Products + Packages ----------------
    def add_product(cat, name, slug, desc, badge, packages):
        p = Product(category_id=cat.id, name=name, slug=slug, description=desc,
                    badge=badge, sort_order=Product.query.count())
        db.session.add(p)
        db.session.flush()
        for j, (pname, amount, price) in enumerate(packages):
            db.session.add(Package(product_id=p.id, name=pname, amount=amount,
                                   price=price, sort_order=j))
        return p

    add_product(cats["free-fire-diamond"], "25 Diamond", "25-diamond",
                "Get 25 Free Fire diamonds instantly. Enter your Player ID and enjoy!",
                "POPULAR", [("25 Diamond", "25 Diamond", 20), ("50 Diamond", "50 Diamond", 38),
                            ("115 Diamond", "115 Diamond", 75), ("240 Diamond", "240 Diamond", 150)])
    add_product(cats["free-fire-diamond"], "100 Diamond", "100-diamond",
                "100 Free Fire diamonds at the cheapest rate in Bangladesh.",
                "BEST VALUE", [("100 Diamond", "100 Diamond", 68), ("210 Diamond", "210 Diamond", 135),
                               ("355 Diamond", "355 Diamond", 225), ("720 Diamond", "720 Diamond", 450)])
    add_product(cats["free-fire-diamond"], "UID Top Up (BD)", "uid-top-up-bd",
                "Top up directly using your Free Fire UID. Only Bangladesh server.",
                "", [("50 Diamond", "50 Diamond", 40), ("115 Diamond", "115 Diamond", 78),
                     ("240 Diamond", "240 Diamond", 155), ("610 Diamond", "610 Diamond", 380)])

    add_product(cats["membership"], "Weekly Membership", "weekly-membership",
                "Free Fire Weekly Membership with 2x, 3x and more options.",
                "HOT", [("Weekly Membership", "Weekly", 158), ("2x Weekly", "2x", 316),
                        ("3x Weekly", "3x", 474), ("4x Weekly", "4x", 632),
                        ("5x Weekly", "5x", 790), ("10x Weekly", "10x", 1580)])
    add_product(cats["membership"], "Monthly Membership", "monthly-membership",
                "Free Fire Monthly Membership — best value for regular players.",
                "", [("Monthly Membership", "Monthly", 790), ("2x Monthly", "2x", 1580),
                     ("3x Monthly", "3x", 2370)])
    add_product(cats["membership"], "Weekly + Monthly Combo", "weekly-monthly-combo",
                "Combo package of weekly and monthly membership together.",
                "COMBO", [("Weekly + Monthly", "Combo", 900), ("2x Combo", "2x", 1800)])
    add_product(cats["membership"], "Level Up Pass", "level-up-pass",
                "Level Up Pass — order after checking the option.",
                "", [("Level Up Pass", "Pass", 199), ("Level Up Pass Plus", "Plus", 399)])

    add_product(cats["special-offers"], "100% Bonus Topup", "100-bonus-topup",
                "Get 100% bonus on your top-up. Limited time offer!",
                "100% BONUS", [("50 Bonus Topup", "50+50", 50), ("100 Bonus Topup", "100+100", 100),
                               ("500 Bonus Topup", "500+500", 500)])
    add_product(cats["special-offers"], "Hot Offer", "hot-offer",
                "Dhaka offer — limited time hot deal on diamonds.",
                "LIMITED", [("Hot Offer 100", "100 Diamond", 60), ("Hot Offer 310", "310 Diamond", 180)])
    add_product(cats["special-offers"], "Big Package", "big-package",
                "Big diamond package for serious players at a discounted rate.",
                "BIG", [("1090 Topup", "1090 Diamond", 720), ("2200 Topup", "2200 Diamond", 1400),
                        ("5000 Topup", "5000 Diamond", 3100)])
    add_product(cats["special-offers"], "Weekly Light", "weekly-light",
                "Weekly Light — fast delivery at a budget price.",
                "FAST", [("Weekly Light", "Weekly", 99), ("2x Weekly Light", "2x", 198)])

    add_product(cats["lucky-lottery"], "Lucky Weekly", "lucky-weekly",
                "Lucky Weekly — depends on your luck! Win big diamonds.",
                "LUCKY", [("Lucky Weekly", "Lucky", 99), ("2x Lucky Weekly", "2x", 198)])
    add_product(cats["lucky-lottery"], "Lucky Monthly", "lucky-monthly",
                "Lucky Monthly — depends on your luck! Win big diamonds.",
                "LUCKY", [("Lucky Monthly", "Lucky", 199), ("2x Lucky Monthly", "2x", 398)])
    add_product(cats["lucky-lottery"], "Mystery Box", "mystery-box",
                "Mystery Box — depends on your luck! Open and win.",
                "MYSTERY", [("Mystery Box", "Box", 49), ("2x Mystery Box", "2x", 98)])
    add_product(cats["lucky-lottery"], "Lottery - 30 Taka", "lottery-30",
                "30 Taka lottery — daily 10 winners get free weekly membership.",
                "LOTTERY", [("Lottery Ticket", "1 Ticket", 30), ("3 Tickets", "3x", 90)])

    add_product(cats["free-diamond-apps"], "DIAMOMDLY", "diamomdly",
                "Watch ads and earn free diamonds. Download the app.",
                "FREE", [("Free Download", "App", 0)])
    add_product(cats["free-diamond-apps"], "DIAMONDJO", "diamondjo",
                "Watch ads and earn free diamonds. Download the app.",
                "FREE", [("Free Download", "App", 0)])
    add_product(cats["free-diamond-apps"], "DIAMONDPRO", "diamondpro",
                "Watch ads and earn free diamonds. Download the app.",
                "FREE", [("Free Download", "App", 0)])

    # ---------------- Payment methods ----------------
    payments = [
        ("Wallet", "wallet", "", "Pay instantly using your Topupjo wallet balance.", "#16a34a"),
        ("bKash", "instant", "01700-000000", "Send money to 01700-000000 (Personal). Then submit the Transaction ID below.", "#e2136e"),
        ("Nagad", "instant", "01700-000000", "Send money to 01700-000000. Then submit the Transaction ID below.", "#f6921e"),
        ("Rocket", "instant", "01700-0000001", "Send money to 01700-0000001. Then submit the Transaction ID below.", "#8c3494"),
        ("Upay", "instant", "01700-000000", "Send money to 01700-000000. Then submit the Transaction ID below.", "#00a651"),
    ]
    for i, (name, typ, acc, instr, color) in enumerate(payments):
        db.session.add(PaymentMethod(name=name, type=typ, account_number=acc,
                                     instructions=instr, color=color, sort_order=i))

    # ---------------- Banners ----------------
    db.session.add(Banner(title="100% Bonus Topup Event",
                          subtitle="Get double diamonds on every top-up. Limited time only!",
                          link="/topup", sort_order=0))
    db.session.add(Banner(title="Weekly Membership ৳158",
                          subtitle="Instant delivery · 100% trusted · 24/7 support",
                          link="/topup/weekly-membership", sort_order=1))
    db.session.add(Banner(title="Lucky Weekly — Win Big!",
                          subtitle="Try your luck and win exciting diamond rewards.",
                          link="/topup/lucky-weekly", sort_order=2))

    db.session.flush()

    # ---------------- Demo orders (for recent orders ticker) ----------------
    all_products = Product.query.all()
    demo_pkgs = []
    for p in all_products:
        for pkg in p.packages:
            demo_pkgs.append((p, pkg))

    import random
    random.seed(7)
    statuses = ["completed", "completed", "completed", "pending", "cancelled"]
    for i in range(18):
        u = random.choice(demo_users)
        p, pkg = random.choice(demo_pkgs)
        created = datetime.utcnow() - timedelta(minutes=random.randint(2, 600))
        o = Order(user_id=u.id, product_id=p.id, package_id=pkg.id,
                  player_id=str(random.randint(100000000, 999999999)),
                  quantity=1, unit_price=pkg.price, total=pkg.price,
                  payment_method=random.choice(["Wallet", "bKash", "Nagad"]),
                  status=random.choice(statuses), created_at=created)
        db.session.add(o)

    # ---------------- Demo deposits ----------------
    for i in range(6):
        u = random.choice(demo_users)
        d = Deposit(user_id=u.id, amount=random.choice([100, 200, 500, 1000]),
                    method=random.choice(["bKash", "Nagad", "Rocket"]),
                    txn_id=f"TXN{random.randint(100000, 999999)}",
                    sender_number=f"018{random.randint(10000000, 99999999)}",
                    status=random.choice(["pending", "approved", "approved", "rejected"]),
                    created_at=datetime.utcnow() - timedelta(hours=random.randint(1, 72)))
        db.session.add(d)

    db.session.commit()
    print("Seed data created.")
    print("   Admin: admin@topupjo.com / admin123")
    print("   User : user@demo.com / user123")
