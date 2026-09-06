# OpenReview CLI

A command-line tool for reviewers and area chairs working with
[OpenReview](https://openreview.net). It lets you check your review
status, browse submitted reviews, download paper files, track venue
deadlines, and list your paper assignments — with human-readable tables
or machine-friendly JSON output.

## Installation

Requires Python 3.10+.

Install as an isolated CLI tool with [pipx](https://pipx.pypa.io) (recommended):

```bash
pipx install git+https://github.com/<you>/openreview_cli.git
openreview --help
``+

or with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/<you>/openreview_cli.git
```

or directly with pip:

```bash
pip install git+https://github.com/<you>/openreview_cli.git
```

For development, install editable from a checkout:

```bash
git clone https://github.com/<you>/openreview_cli.git
cd openreview_cli
pip install -e .
```

## Configuration

The tool reads an optional YAML config file, by default
`~/.openreview_cli.yaml`. Example:

```yaml
username: alice@example.org
password: secret
venues:
  - aclweb.org/ACL/ARR/2026/August
download_dir: ~/openreview-downloads
db_path: ~/.openreview_cli.sqlite3
request_delay: 0.5
```

All keys are optional; unknown keys are ignored.

### Credential resolution order

Credentials are resolved lazily (only when a command needs the API), in
this order — independently for username and password:

1. CLI flags: `--username` / `--password`
2. Environment variables: `OPENREVIEW_USERNAME` / `OPENREVIEW_PASSWORD`
3. Config file: `username` / `password` in `~/.openreview_cli.yaml`
4. Interactive prompt (passwords read with `getpass`; prompting is
   disabled in `--json` mode, where missing credentials are an error)

## Commands

All subcommands share the group-level options `--config`, `--username`,
`--password`, `--venue` (repeatable), and `--json` for JSON output.

### `status` — show review completion status

Shows the status of your submitted papers — whether reviews,
meta-reviews, or decisions are visible on their forums — and reports
new forum activity since the last run.

```bash
python -m openreview_cli status
python -m openreview_cli status --venue aclweb.org/ACL/ARR/2026/August --all
python -m openreview_cli status --watch --interval 600
```

### `reviews` — read submitted reviews

Fetches official reviews, meta reviews, and decisions for your
submissions. With `--mine`, it instead fetches the reviews,
meta-reviews, and decisions **you wrote** on other people's papers,
across all venues or filtered by `--venue`.

```bash
python -m openreview_cli reviews
python -m openreview_cli reviews --submission abc123 --output reviews-json/
python -m openreview_cli reviews --mine
python -m openreview_cli reviews --mine --venue aclweb.org/ACL/ARR --output my-reviews/
```

### `download` — download paper files

Downloads PDFs (and optionally supplementary material) for your own
submissions or a venue's papers. Note: papers currently under review
may not be visible via venue queries.

```bash
python -m openreview_cli download
python -m openreview_cli download --venue aclweb.org/ACL/ARR/2026/August --supplementary
```

### `deadlines` — show venue deadlines

Lists stage deadlines for the configured venues.

```bash
python -m openreview_cli deadlines
python -m openreview_cli deadlines --venue aclweb.org/ACL/ARR/2026/August
```

### `assignments` — list your paper assignments

Lists the papers you are assigned to review (or meta-review), with an
option to download each as a bundle.

```bash
python -m openreview_cli assignments
python -m openreview_cli assignments --download --supplementary
```

## Error handling

Errors (bad credentials, unknown venue, network failures) are printed
as `Error: <message>` on stderr with exit code 1, or as
`{"error": "<message>"}` on stdout in `--json` mode.
