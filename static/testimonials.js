// "Copy" button: copies the link in the box right before it
document.querySelectorAll(".copy-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    const box = btn.previousElementSibling;      // the link box
    try {
      await navigator.clipboard.writeText(box.value);
    } catch {
      box.select();                              // fallback: highlight it so they can Ctrl+C
    }
    btn.textContent = "Copied!";
    setTimeout(() => btn.textContent = "Copy", 1500);   // back to "Copy" after 1.5s
  });
});