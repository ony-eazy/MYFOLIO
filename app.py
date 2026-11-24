import requests
import os
import re
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()   # reads the .env file so os.getenv can find your secrets

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
   
# the languages shown under "Projects" (nav dropdown, filter chips, later the add-project form)
# each pair is (what goes in the URL, what the user sees)
LANGUAGES = [
    ("javascript", "JavaScript"),
    ("typescript", "TypeScript"),
    ("python",     "Python"),
    ("java",       "Java"),
    ("csharp",     "C#"),
    ("cpp",        "C++"),
    ("go",         "Go"),
    ("rust",       "Rust"),
    ("php",        "PHP"),
    ("kotlin",     "Kotlin"),
    ("swift",      "Swift"),
    ("html",       "HTML"),
    ("css",        "CSS"),
]


# makes "languages" available in EVERY template, so the topbar and projects page can use it
@app.context_processor
def inject_languages():
    return {"languages": LANGUAGES}


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

    # look up what this user has filled in so far
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE User_id = %s", (session["user_id"],))
    user = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) AS total FROM projects WHERE User_id = %s", (session["user_id"],))
    project_count = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM social_proof WHERE User_id = %s", (session["user_id"],))
    testimonial_count = cursor.fetchone()["total"]
    cursor.close()
    conn.close()

    # each step: (what to show, is it done?, where to go to do it)
    steps = [
        ("Add your full name",       bool(user["Full_name"]),     "/profile"),
        ("Write a headline",         bool(user["Role_headline"]), "/profile"),
        ("Write your bio",           bool(user["Bio"]),           "/profile"),
        ("List your skills",         bool(user["Skills"]),        "/profile"),
        ("Add your first project",   project_count > 0,           "/projects"),
        ("Add a testimonial",        testimonial_count > 0,       "/testimonials"),
    ]
    done = sum(1 for step in steps if step[1])
    percent = round(done / len(steps) * 100)

    return render_template("dashboard.html", username=session["username"], new_user=new_user,
                           steps=steps, done=done, percent=percent)


# tidies a pasted link: adds https:// if missing and checks the website
# domain="github.com" → must be on GitHub;  domain=None → any real website
# returns (clean_link, error_message) — one of them is always None
def clean_link(value, domain=None):
    value = value.strip()
    if not value:
        return None, None                   # left empty → fine, it's optional

    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value          # "linkedin.com/in/me" → "https://linkedin.com/in/me"

    host = urlparse(value).netloc.lower()   # "https://www.linkedin.com/in/me" → "www.linkedin.com"
    if domain:
        if host != domain and not host.endswith("." + domain):
            return None, f"That doesn't look like a {domain} link."
    elif "." not in host:
        return None, "That doesn't look like a web address."

    return value, None

# GitHub's language names match our labels ("Python", "C#"), so flip LANGUAGES around:
# {"Python": "python", "C#": "csharp", ...}
GITHUB_TO_SLUG = {label: slug for slug, label in LANGUAGES}

# SCSS and Less are CSS with extra features, so they count as CSS
GITHUB_TO_SLUG["SCSS"] = "css"
GITHUB_TO_SLUG["Less"] = "css"

# "https://github.com/ony-eazy/myfolio" → ("ony-eazy", "myfolio")
# returns None if it isn't a github.com/owner/repo link
def parse_github_repo(github_url):
    link, error = clean_link(github_url, "github.com")
    if not link:
        return None

    parts = urlparse(link).path.strip("/").split("/")
    if len(parts) < 2:
        return None

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]   # drop a trailing ".git"
    return owner, repo


# asks GitHub's API about a PUBLIC repository
# returns a dictionary of details, or None if GitHub can't find it
def fetch_github_repo(github_url):
    parsed = parse_github_repo(github_url)
    if not parsed:
        return None
    owner, repo = parsed

    api = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github+json"}

    try:
        info = requests.get(api, headers=headers, timeout=5)
        if info.status_code != 200:
            return None
        langs = requests.get(api + "/languages", headers=headers, timeout=5)
    except requests.RequestException:
        return None

    data = info.json()
    lang_data = langs.json() if langs.status_code == 200 else {}

    # GitHub lists them biggest first; SCSS + CSS would both become "css", so drop repeats
    languages = [GITHUB_TO_SLUG.get(name, name.lower().replace(" ", "-")) for name in lang_data]
    languages = list(dict.fromkeys(languages))

    return {
        "title": data["name"],
        "description": data["description"],              # can be None if the repo has none
        "homepage": data["homepage"],                    # the repo's "website" link, if set
        "stars": data["stargazers_count"],
        "github_url": data["html_url"],                  # the clean, official link
        "languages": languages,
    }

# shows the logged-in user's profile AND saves it when the form is submitted
@app.route("/profile", methods=["GET", "POST"])
def profile():
    # 1. guard: only logged-in users
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor(dictionary=True)

    # 2. the form was submitted → save the changes
    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        # optional fields: an empty box becomes None, so the database stores NULL
        role_headline = request.form["role_headline"].strip() or None
        bio = request.form["bio"].strip() or None
        skills = request.form["skills"].strip() or None

        # links: tidied and checked by clean_link()
        github_url, github_error = clean_link(request.form["github_url"], "github.com")
        linkedin_url, linkedin_error = clean_link(request.form["linkedin_url"], "linkedin.com")

        # Discord: a username, not a link — drop a leading @ and store it in lowercase
        discord_username = request.form["discord_username"].strip().lstrip("@").lower() or None

        # collect every problem so the user sees them all at once
        errors = []
        if not full_name:
            errors.append("Full name can't be empty.")
        if bio and len(bio) > 1000:
            errors.append("Your bio is too long — please keep it under 1000 characters.")

        if github_error:
            errors.append(github_error)
        if linkedin_error:
            errors.append(linkedin_error)
        if discord_username and not re.fullmatch(r"[a-z0-9_.]{2,32}", discord_username):
            errors.append("Discord usernames are 2–32 characters: letters, numbers, _ and .")

        if errors:
            for error in errors:
                flash(error, "error")
        else:
            cursor.execute(
                "UPDATE users SET Full_name = %s, Role_headline = %s, Bio = %s, Skills = %s, "
                "Github_url = %s, Linkedin_url = %s, Discord_username = %s "
                "WHERE User_id = %s",
                (full_name, role_headline, bio, skills,
                 github_url, linkedin_url, discord_username, session["user_id"])
            )
            conn.commit()
            flash("Profile updated!", "success")

        cursor.close()
        conn.close()
        # send them back to the page as a normal visit (see explanation below)
        return redirect(url_for("profile"))

    # 3. normal visit → look up this user's row and show the form
    cursor.execute("SELECT * FROM users WHERE User_id = %s", (session["user_id"],))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    return render_template("profile.html", user=user)


# shows the logged-in user's projects, optionally filtered by language
@app.route("/projects")
def projects():
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))
    # /projects?lang=python  →  "python"      /projects  →  None (show all)
    lang = request.args.get("lang")
    known_slugs = [slug for slug, label in LANGUAGES]  

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor(dictionary=True)

    if lang in known_slugs:
        # projects that use this language (among others)
        cursor.execute(
            "SELECT DISTINCT p.* FROM projects p "
            "JOIN project_languages pl ON pl.Project_id = p.Project_id "
            "WHERE p.User_id = %s AND pl.Language = %s "
            "ORDER BY p.Created_at DESC",
            (session["user_id"], lang)
        )
    elif lang == "other":
        # projects with NO languages, or with at least one language not in the list
        placeholders = ", ".join(["%s"] * len(known_slugs))
        cursor.execute(
            f"SELECT p.* FROM projects p WHERE p.User_id = %s AND ("
            f"  NOT EXISTS (SELECT 1 FROM project_languages pl WHERE pl.Project_id = p.Project_id)"
            f"  OR EXISTS (SELECT 1 FROM project_languages pl"
            f"             WHERE pl.Project_id = p.Project_id AND pl.Language NOT IN ({placeholders}))"
            f") ORDER BY p.Created_at DESC",
            (session["user_id"], *known_slugs)
        )
    else:
        # no filter (or a made-up ?lang=) → show everything
        lang = None
        cursor.execute(
            "SELECT * FROM projects WHERE User_id = %s ORDER BY Created_at DESC",
            (session["user_id"],)
        )
    user_projects = cursor.fetchall()

    # every language of every one of this user's projects, for the badges
    cursor.execute(
        "SELECT pl.Project_id, pl.Language FROM project_languages pl "
        "JOIN projects p ON p.Project_id = pl.Project_id "
        "WHERE p.User_id = %s",
        (session["user_id"],)
    )
    langs_by_project = {}
    for row in cursor.fetchall():
        langs_by_project.setdefault(row["Project_id"], []).append(row["Language"])

    cursor.close()
    conn.close()

        # lets the page turn "csharp" back into "C#"
    labels = dict(LANGUAGES)
    labels["other"] = "Other"


    return render_template("projects.html", projects=user_projects, lang=lang,
    labels=labels, langs_by_project=langs_by_project)

# shows the add-project form AND saves the new project
@app.route("/projects/new", methods=["GET", "POST"])
def new_project():
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    if request.method == "POST":
        title = request.form["title"].strip()
        description = request.form["description"].strip() or None
        problem = request.form["problem"].strip() or None
        process = request.form["process"].strip() or None
        tools = request.form["tools"].strip() or None 
        results = request.form["results"].strip() or None

        github_url, github_error = clean_link(request.form["github_url"], "github.com")
        project_link, link_error = clean_link(request.form["project_link"])

        # languages: the ticked boxes...
        languages = request.form.getlist("languages")          # → ["python", "javascript"]
        # ...plus anything typed in "Other languages", e.g. "Ruby, Dart"
        name_to_slug = {label.lower(): slug for slug, label in LANGUAGES}
        for name in request.form["other_languages"].split(","):
            name = name.strip().lower()
            if name:
                languages.append(name_to_slug.get(name, name.replace(" ", "-")))
        languages = list(dict.fromkeys(languages))              # remove duplicates, keep order

        errors = []
        if not title:
            errors.append("Your project needs a title.")
        if github_error:
            errors.append(github_error)
        if link_error:
            errors.append(link_error)
        if any(len(slug) > 45 for slug in languages):
            errors.append("One of your languages is too long.")

        if errors:
            for error in errors:
                flash(error, "error")
            # show the form again WITH what they typed, so nothing is lost
            return render_template("project_form.html", form=request.form, selected=languages)


        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password=os.getenv("DB_PASSWORD"),
            database="mydb"
        )
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO projects (User_id, Title, Description, Github_url, Project_link, "
            "Problem, Process, Tools, Results, Created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
            (session["user_id"], title, description, github_url, project_link,
             problem, process, tools, results)
        )
        project_id = cursor.lastrowid

        for slug in languages:
            cursor.execute(
                "INSERT INTO project_languages (Project_id, Language) VALUES (%s, %s)",
                (project_id, slug)
            )

        conn.commit()
        cursor.close()
        conn.close()

        flash("Project added!", "success")
        return redirect(url_for("projects"))
    # normal visit → show the empty form
    return render_template("project_form.html", form={}, selected=[])
# returns GitHub info as JSON — the add-project form's "Detect" button will use this
# try it:  /api/github?url=github.com/pallets/flask
@app.route("/api/github")
def api_github():
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    repo = fetch_github_repo(request.args.get("url", ""))
    if repo is None:
        return jsonify({"error": "Couldn't find that public GitHub repository."}), 404

    return jsonify(repo)


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