// Theme toggle functionality
document.addEventListener("DOMContentLoaded", () => {
  const themeToggle = document.getElementById("theme-toggle")

  // Check for saved theme preference or use the system preference
  const savedTheme = localStorage.getItem("theme")
  const systemPrefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches

  // Apply the theme
  if (savedTheme === "dark" || (!savedTheme && systemPrefersDark)) {
    document.body.classList.add("dark")
  }

  // Toggle theme when button is clicked
  themeToggle.addEventListener("click", () => {
    document.body.classList.toggle("dark")

    // Save the preference
    if (document.body.classList.contains("dark")) {
      localStorage.setItem("theme", "dark")
    } else {
      localStorage.setItem("theme", "light")
    }
  })
})
