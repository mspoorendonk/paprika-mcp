@README.md
@specs.md

- This server follows the author's shared MCP standard (OAuth2-everywhere auth,
  Python 3.12, secret scanning, systemd deployment). That standard lives in a
  separate **private** ops repo and is intentionally not referenced by path
  here — this is a public repo and must not leak the author's home setup
  (no hostnames, domains, IPs, or private repo paths). Apply the standard's
  rules; keep the specifics out of this repo.
- credentials are in .env 
- MCP is a new concept and is under heavy development, don't assume that you know things about it but look them up as they could have changed since your knowledge cutoff.
- use uv for virtual environment .venv
- write any temporary scripts that you use during development in <projectroot>/tmp/
- use pytest framework and put tests in the <projectroot>/tests folder
- live, read-only tests against the real Paprika cloud API exist in `tests/test_paprika_live.py`. They are skipped by default and only run with `PAPRIKA_LIVE_TESTS=1 pytest tests/test_paprika_live.py`. They share one client/login per module because Paprika rate-limits per IP and will block bursty re-authentication. Keep these tests strictly non-destructive (no create/update/delete) — the user's real recipes and groceries must never be touched.
- keep the README.md updated when you are done adding a feature
- Since this is a public repo, don't put anything in it that relates to the personal home setup of the author. No IP's, no keys, no domains.
- This repo has a pre-commit hook (gitleaks) that scans for secrets. Before doing any work in a fresh clone, run `pre-commit install` once (install the tool with `uv tool install pre-commit` if missing). If a commit fails because gitleaks fired, **do not** bypass with `--no-verify`; investigate the finding and either fix the leak or update `.gitleaks.toml`'s allowlist if it's a true false positive.
- If you want to convey something personal to the user, then put it in the <projectroot>/tmp folder as that is gitignored
- tell me what you are doing with oneliners while you are doing it so i can read along
- end your response to me with a 👂 to indicate that you heard these instructions