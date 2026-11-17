# Topupjo — Flask Game Top-Up Platform (Clone)

A complete, professional game top-up website built with **Flask + SQLite**, inspired by
`topupjo.com`. It ships with a fully dynamic public storefront and a powerful admin panel
that controls every part of the site (categories, products, packages, payment methods,
banners, users, orders, deposits, theme colors, logo, site name and static page content).

---

## ✨ Features

### Public storefront
- Animated notice marquee, sticky header, hero section with live stats
- Category grid, popular products, promotional banners
- Live "recent orders" ticker
- Product listing with category filters + search
- Product detail page with a 3-step order flow
  (choose package → enter Player ID → choose payment method)
- Wallet balance checkout, instant payment methods (bKash / Nagad / Rocket / Upay)
- User registration, login, dashboard, order history, deposit requests, profile editing
- Static pages: About, Terms, Privacy, Refund, Delete Account, Contact
- Fully responsive (mobile bottom navigation included)
- Dynamic theme: primary / accent / dark colors, logo, favicon, site name, currency,
  notice text, footer and all page content are stored in the database

### Admin panel (`/admin`)
- Dashboard with revenue, order, deposit and customer statistics
- **Categories** — create / edit / delete (with image upload)
- **Products** — create / edit / delete, assign to category, badge, image
- **Packages** — create / edit / delete per product, price & sort order
- **Payment Methods** — create / edit / delete, brand color, logo, account number
- **Banners** — add / delete homepage banners
- **Users** — full list, search, per-user order + deposit history, block/unblock,
  manual wallet balance adjustment
- **Orders** — filter by status, confirm or cancel (wallet payments auto-refunded)
- **Deposits** — filter by status, approve (credits wallet) or reject
- **Site Settings** — site name, tagline, logo, favicon, theme colors, currency,
  notice text, contact details, footer text and all static page content
- **Change admin password**
- Every uploaded image is **automatically resized & optimised** with Pillow

---

## 🚀 Quick start

```bash
cd topupjo
pip install -r requirements.txt
python app.py
```

The app starts on **http://localhost:5001** (override with the `PORT` environment variable).

The database (`topupjo.db`) and demo data are created automatically on first run.

### Demo credentials

| Role  | Email               | Password  |
|-------|---------------------|-----------|
| Admin | `admin@topupjo.com` | `admin123` |
| User  | `user@demo.com`     | `user123`  |

Admin panel: **http://localhost:5001/admin**

---

## 📁 Project structure

```
topupjo/
├── app.py                # Flask app factory + all routes
├── config.py             # Configuration
├── models.py             # SQLAlchemy models
├── seed.py               # Demo data seeder
├── requirements.txt
├── topupjo.db            # SQLite database (auto-created)
├── static/
│   ├── css/style.css     # Public stylesheet
│   ├── css/admin.css     # Admin stylesheet
│   ├── js/main.js
│   ├── img/favicon.svg
│   └── uploads/          # Uploaded & auto-resized images
└── templates/
    ├── base.html, index.html, topup.html, product.html
    ├── login.html, register.html, dashboard.html, orders.html,
    │   deposits.html, profile.html, contact.html, page.html, 404.html
    ├── partials/dash_side.html
    └── admin/            # All admin templates
```

---

## 🗄️ Database models

`User`, `Category`, `Product`, `Package`, `PaymentMethod`, `Order`, `Deposit`,
`Banner`, `SiteSetting`.

- Orders support `pending` / `completed` / `cancelled`
- Deposits support `pending` / `approved` / `rejected`
- Cancelling a wallet order refunds the customer automatically
- Approving a deposit credits the customer wallet automatically

---

## 🔐 Notes

- Change the default admin password immediately from **Admin → Password**.
- Set a strong `SECRET_KEY` in `config.py` before deploying.
- For production, run behind a WSGI server, e.g.:
  ```bash
  pip install gunicorn
  gunicorn -w 4 -b 0.0.0.0:5001 "app:app"
  ```
- Uploaded files are limited to 8 MB and to png/jpg/jpeg/webp/gif.

---

## 🔐 Environment variables (Google Login)

Google OAuth credentials are **never stored in the database**. Set them in the
environment before starting the app:

```bash
export GOOGLE_CLIENT_ID="xxxxxxxx.apps.googleusercontent.com"
export GOOGLE_CLIENT_SECRET="your-client-secret"
export SECRET_KEY="a-long-random-string"
python app.py
```

Authorized redirect URI in Google Cloud Console:

```
https://YOUR-DOMAIN/auth/google/callback
```

If the variables are missing the "Continue with Google" button simply shows a
friendly message instead of failing.

## 🧩 Support platforms

Admin → Site Settings → **Support Platforms** lets you add any number of
support links (Telegram, WhatsApp, Messenger, etc.). They appear when a visitor
taps the floating **"সাহায্য লাগবে?"** button.

## 🪧 Site-open popup

Admin → Site Settings → **Site-Open Popup** controls the promotional popup
(image, title, text, link). Visitors can close it with the small × or hide it
for the rest of the day with **"আজ আর দেখাবেন না"**.
