// "Detect" button: asks our /api/github route about the repo and fills in the form
const detectBtn = document.getElementById("detectBtn");
const statusMsg = document.getElementById("detectStatus");

// shows a message under the GitHub box ("success" = amber, "error" = red)
function showStatus(text, type) {
  statusMsg.textContent = text;
  statusMsg.className = "detect-status " + type;
}

// only fill a box if it's empty, so we never wipe what the user typed
function fillIfEmpty(id, value) {
  const box = document.getElementById(id);
  if (value && !box.value.trim()) {
    box.value = value;
  }
}

detectBtn.addEventListener("click", async () => {
  const githubBox = document.getElementById("github_url");
  const url = githubBox.value.trim();
  if (!url) {
    showStatus("Paste a GitHub link first.", "error");
    return;
  }

  // stop double-clicks while we wait
  detectBtn.disabled = true;
  detectBtn.textContent = "Detecting…";

  try {
    const response = await fetch("/api/github?url=" + encodeURIComponent(url));
    const repo = await response.json();

    if (!response.ok) {                    // our 401 / 404 replies
      showStatus(repo.error, "error");
      return;
    }

    fillIfEmpty("title", repo.title);
    fillIfEmpty("description", repo.description);
    fillIfEmpty("project_link", repo.homepage);
    fillIfEmpty("tools", repo.topics.join(", "));   // ["flask", "mysql"] → "flask, mysql"
    githubBox.value = repo.github_url;     // swap in GitHub's clean, official link

    // tick the checkboxes we have; anything else goes in "Other languages"
    const others = [];
    for (const slug of repo.languages) {
      const box = document.querySelector(`input[name="languages"][value="${CSS.escape(slug)}"]`);
      if (box) {
        box.checked = true;
      } else {
        others.push(slug);
      }
    }
    const otherBox = document.getElementById("other_languages");
    const typed = otherBox.value.split(",").map(s => s.trim().toLowerCase()).filter(Boolean);
    otherBox.value = [...new Set([...typed, ...others])].join(", ");   // no repeats

    const count = repo.languages.length;
    showStatus(`Found "${repo.title}": ${count} language${count === 1 ? "" : "s"} detected.`, "success");
  } catch (err) {
    showStatus("Couldn't reach the server. Try again.", "error");
  } finally {
    // runs whether it worked or not
    detectBtn.disabled = false;
    detectBtn.textContent = "Detect";
  }
});
