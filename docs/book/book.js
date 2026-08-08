/* Self-contained Python highlighter — no internet, no CDN.
   Highlights <pre><code class="python"> blocks. Author code HTML-escaped
   (& < >) only; quotes are left literal. Order matters: comments & strings
   first so keywords inside them are not re-tokenised. */
(function () {
  const KW = new Set(("def class return if elif else for while in and or not is None True False " +
    "import from as with try except finally raise yield lambda global nonlocal assert pass break " +
    "continue del async await match case").split(" "));

  function highlight(src) {
    const out = [];
    let i = 0;
    const n = src.length;
    const push = (cls, txt) => out.push(cls ? `<span class="${cls}">${txt}</span>` : txt);

    while (i < n) {
      const c = src[i];

      // comment to end of line
      if (c === "#") {
        let j = i; while (j < n && src[j] !== "\n") j++;
        push("tok-com", src.slice(i, j)); i = j; continue;
      }
      // strings (triple then single/double). src is already HTML-escaped, quotes literal.
      if (c === '"' || c === "'") {
        const triple = src.substr(i, 3);
        if (triple === '"""' || triple === "'''") {
          let j = i + 3; while (j < n && src.substr(j, 3) !== triple) j++;
          j = Math.min(n, j + 3); push("tok-str", src.slice(i, j)); i = j; continue;
        }
        let j = i + 1; while (j < n && src[j] !== c) { if (src[j] === "\\") j++; j++; }
        j = Math.min(n, j + 1); push("tok-str", src.slice(i, j)); i = j; continue;
      }
      // decorator
      if (c === "@" && (i === 0 || src[i - 1] === "\n")) {
        let j = i + 1; while (j < n && /[\w.]/.test(src[j])) j++;
        push("tok-dec", src.slice(i, j)); i = j; continue;
      }
      // identifier / keyword
      if (/[A-Za-z_]/.test(c)) {
        let j = i; while (j < n && /[\w]/.test(src[j])) j++;
        const word = src.slice(i, j);
        const prev = out.length ? src.slice(0, i).trimEnd() : "";
        if (KW.has(word)) push("tok-kw", word);
        else if (word === "self" || word === "cls") push("tok-self", word);
        else if (prev.endsWith("def") || prev.endsWith("class")) push("tok-def", word);
        else push(null, word);
        i = j; continue;
      }
      // number
      if (/[0-9]/.test(c)) {
        let j = i; while (j < n && /[0-9._eExXa-fA-F]/.test(src[j])) j++;
        push("tok-num", src.slice(i, j)); i = j; continue;
      }
      push(null, c); i++;
    }
    return out.join("");
  }

  document.querySelectorAll("pre > code.python").forEach(el => {
    el.innerHTML = highlight(el.innerHTML);
  });

  // mark active sidebar link
  const here = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".side a").forEach(a => {
    if ((a.getAttribute("href") || "") === here) a.classList.add("active");
  });
})();
