import os
import sqlite3
import secrets
import hmac
from pathlib import Path
from functools import wraps

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-in-render")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

DATA_DIR = Path(os.environ.get("ARENA_DATA_DIR", str(Path(__file__).parent / "instance")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE = DATA_DIR / "arena.db"


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DATABASE)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
      email TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL,
      bio TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS posts (
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      body TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()


init_db()


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(24)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf", "")
        saved = session.get("_csrf", "")
        if not token or not saved or not hmac.compare_digest(token, saved):
            abort(400, description="Form expired. Refresh and try again.")


@app.before_request
def current_user():
    g.user = None
    if session.get("user_id"):
        g.user = db().execute(
            "SELECT id, username, email, bio, created_at FROM users WHERE id=?",
            (session["user_id"],)
        ).fetchone()
        if g.user is None:
            session.clear()


def signed_in(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Sign in to continue.", "info")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
def home():
    posts = db().execute(
        "SELECT p.id,p.body,p.created_at,u.username FROM posts p JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 8"
    ).fetchall()
    return render_template("index.html", posts=posts)


@app.route("/health")
@app.route("/healthz")
def health():
    return {"status": "ok", "app": "ARENA"}, 200


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("feed"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not (3 <= len(username) <= 24 and all(c.isalnum() or c == "_" for c in username)):
            flash("Username must be 3–24 letters, numbers, or underscores.", "error")
        elif "@" not in email or len(email) > 254:
            flash("Enter a valid email address.", "error")
        elif len(password) < 10:
            flash("Password must be at least 10 characters.", "error")
        else:
            try:
                cur = db().execute(
                    "INSERT INTO users(username,email,password_hash) VALUES(?,?,?)",
                    (username, email, generate_password_hash(password))
                )
                db().commit()
                session.clear()
                session["user_id"] = cur.lastrowid
                session["_csrf"] = secrets.token_urlsafe(24)
                flash("Welcome to ARENA!", "success")
                return redirect(url_for("feed"))
            except sqlite3.IntegrityError:
                flash("That username or email is already registered.", "error")
        return render_template("register.html", username=username, email=email), 400
    return render_template("register.html", username="", email="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("feed"))
    if request.method == "POST":
        identity = request.form.get("identity", "").strip()
        user = db().execute(
            "SELECT id,password_hash FROM users WHERE username=? COLLATE NOCASE OR email=? COLLATE NOCASE",
            (identity, identity)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = user["id"]
            session["_csrf"] = secrets.token_urlsafe(24)
            return redirect(url_for("feed"))
        flash("Incorrect username/email or password.", "error")
        return render_template("login.html", identity=identity), 401
    return render_template("login.html", identity="")


@app.route("/logout", methods=["POST"])
@signed_in
def logout():
    session.clear()
    flash("Signed out successfully.", "info")
    return redirect(url_for("home"))


@app.route("/feed")
@signed_in
def feed():
    posts = db().execute(
        "SELECT p.id,p.body,p.created_at,u.username FROM posts p JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 50"
    ).fetchall()
    return render_template("feed.html", posts=posts)


@app.route("/posts", methods=["POST"])
@signed_in
def create_post():
    body = request.form.get("body", "").strip()
    if not body or len(body) > 1000:
        flash("Posts must be between 1 and 1,000 characters.", "error")
    else:
        db().execute("INSERT INTO posts(user_id,body) VALUES(?,?)", (g.user["id"], body))
        db().commit()
        flash("Post published!", "success")
    return redirect(url_for("feed"))


@app.route("/profile/<username>", methods=["GET", "POST"])
def profile(username):
    person = db().execute(
        "SELECT id,username,bio,created_at FROM users WHERE username=? COLLATE NOCASE", (username,)
    ).fetchone()
    if person is None:
        abort(404)
    if request.method == "POST":
        if not g.user or g.user["id"] != person["id"]:
            abort(403)
        bio = request.form.get("bio", "").strip()
        if len(bio) > 240:
            flash("Bio must be 240 characters or fewer.", "error")
        else:
            db().execute("UPDATE users SET bio=? WHERE id=?", (bio, person["id"]))
            db().commit()
            flash("Profile saved.", "success")
            return redirect(url_for("profile", username=person["username"]))
    posts = db().execute("SELECT body,created_at FROM posts WHERE user_id=? ORDER BY id DESC LIMIT 30", (person["id"],)).fetchall()
    return render_template("profile.html", profile_user=person, posts=posts)


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "Bad request.")), 400


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="You don't have permission to access this page."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="Page not found."), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
