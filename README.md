# Dropbox Duplicate Media Report (read-only)

Finds **exact duplicate media files** in your Dropbox by Dropbox's own
`content_hash` and writes a CSV report. It lists metadata only. It does not
download, move, delete or upload anything, and it doesn't need a local Dropbox
sync folder.

Part 1 of the organizer. Cleanup is a separate tool for later, and it will
need its own app with its own permissions.

## This tool cannot modify your Dropbox

It's not that the tool *chooses* not to modify your Dropbox. It *can't*,
because of how the access token is built:

1. **The token is scoped by Dropbox, server-side.** The OAuth flow requests
   exactly `files.metadata.read` and `account_info.read`. A token with only
   those scopes gets rejected by Dropbox's API on any write, move, delete,
   download or sharing call, whatever the code tries. If Dropbox ever hands back
   a token with any extra scope, `auth` refuses to save it.
2. **The code can only make three calls.** All Dropbox access goes through
   `ReadOnlyDropbox` (`dropbox_organizer/client.py`), which exposes only
   `files_list_folder`, `files_list_folder_continue` and
   `users_get_current_account`. It has no passthrough to the underlying SDK.
3. **A test enforces this.** `tests/test_readonly.py` parses every module in
   the package and fails if it finds any call to a Dropbox SDK method outside
   that allowlist (`files_delete*`, `files_move*`, `files_download*`,
   `files_upload*`, `sharing_*`, the raw `request*` transport, `getattr`
   tricks on the client, and so on).
4. **There is no write path.** Not behind a flag, and not commented out.

Point 1 is the real guarantee. Points 2–4 keep the code honest so a future
change can't drift into needing more scopes without someone noticing.

The tool does delete rows from its own **local** SQLite cache (for example,
when Dropbox reports that a file vanished mid-scan). That cache lives on your
disk, not in Dropbox.

## 1. Create the Dropbox app

1. Go to the [Dropbox App Console](https://www.dropbox.com/developers/apps) → **Create app**.
2. Choose **Scoped access**.
3. Choose **Full Dropbox**. "App folder" can't see your photos.
4. Name it anything, e.g. `my-dedupe-report-readonly`.
5. Open the **Permissions** tab and tick **only** these:
   - `files.metadata.read`
   - `account_info.read`

   Leave everything else unticked: no `files.content.*`, no `sharing.*`, no
   `file_requests.*`. Click **Submit**.
6. On the **Settings** tab, copy the **App key**. You don't need the app
   secret, because the login uses PKCE.

## 2. Configure

Put the app key in the environment, or in a `.env` file (gitignored):

```bash
cp .env.example .env
```

Then edit `.env` to `DROPBOX_APP_KEY=your_app_key`. Alternatively, export it:

```bash
export DROPBOX_APP_KEY=your_app_key
```

The refresh token is saved to `~/.config/dropbox_organizer/token` with mode
`600`. The directory is mode `700`. The token never lives in the repo.

## 3. Run it (Docker + make)

You need Docker and `make`. `make help` lists every target.

```bash
make auth     # one-time PKCE login; paste the code from the browser
```

```bash
make scan     # resumable metadata scan into data/dropbox_scan.sqlite
```

```bash
make report   # writes reports/duplicates_<ts>.csv and reports/summary_<ts>.txt
```

Other targets:

| target            | what it does                                                              |
|-------------------|---------------------------------------------------------------------------|
| `make run`        | scan (or resume) then report                                              |
| `make scan-fresh` | discard the checkpoint and rescan from scratch                            |
| `make test`       | ruff + pytest inside Docker with `--network none`                         |
| `make lint`       | ruff only                                                                 |
| `make shell`      | a shell in the container                                                  |
| `make clean-scan` | delete `data/` (the local cache only)                                     |
| `make clean`      | remove the Docker images                                                  |

Pass extra CLI flags with `ARGS`:

```bash
make scan ARGS='--exclude-dirs node_modules,.git,Pods'
```

The container runs as your own uid and bind-mounts three locations:

- `~/.config/dropbox_organizer` for the token
- `./data` for the SQLite checkpoint
- `./reports` for the output

### Without Docker

You need Python 3.12 or newer.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
.venv/bin/python -m dropbox_organizer auth
```

```bash
.venv/bin/python -m dropbox_organizer scan
```

```bash
.venv/bin/python -m dropbox_organizer report
```

## How the scan works

- `files_list_folder(path="", recursive=True, include_deleted=False, limit=2000)`,
  then `files_list_folder_continue` until `has_more` is false.
- Only `FileMetadata` entries count. Folders are ignored.
- **Checkpointing:** each page is committed to SQLite in one transaction
  together with the cursor that follows it. Ctrl+C, a crash or a lost network
  connection costs at most one page. Run `scan` again and it resumes from the
  cursor. A finished scan isn't repeated unless you pass `--fresh`.
- If Dropbox expires the saved cursor (`reset`), the scan logs it and starts over.
- **Rate limits:** on `RateLimitError` the scan sleeps for Dropbox's
  `Retry-After`/`retry_after` (5 s if Dropbox gives none) and retries. It gives
  up after 20 tries in a row, and then you just resume.
- Progress logs once per page: pages, files seen, media kept, excluded media.

**Media whitelist** (extension, case-insensitive):

- RAW: `.cr2 .cr3 .crw` (Canon), `.nef .nrw` (Nikon), `.dng`
- Other: `.jpg .jpeg .heic .tif .tiff .psd .png .mp4 .mov`

**Excluded folders:** media under a folder whose name (case-insensitive) is in
this list is skipped:

`node_modules, .git, venv, .venv, __pycache__, site-packages, vendor,
bower_components, Pods, .tox`

Override the list with `--exclude-dirs a,b,c`, or pass `--exclude-dirs ""` to
exclude nothing. Skipped files aren't silently dropped. They're counted, and
`summary.txt` lists every excluded folder with its file count, so you can check
that nothing real got skipped. A resumed scan keeps the exclusion list it
started with. Use `--fresh` to change it.

## The report

`reports/duplicates_<timestamp>.csv` has one row per file, and only for groups
of two or more files:

| column            | meaning                                                         |
|-------------------|-----------------------------------------------------------------|
| `group_id`        | 1 = biggest reclaimable group                                   |
| `status`          | `DUPLICATE`, or `SIZE_MISMATCH` (see below)                     |
| `content_hash`    | Dropbox content hash                                            |
| `size_bytes`      |                                                                 |
| `path_display`    |                                                                 |
| `client_modified` | UTC, ISO 8601                                                   |
| `server_modified` | UTC, ISO 8601                                                   |
| `suggested_keep`  | `true` for exactly one file per DUPLICATE group                 |

- `suggested_keep` goes to the oldest `client_modified`, then the shortest
  path. It is **only a suggestion**. Nothing acts on it.
- **SIZE_MISMATCH:** files with the same hash should always be the same size.
  If they aren't, the group is flagged rather than treated as duplicates.
  Every row in it gets `suggested_keep=false`, and none of it counts toward
  reclaimable space.
- Media files without a `content_hash` are never grouped. The summary counts them.

`reports/summary_<timestamp>.txt` shows:

- files scanned and media files kept
- excluded media count, plus the folder list
- duplicate groups, duplicate files and redundant copies
- reclaimable bytes, human-readable
- a per-extension breakdown

If the scan hasn't finished, the summary carries a warning.

Note on shared folders: a duplicate inside a shared folder may be someone
else's copy too. Check before you delete anything by hand.

## Development

```bash
make test
```

To run the checks outside Docker:

```bash
ruff check . && ruff format --check . && pytest -q
```

The tests use the SDK's real metadata types with a fake client and never touch
the network.
