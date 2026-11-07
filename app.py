import os
from dotenv import load_dotenv

load_dotenv()   # reads the .env file so os.getenv can find your secrets

from flask import Flask, render_template, request, redirect, url_for, flash, session
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
   


# shows the signup page
@app.route("/signup")
def signup():
    return render_template("signup.html")

# receives the submitted form and saves it
@app.route("/submit_signup", methods=["POST"])
def submit_signup():
    username = request.form["username"]
    email = request.form["email"]
    password = request.form["password"]
    hashed_password = generate_password_hash(password)

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
    password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor()

    sql = "INSERT INTO users (Username, Email, Password_hash) VALUES (%s, %s, %s)"
    cursor.execute(sql, (username, email, hashed_password))

    conn.commit()
    new_user_id = cursor.lastrowid   # the User_id MySQL just gave this account
    cursor.close()
    conn.close()

    # log the new user straight in, and mark them as brand new
    session["user_id"] = new_user_id
    session["username"] = username
    session["new_user"] = True
    return redirect(url_for("dashboard"))


# shows the login page AND handles the login submit
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password=os.getenv("DB_PASSWORD"),
            database="mydb"
        )
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE Username = %s", (username,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user and check_password_hash(user["Password_hash"], password):
            # remember who logged in
            session["user_id"] = user["User_id"]
            session["username"] = user["Username"]
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.")
            return redirect(url_for("login"))

    # THIS LINE must be here — handles the GET (normal page visit):
    return render_template("login.html")


# only works if you're logged in
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))
    # stays True until logout clears the session, so refreshing keeps the "new account" greeting
    new_user = session.get("new_user", False)
    return render_template("dashboard.html", username=session["username"], new_user=new_user)


# shows the logged-in user's profile
@app.route("/profile")
def profile():
    # 1. guard: only logged-in users
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    # 2. look up this user's full row in the database
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE User_id = %s", (session["user_id"],))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    # 3. hand the whole row to the template
    return render_template("profile.html", user=user)



# clears the session and sends you back to login
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))


@app.route("/home")
def about():
    return render_template("home.html")   # use your actual About filename

    # GET request = just show the page normally
    return render_template("login.html")


    



if __name__ == "__main__":
    app.run(debug=True)