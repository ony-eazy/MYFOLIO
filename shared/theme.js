// Grab the toggle button
const themeToggler = document.querySelector('#themeToggler');

// On click: flip the light-mode class, then save the choice
themeToggler.addEventListener('click', function () {
    document.body.classList.toggle('light-mode');

    if (document.body.classList.contains('light-mode')) {
        localStorage.setItem('myfolio_theme', 'light');
        themeToggler.textContent = '🌙';   // in light mode, offer to go dark
    } else {
        localStorage.setItem('myfolio_theme', 'dark');
        themeToggler.textContent = '🌞';   // in dark mode, offer to go light
    }
});

// On page load: re-apply the saved choice so it sticks after refresh
function retrieveTheme() {
    const theme = localStorage.getItem('myfolio_theme');
    if (theme === 'light') {
        document.body.classList.add('light-mode');
        themeToggler.textContent = '🌙';
    } else {
        themeToggler.textContent = '🌞';
    }
}
retrieveTheme();