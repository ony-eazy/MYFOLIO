"""
End-to-end test for Myfolio.

What it does:
  Creates a throwaway account (zz_test_xxxxxx), goes through every feature —
  signup, login, profile, projects, CSRF, testimonials, delete project,
  delete account — then checks nothing is left behind in the database.
  If the test crashes halfway, the cleanup at the end still removes the account.

How to run it (from the MYFOLIO folder, with (.venv) active):
  python tests/e2e_test.py

It uses your real database (mydb), but only ever touches its own test account.
The Flask server does NOT need to be running — it talks to app.py directly.
"""
import os, re, sys, secrets as _s
from pathlib import Path

# find the project folder (the one above tests/) so "import app" works from anywhere
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)
import app as m
import mysql.connector

def db():
    return mysql.connector.connect(host="localhost", user="root",
                                   password=os.getenv("DB_PASSWORD"), database="mydb")

def q(sql, args=()):
    c = db(); cur = c.cursor(); cur.execute(sql, args); rows = cur.fetchall(); c.close(); return rows

results = []
def check(name, ok, detail=""):
    results.append((ok, name, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))

def token_of(client, path="/login"):
    html = client.get(path).data.decode()
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

def post(client, path, data, page=None, follow=True):
    data = dict(data); data["csrf_token"] = token_of(client, page or path)
    return client.post(path, data=data, follow_redirects=follow)

U = "zz_test_" + _s.token_hex(3)
E = U + "@example.com"
PW = "Testpass1!"
uid = None
try:
    a = m.app.test_client()

    # ---------- signup validation ----------
    before = q("SELECT COUNT(*) FROM users")[0][0]
    r = post(a, "/submit_signup", {"username": "bad name/x", "email": "nope", "password": "weak",
                                   "confirmPassword": "different"}, page="/signup")
    body = r.data.decode()
    check("signup: bad input shows errors", "Usernames are 3" in body and "valid email" in body
          and "Passwords need" in body and "match" in body)
    check("signup: bad input creates no account", q("SELECT COUNT(*) FROM users")[0][0] == before)

    # ---------- good signup ----------
    r = post(a, "/submit_signup", {"username": U, "email": E.upper(), "password": PW,
                                   "confirmPassword": PW}, page="/signup")
    check("signup: good input lands on dashboard", r.status_code == 200 and b"Account created" in r.data)
    row = q("SELECT User_id, Email, Created_at FROM users WHERE Username = %s", (U,))
    uid = row[0][0] if row else None
    check("signup: user saved", uid is not None)
    check("signup: email stored lowercase", row and row[0][1] == E)
    check("signup: Created_at filled in", row and row[0][2] is not None)

    # ---------- duplicate signup ----------
    b = m.app.test_client()
    r = post(b, "/submit_signup", {"username": U, "email": "other_" + E, "password": PW,
                                   "confirmPassword": PW}, page="/signup")
    check("signup: duplicate username -> friendly error, no crash",
          r.status_code == 200 and b"already taken" in r.data)

    # ---------- logout / login ----------
    r = a.get("/logout", follow_redirects=True)
    check("logout works", b"logged out" in r.data)
    r = post(a, "/login", {"username": U, "password": "wrong"})
    check("login: wrong password refused", b"Invalid username or password" in r.data)
    r = post(a, "/login", {"username": U, "password": PW})
    check("login: right password -> dashboard", b"Welcome back" in r.data)
    check("login: old 'new user' greeting cleared", b"Account created" not in r.data)

    # ---------- profile ----------
    r = post(a, "/profile", {"full_name": "Zz Tester", "role_headline": "Test dev", "bio": "Bio",
                             "skills": "Python", "github_url": "github.com/zz", "linkedin_url": "",
                             "discord_username": "@ZZ_test"})
    check("profile: saves", b"Profile updated" in r.data)
    p = q("SELECT Full_name, Github_url, Discord_username FROM users WHERE User_id=%s", (uid,))[0]
    check("profile: values tidied", p == ("Zz Tester", "https://github.com/zz", "zz_test"), str(p))
    r = post(a, "/profile", {"full_name": "Zz Tester", "role_headline": "", "bio": "", "skills": "",
                             "github_url": "gitlab.com/x", "linkedin_url": "", "discord_username": ""})
    check("profile: wrong-site GitHub link refused", b"github.com link" in r.data)

    # ---------- projects ----------
    proj = {"title": "ZZ Project", "description": "Desc", "problem": "", "process": "", "tools": "",
            "results": "", "github_url": "", "project_link": "zz.example.com",
            "other_languages": "Ruby, python", "languages": ["python", "html"]}
    r = post(a, "/projects/new", proj, page="/projects/new")
    check("projects: add", b"Project added" in r.data)
    pid = q("SELECT Project_id FROM projects WHERE User_id=%s", (uid,))[0][0]
    langs = sorted(x[0] for x in q("SELECT Language FROM project_languages WHERE Project_id=%s", (pid,)))
    check("projects: languages saved + deduped", langs == ["html", "python", "ruby"], str(langs))
    check("projects: python filter shows it", b"ZZ Project" in a.get("/projects?lang=python").data)
    check("projects: other filter shows it (ruby)", b"ZZ Project" in a.get("/projects?lang=other").data)
    check("projects: java filter hides it", b"ZZ Project" not in a.get("/projects?lang=java").data)
    r = a.get(f"/projects/{pid}/edit")
    check("projects: edit form prefilled", b"ZZ Project" in r.data and b"ruby" in r.data and b"Save changes" in r.data)
    r = post(a, f"/projects/{pid}/edit", dict(proj, title="ZZ Renamed", languages=["css"],
             other_languages=""), page=f"/projects/{pid}/edit")
    check("projects: edit saves", b"Project updated" in r.data and b"ZZ Renamed" in r.data)
    langs = [x[0] for x in q("SELECT Language FROM project_languages WHERE Project_id=%s", (pid,))]
    check("projects: edit replaces languages", langs == ["css"], str(langs))
    r = post(a, "/projects/new", dict(proj, title=""), page="/projects/new")
    check("projects: empty title refused", b"needs a title" in r.data)
    other = q("SELECT Project_id FROM projects WHERE User_id <> %s LIMIT 1", (uid,))
    other_id = other[0][0] if other else 999999
    check("projects: someone else's edit page -> 404", a.get(f"/projects/{other_id}/edit").status_code == 404)
    r = post(a, f"/projects/{other_id}/delete", {}, page="/projects/new", follow=False)
    check("projects: deleting someone else's -> 404", r.status_code == 404)
    check("projects: their project untouched", other_id == 999999 or
          q("SELECT COUNT(*) FROM projects WHERE Project_id=%s", (other_id,))[0][0] == 1)

    # second project, to test "delete project keeps testimonial as General"
    post(a, "/projects/new", dict(proj, title="ZZ Second", languages=[], other_languages=""), page="/projects/new")
    pid2 = q("SELECT Project_id FROM projects WHERE User_id=%s AND Title='ZZ Second'", (uid,))[0][0]

    # ---------- CSRF ----------
    r = a.post("/projects/new", data=proj, follow_redirects=True)
    check("csrf: form without token blocked", b"form expired" in r.data)
    check("csrf: nothing saved without token", q("SELECT COUNT(*) FROM projects WHERE User_id=%s", (uid,))[0][0] == 2)
    r = a.post("/projects/new", data=proj, headers={"Referer": "https://evil.example/"})
    check("csrf: never redirects to another site", "evil.example" not in r.headers.get("Location", ""))

    # ---------- testimonials: request links ----------
    r = post(a, "/testimonials/request", {"label": "Sarah", "project_id": str(pid)}, page="/testimonials")
    check("testimonials: link created", b"Link created" in r.data and b"About: ZZ Renamed" in r.data)
    tok = re.search(rb"/t/([A-Za-z0-9_-]{40,})", r.data).group(1).decode()
    r = post(a, "/testimonials/request", {"label": "x", "project_id": str(other_id)}, page="/testimonials")
    check("testimonials: can't request about someone else's project", b"your own projects" in r.data)
    post(a, "/testimonials/request", {"label": "Cancel me", "project_id": ""}, page="/testimonials")
    tok_c = q("SELECT Token FROM testimonial_requests WHERE User_id=%s AND Label='Cancel me'", (uid,))[0][0]
    r = post(a, f"/testimonials/request/{tok_c}/cancel", {}, page="/testimonials")
    check("testimonials: cancel link", b"Link cancelled" in r.data and
          q("SELECT COUNT(*) FROM testimonial_requests WHERE Token=%s", (tok_c,))[0][0] == 0)
    check("testimonials: owner opening own link is blocked", b"your own link" in a.get(f"/t/{tok}").data)

    # ---------- testimonials: a stranger writes one ----------
    s = m.app.test_client()
    check("stranger: fake link -> 404", s.get("/t/not-real").status_code == 404)
    r = s.get(f"/t/{tok}")
    check("stranger: form shows owner + project", b"Zz Tester" in r.data and b"ZZ Renamed" in r.data)
    w = {"author_name": "Sarah <b>Bold</b>", "author_role": "PM", "relationship": "Banana", "content": "short"}
    r = post(s, f"/t/{tok}", w)
    check("stranger: bad input refused", b"how you know them" in r.data and b"20+ characters" in r.data)
    check("stranger: typed input kept", b"Sarah &lt;b&gt;Bold&lt;/b&gt;" in r.data)
    w.update(relationship="Client", content="Great to work with, delivered on time! 🔥")
    r = post(s, f"/t/{tok}", w)
    check("stranger: valid testimonial -> thank-you page", b"Thank you" in r.data and b"Zz Tester" in r.data)
    check("stranger: link now single-use", s.get(f"/t/{tok}").status_code == 404)
    row = q("SELECT Proof_id, Status, Content, Project_id, Source FROM social_proof WHERE User_id=%s", (uid,))
    check("stranger: saved as pending, emoji intact, linked to project",
          len(row) == 1 and row[0][1] == "pending" and "🔥" in row[0][2] and row[0][3] == pid
          and row[0][4] == "request", str(row))
    proof = row[0][0]

    # ---------- owner reviews ----------
    r = a.get("/testimonials")
    check("owner: pending badge in nav", b'class="nav-badge"' in r.data)
    check("owner: testimonial text escaped (no HTML injection)", b"<b>Bold</b>" not in r.data)
    check("owner: used link gone from Open links", tok.encode() not in r.data)
    d = a.get("/dashboard").data
    check("dashboard: pending does NOT tick the step", b"5 of 6" in d)
    r = post(a, f"/testimonials/{proof}/status", {"status": "banana"}, page="/testimonials", follow=False)
    check("owner: made-up status -> 400", r.status_code == 400)
    r = post(a, f"/testimonials/{proof}/delete", {}, page="/testimonials")
    check("owner: can't delete before hiding", b"Hide it first" in r.data)
    r = post(a, f"/testimonials/{proof}/status", {"status": "approved"}, page="/testimonials")
    check("owner: approve", b"Approved" in r.data and b'class="nav-badge"' not in r.data)
    check("dashboard: approved ticks the step (100%)", b"6 of 6" in a.get("/dashboard").data)
    r = post(a, f"/testimonials/{proof}/status", {"status": "hidden"}, page="/testimonials")
    check("owner: hide", b"Hidden" in r.data)

    # second testimonial about project 2, then delete project 2 -> becomes General
    post(a, "/testimonials/request", {"label": "P2", "project_id": str(pid2)}, page="/testimonials")
    tok2 = q("SELECT Token FROM testimonial_requests WHERE User_id=%s AND Label='P2'", (uid,))[0][0]
    post(s, f"/t/{tok2}", dict(w, author_name="Tom"))
    r = post(a, f"/projects/{pid2}/delete", {}, page="/projects/new")
    check("projects: delete own project", b"Project deleted" in r.data)
    pj = q("SELECT Project_id FROM social_proof WHERE User_id=%s AND Author_name='Tom'", (uid,))
    check("projects: its testimonial kept as General (SET NULL)", pj == [(None,)], str(pj))

    r = post(a, f"/testimonials/{proof}/delete", {}, page="/testimonials")
    check("owner: delete hidden testimonial", b"Testimonial deleted" in r.data)

    # ---------- delete account ----------
    phone = m.app.test_client()          # same account logged in on "another device"
    post(phone, "/login", {"username": U, "password": PW})
    r = post(a, "/account/delete", {"password": "wrong"}, page="/account/delete")
    check("delete account: wrong password refused", b"password isn" in r.data)
    check("delete account: still exists after wrong password", q("SELECT COUNT(*) FROM users WHERE User_id=%s", (uid,))[0][0] == 1)
    r = post(a, "/account/delete", {"password": PW}, page="/account/delete")
    check("delete account: right password deletes + logs out", b"has been deleted" in r.data)
    r = phone.get("/dashboard", follow_redirects=True)
    check("other device: logged out cleanly, no crash", r.status_code == 200 and b"no longer exists" in r.data)
    left = {t: q(f"SELECT COUNT(*) FROM {t} WHERE User_id=%s", (uid,))[0][0]
            for t in ("users", "projects", "social_proof", "testimonial_requests")}
    left["project_languages"] = q("SELECT COUNT(*) FROM project_languages WHERE Project_id IN (%s,%s)", (pid, pid2))[0][0]
    check("delete account: nothing left in any table", all(v == 0 for v in left.values()), str(left))

    # ---------- public pages ----------
    v = m.app.test_client()
    for path, code in [("/", 302), ("/home", 200), ("/login", 200), ("/signup", 200), ("/dashboard", 302)]:
        check(f"public: {path} -> {code}", v.get(path).status_code == code)
finally:
    if uid and q("SELECT COUNT(*) FROM users WHERE User_id=%s", (uid,))[0][0]:
        c = db(); cur = c.cursor(); cur.execute("DELETE FROM users WHERE User_id=%s", (uid,)); c.commit(); c.close()
        print("cleanup: removed leftover test account")

failed = [r for r in results if not r[0]]
print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
