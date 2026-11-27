import secrets
import requests
import os
import re
from urllib.parse import urlparse, urlencode
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect, CSRFError


load_dotenv()   # reads the .env file so os.getenv can find your secrets

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, abort
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
csrf = CSRFProtect(app)  # every POST must now include a valid csrf_token

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
REQUEST_DAYS = 30   # testimonial links stop working after this many days

# the "How do you know them?" choices — the server only accepts these
RELATIONSHIPS = ["Client", "Colleague", "Manager", "Teacher / mentor", "Classmate", "Other"]

# the sites a testimonial writer can verify with — Discord and LinkedIn get added here later
OAUTH_PROVIDERS = {
    "github": {
        "label": "GitHub",
        "client_id": os.getenv("GITHUB_CLIENT_ID"),
        "client_secret": os.getenv("GITHUB_CLIENT_SECRET"),
        "authorize_url": "https://github.com/login/oauth/authorize",   # where Sarah signs in
        "token_url": "https://github.com/login/oauth/access_token",    # where we swap code → token
        "user_url": "https://api.github.com/user",                     # "who is this?"
        "scope": "",          # no extra permissions — public profile only
    },
}

# makes "languages" available in EVERY template, so the topbar and projects page can use it
@app.context_processor
def inject_languages():
    return {"languages": LANGUAGES}

# makes "pending_count" available in every template, for the badge on the Testimonials nav link
@app.context_processor
def inject_pending_count():
    if "user_id" not in session:
        return {"pending_count": 0}

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM social_proof "
        "WHERE User_id = %s AND Type = 'testimonial' AND Status = 'pending'",
        (session["user_id"],)
    )
    count = cursor.fetchone()[0]      # a plain cursor gives a tuple, e.g. (2,) → 2
    cursor.close()
    conn.close()
    return {"pending_count": count}

# runs before EVERY request: if you're "logged in" as an account that's been deleted, log you out
@app.before_request
def drop_deleted_accounts():
    if "user_id" not in session or request.endpoint == "static":
        return   # not logged in, or just loading a CSS/JS file → nothing to check

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users WHERE User_id = %s", (session["user_id"],))
    exists = cursor.fetchone() is not None
    cursor.close()
    conn.close()

    if not exists:
        session.clear()
        flash("That account no longer exists. Please log in.", "error")
        return redirect(url_for("login"))

# shows the signup page
@app.route("/signup")
def signup():
    return render_template("signup.html")

# receives the submitted form and saves it
# receives the submitted form and saves it
@app.route("/submit_signup", methods=["POST"])
def submit_signup():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm = request.form.get("confirmPassword", "")

    # the browser checks these too, but anyone can skip JavaScript — the server must check again
    errors = []
    if not re.fullmatch(r"[A-Za-z0-9_.]{3,30}", username):
        errors.append("Usernames are 3–30 characters: letters, numbers, _ and . (no spaces).")
    if len(email) > 255 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        errors.append("Please enter a valid email address.")
    if (len(password) < 8 or not re.search(r"[0-9]", password)
            or not re.search(r"[A-Z]", password) or not re.search(r"[^A-Za-z0-9]", password)):
        errors.append("Passwords need 8+ characters, a number, a capital letter and a special character.")
    if password != confirm:
        errors.append("The two passwords don't match.")

    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("signup"))

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (Username, Email, Password_hash, Created_at) "
            "VALUES (%s, %s, %s, NOW())",
            (username, email, generate_password_hash(password))
        )
        conn.commit()
    except mysql.connector.IntegrityError:
        # the UNIQUE rule on Username/Email refused it → someone already has it
        cursor.close()
        conn.close()
        flash("That username or email is already taken.", "error")
        return redirect(url_for("signup"))

    new_user_id = cursor.lastrowid   # the User_id MySQL just gave this account
    cursor.close()
    conn.close()

    # log the new user straight in, and mark them as brand new
    session.clear()
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
            session.clear()
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
        # only APPROVED testimonials count — a pending one isn't on your portfolio yet
    cursor.execute(
        "SELECT COUNT(*) AS total FROM social_proof "
        "WHERE User_id = %s AND Type = 'testimonial' AND Status = 'approved'",
        (session["user_id"],)
    )
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
        "topics": data.get("topics", []),                # the repo's tags, e.g. ["flask", "mysql"]
    }

# finds an open testimonial link: exists, unused, under 30 days old
# also brings back who it's for, their GitHub/Discord (for the "can't verify as yourself" check)
def find_open_request(token):
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT r.Token, r.User_id, r.Project_id, u.Username, u.Full_name, "
        "u.Github_url AS Owner_github, u.Discord_username AS Owner_discord, "
        "p.Title AS Project_title "
        "FROM testimonial_requests r "
        "JOIN users u ON u.User_id = r.User_id "
        "LEFT JOIN projects p ON p.Project_id = r.Project_id "
        "WHERE r.Token = %s AND r.Used_at IS NULL "
        "AND r.Created_at > NOW() - INTERVAL %s DAY",
        (token, REQUEST_DAYS)
    )
    req = cursor.fetchone()
    cursor.close()
    conn.close()
    return req


# swaps the one-time code for a token, then asks the site who signed in
# returns {"via", "id", "handle", "name", "avatar", "url"} — or None if anything fails
def fetch_identity(provider, code):
    settings = OAUTH_PROVIDERS[provider]
    try:
        token_reply = requests.post(
            settings["token_url"],
            data={
                "client_id": settings["client_id"],
                "client_secret": settings["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": url_for("oauth_callback", provider=provider, _external=True),
            },
            headers={"Accept": "application/json"},
            timeout=5,
        )
        access_token = token_reply.json().get("access_token")
        if not access_token:
            return None

        user_reply = requests.get(
            settings["user_url"],
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=5,
        )
        if user_reply.status_code != 200:
            return None
        data = user_reply.json()
    except (requests.RequestException, ValueError):
        return None          # site down, timed out, or sent back something that isn't JSON

    # each site describes people differently — turn its answer into OUR shape
    if provider == "github":
        return {
            "via": "github",
            "id": str(data["id"]),                    # permanent account number
            "handle": data["login"],                  # "sarahdev"
            "name": data.get("name") or data["login"],   # not everyone sets a display name
            "avatar": data.get("avatar_url"),
            "url": data.get("html_url"),              # "https://github.com/sarahdev"
        }
    return None


# True if the person verifying is the portfolio owner themselves
def is_owner_identity(identity, req):
    if identity["via"] == "github" and req["Owner_github"]:
        owner_login = urlparse(req["Owner_github"]).path.strip("/").split("/")[0]
        return identity["handle"].lower() == owner_login.lower()
    if identity["via"] == "discord" and req["Owner_discord"]:
        return identity["handle"].lower() == req["Owner_discord"].lower()
    return False

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

# reads the add/edit project form (used by both routes)
# returns (values, languages, errors)
def read_project_form():
    values = {
        "title": request.form["title"].strip(),
        "description": request.form["description"].strip() or None,
        "problem": request.form["problem"].strip() or None,
        "process": request.form["process"].strip() or None,
        "tools": request.form["tools"].strip() or None,
        "results": request.form["results"].strip() or None,
    }
    values["github_url"], github_error = clean_link(request.form["github_url"], "github.com")
    values["project_link"], link_error = clean_link(request.form["project_link"])   # any website

    # languages: the ticked boxes plus anything typed in "Other languages"
    languages = request.form.getlist("languages")
    name_to_slug = {label.lower(): slug for slug, label in LANGUAGES}
    for name in request.form["other_languages"].split(","):
        name = name.strip().lower()
        if name:
            languages.append(name_to_slug.get(name, name.replace(" ", "-")))
    languages = list(dict.fromkeys(languages))              # remove duplicates, keep order

    errors = []
    if not values["title"]:
        errors.append("Your project needs a title.")
    if github_error:
        errors.append(github_error)
    if link_error:
        errors.append(link_error)
    if any(len(slug) > 45 for slug in languages):
        errors.append("One of your languages is too long.")

    return values, languages, errors


# shows the add-project form AND saves the new project
@app.route("/projects/new", methods=["GET", "POST"])
def new_project():
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    if request.method == "POST":
        values, languages, errors = read_project_form()

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
            (session["user_id"], values["title"], values["description"], values["github_url"],
             values["project_link"], values["problem"], values["process"], values["tools"],
             values["results"])
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


# shows a project's form filled in AND saves the changes
@app.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
def edit_project(project_id):
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

    # only find the project if it belongs to the logged-in user
    cursor.execute(
        "SELECT * FROM projects WHERE Project_id = %s AND User_id = %s",
        (project_id, session["user_id"])
    )
    project = cursor.fetchone()
    if project is None:
        cursor.close()
        conn.close()
        abort(404)                      # doesn't exist, or isn't theirs

    if request.method == "POST":
        values, languages, errors = read_project_form()

        if errors:
            cursor.close()
            conn.close()
            for error in errors:
                flash(error, "error")
            return render_template("project_form.html", form=request.form,
                                   selected=languages, editing=project)

        cursor.execute(
            "UPDATE projects SET Title = %s, Description = %s, Github_url = %s, Project_link = %s, "
            "Problem = %s, Process = %s, Tools = %s, Results = %s "
            "WHERE Project_id = %s AND User_id = %s",
            (values["title"], values["description"], values["github_url"], values["project_link"],
             values["problem"], values["process"], values["tools"], values["results"],
             project_id, session["user_id"])
        )

        # replace the languages: remove the old list, then add the new one
        cursor.execute("DELETE FROM project_languages WHERE Project_id = %s", (project_id,))
        for slug in languages:
            cursor.execute(
                "INSERT INTO project_languages (Project_id, Language) VALUES (%s, %s)",
                (project_id, slug)
            )

        conn.commit()
        cursor.close()
        conn.close()

        flash("Project updated!", "success")
        return redirect(url_for("projects"))

    # normal visit → fill the form with what's saved
    cursor.execute("SELECT Language FROM project_languages WHERE Project_id = %s", (project_id,))
    saved = [row["Language"] for row in cursor.fetchall()]
    cursor.close()
    conn.close()

    known = [slug for slug, label in LANGUAGES]
    form = {
        "title": project["Title"] or "",
        "github_url": project["Github_url"] or "",
        "project_link": project["Project_link"] or "",
        "description": project["Description"] or "",
        "problem": project["Problem"] or "",
        "process": project["Process"] or "",
        "tools": project["Tools"] or "",
        "results": project["Results"] or "",
        # languages without a checkbox (e.g. ruby) go back in the "Other languages" box
        "other_languages": ", ".join(slug for slug in saved if slug not in known),
    }
    return render_template("project_form.html", form=form, selected=saved, editing=project)

# deletes one of the logged-in user's projects
@app.route("/projects/<int:project_id>/delete", methods=["POST"])
def delete_project(project_id):
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor()
    # AND User_id: you can only delete YOUR projects (same IDOR check as edit)
    cursor.execute(
        "DELETE FROM projects WHERE Project_id = %s AND User_id = %s",
        (project_id, session["user_id"])
    )
    deleted = cursor.rowcount        # how many rows were removed: 1, or 0 if it wasn't theirs
    conn.commit()
    cursor.close()
    conn.close()

    if deleted == 0:
        abort(404)

    flash("Project deleted.", "success")
    return redirect(url_for("projects"))



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


# the owner's testimonials page: make links, see open links, see what people wrote
@app.route("/testimonials")
def testimonials():
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

    # your projects, for the "About" dropdown
    cursor.execute(
        "SELECT Project_id, Title FROM projects WHERE User_id = %s ORDER BY Title",
        (session["user_id"],)
    )
    my_projects = cursor.fetchall()

    # links that haven't been used AND are less than 30 days old (+ the project's title, if any)
    cursor.execute(
        "SELECT r.Token, r.Label, r.Created_at, p.Title AS Project_title "
        "FROM testimonial_requests r "
        "LEFT JOIN projects p ON p.Project_id = r.Project_id "
        "WHERE r.User_id = %s AND r.Used_at IS NULL "
        "AND r.Created_at > NOW() - INTERVAL %s DAY "
        "ORDER BY r.Created_at DESC",
        (session["user_id"], REQUEST_DAYS)
    )
    open_requests = cursor.fetchall()

    # what people wrote: pending first (needs your attention), then approved, then hidden
    cursor.execute(
        "SELECT s.*, p.Title AS Project_title "
        "FROM social_proof s "
        "LEFT JOIN projects p ON p.Project_id = s.Project_id "
        "WHERE s.User_id = %s AND s.Type = 'testimonial' "
        "ORDER BY FIELD(s.Status, 'pending', 'approved', 'hidden'), s.Created_at DESC",
        (session["user_id"],)
    )
    received = cursor.fetchall()

    cursor.close()
    conn.close()

    # "github" → "GitHub" for the ✔ Verified tag
    provider_labels = {key: s["label"] for key, s in OAUTH_PROVIDERS.items()}

    return render_template("testimonials.html", my_projects=my_projects,
                           open_requests=open_requests, received=received, days=REQUEST_DAYS,
                           provider_labels=provider_labels)


# makes a new single-use link
@app.route("/testimonials/request", methods=["POST"])
def request_testimonial():
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    label = request.form.get("label", "").strip()[:100] or None
    # "" (General) or anything that isn't a number → None
    project_id = request.form.get("project_id", type=int)

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor()

    # the dropdown can be edited in the browser, so check the project really is yours
    if project_id is not None:
        cursor.execute(
            "SELECT 1 FROM projects WHERE Project_id = %s AND User_id = %s",
            (project_id, session["user_id"])
        )
        if cursor.fetchone() is None:
            cursor.close()
            conn.close()
            flash("Please choose one of your own projects.", "error")
            return redirect(url_for("testimonials"))

    token = secrets.token_urlsafe(32)        # 43 random characters, e.g. "x7Kp2mQ9vLr..."
    cursor.execute(
        "INSERT INTO testimonial_requests (Token, User_id, Label, Project_id) "
        "VALUES (%s, %s, %s, %s)",
        (token, session["user_id"], label, project_id)
    )
    conn.commit()
    cursor.close()
    conn.close()

    flash("Link created! Copy it below and send it.", "success")
    return redirect(url_for("testimonials"))


# cancels a link that hasn't been used (e.g. sent to the wrong person)
@app.route("/testimonials/request/<token>/cancel", methods=["POST"])
def cancel_request(token):
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor()
    # AND User_id: only YOUR links.  AND Used_at IS NULL: used links are kept as a record
    cursor.execute(
        "DELETE FROM testimonial_requests "
        "WHERE Token = %s AND User_id = %s AND Used_at IS NULL",
        (token, session["user_id"])
    )
    conn.commit()
    cursor.close()
    conn.close()

    flash("Link cancelled.", "success")
    return redirect(url_for("testimonials"))

# approve or hide one of YOUR testimonials
@app.route("/testimonials/<int:proof_id>/status", methods=["POST"])
def set_testimonial_status(proof_id):
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    # the button sends "approved" or "hidden" — anything else is someone messing with the form
    new_status = request.form.get("status")
    if new_status not in ("approved", "hidden"):
        abort(400)

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor()

    # ownership check: is this testimonial yours?
    cursor.execute(
        "SELECT 1 FROM social_proof "
        "WHERE Proof_id = %s AND User_id = %s AND Type = 'testimonial'",
        (proof_id, session["user_id"])
    )
    if cursor.fetchone() is None:
        cursor.close()
        conn.close()
        abort(404)

    cursor.execute(
        "UPDATE social_proof SET Status = %s WHERE Proof_id = %s AND User_id = %s",
        (new_status, proof_id, session["user_id"])
    )
    conn.commit()
    cursor.close()
    conn.close()

    if new_status == "approved":
        flash("Approved! It will show on your portfolio.", "success")
    else:
        flash("Hidden. It won't show on your portfolio.", "success")
    return redirect(url_for("testimonials"))


# deletes a testimonial for good — only allowed once it's hidden
@app.route("/testimonials/<int:proof_id>/delete", methods=["POST"])
def delete_testimonial(proof_id):
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password=os.getenv("DB_PASSWORD"),
        database="mydb"
    )
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM social_proof "
        "WHERE Proof_id = %s AND User_id = %s AND Type = 'testimonial' AND Status = 'hidden'",
        (proof_id, session["user_id"])
    )
    deleted = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()

    if deleted == 0:
        flash("Only your hidden testimonials can be deleted. Hide it first.", "error")
    else:
        flash("Testimonial deleted.", "success")
    return redirect(url_for("testimonials"))

# the PUBLIC page where someone writes a testimonial — no login needed
@app.route("/t/<token>", methods=["GET", "POST"])
def write_testimonial(token):
    req = find_open_request(token)
    if req is None:
        return render_template("testimonial_write.html", state="invalid"), 404

    owner_name = req["Full_name"] or req["Username"]

    # the owner opened their own link (e.g. to test it) — don't let them review themselves
    if session.get("user_id") == req["User_id"]:
        return render_template("testimonial_write.html", state="own_link",
                               owner_name=owner_name, req=req)

    # who they verified as (from the session, never the form) — only counts for THIS link
    verified = session.get("verified")
    if not verified or verified.get("token") != token:
        verified = None
    verified_label = OAUTH_PROVIDERS[verified["via"]]["label"] if verified else None

    # Verify buttons: only sites whose keys are in .env
    providers = [(key, s["label"]) for key, s in OAUTH_PROVIDERS.items() if s["client_id"]]

    # everything the form needs, in one place (used for both normal visits and errors)
    page = dict(state="form", req=req, owner_name=owner_name, relationships=RELATIONSHIPS,
                verified=verified, verified_label=verified_label, providers=providers)

    if request.method == "POST":
        # verified → the name comes from GitHub, so it can't be changed in the browser
        if verified:
            author_name = verified["name"][:100]
        else:
            author_name = request.form.get("author_name", "").strip()
        author_role = request.form.get("author_role", "").strip() or None
        relationship = request.form.get("relationship", "")
        content = request.form.get("content", "").strip()

        errors = []
        if not author_name:
            errors.append("Please add your name.")
        elif len(author_name) > 100:
            errors.append("Your name is too long (100 characters max).")
        if author_role and len(author_role) > 150:
            errors.append("Your role is too long (150 characters max).")
        if relationship not in RELATIONSHIPS:
            errors.append("Please choose how you know them.")
        if len(content) < 20:
            errors.append("Please write at least a sentence or two (20+ characters).")
        elif len(content) > 1000:
            errors.append("Please keep it under 1000 characters.")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("testimonial_write.html", form=request.form, **page)

        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password=os.getenv("DB_PASSWORD"),
            database="mydb"
        )
        cursor = conn.cursor()

        # "claim" the link first: only succeeds if nobody has used it yet
        cursor.execute(
            "UPDATE testimonial_requests SET Used_at = NOW() "
            "WHERE Token = %s AND Used_at IS NULL",
            (token,)
        )
        if cursor.rowcount == 0:
            # someone else used it in the split second since we checked
            conn.rollback()
            cursor.close()
            conn.close()
            return render_template("testimonial_write.html", state="invalid"), 404

        v = verified or {}     # empty dict → every .get() below gives None (= not verified)
        cursor.execute(
            "INSERT INTO social_proof (User_id, Project_id, Type, Content, Source, "
            "Author_name, Author_role, Relationship, Status, "
            "Verified_via, Verified_id, Verified_handle, Verified_url, Image_url, Created_at) "
            "VALUES (%s, %s, 'testimonial', %s, 'request', %s, %s, %s, 'pending', "
            "%s, %s, %s, %s, %s, NOW())",
            (req["User_id"], req["Project_id"], content,
             author_name, author_role, relationship,
             v.get("via"), v.get("id"), v.get("handle"), v.get("url"), v.get("avatar"))
        )
        conn.commit()          # both changes are saved together, or neither is
        cursor.close()
        conn.close()

        session.pop("verified", None)              # done with it
        session["thanked_owner"] = owner_name      # so the thank-you page can say who
        return redirect(url_for("testimonial_thanks"))

    # normal visit → empty form
    return render_template("testimonial_write.html", form={}, **page)


# shown after sending a testimonial
@app.route("/t/thanks")
def testimonial_thanks():
    owner_name = session.pop("thanked_owner", None)
    return render_template("testimonial_write.html", state="thanks", owner_name=owner_name)

# step 1: send the testimonial writer to GitHub (or Discord / LinkedIn later)
@app.route("/t/<token>/verify/<provider>")
def start_verify(token, provider):
    settings = OAUTH_PROVIDERS.get(provider)
    if settings is None or not settings["client_id"]:
        abort(404)                       # unknown site, or its keys aren't in .env
    if find_open_request(token) is None:
        return render_template("testimonial_write.html", state="invalid"), 404

    # a random value we expect to get back — proves the reply is for THIS sign-in
    state = secrets.token_urlsafe(16)
    session["oauth"] = {"state": state, "token": token, "provider": provider}

    params = {
        "client_id": settings["client_id"],
        "redirect_uri": url_for("oauth_callback", provider=provider, _external=True),
        "state": state,
        "scope": settings["scope"],
        "response_type": "code",
    }
    return redirect(settings["authorize_url"] + "?" + urlencode(params))


# step 2: the site sends them back here
@app.route("/oauth/callback/<provider>")
def oauth_callback(provider):
    pending = session.pop("oauth", None)     # read it AND remove it — each state works once
    if pending is None:
        # no sign-in in progress (reloaded, or a different browser) — explain on its own page
        return render_template("testimonial_write.html", state="signin_expired"), 400

    back_to_form = url_for("write_testimonial", token=pending["token"])

    # they clicked "Cancel" on GitHub's page
    if request.args.get("error"):
        flash("Verification cancelled. You can still send your testimonial unverified.", "error")
        return redirect(back_to_form)

    # the state must match what we sent — otherwise this reply isn't for this sign-in
    if (provider != pending["provider"] or provider not in OAUTH_PROVIDERS
            or not secrets.compare_digest(request.args.get("state", ""), pending["state"])):
        flash("Verification failed. Please try again.", "error")
        return redirect(back_to_form)

    req = find_open_request(pending["token"])
    if req is None:
        return render_template("testimonial_write.html", state="invalid"), 404

    identity = fetch_identity(provider, request.args.get("code", ""))
    if identity is None:
        flash("We couldn't verify you right now. Please try again.", "error")
        return redirect(back_to_form)

    if is_owner_identity(identity, req):
        flash("You can't verify as the portfolio owner.", "error")
        return redirect(back_to_form)

    # remember who they are — tied to THIS link only
    session["verified"] = dict(identity, token=pending["token"])
    label = OAUTH_PROVIDERS[provider]["label"]
    flash(f"Verified as @{identity['handle']} via {label}.", "success")
    return redirect(back_to_form)

# "Not you?" — forget the verification (e.g. signed in with the wrong account)
@app.route("/t/<token>/unverify")
def unverify(token):
    session.pop("verified", None)
    return redirect(url_for("write_testimonial", token=token))


# confirm page + the actual delete — needs your password
@app.route("/account/delete", methods=["GET", "POST"])
def delete_account():
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    if request.method == "POST":
        password = request.form.get("password", "")

        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password=os.getenv("DB_PASSWORD"),
            database="mydb"
        )
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT Password_hash FROM users WHERE User_id = %s", (session["user_id"],))
        user = cursor.fetchone()

        if not user or not user["Password_hash"] or not check_password_hash(user["Password_hash"], password):
            cursor.close()
            conn.close()
            flash("That password isn't right.", "error")
            return redirect(url_for("delete_account"))

        # ON DELETE CASCADE removes projects, languages, testimonials and links too
        cursor.execute("DELETE FROM users WHERE User_id = %s", (session["user_id"],))
        conn.commit()
        cursor.close()
        conn.close()

        session.clear()
        flash("Your account and everything in it has been deleted.")
        return redirect(url_for("login"))

    return render_template("delete_account.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))

# runs when a form is sent without a valid token (e.g. the page was open too long)
# runs when a form is sent without a valid token (e.g. the page was open too long)
@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    flash("Your form expired. Please try again.", "error")
    back = request.referrer
    # only send people back to a page on OUR site
    if not back or not back.startswith(request.host_url):
        back = url_for("dashboard")
    return redirect(back)


@app.route("/")
def index():
    return redirect(url_for("about"))   # the main address goes to the homepage


@app.route("/home")
def about():
    return render_template("home.html")


    



if __name__ == "__main__":
    app.run(debug=True)