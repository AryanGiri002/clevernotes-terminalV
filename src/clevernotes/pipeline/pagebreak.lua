-- pagebreak.lua — convert every Markdown horizontal rule (`---`) into a
-- LaTeX \clearpage, so each clevernotes slide-block (title? + image + notes + ---)
-- lands on its own PDF page.
--
-- Why \clearpage instead of \newpage: \clearpage flushes any pending floats
-- (e.g. images that LaTeX deferred) before starting the new page, so images
-- never "leak" past the block they belong to.
--
-- Only applies when the output is LaTeX / PDF. For other formats (html, epub)
-- we leave the rule as-is.

function HorizontalRule(el)
  if FORMAT:match 'latex' then
    return pandoc.RawBlock('latex', '\\clearpage')
  end
  return el
end
