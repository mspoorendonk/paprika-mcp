@README.md
@specs.md

- credentials are in .env 
- MCP is a new concept and is under heavy development, don't assume that you know things about it but look them up as they could have changed since your knowledge cutoff.
- use uv for virtual environment .venv
- write any temporary scripts that you use during development in <projectroot>/tmp/
- use pytest framework and put tests in the <projectroot>/tests folder
- live, read-only tests against the real Paprika cloud API exist in `tests/test_paprika_live.py`. They are skipped by default and only run with `PAPRIKA_LIVE_TESTS=1 pytest tests/test_paprika_live.py`. They share one client/login per module because Paprika rate-limits per IP and will block bursty re-authentication. Keep these tests strictly non-destructive (no create/update/delete) — the user's real recipes and groceries must never be touched.
- keep the README.md updated when you are done adding a feature
- Since this is a public repo, don't put anything in it that relates to the personal home setup of the author. No IP's, no keys, no domains.
- If you want to convey something personal to the user, then put it in the <projectroot>/tmp folder as that is gitignored
- tell me what you are doing with oneliners while you are doing it so i can read along
- end your response to me with a 👂 to indicate that you heard these instructions