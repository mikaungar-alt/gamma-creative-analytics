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
      versions: vers ? +vers : 1, poster: v.poster || null,
      // below the video: DOMAIN, headline, link description, button (Mika 24.11 18:25: "אם יש עוד נתונים תיקח אותם גם")
      ...(() => { const di = lines.findIndex((x, k) => k > sp && /^[A-Z0-9.-]+\.[A-Z]{2,}$/.test(x));
        if (di < 0) return {};
        const rest = lines.slice(di + 1), cta = rest.length && /^(Watch more|Learn more|Install now|Download|Shop now|Sign up|Play game|Use app|Watch video|Get offer|See more)$/i.test(rest[rest.length - 1]) ? rest.pop() : null;
        return {domain: lines[di].toLowerCase(), headline: rest[0] || null, linkdesc: rest.slice(1).join(" ") || null, cta}; })(),
      ended: (t.match(/[A-Z][a-z]{2} \d{1,2}, \d{4} - ([A-Z][a-z]{2} \d{1,2}, \d{4})/) || [])[1] || null,
      eu: /EU transparency/.test(t), categories: /\nCategories\n/.test("\n" + t + "\n")});
  }
  return JSON.stringify(out);
})()
