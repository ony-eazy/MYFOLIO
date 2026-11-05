// ---- Grab all the elements we need by their IDs ----
const continueBtn = document.getElementById("continueBtn");
const passwordSection = document.getElementById("passwordSection");

const continueBtn2 = document.getElementById("continueBtn2");
const confirmSection = document.getElementById("confirmSection");

const passwordInput = document.getElementById("password");
const confirmPasswordInput = document.getElementById("confirmPassword");
const strengthMsg = document.getElementById("strengthMsg");
const matchMsg = document.getElementById("matchMsg");
const form = document.querySelector("form");

// ---- STEP 1: reveal the password section ----
continueBtn.addEventListener("click", function () {
  passwordSection.style.display = "block";
  continueBtn.style.display = "none";
});

// ---- STEP 2: reveal the confirm password section ----
continueBtn2.addEventListener("click", function () {
  confirmSection.style.display = "block";
  continueBtn2.style.display = "none";
});

// ---- CHECK 1: password strength, live as they type ----
passwordInput.addEventListener("input", function () {
    const pw = passwordInput.value;

    const longEnough = pw.length >= 8;
    const hasNumber = /[0-9]/.test(pw);
    const hasUpper = /[A-Z]/.test(pw);
    const hasSpecial = /[^A-Za-z0-9]/.test(pw);

    if (longEnough && hasNumber && hasUpper && hasSpecial) {
        strengthMsg.textContent = "Strong Password ✓";
        strengthMsg.style.color = "green";
    } else {
        strengthMsg.textContent = "Weak: needs 8+ characters, a number, a capital letter, and a special character";
        strengthMsg.style.color = "red";
    }
});

// ---- CHECK 2: strength + match, when they submit ----
form.addEventListener("submit", function (event) {
    const pw = passwordInput.value;
    const confirm = confirmPasswordInput.value;

    const strong = pw.length >= 8 && /[0-9]/.test(pw) && /[A-Z]/.test(pw) && /[^A-Za-z0-9]/.test(pw);

    if (!strong) {
        event.preventDefault();
        matchMsg.textContent = "Password is not strong enough!";
        matchMsg.style.color = "red";
        return;
    }

    if (pw !== confirm) {
        event.preventDefault();
        matchMsg.textContent = "Passwords do not match!";
        matchMsg.style.color = "red";
    }
});