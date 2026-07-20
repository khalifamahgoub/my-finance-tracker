"""The single colour-token system shared by every surface (the static dashboard template
and the `finance web` interactive pages). One definition, so a palette change or a11y fix
never has to be made twice.

Extracted verbatim from the dashboard's original :root block (impeccable audit, Phase 3)
so this refactor changes zero rendered pixels; :focus-visible is new here.
"""
from __future__ import annotations

TOKENS_CSS = """
  :root{
    --bg:#0f1115; --card:#181b22; --card2:#1f232c; --ink:#eceef2; --muted:#a3a9b4;
    --line:#2a2f3a; --accent:#7cb3ff; --accent-weak:#1b2735;
    --green:#43b95a; --amber:#d8a23a; --red:#ff6b63;
    --mono:"SF Mono",ui-monospace,Menlo,Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  }
  @media (prefers-color-scheme:light){
    :root{--bg:#f5f6f8;--card:#fff;--card2:#eef1f5;--ink:#171a21;--muted:#535a67;
          --line:#e0e4ea;--accent:#1f5fd0;--accent-weak:#eaf1fd;
          --green:#137a34;--amber:#8a5d00;--red:#c62f26;}
  }
  :focus-visible{outline:2px solid var(--accent);outline-offset:2px}
"""
