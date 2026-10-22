
import os
from flask import Flask, render_template

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "change-this-before-production"
)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
@app.route("/healthz")
def health():
    return {"status": "ok", "app": "ARENA"}, 200


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )

