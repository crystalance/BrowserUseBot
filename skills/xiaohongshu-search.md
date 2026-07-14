# Xiaohongshu Search

Search Xiaohongshu (小红书) in-site for posts and return them with links.

Triggers: 小红书, xiaohongshu, xhs, 面经, 笔记
Allowed-Hosts: xiaohongshu.com
Login-Hosts: xiaohongshu.com

## Instructions
- Stay ON xiaohongshu.com the whole time. Do NOT use external search engines
  (Google/Bing/DuckDuckGo) — Xiaohongshu blocks search-engine indexing, so
  `site:xiaohongshu.com` queries return nothing. External search is a dead end.
- If not logged in, wait for the human login handoff, then continue.
- Use the on-site search box (搜索) at the top. Type the user's keywords and submit.
- Your job is to COLLECT raw data, not to summarise. A separate synthesis step
  turns your collected records into the final answer, so focus on gathering.
- STEP 1 — harvest links ONCE with a single `run_js` call. Return an array of
  {title, href} for every result card, e.g.:
    (() => Array.from(document.querySelectorAll('a[href*="/search_result/"], a[href*="/explore/"], a[href*="/discovery/item/"]'))
      .map(a => ({ title: (a.innerText||"").trim(), href: a.href })).filter(x => x.title))
  Scroll 1–2 times and re-run ONCE more if you need more cards. Do NOT keep
  re-harvesting the same list. Links look like `/search_result/<id>?xsec_token=…`.
- STEP 2 — for the ~15 most relevant results, read each post's detail IN THE
  SAME TAB without navigating away. Xiaohongshu opens a clicked note as an in-page
  detail OVERLAY/modal, so:
    1. Click the result card (it opens the note detail overlay on the same page).
    2. Use ONE `extract_and_save` call whose JS RETURNS the record {title, url, body}
       — it is saved directly, so you never re-type the body. Example:
       (() => { const m = document.querySelector('#noteContainer, .note-detail-mask, [class*="note-detail"]') || document.body;
         return { url: location.href, title: (m.querySelector('#detail-title, .title, h1')?.innerText||document.title||"").trim(),
           body: (m.querySelector('#detail-desc, .note-content, .desc, article')?.innerText || m.innerText || "").trim().slice(0, 6000) }; })
    3. Close the overlay (press Escape, or click its close button) to return to the
       results list — do NOT open a new tab or navigate to the href.
    4. Click the next card and repeat.
- Never pass placeholder strings like `__FROM_RUN_JS__` to save_items — always
  save the ACTUAL extracted values. Prefer `extract_and_save` so the JS return
  value is stored directly.
- Aim for ~15 posts with real body text (more is fine if steps allow). Don't stop
  at a tiny number if results are available.
- If a keyword yields too few results, try simpler/broader variants (company name
  alone, "面经", "面试", "new grad", "NG").
- When you have collected enough, call done briefly — you do NOT need to write the
  final list yourself; the synthesis step will format it from your saved records.
