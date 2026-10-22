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
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

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
      bio TEXT NOT NULL DEFAULT '', is_admin INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS posts (
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      body TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS likes (
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (user_id, post_id)
    );
    CREATE TABLE IF NOT EXISTS comments (
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
      body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 500),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS follows (
      follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      followed_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (follower_id, followed_id),
      CHECK (follower_id != followed_id)
    );
    CREATE TABLE IF NOT EXISTS notifications (
      id INTEGER PRIMARY KEY,
      recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('like','comment','follow')),
      post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      is_read INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS groups (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
      description TEXT NOT NULL DEFAULT '', owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS group_members (
      group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(group_id,user_id)
    );
    CREATE TABLE IF NOT EXISTS group_posts (
      id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 1000),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY, sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 2000),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, is_read INTEGER NOT NULL DEFAULT 0,
      CHECK(sender_id != recipient_id)
    );
    CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id,recipient_id,id);
    CREATE INDEX IF NOT EXISTS idx_group_posts ON group_posts(group_id,id);
    """)
    # Safe schema migration for databases created by an earlier ARENA version.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "is_admin" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    admin_name = os.environ.get("ARENA_ADMIN_USERNAME", "").strip()
    if admin_name:
        conn.execute("UPDATE users SET is_admin=1 WHERE username=? COLLATE NOCASE", (admin_name,))
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
            "SELECT id, username, email, bio, is_admin, created_at FROM users WHERE id=?",
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


def posts_with_stats(where_sql="", params=(), viewer_id=None, limit=50):
    sql = """
      SELECT p.id,p.body,p.created_at,p.user_id,u.username,
        (SELECT COUNT(*) FROM likes l WHERE l.post_id=p.id) AS like_count,
        (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,
        EXISTS(SELECT 1 FROM likes mine WHERE mine.post_id=p.id AND mine.user_id=?) AS liked_by_me
      FROM posts p JOIN users u ON u.id=p.user_id
    """
    if where_sql:
        sql += " WHERE " + where_sql
    sql += " ORDER BY p.id DESC LIMIT ?"
    return db().execute(sql, (viewer_id or -1, *params, limit)).fetchall()


def comments_for_posts(posts):
    ids = [p["id"] for p in posts]
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = db().execute(
        f"SELECT c.id,c.post_id,c.body,c.created_at,u.username FROM comments c JOIN users u ON u.id=c.user_id WHERE c.post_id IN ({marks}) ORDER BY c.id ASC",
        ids
    ).fetchall()
    grouped = {post_id: [] for post_id in ids}
    for row in rows:
        grouped[row["post_id"]].append(row)
    return grouped


@app.route("/")
def home():
    posts = posts_with_stats(viewer_id=g.user["id"] if g.user else None, limit=8)
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
                    "INSERT INTO users(username,email,password_hash,is_admin) VALUES(?,?,?,?)",
                    (username, email, generate_password_hash(password), int(bool(os.environ.get("ARENA_ADMIN_USERNAME")) and username.casefold() == os.environ.get("ARENA_ADMIN_USERNAME", "").strip().casefold()))
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
    posts = posts_with_stats(viewer_id=g.user["id"], limit=50)
    return render_template("feed.html", posts=posts, comments_by_post=comments_for_posts(posts))


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
    return redirect(request.referrer if request.referrer and request.referrer.startswith("/") else url_for("feed"))


@app.route("/posts/<int:post_id>/like", methods=["POST"])
@signed_in
def toggle_like(post_id):
    conn = db()
    exists = conn.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone()
    if exists is None:
        abort(404)
    liked = conn.execute("SELECT 1 FROM likes WHERE user_id=? AND post_id=?", (g.user["id"], post_id)).fetchone()
    if liked:
        conn.execute("DELETE FROM likes WHERE user_id=? AND post_id=?", (g.user["id"], post_id))
    else:
        conn.execute("INSERT OR IGNORE INTO likes(user_id,post_id) VALUES(?,?)", (g.user["id"], post_id))
        owner = conn.execute("SELECT user_id FROM posts WHERE id=?", (post_id,)).fetchone()["user_id"]
        if owner != g.user["id"]:
            conn.execute("INSERT INTO notifications(recipient_id,actor_id,kind,post_id) VALUES(?,?,?,?)", (owner, g.user["id"], "like", post_id))
    conn.commit()
    return redirect(request.referrer if request.referrer and request.referrer.startswith("/") else url_for("feed"))


@app.route("/posts/<int:post_id>/comment", methods=["POST"])
@signed_in
def add_comment(post_id):
    body = request.form.get("body", "").strip()
    if not db().execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
        abort(404)
    if not body or len(body) > 500:
        flash("Comments must be between 1 and 500 characters.", "error")
    else:
        conn = db()
        owner = conn.execute("SELECT user_id FROM posts WHERE id=?", (post_id,)).fetchone()["user_id"]
        conn.execute("INSERT INTO comments(user_id,post_id,body) VALUES(?,?,?)", (g.user["id"], post_id, body))
        if owner != g.user["id"]:
            conn.execute("INSERT INTO notifications(recipient_id,actor_id,kind,post_id) VALUES(?,?,?,?)", (owner, g.user["id"], "comment", post_id))
        conn.commit()
        flash("Comment added.", "success")
    return redirect(request.referrer if request.referrer and request.referrer.startswith("/") else url_for("feed"))


@app.route("/follow/<username>", methods=["POST"])
@signed_in
def toggle_follow(username):
    person = db().execute("SELECT id,username FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
    if person is None:
        abort(404)
    if person["id"] == g.user["id"]:
        flash("You can't follow your own profile.", "error")
    else:
        existing = db().execute("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?", (g.user["id"], person["id"])).fetchone()
        if existing:
            db().execute("DELETE FROM follows WHERE follower_id=? AND followed_id=?", (g.user["id"], person["id"]))
            flash("You unfollowed @" + person["username"] + ".", "info")
        else:
            db().execute("INSERT OR IGNORE INTO follows(follower_id,followed_id) VALUES(?,?)", (g.user["id"], person["id"]))
            db().execute("INSERT INTO notifications(recipient_id,actor_id,kind) VALUES(?,?,?)", (person["id"], g.user["id"], "follow"))
            flash("You're now following @" + person["username"] + ".", "success")
        db().commit()
    return redirect(url_for("profile", username=person["username"]))


@app.route("/groups", methods=["GET", "POST"])
@signed_in
def groups():
    conn = db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not 3 <= len(name) <= 40 or len(description) > 300:
            flash("Group name must be 3–40 characters; description max is 300.", "error")
        else:
            try:
                cur = conn.execute("INSERT INTO groups(name,description,owner_id) VALUES(?,?,?)", (name,description,g.user["id"]))
                conn.execute("INSERT INTO group_members(group_id,user_id) VALUES(?,?)", (cur.lastrowid,g.user["id"]))
                conn.commit()
                return redirect(url_for("group_detail", group_id=cur.lastrowid))
            except sqlite3.IntegrityError:
                flash("A group with that name already exists.", "error")
    items = conn.execute("""SELECT gr.id,gr.name,gr.description,u.username,
      (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id=gr.id) AS member_count,
      EXISTS(SELECT 1 FROM group_members mine WHERE mine.group_id=gr.id AND mine.user_id=?) AS joined
      FROM groups gr JOIN users u ON u.id=gr.owner_id ORDER BY gr.id DESC LIMIT 100""",(g.user["id"],)).fetchall()
    return render_template("groups.html", groups=items)


@app.route("/groups/<int:group_id>")
@signed_in
def group_detail(group_id):
    conn = db()
    group = conn.execute("""SELECT gr.id,gr.name,gr.description,gr.owner_id,u.username,
      (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id=gr.id) AS member_count,
      EXISTS(SELECT 1 FROM group_members mine WHERE mine.group_id=gr.id AND mine.user_id=?) AS joined
      FROM groups gr JOIN users u ON u.id=gr.owner_id WHERE gr.id=?""",(g.user["id"],group_id)).fetchone()
    if group is None: abort(404)
    posts = conn.execute("SELECT gp.id,gp.body,gp.created_at,u.username FROM group_posts gp JOIN users u ON u.id=gp.user_id WHERE gp.group_id=? ORDER BY gp.id DESC LIMIT 100",(group_id,)).fetchall()
    return render_template("group_detail.html", group=group, posts=posts)


@app.route("/groups/<int:group_id>/join", methods=["POST"])
@signed_in
def join_group(group_id):
    conn = db()
    group = conn.execute("SELECT id,name FROM groups WHERE id=?",(group_id,)).fetchone()
    if group is None: abort(404)
    conn.execute("INSERT OR IGNORE INTO group_members(group_id,user_id) VALUES(?,?)",(group_id,g.user["id"]))
    conn.commit()
    flash("You joined " + group["name"] + ".", "success")
    return redirect(url_for("group_detail",group_id=group_id))


@app.route("/groups/<int:group_id>/leave", methods=["POST"])
@signed_in
def leave_group(group_id):
    conn = db()
    group = conn.execute("SELECT id,name,owner_id FROM groups WHERE id=?",(group_id,)).fetchone()
    if group is None: abort(404)
    if group["owner_id"] == g.user["id"]:
        flash("Group owners cannot leave their own group.", "error")
    else:
        conn.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?",(group_id,g.user["id"]))
        conn.commit()
        flash("You left " + group["name"] + ".", "info")
    return redirect(url_for("group_detail",group_id=group_id))


@app.route("/groups/<int:group_id>/posts", methods=["POST"])
@signed_in
def group_post(group_id):
    conn = db()
    if not conn.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",(group_id,g.user["id"])).fetchone():
        flash("Join this group before posting.", "error")
        return redirect(url_for("group_detail",group_id=group_id))
    body = request.form.get("body","").strip()
    if not body or len(body)>1000:
        flash("Group posts must be between 1 and 1,000 characters.", "error")
    else:
        conn.execute("INSERT INTO group_posts(group_id,user_id,body) VALUES(?,?,?)",(group_id,g.user["id"],body))
        conn.commit()
        flash("Group post published.", "success")
    return redirect(url_for("group_detail",group_id=group_id))


@app.route("/messages")
@signed_in
def messages():
    items = db().execute("""SELECT u.username,m.body AS last_body,m.created_at,
      (SELECT COUNT(*) FROM messages unread WHERE unread.sender_id=u.id AND unread.recipient_id=? AND unread.is_read=0) AS unread_count
      FROM users u JOIN (
        SELECT CASE WHEN sender_id=? THEN recipient_id ELSE sender_id END AS other_id, MAX(id) AS last_id
        FROM messages WHERE sender_id=? OR recipient_id=? GROUP BY other_id
      ) conv ON conv.other_id=u.id JOIN messages m ON m.id=conv.last_id ORDER BY m.id DESC""",
      (g.user["id"],g.user["id"],g.user["id"],g.user["id"])).fetchall()
    return render_template("messages.html", conversations=items)


@app.route("/messages/<username>", methods=["GET","POST"])
@signed_in
def message_thread(username):
    conn = db()
    other = conn.execute("SELECT id,username FROM users WHERE username=? COLLATE NOCASE",(username,)).fetchone()
    if other is None: abort(404)
    if other["id"] == g.user["id"]:
        flash("Choose another player to message.", "error")
        return redirect(url_for("messages"))
    if request.method == "POST":
        body = request.form.get("body","").strip()
        if not body or len(body)>2000:
            flash("Messages must be between 1 and 2,000 characters.", "error")
        else:
            conn.execute("INSERT INTO messages(sender_id,recipient_id,body) VALUES(?,?,?)",(g.user["id"],other["id"],body))
            conn.commit()
            return redirect(url_for("message_thread",username=other["username"]))
    conn.execute("UPDATE messages SET is_read=1 WHERE sender_id=? AND recipient_id=?",(other["id"],g.user["id"]))
    conn.commit()
    thread = conn.execute("""SELECT m.sender_id,m.body,m.created_at,u.username FROM messages m
      JOIN users u ON u.id=m.sender_id
      WHERE (m.sender_id=? AND m.recipient_id=?) OR (m.sender_id=? AND m.recipient_id=?)
      ORDER BY m.id ASC LIMIT 200""",(g.user["id"],other["id"],other["id"],g.user["id"])).fetchall()
    return render_template("message_thread.html", other=other, messages=thread)


@app.route("/notifications")
@signed_in
def notifications():
    conn = db()
    items = conn.execute("""
        SELECT n.id,n.kind,n.post_id,n.created_at,n.is_read,u.username
        FROM notifications n JOIN users u ON u.id=n.actor_id
        WHERE n.recipient_id=? ORDER BY n.id DESC LIMIT 100
    """, (g.user["id"],)).fetchall()
    conn.execute("UPDATE notifications SET is_read=1 WHERE recipient_id=?", (g.user["id"],))
    conn.commit()
    return render_template("notifications.html", notifications=items)


@app.route("/admin")
@signed_in
def admin_dashboard():
    if not g.user["is_admin"]:
        abort(403)
    stats = {
        "users": db().execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "posts": db().execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "comments": db().execute("SELECT COUNT(*) FROM comments").fetchone()[0],
        "reports": 0,
    }
    recent_posts = db().execute("""
        SELECT p.id,p.body,p.created_at,u.username FROM posts p
        JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 30
    """).fetchall()
    return render_template("admin.html", stats=stats, posts=recent_posts)


@app.route("/admin/posts/<int:post_id>/delete", methods=["POST"])
@signed_in
def admin_delete_post(post_id):
    if not g.user["is_admin"]:
        abort(403)
    db().execute("DELETE FROM posts WHERE id=?", (post_id,))
    db().commit()
    flash("Post removed by administrator.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/profile/<username>", methods=["GET", "POST"])
def profile(username):
    conn = db()
    person = conn.execute(
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
            conn.execute("UPDATE users SET bio=? WHERE id=?", (bio, person["id"]))
            conn.commit()
            flash("Profile saved.", "success")
            return redirect(url_for("profile", username=person["username"]))
    posts = posts_with_stats("p.user_id=?", (person["id"],), g.user["id"] if g.user else None, 30)
    followers = conn.execute("SELECT COUNT(*) AS n FROM follows WHERE followed_id=?", (person["id"],)).fetchone()["n"]
    following = conn.execute("SELECT COUNT(*) AS n FROM follows WHERE follower_id=?", (person["id"],)).fetchone()["n"]
    is_following = bool(g.user and conn.execute("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?", (g.user["id"], person["id"])).fetchone())
    return render_template("profile.html", profile_user=person, posts=posts, comments_by_post=comments_for_posts(posts), followers=followers, following=following, is_following=is_following)


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
