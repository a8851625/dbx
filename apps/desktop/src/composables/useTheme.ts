import { computed, ref } from "vue";
import {
  APP_THEME_STORAGE_KEY,
  normalizeAppThemeMode,
  resolveAppThemeAppearance,
  type AppThemeMode,
} from "@/lib/appTheme";

const themeMode = ref<AppThemeMode>(
  normalizeAppThemeMode(typeof localStorage === "undefined" ? null : localStorage.getItem(APP_THEME_STORAGE_KEY)),
);
const systemPrefersDark = ref(readSystemPrefersDark());
const isDark = computed(() => resolveAppThemeAppearance(themeMode.value, systemPrefersDark.value) === "dark");

let mediaQuery: MediaQueryList | null = null;
let isListeningForSystemTheme = false;
function readSystemPrefersDark() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function setupSystemThemeListener() {
  if (isListeningForSystemTheme || typeof window === "undefined" || typeof window.matchMedia !== "function") return;
  mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
  systemPrefersDark.value = mediaQuery.matches;
  const onChange = (event: MediaQueryListEvent) => {
    systemPrefersDark.value = event.matches;
    if (themeMode.value === "system") applyTheme();
  };
  mediaQuery.addEventListener("change", onChange);
  isListeningForSystemTheme = true;
}

function applyTheme() {
  if (typeof document === "undefined") return;

  const doc = document.documentElement;
  const dark = isDark.value;

  doc.classList.add("disable-transitions");
  doc.classList.toggle("dark", dark);
  doc.style.colorScheme = dark ? "dark" : "light";

  // force reflow so the class toggle takes effect before re-enabling transitions
  doc.offsetHeight; // eslint-disable-line @typescript-eslint/no-unused-expressions
  requestAnimationFrame(() => doc.classList.remove("disable-transitions"));

}

function setThemeMode(mode: AppThemeMode) {
  themeMode.value = mode;
  localStorage.setItem(APP_THEME_STORAGE_KEY, mode);
  applyTheme();
}

export function useTheme() {
  setupSystemThemeListener();

  function toggleTheme() {
    setThemeMode(isDark.value ? "light" : "dark");
  }

  return { isDark, themeMode, applyTheme, setThemeMode, toggleTheme };
}
