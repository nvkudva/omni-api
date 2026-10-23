// omni product site — nav, copy buttons, reveal, counters, screenshot theme.

const nav = document.getElementById("nav");
const navLinks = document.getElementById("navLinks");
const navToggle = document.getElementById("navToggle");

function onScroll() {
  nav.classList.toggle("is-stuck", window.scrollY > 8);
}
window.addEventListener("scroll", onScroll, { passive: true });
onScroll();

navToggle.addEventListener("click", () => {
  const isOpen = navLinks.classList.toggle("is-open");
  navToggle.setAttribute("aria-expanded", String(isOpen));
});

navLinks.addEventListener("click", (event) => {
  if (event.target.tagName !== "A") return;
  navLinks.classList.remove("is-open");
  navToggle.setAttribute("aria-expanded", "false");
});

// ── copy buttons ──────────────────────────────────────────────────────────
const DONE_ICON = '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M6.2 11.6 2.6 8l1.1-1.1 2.5 2.5 6.1-6.1L13.4 4.4Z"/></svg>';

document.querySelectorAll(".copy-btn").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.querySelector(button.dataset.copy);
    if (!target) return;
    const text = target.innerText.trim();
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const scratch = document.createElement("textarea");
      scratch.value = text;
      scratch.setAttribute("readonly", "");
      scratch.style.position = "fixed";
      scratch.style.opacity = "0";
      document.body.appendChild(scratch);
      scratch.select();
      document.execCommand("copy");
      scratch.remove();
    }
    const original = button.innerHTML;
    button.innerHTML = DONE_ICON;
    button.classList.add("is-done");
    setTimeout(() => {
      button.innerHTML = original;
      button.classList.remove("is-done");
    }, 1600);
  });
});

// ── reveal on scroll ──────────────────────────────────────────────────────
const revealer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry, index) => {
      if (!entry.isIntersecting) return;
      entry.target.style.transitionDelay = `${Math.min(index * 70, 350)}ms`;
      entry.target.classList.add("is-in");
      revealer.unobserve(entry.target);
    });
  },
  { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
);
document.querySelectorAll(".reveal").forEach((element) => revealer.observe(element));

// ── counters ──────────────────────────────────────────────────────────────
const counter = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const element = entry.target;
      const target = Number(element.dataset.count);
      const decimals = Number(element.dataset.decimals ?? 0);
      const duration = 1200;
      const start = performance.now();
      element.textContent = "0";
      const step = (now) => {
        const progress = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        element.textContent = (target * eased).toFixed(decimals);
        if (progress < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
      counter.unobserve(element);
    });
  },
  { threshold: 0.6 }
);
document.querySelectorAll(".count").forEach((element) => counter.observe(element));

// ── playground screenshot theme ───────────────────────────────────────────
const playground = document.getElementById("playground");
document.querySelectorAll("[data-shot-theme]").forEach((button) => {
  button.addEventListener("click", () => {
    playground.dataset.shotTheme = button.dataset.shotTheme;
    document.querySelectorAll("[data-shot-theme]").forEach((other) => {
      const isActive = other === button;
      other.classList.toggle("is-active", isActive);
      other.setAttribute("aria-pressed", String(isActive));
    });
  });
});

// ── API example tabs ──────────────────────────────────────────────────────
document.querySelectorAll("[data-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll("[data-tab]").forEach((other) => {
      const isActive = other === tab;
      other.classList.toggle("is-active", isActive);
      other.setAttribute("aria-selected", String(isActive));
    });
    document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.tabPanel !== tab.dataset.tab;
    });
  });
});
