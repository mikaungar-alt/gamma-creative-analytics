// Meta Ad Library results page → one row per card that has a video. Card = the smallest ancestor of the
// <video> whose text holds exactly one "Library ID:". Mika 24.11 16:00: "תן לי את שניהם גם מטא וגם סושיאל פיטה"
(() => {
  const out = [], seen = new Set();
  for (const v of document.querySelectorAll("video")) {
    let el = v, card = null;
    for (let k = 0; k < 25 && el; k++, el = el.parentElement) {
      const n = (el.innerText.match(/Library ID:/g) || []).length;
      if (n === 1) { card = el; } else if (n > 1) break;
    }
    if (!card) continue;
    const t = card.innerText, id = (t.match(/Library ID:\s*(\d+)/) || [])[1];
    if (!id || seen.has(id)) continue; seen.add(id);
    const lines = t.split("\n").map(x => x.trim()).filter(x => x && x !== "​");
    const sp = lines.indexOf("Sponsored");
    const vers = (t.match(/(\d+) ads use this creative/) || [])[1];
    out.push({id, active: /^Active$/m.test(t), started: (t.match(/Started running on ([A-Z][a-z]{2} \d{1,2}, \d{4})/) || [])[1] || null,
      page: sp > 0 ? lines[sp - 1] : null, text: sp >= 0 ? (lines[sp + 1] || "") : "",
      versions: vers ? +vers : 1, poster: v.poster || null});
  }
  return JSON.stringify(out);
})()
