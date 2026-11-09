# linshare-cli

`linshare`: a command-line tool to share documents through a LinShare server
(6.5 or later), designed to be driven by AI agents.

- The tool, its install and usage: [`agent-harness/cli_anything/linshare/README.md`](agent-harness/cli_anything/linshare/README.md)
- Tests and results against real servers: [`agent-harness/cli_anything/linshare/tests/TEST.md`](agent-harness/cli_anything/linshare/tests/TEST.md)
- The agent skill that uses it lives in its own repository, [linshare-skill](https://github.com/wboudiche/linshare-skill).

```bash
pip install "git+https://github.com/wboudiche/linshare-cli#subdirectory=agent-harness"
linshare login --server https://linshare.example.com
linshare share report.pdf --to marc@acme.com --expires 7d --dry-run
```

License: AGPL-3.0
