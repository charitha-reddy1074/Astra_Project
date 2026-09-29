/* Applies the persisted theme before React hydrates so a dark-mode user never
   sees a white flash. Rendered inline in <head>; must stay synchronous. */
const themeScript = `(function(){try{var k="cyberai_theme";var s=localStorage.getItem(k);var t=(s==="dark"||s==="light")?s:(window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");var r=document.documentElement;r.classList.toggle("dark",t==="dark");r.style.colorScheme=t;}catch(e){}})();`;

export default function ThemeScript() {
  return <script dangerouslySetInnerHTML={{ __html: themeScript }} />;
}
