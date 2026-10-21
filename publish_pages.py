"""Publish the built site/ folder to the gh-pages branch (GitHub Pages; Cloudflare Pages can serve the same branch).

  python build_site.py
  python publish_pages.py

The branch holds generated files only, so it is rebuilt as a single fresh commit and force-pushed.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
SKIP = {".vercel", "vercel.json", ".git"}


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True)


def main():
    if not (SITE / "index.html").exists():
        raise SystemExit("site/ is not built - run `python build_site.py` first")
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "fetch", "-q", "origin", "gh-pages"], cwd=ROOT)   # to read the current CNAME, if any
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        for p in SITE.iterdir():
            if p.name in SKIP:
                continue
            (shutil.copytree if p.is_dir() else shutil.copy2)(p, out / p.name)
        (out / ".nojekyll").touch()          # serve files as-is (no Jekyll processing)
        # keep the custom domain: GitHub stores it as a CNAME file on the published branch
        cname = subprocess.run(["git", "show", "origin/gh-pages:CNAME"], cwd=ROOT, capture_output=True, text=True)
        if cname.returncode == 0 and cname.stdout.strip():
            (out / "CNAME").write_text(cname.stdout.strip() + "\n")
        git("init", "-q", "-b", "gh-pages", cwd=out)
        git("add", "-A", cwd=out)
        git("commit", "-q", "-m", "Publish static site (build_site.py)", cwd=out)
        git("push", "-f", remote, "gh-pages", cwd=out)
    print("Published gh-pages ->", remote)


if __name__ == "__main__":
    main()
